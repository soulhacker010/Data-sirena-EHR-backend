"""
Clinical views — session notes, templates, treatment plans, documents.

Endpoints coordinated with frontend api/notes.ts:
- SessionNote CRUD + sign + co-sign
- NoteTemplate CRUD
- Document upload/download
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from django.db.models import Q

from apps.core.permissions import IsClinicalStaff
from apps.core.audit_mixins import PHIAccessAuditMixin
from .models import (
    NoteTemplate, SessionNote, TreatmentPlan, IntakeAssessment,
    Document, Addendum, ContactNote,
)
from .serializers import (
    NoteTemplateSerializer,
    SessionNoteSerializer,
    SessionNoteWriteSerializer,
    SessionNoteListSerializer,
    SignNoteSerializer,
    CoSignNoteSerializer,
    IntakeAssessmentSerializer,
    IntakeAssessmentWriteSerializer,
    IntakeAssessmentListSerializer,
    TreatmentPlanSerializer,
    TreatmentPlanWriteSerializer,
    TreatmentPlanListSerializer,
    DocumentSerializer,
    AddendumSerializer,
    AddendumWriteSerializer,
    ContactNoteSerializer,
    ContactNoteWriteSerializer,
)
from .services import NoteSigningService, DocumentStorageService


class AddendumActionMixin:
    """
    Adds a nested `addendums` action to any parent ViewSet.

    Subclasses set ``addendum_parent_field`` to one of:
      - 'parent_session_note'
      - 'parent_intake'
      - 'parent_treatment_plan'

    GET   /<parent>/{id}/addendums/  → list, oldest first
    POST  /<parent>/{id}/addendums/  → create (body required); 201 with addendum

    Addendums are immutable after creation — there is no update or delete
    action on purpose. To "correct" an addendum, write another one.
    """
    addendum_parent_field: str = ''

    @action(detail=True, methods=['get', 'post'], url_path='addendums')
    def addendums(self, request, pk=None):
        parent = self.get_object()
        if not self.addendum_parent_field:
            raise NotImplementedError(
                'Subclass must set `addendum_parent_field` on the ViewSet.'
            )

        if request.method == 'GET':
            qs = Addendum.objects.filter(
                **{self.addendum_parent_field: parent}
            ).select_related('created_by').order_by('created_at')
            return Response(AddendumSerializer(qs, many=True).data)

        # POST — create. Body is the only writable field; author = request.user;
        # parent comes from the URL via self.get_object() (already org-scoped
        # because each parent viewset's get_queryset filters by organization).
        write = AddendumWriteSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        addendum = Addendum.objects.create(
            body=write.validated_data['body'],
            created_by=request.user,
            **{self.addendum_parent_field: parent},
        )
        return Response(
            AddendumSerializer(addendum).data,
            status=status.HTTP_201_CREATED,
        )


class NoteTemplateViewSet(viewsets.ModelViewSet):
    """
    CRUD for note templates.

    GET/POST   /api/v1/note-templates/
    GET/PUT/DEL /api/v1/note-templates/{id}/
    """
    permission_classes = [IsAuthenticated, IsClinicalStaff]
    serializer_class = NoteTemplateSerializer

    def get_queryset(self):
        return NoteTemplate.objects.filter(
            organization=self.request.user.organization
        )

    def perform_create(self, serializer):
        serializer.save(
            organization=self.request.user.organization,
            created_by=self.request.user,
        )


class SessionNoteViewSet(PHIAccessAuditMixin, AddendumActionMixin, viewsets.ModelViewSet):
    """
    Session note CRUD with sign/co-sign actions.

    GET    /api/v1/notes/                → list notes (filterable)
    POST   /api/v1/notes/                → create draft
    GET    /api/v1/notes/{id}/           → detail
    PUT    /api/v1/notes/{id}/           → update (if not locked)
    DELETE /api/v1/notes/{id}/           → delete (if draft only)
    POST   /api/v1/notes/{id}/sign/      → sign note
    POST   /api/v1/notes/{id}/co-sign/   → co-sign note (supervisor)
    GET/POST /api/v1/notes/{id}/addendums/ → list/add addendums (E18)
    """
    permission_classes = [IsAuthenticated, IsClinicalStaff]
    addendum_parent_field = 'parent_session_note'
    audit_table_name = 'notes'
    # `status` is intentionally NOT in filterset_fields — we handle it in
    # get_queryset so we can support the dashboard's `?status=pending` pseudo-
    # value (draft+completed) alongside the normal exact-match values.
    # `appointment` is in filterset_fields (E24): the calendar's "Write Note"
    # flow uses ?appointment=<id> to find an existing note for an appointment
    # so we can redirect the provider to it instead of creating a duplicate.
    filterset_fields = ['client', 'provider', 'appointment']
    search_fields = ['client__first_name', 'client__last_name']
    ordering_fields = ['created_at', 'signed_at']

    def get_queryset(self):
        qs = SessionNote.objects.select_related(
            'client', 'provider', 'co_signed_by', 'template', 'appointment'
        )

        # FIX CT-1: Both branches MUST scope by organization.
        # Clinicians see only their own notes; supervisors/admins see all.
        user = self.request.user
        if user.role == 'clinician':
            qs = qs.filter(
                client__organization=self.request.user.organization,
            ).filter(
                Q(provider=user)
                | Q(co_signed_by=user)
                | Q(note_data__co_sign_request__recipient_id=str(user.id))
            )
        else:
            qs = qs.filter(
                client__organization=self.request.user.organization
            )

        # BUILD 7.1: Filter by service_code (via linked appointment or note_data)
        service_code = self.request.query_params.get('service_code')
        if service_code:
            qs = qs.filter(
                Q(appointment__service_code=service_code)
                | Q(note_data__service_code=service_code)
            )

        # Status filter — `pending` is a pseudo-value meaning "still needs
        # attention" (draft + completed), used by the dashboard's Pending Notes
        # drill-down. Without this, the dashboard click ended up at /notes
        # showing ALL statuses including signed ones — Dr. Joe's reported bug.
        # Other values (draft, completed, signed, co_signed) are exact matches.
        status_param = self.request.query_params.get('status')
        if status_param == 'pending':
            qs = qs.filter(status__in=['draft', 'completed'])
        elif status_param:
            qs = qs.filter(status=status_param)

        return qs

    def get_serializer_class(self):
        if self.action == 'list':
            return SessionNoteListSerializer
        if self.action in ('create', 'update', 'partial_update'):
            return SessionNoteWriteSerializer
        return SessionNoteSerializer

    def perform_create(self, serializer):
        # Security: Validate client belongs to user's organization
        from apps.clients.models import Client
        client_id = serializer.validated_data.get('client_id')
        if client_id:
            org = self.request.user.organization
            if not Client.objects.filter(id=client_id, organization=org).exists():
                from rest_framework.exceptions import ValidationError
                raise ValidationError({
                    'client_id': 'Client does not belong to your organization.'
                })
        note = serializer.save(provider=self.request.user)

        # Audit: if this note was created from an appointment, log session_start.
        # (A free-standing note has no appointment_id — we only track sessions
        # that began from a scheduled appointment.)
        if note.appointment_id:
            try:
                from apps.audit.utils import write_audit
                write_audit(self.request, 'session_start', 'notes', record_id=str(note.id), changes={
                    'appointment_id': str(note.appointment_id),
                    'client_id': str(note.client_id),
                    'client_name': f'{note.client.first_name} {note.client.last_name}',
                    'provider': f'{self.request.user.first_name} {self.request.user.last_name}',
                })
            except Exception:
                pass

    def perform_update(self, serializer):
        note = self.get_object()
        if note.is_locked or note.status in ('signed', 'co_signed'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Signed or locked notes cannot be edited')
        serializer.save()

    def perform_destroy(self, instance):
        if instance.status != 'draft':
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Only draft notes can be deleted')
        instance.delete()

    @action(detail=True, methods=['post'], url_path='sign')
    def sign(self, request, pk=None):
        """POST /api/v1/notes/{id}/sign/ — sign a note."""
        note = self.get_object()
        serializer = SignNoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            note = NoteSigningService.sign_note(
                note,
                serializer.validated_data['signature_data'],
                request.user,
            )
            from apps.audit.utils import write_audit
            write_audit(request, 'sign', 'notes', record_id=str(note.id), changes={
                'client_id': str(note.client_id),
                'client_name': f'{note.client.first_name} {note.client.last_name}',
                'signed_by': f'{request.user.first_name} {request.user.last_name}',
            })
            return Response(SessionNoteSerializer(note).data)
        except ValueError as e:
            return Response(
                {'error': True, 'message': str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=['post'], url_path='cosign')
    def co_sign(self, request, pk=None):
        """POST /api/v1/notes/{id}/cosign/ — request or complete co-sign."""
        note = self.get_object()
        serializer = CoSignNoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            if serializer.validated_data.get('supervisor_id'):
                from apps.accounts.models import User
                recipient = User.objects.filter(
                    id=serializer.validated_data['supervisor_id'],
                    organization=request.user.organization,
                    is_active=True,
                ).first()
                if not recipient:
                    return Response(
                        {'error': True, 'message': 'Selected co-signer was not found in your organization.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                note = NoteSigningService.request_co_sign_note(
                    note,
                    recipient,
                    request.user,
                    serializer.validated_data.get('message', ''),
                )
            else:
                note = NoteSigningService.co_sign_note(
                    note,
                    serializer.validated_data['supervisor_signature'],
                    request.user,
                )
            from apps.audit.utils import write_audit
            write_audit(request, 'co_sign', 'notes', record_id=str(note.id), changes={
                'client_id': str(note.client_id),
                'client_name': f'{note.client.first_name} {note.client.last_name}',
                'co_signed_by': f'{request.user.first_name} {request.user.last_name}',
            })
            return Response(SessionNoteSerializer(note).data)
        except ValueError as e:
            return Response(
                {'error': True, 'message': str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=['post'], url_path='unlock')
    def unlock(self, request, pk=None):
        """POST /api/v1/notes/{id}/unlock/ — admin-only unlock."""
        note = self.get_object()
        try:
            note = NoteSigningService.unlock_note(note, request.user)
            return Response(SessionNoteSerializer(note).data)
        except ValueError as e:
            return Response(
                {'error': True, 'message': str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )


    @action(detail=False, methods=['get'], url_path='last-note')
    def last_note(self, request):
        """GET /api/v1/notes/last-note/?client={id} — most recent note for Copy from Last."""
        client_id = request.query_params.get('client')
        if not client_id:
            return Response(
                {'error': True, 'message': 'client query parameter is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        note = (
            self.get_queryset()
            .filter(client_id=client_id, status__in=['signed', 'co_signed'])
            .order_by('-signed_at')
            .first()
        )
        if not note:
            return Response(
                {'error': True, 'message': 'No previous signed note found for this client'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(SessionNoteSerializer(note).data)


class TreatmentPlanViewSet(PHIAccessAuditMixin, AddendumActionMixin, viewsets.ModelViewSet):
    """
    Treatment Plan CRUD with sign, copy, and intake-pull actions (BUILD 4).

    GET    /api/v1/treatment-plans/                        → list
    POST   /api/v1/treatment-plans/                        → create
    GET    /api/v1/treatment-plans/{id}/                   → detail
    PUT    /api/v1/treatment-plans/{id}/                   → update
    DELETE /api/v1/treatment-plans/{id}/                   → delete (draft only)
    POST   /api/v1/treatment-plans/{id}/sign/              → sign plan
    POST   /api/v1/treatment-plans/{id}/co-sign/           → co-sign plan
    GET    /api/v1/treatment-plans/copy-from-previous/     → copy from previous
    GET    /api/v1/treatment-plans/pull-intake-strengths/   → pull from intake
    GET/POST /api/v1/treatment-plans/{id}/addendums/        → list/add addendums (E18)
    """
    permission_classes = [IsAuthenticated, IsClinicalStaff]
    addendum_parent_field = 'parent_treatment_plan'
    audit_table_name = 'treatment_plans'
    filterset_fields = ['client', 'is_active', 'status']

    def get_queryset(self):
        return TreatmentPlan.objects.filter(
            client__organization=self.request.user.organization
        ).select_related('client', 'provider', 'co_signed_by')

    def get_serializer_class(self):
        if self.action == 'list':
            return TreatmentPlanListSerializer
        if self.action in ('create', 'update', 'partial_update'):
            return TreatmentPlanWriteSerializer
        return TreatmentPlanSerializer

    def create(self, request, *args, **kwargs):
        from apps.clients.models import Client
        write_ser = self.get_serializer(data=request.data)
        write_ser.is_valid(raise_exception=True)
        client_id = write_ser.validated_data.get('client_id')
        if client_id:
            org = request.user.organization
            if not Client.objects.filter(id=client_id, organization=org).exists():
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied('Client does not belong to your organization')
        instance = write_ser.save(provider=request.user)
        read_ser = TreatmentPlanSerializer(instance)
        return Response(read_ser.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.is_locked:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Treatment plan is locked and cannot be modified')
        write_ser = self.get_serializer(instance, data=request.data, partial=kwargs.get('partial', False))
        write_ser.is_valid(raise_exception=True)
        updated = write_ser.save()
        read_ser = TreatmentPlanSerializer(updated)
        return Response(read_ser.data)

    def perform_destroy(self, instance):
        if instance.status != 'draft':
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Only draft treatment plans can be deleted')
        instance.delete()

    @action(detail=True, methods=['post'], url_path='sign')
    def sign(self, request, pk=None):
        """Sign the treatment plan."""
        from django.utils import timezone
        plan = self.get_object()
        if plan.is_locked:
            return Response(
                {'error': True, 'message': 'Plan is already signed and locked'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        signature_data = request.data.get('signature_data')
        if not signature_data:
            return Response(
                {'error': True, 'message': 'signature_data is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        plan.signature_data = signature_data
        plan.signed_at = timezone.now()
        plan.status = 'signed'
        plan.is_locked = True
        plan.save()
        return Response(TreatmentPlanSerializer(plan).data)

    @action(detail=True, methods=['post'], url_path='co-sign')
    def co_sign(self, request, pk=None):
        """Co-sign the treatment plan (supervisor)."""
        from django.utils import timezone
        plan = self.get_object()
        if plan.status not in ('signed',):
            return Response(
                {'error': True, 'message': 'Plan must be signed before co-signing'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        signature_data = request.data.get('signature_data')
        if not signature_data:
            return Response(
                {'error': True, 'message': 'signature_data is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        plan.supervisor_signature = signature_data
        plan.co_signed_by = request.user
        plan.co_signed_at = timezone.now()
        plan.status = 'co_signed'
        plan.save()
        return Response(TreatmentPlanSerializer(plan).data)

    @action(detail=False, methods=['get'], url_path='copy-from-previous')
    def copy_from_previous(self, request):
        """Return the most recent signed plan for a client to copy from."""
        client_id = request.query_params.get('client')
        if not client_id:
            return Response(
                {'error': True, 'message': 'client query param is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        plan = TreatmentPlan.objects.filter(
            client_id=client_id,
            client__organization=request.user.organization,
            status__in=['signed', 'co_signed'],
        ).order_by('-start_date').first()
        if not plan:
            return Response(
                {'error': True, 'message': 'No previous signed plan found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(TreatmentPlanSerializer(plan).data)

    @action(detail=False, methods=['get'], url_path='pull-intake-strengths')
    def pull_intake_strengths(self, request):
        """Pull strengths and supports from latest intake for a client (4.10)."""
        client_id = request.query_params.get('client')
        if not client_id:
            return Response(
                {'error': True, 'message': 'client query param is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        intake = IntakeAssessment.objects.filter(
            client_id=client_id,
            client__organization=request.user.organization,
            status__in=['signed', 'co_signed'],
        ).order_by('-assessment_date').first()
        if not intake:
            return Response(
                {'error': True, 'message': 'No signed intake found for this client'},
                status=status.HTTP_404_NOT_FOUND,
            )
        data = intake.intake_data or {}
        return Response({
            'client_strengths': data.get('client_strengths', ''),
            'support_systems': data.get('support_systems', ''),
            'tentative_goals': data.get('tentative_goals', ''),
            'treatment_frequency': data.get('treatment_frequency', ''),
            'treatment_duration': data.get('treatment_duration', ''),
            'primary_diagnosis': data.get('primary_diagnosis', ''),
            'secondary_diagnoses': data.get('secondary_diagnoses', []),
        })


class IntakeAssessmentViewSet(PHIAccessAuditMixin, AddendumActionMixin, viewsets.ModelViewSet):
    """
    Intake/Initial Assessment CRUD with sign/co-sign actions (BUILD 3).

    GET    /api/v1/intakes/           → list
    POST   /api/v1/intakes/           → create
    GET    /api/v1/intakes/{id}/      → detail
    PUT    /api/v1/intakes/{id}/      → update
    DELETE /api/v1/intakes/{id}/      → delete (draft only)
    POST   /api/v1/intakes/{id}/sign/ → sign intake
    GET/POST /api/v1/intakes/{id}/addendums/ → list/add addendums (E11/E18)
    """
    permission_classes = [IsAuthenticated, IsClinicalStaff]
    addendum_parent_field = 'parent_intake'
    audit_table_name = 'intakes'
    filterset_fields = ['client', 'provider', 'status']

    def get_queryset(self):
        qs = IntakeAssessment.objects.filter(
            client__organization=self.request.user.organization
        ).select_related('client', 'provider', 'co_signed_by')

        # Date-range filter on `assessment_date` so the calendar can fetch
        # intakes alongside appointments (B10 — Dr. Joe's signed intakes were
        # not visible on the calendar because there was no way to pull them
        # by date window). Both bounds inclusive on the date the user picked.
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        if start_date:
            qs = qs.filter(assessment_date__gte=start_date)
        if end_date:
            qs = qs.filter(assessment_date__lte=end_date)

        return qs

    def get_serializer_class(self):
        if self.action == 'list':
            return IntakeAssessmentListSerializer
        if self.action in ('create', 'update', 'partial_update'):
            return IntakeAssessmentWriteSerializer
        return IntakeAssessmentSerializer

    def perform_create(self, serializer):
        from apps.clients.models import Client
        client_id = serializer.validated_data.get('client_id')
        if client_id:
            org = self.request.user.organization
            if not Client.objects.filter(id=client_id, organization=org).exists():
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied('Client does not belong to your organization')
        serializer.save(provider=self.request.user)

    def perform_update(self, serializer):
        if serializer.instance.is_locked:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Intake is locked and cannot be modified')
        serializer.save()

    def perform_destroy(self, instance):
        if instance.status != 'draft':
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Only draft intakes can be deleted')
        instance.delete()

    @action(detail=True, methods=['post'], url_path='sign')
    def sign(self, request, pk=None):
        """Sign the intake assessment."""
        from django.utils import timezone
        intake = self.get_object()
        if intake.is_locked:
            return Response(
                {'error': True, 'message': 'Intake is already signed and locked'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        signature_data = request.data.get('signature_data')
        if not signature_data:
            return Response(
                {'error': True, 'message': 'signature_data is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        intake.signature_data = signature_data
        intake.signed_at = timezone.now()
        intake.status = 'signed'
        intake.is_locked = True
        intake.save()
        return Response(IntakeAssessmentSerializer(intake).data)

    @action(detail=True, methods=['post'], url_path='client-sign')
    def client_sign(self, request, pk=None):
        """Record client signature on intake."""
        from django.utils import timezone
        intake = self.get_object()
        signature_data = request.data.get('signature_data')
        if not signature_data:
            return Response(
                {'error': True, 'message': 'signature_data is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        intake.client_signature = signature_data
        intake.client_signed_at = timezone.now()
        intake.save()
        return Response(IntakeAssessmentSerializer(intake).data)


class DocumentViewSet(viewsets.ModelViewSet):
    """
    Document upload/download/delete.

    Supports multipart file uploads via POST.

    FIX FU-1: Validates file extension, MIME type, and size before accepting.
    HIPAA requirement: only safe document types, max 10MB.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = DocumentSerializer
    parser_classes = [MultiPartParser, FormParser]
    filterset_fields = ['client', 'document_type']

    # FIX FU-1: File upload security constants
    ALLOWED_EXTENSIONS = {'.pdf', '.jpg', '.jpeg', '.png', '.docx', '.doc'}
    ALLOWED_MIME_TYPES = {
        'application/pdf',
        'image/jpeg',
        'image/png',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/msword',
    }
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

    def get_queryset(self):
        return Document.objects.filter(
            client__organization=self.request.user.organization
        ).select_related('client', 'uploaded_by')

    def _validate_file(self, file):
        """Validate file extension, MIME type, and size."""
        import os
        from rest_framework.exceptions import ValidationError

        ext = os.path.splitext(file.name)[1].lower()
        if ext not in self.ALLOWED_EXTENSIONS:
            raise ValidationError({
                'file': f'File type "{ext}" is not allowed. '
                        f'Accepted: {", ".join(sorted(self.ALLOWED_EXTENSIONS))}'
            })

        mime = (file.content_type or '').lower()
        if mime and mime not in self.ALLOWED_MIME_TYPES:
            raise ValidationError({
                'file': f'MIME type "{mime}" is not allowed.'
            })

        if file.size > self.MAX_FILE_SIZE:
            size_mb = round(file.size / (1024 * 1024), 1)
            raise ValidationError({
                'file': f'File size {size_mb}MB exceeds the 10MB limit.'
            })

    def perform_create(self, serializer):
        file = self.request.FILES.get('file')
        if file:
            self._validate_file(file)
            client = serializer.validated_data['client']
            upload_result = DocumentStorageService.upload_document(file, client)
            serializer.save(
                uploaded_by=self.request.user,
                file_name=file.name,
                file_type=file.content_type or '',
                file_size=file.size,
                file_path=upload_result['file_path'],
                s3_key=upload_result['s3_key'],
            )
        else:
            serializer.save(uploaded_by=self.request.user)

    def perform_destroy(self, instance):
        DocumentStorageService.delete_document(
            s3_key=instance.s3_key,
        )
        instance.delete()

    @action(detail=True, methods=['get'], url_path='access')
    def access(self, request, pk=None):
        instance = self.get_object()
        download = request.query_params.get('download', '').lower() in {'1', 'true', 'yes'}
        access_url = DocumentStorageService.generate_access_url(
            instance,
            as_attachment=download,
        )
        from apps.audit.utils import write_audit
        write_audit(request, 'document_download' if download else 'document_access', 'documents',
                    record_id=str(instance.id), changes={
                        'file_name': instance.file_name,
                        'client_id': str(instance.client_id),
                        'download': download,
                    })
        return Response({'url': access_url})


