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

    is_active = models.BooleanField(default=True)

    # Analytics — how the client found us
    referral_source = models.CharField(max_length=255, blank=True, default='')

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
        if not self.mrn:
            prefix = (
                (self.first_name[0] if self.first_name else 'X') +
                (self.last_name[0] if self.last_name else 'X')
            ).upper()
            last = Client.objects.filter(
                organization=self.organization,
            ).order_by('-mrn').values_list('mrn', flat=True).first()
            if last and len(last) >= 6 and last[-6:].isdigit():
                next_num = str(int(last[-6:]) + 1).zfill(6)
            else:
                next_num = '000001'
            self.mrn = f"{prefix}{next_num}"
        super().save(*args, **kwargs)


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
