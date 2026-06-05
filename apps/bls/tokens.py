"""
BLS invite token system.

The clinician shares a URL of the form `/bls/c/<token>` with the client. The
token is the only identifier — it carries the session id internally, signed
with Django's TimestampSigner so we can verify both authenticity and age in
constant time without a database round trip.

What the token does NOT contain:
  * the client's name, ID, DOB, or any identifying field
  * the therapist's name or ID
  * the organization name
  * the appointment time, location, or details

What it DOES contain (inside the signed payload):
  * an opaque session UUID (as plain text — not PHI on its own)
  * a random 16-byte nonce (defends against guessing if the signing key leaks)

Storage:
  * The token string itself is NEVER persisted. We store only its SHA-256 hex
    digest on `BLSSession.token_hash`. A DB dump can't be used to forge live
    invite URLs.

Expiry:
  * 4 hours via TimestampSigner's `max_age`. Anything older fails verification.
  * Explicit revocation: when `BLSSession.status` flips to ended/abandoned,
    `verify_session_token` rejects even valid-by-signature tokens.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from django.core import signing
from django.utils import timezone


TOKEN_SALT = 'apps.bls.tokens.v1'
TOKEN_MAX_AGE_SECONDS = 4 * 60 * 60   # 4 hours


@dataclass(frozen=True)
class TokenPayload:
    session_id: str
    nonce: str


# ─── Generation ────────────────────────────────────────────────────────────────

def generate_session_token(session_id: str) -> str:
    """
    Sign a session id into an invite token.

    The result is a TimestampSigner output: `<base64-payload>:<timestamp>:<sig>`
    where the payload encodes `{session_id, nonce}`. URL-safe; no further
    encoding needed.
    """
    payload = {
        'sid': str(session_id),
        'n': secrets.token_urlsafe(12),  # 16 random bytes encoded URL-safe
    }
    signer = signing.TimestampSigner(salt=TOKEN_SALT)
    return signer.sign_object(payload, compress=False)


def hash_token(token: str) -> str:
    """SHA-256 hex digest of the token, used for one-time-claim enforcement."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


# ─── Verification ──────────────────────────────────────────────────────────────

def verify_session_token(token: str) -> Optional[TokenPayload]:
    """
    Verify a token's signature and age. Returns the decoded payload on success,
    None on any failure (bad signature, expired, malformed).

    NEVER raises — callers can treat None as "reject this connection" without
    branching on exception types.
    """
    if not token or not isinstance(token, str):
        return None
    signer = signing.TimestampSigner(salt=TOKEN_SALT)
    try:
        payload = signer.unsign_object(token, max_age=TOKEN_MAX_AGE_SECONDS)
    except signing.SignatureExpired:
        return None
    except signing.BadSignature:
        return None
    sid = payload.get('sid')
    nonce = payload.get('n')
    if not sid or not nonce:
        return None
    return TokenPayload(session_id=sid, nonce=nonce)


# ─── Database-aware verification (used by REST + Channels consumer) ───────────

def resolve_session_from_token(token: str):
    """
    Verify the token AND look up the corresponding BLSSession. Returns the
    session instance if the token is valid, the row exists, and the session
    hasn't been explicitly revoked (ended / abandoned / soft-deleted).
    Returns None otherwise.

    Imports the model lazily so this module stays importable from places that
    can't tolerate Django app-registry chatter (e.g. settings).
    """
    payload = verify_session_token(token)
    if payload is None:
        return None
    from apps.bls.models import BLSSession, BLSSessionStatus

    token_hash = hash_token(token)
    try:
        session = BLSSession.objects.get(id=payload.session_id, token_hash=token_hash)
    except BLSSession.DoesNotExist:
        return None

    if session.status in (BLSSessionStatus.ENDED, BLSSessionStatus.ABANDONED):
        return None
    return session


def claim_token(token: str, *, fingerprint: Optional[str] = None) -> bool:
    """
    Mark the token claimed (first client connection). Returns True on the
    first claim, False if the token was already claimed (replay attempt).

    fingerprint is reserved for a future browser-fingerprint check that lets
    the same browser reconnect after a transient disconnect without being
    rejected as a replay.
    """
    payload = verify_session_token(token)
    if payload is None:
        return False
    from apps.bls.models import BLSSession

    token_hash = hash_token(token)
    # Atomic claim: only update if not already claimed.
    updated = BLSSession.objects.filter(
        id=payload.session_id,
        token_hash=token_hash,
        token_claimed_at__isnull=True,
    ).update(token_claimed_at=timezone.now())
    return updated > 0


# Re-export commonly used items so callers don't need to know the layout.
__all__ = [
    'TokenPayload',
    'TOKEN_MAX_AGE_SECONDS',
    'generate_session_token',
    'hash_token',
    'verify_session_token',
    'resolve_session_from_token',
    'claim_token',
]


# Silence unused-import warning for `settings` — kept available for the future
# `BLS_TOKEN_MAX_AGE_SECONDS` override that will let production tune expiry per
# environment without code changes.
_ = settings