class ContactNoteViewSet(PHIAccessAuditMixin, viewsets.ModelViewSet):
    """
    Non-billable client contact log (E19).

    GET    /api/v1/contact-notes/?client={id}  → list (filterable by client)
    POST   /api/v1/contact-notes/              → create
    GET    /api/v1/contact-notes/{id}/         → detail
    PUT    /api/v1/contact-notes/{id}/         → update (only the author)
    DELETE /api/v1/contact-notes/{id}/         → delete (only the author or admin)

    Scoping rules:
      - All requests are org-scoped via the client's organization.
      - Clinicians see only contacts they authored. Admins/supervisors see
        all contacts in the org.
    """
    permission_classes = [IsAuthenticated, IsClinicalStaff]
    audit_table_name = 'contact_notes'
    filterset_fields = ['client', 'provider', 'contact_type']
    ordering_fields = ['contact_date', 'created_at']
    ordering = ['-contact_date']

    def get_queryset(self):
        qs = ContactNote.objects.select_related('client', 'provider').filter(
            client__organization=self.request.user.organization,
        )
        if self.request.user.role == 'clinician':
            qs = qs.filter(provider=self.request.user)
        return qs

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return ContactNoteWriteSerializer
        return ContactNoteSerializer

    def perform_create(self, serializer):
        # Validate the client belongs to the user's org before saving — same
        # pattern as SessionNoteViewSet.perform_create.
        from apps.clients.models import Client
        client_id = serializer.validated_data.get('client_id')
        if client_id:
            org = self.request.user.organization
            if not Client.objects.filter(id=client_id, organization=org).exists():
                from rest_framework.exceptions import ValidationError
                raise ValidationError({
                    'client_id': 'Client does not belong to your organization.',
                })
        serializer.save(provider=self.request.user)

    def perform_update(self, serializer):
        # Only the author may edit a contact note. Admins can fix authorship
        # mistakes via the Django admin or a dedicated endpoint, not casually
        # through this one.
        instance = self.get_object()
        if instance.provider_id != self.request.user.id:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Only the author can edit this contact note.')
        serializer.save()

    def perform_destroy(self, instance):
        if (
            instance.provider_id != self.request.user.id
            and self.request.user.role not in ('admin', 'supervisor')
        ):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied(
                'Only the author or an admin can delete a contact note.',
            )
        instance.delete()
