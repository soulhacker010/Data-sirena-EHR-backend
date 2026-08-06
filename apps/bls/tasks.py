"""
Background maintenance for the BLS module.

The lifecycle documented on BLSSessionStatus promises that a session left open
becomes `abandoned` after 6 hours. Nothing implemented that promise: there was
no task, and no beat entry. A session created and never explicitly ended sat in
`created` / `waiting_for_client` / `active` forever, and its short code kept
resolving — minting a fresh 4-hour invite token on every call.

The load-bearing fix lives in `apps.bls.tokens.is_session_expired`, which is
consulted on every database-aware lookup and therefore holds whether or not a
Celery worker is running. This task is the bookkeeping half: it makes the
stored `status` agree with what the resolvers already enforce, so the admin,
the client chart history, and any reporting see the truth.
"""
import logging

from celery import shared_task
from django.utils import timezone

from apps.bls.models import BLSSession, BLSSessionStatus
from apps.bls.tokens import SESSION_MAX_AGE_SECONDS

logger = logging.getLogger(__name__)

# Statuses that represent a session still considered "live" and therefore
# eligible to be swept once it ages out.
_OPEN_STATUSES = (
    BLSSessionStatus.CREATED,
    BLSSessionStatus.WAITING_FOR_CLIENT,
    BLSSessionStatus.ACTIVE,
    BLSSessionStatus.PAUSED,
)


@shared_task(name='apps.bls.tasks.abandon_stale_sessions')
def abandon_stale_sessions() -> int:
    """
    Mark sessions older than SESSION_MAX_AGE_SECONDS as abandoned.

    Idempotent by construction, which matters because Celery retries: the
    queryset excludes anything already ended or abandoned, so a second run
    over the same window matches nothing and updates nothing. It is a single
    bulk UPDATE — no per-row work, no N+1, and it stays cheap as the table
    grows because `status` and `created_at` are both indexed.

    Deliberately does NOT touch counters, `settings_snapshot`, or `ended_at`.
    An abandoned session was never properly ended, and writing an `ended_at`
    would misrepresent the clinical record by implying a clinician closed it.

    Returns the number of rows swept, for the task result log.
    """
    cutoff = timezone.now() - timezone.timedelta(seconds=SESSION_MAX_AGE_SECONDS)

    swept = BLSSession.objects.filter(
        status__in=_OPEN_STATUSES,
        created_at__lt=cutoff,
    ).update(
        status=BLSSessionStatus.ABANDONED,
        updated_at=timezone.now(),
    )

    if swept:
        # Count only — a session id here would be fine, but there is no reason
        # to put clinical identifiers in a routine maintenance log.
        logger.info('BLS sweeper marked %d stale session(s) abandoned', swept)

    return swept
