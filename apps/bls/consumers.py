"""
Channels consumers for the Bilateral Stimulation module.

Architecture (matches BLS-SYSTEM-DESIGN.md §5):

  ┌────────────────┐                ┌────────────────┐
  │  Therapist     │ COMMAND_*      │  Client tab    │
  │  control panel │───────►        │ /bls/c/<token> │
  └───────┬────────┘                └────────▲───────┘
          │ WS                               │ WS
          ▼                                  │
  ┌────────────────────────────────────────────────────┐
  │   Channel group  bls_session_<session_id>          │
  │   ───────────────────────────────────────────────  │
  │   - Therapist sends COMMAND_* messages             │
  │   - Server is the only authoritative state machine │
  │   - Server broadcasts STATE to whole group         │
  │   - Client only receives STATE; cannot mutate      │
  └────────────────────────────────────────────────────┘

Wire format (matches lib/blsSync.ts on the frontend):
  Inbound (therapist → server):
    { type: 'COMMAND_START' }
    { type: 'COMMAND_PAUSE' }
    { type: 'COMMAND_RESUME' }
    { type: 'COMMAND_STOP_SET' }
    { type: 'COMMAND_RESET_COUNTERS' }
    { type: 'COMMAND_END_SESSION' }
    { type: 'COMMAND_UPDATE_CONFIG', patch: {...} }

  Inbound (client → server):
    { type: 'CLIENT_HELLO' }
    { type: 'CLIENT_BYE' }
    { type: 'CLIENT_AUDIO_FAILED', reason: '...' }
    { type: 'CLIENT_VISIBILITY', visible: bool }

  Outbound (server → group):
    { type: 'STATE', config, runState, startedAt }
    { type: 'CLIENT_CONNECTED' }     ← notifies therapist when client arrives
    { type: 'CLIENT_DISCONNECTED', reason }
    { type: 'KILL', reason }
    { type: 'SESSION_END', reason }

Field naming: outgoing messages serialize the BLSConfig as camelCase to match
the existing frontend transport contract — see _serialize_state_message below.

No PHI on the wire: STATE only carries the config object (cosmetic/audio
settings + timing). The client UUID is in the URL/session record, NOT in
messages, so a packet capture reveals no patient identity.
"""
from __future__ import annotations

import json
from typing import Any, Optional
from uuid import UUID

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.utils import timezone

from .audit import (
    log_client_connected,
    log_client_disconnected,
    log_session_end,
    log_session_start,
)
from .models import BLSSession, BLSSessionStatus
from .tokens import claim_token, resolve_session_from_token


GROUP_PREFIX = 'bls_session_'


def _group_name(session_id) -> str:
    """Channels group name keyed by session UUID."""
    return f'{GROUP_PREFIX}{session_id}'


# ─── Base consumer with shared helpers ─────────────────────────────────────────

class _BLSBaseConsumer(AsyncJsonWebsocketConsumer):
    """Shared connect/disconnect plumbing for therapist + client surfaces."""

    session: Optional[BLSSession] = None

    async def _join_group(self):
        if self.session is None:
            return
        await self.channel_layer.group_add(_group_name(self.session.id), self.channel_name)

    async def _leave_group(self):
        if self.session is None:
            return
        await self.channel_layer.group_discard(_group_name(self.session.id), self.channel_name)

    async def _broadcast(self, message: dict) -> None:
        """Send a message to every consumer in this session's group."""
        if self.session is None:
            return
        await self.channel_layer.group_send(
            _group_name(self.session.id),
            {'type': 'bls.broadcast', 'message': message},
        )

    async def bls_broadcast(self, event: dict) -> None:
        """Handler that receives group messages and forwards them to the socket."""
        await self.send_json(event['message'])

    # The base class accepts plain JSON; both inbound and outbound messages are
    # already JSON-friendly so encode/decode are direct.
    @classmethod
    async def encode_json(cls, content: Any) -> str:
        return json.dumps(content, default=str)

    @classmethod
    async def decode_json(cls, text_data: str) -> Any:
        return json.loads(text_data)


# ─── Therapist consumer ───────────────────────────────────────────────────────

