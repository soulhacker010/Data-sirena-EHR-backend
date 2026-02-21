from django.contrib import admin
from .models import Client, Authorization


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ['last_name', 'first_name', 'date_of_birth', 'phone', 'insurance_primary_name', 'is_active']
    list_filter = ['is_active', 'organization']
    search_fields = ['first_name', 'last_name', 'email', 'phone']


@admin.register(Authorization)
class AuthorizationAdmin(admin.ModelAdmin):
    list_display = ['authorization_number', 'client', 'service_code', 'units_approved', 'units_used', 'start_date', 'end_date']
    list_filter = ['service_code']
    search_fields = ['authorization_number']
