"""
Base models for the Sirena Health EHR platform.

All app models should extend BaseModel (or OrganizationModel for tenant-scoped data).
"""
import uuid
from django.db import models


class BaseModel(models.Model):
    """Abstract base model with UUID primary key and timestamps."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ['-created_at']


class OrganizationManager(models.Manager):
    """Manager that auto-filters by organization when available on the request."""

    def for_organization(self, organization):
        """Filter queryset by organization."""
        return self.filter(organization=organization)


class OrganizationModel(BaseModel):
    """Abstract model for organization-scoped (multi-tenant) data."""
    organization = models.ForeignKey(
        'accounts.Organization',
        on_delete=models.CASCADE,
        related_name='%(class)ss',
    )

    objects = OrganizationManager()

    class Meta(BaseModel.Meta):
        abstract = True
