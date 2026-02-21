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

from apps.core.permissions import IsClinicalStaff, IsSupervisorOrAbove, IsOwnerOrAdmin
from .models import NoteTemplate, SessionNote, TreatmentPlan, Document
from .serializers import (
    NoteTemplateSerializer,
    SessionNoteSerializer,
    SessionNoteCreateSerializer,
    SessionNoteListSerializer,
    SignNoteSerializer,
    CoSignNoteSerializer,
    TreatmentPlanSerializer,
    DocumentSerializer,
)
from .services import NoteSigningService


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
            organization=self.request.organization
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
                provider=user,
                client__organization=self.request.organization,
            )

        return qs.filter(
            client__organization=self.request.organization
        )

    def get_serializer_class(self):
        if self.action == 'list':
            return SessionNoteListSerializer
        if self.action == 'create':
            return SessionNoteCreateSerializer
        return SessionNoteSerializer

    def perform_create(self, serializer):
        serializer.save(provider=self.request.user)

    def perform_update(self, serializer):
        note = self.get_object()
        if note.is_locked:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Locked notes cannot be edited')
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
        """POST /api/v1/notes/{id}/cosign/ — supervisor co-signs."""
        note = self.get_object()
        serializer = CoSignNoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
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
            client__organization=self.request.organization
        ).select_related('client', 'provider')

    def perform_create(self, serializer):
        serializer.save(provider=self.request.user)


class DocumentViewSet(viewsets.ModelViewSet):
    """
    Document upload/download/delete.

    Supports multipart file uploads via POST.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = DocumentSerializer
    parser_classes = [MultiPartParser, FormParser]
    filterset_fields = ['client', 'document_type']

    def get_queryset(self):
        return Document.objects.filter(
            client__organization=self.request.organization
        ).select_related('client', 'uploaded_by')

    def perform_create(self, serializer):
        # Handle file upload (store path — Cloudinary integration TBD)
        file = self.request.FILES.get('file')
        if file:
            serializer.save(
                uploaded_by=self.request.user,
                file_name=file.name,
                file_type=file.content_type or '',
                file_size=file.size,
                file_path=f"documents/{file.name}",  # Placeholder path
            )
        else:
            serializer.save(uploaded_by=self.request.user)
