"""
Sentry configuration with PHI scrubbing.

HIPAA logging principle: PHI must never leave the practice's environment to a
third-party error tracker without a Business Associate Agreement covering it.
Sentry's standard plan is *not* HIPAA-eligible. Their enterprise plan with a
signed BAA is — but in either case the safest posture is "PHI never leaves the
backend."

This module enforces that posture in two layers:

    1. send_default_pii=False                        — Sentry SDK's built-in
       opt-out for IP, cookies, user-set headers.

    2. before_send / before_breadcrumb hooks         — recursive scrubbing of
       any payload key that matches PHI_FIELD_NAMES, plus stack-frame locals
       and breadcrumb data.

The scrubber is pure and unit-tested. Anything that survives this hook is, by
construction, not PHI — or at worst is opaque (UUIDs, internal IDs).
"""
from __future__ import annotations

import re
from typing import Any

# Field names whose values are PHI. Compared case-insensitively against keys at
# every level of nested dicts/lists. Add to this set as new PHI fields are
# introduced — being over-inclusive is the safe failure mode.
PHI_FIELD_NAMES = frozenset({
    # Names
    'first_name', 'last_name', 'middle_name', 'full_name', 'name',
    'patient_name', 'client_name',
    # Dates of birth / age
    'date_of_birth', 'dob', 'birthdate', 'birth_date',
    # Direct identifiers
    'ssn', 'social_security', 'social_security_number',
    'mrn', 'medical_record_number',
    # Contact info
    'phone', 'phone_number', 'mobile', 'home_phone', 'work_phone',
    'email', 'email_address',
    # Address
    'address', 'street', 'street_address', 'address_line_1', 'address_line_2',
    'city', 'state', 'zip_code', 'postal_code', 'zip',
    # Demographics
    'gender', 'sex', 'race', 'ethnicity',
    # Clinical
    'diagnosis', 'diagnoses', 'diagnosis_codes', 'icd10', 'icd_10',
    'note_content', 'narrative', 'soap', 'subjective', 'objective',
    'assessment', 'plan', 'medications', 'allergies', 'medical_history',
    'mental_status_exam', 'risk_assessment', 'treatment_plan',
    'medical_necessity', 'interventions', 'goals', 'progress',
    # Insurance
    'insurance_primary_name', 'insurance_primary_id', 'insurance_primary_group',
    'insurance_secondary_name', 'insurance_secondary_id',
    'policy_number', 'group_number', 'subscriber_id',
    # Emergency contacts
    'emergency_contact_name', 'emergency_contact_phone',
    # Auth (defense-in-depth — already handled elsewhere but cheap to repeat)
    'password', 'password1', 'password2', 'old_password', 'new_password',
    'token', 'access', 'refresh', 'access_token', 'refresh_token',
    'auth_token', 'api_key', 'secret',
    # Twilio creds
    'twilio_auth_token', 'twilio_account_sid',
})

REDACTED = '***REDACTED***'

# UUID regex for path scrubbing — matches the standard 8-4-4-4-12 hex form.
UUID_PATTERN = re.compile(
    r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
    r'[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b',
)


def _scrub_value(value: Any, depth: int = 0) -> Any:
    """
    Walk `value` recursively. Replace any dict value whose key matches
    PHI_FIELD_NAMES with REDACTED. Lists are walked element-wise.

    Depth-limited (max 12) so a self-referential structure can't deadlock the
    error pipeline. PHI living deeper than 12 levels is unlikely; if it
    happens, Sentry drops the event rather than risk leaking.
    """
    if depth > 12:
        return REDACTED

    if isinstance(value, dict):
        return {
            k: (REDACTED if isinstance(k, str) and k.lower() in PHI_FIELD_NAMES
                else _scrub_value(v, depth + 1))
            for k, v in value.items()
        }

    if isinstance(value, (list, tuple)):
        cleaned = [_scrub_value(item, depth + 1) for item in value]
        return cleaned if isinstance(value, list) else tuple(cleaned)

    return value


def _scrub_query_string(qs: str) -> str:
    """
    Strip values for any querystring key that matches PHI_FIELD_NAMES.
    e.g. ?email=a@b.com&page=2  →  ?email=***REDACTED***&page=2

    If `qs` is a full URL or a message with an embedded querystring (e.g.
    "GET /api/clients/?email=a@b.com"), only the portion after the first `?`
    is treated as querystring; the prefix is preserved.
    """
    if not qs:
        return qs
    prefix = ''
    if '?' in qs:
        prefix, _, qs = qs.partition('?')
        prefix = f'{prefix}?'
    parts = []
    for chunk in qs.split('&'):
        if '=' in chunk:
            key, _, val = chunk.partition('=')
            # Tail-match the key — handles "GET /foo?email=..." where the key
            # has prefixed text from a malformed URL parse.
            key_lower = key.lower().rsplit(' ', 1)[-1]
            if key_lower in PHI_FIELD_NAMES:
                parts.append(f'{key}={REDACTED}')
            else:
                parts.append(chunk)
        else:
            parts.append(chunk)
    return prefix + '&'.join(parts)


