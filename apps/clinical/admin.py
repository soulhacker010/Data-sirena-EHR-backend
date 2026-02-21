from django.contrib import admin
from .models import NoteTemplate, SessionNote, TreatmentPlan, Document


@admin.register(NoteTemplate)
class NoteTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'template_type', 'organization', 'created_at']
    search_fields = ['name']


@admin.register(SessionNote)
class SessionNoteAdmin(admin.ModelAdmin):
    list_display = ['client', 'provider', 'status', 'is_locked', 'signed_at', 'created_at']
    list_filter = ['status', 'is_locked']
    search_fields = ['client__first_name', 'client__last_name']


@admin.register(TreatmentPlan)
class TreatmentPlanAdmin(admin.ModelAdmin):
    list_display = ['client', 'provider', 'start_date', 'review_date', 'is_active']
    list_filter = ['is_active']


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ['file_name', 'client', 'document_type', 'uploaded_by', 'created_at']
    search_fields = ['file_name']
