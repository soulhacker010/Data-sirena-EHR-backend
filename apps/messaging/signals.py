"""
Lifecycle signals for AppointmentReminder.

A signal handler keeps the reminder rows in sync with the appointment without
the calling code (views, serializers, management commands) having to remember
to do it. Idempotent by design — the (appointment, lead_time) unique key means
re-saving an appointment N times produces the same N×3 reminders.

Why signals and not a service-layer call? The Appointment model is touched
from many places: REST views, recurring-series generators, Django admin,
management commands, future iCal sync. A signal is the one place that catches
all of them.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from apps.scheduling.models import Appointment

from .models import AppointmentReminder
from .services import compute_scheduled_for, is_reminder_eligible, lead_times

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Appointment)
def sync_reminders_on_appointment_save(sender, instance: Appointment, created: bool, **kwargs):
    """
    Re-sync the 3 reminder rows for this appointment.

    Runs on every save, including updates. Per-row update_or_create against the
    unique (appointment, lead_time) key, so this is safe to fire on every edit
    — no duplicates, scheduled_for tracks the latest start_time, status of
    already-sent reminders is preserved.
    """
    # Defer until the surrounding transaction commits. If it rolls back, we
    # never created the reminders. This also means the appointment row is
    # visible to any subsequent SELECT inside the handler.
    transaction.on_commit(lambda: _sync_reminders(instance))


def _sync_reminders(appointment: Appointment) -> None:
    eligible, skip_reason = is_reminder_eligible(appointment)

    if not eligible:
        # Cancel any pending rows that became ineligible (client opted out,
        # appointment cancelled, switched to a non-session event, etc.).
        AppointmentReminder.objects.filter(
            appointment=appointment,
            status__in=[
                AppointmentReminder.STATUS_PENDING,
                AppointmentReminder.STATUS_IN_FLIGHT,
            ],
        ).update(
            status=AppointmentReminder.STATUS_CANCELLED,
            skip_reason=skip_reason,
        )
        return

    appointment_start_utc = appointment.start_time
    from django.utils import timezone

    for lead in lead_times():
        scheduled_for = compute_scheduled_for(appointment_start_utc, lead)

        # If the lead-time is already in the past at the moment of scheduling,
        # don't bother — record a skipped row so audits show we considered it.
        if scheduled_for <= timezone.now():
            AppointmentReminder.objects.update_or_create(
                appointment=appointment,
                lead_time=lead,
                defaults={
                    'scheduled_for': scheduled_for,
                    'status': AppointmentReminder.STATUS_SKIPPED,
                    'skip_reason': AppointmentReminder.SKIP_REASON_PAST,
                },
            )
            continue

        # Standard path: create or refresh the pending reminder. We deliberately
        # only overwrite status if the existing row is still pending — if it
        # already sent, we don't want to resurrect it just because the
        # appointment got edited.
        existing = AppointmentReminder.objects.filter(
            appointment=appointment, lead_time=lead,
        ).first()

        if existing is None:
            AppointmentReminder.objects.create(
                appointment=appointment,
                lead_time=lead,
                scheduled_for=scheduled_for,
                status=AppointmentReminder.STATUS_PENDING,
            )
        elif existing.status in (
            AppointmentReminder.STATUS_PENDING,
            AppointmentReminder.STATUS_SKIPPED,
            AppointmentReminder.STATUS_CANCELLED,
        ):
            # Reschedule and reactivate. SKIPPED/CANCELLED rows are revived if
            # the appointment was edited back into eligibility (e.g. moved
            # forward in time, or client opted back in).
            existing.scheduled_for = scheduled_for
            existing.status = AppointmentReminder.STATUS_PENDING
            existing.skip_reason = ''
            existing.last_error = ''
            existing.save(update_fields=[
                'scheduled_for', 'status', 'skip_reason', 'last_error', 'updated_at',
            ])
        # else: status in (in_flight, sent, failed) — leave alone. A sent
        # reminder stays sent; an in-flight one will resolve on its own; a
        # failed one stays failed (operator can re-trigger manually if needed).


@receiver(pre_delete, sender=Appointment)
def cancel_reminders_on_appointment_delete(sender, instance: Appointment, **kwargs):
    """
    Mark all pending/in-flight reminders cancelled before the appointment row
    is deleted. CASCADE would remove them anyway, but cancelling first leaves
    an audit trail of "we had reminders, we chose not to send them".
    """
    AppointmentReminder.objects.filter(
        appointment=instance,
        status__in=[
            AppointmentReminder.STATUS_PENDING,
            AppointmentReminder.STATUS_IN_FLIGHT,
        ],
    ).update(status=AppointmentReminder.STATUS_CANCELLED)
