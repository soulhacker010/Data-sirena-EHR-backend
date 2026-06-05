"""
Serializers for the BLS REST API. Frontend types live in
sirena-frontend/src/types/bls.ts — keep field names in sync.

Serialization convention:
  * REST endpoints use snake_case JSON (matches Django default + DRF norm).
  * WebSocket consumers serialize messages as camelCase to match the existing
    transport contract on the frontend (see lib/blsSync.ts).
"""
from __future__ import annotations

from rest_framework import serializers

from .models import (
    BLSClientPreference,
    BLSOrgDefaults,
    BLSSession,
    BLSSessionStatus,
)


# ─── BLSSession ────────────────────────────────────────────────────────────────

class BLSSessionCreateSerializer(serializers.Serializer):
    """
    Therapist-side payload when starting a new BLS session.

    Both client_id and appointment_id are accepted; appointment_id is optional
    for ad-hoc sessions launched without a calendar context.
    """
    client_id = serializers.UUIDField()
    appointment_id = serializers.UUIDField(required=False, allow_null=True)


class BLSSessionDetailSerializer(serializers.ModelSerializer):
    """Read-side serializer for individual session detail / history rows."""
    id = serializers.UUIDField(read_only=True)
    client_id = serializers.UUIDField(source='client.id', read_only=True)
    appointment_id = serializers.SerializerMethodField()
    therapist_id = serializers.UUIDField(source='therapist.id', read_only=True)

    class Meta:
        model = BLSSession
        fields = [
            'id',
            'client_id',
            'appointment_id',
            'therapist_id',
            'status',
            'started_at',
            'ended_at',
            'pass_count',
            'set_count',
            'duration_seconds',
            'modality',
            'settings_snapshot',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields

    def get_appointment_id(self, obj):
        return str(obj.appointment_id) if obj.appointment_id else None


class BLSSessionInviteResponseSerializer(serializers.Serializer):
    """Returned to the therapist after POST /sessions/."""
    session_id = serializers.UUIDField()
    token = serializers.CharField()
    invite_url = serializers.CharField()
    expires_in_seconds = serializers.IntegerField()


class BLSSessionVerifyResponseSerializer(serializers.Serializer):
    """
    Public response from GET /sessions/verify/. INTENTIONALLY non-PHI.

    The client view uses this to know "yes the link is real, here's the
    session id so the WebSocket connection knows what to ask for." We
    deliberately do NOT return client name, therapist name, or any other
    identifying info.
    """
    valid = serializers.BooleanField()
    session_id = serializers.UUIDField(required=False)
    status = serializers.ChoiceField(
        choices=BLSSessionStatus.choices,
        required=False,
    )


class BLSSessionEndSerializer(serializers.Serializer):
    """
    Counters + settings the frontend sends with the End request, so the
    snapshot persisted to history reflects what actually happened in the
    therapist's view (the source of truth until Phase 1 consumer state).
    """
    duration_seconds = serializers.IntegerField(min_value=0)
    pass_count = serializers.IntegerField(min_value=0)
    set_count = serializers.IntegerField(min_value=0)
    settings_snapshot = serializers.JSONField(required=False)
    modality = serializers.ChoiceField(
        choices=[(m.value, m.label) for m in __import__(
            'apps.bls.models', fromlist=['BLSModality']
        ).BLSModality],
        required=False,
    )


# ─── BLSClientPreference ───────────────────────────────────────────────────────

class BLSClientPreferenceSerializer(serializers.ModelSerializer):
    """Per-client preference get/put."""
    client_id = serializers.UUIDField(source='client.id', read_only=True)

    class Meta:
        model = BLSClientPreference
        fields = ['client_id', 'config', 'last_used_at']
        read_only_fields = ['client_id', 'last_used_at']


# ─── BLSOrgDefaults ────────────────────────────────────────────────────────────

class BLSOrgDefaultsSerializer(serializers.ModelSerializer):
    """Org-wide defaults get/put."""
    class Meta:
        model = BLSOrgDefaults
        fields = ['config']
