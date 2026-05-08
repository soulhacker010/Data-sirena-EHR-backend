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
            'service_code', 'modifiers', 'place_of_service', 'units', 'status', 'notes',
            'is_recurring', 'recurrence_pattern', 'series_id',
            'event_type', 'title',  # E31 Half A
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'organization_id', 'created_at', 'updated_at']


class AppointmentCreateSerializer(serializers.ModelSerializer):
    """
    For creating appointments — accepts IDs as the frontend sends them:
      client_id, provider_id, location_id, authorization_id

    E31 Half A: client_id is now optional. The serializer-level validate()
    enforces that client_id is REQUIRED for client_session events and ABSENT
    for non-session events; non-session events must carry a non-empty title.
    Mirrors the DB CheckConstraint so we surface clean 400s instead of
    bubbling up an IntegrityError.
    """
    client_id = serializers.UUIDField(required=False, allow_null=True)
    provider_id = serializers.UUIDField(write_only=False)
    location_id = serializers.UUIDField(required=False, allow_null=True)
    authorization_id = serializers.UUIDField(required=False, allow_null=True)
    service_code = serializers.CharField(required=False, allow_blank=True)
    modifiers = serializers.CharField(required=False, allow_blank=True)
    units = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True
    )
    event_type = serializers.ChoiceField(
        choices=Appointment.EVENT_TYPE_CHOICES,
        required=False,
        default='client_session',
    )
    title = serializers.CharField(required=False, allow_blank=True, max_length=255)

    class Meta:
        model = Appointment
        fields = [
            'client_id', 'provider_id', 'location_id', 'authorization_id',
            'start_time', 'end_time', 'service_code', 'modifiers', 'place_of_service', 'units',
            'notes', 'is_recurring', 'recurrence_pattern',
            'event_type', 'title',
        ]

    def validate(self, attrs):
        event_type = attrs.get('event_type', 'client_session')
        client_id = attrs.get('client_id')
        title = (attrs.get('title') or '').strip()

        if event_type == 'client_session':
            if not client_id:
                raise serializers.ValidationError({
                    'client_id': 'A client is required for client_session events.',
                })
        else:
            if client_id:
                raise serializers.ValidationError({
                    'client_id': (
                        'Non-session events (staff_meeting, personal_block, '
                        'training, other) must NOT reference a client. Leave '
                        'client_id blank or set event_type to client_session.'
                    ),
                })
            if not title:
                raise serializers.ValidationError({
                    'title': (
                        'A title is required for non-session events so the '
                        'calendar has something to display.'
                    ),
                })
        return attrs


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
            'start_time', 'end_time', 'service_code', 'modifiers', 'place_of_service', 'units',
            'status', 'notes', 'is_recurring', 'recurrence_pattern', 'series_id',
            'event_type', 'title',  # E31 Half A
            'created_at', 'updated_at',
        ]


class AppointmentStatusSerializer(serializers.Serializer):
    """For updating appointment status only."""
    status = serializers.ChoiceField(
        choices=['scheduled', 'attended', 'cancelled', 'no_show']
    )
