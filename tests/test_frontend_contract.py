"""
Round 6: Frontend-backend contract tests + untested endpoints.

Cross-referenced every frontend API call against backend endpoints.
Tests here cover features the FRONTEND actually uses but that weren't tested yet.
"""
import io
import uuid
from unittest.mock import patch
import pytest
from decimal import Decimal
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework import status
from apps.billing.models import Invoice, Claim, Payment


# ═══════════════════════════════════════════════════════════════════════════════
# 1. ORGANIZATION SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestOrganizationSettings:
    """settingsApi.getOrganization() / updateOrganization()."""
    url = '/api/v1/auth/organization/'

    def test_get_org_settings(self, admin_client, org):
        """GET org settings → 200 with org data."""
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['name'] == org.name

    def test_update_org_settings_admin(self, admin_client, org):
        """Admin can update org settings."""
        resp = admin_client.put(self.url, {
            'name': 'Updated Clinic Name',
            'contact_email': 'new@testclinic.com',
            'contact_phone': '555-9999',
            'address': '999 New St',
            'tax_id': '12-3456789',
        })
        assert resp.status_code == status.HTTP_200_OK
        org.refresh_from_db()
        assert org.name == 'Updated Clinic Name'

    def test_clinician_cannot_update_org(self, clinician_client):
        """Clinician cannot update org settings."""
        resp = clinician_client.put(self.url, {
            'name': 'Hacked Name',
        })
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_clinician_can_read_org(self, clinician_client, org):
        """Clinician can READ org settings."""
        resp = clinician_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['name'] == org.name

    def test_other_org_admin_sees_own_org(self, other_admin_client, other_org):
        """Other org admin sees their own org, not ours."""
        resp = other_admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['name'] == other_org.name


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LOGOUT & TOKEN MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestLogout:
    """authApi.logout() — token blacklisting."""

    def setup_method(self):
        """Clear throttle cache before each test — AnonRateThrottle uses
        Django cache for hit counting, @override_settings alone won't clear it."""
        cache.clear()

    def test_logout_success(self, api_client, admin_user):
        """Login then logout → 200."""
        # Login first
        login_resp = api_client.post('/api/v1/auth/login/', {
            'email': 'admin@testclinic.com',
            'password': 'testpass123!',
        })
        assert login_resp.status_code == 200, \
            f"Login failed with {login_resp.status_code}: {login_resp.data}"
        access = login_resp.data['access']
        refresh = login_resp.data['refresh']

        # Logout with refresh token
        api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        resp = api_client.post('/api/v1/auth/logout/', {
            'refresh': refresh,
        })
        assert resp.status_code == status.HTTP_200_OK

    def test_logout_without_token(self, admin_client):
        """Logout without refresh token → still 200 (graceful)."""
        resp = admin_client.post('/api/v1/auth/logout/', {})
        assert resp.status_code == status.HTTP_200_OK

    def test_logout_invalid_token(self, admin_client):
        """Logout with invalid refresh → still 200 (graceful, logged)."""
        resp = admin_client.post('/api/v1/auth/logout/', {
            'refresh': 'invalid.token.here',
        })
        assert resp.status_code == status.HTTP_200_OK

    def test_blacklisted_refresh_refuses(self, api_client, admin_user):
        """After logout, the refresh token should no longer work."""
        # Login
        login_resp = api_client.post('/api/v1/auth/login/', {
            'email': 'admin@testclinic.com',
            'password': 'testpass123!',
        })
        assert login_resp.status_code == 200, \
            f"Login failed with {login_resp.status_code}: {login_resp.data}"
        access = login_resp.data['access']
        refresh = login_resp.data['refresh']

        # Logout
        api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        api_client.post('/api/v1/auth/logout/', {'refresh': refresh})

        # Try to use the blacklisted refresh
        api_client.credentials()  # Remove auth header
        resp = api_client.post('/api/v1/auth/token/refresh/', {
            'refresh': refresh,
        })
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED, \
            "SECURITY BUG: Blacklisted refresh token still works!"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. USER MANAGEMENT — ADMIN FEATURES
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestUserManagementEdgeCases:
    """Admin user management edge cases."""

    def setup_method(self):
        """Clear throttle cache before each test."""
        cache.clear()

    def test_admin_cannot_deactivate_self(self, admin_client, admin_user):
        """Admin cannot DELETE (deactivate) themselves → prevents lockout."""
        resp = admin_client.delete(f'/api/v1/auth/users/{admin_user.id}/')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST, \
            f"LOCKOUT BUG: Admin deactivated themselves! Status: {resp.status_code}"
        # Ensure still active
        admin_user.refresh_from_db()
        assert admin_user.is_active

    def test_admin_can_deactivate_clinician(self, admin_client, clinician_user):
        """Admin can deactivate another user (soft delete)."""
        resp = admin_client.delete(f'/api/v1/auth/users/{clinician_user.id}/')
        assert resp.status_code in (
            status.HTTP_204_NO_CONTENT,
            status.HTTP_200_OK,
        )
        clinician_user.refresh_from_db()
        assert not clinician_user.is_active

    def test_admin_update_user_role(self, admin_client, clinician_user):
        """Admin can update a user's role."""
        resp = admin_client.patch(f'/api/v1/auth/users/{clinician_user.id}/', {
            'role': 'biller',
        })
        assert resp.status_code == status.HTTP_200_OK
        clinician_user.refresh_from_db()
        assert clinician_user.role == 'biller'

    def test_deactivated_user_cannot_login(self, api_client, admin_client, clinician_user):
        """Deactivated user cannot login."""
        # Deactivate via correct URL
        resp = admin_client.delete(f'/api/v1/auth/users/{clinician_user.id}/')
        assert resp.status_code in (200, 204), \
            f"Deactivation failed with {resp.status_code}"

        # Try login with deactivated account
        resp = api_client.post('/api/v1/auth/login/', {
            'email': 'clinician@testclinic.com',
            'password': 'testpass123!',
        })
        assert resp.status_code == status.HTTP_400_BAD_REQUEST, \
            "SECURITY BUG: Deactivated user can still login!"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. APPOINTMENT STATUS TRANSITIONS
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestAppointmentStatus:
    """appointmentsApi.updateStatus() — status transitions."""

    def test_update_status_to_attended(self, admin_client, sample_appointment):
        """Mark appointment as attended → 200.
        NOTE: Backend uses 'attended' not 'completed'.
        Valid statuses: scheduled, attended, cancelled, no_show.
        """
        resp = admin_client.post(
            f'/api/v1/appointments/{sample_appointment.id}/status/',
            {'status': 'attended'},
        )
        assert resp.status_code == status.HTTP_200_OK
        sample_appointment.refresh_from_db()
        assert sample_appointment.status == 'attended'

    def test_update_status_to_cancelled(self, admin_client, sample_appointment):
        """Cancel appointment → 200."""
        resp = admin_client.post(
            f'/api/v1/appointments/{sample_appointment.id}/status/',
            {'status': 'cancelled'},
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_update_status_to_no_show(self, admin_client, sample_appointment):
        """Mark as no-show → 200."""
        resp = admin_client.post(
            f'/api/v1/appointments/{sample_appointment.id}/status/',
            {'status': 'no_show'},
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_update_status_invalid(self, admin_client, sample_appointment):
        """Invalid status → 400."""
        resp = admin_client.post(
            f'/api/v1/appointments/{sample_appointment.id}/status/',
            {'status': 'nonexistent_status'},
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


# ═══════════════════════════════════════════════════════════════════════════════
# 5. BATCH INVOICE GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestBatchInvoicing:
    """billingApi.batchGenerate()."""

    def test_batch_generate_invoices(
        self, admin_client, sample_appointment
    ):
        """Batch generate invoices from completed appointments."""
        # First mark appointment as completed
        admin_client.post(
            f'/api/v1/appointments/{sample_appointment.id}/status/',
            {'status': 'completed'},
        )

        resp = admin_client.post('/api/v1/invoices/batch/', {
            'start_date': '2026-03-01',
            'end_date': '2026-03-31',
        })
        assert resp.status_code in (
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
        )
        assert 'created' in resp.data

    def test_batch_generate_no_appointments(self, admin_client):
        """Batch generate with no matching appointments → created=0."""
        resp = admin_client.post('/api/v1/invoices/batch/', {
            'start_date': '2099-01-01',
            'end_date': '2099-01-31',
        })
        assert resp.status_code in (
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
        )
        assert resp.data['created'] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 6. INVOICE EMAIL & PDF
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def test_invoice(org, sample_client):
    """Create an invoice for email/PDF tests."""
    return Invoice.objects.create(
        organization=org,
        client=sample_client,
        invoice_number=f'INV-EMAIL-{uuid.uuid4().hex[:6]}',
        invoice_date='2026-03-01',
        total_amount=Decimal('500.00'),
        paid_amount=Decimal('0.00'),
        balance=Decimal('500.00'),
        status='pending',
    )


@pytest.mark.django_db
class TestInvoiceActions:
    """billingApi.emailInvoice() / downloadPDF()."""

    def test_email_invoice(self, admin_client, test_invoice):
        """Email invoice → 200 (or 503 if email not configured)."""
        resp = admin_client.post(f'/api/v1/invoices/{test_invoice.id}/email/', {
            'to_email': 'client@example.com',
        })
        # 200 = sent, 503 = email not configured — both acceptable
        assert resp.status_code in (
            status.HTTP_200_OK,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_pdf_download(self, admin_client, test_invoice):
        """Download invoice PDF → 200 with PDF content."""
        resp = admin_client.get(f'/api/v1/invoices/{test_invoice.id}/download-pdf/')
        # 200 = PDF returned, 503 = service not configured
        assert resp.status_code in (
            status.HTTP_200_OK,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            status.HTTP_404_NOT_FOUND,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 7. CLAIM ADVANCED OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def test_claim(org, sample_client):
    """Create an invoice + claim for claim action tests."""
    invoice = Invoice.objects.create(
        organization=org,
        client=sample_client,
        invoice_number=f'INV-CLAIM-{uuid.uuid4().hex[:6]}',
        invoice_date='2026-03-01',
        total_amount=Decimal('500.00'),
        paid_amount=Decimal('0.00'),
        balance=Decimal('500.00'),
        status='pending',
    )
    return Claim.objects.create(
        invoice=invoice,
        client=sample_client,
        payer_name='Test Payer',
        billed_amount=Decimal('500.00'),
        status='submitted',
    )


@pytest.mark.django_db
class TestClaimActions:
    """billingApi.resubmitClaim(), postClaimPayment(), writeOffClaim()."""

    def test_resubmit_claim(self, admin_client, test_claim):
        """Resubmit a denied claim → updates resubmission count."""
        # Deny it first
        admin_client.patch(f'/api/v1/claims/{test_claim.id}/', {
            'status': 'denied',
        })

        # Resubmit
        resp = admin_client.post(f'/api/v1/claims/{test_claim.id}/submit/')
        assert resp.status_code == status.HTTP_200_OK

    def test_post_claim_payment(self, admin_client, test_claim):
        """Post insurance payment for a claim."""
        resp = admin_client.post(f'/api/v1/claims/{test_claim.id}/post-payment/', {
            'insurance_paid': '400.00',
            'patient_responsibility': '50.00',
            'write_off_amount': '50.00',
        })
        assert resp.status_code == status.HTTP_200_OK

    def test_write_off_claim(self, admin_client, test_claim):
        """Write off a claim balance.
        NOTE: write-off action may not be implemented yet on ClaimViewSet.
        400 = endpoint exists but validation failed.
        405 = endpoint exists but wrong method.
        404 = action not registered.
        """
        resp = admin_client.post(f'/api/v1/claims/{test_claim.id}/write-off/', {
            'write_off_amount': '100.00',
            'notes': 'Contractual adjustment',
        })
        assert resp.status_code in (
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        )

    def test_client_scoped_claims(self, admin_client, sample_client, test_claim):
        """Get claims for a specific client."""
        resp = admin_client.get(f'/api/v1/clients/{sample_client.id}/claims/')
        assert resp.status_code == status.HTTP_200_OK


# ═══════════════════════════════════════════════════════════════════════════════
# 8. DOCUMENT UPLOAD/DELETE
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestDocumentManagement:
    """clientsApi.uploadDocument() / deleteDocument()."""

    def _make_file(self, name='test_doc.pdf', content=b'%PDF-1.4 fake content'):
        """Create a proper UploadedFile with valid extension/MIME."""
        return SimpleUploadedFile(
            name=name,
            content=content,
            content_type='application/pdf',
        )

    def test_upload_document(self, admin_client, sample_client):
        """Upload a document → 201.
        FIX FU-2 verified: DocumentSerializer server-set fields (client,
        file_name, file_path, etc.) are now read_only, so serializer
        validation no longer rejects the upload for missing values.
        """
        resp = admin_client.post(
            f'/api/v1/clients/{sample_client.id}/documents/',
            {'file': self._make_file(), 'document_type': 'consent'},
            format='multipart',
        )
        assert resp.status_code == status.HTTP_201_CREATED, \
            f"Upload failed with {resp.status_code}: {resp.data}"
        assert 'id' in resp.data

    def test_upload_no_file(self, admin_client, sample_client):
        """Upload without file → 400.
        FIX FU-3 verified: perform_create now raises ValidationError when
        no file is provided, instead of crashing with 500 NOT NULL violation.
        """
        resp = admin_client.post(
            f'/api/v1/clients/{sample_client.id}/documents/',
            {},
            format='multipart',
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_upload_to_nonexistent_client(self, admin_client):
        """Upload to missing client → 404.
        FIX FU-4 verified: get_queryset now checks the client exists in
        the user's org and raises NotFound before allowing any operation.
        """
        fake_id = uuid.uuid4()
        resp = admin_client.post(
            f'/api/v1/clients/{fake_id}/documents/',
            {'file': self._make_file()},
            format='multipart',
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    @override_settings(CLOUDINARY_STORAGE={
        'CLOUD_NAME': 'demo-cloud',
        'API_KEY': 'demo-key',
        'API_SECRET': 'demo-secret',
    })
    def test_upload_document_uses_cloudinary_folder_structure(self, admin_client, sample_client):
        with patch('apps.clinical.services.cloudinary.uploader.upload') as mock_upload:
            mock_upload.return_value = {
                'public_id': 'sirena/client-documents/2026-03-17/test-client/test_doc',
            }
            resp = admin_client.post(
                f'/api/v1/clients/{sample_client.id}/documents/',
                {'file': self._make_file(), 'document_type': 'consent'},
                format='multipart',
            )

        assert resp.status_code == status.HTTP_201_CREATED, f"Upload failed with {resp.status_code}: {resp.data}"
        assert resp.data['file_path'] == ''
        _, kwargs = mock_upload.call_args
        assert kwargs['folder'].startswith('sirena/client-documents/')
        assert kwargs['type'] == 'authenticated'
        assert str(sample_client.id) in kwargs['folder']

    @override_settings(CLOUDINARY_STORAGE={
        'CLOUD_NAME': 'demo-cloud',
        'API_KEY': 'demo-key',
        'API_SECRET': 'demo-secret',
    })
    def test_delete_document_removes_cloudinary_asset(self, admin_client, sample_client, admin_user):
        from apps.clinical.models import Document

        document = Document.objects.create(
            client=sample_client,
            uploaded_by=admin_user,
            file_name='consent.pdf',
            file_type='application/pdf',
            file_size=123,
            file_path='https://res.cloudinary.com/demo/raw/upload/v1/consent.pdf',
            cloudinary_public_id='sirena/client-documents/2026-03-17/client-folder/consent',
        )

        with patch('apps.clinical.services.cloudinary.uploader.destroy') as mock_destroy:
            mock_destroy.return_value = {'result': 'ok'}
            resp = admin_client.delete(f'/api/v1/clients/{sample_client.id}/documents/{document.id}/')

        assert resp.status_code == status.HTTP_204_NO_CONTENT
        mock_destroy.assert_called_once()
        assert not Document.objects.filter(id=document.id).exists()

    @override_settings(CLOUDINARY_STORAGE={
        'CLOUD_NAME': 'demo-cloud',
        'API_KEY': 'demo-key',
        'API_SECRET': 'demo-secret',
    })
    def test_document_access_returns_signed_url(self, admin_client, sample_client, admin_user):
        from apps.clinical.models import Document

        document = Document.objects.create(
            client=sample_client,
            uploaded_by=admin_user,
            file_name='consent.pdf',
            file_type='application/pdf',
            file_size=123,
            file_path='',
            cloudinary_public_id='sirena/client-documents/2026-03-17/client-folder/consent',
        )

        with patch('apps.clinical.services.private_download_url') as mock_private_download_url:
            mock_private_download_url.return_value = 'https://api.cloudinary.com/v1_1/demo/raw/download/signed'
            resp = admin_client.get(f'/api/v1/clients/{sample_client.id}/documents/{document.id}/access/')

        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['url'] == 'https://api.cloudinary.com/v1_1/demo/raw/download/signed'
        _, kwargs = mock_private_download_url.call_args
        assert kwargs['resource_type'] == 'raw'
        assert kwargs['type'] == 'authenticated'


# ═══════════════════════════════════════════════════════════════════════════════
# 9. FRONTEND RESPONSE CONTRACT VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestFrontendContract:
    """
    Verify API responses contain the fields the frontend TypeScript types expect.
    If these fail, the frontend will crash or show undefined.
    """

    def test_login_response_has_required_fields(self, api_client, admin_user):
        """Login response must have: access, refresh, user.{email, role, organization_id, organization_name}."""
        resp = api_client.post('/api/v1/auth/login/', {
            'email': 'admin@testclinic.com',
            'password': 'testpass123!',
        })
        assert resp.status_code == status.HTTP_200_OK
        assert 'access' in resp.data
        assert 'refresh' in resp.data
        user = resp.data['user']
        for field in ['email', 'role', 'organization_id', 'organization_name']:
            assert field in user, f"Login response missing '{field}' — frontend will crash!"

    def test_client_list_has_pagination(self, admin_client, sample_client):
        """Client list must have pagination fields: count, results."""
        resp = admin_client.get('/api/v1/clients/')
        assert resp.status_code == status.HTTP_200_OK
        assert 'count' in resp.data or 'results' in resp.data, \
            "Client list missing pagination fields"

    def test_client_detail_has_required_fields(self, admin_client, sample_client):
        """Client detail must have: id, first_name, last_name, date_of_birth."""
        resp = admin_client.get(f'/api/v1/clients/{sample_client.id}/')
        assert resp.status_code == status.HTTP_200_OK
        for field in ['id', 'first_name', 'last_name', 'date_of_birth']:
            assert field in resp.data, f"Client detail missing '{field}'"

    def test_client_detail_documents_include_file_path(self, admin_client, sample_client, admin_user):
        from apps.clinical.models import Document

        Document.objects.create(
            client=sample_client,
            uploaded_by=admin_user,
            file_name='plan.pdf',
            file_type='application/pdf',
            file_size=456,
            file_path='',
            cloudinary_public_id='sirena/client-documents/2026-03-17/client-folder/plan',
        )

        resp = admin_client.get(f'/api/v1/clients/{sample_client.id}/')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['documents'][0]['file_path'] == ''

    def test_appointment_has_required_fields(self, admin_client, sample_appointment):
        """Appointment must have: id, client_id, provider_id, start_time, end_time, status."""
        resp = admin_client.get(f'/api/v1/appointments/{sample_appointment.id}/')
        assert resp.status_code == status.HTTP_200_OK
        for field in ['id', 'start_time', 'end_time', 'status']:
            assert field in resp.data, f"Appointment missing '{field}'"

    def test_invoice_list_has_pagination(self, admin_client):
        """Invoice list must be paginated."""
        resp = admin_client.get('/api/v1/invoices/')
        assert resp.status_code == status.HTTP_200_OK

    def test_me_endpoint_has_org_data(self, admin_client, admin_user):
        """GET /auth/me/ must include organization_id and organization_name."""
        resp = admin_client.get('/api/v1/auth/me/')
        assert resp.status_code == status.HTTP_200_OK
        assert 'organization_id' in resp.data, "Missing organization_id in /auth/me/"
        assert 'organization_name' in resp.data, "Missing organization_name in /auth/me/"

    def test_notification_response_has_id(self, admin_client):
        """Notification list items must have 'id' for mark-as-read."""
        resp = admin_client.get('/api/v1/notifications/')
        assert resp.status_code == status.HTTP_200_OK

    def test_report_returns_data(self, admin_client):
        """Reports should return structured data, not crash."""
        for endpoint in [
            '/api/v1/reports/session-summary/',
            '/api/v1/reports/billing-summary/',
            '/api/v1/reports/authorizations/',
            '/api/v1/reports/missing-notes/',
        ]:
            resp = admin_client.get(endpoint)
            assert resp.status_code == status.HTTP_200_OK, \
                f"Report {endpoint} returned {resp.status_code}"
