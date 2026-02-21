"""
Client URL routes.

All coordinated with frontend api/clients.ts:
- /api/v1/clients/                              → ClientViewSet
- /api/v1/clients/{id}/authorizations/          → AuthorizationViewSet (nested)
- /api/v1/clients/{id}/documents/               → ClientDocumentViewSet (nested)
- /api/v1/authorizations/                       → TopLevelAuthorizationViewSet (direct create/update)
- /api/v1/clients/import/                       → ClientViewSet.import_csv
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_nested import routers as nested_routers
from .views import (
    ClientViewSet,
    AuthorizationViewSet,
    TopLevelAuthorizationViewSet,
    ClientDocumentViewSet,
)

# Top-level client routes
router = DefaultRouter()
router.register(r'clients', ClientViewSet, basename='client')
router.register(r'authorizations', TopLevelAuthorizationViewSet, basename='authorization')

# Nested: /api/v1/clients/{client_pk}/authorizations/
clients_router = nested_routers.NestedDefaultRouter(router, r'clients', lookup='client')
clients_router.register(r'authorizations', AuthorizationViewSet, basename='client-authorization')
clients_router.register(r'documents', ClientDocumentViewSet, basename='client-document')

urlpatterns = [
    path('', include(router.urls)),
    path('', include(clients_router.urls)),
]
