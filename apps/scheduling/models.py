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

    client = models.ForeignKey(
        'clients.Client',
        on_delete=models.PROTECT,  # FIX CD-1: Prevent accidental loss of scheduling records
        related_name='appointments',
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
        ]

    def __str__(self):
        return f"{self.client} — {self.provider} @ {self.start_time.strftime('%Y-%m-%d %H:%M')}"

    @property
    def duration_minutes(self):
        return int((self.end_time - self.start_time).total_seconds() / 60)
