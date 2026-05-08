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


class Addendum(BaseModel):
    """
    Time-stamped, immutable amendment attached to a clinical document
    (session note, intake, or treatment plan).

    HIPAA / clinical-record convention: once a note is signed, the original
    contents must NOT be modified. Corrections, late-arriving information, or
    diagnostic changes are added as separate addendums that reference the
    original. Each addendum carries its own author and timestamp; the parent
    record remains exactly as it was at signing.

    Dr. Joe (2026-05-04 feedback):
      "*all notes/documents should be able to have an addendum to them,
       time stamped corrections or changes."
      "Intake: -need a place for an addendum as you learn more info or want
       to change diagnosis"

    Exactly one of `parent_session_note`, `parent_intake`, `parent_treatment_plan`
    must be set — enforced both at the DB level (CheckConstraint) and in
    `clean()` for friendly form errors.
    """

    body = models.TextField(
        help_text='The amendment text. Required and immutable once written.',
    )
    created_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,  # Preserve audit trail — we never delete the author
        related_name='authored_addendums',
    )

    parent_session_note = models.ForeignKey(
        'clinical.SessionNote',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='addendums',
    )
    parent_intake = models.ForeignKey(
        'clinical.IntakeAssessment',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='addendums',
    )
    parent_treatment_plan = models.ForeignKey(
        'clinical.TreatmentPlan',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='addendums',
    )

    class Meta(BaseModel.Meta):
        ordering = ['created_at']  # Chronological — oldest first under the parent
        constraints = [
            # Exactly one parent FK populated. The pattern (a IS NULL) + (b IS NULL) + ... = N-1
            # is more readable in PostgreSQL using `num_nonnulls`, but Django ORM doesn't
            # surface that — falls back to the explicit OR-of-XORs.
            models.CheckConstraint(
                name='addendum_exactly_one_parent',
                check=(
                    models.Q(
                        parent_session_note__isnull=False,
                        parent_intake__isnull=True,
                        parent_treatment_plan__isnull=True,
                    )
                    | models.Q(
                        parent_session_note__isnull=True,
                        parent_intake__isnull=False,
                        parent_treatment_plan__isnull=True,
                    )
                    | models.Q(
                        parent_session_note__isnull=True,
                        parent_intake__isnull=True,
                        parent_treatment_plan__isnull=False,
                    )
                ),
            ),
        ]
        indexes = [
            models.Index(fields=['parent_session_note', 'created_at']),
            models.Index(fields=['parent_intake', 'created_at']),
            models.Index(fields=['parent_treatment_plan', 'created_at']),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError
        parents_set = sum(
            1 for fk in (
                self.parent_session_note_id,
                self.parent_intake_id,
                self.parent_treatment_plan_id,
            ) if fk is not None
        )
        if parents_set != 1:
            raise ValidationError(
                'Addendum must reference exactly one parent document '
                '(session note, intake, or treatment plan).'
            )
        if not (self.body or '').strip():
            raise ValidationError({'body': 'Addendum body cannot be empty.'})

    @property
    def parent(self):
        """The single non-null parent reference, whichever it is."""
        return (
            self.parent_session_note
            or self.parent_intake
            or self.parent_treatment_plan
        )

    def __str__(self):
        return f'Addendum on {self.parent} by {self.created_by} @ {self.created_at:%Y-%m-%d %H:%M}'


class ContactNote(BaseModel):
    """
    Non-billable contact log: a phone call, email, voicemail, missed-appointment
    outreach, or any brief touchpoint with a client that doesn't warrant a
    full session note but must still appear in the patient record.

    Dr. Joe (2026-05-04 feedback):
      "*contact note- need something like contact note for nonbillable contacts"

    Distinct from `SessionNote` because:
      - No CPT code, no service line, never invoiced
      - No signature workflow — these are short, factual log entries
      - Authored by a single clinician; no co-sign machinery
    """

    CONTACT_TYPE_CHOICES = [
        ('phone_outbound', 'Phone (Outbound)'),
        ('phone_inbound', 'Phone (Inbound)'),
        ('voicemail_left', 'Voicemail Left'),
        ('email', 'Email'),
        ('text_message', 'Text Message'),
        ('missed_outreach', 'Missed-Appointment Outreach'),
        ('in_person_brief', 'In-Person (Brief)'),
        ('collateral', 'Collateral Contact'),  # spoke with parent/teacher/etc.
        ('other', 'Other'),
    ]

    client = models.ForeignKey(
        'clients.Client',
        on_delete=models.PROTECT,  # Patient-record continuity — never cascade-delete
        related_name='contact_notes',
    )
    provider = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,  # Audit trail — preserve the author even on user deactivation
        related_name='authored_contact_notes',
    )

    contact_date = models.DateTimeField(
        help_text='When the contact occurred (not when this note was written).',
    )
    contact_type = models.CharField(
        max_length=32, choices=CONTACT_TYPE_CHOICES,
    )
    summary = models.TextField(
        help_text='Plain-text description of the contact.',
    )
    duration_minutes = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Optional — for tracking unbilled time (e.g. long phone consults).',
    )

    class Meta(BaseModel.Meta):
        ordering = ['-contact_date']
        indexes = [
            models.Index(fields=['client', '-contact_date']),
            models.Index(fields=['provider']),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError
        if not (self.summary or '').strip():
            raise ValidationError({'summary': 'Summary cannot be empty.'})

    def __str__(self):
        return f'Contact ({self.get_contact_type_display()}) — {self.client} @ {self.contact_date:%Y-%m-%d %H:%M}'


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
