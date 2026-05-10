"""
Account URL routes.

Auth endpoints:     /api/v1/auth/login/, /api/v1/auth/token/refresh/, /api/v1/auth/me/, /api/v1/auth/password/
Organization:       /api/v1/auth/organization/ (GET any auth, PUT admin-only)
User management:    /api/v1/auth/users/ (admin-only)
"""
from django.urls import path, include
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.routers import DefaultRouter
from rest_framework.throttling import AnonRateThrottle
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView
from .views import (
    LoginView, LogoutView, MeView, ChangePasswordView,
    OrganizationSettingsView, UserViewSet, NPIViewSet,
    LocationListView, ProviderListView,
    NotificationPreferenceView,
    PasswordResetRequestView, PasswordResetConfirmView,
)


class _TokenRefreshThrottle(AnonRateThrottle):
    """
    Rate-limit /token/refresh/ to blunt credential-stuffing using leaked
    refresh tokens. Legit clients refresh on idle (~ every 14 min) so the
    rate is generous; the goal is to stop sustained scanning, not throttle
    real users.
    """
    scope = 'token_refresh'


class _IdleAwareTokenRefreshView(TokenRefreshView):
    """
    Token refresh with two extra checks beyond simplejwt's defaults:

        1. Rate-limit (`_TokenRefreshThrottle`) — stops brute-force.
        2. Idle timeout — rejects refreshes from sessions where the user has
           been inactive longer than IDLE_TIMEOUT_MINUTES. Prevents an
           abandoned tab from minting fresh access tokens for the full 7-day
           refresh-token lifetime.

    The idle check decodes the refresh token, looks up the user, and compares
    User.last_seen_at to now. We deliberately decode here (cheaply) rather
    than running full DRF auth — the refresh endpoint isn't authenticated, so
    request.user is anonymous at this point.
    """
    throttle_classes = [_TokenRefreshThrottle]

    def post(self, request, *args, **kwargs):
        from django.conf import settings
        from apps.core.middleware import is_session_idle
        from apps.accounts.models import User
        from apps.audit.utils import write_audit

        raw_refresh = request.data.get('refresh', '')
        if raw_refresh:
            try:
                token = RefreshToken(raw_refresh)
                user_id = token.get('user_id')
            except (TokenError, InvalidToken, KeyError):
                # Let the parent handler surface the standard 401 — we don't
                # need to second-guess the token decoder.
                user_id = None

            if user_id is not None:
                try:
                    user = User.objects.only('id', 'last_seen_at').get(pk=user_id)
                except User.DoesNotExist:
                    user = None

                if user is not None and is_session_idle(
                    user, settings.IDLE_TIMEOUT_MINUTES,
                ):
                    write_audit(
                        request, 'session_idle_timeout', 'auth',
                        changes={'user_id': str(user.pk)},
                    )
                    raise AuthenticationFailed(
                        detail='Session expired due to inactivity. Please sign in again.',
                        code='session_idle',
                    )

        return super().post(request, *args, **kwargs)

router = DefaultRouter()
router.register(r'users', UserViewSet, basename='user')
router.register(r'npis', NPIViewSet, basename='npi')

urlpatterns = [
    # Auth
    path('login/', LoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('token/refresh/', _IdleAwareTokenRefreshView.as_view(), name='token-refresh'),
    path('me/', MeView.as_view(), name='me'),
    path('password/', ChangePasswordView.as_view(), name='change-password'),
    path('password-reset/', PasswordResetRequestView.as_view(), name='password-reset-request'),
    path('password-reset/confirm/', PasswordResetConfirmView.as_view(), name='password-reset-confirm'),
    path('organization/', OrganizationSettingsView.as_view(), name='organization-settings'),

    # Lookup endpoints (any authenticated user)
    path('locations/', LocationListView.as_view(), name='location-list'),
    path('providers/', ProviderListView.as_view(), name='provider-list'),
    path('notifications/preferences/', NotificationPreferenceView.as_view(), name='notification-preferences'),

    # User management (admin)
    path('', include(router.urls)),
]
