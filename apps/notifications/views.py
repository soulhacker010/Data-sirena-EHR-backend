"""Notification serializers and views."""
from rest_framework import serializers, viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    """Matches frontend Notification type in types/common.ts."""
    user_id = serializers.UUIDField(source='user.id', read_only=True)
    organization_id = serializers.UUIDField(source='user.organization.id', read_only=True)
    type = serializers.CharField(source='notification_type')
    action_url = serializers.CharField(source='link', allow_blank=True, required=False)

    class Meta:
        model = Notification
        fields = [
            'id', 'user_id', 'organization_id',
            'title', 'message', 'type', 'priority',
            'is_read', 'action_url', 'created_at',
        ]
        read_only_fields = ['id', 'user_id', 'organization_id', 'created_at']


class NotificationViewSet(viewsets.ModelViewSet):
    """
    GET    /api/v1/notifications/                         → list for current user
    PATCH  /api/v1/notifications/{id}/                    → mark as read
    POST   /api/v1/notifications/mark-all-read/           → mark all read
    DELETE /api/v1/notifications/{id}/                     → delete
    """
    permission_classes = [IsAuthenticated]
    serializer_class = NotificationSerializer
    pagination_class = None  # Frontend expects flat Notification[] array

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user)

    @action(detail=False, methods=['post'], url_path='mark-all-read')
    def mark_all_read(self, request):
        """POST /api/v1/notifications/mark-all-read/"""
        count = self.get_queryset().filter(is_read=False).update(is_read=True)
        return Response({'marked_read': count})
