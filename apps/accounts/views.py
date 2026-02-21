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

Hardening fixes applied:
- FIX #8:  Admin cannot deactivate themselves
- FIX #11: Logout token blacklist errors are logged, not silently swallowed
"""
import logging

from django.utils import timezone
from rest_framework import generics, status, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.permissions import IsAdmin
from .models import User
from .serializers import (
    LoginSerializer,
    UserSerializer,
    UserCreateSerializer,
    UserUpdateSerializer,
    ChangePasswordSerializer,
    OrganizationSerializer,
)


class LoginView(generics.GenericAPIView):
    """
    POST /api/v1/auth/login/

    Returns access + refresh tokens with user data.
    Matches frontend LoginRequest/LoginResponse types.
    """
    permission_classes = [AllowAny]
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


class MeView(generics.RetrieveAPIView):
    """
    GET /api/v1/auth/me/

    Returns the currently authenticated user's profile.
    Called by AuthContext on app load.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = UserSerializer

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
