"""
Clinical models: NoteTemplate, SessionNote, TreatmentPlan, Document.

Matches backend.md §3 Clinical Records tables.
Coordinated with frontend types/note.ts.
"""
from django.db import models
from apps.core.models import BaseModel, OrganizationModel


class NoteTemplate(OrganizationModel):
    """
    Template for session notes (SOAP format, etc.).

    Fields stored as JSONB:
    [
        {"name": "subjective", "label": "Subjective", "type": "textarea", "required": true},
        {"name": "objective", "label": "Objective", "type": "textarea", "required": true},
        ...
    ]
    """
    name = models.CharField(max_length=255)
    template_type = models.CharField(max_length=100, blank=True, default='')
    fields = models.JSONField(default=list)
    required_fields = models.JSONField(default=list)
    created_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_templates',
    )

    class Meta(OrganizationModel.Meta):
        ordering = ['name']

    def __str__(self):
        return self.name


class SessionNote(BaseModel):
    """
    Clinical session note tied to an appointment.

    Lifecycle: draft → completed → signed → co_signed (locked)
    """
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('completed', 'Completed'),
        ('signed', 'Signed'),
        ('co_signed', 'Co-Signed'),
    ]

    appointment = models.OneToOneField(
        'scheduling.Appointment',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='session_note',
    )
    client = models.ForeignKey(
        'clients.Client',
        on_delete=models.CASCADE,
        related_name='session_notes',
    )
    provider = models.ForeignKey(
        'accounts.User',
        on_delete=models.CASCADE,
        related_name='session_notes',
    )
    template = models.ForeignKey(
        NoteTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    # SOAP note data as JSONB
    note_data = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')

    # Signature workflow
    signature_data = models.TextField(blank=True, default='')
    signed_at = models.DateTimeField(null=True, blank=True)
    supervisor_signature = models.TextField(blank=True, default='')
    co_signed_at = models.DateTimeField(null=True, blank=True)
    co_signed_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='co_signed_notes',
    )

    is_locked = models.BooleanField(default=False)
    version = models.IntegerField(default=1)

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['client']),
            models.Index(fields=['provider']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"Note — {self.client} by {self.provider} ({self.status})"


class TreatmentPlan(BaseModel):
    """Treatment plan with goals for a client."""
    client = models.ForeignKey(
        'clients.Client',
        on_delete=models.CASCADE,
        related_name='treatment_plans',
    )
    provider = models.ForeignKey(
        'accounts.User',
        on_delete=models.CASCADE,
        related_name='treatment_plans',
    )
    goals = models.JSONField(default=list)
    start_date = models.DateField()
    review_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        ordering = ['-start_date']

    def __str__(self):
        return f"Treatment Plan — {self.client} ({self.start_date})"


class Document(BaseModel):
    """
    Client document (uploaded to Cloudinary).

    Supports signed documents for consent forms, etc.
    """
    client = models.ForeignKey(
        'clients.Client',
        on_delete=models.CASCADE,
        related_name='documents',
    )
    uploaded_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.CASCADE,
        related_name='uploaded_documents',
    )
    file_name = models.CharField(max_length=255)
    file_type = models.CharField(max_length=50)
    file_size = models.IntegerField()
    file_path = models.TextField()  # Cloudinary URL
    document_type = models.CharField(max_length=100, blank=True, default='')
    is_signed = models.BooleanField(default=False)
    signature_data = models.TextField(blank=True, default='')
    signed_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['client']),
        ]

    def __str__(self):
        return f"{self.file_name} — {self.client}"
