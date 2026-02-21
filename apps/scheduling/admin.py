from django.contrib import admin
from .models import Appointment


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ['client', 'provider', 'start_time', 'service_code', 'status', 'is_recurring']
    list_filter = ['status', 'is_recurring', 'provider']
    search_fields = ['client__first_name', 'client__last_name', 'service_code']
    date_hierarchy = 'start_time'
