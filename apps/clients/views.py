"""
Client and Authorization views.

Endpoints coordinated with frontend api/clients.ts:
- GET/POST   /api/v1/clients/                              → clientsApi.getAll() / create()
- GET/PUT/DEL /api/v1/clients/{id}/                        → clientsApi.getById() / update() / delete()
- GET/POST   /api/v1/clients/{id}/authorizations/          → clientsApi.getAuthorizations()
- POST       /api/v1/authorizations/                       → clientsApi.createAuthorization()
- PUT        /api/v1/authorizations/{id}/                  → clientsApi.updateAuthorization()
- POST       /api/v1/clients/{id}/documents/               → clientsApi.uploadDocument()
- DELETE     /api/v1/clients/{id}/documents/{doc_id}/      → clientsApi.deleteDocument()
- POST       /api/v1/clients/import/                       → clientsApi.importCSV()
"""
import csv
import io
from django.db import transaction
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser

from apps.core.permissions import IsFrontDesk
from .models import Client, Authorization
from .serializers import (
    ClientSerializer,
    ClientCreateSerializer,
    ClientListSerializer,
    ClientDetailSerializer,
    AuthorizationSerializer,
    AuthorizationCreateSerializer,
)


class ClientViewSet(viewsets.ModelViewSet):
    """
    Full CRUD for clients, scoped to the user's organization.

    Supports:
    - Search by name (first_name, last_name, email, phone)
    - Filter by is_active, status
    - Ordering by last_name, created_at
    - POST /clients/import/ for CSV import
    """
    permission_classes = [IsAuthenticated, IsFrontDesk]
    filterset_fields = ['is_active']
    search_fields = ['first_name', 'last_name', 'email', 'phone']
    ordering_fields = ['last_name', 'first_name', 'created_at']

    def get_queryset(self):
        return Client.objects.filter(
            organization=self.request.user.organization
        ).prefetch_related('authorizations')

    def get_serializer_class(self):
        if self.action == 'list':
            return ClientListSerializer
        if self.action == 'create':
            return ClientCreateSerializer
        if self.action == 'retrieve':
            return ClientDetailSerializer
        return ClientSerializer

    def perform_create(self, serializer):
        """Auto-set organization from the authenticated user."""
        serializer.save(organization=self.request.user.organization)

    def perform_destroy(self, instance):
        """Soft-delete: deactivate instead of removing."""
        instance.is_active = False
        instance.save(update_fields=['is_active'])

    @action(
        detail=False,
        methods=['post'],
        url_path='import',
        parser_classes=[MultiPartParser, FormParser],
    )
    def import_csv(self, request):
        """
        POST /api/v1/clients/import/ — bulk import clients from CSV.

        FIX SV-2: Uses ClientCreateSerializer for per-row validation instead of
        raw dict.get() with dummy defaults. Wrapped in transaction.atomic()
        for all-or-nothing behavior with proper error collection.
        """
        file = request.FILES.get('file')
        if not file:
            return Response(
                {'error': True, 'message': 'No file provided'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            decoded = file.read().decode('utf-8')
            reader = csv.DictReader(io.StringIO(decoded))

            imported = 0
            row_errors = []

            with transaction.atomic():
                for row_num, row in enumerate(reader, start=2):  # row 1 is header
                    serializer = ClientCreateSerializer(data={
                        'first_name': row.get('first_name', '').strip(),
                        'last_name': row.get('last_name', '').strip(),
                        'date_of_birth': row.get('date_of_birth', '').strip() or None,
                        'gender': row.get('gender', '').strip(),
                        'phone': row.get('phone', '').strip(),
                        'email': row.get('email', '').strip(),
                        'address': row.get('address', '').strip(),
                        'city': row.get('city', '').strip(),
                        'state': row.get('state', '').strip(),
                        'zip_code': row.get('zip_code', '').strip(),
                    })
                    if serializer.is_valid():
                        serializer.save(organization=request.user.organization)
                        imported += 1
                    else:
                        row_errors.append({
                            'row': row_num,
                            'errors': serializer.errors,
                        })

            return Response({
                'imported': imported,
                'errors': len(row_errors),
                'error_details': row_errors[:20],  # Cap error detail output
            })

        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f'CSV import failed: {e}', exc_info=True)
            return Response(
                {'error': True, 'message': 'Failed to import CSV. Please check the file format.'},
                status=status.HTTP_400_BAD_REQUEST,
            )


class AuthorizationViewSet(viewsets.ModelViewSet):
    """
    Authorization CRUD — nested under client.

    GET/POST /api/v1/clients/{client_id}/authorizations/
    GET/PUT/DELETE /api/v1/clients/{client_id}/authorizations/{id}/
    """
    permission_classes = [IsAuthenticated, IsFrontDesk]
    serializer_class = AuthorizationSerializer

    def get_queryset(self):
        return Authorization.objects.filter(
            client_id=self.kwargs['client_pk'],
            client__organization=self.request.user.organization,
        )

    def perform_create(self, serializer):
        serializer.save(
            client_id=self.kwargs['client_pk'],
            created_by=self.request.user,
        )


class TopLevelAuthorizationViewSet(viewsets.ModelViewSet):
    """
    Top-level authorization routes for direct create/update.

    POST   /api/v1/authorizations/      → Create (frontend sends client_id in body)
    PUT    /api/v1/authorizations/{id}/  → Update
    """
    permission_classes = [IsAuthenticated, IsFrontDesk]

    def get_serializer_class(self):
        if self.action == 'create':
            return AuthorizationCreateSerializer
        return AuthorizationSerializer

    def get_queryset(self):
        return Authorization.objects.filter(
            client__organization=self.request.user.organization,
        )

    def perform_create(self, serializer):
        """
        FIX CT-2: Validate that client_id belongs to the user's organization.
        Without this, a malicious user could POST client_id from another org.
        """
        from apps.clients.models import Client
        client_id = self.request.data.get('client_id')
        if client_id:
            if not Client.objects.filter(
                id=client_id,
                organization=self.request.user.organization,
            ).exists():
                from rest_framework.exceptions import ValidationError
                raise ValidationError(
                    {'client_id': 'Client does not belong to your organization.'}
                )
        serializer.save(created_by=self.request.user)


class ClientDocumentViewSet(viewsets.ModelViewSet):
    """
    Client-scoped document management.

    POST   /api/v1/clients/{client_id}/documents/                → Upload
    DELETE /api/v1/clients/{client_id}/documents/{doc_id}/       → Delete

    FIX FU-1: Validates file extension, MIME type, and size before accepting.
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

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
        from apps.clinical.models import Document
        from apps.clients.models import Client
        from rest_framework.exceptions import NotFound

        # FIX FU-4: Verify the client exists in this org before returning documents.
        # Without this check, a request with a nonexistent client_pk UUID would
        # return an empty queryset and allow create to proceed (returning 201).
        client_pk = self.kwargs['client_pk']
        if not Client.objects.filter(
            id=client_pk,
            organization=self.request.user.organization,
        ).exists():
            raise NotFound('Client not found.')

        return Document.objects.filter(
            client_id=client_pk,
            client__organization=self.request.user.organization,
        )

    def get_serializer_class(self):
        from apps.clinical.serializers import DocumentSerializer
        return DocumentSerializer

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
        from apps.clients.models import Client
        from rest_framework.exceptions import NotFound, ValidationError

        # FIX FU-4: Verify client exists in this org.
        # DRF does NOT call get_queryset() on POST, so this check must live here.
        # Without it, a fake client UUID would silently create a document and
        # then fail at teardown with a FK violation.
        client_pk = self.kwargs['client_pk']
        if not Client.objects.filter(
            id=client_pk,
            organization=self.request.user.organization,
        ).exists():
            raise NotFound('Client not found.')

        file = self.request.FILES.get('file')

        # FIX FU-3: Require a file — without this guard, the DB would crash
        # with a NOT NULL violation on file_size (500 instead of 400).
        if not file:
            raise ValidationError({'file': 'A file is required for upload.'})

        self._validate_file(file)
        serializer.save(
            client_id=client_pk,
            uploaded_by=self.request.user,
            file_name=file.name,
            file_type=file.content_type or '',
            file_size=file.size,
            file_path=f"documents/{file.name}",
        )
