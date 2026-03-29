"""
Authentication and user management views.

Coordinates with frontend:
- POST /api/v1/auth/login/          → LoginPage → authApi.login()
- POST /api/v1/auth/token/refresh/  → Axios interceptor → authApi.refreshToken()
- GET  /api/v1/auth/me/             → AuthContext → authApi.getMe()
- PUT  /api/v1/auth/password/       → SettingsPage → authApi.changePassword()
- GET  /api/v1/auth/organization/   → SettingsPage → settingsApi.getOrganization()
- PUT  /api/v1/auth/organization/   → SettingsPage → settingsApi.updateOrganization()
- GET/POST /api/v1/users/           → AdminUsersPage → userApi (via admin)
- GET  /api/v1/auth/locations/      → LocationSelect → lookupsApi.getLocations()
- GET  /api/v1/auth/providers/      → ProviderSelect → lookupsApi.getProviders()

Hardening fixes applied:
- FIX #8:  Admin cannot deactivate themselves
- FIX #11: Logout token blacklist errors are logged, not silently swallowed
"""
import logging

from django.utils import timezone
from rest_framework import generics, serializers as drf_serializers, status, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.permissions import IsAdmin
from .models import User, Location, NotificationPreference
from .serializers import (
    LoginSerializer,
    UserSerializer,
    UserCreateSerializer,
    UserUpdateSerializer,
    ProfileUpdateSerializer,
    ChangePasswordSerializer,
    OrganizationSerializer,
    LocationSerializer,
    NotificationPreferenceSerializer,
)


# FIX RL-2: Brute-force prevention — 5 login attempts per minute per IP
class LoginRateThrottle(AnonRateThrottle):
    """Strict throttle for login endpoint to prevent credential brute-forcing."""
    scope = 'login'


class LoginView(generics.GenericAPIView):
    """
    POST /api/v1/auth/login/

    Returns access + refresh tokens with user data.
    Matches frontend LoginRequest/LoginResponse types.
    """
    permission_classes = [AllowAny]
    throttle_classes = [LoginRateThrottle]
    serializer_class = LoginSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data['user']
        user.last_login = timezone.now()
        user.save(update_fields=['last_login'])

        refresh = RefreshToken.for_user(user)

        return Response({
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': UserSerializer(user).data,
        })


class MeView(generics.RetrieveUpdateAPIView):
    """
    GET /api/v1/auth/me/  → Returns the currently authenticated user's profile.
    PUT /api/v1/auth/me/  → Updates the user's own first_name, last_name.

    Called by AuthContext on app load (GET) and SettingsPage profile save (PUT).
    """
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.request.method in ('PUT', 'PATCH'):
            return ProfileUpdateSerializer
        return UserSerializer

    def get_object(self):
        return self.request.user


class ChangePasswordView(generics.GenericAPIView):
    """
    PUT /api/v1/auth/password/

    Changes the authenticated user's password.
    Triggered from SettingsPage.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ChangePasswordSerializer

    def put(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        request.user.set_password(serializer.validated_data['new_password'])
        request.user.save(update_fields=['password'])

        return Response({'message': 'Password updated successfully'})


class LogoutView(APIView):
    """
    POST /api/v1/auth/logout/

    Blacklists the refresh token so it can no longer be used.
    Frontend calls this on logout button click.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # FIX #11: Log blacklist errors instead of silently swallowing
        logger = logging.getLogger(__name__)
        try:
            refresh_token = request.data.get('refresh')
            if refresh_token:
                token = RefreshToken(refresh_token)
                token.blacklist()
        except Exception as e:
            # Token might already be invalid or blacklisted — log it
            logger.warning(f'Token blacklist failed during logout: {e}')
        return Response({'message': 'Logged out successfully'}, status=status.HTTP_200_OK)


class OrganizationSettingsView(generics.RetrieveUpdateAPIView):
    """
    GET/PUT /api/v1/auth/organization/

    Returns or updates the organization the current user belongs to.
    Only admins can update; all authenticated users can view.
    Triggered from SettingsPage practice settings.
    """
    serializer_class = OrganizationSerializer

    def get_permissions(self):
        if self.request.method in ('PUT', 'PATCH'):
            return [IsAuthenticated(), IsAdmin()]
        return [IsAuthenticated()]

    def get_object(self):
        return self.request.user.organization


