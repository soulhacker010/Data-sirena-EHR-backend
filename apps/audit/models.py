"""
Audit log model for HIPAA compliance.

Records all write operations with user, action, table, changes, and client IP.
Matches backend.md §3 audit_logs table.
"""
import uuid
from django.db import models


class AuditLog(models.Model):
    """Immutable audit trail — never updated or deleted."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'accounts.Organization',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs',
    )
    user = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='audit_logs',
    )
    action = models.CharField(max_length=100)  # create, update, delete
    table_name = models.CharField(max_length=100, blank=True, default='')
    record_id = models.UUIDField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default='')
    changes = models.JSONField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['user']),
            models.Index(fields=['table_name', 'record_id']),
        ]

    def __str__(self):
        return f"{self.user} — {self.action} — {self.table_name} @ {self.timestamp}"
