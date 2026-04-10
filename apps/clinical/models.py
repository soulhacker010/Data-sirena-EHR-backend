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
        on_delete=models.PROTECT,  # FIX CD-1: Prevent accidental loss of clinical records
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
    """
    Treatment plan with structured goals for a client (BUILD 4).

    goals: list of goal objects (Problem, LTG, Objectives, Target Date, Progress, Notes, Type, Status)
    plan_data: flexible JSONB for interventions, frequency, involvement, special needs, strengths, etc.
    """
    client = models.ForeignKey(
        'clients.Client',
        on_delete=models.PROTECT,
        related_name='treatment_plans',
    )
    provider = models.ForeignKey(
        'accounts.User',
        on_delete=models.CASCADE,
        related_name='treatment_plans',
    )
    from_intake = models.ForeignKey(
        'clinical.IntakeAssessment',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='treatment_plans',
    )
    goals = models.JSONField(default=list)
    plan_data = models.JSONField(default=dict, blank=True)
    start_date = models.DateField()
    review_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(
        max_length=20,
        choices=[
            ('draft', 'Draft'),
            ('active', 'Active'),
            ('signed', 'Signed'),
            ('co_signed', 'Co-Signed'),
            ('expired', 'Expired'),
        ],
        default='draft',
    )
    is_locked = models.BooleanField(default=False)
    signature_data = models.TextField(blank=True, default='')
    signed_at = models.DateTimeField(null=True, blank=True)
    co_signed_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='cosigned_treatment_plans',
    )
    co_signed_at = models.DateTimeField(null=True, blank=True)
    supervisor_signature = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-start_date']

    def __str__(self):
        return f"Treatment Plan v{self.version} — {self.client} ({self.start_date})"


class IntakeAssessment(BaseModel):
    """
    Initial Intake / Assessment form for new clients (BUILD 3).

    Stores comprehensive intake data including presenting problem,
    diagnosis, history, MSE, risk factors, and treatment recommendations.
    Supports signature workflow similar to SessionNote.
    """
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('completed', 'Completed'),
        ('signed', 'Signed'),
        ('co_signed', 'Co-Signed'),
    ]

    client = models.ForeignKey(
        'clients.Client',
        on_delete=models.PROTECT,
        related_name='intake_assessments',
    )
    provider = models.ForeignKey(
        'accounts.User',
        on_delete=models.CASCADE,
        related_name='intake_assessments',
    )
    assessment_date = models.DateField()

    # All intake data stored as JSONB for flexibility
    # Includes: presenting_problem, primary_diagnosis, secondary_diagnoses,
    # psychiatric_history, medical_history, developmental_history,
    # trauma_history, substance_use_history, family_assessment,
    # mse_* fields, risk_factors, safety_plan, strengths, supports,
    # treatment_goals, treatment_frequency, special_needs, medical_necessity
    intake_data = models.JSONField(default=dict)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    is_locked = models.BooleanField(default=False)

    # Signature workflow
    signature_data = models.TextField(blank=True, default='')
    signed_at = models.DateTimeField(null=True, blank=True)
    co_signed_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='co_signed_intakes',
    )
    co_signed_at = models.DateTimeField(null=True, blank=True)
    supervisor_signature = models.TextField(blank=True, default='')

    # Client signature for consent
    client_signature = models.TextField(blank=True, default='')
    client_signed_at = models.DateTimeField(null=True, blank=True)

    version = models.IntegerField(default=1)

    class Meta(BaseModel.Meta):
        ordering = ['-assessment_date']
        indexes = [
            models.Index(fields=['client']),
            models.Index(fields=['provider']),
        ]

    def __str__(self):
        return f"Intake — {self.client} ({self.assessment_date})"


class Document(BaseModel):
    """
    Client document (uploaded to AWS S3).

    Supports signed documents for consent forms, etc.
    """
    client = models.ForeignKey(
        'clients.Client',
        on_delete=models.PROTECT,  # FIX CD-1: Prevent accidental loss of documents
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
    file_path = models.TextField()  # S3 key path
    s3_key = models.CharField(max_length=500, blank=True, default='')
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
