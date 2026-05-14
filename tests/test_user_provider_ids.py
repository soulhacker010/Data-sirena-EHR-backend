"""
End-to-end tests for the per-user provider-ID fields surfaced in User Management
(Dr. Joe 2026-05-12 feedback):

    - Individual NPI (10 digits, CMS Luhn-validated)
    - EIN (9 digits, optional, no dashes stored)

Covers: create flow, update flow, validation rejections, profile read-back,
plus role coverage across all five roles (admin, supervisor, clinician,
biller, front_desk) — front_desk and biller don't have NPI/EIN since they
don't render or bill claims under their own identity, but they must still
be able to view their basic profile without errors.
"""
import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User


@pytest.fixture
def all_role_users(org):
    """One user per role, all in the same org."""
    users = {}
    for role in ('admin', 'supervisor', 'clinician', 'biller', 'front_desk'):
        users[role] = User.objects.create_user(
            email=f'{role}@testclinic.com',
            password='testpass123!',
            first_name=role.capitalize().replace('_', ' '),
            last_name='User',
            role=role,
            organization=org,
        )
    return users


def _client_for(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


@pytest.mark.django_db
class TestUserCreateWithProviderIds:
    def test_create_user_with_npi_and_ein(self, admin_client, org):
        payload = {
            'email': 'newprovider@testclinic.com',
            'first_name': 'New',
            'last_name': 'Provider',
            'role': 'clinician',
            'password': 'testpass123!',
            'organization_id': str(org.id),
            # Valid CMS Luhn NPI (BSBH's actual one)
            'npi': '1659841096',
            # 9-digit EIN, sent with dashes to verify they get stripped
            'ein': '83-2541331',
        }
        r = admin_client.post('/api/v1/auth/users/', payload, format='json')
        assert r.status_code == 201, r.data
        assert r.data['npi'] == '1659841096'
        assert r.data['ein'] == '832541331'  # dashes stripped

    def test_create_user_without_provider_ids(self, admin_client, org):
        """Front desk / biller may have no NPI or EIN — both fields are optional."""
        r = admin_client.post('/api/v1/auth/users/', {
            'email': 'frontdesk@testclinic.com',
            'first_name': 'Front',
            'last_name': 'Desk',
            'role': 'front_desk',
            'password': 'testpass123!',
            'organization_id': str(org.id),
        }, format='json')
        assert r.status_code == 201, r.data
        assert r.data['npi'] == ''
        assert r.data['ein'] == ''

    def test_create_with_invalid_npi_rejected(self, admin_client, org):
        r = admin_client.post('/api/v1/auth/users/', {
            'email': 'bad@testclinic.com',
            'first_name': 'Bad',
            'last_name': 'NPI',
            'role': 'clinician',
            'password': 'testpass123!',
            'organization_id': str(org.id),
            'npi': '1234567890',  # fails CMS Luhn
        }, format='json')
        assert r.status_code == 400
        # Project uses a custom exception handler that wraps errors in
        # {'error': True, 'errors': {...}, 'message': ...}
        errors = r.data.get('errors', r.data)
        assert 'npi' in errors

    def test_create_with_invalid_ein_rejected(self, admin_client, org):
        r = admin_client.post('/api/v1/auth/users/', {
            'email': 'bad@testclinic.com',
            'first_name': 'Bad',
            'last_name': 'EIN',
            'role': 'clinician',
            'password': 'testpass123!',
            'organization_id': str(org.id),
            'ein': '12345',  # too short
        }, format='json')
        assert r.status_code == 400
        errors = r.data.get('errors', r.data)
        assert 'ein' in errors


@pytest.mark.django_db
class TestUserUpdateProviderIds:
    def test_admin_can_update_npi_and_ein(self, admin_client, clinician_user):
        r = admin_client.put(
            f'/api/v1/auth/users/{clinician_user.id}/',
            {
                'first_name': clinician_user.first_name,
                'last_name': clinician_user.last_name,
                'email': clinician_user.email,
                'role': clinician_user.role,
                'npi': '1659841096',
                'ein': '832541331',
            },
            format='json',
        )
        assert r.status_code == 200, r.data

        clinician_user.refresh_from_db()
        assert clinician_user.npi == '1659841096'
        assert clinician_user.ein == '832541331'

    def test_update_strips_ein_punctuation(self, admin_client, clinician_user):
        r = admin_client.put(
            f'/api/v1/auth/users/{clinician_user.id}/',
            {
                'first_name': clinician_user.first_name,
                'last_name': clinician_user.last_name,
                'email': clinician_user.email,
                'role': clinician_user.role,
                'ein': '83-2541331',
            },
            format='json',
        )
        assert r.status_code == 200, r.data
        clinician_user.refresh_from_db()
        assert clinician_user.ein == '832541331'


@pytest.mark.django_db
class TestProfileExposesProviderIds:
    """The clinician's own Settings page calls /auth/me/ — it must surface
    the admin-managed Provider IDs read-only."""

    def test_me_endpoint_returns_npi_and_ein(self, clinician_client, clinician_user):
        clinician_user.npi = '1659841096'
        clinician_user.ein = '832541331'
        clinician_user.credentials = 'PsyD'
        clinician_user.licenses = ['NJ-12345']
        clinician_user.save()

        r = clinician_client.get('/api/v1/auth/me/')
        assert r.status_code == 200
        assert r.data['npi'] == '1659841096'
        assert r.data['ein'] == '832541331'
        assert r.data['credentials'] == 'PsyD'
        assert r.data['licenses'] == ['NJ-12345']

    def test_clinician_cannot_self_update_provider_ids(self, clinician_client, clinician_user):
        """ProfileUpdateSerializer only allows first_name/last_name/phone — admin-only
        fields must NOT be writable from the self-update endpoint."""
        clinician_user.npi = ''
        clinician_user.save()

        r = clinician_client.put(
            '/api/v1/auth/me/',
            {
                'first_name': clinician_user.first_name,
                'last_name': clinician_user.last_name,
                'npi': '1659841096',  # should be silently ignored
                'ein': '832541331',
            },
            format='json',
        )
        assert r.status_code == 200, r.data
        clinician_user.refresh_from_db()
        # Confirm the admin-managed fields did NOT get changed via self-update
        assert clinician_user.npi == ''
        assert clinician_user.ein == ''


@pytest.mark.django_db
class TestAllRolesProfileFlow:
    """Smoke-tests the Settings → Profile flow for every role.

    For each role, verify that:
      1. The user can hit /auth/me/ and get a 200 with their profile data
      2. The user can self-update first_name/last_name/phone
      3. The user cannot self-update admin-managed billing fields (npi, ein)

    Front desk and biller are deliberately included even though they don't
    have an NPI/EIN — the profile endpoint must still serve them cleanly.
    """

    @pytest.mark.parametrize('role', ['admin', 'supervisor', 'clinician', 'biller', 'front_desk'])
    def test_role_can_view_own_profile(self, all_role_users, role):
        user = all_role_users[role]
        client = _client_for(user)

        r = client.get('/api/v1/auth/me/')
        assert r.status_code == 200, (role, r.data)
        assert r.data['email'] == user.email
        assert r.data['role'] == role
        # All roles get NPI/EIN keys back (empty for non-clinical roles)
        assert 'npi' in r.data
        assert 'ein' in r.data

    @pytest.mark.parametrize('role', ['admin', 'supervisor', 'clinician', 'biller', 'front_desk'])
    def test_role_can_self_update_basics(self, all_role_users, role):
        user = all_role_users[role]
        client = _client_for(user)

        r = client.put('/api/v1/auth/me/', {
            'first_name': 'Renamed',
            'last_name': user.last_name,
            'phone': '555-0199',
        }, format='json')
        assert r.status_code == 200, (role, r.data)
        user.refresh_from_db()
        assert user.first_name == 'Renamed'
        assert user.phone == '555-0199'

    @pytest.mark.parametrize('role', ['admin', 'supervisor', 'clinician', 'biller', 'front_desk'])
    def test_role_cannot_self_assign_npi(self, all_role_users, role):
        """No role should be able to grant themselves an NPI through the
        self-update endpoint — even admins go through User Management for
        billing-critical fields so the audit log shows an admin action."""
        user = all_role_users[role]
        user.npi = ''
        user.save()
        client = _client_for(user)

        r = client.put('/api/v1/auth/me/', {
            'first_name': user.first_name,
            'last_name': user.last_name,
            'npi': '1659841096',  # should be ignored by ProfileUpdateSerializer
        }, format='json')
        assert r.status_code == 200, (role, r.data)
        user.refresh_from_db()
        assert user.npi == '', f'{role} was able to self-assign NPI'
