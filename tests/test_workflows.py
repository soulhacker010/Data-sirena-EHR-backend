"""
Round 4: Workflow integration, audit logging, CSV import, and role escalation tests.

These simulate REAL USER JOURNEYS — multi-step workflows where bugs most commonly hide.
For EHR production readiness, this is the most critical round.

Tests:
1. Full clinical workflow: create client → book appt → write note → sign
2. CSV import: valid files, malformed files, missing columns
3. Audit trail: every write creates an audit log (HIPAA compliance)
4. Role escalation: users can't change their own role
5. Session integrity: updating your own profile
6. Multi-org isolation: full journey in two orgs, data never leaks
"""
import io
import pytest
from rest_framework import status


# ═══════════════════════════════════════════════════════════════════════════════
# 1. FULL CLINICAL WORKFLOW — THE MOST IMPORTANT TEST
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestFullClinicalWorkflow:
    """
    Simulates a real day at the clinic:
    Admin creates client → clinician books appointment → clinician writes note → signs it.
    
    This catches integration bugs that unit tests miss.
    """

    def test_complete_clinical_journey(
        self, admin_client, clinician_client, clinician_user, org
    ):
        """Full journey: create client → book appt → create note → sign."""

        # ── Step 1: Admin creates a new client ────────────────────────
        client_resp = admin_client.post('/api/v1/clients/', {
            'first_name': 'Emma',
            'last_name': 'Rodriguez',
            'date_of_birth': '2019-08-22',
            'gender': 'female',
            'phone': '555-0201',
        })
        assert client_resp.status_code == status.HTTP_201_CREATED, \
            f"Step 1 FAILED: Client creation returned {client_resp.status_code}: {client_resp.data}"
        new_client_id = client_resp.data['id']
        assert new_client_id is not None, "Step 1 FAILED: No ID returned for new client"

        # ── Step 2: Admin books an appointment ────────────────────────
        appt_resp = admin_client.post('/api/v1/appointments/', {
            'client_id': new_client_id,
            'provider_id': str(clinician_user.id),
            'start_time': '2026-04-01T09:00:00Z',
            'end_time': '2026-04-01T11:00:00Z',
            'service_code': '97153',
            'units': 8,
        })
        assert appt_resp.status_code == status.HTTP_201_CREATED, \
            f"Step 2 FAILED: Appointment creation returned {appt_resp.status_code}: {appt_resp.data}"

        # ── Step 3: Clinician creates a session note ──────────────────
        note_resp = clinician_client.post('/api/v1/notes/', {
            'client_id': new_client_id,
            'note_data': {
                'objectives': 'Work on social skills',
                'interventions': 'DTT, Natural Environment Training',
                'client_response': 'Engaged well, 80% correct trials',
                'notes': 'Good session, client was cooperative',
            },
        }, format='json')
        assert note_resp.status_code == status.HTTP_201_CREATED, \
            f"Step 3 FAILED: Note creation returned {note_resp.status_code}: {note_resp.data}"
        note_id = note_resp.data['id']
        assert note_id is not None, "Step 3 FAILED: No ID returned for new note"

        # ── Step 4: Clinician signs the note ──────────────────────────
        sign_resp = clinician_client.post(f'/api/v1/notes/{note_id}/sign/', {
            'signature_data': 'data:image/png;base64,clinician_sig_hash',
        }, format='json')
        assert sign_resp.status_code == status.HTTP_200_OK, \
            f"Step 4 FAILED: Note signing returned {sign_resp.status_code}: {sign_resp.data}"
        assert sign_resp.data['status'] == 'signed'

        # ── Step 5: Verify the note is now locked ─────────────────────
        edit_resp = clinician_client.patch(f'/api/v1/notes/{note_id}/', {
            'note_data': {'objectives': 'TAMPERED'},
        }, format='json')
        assert edit_resp.status_code == status.HTTP_403_FORBIDDEN, \
            f"Step 5 FAILED: Signed note was editable! Status: {edit_resp.status_code}"

        # ── Step 6: Verify client detail shows the session ────────────
        detail_resp = admin_client.get(f'/api/v1/clients/{new_client_id}/')
        assert detail_resp.status_code == status.HTTP_200_OK
        assert detail_resp.data['first_name'] == 'Emma'

    def test_admin_creates_client_clinician_sees_it(
        self, admin_client, clinician_client
    ):
        """Data created by admin is visible to clinician in same org."""
        # Admin creates client
        resp = admin_client.post('/api/v1/clients/', {
            'first_name': 'Shared',
            'last_name': 'Visibility',
            'date_of_birth': '2020-01-01',
            'gender': 'male',
        })
        assert resp.status_code == status.HTTP_201_CREATED
        client_id = resp.data['id']

        # Clinician can see the client
        detail = clinician_client.get(f'/api/v1/clients/{client_id}/')
        assert detail.status_code == status.HTTP_200_OK
        assert detail.data['first_name'] == 'Shared'