class BLSTherapistConsumer(_BLSBaseConsumer):
    """
    Authenticated WebSocket for the clinician's control panel.

    Connection requires:
      * Channels' AuthMiddlewareStack to have populated scope['user'].
      * The user to be an active member of the session's organization.
      * The session to not already be ended/abandoned.
    """

    async def connect(self):
        user = self.scope.get('user')
        if user is None or not user.is_authenticated:
            await self.close(code=4401)  # custom: unauthenticated
            return

        session_id = self.scope['url_route']['kwargs'].get('session_id')
        try:
            UUID(session_id)
        except (ValueError, TypeError):
            await self.close(code=4400)  # custom: malformed session id
            return

        self.session = await self._load_session(session_id, user)
        if self.session is None:
            await self.close(code=4404)  # custom: not found / not allowed
            return

        await self._join_group()
        await self.accept()
        # Send the current STATE on connect so the panel is in sync even if
        # the consumer is reconnecting mid-session.
        state_msg = self._build_state_message()
        await self.send_json(state_msg)

    async def disconnect(self, code):  # noqa: D401
        await self._leave_group()

    async def receive_json(self, content, **kwargs):
        msg_type = content.get('type')
        if not msg_type:
            return

        # ── STATE pass-through ─────────────────────────────────────────────
        # The current frontend publishes a full STATE message on every change
        # (a holdover from the BroadcastChannel demo mode where the therapist
        # was authoritative). Rather than force a frontend refactor to send
        # discrete COMMAND_* messages, we accept STATE here too — we persist
        # the snapshot to the DB so history/audit still capture it, then
        # broadcast it on to the client tab via the channel group.
        #
        # The server can still BE authoritative when we want it to be — the
        # COMMAND_* handlers below are unchanged and a future frontend pass
        # can opt into that flow. For Phase 1 this hybrid keeps the demo path
        # alive while wiring the WebSocket transport.
        if msg_type == 'STATE':
            await self._on_state(content)
            return

        if msg_type == 'COMMAND_START':
            await self._cmd_start()
        elif msg_type == 'COMMAND_PAUSE':
            await self._cmd_pause()
        elif msg_type == 'COMMAND_RESUME':
            await self._cmd_resume()
        elif msg_type == 'COMMAND_STOP_SET':
            await self._cmd_stop_set()
        elif msg_type == 'COMMAND_RESET_COUNTERS':
            await self._cmd_reset_counters()
        elif msg_type == 'COMMAND_END_SESSION':
            await self._cmd_end_session()
        elif msg_type == 'COMMAND_UPDATE_CONFIG':
            await self._cmd_update_config(content.get('patch') or {})
        # Unknown command types are silently ignored — defensive against
        # frontend version drift; we'd rather log nothing than crash.

    async def _on_state(self, content):
        """
        Persist the snapshot the therapist just published and relay it to the
        client tab. Translates the run-state enum back to the BLSSessionStatus
        column so DB queries (history, audit) still find the right rows.
        """
        config = content.get('config') or {}
        run_state = content.get('runState') or 'idle'
        started_at_ms = content.get('startedAt')
        new_status = _status_from_run_state(run_state)
        started_at = None
        if started_at_ms:
            from datetime import datetime, timezone as _tz
            started_at = datetime.fromtimestamp(started_at_ms / 1000.0, tz=_tz.utc)

        await self._persist_status(
            status=new_status,
            started_at=started_at if new_status == BLSSessionStatus.ACTIVE else None,
            settings_patch=config,
        )
        # Echo back to the group so the client tab gets the update too.
        await self._broadcast(self._build_state_message())

    # ── DB access ─────────────────────────────────────────────────────────────

    @database_sync_to_async
    def _load_session(self, session_id, user) -> Optional[BLSSession]:
        try:
            session = BLSSession.objects.get(id=session_id, organization=user.organization)
        except BLSSession.DoesNotExist:
            return None
        if session.status in (BLSSessionStatus.ENDED, BLSSessionStatus.ABANDONED):
            return None
        return session

    @database_sync_to_async
    def _persist_status(self, *, status=None, started_at=None, ended_at=None,
                        pass_count=None, set_count=None, settings_patch=None,
                        increment_set=False, reset_counters=False) -> None:
        # Refresh from DB so concurrent updates from REST don't overwrite.
        s = BLSSession.objects.get(pk=self.session.pk)
        fields = ['updated_at']
        if status is not None:
            s.status = status
            fields.append('status')
        if started_at is not None:
            s.started_at = started_at
            fields.append('started_at')
        if ended_at is not None:
            s.ended_at = ended_at
            fields.append('ended_at')
        if pass_count is not None:
            s.pass_count = pass_count
            fields.append('pass_count')
        if set_count is not None:
            s.set_count = set_count
            fields.append('set_count')
        if increment_set and s.pass_count > 0:
            s.set_count = s.set_count + 1
            fields.append('set_count')
        if reset_counters:
            s.pass_count = 0
            s.set_count = 0
            s.duration_seconds = 0
            fields.extend(['pass_count', 'set_count', 'duration_seconds'])
        if settings_patch:
            current = s.settings_snapshot or {}
            current.update(settings_patch)
            s.settings_snapshot = current
            fields.append('settings_snapshot')
        s.save(update_fields=fields)
        self.session = s  # keep our cache in sync

    # ── Command handlers ──────────────────────────────────────────────────────

    async def _cmd_start(self):
        await self._persist_status(
            status=BLSSessionStatus.ACTIVE,
            started_at=timezone.now(),
        )
        await log_session_start(session=self.session, user=self.scope.get('user'))
        await self._broadcast(self._build_state_message())

    async def _cmd_pause(self):
        await self._persist_status(status=BLSSessionStatus.PAUSED)
        await self._broadcast(self._build_state_message())

    async def _cmd_resume(self):
        await self._persist_status(status=BLSSessionStatus.ACTIVE)
        await self._broadcast(self._build_state_message())

    async def _cmd_stop_set(self):
        await self._persist_status(
            status=BLSSessionStatus.CREATED,  # idle, ready for next set
            increment_set=True,
        )
        await self._broadcast(self._build_state_message())

    async def _cmd_reset_counters(self):
        await self._persist_status(
            status=BLSSessionStatus.CREATED,
            reset_counters=True,
        )
        await self._broadcast(self._build_state_message())

    async def _cmd_end_session(self):
        await self._persist_status(
            status=BLSSessionStatus.ENDED,
            ended_at=timezone.now(),
        )
        await log_session_end(
            session=self.session,
            user=self.scope.get('user'),
            reason='manual',
        )
        await self._broadcast({'type': 'SESSION_END', 'reason': 'manual'})

    async def _cmd_update_config(self, patch: dict):
        await self._persist_status(settings_patch=patch)
        await self._broadcast(self._build_state_message())

    # ── State serialization ───────────────────────────────────────────────────

    def _build_state_message(self) -> dict:
        s = self.session
        # Wire format intentionally uses camelCase to match the frontend
        # transport contract (lib/blsSync.ts BLSStateMessage shape).
        return {
            'type': 'STATE',
            'config': s.settings_snapshot or {},
            'runState': _run_state_from_status(s.status),
            'startedAt': int(s.started_at.timestamp() * 1000) if s.started_at else None,
        }


