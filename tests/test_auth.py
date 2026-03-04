"""
Auth endpoint tests — login, me, refresh, logout, password change.

These tests verify the complete authentication flow that the frontend relies on.
"""
import pytest
from rest_framework import status


@pytest.mark.django_db
class TestLogin:
    url = '/api/v1/auth/login/'

    def test_login_success(self, api_client, admin_user):
        """Valid credentials → 200 + access/refresh tokens + user data."""
        resp = api_client.post(self.url, {
            'email': 'admin@testclinic.com',
            'password': 'testpass123!',
        })
        assert resp.status_code == status.HTTP_200_OK
        assert 'access' in resp.data
        assert 'refresh' in resp.data
        assert resp.data['user']['email'] == 'admin@testclinic.com'
        assert resp.data['user']['role'] == 'admin'

    def test_login_wrong_password(self, api_client, admin_user):
        """Wrong password → 400."""
        resp = api_client.post(self.url, {
            'email': 'admin@testclinic.com',
            'password': 'wrongpassword',
        })
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_login_nonexistent_user(self, api_client):
        """Nonexistent user → 400."""
        resp = api_client.post(self.url, {
            'email': 'nobody@test.com',
            'password': 'whatever',
        })
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestMe:
    url = '/api/v1/auth/me/'

    def test_get_me_authenticated(self, admin_client, admin_user):
        """Authenticated user → 200 + user profile."""
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['email'] == admin_user.email
        assert resp.data['role'] == 'admin'
        assert 'organization_id' in resp.data
        assert 'organization_name' in resp.data

    def test_get_me_unauthenticated(self, api_client):
        """No auth → 401."""
        resp = api_client.get(self.url)
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestTokenRefresh:
    login_url = '/api/v1/auth/login/'
    refresh_url = '/api/v1/auth/token/refresh/'

    def test_refresh_token(self, api_client, admin_user):
        """Valid refresh token → new access token."""
        login_resp = api_client.post(self.login_url, {
            'email': 'admin@testclinic.com',
            'password': 'testpass123!',
        })
        refresh_token = login_resp.data['refresh']

        resp = api_client.post(self.refresh_url, {
            'refresh': refresh_token,
        })
        assert resp.status_code == status.HTTP_200_OK
        assert 'access' in resp.data

    def test_refresh_invalid_token(self, api_client):
        """Invalid refresh token → 401."""
        resp = api_client.post(self.refresh_url, {
            'refresh': 'invalid-token',
        })
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestChangePassword:
    url = '/api/v1/auth/password/'

    def test_change_password_success(self, admin_client, admin_user):
        """Valid password change → 200."""
        resp = admin_client.put(self.url, {
            'current_password': 'testpass123!',
            'new_password': 'newpass456!',
            'confirm_password': 'newpass456!',
        })
        assert resp.status_code == status.HTTP_200_OK

        # Verify new password works
        admin_user.refresh_from_db()
        assert admin_user.check_password('newpass456!')

    def test_change_password_wrong_current(self, admin_client):
        """Wrong current password → 400."""
        resp = admin_client.put(self.url, {
            'current_password': 'wrongpassword',
            'new_password': 'newpass456!',
            'confirm_password': 'newpass456!',
        })
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_change_password_mismatch(self, admin_client):
        """New password != confirm → 400."""
        resp = admin_client.put(self.url, {
            'current_password': 'testpass123!',
            'new_password': 'newpass456!',
            'confirm_password': 'different789!',
        })
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
