"""
Account models: Organization, User, NPI, Location.

Matches the schema defined in backend.md §3 (Organizations & Multi-Tenancy, Users & Authentication).
"""
import uuid
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.contrib.postgres.fields import ArrayField
from django.db import models
from apps.core.models import BaseModel
from .managers import UserManager


class Organization(BaseModel):
    """Multi-tenant organization (ABA therapy clinic)."""
    name = models.CharField(max_length=255)
    tax_id = models.CharField(max_length=50, blank=True, default='')
    contact_email = models.EmailField(blank=True, default='')
    contact_phone = models.CharField(max_length=50, blank=True, default='')
    address = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['name']

    def __str__(self):
        return self.name


class User(AbstractBaseUser, PermissionsMixin):
    """
    Custom user model with email-based authentication and role-based access.

    Roles: admin, clinician, supervisor, biller, front_desk
    """
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('clinician', 'Clinician'),
        ('supervisor', 'Supervisor'),
        ('biller', 'Biller'),
        ('front_desk', 'Front Desk'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='users',
        null=True,
        blank=True,
    )
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='clinician')
    licenses = ArrayField(
        models.CharField(max_length=100),
        blank=True,
        default=list,
    )
    phone = models.CharField(max_length=50, blank=True, default='')
    credentials = models.CharField(max_length=255, blank=True, default='')
    # E5: provider's individual (Type 1) NPI — distinct from the
    # organization's Type 2 NPI in the NPI table. This is the rendering
    # provider NPI that goes on Loop 2310B of an 837P claim. Validated
    # with the CMS Luhn check at the serializer layer when set; blank is
    # allowed for non-clinical roles (front desk, biller).
    npi = models.CharField(
        max_length=10,
        blank=True,
        default='',
        help_text='Individual (Type 1) NPI — 10 digits, Luhn-validated.',
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    last_login = models.DateTimeField(null=True, blank=True)
    # Idle-timeout tracking. Updated by LastSeenMiddleware on every authenticated
    # request, debounced to ≤ 1 write/min/user. The token refresh view rejects
    # refreshes when (now - last_seen_at) > IDLE_TIMEOUT_MINUTES so an
    # abandoned tab can't keep itself logged in for the full refresh-token
    # lifetime (7 days).
    last_seen_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name', 'last_name']

    class Meta:
        ordering = ['last_name', 'first_name']

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def is_supervisor(self):
        return self.role in ('supervisor', 'admin')


class NPI(BaseModel):
    """National Provider Identifier linked to an organization."""
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='npis',
    )
    npi_number = models.CharField(max_length=10, unique=True)
    business_name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        verbose_name = 'NPI'
        verbose_name_plural = 'NPIs'

    def __str__(self):
        return f"{self.npi_number} — {self.business_name}"


class Location(BaseModel):
    """Physical location or telehealth site for an organization."""
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='locations',
    )
    name = models.CharField(max_length=255)
    address = models.TextField()
    city = models.CharField(max_length=100, blank=True, default='')
    state = models.CharField(max_length=2, blank=True, default='')
    zip_code = models.CharField(max_length=10, blank=True, default='')
    is_telehealth = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_primary = models.BooleanField(
        default=False,
        help_text='Primary billing location for the organization. '
                  'Used as the Billing Provider address on claims and PDFs.',
    )

    class Meta(BaseModel.Meta):
        ordering = ['name']
        constraints = [
            # At most one primary location per organization, enforced in the DB.
            models.UniqueConstraint(
                fields=['organization'],
                condition=models.Q(is_primary=True),
                name='accounts_location_one_primary_per_org',
            ),
        ]

    def __str__(self):
        return f"{self.name} ({'Telehealth' if self.is_telehealth else self.city})"


class NotificationPreference(models.Model):
    """Per-user notification preferences."""
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='notification_preferences',
        primary_key=True,
    )
    email_appointments = models.BooleanField(default=True)
    email_billing = models.BooleanField(default=True)
    email_notes = models.BooleanField(default=False)
    sms_reminders = models.BooleanField(default=True)
    auth_alerts = models.BooleanField(default=True)
    denial_alerts = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Notification Preference'
        verbose_name_plural = 'Notification Preferences'

    def __str__(self):
        return f"NotificationPreferences({self.user})"
