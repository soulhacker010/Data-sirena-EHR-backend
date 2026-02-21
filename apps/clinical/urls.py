"""
Clinical URL routes.

Coordinated with frontend api/notes.ts:
- /api/v1/notes/                     → SessionNoteViewSet
- /api/v1/notes/{id}/sign/           → SessionNoteViewSet.sign
- /api/v1/notes/{id}/cosign/         → SessionNoteViewSet.co_sign
- /api/v1/notes/templates/           → NoteTemplateViewSet (alias for frontend)
- /api/v1/note-templates/            → NoteTemplateViewSet (original)
- /api/v1/treatment-plans/           → TreatmentPlanViewSet
- /api/v1/documents/                 → DocumentViewSet
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import NoteTemplateViewSet, SessionNoteViewSet, TreatmentPlanViewSet, DocumentViewSet

router = DefaultRouter()
router.register(r'note-templates', NoteTemplateViewSet, basename='note-template')
router.register(r'notes', SessionNoteViewSet, basename='session-note')
router.register(r'treatment-plans', TreatmentPlanViewSet, basename='treatment-plan')
router.register(r'documents', DocumentViewSet, basename='document')

# Frontend calls GET /notes/templates/ — add an alias
templates_router = DefaultRouter()
templates_router.register(r'templates', NoteTemplateViewSet, basename='notes-templates')

urlpatterns = [
    path('', include(router.urls)),
    # Alias: /api/v1/notes/templates/ → same NoteTemplateViewSet
    path('notes/', include(templates_router.urls)),
]
