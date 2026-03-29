"""
Clinical serializers — coordinated with frontend types/note.ts.

Frontend expects:
- SessionNote: client_id, provider_id, template_id, appointment_id (not FK default names)
- NoteTemplate: organization_id (flat UUID)
- Plus computed: template_name, co_signer_name, service_code, session_date
"""
from rest_framework import serializers
from .models import NoteTemplate, SessionNote, TreatmentPlan, Document


class SessionNoteFieldsMixin:
    def _get_co_sign_request(self, obj):
        note_data = obj.note_data or {}
        co_sign_request = note_data.get('co_sign_request')
        if isinstance(co_sign_request, dict):
            return co_sign_request
        return {}

    def get_service_code(self, obj):
        if obj.appointment:
            return obj.appointment.service_code
        note_data = obj.note_data or {}
        return note_data.get('service_code') or None

    def get_session_date(self, obj):
        if obj.appointment:
            return obj.appointment.start_time.strftime('%Y-%m-%d')
        note_data = obj.note_data or {}
        return note_data.get('session_date') or None

    def get_co_sign_requested_to_id(self, obj):
        return self._get_co_sign_request(obj).get('recipient_id') or None

    def get_co_sign_requested_to_name(self, obj):
        return self._get_co_sign_request(obj).get('recipient_name') or None

    def get_co_sign_requested_at(self, obj):
        return self._get_co_sign_request(obj).get('requested_at') or None

    def get_co_sign_request_message(self, obj):
        return self._get_co_sign_request(obj).get('message') or None

    def get_is_pending_co_sign(self, obj):
        return bool(self._get_co_sign_request(obj))


class NoteTemplateSerializer(serializers.ModelSerializer):
    """Matches frontend NoteTemplate type."""
    organization_id = serializers.UUIDField(source='organization.id', read_only=True)

    class Meta:
        model = NoteTemplate
        fields = [
            'id', 'organization_id', 'name', 'template_type',
            'fields', 'required_fields', 'created_by', 'created_at',
        ]
        read_only_fields = ['id', 'organization_id', 'created_by', 'created_at']


class SessionNoteSerializer(SessionNoteFieldsMixin, serializers.ModelSerializer):
    """Full session note — matches frontend SessionNote type."""
    appointment_id = serializers.UUIDField(source='appointment.id', read_only=True, allow_null=True)
    client_id = serializers.UUIDField(source='client.id', read_only=True)
    client_name = serializers.SerializerMethodField()
    provider_id = serializers.UUIDField(source='provider.id', read_only=True)
    provider_name = serializers.SerializerMethodField()
    template_id = serializers.UUIDField(source='template.id', read_only=True, allow_null=True)
    template_name = serializers.SerializerMethodField()
    co_signer_name = serializers.SerializerMethodField()
    service_code = serializers.SerializerMethodField()
    session_date = serializers.SerializerMethodField()
    co_sign_requested_to_id = serializers.SerializerMethodField()
    co_sign_requested_to_name = serializers.SerializerMethodField()
    co_sign_requested_at = serializers.SerializerMethodField()
    co_sign_request_message = serializers.SerializerMethodField()
    is_pending_co_sign = serializers.SerializerMethodField()

    class Meta:
        model = SessionNote
        fields = [
            'id', 'appointment_id', 'client_id', 'client_name',
            'provider_id', 'provider_name',
            'template_id', 'template_name',
            'note_data', 'status',
            'signature_data', 'signed_at',
            'supervisor_signature', 'co_signed_at',
            'co_signed_by', 'co_signer_name',
            'co_sign_requested_to_id', 'co_sign_requested_to_name',
            'co_sign_requested_at', 'co_sign_request_message', 'is_pending_co_sign',
            'is_locked', 'version',
            'service_code', 'session_date',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'appointment_id', 'client_id', 'provider_id', 'template_id',
            'signature_data', 'signed_at',
            'supervisor_signature', 'co_signed_at', 'co_signed_by',
            'is_locked', 'version', 'created_at', 'updated_at',
        ]

    def get_client_name(self, obj):
        return obj.client.full_name if obj.client else None

    def get_provider_name(self, obj):
        return obj.provider.full_name if obj.provider else None

    def get_template_name(self, obj):
        return obj.template.name if obj.template else None

    def get_co_signer_name(self, obj):
        return obj.co_signed_by.full_name if obj.co_signed_by else None

