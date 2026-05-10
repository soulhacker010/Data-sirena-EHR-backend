"""
Tests that anti-brute-force throttles are *actually wired* on the auth views.

Configuring DEFAULT_THROTTLE_RATES in settings does nothing unless each view
also declares throttle_classes. Easy to forget; easy to regress. These tests
catch the regression two ways:

  1. Structural — resolve the URL, inspect the view's throttle_classes
  2. Runtime — hit the endpoint with the production rate (5/min for login)
     and verify the 6th attempt returns 429

We deliberately do NOT override DEFAULT_THROTTLE_RATES in these tests.
DRF's SimpleRateThrottle captures THROTTLE_RATES as a class attribute at
import time, and override_settings does not reliably invalidate that
reference once any prior test has triggered the throttle to parse a rate
(the cached parsed rate sticks). Testing against the real production rate
sidesteps that whole class of test-isolation bugs and exercises the same
config the production deployment uses.
"""
import pytest
from django.core.cache import cache
from django.urls import reverse


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    cache.clear()
    yield
    cache.clear()


# Production login throttle: 5 attempts per minute (settings.REST_FRAMEWORK
# DEFAULT_THROTTLE_RATES['login']). The 6th attempt within the window should
# return 429.
LOGIN_RATE_LIMIT = 5


# ─── Structural tests (cheap, run regardless of cache state) ────────────────

class TestThrottleWiring:
    """
    Confirms each auth view declares its expected throttle. These tests
    don't make HTTP calls — they introspect the URL resolver. If an engineer
    accidentally drops `throttle_classes` from one of the auth views (or
    forgets to add it on a new one), these fail loudly.
    """

    def _resolve(self, path: str):
        from django.urls import resolve
        return resolve(path).func.cls

    def test_login_has_login_throttle(self):
        from apps.accounts.views import LoginRateThrottle
        assert LoginRateThrottle in self._resolve('/api/v1/auth/login/').throttle_classes

    def test_password_reset_request_has_login_throttle(self):
        from apps.accounts.views import LoginRateThrottle
        assert LoginRateThrottle in self._resolve(
            '/api/v1/auth/password-reset/'
        ).throttle_classes

    def test_password_reset_confirm_has_login_throttle(self):
        from apps.accounts.views import LoginRateThrottle
        assert LoginRateThrottle in self._resolve(
            '/api/v1/auth/password-reset/confirm/'
        ).throttle_classes

    def test_token_refresh_has_token_refresh_throttle(self):
        from apps.accounts.urls import _TokenRefreshThrottle
        assert _TokenRefreshThrottle in self._resolve(
            '/api/v1/auth/token/refresh/'
        ).throttle_classes

    def test_token_refresh_scope_has_a_rate_configured(self):
        from django.conf import settings
        rates = settings.REST_FRAMEWORK.get('DEFAULT_THROTTLE_RATES', {})
        assert 'token_refresh' in rates, (
            'token_refresh throttle scope set but no rate in DEFAULT_THROTTLE_RATES'
        )


# ─── Runtime test: login throttle actually fires at production rate ─────────

@pytest.mark.django_db
class TestLoginThrottleRuntime:
    """
    Hits the real /login/ endpoint LOGIN_RATE_LIMIT + 1 times and verifies the
    last attempt returns 429. Uses the production rate so we don't need to
    override settings (which collides with DRF's class-level rate caching).
    """

    def test_429_after_production_burst(self, api_client):
        url = reverse('login')
        creds = {'email': 'noone@example.com', 'password': 'wrong'}

        # First N attempts must NOT be throttled.
        for i in range(LOGIN_RATE_LIMIT):
            r = api_client.post(url, creds, format='json')
            assert r.status_code != 429, (
                f'attempt {i + 1} of {LOGIN_RATE_LIMIT} hit 429 prematurely '
                f'— prior test polluted the throttle cache'
            )

        # (N+1)-th must be throttled.
        r = api_client.post(url, creds, format='json')
        assert r.status_code == 429, (
            f'login throttle did not fire on attempt {LOGIN_RATE_LIMIT + 1} '
            f'— got {r.status_code}'
        )
