"""
Notification auto-generation service.

Creates notifications automatically when specific events occur:
- Authorization reaching 75%, 90%, or 100% utilization
- Attended session without a signed note (missing notes)
- Insurance claim denied

These functions should be called from the relevant views after
the triggering action completes.
"""
import logging
from datetime import timedelta
from django.utils import timezone

from .models import Notification

logger = logging.getLogger(__name__)


def notify_authorization_utilization(authorization):
    """
    Check authorization utilization and create alerts at thresholds.

    Called after auth units are updated (e.g. appointment marked attended).
    Creates notifications for the assigned provider (or admin fallback).
    """
    if not authorization or not authorization.units_approved:
        return

    used = float(authorization.units_used or 0)
    approved = float(authorization.units_approved)
    utilization = (used / approved) * 100

    # Determine which threshold was hit
    thresholds = [
        (100, 'urgent', 'Authorization Fully Used',
         f'Authorization #{authorization.authorization_number} for '
         f'{authorization.client.full_name} has reached 100% utilization '
         f'({used:.0f}/{approved:.0f} units). No more sessions can be billed.'),
        (90, 'high', 'Authorization at 90%',
         f'Authorization #{authorization.authorization_number} for '
         f'{authorization.client.full_name} is at {utilization:.0f}% utilization '
         f'({used:.0f}/{approved:.0f} units). Only {approved - used:.0f} units remaining.'),
        (75, 'medium', 'Authorization at 75%',
         f'Authorization #{authorization.authorization_number} for '
         f'{authorization.client.full_name} is at {utilization:.0f}% utilization '
         f'({used:.0f}/{approved:.0f} units).'),
    ]

    for threshold, priority, title, message in thresholds:
        if utilization >= threshold:
            # Don't create duplicate alerts for the same threshold
            existing = Notification.objects.filter(
                notification_type='auth_expiring',
                title=title,
                user=authorization.client.organization.users.filter(
                    role='admin',
                ).first(),
            ).filter(
                message__contains=authorization.authorization_number,
                created_at__gte=timezone.now() - timedelta(days=1),
            ).exists()

            if existing:
                return  # Already notified recently

            # Notify all admins and supervisors in the organization
            from apps.accounts.models import User
            recipients = User.objects.filter(
                organization=authorization.client.organization,
                role__in=['admin', 'supervisor'],
                is_active=True,
            )

            for user in recipients:
                Notification.objects.create(
                    user=user,
                    organization=authorization.client.organization,
                    notification_type='auth_expiring',
                    title=title,
                    message=message,
                    priority=priority,
                    link=f'/clients/{authorization.client_id}',
                )
            return  # Only alert for the highest threshold hit


def notify_missing_note(appointment):
    """
    Create a "missing note" notification if an attended session
    has no signed note after 24 hours.

    Called by a periodic task (or manually). Checks appointments
    that were attended > 24h ago without a corresponding signed note.
    """
    from apps.clinical.models import SessionNote

    has_note = SessionNote.objects.filter(
        appointment_id=appointment.id,
        status='signed',
    ).exists()

    if has_note:
        return

    # Check if we already sent this notification
    existing = Notification.objects.filter(
        notification_type='missing_note',
        message__contains=str(appointment.id),
        created_at__gte=timezone.now() - timedelta(days=3),
    ).exists()

    if existing:
        return

    # Notify the provider
    if appointment.provider:
        Notification.objects.create(
            user=appointment.provider,
            organization=appointment.organization,
            notification_type='missing_note',
            title='Missing Session Note',
            message=(
                f'Session with {appointment.client.full_name} on '
                f'{appointment.start_time.strftime("%b %d, %Y")} is missing a signed note. '
                f'Please complete and sign the progress note.'
            ),
            priority='high',
            link=f'/notes/new?client={appointment.client_id}',
        )


def notify_claim_denied(claim):
    """
    Create a notification when an insurance claim is denied.

    Called from ClaimViewSet when a claim status changes to 'denied'.
    """
    # Notify all billers and admins
    from apps.accounts.models import User

    org = claim.invoice.organization if claim.invoice else None
    if not org:
        return

    recipients = User.objects.filter(
        organization=org,
        role__in=['admin', 'biller'],
        is_active=True,
    )

    for user in recipients:
        Notification.objects.create(
            user=user,
            organization=org,
            notification_type='claim_denied',
            title='Insurance Claim Denied',
            message=(
                f'Claim #{claim.claim_number} for invoice '
                f'#{claim.invoice.invoice_number} has been denied. '
                f'Reason: {claim.denial_reason or "Not specified"}. '
                f'Please review and consider resubmission.'
            ),
            priority='high',
            link=f'/billing?tab=claims',
        )


def check_missing_notes_bulk(organization):
    """
    Bulk check for all attended appointments without signed notes.

    Intended to be run periodically (daily via Celery beat or management command).
    Checks all attended appointments from the last 7 days.
    """
    from apps.scheduling.models import Appointment
    from apps.clinical.models import SessionNote

    cutoff = timezone.now() - timedelta(days=7)
    threshold = timezone.now() - timedelta(hours=24)

    # Find attended appointments > 24h old without signed notes
    attended = Appointment.objects.filter(
        organization=organization,
        status='attended',
        start_time__gte=cutoff,
        start_time__lt=threshold,
    ).select_related('client', 'provider')

    for appointment in attended:
        notify_missing_note(appointment)


def check_expiring_authorizations(organization):
    """
    Bulk check for authorizations expiring within 30 days.

    Creates 'auth_expiring' notifications for admin/supervisor users.
    """
    from apps.clients.models import Authorization

    expiring_soon = Authorization.objects.filter(
        client__organization=organization,
        end_date__lte=timezone.now().date() + timedelta(days=30),
        end_date__gte=timezone.now().date(),
    ).select_related('client')

    from apps.accounts.models import User
    recipients = User.objects.filter(
        organization=organization,
        role__in=['admin', 'supervisor'],
        is_active=True,
    )

    for auth in expiring_soon:
        # Don't duplicate
        existing = Notification.objects.filter(
            notification_type='auth_expiring',
            message__contains=auth.authorization_number,
            created_at__gte=timezone.now() - timedelta(days=7),
        ).exists()

        if existing:
            continue

        days_left = (auth.end_date - timezone.now().date()).days

        for user in recipients:
            Notification.objects.create(
                user=user,
                organization=organization,
                notification_type='auth_expiring',
                title=f'Authorization Expiring in {days_left} Days',
                message=(
                    f'Authorization #{auth.authorization_number} for '
                    f'{auth.client.full_name} expires on '
                    f'{auth.end_date.strftime("%b %d, %Y")} '
                    f'({days_left} days remaining).'
                ),
                priority='high' if days_left <= 7 else 'medium',
                link=f'/clients/{auth.client_id}',
            )
