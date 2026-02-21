"""Audit log serializer — matches frontend AuditLog type in types/common.ts."""
from rest_framework import serializers
from .models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    """
    Frontend AuditLog type expects:
    - user_id (not raw 'user' FK)
    - organization_id
    - user_name
    - user_agent
    """
    user_id = serializers.UUIDField(source='user.id', read_only=True, allow_null=True)
    organization_id = serializers.UUIDField(source='organization.id', read_only=True)
    user_name = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = [
            'id', 'organization_id', 'user_id', 'user_name',
            'action', 'table_name', 'record_id',
            'ip_address', 'user_agent', 'changes', 'timestamp',
        ]

    def get_user_name(self, obj):
        if obj.user:
            return f"{obj.user.first_name} {obj.user.last_name}"
        return 'System'