class SessionNoteWriteSerializer(serializers.ModelSerializer):
    appointment_id = serializers.UUIDField(required=False, allow_null=True)
    client_id = serializers.UUIDField(required=False)
    template_id = serializers.UUIDField(required=False, allow_null=True)
    service_code = serializers.CharField(required=False, allow_blank=True)
    session_date = serializers.DateField(required=False, allow_null=True)

    class Meta:
        model = SessionNote
        fields = [
            'id', 'appointment_id', 'client_id', 'template_id',
            'note_data', 'status', 'service_code', 'session_date',
        ]
        read_only_fields = ['id']

    def _merge_note_metadata(self, validated_data):
        note_data = dict(validated_data.get('note_data') or {})

        if 'service_code' in self.initial_data:
            service_code = validated_data.pop('service_code', '')
            if service_code:
                note_data['service_code'] = service_code
            else:
                note_data.pop('service_code', None)

        if 'session_date' in self.initial_data:
            session_date = validated_data.pop('session_date', None)
            if session_date:
                note_data['session_date'] = session_date.isoformat()
            else:
                note_data.pop('session_date', None)

        validated_data['note_data'] = note_data
        return validated_data

    def create(self, validated_data):
        validated_data = self._merge_note_metadata(validated_data)
        instance = SessionNote.objects.create(**validated_data)
        return instance

    def update(self, instance, validated_data):
        validated_data = self._merge_note_metadata(validated_data)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance

    def to_representation(self, instance):
        return SessionNoteSerializer(instance, context=self.context).data


class SessionNoteListSerializer(SessionNoteFieldsMixin, serializers.ModelSerializer):
    client_id = serializers.UUIDField(source='client.id', read_only=True)
    client_name = serializers.SerializerMethodField()
    provider_id = serializers.UUIDField(source='provider.id', read_only=True)
    provider_name = serializers.SerializerMethodField()
    service_code = serializers.SerializerMethodField()
    session_date = serializers.SerializerMethodField()
    co_sign_requested_to_id = serializers.SerializerMethodField()
    co_sign_requested_to_name = serializers.SerializerMethodField()
    co_sign_requested_at = serializers.SerializerMethodField()
    is_pending_co_sign = serializers.SerializerMethodField()

    class Meta:
        model = SessionNote
        fields = [
            'id', 'client_id', 'client_name', 'provider_id', 'provider_name',
            'status', 'is_locked', 'version', 'service_code', 'session_date', 'created_at',
            'co_sign_requested_to_id', 'co_sign_requested_to_name',
            'co_sign_requested_at', 'is_pending_co_sign',
        ]

    def get_client_name(self, obj):
        return obj.client.full_name if obj.client else None

    def get_provider_name(self, obj):
        return obj.provider.full_name if obj.provider else None


class SignNoteSerializer(serializers.Serializer):
    """For signing a note."""
    signature_data = serializers.CharField()


class CoSignNoteSerializer(serializers.Serializer):
    supervisor_signature = serializers.CharField(required=False)
    supervisor_id = serializers.UUIDField(required=False)
    message = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if not attrs.get('supervisor_signature') and not attrs.get('supervisor_id'):
            raise serializers.ValidationError({
                'detail': 'Either supervisor_signature or supervisor_id is required.'
            })
        return attrs


class TreatmentPlanSerializer(serializers.ModelSerializer):
    """Matches frontend TreatmentPlan type."""
    client_id = serializers.UUIDField(source='client.id', read_only=True)
    provider_id = serializers.UUIDField(source='provider.id', read_only=True)

    class Meta:
        model = TreatmentPlan
        fields = [
            'id', 'client_id', 'provider_id', 'goals',
            'start_date', 'review_date', 'is_active',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class DocumentSerializer(serializers.ModelSerializer):
    """Matches frontend ClientDocument type.

    Fields set by the view's perform_create (client, file_name, file_type,
    file_size, file_path, uploaded_by) are read_only so serializer validation
    does not reject the upload request for missing values.
    The only user-supplied writable field is document_type.

    FIX FU-2: Previously these server-set fields were writable, causing a 400
    error on every document upload because serializer validation ran before
    perform_create could supply the values.
    """
    uploaded_by_name = serializers.SerializerMethodField()
    file_path = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = [
            'id', 'client', 'uploaded_by', 'uploaded_by_name',
            'file_name', 'file_type', 'file_size', 'file_path',
            'document_type', 'is_signed', 'signed_at', 'created_at',
        ]
        read_only_fields = [
            'id', 'client', 'uploaded_by', 'uploaded_by_name',
            'file_name', 'file_type', 'file_size', 'file_path',
            'is_signed', 'signed_at', 'created_at',
        ]

    def get_uploaded_by_name(self, obj):
        return obj.uploaded_by.full_name if obj.uploaded_by else None

    def get_file_path(self, obj):
        return obj.file_path or ''
