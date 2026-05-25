"""
Root URL Configuration for Sirena Health EHR.

All API endpoints are under /api/v1/ prefix.
User management moved to top-level /api/v1/users/ (not under /auth/).
"""
import os

from django.contrib import admin
from django.http import JsonResponse
from django.urls import path, include


def _root_health(_request):
    """
    Lightweight 200 OK for Render's uptime probe.

    Render's platform pings `GET /` to confirm the service is alive. Without
    a handler this returns 404 and spams the request log with WARN entries
    every minute. A flat 200 makes those checks silent and gives any other
    "is the box up?" probe a sane response too.
    """
    return JsonResponse({'status': 'ok', 'service': 'sirena-ehr-backend'})


urlpatterns = [
    path('', _root_health, name='health'),
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
