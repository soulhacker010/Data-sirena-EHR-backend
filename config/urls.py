"""
Root URL Configuration for Sirena Health EHR.

All API endpoints are under /api/v1/ prefix.
User management moved to top-level /api/v1/users/ (not under /auth/).
"""
import os

from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path(f'{os.getenv("DJANGO_ADMIN_URL", "admin")}/', admin.site.urls),

    # API v1
    path('api/v1/auth/', include('apps.accounts.urls')),
    path('api/v1/', include('apps.clients.urls')),
    path('api/v1/', include('apps.scheduling.urls')),
    path('api/v1/', include('apps.clinical.urls')),
    path('api/v1/', include('apps.billing.urls')),
    path('api/v1/', include('apps.dashboard.urls')),
    path('api/v1/', include('apps.reports.urls')),
    path('api/v1/', include('apps.notifications.urls')),
    path('api/v1/', include('apps.audit.urls')),
]
