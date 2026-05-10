"""
Celery tasks for the SMS reminder pipeline.

Two-task design (deliberately):

    dispatch_due_reminders   beat-scheduled, every 5 min
        Scans for pending reminders whose scheduled_for is in the dispatch
        window. Atomically claims each (status PENDING → IN_FLIGHT) so two
        beat firings can't fire the same reminder twice. Hands each claimed
        ID off to send_reminder.delay().

    send_reminder(reminder_id)   queued, one per send
        Re-checks eligibility at send time (client may have opted out since
        scheduling), builds the HIPAA-safe body, calls the provider, writes
        the result back. Transient failures retry with exponential backoff.

This is the same pattern used by apps.billing.tasks (see send_payment_reminder),
chosen so per-row failures don't stall the dispatch loop and so retries are
cheap.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import AppointmentReminder

logger = logging.getLogger(__name__)


@shared_task(ignore_result=True)
def dispatch_due_reminders():
    """
    Beat task. Find pending reminders that are due now (within the dispatch
    window), atomically claim them, and queue per-row send tasks.

    Stale reminders (scheduled_for older than the stale window) are marked
    skipped — if the worker was down for hours, we don't want to flood the
    practice's clients with a backlog of "reminder for an appointment that
    started 4 hours ago" texts.
    """
    now = timezone.now()
    stale_window_hours = getattr(settings, 'SMS_REMINDER_STALE_WINDOW_HOURS', 1)
    stale_cutoff = now - timedelta(hours=stale_window_hours)

    # Mark stale rows skipped first. Cheap UPDATE, no lock needed because
    # only this task touches PENDING-and-old rows.
    stale_count = AppointmentReminder.objects.filter(
        status=AppointmentReminder.STATUS_PENDING,
        scheduled_for__lt=stale_cutoff,
    ).update(
        status=AppointmentReminder.STATUS_SKIPPED,
        skip_reason=AppointmentReminder.SKIP_REASON_QUIET_HOURS_STALE,
    )
    if stale_count:
        logger.warning('Marked %d stale reminders skipped', stale_count)

    # Atomically claim due reminders. select_for_update + same-transaction
    # update is the canonical Django pattern for "fetch + claim". skip_locked
    # means a second beat tick won't block on rows the first tick is claiming.
    claimed_ids = []
    with transaction.atomic():
        due_qs = (
            AppointmentReminder.objects
            .select_for_update(skip_locked=True)
            .filter(
                status=AppointmentReminder.STATUS_PENDING,
                scheduled_for__gte=stale_cutoff,
                scheduled_for__lte=now,
            )
            .order_by('scheduled_for')
        )
        for reminder in due_qs[:200]:  # cap each tick so a flood doesn't OOM
            reminder.status = AppointmentReminder.STATUS_IN_FLIGHT
            reminder.attempts = (reminder.attempts or 0) + 1
            reminder.save(update_fields=['status', 'attempts', 'updated_at'])
            claimed_ids.append(str(reminder.id))

    for reminder_id in claimed_ids:
        send_reminder.delay(reminder_id)

    return {
        'claimed': len(claimed_ids),
        'stale_skipped': stale_count,
    }


@shared_task(
    bind=True,
    autoretry_for=(),  # we classify and re-raise manually below
    retry_backoff=True,
    retry_backoff_max=600,
    retry_kwargs={'max_retries': 3},
)
def send_reminder(self, reminder_id: str):
    """
    Send a single reminder. Re-checks eligibility, builds the message, calls
    the provider, audit-logs the outcome.

    Returns a dict for observability; doesn't raise on permanent failure (the
    row is marked failed and we move on).
    """
    from .providers import (
        ProviderError, TransientProviderError, get_sms_provider,
    )
    from .services import (
        MessageContext, build_message_body, is_reminder_eligible,
        practice_provider_label, to_e164_us,
    )
    from .services import PRACTICE_TZ

    try:
        reminder = AppointmentReminder.objects.select_related(
            'appointment',
            'appointment__client',
            'appointment__provider',
            'appointment__location',
            'appointment__organization',
        ).get(id=reminder_id)
    except AppointmentReminder.DoesNotExist:
        logger.error('send_reminder: reminder %s not found', reminder_id)
        return {'status': 'error', 'reason': 'not_found'}

    # If something else (admin, signal, parallel send) moved the row out of
    # IN_FLIGHT, bail. We never overwrite a sent/cancelled/skipped row.
    if reminder.status != AppointmentReminder.STATUS_IN_FLIGHT:
        logger.info(
            'send_reminder: reminder %s no longer in_flight (status=%s), bailing',
            reminder_id, reminder.status,
        )
        return {'status': 'noop', 'current_status': reminder.status}

    appointment = reminder.appointment

    eligible, skip_reason = is_reminder_eligible(appointment)
    if not eligible:
        _mark_skipped(reminder, skip_reason)
        _audit(reminder, 'sms_reminder_skipped', {'reason': skip_reason})
        return {'status': 'skipped', 'reason': skip_reason}

    to_number = to_e164_us(appointment.client.phone)
    if to_number is None:
        _mark_skipped(reminder, AppointmentReminder.SKIP_REASON_INVALID_PHONE)
        _audit(reminder, 'sms_reminder_skipped', {'reason': 'invalid_phone'})
        return {'status': 'skipped', 'reason': 'invalid_phone'}

    location = appointment.location
    is_telehealth = bool(location and getattr(location, 'is_telehealth', False))
    location_name = location.name if location else 'our office'

    practice_phone = getattr(settings, 'PRACTICE_CALLBACK_PHONE', '') or 'our office'

    body = build_message_body(
        ctx=MessageContext(
            provider_label=practice_provider_label(appointment.provider),
            appointment_local=appointment.start_time.astimezone(PRACTICE_TZ),
            location_name=location_name,
            practice_phone=practice_phone,
            is_telehealth=is_telehealth,
        ),
        lead_time=reminder.lead_time,
    )

    try:
        provider = get_sms_provider()
        result = provider.send_sms(to_number=to_number, body=body)
    except TransientProviderError as e:
        # Put the row back to PENDING for the next dispatch tick — but only if
        # we still have retry budget. Celery's autoretry would re-queue *this
        # task*, which is fine, but flipping back to PENDING also lets the
        # beat scheduler pick it up if the worker pool restarts.
        reminder.status = AppointmentReminder.STATUS_PENDING
        reminder.last_error = f'{e.code}: {e.detail}'[:255]
        reminder.save(update_fields=['status', 'last_error', 'updated_at'])
        _audit(reminder, 'sms_reminder_transient_failure', {
            'code': e.code, 'attempt': reminder.attempts,
        })
        if reminder.attempts >= 3:
            # Give up — promote to permanent failure so we don't churn forever.
            _mark_failed(reminder, f'gave_up_after_{reminder.attempts}_attempts')
            return {'status': 'failed', 'reason': 'max_retries'}
        raise self.retry(exc=e)
    except ProviderError as e:
        _mark_failed(reminder, f'{e.code}: {e.detail}'[:255])
        _audit(reminder, 'sms_reminder_failed', {'code': e.code})
        return {'status': 'failed', 'reason': e.code}

    reminder.status = AppointmentReminder.STATUS_SENT
    reminder.sent_at = timezone.now()
    reminder.provider = result.provider_name
    reminder.provider_message_id = result.provider_message_id
    reminder.last_error = ''
    reminder.save(update_fields=[
        'status', 'sent_at', 'provider', 'provider_message_id',
        'last_error', 'updated_at',
    ])
    _audit(reminder, 'sms_reminder_sent', {
        'provider': result.provider_name,
        'lead_time': reminder.lead_time,
    })

    return {
        'status': 'sent',
        'reminder_id': str(reminder.id),
        'provider_message_id': result.provider_message_id,
    }


# ─── helpers ────────────────────────────────────────────────────────────────

def _mark_skipped(reminder: AppointmentReminder, reason: str) -> None:
    reminder.status = AppointmentReminder.STATUS_SKIPPED
    reminder.skip_reason = reason
    reminder.save(update_fields=['status', 'skip_reason', 'updated_at'])


def _mark_failed(reminder: AppointmentReminder, error: str) -> None:
    reminder.status = AppointmentReminder.STATUS_FAILED
    reminder.last_error = error[:255]
    reminder.save(update_fields=['status', 'last_error', 'updated_at'])


def _audit(reminder: AppointmentReminder, action: str, changes: dict) -> None:
    """
    Audit-log a reminder lifecycle event. No PHI in `changes` — only
    operational data (status, code, lead_time). Uses the org from the
    appointment so multi-tenant filters work.
    """
    from apps.audit.models import AuditLog

    org = reminder.appointment.organization if reminder.appointment_id else None
    try:
        AuditLog.objects.create(
            organization=org,
            user=None,  # system action
            action=action,
            table_name='messaging_appointmentreminder',
            record_id=reminder.id,
            changes=changes,
        )
    except Exception:
        # Audit failure must never crash the send. Log and move on; ops can
        # reconstruct from application logs if AuditLog is degraded.
        logger.exception('AuditLog write failed for reminder %s', reminder.id)
