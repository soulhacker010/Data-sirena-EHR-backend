"""
Appointment model — matches backend.md §3 Scheduling table.

Supports single and recurring appointments with authorization linking.
"""
from django.db import models
from apps.core.models import OrganizationModel


class Appointment(OrganizationModel):
    """
    Scheduled appointment between a provider and client.

    Supports recurring patterns stored as JSONB:
    {
        "frequency": "weekly",    // daily, weekly, biweekly, monthly
        "days": [1, 3, 5],       // Mon=1, Tue=2, etc.
        "end_date": "2026-06-30",
        "series_id": "uuid"      // groups recurring instances
    }
    """
    STATUS_CHOICES = [
        ('scheduled', 'Scheduled'),
        ('attended', 'Attended'),
        ('cancelled', 'Cancelled'),
        ('no_show', 'No Show'),
    ]

    # E31 Half A: client is now optional. Non-session events (staff meeting,
    # personal block, training) won't carry a client. The DB CheckConstraint
    # below enforces "client_session implies client present; everything else
    # implies client absent" so we can't drift into ambiguous rows.
    client = models.ForeignKey(
        'clients.Client',
        on_delete=models.PROTECT,  # FIX CD-1: Prevent accidental loss of scheduling records
        null=True, blank=True,
        related_name='appointments',
    )

    EVENT_TYPE_CHOICES = [
        ('client_session', 'Client Session'),
        ('staff_meeting', 'Staff Meeting'),
        ('personal_block', 'Personal Block'),
        ('training', 'Training / CEU'),
        ('other', 'Other'),
    ]
    event_type = models.CharField(
        max_length=32,
        choices=EVENT_TYPE_CHOICES,
        default='client_session',
        help_text='client_session is the only billable type. Others are calendar blocks only.',
    )
    title = models.CharField(
        max_length=255, blank=True, default='',
        help_text='Title shown for non-session events. Ignored for client sessions (client name is used).',
    )
    provider = models.ForeignKey(
        'accounts.User',
        on_delete=models.CASCADE,
        related_name='appointments',
    )
    location = models.ForeignKey(
        'accounts.Location',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='appointments',
    )
    authorization = models.ForeignKey(
        'clients.Authorization',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='appointments',
    )

    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    service_code = models.CharField(max_length=50, blank=True, default='')
    modifiers = models.CharField(
        max_length=50, blank=True, default='',
        help_text='CPT modifiers (e.g., -95 for telehealth, GO/GP/GN for therapy)'
    )
    place_of_service = models.CharField(
        max_length=2, blank=True, default='11',
        help_text='Place of Service code (e.g., 11=Office, 02=Telehealth, 12=Home)'
    )
    units = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='scheduled')
    notes = models.TextField(blank=True, default='')

    # Recurring appointment fields
    is_recurring = models.BooleanField(default=False)
    recurrence_pattern = models.JSONField(null=True, blank=True)
    series_id = models.UUIDField(
        null=True, blank=True, db_index=True,
        help_text='Groups recurring appointment instances into a series',
    )

    class Meta(OrganizationModel.Meta):
        ordering = ['start_time']
        indexes = [
            models.Index(fields=['organization', 'start_time', 'end_time']),
            models.Index(fields=['client']),
            models.Index(fields=['provider']),
            models.Index(fields=['service_code']),
            models.Index(fields=['event_type']),
        ]
        constraints = [
            # E31 Half A: a row is either (client_session AND client set) OR
            # (any other type AND client null). Keeps the data clean so a
            # billing query can trust `event_type='client_session'` to imply
            # a client is present, and a non-session row never accidentally
            # gets a client attached and pulled into invoice generation.
            models.CheckConstraint(
                name='appointment_client_matches_event_type',
                check=(
                    models.Q(event_type='client_session', client__isnull=False)
                    | (~models.Q(event_type='client_session') & models.Q(client__isnull=True))
                ),
            ),
        ]

    def __str__(self):
        if self.event_type != 'client_session':
            return f'{self.title or self.get_event_type_display()} — {self.provider} @ {self.start_time:%Y-%m-%d %H:%M}'
        return f"{self.client} — {self.provider} @ {self.start_time.strftime('%Y-%m-%d %H:%M')}"

    @property
    def duration_minutes(self):
        return int((self.end_time - self.start_time).total_seconds() / 60)
