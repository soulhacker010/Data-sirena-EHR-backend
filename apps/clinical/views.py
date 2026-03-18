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
from .models import NoteTemplate, SessionNote, TreatmentPlan, Document
from .serializers import (
    NoteTemplateSerializer,
    SessionNoteSerializer,
    SessionNoteWriteSerializer,
    SessionNoteListSerializer,
    SignNoteSerializer,
    CoSignNoteSerializer,
    TreatmentPlanSerializer,
    DocumentSerializer,
)
from .services import NoteSigningService, DocumentStorageService


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


class SessionNoteViewSet(viewsets.ModelViewSet):
    """
    Session note CRUD with sign/co-sign actions.

    GET    /api/v1/notes/                → list notes (filterable)
    POST   /api/v1/notes/                → create draft
    GET    /api/v1/notes/{id}/           → detail
    PUT    /api/v1/notes/{id}/           → update (if not locked)
    DELETE /api/v1/notes/{id}/           → delete (if draft only)
    POST   /api/v1/notes/{id}/sign/      → sign note
    POST   /api/v1/notes/{id}/co-sign/   → co-sign note (supervisor)
    """
    permission_classes = [IsAuthenticated, IsClinicalStaff]
    filterset_fields = ['client', 'provider', 'status']
    search_fields = ['client__first_name', 'client__last_name']
    ordering_fields = ['created_at', 'signed_at']

    def get_queryset(self):
        qs = SessionNote.objects.select_related(
            'client', 'provider', 'co_signed_by', 'template'
        )

        # FIX CT-1: Both branches MUST scope by organization.
        # Clinicians see only their own notes; supervisors/admins see all.
        user = self.request.user
        if user.role == 'clinician':
            return qs.filter(
                client__organization=self.request.user.organization,
            ).filter(
                Q(provider=user)
                | Q(co_signed_by=user)
                | Q(note_data__co_sign_request__recipient_id=str(user.id))
            )

        return qs.filter(
            client__organization=self.request.user.organization
        )

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
        serializer.save(provider=self.request.user)

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
            return Response(SessionNoteSerializer(note).data)
        except ValueError as e:
            return Response(
                {'error': True, 'message': str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )


class TreatmentPlanViewSet(viewsets.ModelViewSet):
    """Treatment plan CRUD."""
    permission_classes = [IsAuthenticated, IsClinicalStaff]
    serializer_class = TreatmentPlanSerializer
    filterset_fields = ['client', 'is_active']

    def get_queryset(self):
        return TreatmentPlan.objects.filter(
            client__organization=self.request.user.organization
        ).select_related('client', 'provider')

    def perform_create(self, serializer):
        serializer.save(provider=self.request.user)


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
                cloudinary_public_id=upload_result['cloudinary_public_id'],
            )
        else:
            serializer.save(uploaded_by=self.request.user)

    def perform_destroy(self, instance):
        DocumentStorageService.delete_document(
            cloudinary_public_id=instance.cloudinary_public_id,
            file_name=instance.file_name,
            file_type=instance.file_type,
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
        return Response({'url': access_url})
