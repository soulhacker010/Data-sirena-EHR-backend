"""
Changing or resetting a password must end sessions that are already open.

Password reset is the remediation path when an account is believed
compromised. If it leaves existing refresh tokens usable, the attacker keeps
access for the remainder of the refresh lifetime (7 days here) and the reset
accomplishes nothing against the threat it exists for.

Scope note: JWT access tokens are stateless and cannot be revoked — an access
token issued before the change stays valid until it expires (15 minutes). What
these tests pin down is that no *new* access token can be minted afterwards,
which is the part that is actually enforceable.
"""
import pytest
from django.core.cache import cache
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken


REFRESH_URL = '/api/v1/auth/token/refresh/'
CHANGE_PASSWORD_URL = '/api/v1/auth/password/'
RESET_CONFIRM_URL = '/api/v1/auth/password-reset/confirm/'


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    """
    The reset-confirm and refresh endpoints share throttle scopes with login,
    and the throttle cache is not rolled back with the test transaction. Same
    convention as tests/test_throttling.py — without it these tests eat the
    login budget and starve whatever runs next in the same session.
    """
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
class TestPasswordChangeRevokesSessions:

    def test_refresh_token_rejected_after_password_change(
        self, admin_client, admin_user, api_client,
    ):
        # Two independent sessions open before the change. Two are needed
        # because ROTATE_REFRESH_TOKENS + BLACKLIST_AFTER_ROTATION means simply
        # *using* a refresh token blacklists it — so the sanity check has to
        # burn a different token than the one under test, or this test passes
        # for the wrong reason.
        probe_refresh = str(RefreshToken.for_user(admin_user))
        old_refresh = str(RefreshToken.for_user(admin_user))

        # Sanity: refreshing works at all right now.
        pre = api_client.post(REFRESH_URL, {'refresh': probe_refresh}, format='json')
        assert pre.status_code == status.HTTP_200_OK

        resp = admin_client.put(CHANGE_PASSWORD_URL, {
            'current_password': 'testpass123!',
            'new_password': 'BrandNewPass456!',
            'confirm_password': 'BrandNewPass456!',
        }, format='json')
        assert resp.status_code == status.HTTP_200_OK

        post = api_client.post(REFRESH_URL, {'refresh': old_refresh}, format='json')
        assert post.status_code != status.HTTP_200_OK, (
            'refresh token still worked after the password was changed'
        )


@pytest.mark.django_db
class TestPasswordResetRevokesSessions:

    def test_refresh_token_rejected_after_password_reset(self, admin_user, api_client):
        # See the note in the change-password test: rotation blacklists a token
        # on use, so the probe and the token under test must be different.
        probe_refresh = str(RefreshToken.for_user(admin_user))
        old_refresh = str(RefreshToken.for_user(admin_user))

        pre = api_client.post(REFRESH_URL, {'refresh': probe_refresh}, format='json')
        assert pre.status_code == status.HTTP_200_OK

        uid = urlsafe_base64_encode(force_bytes(str(admin_user.pk)))
        token = default_token_generator.make_token(admin_user)

        resp = api_client.post(RESET_CONFIRM_URL, {
            'uid': uid,
            'token': token,
            'new_password': 'ResetPass789!',
        }, format='json')
        assert resp.status_code == status.HTTP_200_OK

        post = api_client.post(REFRESH_URL, {'refresh': old_refresh}, format='json')
        assert post.status_code != status.HTTP_200_OK, (
            'refresh token still worked after the password was reset'
        )

    def test_password_reset_writes_audit_row(self, admin_user, api_client):
        uid = urlsafe_base64_encode(force_bytes(str(admin_user.pk)))
        token = default_token_generator.make_token(admin_user)

        resp = api_client.post(RESET_CONFIRM_URL, {
            'uid': uid,
            'token': token,
            'new_password': 'AuditedReset123!',
        }, format='json')
        assert resp.status_code == status.HTTP_200_OK

        from apps.audit.models import AuditLog
        assert AuditLog.objects.filter(action='password_reset').exists(), (
            'password reset must leave an audit trail'
        )
