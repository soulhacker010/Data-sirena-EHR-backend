"""
Custom middleware for multi-tenancy, audit logging, and session activity.

OrganizationMiddleware: Attaches request.organization from the authenticated user.
AuditMiddleware:        Logs all write operations to the audit_logs table.
LastSeenMiddleware:     Updates User.last_seen_at on each request (debounced)
                        so the token refresh view can enforce idle timeout.
"""
import json
import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)

from apps.core.sentry import PHI_FIELD_NAMES, scrub_phi

# AuditMiddleware snapshots the whole JSON request body onto the audit row, so
# the scrubber below is the only thing standing between a patient chart edit
# and a permanent PHI copy in `audit_logs.changes` — a table that gets exported
# wholesale for compliance review.
#
# We reuse the Sentry scrubber's vocabulary and its recursive walk rather than
# maintaining a second list that drifts. Recursion is the important part:
# request bodies nest (a session note posts its narrative under `note_data`),
# and a top-level-only pass would wave the entire clinical note through while
# looking like it was doing its job.
#
# Over-inclusive is the safe failure mode: a redacted non-PHI field costs a
# little debugging context, a leaked PHI field is a disclosure. The audit row
# still records who / what / when / from where plus the opaque record_id, which
# is what HIPAA §164.312(b) actually requires.
SENSITIVE_KEYS = PHI_FIELD_NAMES  # retained for callers that introspect it


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
                        # Keep the keys, drop the values, at every depth: the
                        # audit row still records *which* fields a user changed
                        # without persisting the PHI itself.
                        changes = scrub_phi(raw)
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


class LastSeenMiddleware(MiddlewareMixin):
    """
    Touches User.last_seen_at on every authenticated request, debounced to one
    write per minute per user so a busy clinician doesn't trigger 100 writes
    in a session.

    Why this matters: the JWT refresh token lifetime is 7 days. Without an
    idle-tracking signal, a forgotten browser tab can mint fresh access
    tokens for a week. By stamping last_seen_at on every request, the token
    refresh view can short-circuit refreshes from sessions that have been
    idle for more than IDLE_TIMEOUT_MINUTES (default 30) and force re-login.

    Skipped requests:
        - Unauthenticated (anonymous, login, password reset, refresh itself)
        - Token refresh path (we don't want a refresh attempt to count as
          "activity" — that's circular)
    """

    DEBOUNCE_SECONDS = 60
    REFRESH_PATH_SUFFIX = '/auth/token/refresh/'

    def process_request(self, request):
        if not getattr(request, 'user', None) or not request.user.is_authenticated:
            return
        if request.path.endswith(self.REFRESH_PATH_SUFFIX):
            return

        user = request.user
        now = timezone.now()
        last = user.last_seen_at
        if last and (now - last).total_seconds() < self.DEBOUNCE_SECONDS:
            return

        # Use update() to skip auto_now / signals — this is hot-path code,
        # we just want the column written. update_fields keeps the audit
        # surface tight.
        try:
            type(user).objects.filter(pk=user.pk).update(last_seen_at=now)
            user.last_seen_at = now  # so subsequent middleware sees the new value
        except Exception:
            # Never fail a request because we couldn't bump activity timestamp
            logger.exception('LastSeenMiddleware failed to update last_seen_at')


def is_session_idle(user, timeout_minutes: int | None = None) -> bool:
    """
    Pure helper used by the token refresh view. Separated from middleware so
    it's trivially unit-testable without a request.

    A user with no last_seen_at (first-ever refresh, or migrated user) is
    treated as not idle — we don't lock them out on first access.
    """
    if user.last_seen_at is None:
        return False
    timeout_minutes = timeout_minutes if timeout_minutes is not None \
        else getattr(settings, 'IDLE_TIMEOUT_MINUTES', 30)
    return (timezone.now() - user.last_seen_at) > timedelta(minutes=timeout_minutes)
