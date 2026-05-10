from django.contrib import admin

from .models import AppointmentReminder


@admin.register(AppointmentReminder)
class AppointmentReminderAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'appointment', 'lead_time', 'scheduled_for',
        'status', 'attempts', 'sent_at', 'provider',
    )
    list_filter = ('status', 'lead_time', 'provider', 'skip_reason')
    search_fields = ('appointment__id', 'provider_message_id')
    readonly_fields = (
        'id', 'created_at', 'updated_at', 'sent_at', 'attempts',
        'provider_message_id', 'last_error', 'skip_reason',
    )
    date_hierarchy = 'scheduled_for'
    ordering = ('-scheduled_for',)
