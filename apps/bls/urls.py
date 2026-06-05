"""
URL configuration for the BLS module. Mounted at /api/v1/bls/ in config/urls.py.

Layout:
  bls/sessions/                  GET (list — not exposed), POST (create)
  bls/sessions/{id}/             GET (detail), PATCH
  bls/sessions/{id}/end/         POST — end + persist
  bls/sessions/verify/           GET — public, validates a token
  bls/clients/{client_id}/history/   GET — per-client BLS history
  bls/preferences/{client_id}/       GET/PUT — per-client preferences
  bls/defaults/                       GET/PUT — org-wide defaults
"""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    BLSClientHistoryView,
    BLSClientPreferenceView,
    BLSOrgDefaultsView,
    BLSSessionViewSet,
    BLSShortCodeResolveView,
    BLSTokenVerifyView,
)


router = DefaultRouter()
router.register(r'sessions', BLSSessionViewSet, basename='bls-session')


urlpatterns = [
    # Public — used by the client view on page load to validate the invite
    # link. Registered BEFORE the router so /sessions/verify/ doesn't collide
    # with the ViewSet detail pattern (DRF would otherwise try to treat
    # "verify" as a UUID and 404).
    path('bls/sessions/verify/', BLSTokenVerifyView.as_view(), name='bls-session-verify'),
    path('bls/sessions/resolve/', BLSShortCodeResolveView.as_view(), name='bls-session-resolve'),

    # Authenticated client-context endpoints
    path(
        'bls/clients/<uuid:client_id>/history/',
        BLSClientHistoryView.as_view(),
        name='bls-client-history',
    ),
    path(
        'bls/preferences/<uuid:client_id>/',
        BLSClientPreferenceView.as_view(),
        name='bls-client-preference',
    ),
    path('bls/defaults/', BLSOrgDefaultsView.as_view(), name='bls-org-defaults'),

    # ViewSet routes (POST /sessions/, GET/POST /sessions/{id}/end/, etc.)
    path('bls/', include(router.urls)),
]
