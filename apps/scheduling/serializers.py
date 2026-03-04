"""
Appointment serializers — coordinated with frontend types/appointment.ts.

Frontend expects nested objects for client, provider, location, authorization:
  - client: { id, first_name, last_name }
  - provider: { id, first_name, last_name }
  - location: { id, name } | null
  - authorization: { id, authorization_number, units_remaining } | null
  - organization_id (flat UUID, not nested object)
"""
from rest_framework import serializers
from .models import Appointment


# ─── Nested serializers for read responses ────────────────────────────────────

class AppointmentClientSerializer(serializers.Serializer):
    """Matches frontend AppointmentClient type."""
    id = serializers.UUIDField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()


class AppointmentProviderSerializer(serializers.Serializer):
    """Matches frontend AppointmentProvider type."""
    id = serializers.UUIDField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()


class AppointmentLocationSerializer(serializers.Serializer):
    """Matches frontend AppointmentLocation type."""
    id = serializers.UUIDField()
    name = serializers.CharField()


class AppointmentAuthorizationSerializer(serializers.Serializer):
    """Matches frontend AppointmentAuthorization type."""
    id = serializers.UUIDField()
    authorization_number = serializers.CharField()
    units_remaining = serializers.IntegerField()


# ─── Main Serializers ────────────────────────────────────────────────────────

class AppointmentSerializer(serializers.ModelSerializer):
    """
    Full appointment data for detail views.

    Returns nested client/provider/location/authorization objects
    exactly as the frontend Appointment type expects.
    """
    organization_id = serializers.UUIDField(source='organization.id', read_only=True)
    client = AppointmentClientSerializer(read_only=True)
    provider = AppointmentProviderSerializer(read_only=True)
    location = AppointmentLocationSerializer(read_only=True)
    authorization = AppointmentAuthorizationSerializer(read_only=True)
    duration_minutes = serializers.ReadOnlyField()

    class Meta:
        model = Appointment
        fields = [
            'id', 'organization_id',
            'client', 'provider', 'location', 'authorization',
            'start_time', 'end_time', 'duration_minutes',
            'service_code', 'units', 'status', 'notes',
            'is_recurring', 'recurrence_pattern',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'organization_id', 'created_at', 'updated_at']


class AppointmentCreateSerializer(serializers.ModelSerializer):
    """
    For creating appointments — accepts IDs as the frontend sends them:
      client_id, provider_id, location_id, authorization_id
    """
    client_id = serializers.UUIDField(write_only=False)
    provider_id = serializers.UUIDField(write_only=False)
    location_id = serializers.UUIDField(required=False, allow_null=True)
    authorization_id = serializers.UUIDField(required=False, allow_null=True)

    class Meta:
        model = Appointment
        fields = [
            'client_id', 'provider_id', 'location_id', 'authorization_id',
            'start_time', 'end_time', 'service_code', 'units',
            'notes', 'is_recurring', 'recurrence_pattern',
        ]


class AppointmentListSerializer(serializers.ModelSerializer):
    """Lightweight for calendar/list views — still has nested client/provider."""
    organization_id = serializers.UUIDField(source='organization.id', read_only=True)
    client = AppointmentClientSerializer(read_only=True)
    provider = AppointmentProviderSerializer(read_only=True)
    location = AppointmentLocationSerializer(read_only=True)
    authorization = AppointmentAuthorizationSerializer(read_only=True)

    class Meta:
        model = Appointment
        fields = [
            'id', 'organization_id',
            'client', 'provider', 'location', 'authorization',
            'start_time', 'end_time', 'service_code', 'units',
            'status', 'notes', 'is_recurring', 'recurrence_pattern',
            'created_at', 'updated_at',
        ]


class AppointmentStatusSerializer(serializers.Serializer):
    """For updating appointment status only."""
    status = serializers.ChoiceField(
        choices=['scheduled', 'attended', 'cancelled', 'no_show']
    )
