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

    # ─── B13: admin can update email + phone ───────────────────────────────

    def test_admin_can_update_user_email(self, admin_client, clinician_user):
        """Dr. Joe's bug: 'Can't Update email if a change needs to be made'.
        UserUpdateSerializer was missing 'email' and 'phone' from its fields."""
        resp = admin_client.patch(
            f'{self.url}{clinician_user.id}/',
            {'email': 'changed@example.com'},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['email'] == 'changed@example.com'

        clinician_user.refresh_from_db()
        assert clinician_user.email == 'changed@example.com'

    def test_admin_can_update_user_phone(self, admin_client, clinician_user):
        resp = admin_client.patch(
            f'{self.url}{clinician_user.id}/',
            {'phone': '555-9999'},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        clinician_user.refresh_from_db()
        assert clinician_user.phone == '555-9999'

    def test_email_collision_rejected_with_clear_error(
        self, admin_client, admin_user, clinician_user,
    ):
        """Setting one user's email to another user's email → 400 with a clear
        message (not a 500/IntegrityError)."""
        resp = admin_client.patch(
            f'{self.url}{clinician_user.id}/',
            {'email': admin_user.email},
            format='json',
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        # Error wrapper format: {'error': True, 'errors': {'email': [...]}, 'message': ...}
        assert 'email' in resp.data.get('errors', {})

    def test_no_op_email_save_succeeds(self, admin_client, clinician_user):
        """Saving the user with their existing email shouldn't trip the
        uniqueness check (we exclude self when validating)."""
        resp = admin_client.patch(
            f'{self.url}{clinician_user.id}/',
            {'email': clinician_user.email, 'first_name': 'Renamed'},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['first_name'] == 'Renamed'

    def test_email_normalized_lowercase(self, admin_client, clinician_user):
        resp = admin_client.patch(
            f'{self.url}{clinician_user.id}/',
            {'email': 'MIXED.Case@Example.com'},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['email'] == 'mixed.case@example.com'

    # ─── B12: admin sends password reset link ──────────────────────────────

    def test_admin_can_send_reset_link(self, admin_client, clinician_user, mocker):
        """POST /auth/users/{id}/send-reset-link/ → 200 + email sent."""
        mock_send = mocker.patch(
            'apps.core.email.EmailService.send_password_reset_email',
            return_value=None,
        )
        resp = admin_client.post(
            f'{self.url}{clinician_user.id}/send-reset-link/',
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['email'] == clinician_user.email
        assert mock_send.call_count == 1
        # Reset URL passed to the email contains uid + token query params.
        called_user, reset_url = mock_send.call_args.args[0], mock_send.call_args.args[1]
        assert called_user.pk == clinician_user.pk
        assert 'uid=' in reset_url and 'token=' in reset_url

    def test_send_reset_link_inactive_user_rejected(
        self, admin_client, clinician_user, mocker,
    ):
        """Inactive users can't be reset — admin must reactivate first."""
        mock_send = mocker.patch(
            'apps.core.email.EmailService.send_password_reset_email',
        )
        clinician_user.is_active = False
        clinician_user.save()
        resp = admin_client.post(
            f'{self.url}{clinician_user.id}/send-reset-link/',
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert mock_send.call_count == 0

    def test_send_reset_link_requires_admin(
        self, clinician_client, clinician_user, mocker,
    ):
        """Non-admins can't trigger resets for other users."""
        mock_send = mocker.patch(
            'apps.core.email.EmailService.send_password_reset_email',
        )
        resp = clinician_client.post(
            f'{self.url}{clinician_user.id}/send-reset-link/',
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert mock_send.call_count == 0

    def test_send_reset_link_email_failure_returns_502(
        self, admin_client, clinician_user, mocker,
    ):
        """If the email service throws, return a clean 502 (not 500)."""
        mocker.patch(
            'apps.core.email.EmailService.send_password_reset_email',
            side_effect=Exception('SMTP down'),
        )
        resp = admin_client.post(
            f'{self.url}{clinician_user.id}/send-reset-link/',
        )
        assert resp.status_code == status.HTTP_502_BAD_GATEWAY

    # ─── E27: admin can edit credentials and licenses ──────────────────────

    def test_admin_can_update_credentials(self, admin_client, clinician_user):
        """Credentials (PsyD, MD, etc.) — patient-facing on superbills."""
        resp = admin_client.patch(
            f'{self.url}{clinician_user.id}/',
            {'credentials': 'PsyD, ABPP'},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        clinician_user.refresh_from_db()
        assert clinician_user.credentials == 'PsyD, ABPP'

    def test_admin_can_update_licenses(self, admin_client, clinician_user):
        """State license array — required for billing in some payer contracts."""
        resp = admin_client.patch(
            f'{self.url}{clinician_user.id}/',
            {'licenses': ['NJ-12345', 'NY-67890']},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        clinician_user.refresh_from_db()
        assert clinician_user.licenses == ['NJ-12345', 'NY-67890']

    # ─── E28: admin archive (soft-delete) staff ────────────────────────────

    def test_admin_can_archive_staff_via_delete(self, admin_client, clinician_user):
        """DELETE soft-deletes (sets is_active=False) — HIPAA-safe archive
        rather than hard-delete so audit trail integrity is preserved."""
        resp = admin_client.delete(f'{self.url}{clinician_user.id}/')
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        clinician_user.refresh_from_db()
        assert clinician_user.is_active is False
        # Row still exists — not hard-deleted.
        from apps.accounts.models import User as UserModel
        assert UserModel.objects.filter(pk=clinician_user.pk).exists()

    def test_admin_can_reactivate_archived_staff(self, admin_client, clinician_user):
        """Reactivation flips is_active back to True."""
        clinician_user.is_active = False
        clinician_user.save()
        resp = admin_client.patch(
            f'{self.url}{clinician_user.id}/',
            {'is_active': True},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        clinician_user.refresh_from_db()
        assert clinician_user.is_active is True

    # ─── E5: provider NPI on User ──────────────────────────────────────────

    def test_admin_can_set_user_npi(self, admin_client, clinician_user):
        """Each provider gets their own (Type 1) individual NPI for billing
        as the rendering provider."""
        resp = admin_client.patch(
            f'{self.url}{clinician_user.id}/',
            {'npi': '1659841096'},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        clinician_user.refresh_from_db()
        assert clinician_user.npi == '1659841096'

    def test_invalid_npi_rejected_with_clean_error(self, admin_client, clinician_user):
        """Bad Luhn → 400, not silent acceptance."""
        resp = admin_client.patch(
            f'{self.url}{clinician_user.id}/',
            {'npi': '1659841090'},  # check digit busted
            format='json',
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert 'npi' in resp.data.get('errors', {})
        clinician_user.refresh_from_db()
        assert clinician_user.npi == ''  # not saved

    def test_empty_npi_allowed(self, admin_client, clinician_user):
        """Non-clinical roles (front desk, biller) shouldn't be forced to have
        an NPI. Empty string is a valid 'not applicable' state."""
        clinician_user.npi = '1659841096'
        clinician_user.save()
        resp = admin_client.patch(
            f'{self.url}{clinician_user.id}/',
            {'npi': ''},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        clinician_user.refresh_from_db()
        assert clinician_user.npi == ''

    def test_user_response_includes_npi_field(self, admin_client, clinician_user):
        """Frontend needs to read .npi to pre-fill intake author NPI (E4)."""
        clinician_user.npi = '1659841096'
        clinician_user.save()
        resp = admin_client.get(f'{self.url}{clinician_user.id}/')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['npi'] == '1659841096'


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
