"""
Shared audit logging helper.

Call write_audit() from any view to create an explicit audit entry.
The AuditMiddleware handles write operations automatically — use this
for events the middleware doesn't cover: logins, PHI access, exports, signing.
"""
import logging

logger = logging.getLogger(__name__)

# Maps URL path segments → friendly resource names for middleware
RESOURCE_MAP = {
    'clients': 'clients',
    'notes': 'notes',
    'appointments': 'appointments',
    'scheduling': 'appointments',
    'invoices': 'invoices',
    'payments': 'payments',
    'claims': 'claims',
    'users': 'users',
    'intakes': 'intakes',
    'treatment-plans': 'treatment_plans',
    'treatmentplans': 'treatment_plans',
    'documents': 'documents',
    'authorizations': 'authorizations',
    'auth': 'auth',
    'reports': 'reports',
    'billing': 'billing',
}


def extract_resource_from_path(path_parts: list) -> tuple:
    """
    Return (table_name, record_id) from URL path segments.

    Walks the path backwards looking for a known resource name.
    The segment after it (if UUID-like) becomes the record_id.
    """
    import re
    UUID_RE = re.compile(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        re.IGNORECASE
    )

    table_name = 'unknown'
    record_id = None

    for i, part in enumerate(path_parts):
        mapped = RESOURCE_MAP.get(part)
        if mapped:
            table_name = mapped
            # Next segment might be a UUID (record ID)
            if i + 1 < len(path_parts) and UUID_RE.match(path_parts[i + 1]):
                record_id = path_parts[i + 1]
            break

    return table_name, record_id


def write_audit(request, action: str, table_name: str, record_id=None, changes: dict = None):
    """
    Write a single audit log entry.

    Args:
        request:    Django request object (provides user, org, IP, UA)
        action:     Action string — login, logout, sign, co_sign, phi_access,
                    document_access, export, failed_login, password_change, etc.
        table_name: Friendly resource name (e.g. 'clients', 'notes')
        record_id:  UUID string of the affected record (optional)
        changes:    Dict of relevant metadata (optional, PII already excluded by caller)
    """
    try:
        from apps.audit.models import AuditLog

        user = getattr(request, 'user', None)
        if user and not user.is_authenticated:
            user = None

        org = getattr(request, 'organization', None)
        # SimpleLazyObject — force evaluation
        try:
            org = org._wrapped if hasattr(org, '_wrapped') else org
        except Exception:
            org = None
        if org and not hasattr(org, 'pk'):
            org = None
        # Fallback: pull org from the authenticated user if middleware didn't set it
        if not org and user is not None:
            org = getattr(user, 'organization', None)

        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        ip = x_forwarded_for.split(',')[0].strip() if x_forwarded_for else request.META.get('REMOTE_ADDR', '0.0.0.0')

        AuditLog.objects.create(
            organization=org,
            user=user,
            action=action,
            table_name=table_name,
            record_id=record_id or None,
            ip_address=ip,
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
            changes=changes,
        )
    except Exception as e:
        logger.warning('Explicit audit write failed (%s / %s): %s', action, table_name, e)
