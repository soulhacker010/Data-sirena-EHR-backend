"""
Audit hooks for BLS lifecycle events.

The REST viewsets get automatic audit coverage from
apps.core.audit_mixins.PHIAccessAuditMixin via AuditMiddleware. But the
Channels consumers operate outside the HTTP request cycle, so we log
lifecycle events explicitly here.

HIPAA rules (per CLAUDE.md):
  * Audit-log every PHI access and every PHI mutation. ← session start/end
    qualify (they're clinical actions on the patient record).
  * NEVER log PHI in the changes JSON. Use record_id and non-identifying
    metadata only.

What we audit:
  bls.session.start       — clinician hit START
  bls.session.end         — clinician hit END, or system ended via timeout
  bls.session.kill        — Kill Switch fired (network drop, etc.)
  bls.client.connected    — client opened the invite link
  bls.client.disconnected — client tab closed / network dropped

What we deliberately DO NOT audit:
  * Every config change (sound switch, color tweak) — noise; would flood
    the audit log without forensic value.
  * Per-frame pass counts — same reason.
  * Client visibility events (tab hidden / shown) — not a PHI access.
"""
from __future__ import annotations

from typing import Optional

from channels.db import database_sync_to_async


# ─── Public async helpers (called from consumers) ─────────────────────────────

@database_sync_to_async
def log_bls_event_sync(
    *,
    organization_id,
    user_id,
    action: str,
    session_id,
    changes: Optional[dict] = None,
) -> None:
    """
    Write one audit row. Imports happen lazily so this module is safe to
    import from anywhere (including settings) without triggering the app
    registry early.
    """
    from apps.audit.models import AuditLog
    payload = dict(changes or {})
    # Defensive — strip anything that *might* be PHI even if a caller fat-
    # fingered the dict. Allow-list known safe keys.
    safe_keys = {'reason', 'modality', 'pass_count', 'set_count', 'duration_seconds'}
    payload = {k: v for k, v in payload.items() if k in safe_keys}
    AuditLog.objects.create(
        organization_id=organization_id,
        user_id=user_id,
        action=action,
        table_name='bls_sessions',
        record_id=session_id,
        changes=payload,
    )


async def log_session_start(*, session, user):
    await log_bls_event_sync(
        organization_id=session.organization_id,
        user_id=getattr(user, 'id', None),
        action='bls.session.start',
        session_id=session.id,
    )


async def log_session_end(*, session, user, reason: str = 'manual'):
    await log_bls_event_sync(
        organization_id=session.organization_id,
        user_id=getattr(user, 'id', None) if user is not None else None,
        action='bls.session.end',
        session_id=session.id,
        changes={
            'reason': reason,
            'pass_count': session.pass_count,
            'set_count': session.set_count,
            'duration_seconds': session.duration_seconds,
        },
    )


async def log_client_connected(*, session):
    await log_bls_event_sync(
        organization_id=session.organization_id,
        user_id=None,  # client view is unauthenticated
        action='bls.client.connected',
        session_id=session.id,
    )


async def log_client_disconnected(*, session, reason: str = 'transport_close'):
    await log_bls_event_sync(
        organization_id=session.organization_id,
        user_id=None,
        action='bls.client.disconnected',
        session_id=session.id,
        changes={'reason': reason},
    )
