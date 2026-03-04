"""Audit log views — admin-only, read-only."""
from rest_framework import viewsets, mixins
from rest_framework.permissions import IsAuthenticated
from apps.core.permissions import IsAdmin
from .models import AuditLog
from .serializers import AuditLogSerializer


class AuditLogViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    GET /api/v1/audit-logs/ — admin-only audit trail.

    Filterable by action, table_name, user, and date range.
    Frontend sends start_date / end_date (not date_from / date_to).
    """
    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = AuditLogSerializer
    filterset_fields = ['action', 'table_name', 'user']
    ordering_fields = ['timestamp']

    def get_queryset(self):
        qs = AuditLog.objects.filter(
            organization=self.request.user.organization
        ).select_related('user')

        # Date range — frontend sends start_date / end_date
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        if start_date:
            qs = qs.filter(timestamp__gte=start_date)
        if end_date:
            qs = qs.filter(timestamp__lte=end_date)

        return qs
