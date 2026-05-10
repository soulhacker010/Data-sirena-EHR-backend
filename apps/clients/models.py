"""
Client and Authorization models.

Matches backend.md §3 Clients + Authorizations tables.
Coordinated with frontend types/client.ts.
"""
from django.contrib.postgres.fields import ArrayField
from django.db import models
from apps.core.models import OrganizationModel, BaseModel


class Client(OrganizationModel):
    """
    Patient/client record in the EHR system.

    Organization-scoped for multi-tenancy.
    """
    mrn = models.CharField(
        max_length=20,
        blank=True,
        default='',
        db_index=True,
        verbose_name='Medical Record Number',
        help_text='Auto-generated unique patient chart number',
    )
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    date_of_birth = models.DateField()
    gender = models.CharField(max_length=50, blank=True, default='')
    address = models.TextField(blank=True, default='')
    city = models.CharField(max_length=100, blank=True, default='')
    state = models.CharField(max_length=2, blank=True, default='')
    zip_code = models.CharField(max_length=10, blank=True, default='')
    phone = models.CharField(max_length=50, blank=True, default='')
    email = models.EmailField(blank=True, default='')

    # Emergency contact
    emergency_contact_name = models.CharField(max_length=255, blank=True, default='')
    emergency_contact_phone = models.CharField(max_length=50, blank=True, default='')

    # Insurance — Primary
    insurance_primary_name = models.CharField(max_length=255, blank=True, default='')
    insurance_primary_id = models.CharField(max_length=100, blank=True, default='')
    insurance_primary_group = models.CharField(max_length=100, blank=True, default='')

    # Insurance — Secondary
    insurance_secondary_name = models.CharField(max_length=255, blank=True, default='')
    insurance_secondary_id = models.CharField(max_length=100, blank=True, default='')

    # Clinical
    diagnosis_codes = ArrayField(
        models.CharField(max_length=20),
        blank=True,
        default=list,
    )

    # E21 (Dr. Joe 2026-05-04): service categories for color-coding lists.
    # Multi-valued because clients can receive both Psych and OT, etc. —
    # which is exactly the case Dr. Joe was working around by creating
    # duplicate client rows. With this field a single Client carries every
    # service they're enrolled in.
    SERVICE_CATEGORY_CHOICES = [
        ('psychotherapy', 'Psychotherapy'),
        ('behavior', 'Behavior / ABA'),
        ('occupational', 'Occupational Therapy'),
        ('speech', 'Speech Therapy'),
        ('biofeedback', 'Biofeedback'),
        ('assessment', 'Assessment'),
        ('other', 'Other'),
    ]
    service_categories = ArrayField(
        models.CharField(max_length=32),
        blank=True,
        default=list,
        help_text='Service categories this client is enrolled in (Psych, OT, Speech, etc.).',
    )

    is_active = models.BooleanField(default=True)

    # Analytics — how the client found us
    referral_source = models.CharField(max_length=255, blank=True, default='')

    # E26: SMS appointment reminders. HIPAA / TCPA require explicit consent
    # before sending automated texts about healthcare. Default off; flip on
    # only after the client has signed (or verbally confirmed and we logged)
    # consent. The timestamp is the audit trail Twilio asks for in their BAA.
    sms_reminders_enabled = models.BooleanField(
        default=False,
        help_text='Send SMS appointment reminders. Requires sms_consent_obtained_at.',
    )
    sms_consent_obtained_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When the client consented to receive SMS reminders.',
    )

    class Meta(OrganizationModel.Meta):
        ordering = ['last_name', 'first_name']
        indexes = [
            models.Index(fields=['organization', 'last_name', 'first_name']),
        ]

    def __str__(self):
        return f"{self.last_name}, {self.first_name}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    def save(self, *args, **kwargs):
        if not self.mrn and self.organization_id:
            self.mrn = self._generate_mrn()
        super().save(*args, **kwargs)

    def _generate_mrn(self) -> str:
        """
        Generate a chart number in the form `<INITIALS><6-digit-number>` where
        the number is one greater than the highest numeric suffix already used
        in the organization.

        We must compute the max NUMERICALLY, not by string ordering. Sorting
        MRNs as strings would let "ZZ000001" outrank "AA000099" alphabetically,
        causing the next client to be assigned suffix 000002 — colliding with
        any existing AA000002. Numeric max prevents that whole class of bug.
        """
        first_initial = (self.first_name[:1] if self.first_name else 'X').upper()
        last_initial = (self.last_name[:1] if self.last_name else 'X').upper()
        prefix = first_initial + last_initial

        existing_mrns = Client.objects.filter(
            organization=self.organization,
        ).exclude(mrn='').values_list('mrn', flat=True)

        max_num = 0
        for mrn in existing_mrns:
            if len(mrn) >= 6 and mrn[-6:].isdigit():
                num = int(mrn[-6:])
                if num > max_num:
                    max_num = num

        next_num = str(max_num + 1).zfill(6)
        return f'{prefix}{next_num}'


class Authorization(BaseModel):
    """
    Insurance authorization for a client.

    Tracks units approved vs used for a service code within a date range.
    """
    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name='authorizations',
    )
    insurance_name = models.CharField(max_length=255)
    authorization_number = models.CharField(max_length=100, blank=True, default='')
    service_code = models.CharField(max_length=50, blank=True, default='')
    units_approved = models.IntegerField()
    units_used = models.IntegerField(default=0)
    start_date = models.DateField()
    end_date = models.DateField()
    created_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_authorizations',
    )

    class Meta(BaseModel.Meta):
        ordering = ['-start_date']
        indexes = [
            models.Index(fields=['client', 'start_date', 'end_date']),
        ]

    def __str__(self):
        return f"Auth #{self.authorization_number} — {self.client}"

    @property
    def units_remaining(self):
        return self.units_approved - self.units_used

    @property
    def is_expired(self):
        from django.utils import timezone
        return self.end_date < timezone.now().date()
