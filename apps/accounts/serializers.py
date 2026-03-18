"""
Serializers for authentication and user management.

Coordinates with frontend types in src/types/user.ts:
- LoginSerializer → LoginRequest
- UserSerializer → AuthUser/User
- ChangePasswordSerializer → ChangePasswordPayload
"""
from django.contrib.auth import authenticate
from rest_framework import serializers
from .models import Organization, User, NPI, Location


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
            'phone', 'licenses', 'credentials',
            'is_active', 'is_supervisor',
            'organization_id', 'organization_name',
            'last_login', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'last_login', 'created_at', 'updated_at']


class UserCreateSerializer(serializers.ModelSerializer):
    """For admin creating new users."""
    password = serializers.CharField(write_only=True, min_length=8)
    organization_id = serializers.UUIDField(write_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'role',
            'phone', 'licenses', 'credentials', 'password', 'organization_id',
        ]
        read_only_fields = ['id']

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


class UserUpdateSerializer(serializers.ModelSerializer):
    """For admin updating existing users."""
    class Meta:
        model = User
        fields = [
            'first_name', 'last_name', 'role', 'licenses',
            'credentials', 'is_active',
        ]


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
