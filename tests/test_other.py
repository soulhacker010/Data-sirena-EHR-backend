"""
Dashboard, reports, notifications, audit, user management, and organization tests.

These cover the remaining endpoints that the frontend relies on.
"""
import pytest
from rest_framework import status


# ─── Dashboard ──────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDashboard:
    url = '/api/v1/dashboard/stats/'

    def test_dashboard_stats(self, admin_client):
        """Dashboard stats → 200 with summary data."""
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK

    def test_dashboard_unauthenticated(self, api_client):
        """No auth → 401."""
        resp = api_client.get(self.url)
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ─── Reports ────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestReports:
    def test_session_summary_report(self, admin_client):
        """Session summary → 200."""
        resp = admin_client.get('/api/v1/reports/session-summary/')
        assert resp.status_code == status.HTTP_200_OK

    def test_billing_summary_report(self, admin_client):
        """Billing summary → 200."""
        resp = admin_client.get('/api/v1/reports/billing-summary/')
        assert resp.status_code == status.HTTP_200_OK

    def test_authorization_report(self, admin_client):
        """Authorization report → 200."""
        resp = admin_client.get('/api/v1/reports/authorizations/')
        assert resp.status_code == status.HTTP_200_OK

    def test_missing_notes_report(self, admin_client):
        """Missing notes report → 200."""
        resp = admin_client.get('/api/v1/reports/missing-notes/')
        assert resp.status_code == status.HTTP_200_OK


# ─── Notifications ──────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestNotifications:
    url = '/api/v1/notifications/'

    def test_list_notifications(self, admin_client):
        """List notifications → 200."""
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK

    def test_notifications_unauthenticated(self, api_client):
        """No auth → 401."""
        resp = api_client.get(self.url)
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ─── Audit Logs ─────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestAuditLogs:
    url = '/api/v1/audit-logs/'

    def test_list_audit_logs(self, admin_client):
        """List audit logs → 200."""
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK


# ─── User Management ───────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestUserManagement:
    url = '/api/v1/auth/users/'

    def test_list_users_admin(self, admin_client, admin_user):
        """Admin can list users → 200."""
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK

    def test_list_users_non_admin(self, clinician_client):
        """Non-admin can't access user management → 403."""
        resp = clinician_client.get(self.url)
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_create_user(self, admin_client, org):
        """Admin creates new user → 201."""
        resp = admin_client.post(self.url, {
            'email': 'newuser@testclinic.com',
            'password': 'newpass123!',
            'first_name': 'New',
            'last_name': 'User',
            'role': 'clinician',
            'organization_id': str(org.id),
        })
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data['email'] == 'newuser@testclinic.com'


# ─── Organization Settings ─────────────────────────────────────────────────────

@pytest.mark.django_db
class TestOrganizationSettings:
    url = '/api/v1/auth/organization/'

    def test_get_org_settings(self, admin_client, org):
        """Get org settings → 200."""
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['name'] == org.name

    def test_update_org_settings(self, admin_client):
        """Update org name → 200."""
        resp = admin_client.put(self.url, {
            'name': 'Updated Clinic Name',
            'tax_id': '12-3456789',
            'contact_email': 'updated@clinic.com',
            'contact_phone': '555-0200',
            'address': '999 New St',
        })
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['name'] == 'Updated Clinic Name'
