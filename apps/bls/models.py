"""
Models for the Bilateral Stimulation (BLS) module.

Schema mirrors BLS-SYSTEM-DESIGN.md §4. Three tables:
 - BLSSession            — one row per clinical BLS session (soft-deleted)
 - BLSClientPreference   — per-client saved settings (1:1 with Client)
 - BLSOrgDefaults        — per-organization default settings

All clinical records (BLSSession) follow the soft-delete pattern required by
CLAUDE.md so they remain auditable for the legal retention period.
"""
from __future__ import annotations

import uuid

from django.db import models
from django.db.models import CheckConstraint, Q

from apps.core.models import BaseModel, OrganizationModel


# ─── Status / enum choices ─────────────────────────────────────────────────────

class BLSSessionStatus(models.TextChoices):
    """
    Lifecycle states for a BLS session. State machine transitions:

        created → waiting_for_client → active ⇄ paused → ended
                                                  ↓
                                          abandoned (6h timeout)
                                          ended (KILL switch)
    """
    CREATED            = 'created',            'Created'
    WAITING_FOR_CLIENT = 'waiting_for_client', 'Waiting for client'
    ACTIVE             = 'active',             'Active'
    PAUSED             = 'paused',             'Paused'
    ENDED              = 'ended',              'Ended'
    ABANDONED          = 'abandoned',          'Abandoned'


class BLSModality(models.TextChoices):
    VISUAL_ONLY = 'visual_only', 'Visual only'
    AUDIO_ONLY  = 'audio_only',  'Audio only'
    BOTH        = 'both',        'Visual + audio'


# ─── BLSSession ────────────────────────────────────────────────────────────────

class BLSSessionQuerySet(models.QuerySet):
    """Custom queryset that excludes soft-deleted rows by default."""

    def active(self):
        return self.filter(is_deleted=False)

    def deleted(self):
        return self.filter(is_deleted=True)


class BLSSessionManager(models.Manager):
    """Default manager — hides soft-deleted rows. Use `all_with_deleted()` for audit."""

    def get_queryset(self):
        return BLSSessionQuerySet(self.model, using=self._db).active()

    def all_with_deleted(self):
        return BLSSessionQuerySet(self.model, using=self._db)


class BLSSession(OrganizationModel):
    """
    One clinical Bilateral Stimulation session.

    Lifecycle:
      1. Therapist POSTs /api/v1/bls/sessions/ → row created (status=created),
         signed token generated, token_hash stored.
      2. Therapist shares the URL with the client (in-office or telehealth).
      3. Client opens the URL → backend verifies token → status transitions
         to waiting_for_client, token_claimed_at set.
      4. Therapist hits START → status=active, started_at set.
      5. Pauses / resumes flip between active and paused.
      6. End or 90-minute hard timeout → status=ended, ended_at set,
         counters frozen, settings_snapshot captured.
      7. Soft-delete only — retain for legal retention period.
    """
    appointment = models.ForeignKey(
        'scheduling.Appointment',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='bls_sessions',
        help_text='Optional — ad-hoc sessions launched outside the calendar leave this null.',
    )
    client = models.ForeignKey(
        'clients.Client',
        on_delete=models.PROTECT,
        related_name='bls_sessions',
    )
    therapist = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='bls_sessions_run',
    )

    # ── Token (see apps.bls.tokens) ───────────────────────────────────────────
    # The full signed token NEVER touches the database. We store SHA-256 only
    # so a compromised DB dump can't be used to forge live URLs.
    token_hash = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text='SHA-256 hex digest of the signed token. The token itself is never stored.',
    )
    token_claimed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Set when the client first opens the invite link. Used to enforce one-time claim.',
    )

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    status = models.CharField(
        max_length=20,
        choices=BLSSessionStatus.choices,
        default=BLSSessionStatus.CREATED,
        db_index=True,
    )
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    # ── Counters (server-authoritative once consumers wire this up) ───────────
    pass_count = models.PositiveIntegerField(default=0)
    set_count = models.PositiveIntegerField(default=0)
    duration_seconds = models.PositiveIntegerField(default=0)

    # ── Settings snapshot at end of session ───────────────────────────────────
    # Full BLSConfig as it was when the session ended. Used to render history.
    # Persisted as JSON so adding new BLSConfig fields doesn't need a migration.
    settings_snapshot = models.JSONField(default=dict, blank=True)
    modality = models.CharField(
        max_length=16,
        choices=BLSModality.choices,
        default=BLSModality.BOTH,
    )

    # ── Soft-delete (clinical retention) ──────────────────────────────────────
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = BLSSessionManager()

    class Meta(OrganizationModel.Meta):
        verbose_name = 'BLS Session'
        verbose_name_plural = 'BLS Sessions'
        indexes = [
            models.Index(fields=['client', '-created_at'], name='bls_session_client_history'),
            models.Index(fields=['therapist', '-created_at'], name='bls_session_therapist_act'),
        ]
        constraints = [
            CheckConstraint(
                check=Q(status='ended') | Q(ended_at__isnull=True),
                name='bls_session_ended_at_only_when_ended',
            ),
            CheckConstraint(
                check=Q(started_at__isnull=True) | Q(started_at__gte=models.F('created_at')),
                name='bls_session_started_after_created',
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover — admin shorthand
        return f'BLSSession({self.id}, {self.status})'

    def soft_delete(self) -> None:
        """Mark the row deleted without removing it from the database."""
        from django.utils import timezone
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_deleted', 'deleted_at', 'updated_at'])


# ─── BLSClientPreference ───────────────────────────────────────────────────────

class BLSClientPreference(BaseModel):
    """
    Per-client BLS settings — re-loaded automatically when the clinician starts
    a new session for the same client. One row per client; updated in place.

    Stored as a single JSON `config` field because the BLSConfig shape evolves
    independently of the database schema (see types/bls.ts on the frontend).
    """
    client = models.OneToOneField(
        'clients.Client',
        on_delete=models.CASCADE,
        related_name='bls_preference',
    )
    config = models.JSONField(default=dict, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name = 'BLS Client Preference'
        verbose_name_plural = 'BLS Client Preferences'

    def __str__(self) -> str:  # pragma: no cover
        return f'BLSClientPreference(client={self.client_id})'


# ─── BLSOrgDefaults ────────────────────────────────────────────────────────────

class BLSOrgDefaults(BaseModel):
    """
    Practice-wide BLS defaults — used to seed the panel when a clinician opens
    a new session and no per-client preference exists.

    OneToOne with Organization so each practice has exactly one row. Created
    lazily on first GET, mutable via PUT.
    """
    organization = models.OneToOneField(
        'accounts.Organization',
        on_delete=models.CASCADE,
        related_name='bls_defaults',
    )
    config = models.JSONField(default=dict, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name = 'BLS Org Defaults'
        verbose_name_plural = 'BLS Org Defaults'

    def __str__(self) -> str:  # pragma: no cover
        return f'BLSOrgDefaults(org={self.organization_id})'


# ─── Module-level helper (used by audit hooks) ────────────────────────────────

def get_or_create_org_defaults(organization) -> BLSOrgDefaults:
    """Return the org's defaults row, creating it with empty config if missing."""
    obj, _ = BLSOrgDefaults.objects.get_or_create(organization=organization)
    return obj