# ═══════════════════════════════════════════════════════════════════════════════
# 2. CSV IMPORT TESTS
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestCSVImport:
    """Test bulk client CSV import — a common real-world operation."""
    url = '/api/v1/clients/import/'

    def _make_csv(self, content):
        """Helper to create an in-memory CSV file."""
        f = io.BytesIO(content.encode('utf-8'))
        f.name = 'clients.csv'
        return f

    def test_import_valid_csv(self, admin_client):
        """Import 3 valid clients → imported=3."""
        csv_content = (
            "first_name,last_name,date_of_birth,gender,phone,email\n"
            "Alice,Smith,2018-03-15,female,555-0301,alice@test.com\n"
            "Bob,Jones,2017-07-22,male,555-0302,bob@test.com\n"
            "Carlos,Garcia,2019-01-10,male,555-0303,carlos@test.com\n"
        )
        resp = admin_client.post(
            self.url,
            {'file': self._make_csv(csv_content)},
            format='multipart',
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['imported'] == 3
        assert resp.data['errors'] == 0

    def test_import_csv_with_bad_rows(self, admin_client):
        """CSV with some invalid rows → partial import with error details."""
        csv_content = (
            "first_name,last_name,date_of_birth,gender\n"
            "Good,Client,2018-01-01,male\n"
            ",Missing Name,,\n"
            "Another,Good,2019-05-05,female\n"
        )
        resp = admin_client.post(
            self.url,
            {'file': self._make_csv(csv_content)},
            format='multipart',
        )
        assert resp.status_code == status.HTTP_200_OK
        # Should import good rows, report bad ones
        assert resp.data['imported'] >= 1
        assert 'error_details' in resp.data

    def test_import_no_file(self, admin_client):
        """POST without file → 400 with readable message."""
        resp = admin_client.post(self.url, {}, format='multipart')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert 'message' in resp.data

    def test_import_empty_csv(self, admin_client):
        """Empty CSV → 200 with 0 imported."""
        csv_content = "first_name,last_name,date_of_birth,gender\n"
        resp = admin_client.post(
            self.url,
            {'file': self._make_csv(csv_content)},
            format='multipart',
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['imported'] == 0

    def test_import_wrong_columns(self, admin_client):
        """CSV with wrong column names → should handle gracefully."""
        csv_content = (
            "name,birthday,sex\n"
            "John Doe,2018-01-01,M\n"
        )
        resp = admin_client.post(
            self.url,
            {'file': self._make_csv(csv_content)},
            format='multipart',
        )
        # Should not crash — either imports 0 or returns error
        assert resp.status_code in (
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_import_clinician_blocked(self, clinician_client):
        """Non-admin importing → depends on permissions. Should not crash."""
        csv_content = (
            "first_name,last_name,date_of_birth,gender\n"
            "Test,User,2020-01-01,male\n"
        )
        resp = clinician_client.post(
            self.url,
            {'file': self._make_csv(csv_content)},
            format='multipart',
        )
        # Clinician has IsFrontDesk permission so this may work
        assert resp.status_code in (
            status.HTTP_200_OK,
            status.HTTP_403_FORBIDDEN,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. AUDIT TRAIL VERIFICATION (HIPAA)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestAuditTrail:
    """HIPAA requires every write action to be logged in the audit trail."""

    def test_client_creation_logged(self, admin_client):
        """Creating a client should create an audit log entry."""
        from apps.audit.models import AuditLog
        initial_count = AuditLog.objects.count()

        admin_client.post('/api/v1/clients/', {
            'first_name': 'Audited',
            'last_name': 'Client',
            'date_of_birth': '2020-01-01',
            'gender': 'male',
        })

        # There should be at least one new audit entry
        new_count = AuditLog.objects.count()
        assert new_count > initial_count, \
            "HIPAA VIOLATION: Client creation was NOT recorded in audit log!"

    def test_client_update_logged(self, admin_client, sample_client):
        """Updating a client should create an audit log entry."""
        from apps.audit.models import AuditLog
        initial_count = AuditLog.objects.count()

        admin_client.patch(f'/api/v1/clients/{sample_client.id}/', {
            'phone': '555-9999',
        })

        new_count = AuditLog.objects.count()
        assert new_count > initial_count, \
            "HIPAA VIOLATION: Client update was NOT recorded in audit log!"

    def test_client_delete_logged(self, admin_client, sample_client):
        """Deleting a client should create an audit log entry."""
        from apps.audit.models import AuditLog
        initial_count = AuditLog.objects.count()

        admin_client.delete(f'/api/v1/clients/{sample_client.id}/')

        new_count = AuditLog.objects.count()
        assert new_count > initial_count, \
            "HIPAA VIOLATION: Client deletion was NOT recorded in audit log!"

    def test_audit_log_has_user(self, admin_client, admin_user):
        """Audit log should record WHICH user performed the action."""
        from apps.audit.models import AuditLog

        admin_client.post('/api/v1/clients/', {
            'first_name': 'Who',
            'last_name': 'Did This',
            'date_of_birth': '2020-01-01',
            'gender': 'female',
        })

        latest_log = AuditLog.objects.order_by('-timestamp').first()
        assert latest_log is not None
        assert latest_log.user_id == admin_user.id, \
            "HIPAA VIOLATION: Audit log does not record the user!"

    def test_audit_log_masks_sensitive_data(self, api_client, admin_user):
        """Passwords should be masked in audit logs."""
        from apps.audit.models import AuditLog

        # Login generates audit entries
        api_client.post('/api/v1/auth/login/', {
            'email': 'admin@testclinic.com',
            'password': 'testpass123!',
        })

        # Check that password is NOT stored in plaintext
        sensitive_logs = AuditLog.objects.filter(
            changes__has_key='password'
        )
        for log in sensitive_logs:
            if log.changes and 'password' in log.changes:
                assert log.changes['password'] == '***REDACTED***', \
                    "HIPAA VIOLATION: Password stored in plaintext in audit log!"

    def test_audit_logs_accessible_to_admin(self, admin_client):
        """Admin can view audit logs."""
        resp = admin_client.get('/api/v1/audit-logs/')
        assert resp.status_code == status.HTTP_200_OK

    def test_audit_logs_blocked_for_clinician(self, clinician_client):
        """Clinician should NOT be able to view audit logs."""
        resp = clinician_client.get('/api/v1/audit-logs/')
        assert resp.status_code == status.HTTP_403_FORBIDDEN


# ═══════════════════════════════════════════════════════════════════════════════
# 4. ROLE ESCALATION PREVENTION
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestRoleEscalation:
    """Prevent users from escalating their own privileges."""

    def test_clinician_cannot_create_admin_user(self, clinician_client, org):
        """Clinician cannot create a new admin user."""
        resp = clinician_client.post('/api/v1/auth/users/', {
            'email': 'hacker@exploit.com',
            'first_name': 'Hacker',
            'last_name': 'Admin',
            'role': 'admin',
            'password': 'hacked123!',
            'organization_id': str(org.id),
        })
        assert resp.status_code == status.HTTP_403_FORBIDDEN, \
            f"SECURITY BUG: Clinician created an admin! Status: {resp.status_code}"

    def test_clinician_cannot_change_own_role(self, clinician_client, clinician_user):
        """Clinician cannot promote themselves to admin."""
        resp = clinician_client.patch(f'/api/v1/auth/users/{clinician_user.id}/', {
            'role': 'admin',
        })
        # Should either be 403 or the role should not change
        if resp.status_code == status.HTTP_200_OK:
            clinician_user.refresh_from_db()
            assert clinician_user.role == 'clinician', \
                "SECURITY BUG: Clinician promoted themselves to admin!"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. MULTI-ORG COMPLETE ISOLATION
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestMultiOrgIsolation:
    """Verify that two orgs NEVER see each other's data across all endpoints."""

    def test_full_isolation_journey(
        self, admin_client, other_admin_client, sample_client
    ):
        """Two orgs performing operations — data never leaks."""
        # Org 1 admin creates an appointment
        admin_client.post('/api/v1/clients/', {
            'first_name': 'Org1',
            'last_name': 'Only',
            'date_of_birth': '2020-01-01',
            'gender': 'female',
        })

        # Org 2 admin lists clients — should NOT see Org 1's clients
        resp = other_admin_client.get('/api/v1/clients/')
        assert resp.status_code == status.HTTP_200_OK
        for client in resp.data.get('results', resp.data):
            assert client.get('first_name') != 'Org1', \
                f"DATA LEAK: Org 2 can see Org 1's client: {client}"

    def test_other_org_cant_read_client_detail(
        self, other_admin_client, sample_client
    ):
        """Org 2 admin cannot GET a client from Org 1."""
        resp = other_admin_client.get(f'/api/v1/clients/{sample_client.id}/')
        assert resp.status_code == status.HTTP_404_NOT_FOUND, \
            f"DATA LEAK: Org 2 can read Org 1's client detail! Status: {resp.status_code}"

    def test_other_org_cant_update_client(
        self, other_admin_client, sample_client
    ):
        """Org 2 admin cannot PATCH a client from Org 1."""
        resp = other_admin_client.patch(f'/api/v1/clients/{sample_client.id}/', {
            'phone': '555-HACKED',
        })
        assert resp.status_code in (
            status.HTTP_404_NOT_FOUND,
            status.HTTP_403_FORBIDDEN,
        ), f"DATA LEAK: Org 2 modified Org 1's client! Status: {resp.status_code}"

    def test_other_org_cant_delete_client(
        self, other_admin_client, sample_client
    ):
        """Org 2 admin cannot DELETE a client from Org 1."""
        resp = other_admin_client.delete(f'/api/v1/clients/{sample_client.id}/')
        assert resp.status_code in (
            status.HTTP_404_NOT_FOUND,
            status.HTTP_403_FORBIDDEN,
        ), f"DATA LEAK: Org 2 deleted Org 1's client! Status: {resp.status_code}"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. USER PROFILE & PASSWORD SECURITY
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestUserProfile:
    """User profile and credential management."""

    def test_get_my_profile(self, admin_client, admin_user):
        """GET /auth/me/ returns current user's profile."""
        resp = admin_client.get('/api/v1/auth/me/')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['email'] == admin_user.email

    def test_password_not_in_profile(self, admin_client):
        """Password should NEVER appear in profile response."""
        resp = admin_client.get('/api/v1/auth/me/')
        assert resp.status_code == status.HTTP_200_OK
        assert 'password' not in resp.data
        assert 'password_hash' not in resp.data

    def test_weak_password_rejected(self, admin_client, org):
        """Creating user with weak password → 400."""
        resp = admin_client.post('/api/v1/auth/users/', {
            'email': 'weak@test.com',
            'first_name': 'Weak',
            'last_name': 'Pass',
            'role': 'clinician',
            'password': '123',
            'organization_id': str(org.id),
        })
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_change_password_works(self, admin_client, admin_user):
        """Change password with correct current password → 200."""
        resp = admin_client.put('/api/v1/auth/password/', {
            'current_password': 'testpass123!',
            'new_password': 'newSecurePass456!',
            'confirm_password': 'newSecurePass456!',
        })
        assert resp.status_code == status.HTTP_200_OK
        # Verify the new password works
        admin_user.refresh_from_db()
        assert admin_user.check_password('newSecurePass456!')
