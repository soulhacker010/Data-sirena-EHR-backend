"""
Unit tests for the BLS invite-token system. Targets the boundaries that bite
in production: signature tampering, expiry, malformed input, hash determinism.
"""
import time
import uuid
from unittest.mock import patch

import pytest

from apps.bls.tokens import (
    TOKEN_MAX_AGE_SECONDS,
    generate_session_token,
    hash_token,
    verify_session_token,
)


SAMPLE_SID = '11111111-1111-4111-8111-111111111111'


class TestGenerateAndVerify:
    def test_round_trip(self):
        token = generate_session_token(SAMPLE_SID)
        payload = verify_session_token(token)
        assert payload is not None
        assert payload.session_id == SAMPLE_SID

    def test_nonce_is_random(self):
        token1 = generate_session_token(SAMPLE_SID)
        token2 = generate_session_token(SAMPLE_SID)
        # Same session id but different tokens — nonce should differ
        assert token1 != token2
        # Both should verify back to the same session id
        assert verify_session_token(token1).session_id == SAMPLE_SID
        assert verify_session_token(token2).session_id == SAMPLE_SID

    def test_session_id_uuid_accepted(self):
        sid = str(uuid.uuid4())
        token = generate_session_token(sid)
        payload = verify_session_token(token)
        assert payload.session_id == sid


class TestRejection:
    def test_empty_token_rejected(self):
        assert verify_session_token('') is None
        assert verify_session_token(None) is None

    def test_non_string_token_rejected(self):
        assert verify_session_token(123) is None  # type: ignore

    def test_malformed_token_rejected(self):
        assert verify_session_function_safe('totally-fake') is None
        assert verify_session_function_safe('no-colons') is None

    def test_tampered_token_rejected(self):
        token = generate_session_token(SAMPLE_SID)
        # Flip a character in the signed payload — should fail verification
        tampered = token[:-1] + ('A' if token[-1] != 'A' else 'B')
        assert verify_session_token(tampered) is None

    def test_expired_token_rejected(self):
        token = generate_session_token(SAMPLE_SID)
        # Patch time.time() (used by Django's signer) to be far in the future
        future = time.time() + TOKEN_MAX_AGE_SECONDS + 60
        with patch('django.core.signing.time.time', return_value=future):
            assert verify_session_token(token) is None


class TestTokenHash:
    def test_hash_is_sha256_hex(self):
        h = hash_token('test-token')
        # SHA-256 hex = 64 chars
        assert len(h) == 64
        assert all(c in '0123456789abcdef' for c in h)

    def test_hash_is_deterministic(self):
        token = 'consistent-token'
        assert hash_token(token) == hash_token(token)

    def test_different_tokens_different_hashes(self):
        assert hash_token('token-a') != hash_token('token-b')


# ─── Helpers ───────────────────────────────────────────────────────────────────

def verify_session_function_safe(arg):
    """Wrapper so the test name reads naturally."""
    return verify_session_token(arg)
