"""
Tests for the session-idle-timeout enforcement.

Three layers:
    1. is_session_idle() — pure helper, no request needed
    2. LastSeenMiddleware — debounced last_seen_at writes
    3. /token/refresh/ — rejects refresh when user has been idle > timeout
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.middleware import LastSeenMiddleware, is_session_idle


# ─── Pure helper ────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestIsSessionIdle:
    def test_no_last_seen_is_not_idle(self, admin_user):
        admin_user.last_seen_at = None
        assert is_session_idle(admin_user, timeout_minutes=30) is False

    def test_recent_activity_is_not_idle(self, admin_user):
        admin_user.last_seen_at = timezone.now() - timedelta(minutes=5)
        assert is_session_idle(admin_user, timeout_minutes=30) is False

    def test_idle_past_timeout(self, admin_user):
        admin_user.last_seen_at = timezone.now() - timedelta(minutes=45)
        assert is_session_idle(admin_user, timeout_minutes=30) is True

    def test_at_exact_boundary_is_not_idle(self, admin_user):
        # Strictly greater-than, not greater-than-or-equal — a user who is
        # exactly at the timeout should still be allowed to refresh.
        admin_user.last_seen_at = timezone.now() - timedelta(minutes=30)
        assert is_session_idle(admin_user, timeout_minutes=30) is False


# ─── Middleware debounce ────────────────────────────────────────────────────

@pytest.mark.django_db
class TestLastSeenMiddleware:
    def _make_request(self, path: str, user=None):
        from django.test import RequestFactory
        rf = RequestFactory()
        request = rf.get(path)
        if user is not None:
            request.user = user
        else:
            from django.contrib.auth.models import AnonymousUser
            request.user = AnonymousUser()
        return request

    def test_anonymous_request_skipped(self, admin_user):
        admin_user.last_seen_at = None
        admin_user.save(update_fields=['last_seen_at'])
        mw = LastSeenMiddleware(get_response=lambda r: None)
        mw.process_request(self._make_request('/api/v1/clients/'))
        admin_user.refresh_from_db()
        assert admin_user.last_seen_at is None

    def test_first_request_writes(self, admin_user):
        admin_user.last_seen_at = None
        admin_user.save(update_fields=['last_seen_at'])
        mw = LastSeenMiddleware(get_response=lambda r: None)
        mw.process_request(self._make_request('/api/v1/clients/', user=admin_user))
        admin_user.refresh_from_db()
        assert admin_user.last_seen_at is not None

    def test_debounced_within_window(self, admin_user):
        # Set last_seen 10 seconds ago — within debounce
        ten_seconds_ago = timezone.now() - timedelta(seconds=10)
        admin_user.last_seen_at = ten_seconds_ago
        admin_user.save(update_fields=['last_seen_at'])

        mw = LastSeenMiddleware(get_response=lambda r: None)
        mw.process_request(self._make_request('/api/v1/clients/', user=admin_user))

        admin_user.refresh_from_db()
        # Should not have moved (debounce window is 60s)
        assert abs((admin_user.last_seen_at - ten_seconds_ago).total_seconds()) < 1

    def test_writes_after_debounce_window(self, admin_user):
        # 90s ago — past debounce
        ninety_seconds_ago = timezone.now() - timedelta(seconds=90)
        admin_user.last_seen_at = ninety_seconds_ago
        admin_user.save(update_fields=['last_seen_at'])

        mw = LastSeenMiddleware(get_response=lambda r: None)
        mw.process_request(self._make_request('/api/v1/clients/', user=admin_user))

        admin_user.refresh_from_db()
        # Should have updated to roughly now
        assert (timezone.now() - admin_user.last_seen_at).total_seconds() < 5

    def test_refresh_path_skipped(self, admin_user):
        admin_user.last_seen_at = None
        admin_user.save(update_fields=['last_seen_at'])

        mw = LastSeenMiddleware(get_response=lambda r: None)
        mw.process_request(self._make_request(
            '/api/v1/auth/token/refresh/', user=admin_user,
        ))
        admin_user.refresh_from_db()
        # The refresh endpoint should not count as "activity" — that's circular
        assert admin_user.last_seen_at is None


# ─── End-to-end: /token/refresh/ with idle check ────────────────────────────

@pytest.mark.django_db
class TestRefreshIdleTimeout:
    def test_recent_user_can_refresh(self, admin_user, api_client):
        admin_user.last_seen_at = timezone.now() - timedelta(minutes=5)
        admin_user.save(update_fields=['last_seen_at'])

        refresh = RefreshToken.for_user(admin_user)
        r = api_client.post(
            reverse('token-refresh'),
            {'refresh': str(refresh)},
            format='json',
        )
        assert r.status_code == 200, r.data
        assert 'access' in r.data

    def test_idle_user_rejected(self, admin_user, api_client, settings):
        settings.IDLE_TIMEOUT_MINUTES = 30
        admin_user.last_seen_at = timezone.now() - timedelta(minutes=45)
        admin_user.save(update_fields=['last_seen_at'])

        refresh = RefreshToken.for_user(admin_user)
        r = api_client.post(
            reverse('token-refresh'),
            {'refresh': str(refresh)},
            format='json',
        )
        assert r.status_code == 401, r.data
        # Error code surfaces in payload so frontend can route to "logged out
        # for inactivity" message instead of generic auth error
        assert 'session_idle' in str(r.data).lower() or 'inactivity' in str(r.data).lower()

    def test_first_ever_refresh_allowed(self, admin_user, api_client):
        # User who has logged in but never had middleware run (e.g. fresh
        # token from login flow with no subsequent requests). last_seen_at
        # is None — must NOT be treated as idle.
        admin_user.last_seen_at = None
        admin_user.save(update_fields=['last_seen_at'])

        refresh = RefreshToken.for_user(admin_user)
        r = api_client.post(
            reverse('token-refresh'),
            {'refresh': str(refresh)},
            format='json',
        )
        assert r.status_code == 200, r.data

    def test_idle_timeout_writes_audit_log(self, admin_user, api_client, settings):
        from apps.audit.models import AuditLog

        settings.IDLE_TIMEOUT_MINUTES = 30
        admin_user.last_seen_at = timezone.now() - timedelta(hours=2)
        admin_user.save(update_fields=['last_seen_at'])

        refresh = RefreshToken.for_user(admin_user)
        api_client.post(
            reverse('token-refresh'),
            {'refresh': str(refresh)},
            format='json',
        )

        log = AuditLog.objects.filter(action='session_idle_timeout').order_by('-timestamp').first()
        assert log is not None
        # No PHI in changes — only user_id
        assert set(log.changes.keys()) <= {'user_id'}
