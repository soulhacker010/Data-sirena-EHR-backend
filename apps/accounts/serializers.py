"""
Serializers for authentication and user management.

Coordinates with frontend types in src/types/user.ts:
- LoginSerializer → LoginRequest
- UserSerializer → AuthUser/User
- ChangePasswordSerializer → ChangePasswordPayload
"""
from django.contrib.auth import authenticate
from rest_framework import serializers
from .models import Organization, User, NPI, Location, NotificationPreference


# ─── Auth Serializers ───────────────────────────────────────────────────────────

class LoginSerializer(serializers.Serializer):
    """Validates login credentials."""
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        user = authenticate(
            email=attrs['email'],
            password=attrs['password'],
        )
        if not user:
            raise serializers.ValidationError('Invalid email or password')
        if not user.is_active:
            raise serializers.ValidationError('Account is disabled')
        attrs['user'] = user
        return attrs


class ProfileUpdateSerializer(serializers.ModelSerializer):
    """For users updating their own profile."""
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'phone']


class ChangePasswordSerializer(serializers.Serializer):
    """Validates password change request."""
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        if attrs['new_password'] != attrs['confirm_password']:
            raise serializers.ValidationError(
                {'confirm_password': 'Passwords do not match'}
            )
        return attrs

    def validate_current_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError('Current password is incorrect')
        return value


# ─── Organization Serializer ────────────────────────────────────────────────────

class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ['id', 'name', 'tax_id', 'contact_email', 'contact_phone', 'address']
        read_only_fields = ['id']


class OrganizationMinimalSerializer(serializers.ModelSerializer):
    """Minimal org data for embedding in user responses."""
    class Meta:
        model = Organization
        fields = ['id', 'name']


# ─── User Serializers ──────────────────────────────────────────────────────────

class UserSerializer(serializers.ModelSerializer):
    """
    Full user data for GET responses — matches frontend User/AuthUser types.

    Frontend expects flat `organization_id` + `organization_name`,
    NOT a nested organization object.
    """
    organization_id = serializers.UUIDField(source='organization.id', read_only=True)
    organization_name = serializers.CharField(source='organization.name', read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'role',
            'phone', 'licenses', 'credentials', 'npi',
            'ein',
            'is_active', 'is_supervisor',
            'organization_id', 'organization_name',
            'last_login', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'last_login', 'created_at', 'updated_at']


class _NPIValidationMixin:
    """Shared NPI validator. Empty string is allowed (non-clinical roles).
    When populated, must be 10 digits passing the CMS Luhn check.
    """
    def validate_npi(self, value: str) -> str:
        from apps.accounts.npi import luhn_validate_npi
        cleaned = (value or '').strip()
        if not cleaned:
            return ''
        if not luhn_validate_npi(cleaned):
            raise serializers.ValidationError(
                'Invalid NPI. Must be 10 digits passing the CMS Luhn check '
                '(prefix 80840). Double-check the number on the NPPES registry.'
            )
        return cleaned


class _EINValidationMixin:
    """Shared validator for the per-user EIN field.

    Strips dashes/whitespace so downstream code always sees digits-only.
    Empty string is allowed (most W-2 staff bill under the practice EIN
    and don't carry their own).

    Subclasses MUST relax the auto-generated `ein` field's max_length
    via Meta.extra_kwargs (or an explicit field declaration), otherwise
    DRF inherits the model's `max_length=9` cap and rejects "83-2541331"
    (11 chars with the dash) before this validator can strip it. The
    standard incantation:

        class Meta:
            ...
            extra_kwargs = {'ein': {'max_length': 11}}
    """

    def validate_ein(self, value: str) -> str:
        import re
        cleaned = re.sub(r'\D', '', value or '')
        if cleaned and len(cleaned) != 9:
            raise serializers.ValidationError(
                'EIN must be 9 digits (e.g., 12-3456789).'
            )
        return cleaned


class UserCreateSerializer(_NPIValidationMixin, _EINValidationMixin, serializers.ModelSerializer):
    """For admin creating new users."""
    password = serializers.CharField(write_only=True, min_length=8)
    organization_id = serializers.UUIDField(write_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'role',
            'phone', 'licenses', 'credentials', 'npi',
            'ein',
            'password', 'organization_id',
        ]
        read_only_fields = ['id']
        # See _EINValidationMixin: relax max_length so a user-supplied
        # "12-3456789" makes it through to the strip-punctuation validator.
        extra_kwargs = {'ein': {'max_length': 11}}

    def create(self, validated_data):
        org_id = validated_data.pop('organization_id')
        password = validated_data.pop('password')
        user = User(**validated_data)
        user.organization_id = org_id
        user.set_password(password)
        user.save()
        return user

    def to_representation(self, instance):
        return UserSerializer(instance, context=self.context).data


class UserUpdateSerializer(_NPIValidationMixin, _EINValidationMixin, serializers.ModelSerializer):
    """For admin updating existing users.

    Allows email and phone updates (B13). Email uniqueness is enforced at the
    DB level; we add an explicit serializer-level check that excludes the
    instance being updated so a no-op save (same email) doesn't trip.

    NPI (E5) is admin-editable and Luhn-validated by the mixin.
    EIN (Dr. Joe 2026-05-12 feedback) is admin-editable. SSN was intentionally
    not added — payroll lives outside the EHR.
    """
    class Meta:
        model = User
        fields = [
            'first_name', 'last_name', 'email', 'phone', 'role',
            'licenses', 'credentials', 'npi',
            'ein',
            'is_active',
        ]
        # See _EINValidationMixin: same relaxed input width on update.
        extra_kwargs = {'ein': {'max_length': 11}}

    def validate_email(self, value: str) -> str:
        normalized = (value or '').strip().lower()
        if not normalized:
            raise serializers.ValidationError('Email cannot be blank.')
        qs = User.objects.filter(email__iexact=normalized)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return normalized


# ─── NPI / Location Serializers ─────────────────────────────────────────────────

class NPISerializer(serializers.ModelSerializer):
    class Meta:
        model = NPI
        fields = ['id', 'organization', 'npi_number', 'business_name', 'is_active']
        read_only_fields = ['id', 'organization']


class LocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Location
        fields = [
            'id', 'organization', 'name', 'address', 'city',
            'state', 'zip_code', 'is_telehealth', 'is_active',
        ]
        read_only_fields = ['id', 'organization']


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = [
            'email_appointments', 'email_billing', 'email_notes',
            'sms_reminders', 'auth_alerts', 'denial_alerts',
        ]
