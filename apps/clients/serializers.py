"""
Client and Authorization serializers.

Coordinated with frontend types/client.ts:
- ClientSerializer → Client type (organization_id, flat fields)
- ClientDetailSerializer → ClientDetail type (with recent_sessions, documents, treatment_plan)
- AuthorizationSerializer → Authorization type (client_id)
"""
from datetime import date
from rest_framework import serializers
from .models import Client, Authorization


class AuthorizationSerializer(serializers.ModelSerializer):
    """Serializer for client authorizations — matches frontend Authorization type."""
    client_id = serializers.UUIDField(source='client.id', read_only=True)
    units_remaining = serializers.ReadOnlyField()
    is_expired = serializers.ReadOnlyField()

    class Meta:
        model = Authorization
        fields = [
            'id', 'client_id', 'insurance_name', 'authorization_number',
            'service_code', 'units_approved', 'units_used', 'units_remaining',
            'start_date', 'end_date', 'is_expired', 'created_by',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'client_id', 'created_by', 'created_at', 'updated_at']


class AuthorizationCreateSerializer(serializers.ModelSerializer):
    """For creating authorizations — accepts client_id directly."""
    client_id = serializers.UUIDField(write_only=True)

    class Meta:
        model = Authorization
        fields = [
            'client_id', 'insurance_name', 'authorization_number',
            'service_code', 'units_approved', 'start_date', 'end_date',
        ]

    def create(self, validated_data):
        client_id = validated_data.pop('client_id')
        validated_data['client_id'] = client_id
        return super().create(validated_data)


class ClientListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views — matches frontend Client type."""
    organization_id = serializers.UUIDField(source='organization.id', read_only=True)
    full_name = serializers.ReadOnlyField()
    age = serializers.SerializerMethodField()

    class Meta:
        model = Client
        fields = [
            'id', 'organization_id', 'first_name', 'last_name', 'full_name',
            'date_of_birth', 'age', 'gender', 'address', 'city', 'state', 'zip_code',
            'phone', 'email',
            'emergency_contact_name', 'emergency_contact_phone',
            'insurance_primary_name', 'insurance_primary_id', 'insurance_primary_group',
            'insurance_secondary_name', 'insurance_secondary_id',
            'diagnosis_codes', 'is_active',
            'created_at', 'updated_at',
        ]

    def get_age(self, obj):
        if obj.date_of_birth:
            today = date.today()
            return today.year - obj.date_of_birth.year - (
                (today.month, today.day) < (obj.date_of_birth.month, obj.date_of_birth.day)
            )
        return None


class ClientSerializer(ClientListSerializer):
    """Full client data with embedded authorizations — for detail view."""
    authorizations = AuthorizationSerializer(many=True, read_only=True)

    class Meta(ClientListSerializer.Meta):
        fields = ClientListSerializer.Meta.fields + ['authorizations']


class ClientDetailSerializer(ClientSerializer):
    """
    Extended client for ClientDetailPage — includes related data.

    Matches frontend ClientDetail type:
    - authorizations: Authorization[]
    - recent_sessions: RecentSession[]
    - documents: ClientDocument[]
    - treatment_plan?: TreatmentPlanSummary
    """
    recent_sessions = serializers.SerializerMethodField()
    documents = serializers.SerializerMethodField()
    treatment_plan = serializers.SerializerMethodField()

    class Meta(ClientSerializer.Meta):
        fields = ClientSerializer.Meta.fields + [
            'recent_sessions', 'documents', 'treatment_plan',
        ]

    def get_recent_sessions(self, obj):
        """Return last 10 attended appointments as RecentSession[]."""
        from apps.scheduling.models import Appointment
        sessions = Appointment.objects.filter(
            client=obj,
            status='attended',
        ).select_related('provider').order_by('-start_time')[:10]

        return [
            {
                'id': str(s.id),
                'date': s.start_time.strftime('%Y-%m-%d'),
                'provider_name': s.provider.full_name if s.provider else '',
                'service_code': s.service_code or '',
                'status': s.status,
            }
            for s in sessions
        ]

    def get_documents(self, obj):
        """Return documents as ClientDocument[]."""
        from apps.clinical.models import Document
        docs = Document.objects.filter(client=obj).order_by('-created_at')[:20]

        return [
            {
                'id': str(d.id),
                'file_name': d.file_name,
                'file_type': d.file_type,
                'file_size': d.file_size,
                'file_path': '' if d.cloudinary_public_id else d.file_path,
                'document_type': d.document_type or '',
                'is_signed': d.is_signed,
                'created_at': d.created_at.isoformat(),
            }
            for d in docs
        ]

    def get_treatment_plan(self, obj):
        """Return active treatment plan as TreatmentPlanSummary."""
        from apps.clinical.models import TreatmentPlan
        plan = TreatmentPlan.objects.filter(
            client=obj, is_active=True
        ).order_by('-start_date').first()

        if not plan:
            return None

        return {
            'id': str(plan.id),
            'goals': plan.goals,
            'start_date': str(plan.start_date),
            'review_date': str(plan.review_date) if plan.review_date else None,
        }


class ClientCreateSerializer(serializers.ModelSerializer):
    """For creating clients — no nested data."""
    class Meta:
        model = Client
        fields = [
            'id', 'first_name', 'last_name', 'date_of_birth', 'gender',
            'address', 'city', 'state', 'zip_code', 'phone', 'email',
            'emergency_contact_name', 'emergency_contact_phone',
            'insurance_primary_name', 'insurance_primary_id', 'insurance_primary_group',
            'insurance_secondary_name', 'insurance_secondary_id',
            'diagnosis_codes',
        ]
        read_only_fields = ['id']
