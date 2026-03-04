"""
Clinical serializers — coordinated with frontend types/note.ts.

Frontend expects:
- SessionNote: client_id, provider_id, template_id, appointment_id (not FK default names)
- NoteTemplate: organization_id (flat UUID)
- Plus computed: template_name, co_signer_name, service_code, session_date
"""
from rest_framework import serializers
from .models import NoteTemplate, SessionNote, TreatmentPlan, Document


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


class SessionNoteSerializer(serializers.ModelSerializer):
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

    def get_service_code(self, obj):
        if obj.appointment:
            return obj.appointment.service_code
        return None

    def get_session_date(self, obj):
        if obj.appointment:
            return obj.appointment.start_time.strftime('%Y-%m-%d')
        return None


class SessionNoteCreateSerializer(serializers.ModelSerializer):
    """For creating notes — accepts _id fields as frontend sends them."""
    appointment_id = serializers.UUIDField(required=False, allow_null=True)
    client_id = serializers.UUIDField()
    template_id = serializers.UUIDField(required=False, allow_null=True)

    class Meta:
        model = SessionNote
        fields = ['id', 'appointment_id', 'client_id', 'template_id', 'note_data']
        read_only_fields = ['id']


class SessionNoteListSerializer(serializers.ModelSerializer):
    """Lightweight for list views."""
    client_id = serializers.UUIDField(source='client.id', read_only=True)
    client_name = serializers.SerializerMethodField()
    provider_id = serializers.UUIDField(source='provider.id', read_only=True)
    provider_name = serializers.SerializerMethodField()

    class Meta:
        model = SessionNote
        fields = [
            'id', 'client_id', 'client_name', 'provider_id', 'provider_name',
            'status', 'is_locked', 'created_at',
        ]

    def get_client_name(self, obj):
        return obj.client.full_name if obj.client else None

    def get_provider_name(self, obj):
        return obj.provider.full_name if obj.provider else None


class SignNoteSerializer(serializers.Serializer):
    """For signing a note."""
    signature_data = serializers.CharField()


class CoSignNoteSerializer(serializers.Serializer):
    """For co-signing a note (supervisor)."""
    supervisor_signature = serializers.CharField()


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
