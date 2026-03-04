"""
Custom middleware for multi-tenancy and audit logging.

OrganizationMiddleware: Attaches request.organization from the authenticated user.
AuditMiddleware: Logs all write operations to the audit_logs table.
"""
import json
import logging

from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)


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
    """

    WRITE_METHODS = ('POST', 'PUT', 'PATCH', 'DELETE')
    SKIP_PATHS = ('/admin/', '/api/v1/auth/login/', '/api/v1/auth/token/refresh/')

    def process_response(self, request, response):
        # Only log write operations
        if request.method not in self.WRITE_METHODS:
            return response

        # Skip certain paths
        if any(request.path.startswith(skip) for skip in self.SKIP_PATHS):
            return response

        # Only log successful operations
        if response.status_code >= 400:
            return response

        # Only log for authenticated users
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return response

        try:
            from apps.audit.models import AuditLog

            # Determine action from method
            action_map = {
                'POST': 'create',
                'PUT': 'update',
                'PATCH': 'partial_update',
                'DELETE': 'delete',
            }

            # Parse request body for changes
            changes = None
            if request.content_type and 'json' in request.content_type:
                try:
                    changes = json.loads(request.body.decode('utf-8')) if request.body else None
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass

            # FIX PII-1: Mask sensitive fields before storing in audit log.
            # This prevents passwords, SSNs, and other PII from being stored
            # in plaintext audit records.
            if changes and isinstance(changes, dict):
                SENSITIVE_KEYS = {
                    'password', 'password1', 'password2', 'old_password',
                    'new_password', 'confirm_password',
                    'ssn', 'social_security', 'social_security_number',
                    'date_of_birth', 'dob',
                    'credit_card', 'card_number', 'cvv', 'cvc',
                    'token', 'secret', 'api_key', 'refresh',
                }
                changes = {
                    k: '***REDACTED***' if k.lower() in SENSITIVE_KEYS else v
                    for k, v in changes.items()
                }

            # Extract table/record info from the URL path
            path_parts = [p for p in request.path.split('/') if p]

            AuditLog.objects.create(
                organization=getattr(request, 'organization', None),
                user=request.user,
                action=action_map.get(request.method, request.method.lower()),
                table_name=path_parts[-2] if len(path_parts) >= 2 else path_parts[-1] if path_parts else 'unknown',
                ip_address=self._get_client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', ''),
                changes=changes,
            )
        except Exception as e:
            # FIX #12: Never let audit logging break the request,
            # but DO log the error so we can debug it.
            logger.warning(f'Audit log creation failed: {e}')

        return response

    def _get_client_ip(self, request):
        """Extract client IP, considering X-Forwarded-For for proxies."""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '0.0.0.0')