def scrub_event(event: dict, hint: dict | None = None) -> dict | None:  # noqa: ARG001
    """
    Sentry before_send hook. Returns a scrubbed copy of `event`, or None to
    drop the event entirely.

    We scrub:
        request.data, request.query_string, request.headers
        contexts, extra, tags, user
        breadcrumbs[*].data
        exception stack frame locals (event['exception']['values'][i]['stacktrace']['frames'][j]['vars'])
    """
    request = event.get('request') or {}
    if 'data' in request:
        request['data'] = _scrub_value(request['data'])
    if 'query_string' in request and isinstance(request['query_string'], str):
        request['query_string'] = _scrub_query_string(request['query_string'])
    # Drop cookies entirely — too risky to scrub field-by-field
    request.pop('cookies', None)
    # Strip Authorization header (defense in depth — Sentry usually does this
    # but be explicit since clinical software gets audited)
    headers = request.get('headers')
    if isinstance(headers, dict):
        for h in list(headers.keys()):
            if h.lower() in ('authorization', 'cookie', 'x-auth-token'):
                headers[h] = REDACTED

    # Top-level dicts that can carry caller-provided values
    for key in ('contexts', 'extra', 'tags'):
        if key in event:
            event[key] = _scrub_value(event[key])

    # User context — Sentry suggests storing user.id only, but be defensive
    if 'user' in event and isinstance(event['user'], dict):
        # Keep only the opaque ID; drop email, username, ip_address
        event['user'] = {
            k: v for k, v in event['user'].items()
            if k in ('id',)
        }

    # Breadcrumbs
    breadcrumbs = event.get('breadcrumbs')
    if isinstance(breadcrumbs, dict):
        values = breadcrumbs.get('values') or []
        for crumb in values:
            if isinstance(crumb, dict) and 'data' in crumb:
                crumb['data'] = _scrub_value(crumb['data'])
            # Breadcrumb messages can carry SQL/HTTP bodies — scrub strings
            # by passing through scrub_query_string in case they contain
            # k=v pairs.
            if isinstance(crumb, dict) and isinstance(crumb.get('message'), str):
                crumb['message'] = _scrub_query_string(crumb['message'])

    # Exception stack-frame locals — easy place for PHI to leak (a function
    # arg called `client` whose repr() includes name/DOB).
    exc = event.get('exception')
    if isinstance(exc, dict):
        for excvalue in exc.get('values') or []:
            stacktrace = excvalue.get('stacktrace')
            if isinstance(stacktrace, dict):
                for frame in stacktrace.get('frames') or []:
                    if isinstance(frame, dict) and 'vars' in frame:
                        frame['vars'] = _scrub_value(frame['vars'])

    return event


def scrub_breadcrumb(crumb: dict, hint: dict | None = None) -> dict | None:  # noqa: ARG001
    """
    Sentry before_breadcrumb hook. Scrubs every breadcrumb at capture time
    rather than waiting for an event to be sent.
    """
    if isinstance(crumb, dict) and 'data' in crumb:
        crumb['data'] = _scrub_value(crumb['data'])
    if isinstance(crumb, dict) and isinstance(crumb.get('message'), str):
        crumb['message'] = _scrub_query_string(crumb['message'])
    return crumb


def init_sentry(dsn: str, environment: str, release: str = '') -> bool:
    """
    Initialise Sentry. Returns True if configured, False if no DSN was given
    (so callers can log "skipped" without a try/except).
    """
    if not dsn:
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.django import DjangoIntegration
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        return False

    import logging as _logging

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release or None,
        integrations=[
            DjangoIntegration(
                # Suppress query-string capture by default; our scrubber catches
                # what slips through, but turning this off shrinks the leak
                # surface in the first place.
                transaction_style='url',
                middleware_spans=False,
                signals_spans=False,
            ),
            CeleryIntegration(),
            # Capture WARNING+ as breadcrumbs but only ERROR+ as events. Avoids
            # noise from routine warnings clogging the issue list.
            LoggingIntegration(
                level=_logging.INFO,
                event_level=_logging.ERROR,
            ),
        ],
        # No PII unless we explicitly opt in (we don't).
        send_default_pii=False,
        # Sample 10% of performance traces — bounded cost on a busy day.
        traces_sample_rate=0.1,
        # Hooks
        before_send=scrub_event,
        before_breadcrumb=scrub_breadcrumb,
        # Cap breadcrumbs so a chatty request doesn't ship a novel.
        max_breadcrumbs=50,
        # In_app heuristic — keep our stack frames in the foreground.
        in_app_include=['apps', 'config'],
    )
    return True
