"""Django admin registration for the BLS module."""
from django.contrib import admin

from .models import BLSClientPreference, BLSOrgDefaults, BLSSession


@admin.register(BLSSession)
class BLSSessionAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'organization',
        'client',
        'therapist',
        'status',
        'started_at',
        'ended_at',
        'pass_count',
        'set_count',
        'is_deleted',
    )
    list_filter = ('status', 'is_deleted', 'organization')
    search_fields = ('id', 'client__first_name', 'client__last_name')
    readonly_fields = ('id', 'token_hash', 'created_at', 'updated_at')


@admin.register(BLSClientPreference)
class BLSClientPreferenceAdmin(admin.ModelAdmin):
    list_display = ('client', 'last_used_at', 'updated_at')
    readonly_fields = ('id', 'created_at', 'updated_at')


@admin.register(BLSOrgDefaults)
class BLSOrgDefaultsAdmin(admin.ModelAdmin):
    list_display = ('organization', 'updated_at')
    readonly_fields = ('id', 'created_at', 'updated_at')
