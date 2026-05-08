"""
Custom middleware for multi-tenancy and audit logging.

OrganizationMiddleware: Attaches request.organization from the authenticated user.
AuditMiddleware: Logs all write operations to the audit_logs table.
"""
import json
import logging

from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)

SENSITIVE_KEYS = {
    'password', 'password1', 'password2', 'old_password',
    'new_password', 'confirm_password',
    'ssn', 'social_security', 'social_security_number',
    'date_of_birth', 'dob',
    'credit_card', 'card_number', 'cvv', 'cvc',
    'token', 'secret', 'api_key', 'refresh',
}


class OrganizationMiddleware(MiddlewareMixin):
    """
    Injects `request.organization` from the authenticated user's organization.
    This enables all views to scope queries to the user's organization without
    manually looking it up each time.

    Uses SimpleLazyObject so the lookup is deferred until the attribute is
    actually accessed — by that time DRF's JWT authentication has already run.
    """

    def process_request(self, request):
        from django.utils.functional import SimpleLazyObject

        def _get_organization():
            if hasattr(request, 'user') and request.user.is_authenticated:
                return getattr(request.user, 'organization', None)
            return None

        request.organization = SimpleLazyObject(_get_organization)


class AuditMiddleware(MiddlewareMixin):
    """
    Automatically logs all write operations (POST, PUT, PATCH, DELETE)
    to the audit_logs table for HIPAA compliance.

    Explicit events (login, sign, PHI access, export, etc.) are written
    directly from views using apps.audit.utils.write_audit().
    """

    WRITE_METHODS = ('POST', 'PUT', 'PATCH', 'DELETE')
    # Skip paths handled by explicit view-level audit calls
    SKIP_PATHS = (
        '/api/v1/auth/login/',
        '/api/v1/auth/token/refresh/',
        '/api/v1/auth/logout/',
        '/api/v1/auth/password/',
    )

    def process_request(self, request):
        """
        Capture request body BEFORE the view consumes it.

        Once a DRF view reads request.data, the underlying stream is drained
        and request.body becomes inaccessible. So we snapshot the body here
        and stash it on the request for process_response to read.
        """
        if request.method not in self.WRITE_METHODS:
            return
        if not (request.content_type and 'json' in request.content_type):
            return
        try:
            request._audit_body = request.body
        except Exception:
            request._audit_body = None

    def process_response(self, request, response):
        if request.method not in self.WRITE_METHODS:
            return response

        if any(request.path.startswith(skip) for skip in self.SKIP_PATHS):
            return response

        if response.status_code >= 400:
            return response

        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return response

        try:
            from apps.audit.models import AuditLog
            from apps.audit.utils import extract_resource_from_path

            action_map = {
                'POST': 'create',
                'PUT': 'update',
                'PATCH': 'update',
                'DELETE': 'delete',
            }

            changes = None
            body = getattr(request, '_audit_body', None)
            if body:
                try:
                    raw = json.loads(body.decode('utf-8'))
                    if raw and isinstance(raw, dict):
                        changes = {
                            k: '***REDACTED***' if k.lower() in SENSITIVE_KEYS else v
                            for k, v in raw.items()
                        }
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass

            path_parts = [p for p in request.path.split('/') if p]
            table_name, record_id = extract_resource_from_path(path_parts)

            org = getattr(request, 'organization', None)

            AuditLog.objects.create(
                organization=org,
                user=request.user,
                action=action_map.get(request.method, request.method.lower()),
                table_name=table_name,
                record_id=record_id or None,
                ip_address=self._get_client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', ''),
                changes=changes,
            )
        except Exception as e:
            logger.warning('Audit log creation failed: %s', e)

        return response

    def _get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '0.0.0.0')
