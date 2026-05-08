"""
Custom permission classes for role-based access control.

Matches roles from backend.md:
- Admin: everything
- Supervisor: clinical + co-sign + reports
- Clinician: own clients, own notes, own calendar
- Biller: billing, invoices, claims, payments
- Front Desk: scheduling, client intake
"""
from rest_framework.permissions import BasePermission


class IsAdmin(BasePermission):
    """Only admins."""
    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role == 'admin'
        )


class IsSupervisorOrAbove(BasePermission):
    """Supervisors and admins."""
    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role in ('admin', 'supervisor')
        )


class IsClinician(BasePermission):
    """Clinicians, supervisors, and admins."""
    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role in ('admin', 'supervisor', 'clinician')
        )


class IsBiller(BasePermission):
    """Billers and admins."""
    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role in ('admin', 'biller')
        )


class IsFrontDesk(BasePermission):
    """
    Anyone in the practice may access scheduling — admins, supervisors,
    clinicians, front desk, AND billers.

    E25 (Dr. Joe 2026-05-04 feedback — "Ensure everyone has permission to
    create appointments etc."): billers were previously excluded but small
    practices share scheduling duties across roles; honoring the explicit
    request rather than enforcing a strict role-silo we didn't ship with
    a workflow rationale.
    """
    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role in (
                'admin', 'supervisor', 'clinician', 'front_desk', 'biller',
            )
        )


class IsClinicalStaff(BasePermission):
    """Anyone with clinical access: clinicians, supervisors, admins."""
    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role in ('admin', 'supervisor', 'clinician')
        )


class IsOwnerOrAdmin(BasePermission):
    """Object-level: owner of the record or admin."""
    def has_object_permission(self, request, view, obj):
        if request.user.role == 'admin':
            return True
        # Check for common owner fields
        if hasattr(obj, 'provider_id'):
            return obj.provider_id == request.user.id
        if hasattr(obj, 'user_id'):
            return obj.user_id == request.user.id
        if hasattr(obj, 'created_by_id'):
            return obj.created_by_id == request.user.id
        return False


class IsAnyAuthenticated(BasePermission):
    """Any authenticated user regardless of role."""
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated
