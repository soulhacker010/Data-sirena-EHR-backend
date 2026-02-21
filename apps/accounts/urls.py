"""
Account URL routes.

Auth endpoints:     /api/v1/auth/login/, /api/v1/auth/token/refresh/, /api/v1/auth/me/, /api/v1/auth/password/
Organization:       /api/v1/auth/organization/ (GET any auth, PUT admin-only)
User management:    /api/v1/auth/users/ (admin-only)
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView
from .views import LoginView, LogoutView, MeView, ChangePasswordView, OrganizationSettingsView, UserViewSet

router = DefaultRouter()
router.register(r'users', UserViewSet, basename='user')

urlpatterns = [
    # Auth
    path('login/', LoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
    path('me/', MeView.as_view(), name='me'),
    path('password/', ChangePasswordView.as_view(), name='change-password'),
    path('organization/', OrganizationSettingsView.as_view(), name='organization-settings'),

    # User management (admin)
    path('', include(router.urls)),
]