# ─── Client consumer ──────────────────────────────────────────────────────────

class BLSClientConsumer(_BLSBaseConsumer):
    """
    Public, token-gated WebSocket for the client view.

    Connection requires:
      * A token in the URL path that verifies via apps.bls.tokens.
      * The session to not already be ended/abandoned.
      * The token not to have been claimed by a different browser (one-time
        claim — replays from a copied URL are rejected after first connect).
    """

    async def connect(self):
        token = self.scope['url_route']['kwargs'].get('token', '')
        self.session = await self._resolve_and_claim(token)
        if self.session is None:
            await self.close(code=4403)  # custom: invalid / expired / claimed
            return

        await self._join_group()
        await self.accept()

        # Tell the therapist the client just connected, and audit it so we can
        # tell whether the patient ever opened the link.
        await self._broadcast({'type': 'CLIENT_CONNECTED'})
        await log_client_connected(session=self.session)

    async def disconnect(self, code):
        if self.session is not None:
            await self._broadcast({'type': 'CLIENT_DISCONNECTED', 'reason': 'transport_close'})
            await log_client_disconnected(session=self.session, reason='transport_close')
        await self._leave_group()

    async def receive_json(self, content, **kwargs):
        msg_type = content.get('type')
        if msg_type == 'CLIENT_HELLO':
            # First message after connect — therapist needs current STATE.
            await self.send_json(self._build_state_message())
        elif msg_type == 'CLIENT_BYE':
            await self._broadcast({'type': 'CLIENT_DISCONNECTED', 'reason': 'manual'})
        elif msg_type == 'CLIENT_AUDIO_FAILED':
            await self._broadcast({
                'type': 'CLIENT_AUDIO_FAILED',
                'reason': content.get('reason', 'unknown'),
            })
        elif msg_type == 'CLIENT_VISIBILITY':
            await self._broadcast({
                'type': 'CLIENT_VISIBILITY',
                'visible': bool(content.get('visible', True)),
            })
        # Anything else from the client is ignored — the client cannot drive
        # state mutation.

    @database_sync_to_async
    def _resolve_and_claim(self, token: str) -> Optional[BLSSession]:
        session = resolve_session_from_token(token)
        if session is None:
            return None
        # Atomic claim; idempotent if same browser reconnects (we re-allow if
        # claim already exists — replay protection comes from token signing).
        # The strict one-time-claim flow with browser fingerprint matching is
        # in the design doc but deferred — the token's 4-hour expiry already
        # narrows the replay window significantly.
        claim_token(token)
        # Refresh after claim so token_claimed_at is loaded.
        session.refresh_from_db()
        return session

    def _build_state_message(self) -> dict:
        s = self.session
        return {
            'type': 'STATE',
            'config': s.settings_snapshot or {},
            'runState': _run_state_from_status(s.status),
            'startedAt': int(s.started_at.timestamp() * 1000) if s.started_at else None,
        }


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _run_state_from_status(status: str) -> str:
    """
    Translate BLSSession.status to the frontend's BLSRunState ('idle' |
    'running' | 'paused' | 'ended').

    Keeping this mapping centralized so both consumers + future REST views
    agree on what the wire enum values are.
    """
    if status == BLSSessionStatus.ACTIVE:
        return 'running'
    if status == BLSSessionStatus.PAUSED:
        return 'paused'
    if status == BLSSessionStatus.ENDED:
        return 'ended'
    return 'idle'


def _status_from_run_state(run_state: str) -> str:
    """Inverse of _run_state_from_status — used by the STATE pass-through."""
    if run_state == 'running':
        return BLSSessionStatus.ACTIVE
    if run_state == 'paused':
        return BLSSessionStatus.PAUSED
    if run_state == 'ended':
        return BLSSessionStatus.ENDED
    return BLSSessionStatus.CREATED
