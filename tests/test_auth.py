"""
Auth endpoint tests — login, me, refresh, logout, password change, password reset.

These tests verify the complete authentication flow that the frontend relies on.
"""
import pytest
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
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


@pytest.mark.django_db
class TestPasswordReset:
    request_url = '/api/v1/auth/password-reset/'
    confirm_url = '/api/v1/auth/password-reset/confirm/'

    @pytest.fixture(autouse=True)
    def clear_throttle_cache(self):
        from django.core.cache import cache
        cache.clear()
        yield
        cache.clear()

    def _make_reset_link(self, user):
        uid = urlsafe_base64_encode(force_bytes(str(user.pk)))
        token = default_token_generator.make_token(user)
        return uid, token

    def test_request_reset_existing_email_returns_200(self, api_client, admin_user):
        """Valid email → 200 with generic message (no email enumeration)."""
        resp = api_client.post(self.request_url, {'email': admin_user.email})
        assert resp.status_code == status.HTTP_200_OK
        assert 'message' in resp.data

    def test_request_reset_unknown_email_still_returns_200(self, api_client):
        """Unknown email → still 200, prevents email enumeration."""
        resp = api_client.post(self.request_url, {'email': 'nobody@nowhere.com'})
        assert resp.status_code == status.HTTP_200_OK

    def test_request_reset_empty_email_returns_200(self, api_client):
        """Empty email → 200 generic response, no crash."""
        resp = api_client.post(self.request_url, {'email': ''})
        assert resp.status_code == status.HTTP_200_OK

    def test_confirm_reset_valid_token_changes_password(self, api_client, admin_user):
        """Valid uid + token + new password → 200, password is updated."""
        uid, token = self._make_reset_link(admin_user)
        resp = api_client.post(self.confirm_url, {
            'uid': uid,
            'token': token,
            'new_password': 'NewSecurePass99!',
        })
        assert resp.status_code == status.HTTP_200_OK
        admin_user.refresh_from_db()
        assert admin_user.check_password('NewSecurePass99!')

    def test_confirm_reset_invalid_token_returns_400(self, api_client, admin_user):
        """Invalid token → 400."""
        uid = urlsafe_base64_encode(force_bytes(str(admin_user.pk)))
        resp = api_client.post(self.confirm_url, {
            'uid': uid,
            'token': 'completely-wrong-token',
            'new_password': 'NewSecurePass99!',
        })
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_confirm_reset_invalid_uid_returns_400(self, api_client):
        """Garbage uid → 400, no crash."""
        resp = api_client.post(self.confirm_url, {
            'uid': 'notavaliduid',
            'token': 'sometoken',
            'new_password': 'NewSecurePass99!',
        })
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_confirm_reset_short_password_returns_400(self, api_client, admin_user):
        """Password < 8 chars → 400."""
        uid, token = self._make_reset_link(admin_user)
        resp = api_client.post(self.confirm_url, {
            'uid': uid,
            'token': token,
            'new_password': 'short',
        })
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_confirm_reset_missing_fields_returns_400(self, api_client):
        """Missing fields → 400."""
        resp = api_client.post(self.confirm_url, {})
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_token_invalidated_after_password_change(self, api_client, admin_user):
        """Token cannot be reused after password is reset."""
        uid, token = self._make_reset_link(admin_user)
        # First reset succeeds
        api_client.post(self.confirm_url, {
            'uid': uid, 'token': token, 'new_password': 'FirstNewPass99!',
        })
        # Second attempt with same token → 400
        resp = api_client.post(self.confirm_url, {
            'uid': uid, 'token': token, 'new_password': 'SecondAttempt99!',
        })
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
