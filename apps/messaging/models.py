"""
Outbound messaging models.

`AppointmentReminder` represents one scheduled SMS for one appointment at one
lead-time (48h / 24h / 2h). The (appointment, lead_time) unique constraint is
the idempotency key — signal handlers can re-sync reminders on every appointment
save without creating duplicates, and Celery retries can't double-send.

Per CLAUDE.md: no PHI is logged here. Failures store an error category, not the
message body or any client identifier beyond the FK.
"""
from django.db import models

from apps.core.models import BaseModel


class AppointmentReminder(BaseModel):
    """
    One scheduled outbound reminder for one appointment.

    Lifecycle:
        pending     → created by signal handler, waiting for scheduled_for
        in_flight   → claimed by dispatch_due_reminders, send_reminder.delay() queued
        sent        → provider accepted; provider_message_id populated
        failed      → provider rejected after max retries; last_error categorised
        skipped     → not sent on purpose (client opted out, no phone, quiet hours stale)
        cancelled   → appointment was cancelled or moved out of reach before send
    """

    LEAD_TIME_48H = '48h'
    LEAD_TIME_24H = '24h'
    LEAD_TIME_2H = '2h'
    LEAD_TIME_CHOICES = [
        (LEAD_TIME_48H, '48 hours before'),
        (LEAD_TIME_24H, '24 hours before'),
        (LEAD_TIME_2H, '2 hours before'),
    ]
    LEAD_TIME_HOURS = {
        LEAD_TIME_48H: 48,
        LEAD_TIME_24H: 24,
        LEAD_TIME_2H: 2,
    }

    STATUS_PENDING = 'pending'
    STATUS_IN_FLIGHT = 'in_flight'
    STATUS_SENT = 'sent'
    STATUS_FAILED = 'failed'
    STATUS_SKIPPED = 'skipped'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_IN_FLIGHT, 'In flight'),
        (STATUS_SENT, 'Sent'),
        (STATUS_FAILED, 'Failed'),
        (STATUS_SKIPPED, 'Skipped'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    SKIP_REASON_OPTED_OUT = 'opted_out'
    SKIP_REASON_NO_PHONE = 'no_phone'
    SKIP_REASON_INVALID_PHONE = 'invalid_phone'
    SKIP_REASON_QUIET_HOURS_STALE = 'quiet_hours_stale'
    SKIP_REASON_PAST = 'past'
    SKIP_REASON_NON_SESSION = 'non_session_event'
    SKIP_REASON_CHOICES = [
        (SKIP_REASON_OPTED_OUT, 'Client opted out'),
        (SKIP_REASON_NO_PHONE, 'No phone number on file'),
        (SKIP_REASON_INVALID_PHONE, 'Phone failed E.164 parsing'),
        (SKIP_REASON_QUIET_HOURS_STALE, 'Quiet hours pushed past appointment'),
        (SKIP_REASON_PAST, 'Lead time was already past at scheduling'),
        (SKIP_REASON_NON_SESSION, 'Not a billable client session'),
    ]

    appointment = models.ForeignKey(
        'scheduling.Appointment',
        on_delete=models.CASCADE,
        related_name='reminders',
    )
    lead_time = models.CharField(max_length=4, choices=LEAD_TIME_CHOICES)
    scheduled_for = models.DateTimeField(
        help_text='UTC instant at which dispatch_due_reminders should pick this row up.',
    )
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING,
    )

    # Send-time bookkeeping
    attempts = models.PositiveSmallIntegerField(default=0)
    sent_at = models.DateTimeField(null=True, blank=True)
    provider = models.CharField(
        max_length=32, blank=True, default='',
        help_text='Provider that handled the send (stub, twilio).',
    )
    provider_message_id = models.CharField(
        max_length=128, blank=True, default='',
        help_text='External message ID from the SMS provider (e.g. Twilio SID).',
    )

    # Failure / skip categorisation. Never store PHI here — only an enum and a
    # short non-PHI detail (e.g. "twilio: 21610 unsubscribed").
    skip_reason = models.CharField(
        max_length=32, choices=SKIP_REASON_CHOICES, blank=True, default='',
    )
    last_error = models.CharField(
        max_length=255, blank=True, default='',
        help_text='Truncated error category from the provider. No PHI.',
    )

    class Meta(BaseModel.Meta):
        ordering = ['scheduled_for']
        constraints = [
            # Idempotency: a given appointment has at most one reminder row per
            # lead-time. Signal handlers re-sync via update_or_create against
            # this key, so editing an appointment does not create duplicates.
            models.UniqueConstraint(
                fields=['appointment', 'lead_time'],
                name='msg_reminder_appt_lead_uniq',
            ),
        ]
        indexes = [
            # Hot path: dispatch_due_reminders scans pending rows whose
            # scheduled_for is now-ish. Composite index makes this an index-only
            # scan even when the table grows to hundreds of thousands of rows.
            models.Index(
                fields=['status', 'scheduled_for'],
                name='msg_reminder_dispatch_idx',
            ),
            models.Index(fields=['appointment']),
        ]

    def __str__(self):
        return f'Reminder({self.lead_time}) for appt {self.appointment_id} → {self.status}'
