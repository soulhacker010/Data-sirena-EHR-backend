"""
BLS invite links must stop working (GAP-5).

The short code resolves to a *freshly minted* 4-hour token on every call, so
the token's own expiry bounded nothing on its own — a session nobody explicitly
ended handed out new tokens indefinitely. These tests pin the 6-hour ceiling
that closes it, and the sweeper that keeps the stored status honest.

The age check is tested separately from the sweeper on purpose: production
currently runs no Celery worker, so the read-time check is the only thing
actually holding the line and must work without the task ever running.
"""
import pytest
from django.utils import timezone

from apps.bls.models import BLSSession, BLSSessionStatus
from apps.bls.tokens import (
    SESSION_MAX_AGE_SECONDS,
    generate_session_token,
    hash_token,
    resolve_session_from_short_code,
    resolve_session_from_token,
)


def _make_session(
    org, client, therapist, *,
    age_seconds=0, status=BLSSessionStatus.CREATED, short_code='AB7K9Q',
):
    token = generate_session_token('00000000-0000-0000-0000-000000000000')
    session = BLSSession.objects.create(
        organization=org,
        client=client,
        therapist=therapist,
        token_hash=hash_token(token),
        short_code=short_code,
        status=status,
    )
    # Re-mint against the real primary key now that we have one.
    token = generate_session_token(str(session.id))
    session.token_hash = hash_token(token)
    session.save(update_fields=['token_hash'])

    if age_seconds:
        # auto_now_add blocks assignment on create, so backdate with an UPDATE.
        BLSSession.objects.filter(pk=session.pk).update(
            created_at=timezone.now() - timezone.timedelta(seconds=age_seconds)
        )
        session.refresh_from_db()
    return session, token


@pytest.mark.django_db
class TestShortCodeExpiry:

    def test_fresh_session_resolves(self, org, sample_client, clinician_user):
        _make_session(org, sample_client, clinician_user)
        assert resolve_session_from_short_code('AB7K9Q') is not None

    def test_session_past_ceiling_does_not_resolve(self, org, sample_client, clinician_user):
        _make_session(
            org, sample_client, clinician_user,
            age_seconds=SESSION_MAX_AGE_SECONDS + 60,
        )
        assert resolve_session_from_short_code('AB7K9Q') is None, (
            'an aged-out short code must stop minting fresh tokens'
        )

    def test_session_just_inside_ceiling_still_resolves(self, org, sample_client, clinician_user):
        """A long-but-plausible clinical session must not be cut off."""
        _make_session(
            org, sample_client, clinician_user,
            age_seconds=SESSION_MAX_AGE_SECONDS - 600,
        )
        assert resolve_session_from_short_code('AB7K9Q') is not None


@pytest.mark.django_db
class TestTokenExpiry:

    def test_token_rejected_once_session_ages_out(self, org, sample_client, clinician_user):
        _, token = _make_session(
            org, sample_client, clinician_user,
            age_seconds=SESSION_MAX_AGE_SECONDS + 60,
        )
        assert resolve_session_from_token(token) is None


@pytest.mark.django_db
class TestAbandonStaleSessionsTask:

    def test_sweeps_only_aged_open_sessions(self, org, sample_client, clinician_user):
        from apps.bls.tasks import abandon_stale_sessions

        fresh, _ = _make_session(org, sample_client, clinician_user)
        stale, _ = _make_session(
            org, sample_client, clinician_user,
            age_seconds=SESSION_MAX_AGE_SECONDS + 60,
            short_code='ZZ9Y8X',
        )

        swept = abandon_stale_sessions()

        fresh.refresh_from_db()
        stale.refresh_from_db()
        assert swept == 1
        assert stale.status == BLSSessionStatus.ABANDONED
        assert fresh.status == BLSSessionStatus.CREATED

    def test_is_idempotent(self, org, sample_client, clinician_user):
        """Celery retries. A second run must be a no-op, not a double-write."""
        from apps.bls.tasks import abandon_stale_sessions

        _make_session(
            org, sample_client, clinician_user,
            age_seconds=SESSION_MAX_AGE_SECONDS + 60,
        )
        assert abandon_stale_sessions() == 1
        assert abandon_stale_sessions() == 0

    def test_does_not_fabricate_ended_at(self, org, sample_client, clinician_user):
        """
        An abandoned session was never closed by a clinician. Writing ended_at
        would put a false 'someone ended this' marker in the clinical record.
        """
        from apps.bls.tasks import abandon_stale_sessions

        stale, _ = _make_session(
            org, sample_client, clinician_user,
            age_seconds=SESSION_MAX_AGE_SECONDS + 60,
        )
        abandon_stale_sessions()

        stale.refresh_from_db()
        assert stale.status == BLSSessionStatus.ABANDONED
        assert stale.ended_at is None