class UserViewSet(viewsets.ModelViewSet):
    """
    Admin-only user management.

    GET    /api/v1/users/       → List all users in the organization
    POST   /api/v1/users/       → Create a new user
    GET    /api/v1/users/{id}/  → Get user details
    PUT    /api/v1/users/{id}/  → Update user
    DELETE /api/v1/users/{id}/  → Deactivate user
    """
    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = UserSerializer
    filterset_fields = ['role', 'is_active']
    search_fields = ['first_name', 'last_name', 'email']
    ordering_fields = ['last_name', 'created_at']

    def get_queryset(self):
        """Scope users to the admin's organization."""
        return User.objects.filter(
            organization=self.request.user.organization
        ).select_related('organization')

    def get_serializer_class(self):
        if self.action == 'create':
            return UserCreateSerializer
        if self.action in ('update', 'partial_update'):
            return UserUpdateSerializer
        return UserSerializer

    def perform_create(self, serializer):
        """Create user and send welcome email."""
        import logging
        logger = logging.getLogger(__name__)

        request_org = self.request.user.organization_id
        payload_org = serializer.validated_data.get('organization_id')
        if payload_org != request_org:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({
                'organization_id': 'You can only create users in your own organization.'
            })

        user = serializer.save()

        # FIX #2: Log errors instead of silently swallowing them
        if not user.email:
            logger.warning(f'User {user} created without email - skipping welcome email')
            return

        try:
            from apps.core.email import EmailService
            temp_password = self.request.data.get('password', '')
            EmailService.send_welcome_email(user, temp_password=temp_password)
        except ValueError as e:
            logger.warning(f'Welcome email skipped for {user.email}: {e}')
        except Exception as e:
            logger.error(
                f'Failed to send welcome email to {user.email}: {e}',
                exc_info=True,
            )

    def perform_destroy(self, instance):
        """
        Soft-delete: deactivate instead of deleting.

        FIX #8: Admin cannot deactivate themselves — prevents lockout.
        """
        if instance.pk == self.request.user.pk:
            from rest_framework.exceptions import ValidationError
            raise ValidationError(
                {'detail': 'You cannot deactivate your own account.'}
            )

        instance.is_active = False
        instance.save(update_fields=['is_active'])


# ─── Lookup endpoints (any authenticated user) ────────────────────────────────

class LocationListView(generics.ListAPIView):
    """
    GET /api/v1/auth/locations/

    Returns active locations for the current user's organization.
    Used by LocationSelect dropdown in notes and appointments.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = LocationSerializer
    pagination_class = None

    def get_queryset(self):
        return Location.objects.filter(
            organization=self.request.user.organization,
            is_active=True,
        )


class _ProviderSerializer(drf_serializers.ModelSerializer):
    """Lightweight read-only serializer for provider dropdown."""
    name = drf_serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'name', 'first_name', 'last_name', 'role', 'credentials']
        read_only_fields = fields

    def get_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip()


class ProviderListView(generics.ListAPIView):
    """
    GET /api/v1/auth/providers/

    Returns active staff members (providers) for the current user's organization.
    Used by ProviderSelect dropdown in notes and appointments.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = _ProviderSerializer
    pagination_class = None

    def get_queryset(self):
        return User.objects.filter(
            organization=self.request.user.organization,
            is_active=True,
            role__in=['admin', 'supervisor', 'clinician'],
        ).order_by('last_name', 'first_name')


class NotificationPreferenceView(APIView):
    """
    GET  /api/v1/auth/notifications/preferences/
    PUT  /api/v1/auth/notifications/preferences/

    Returns or updates notification preferences for the current user.
    Auto-creates default preferences on first access.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        prefs, _ = NotificationPreference.objects.get_or_create(user=request.user)
        serializer = NotificationPreferenceSerializer(prefs)
        return Response(serializer.data)

    def put(self, request):
        prefs, _ = NotificationPreference.objects.get_or_create(user=request.user)
        serializer = NotificationPreferenceSerializer(prefs, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
