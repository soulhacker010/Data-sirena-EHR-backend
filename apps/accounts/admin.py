"""Django admin configuration for accounts."""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import Organization, User, NPI, Location


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ['name', 'contact_email', 'created_at']
    search_fields = ['name', 'contact_email']


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ['email', 'first_name', 'last_name', 'role', 'organization', 'is_active']
    list_filter = ['role', 'is_active', 'organization']
    search_fields = ['email', 'first_name', 'last_name']
    ordering = ['email']

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal Info', {'fields': ('first_name', 'last_name', 'credentials', 'licenses')}),
        ('Organization', {'fields': ('organization', 'role')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'first_name', 'last_name', 'organization', 'role', 'password1', 'password2'),
        }),
    )


@admin.register(NPI)
class NPIAdmin(admin.ModelAdmin):
    list_display = ['npi_number', 'business_name', 'organization', 'is_active']
    search_fields = ['npi_number', 'business_name']


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ['name', 'city', 'state', 'organization', 'is_telehealth', 'is_active']
    list_filter = ['is_telehealth', 'is_active']
    search_fields = ['name', 'city']
