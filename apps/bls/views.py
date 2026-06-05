"""
REST views for the BLS module.

Endpoints (all under /api/v1/bls/):
  POST   /sessions/                       — create a session, return token + URL
  GET    /sessions/verify/?token=         — public, validates token
  POST   /sessions/{id}/end/              — end + persist counters
  GET    /clients/{client_id}/history/    — BLS history for a client
  GET/PUT /preferences/{client_id}/       — per-client preferences
  GET/PUT /defaults/                      — org-wide defaults

All clinician-side endpoints require authentication + the IsClinicalStaff
permission. The `verify` endpoint is the only public surface.
"""
from __future__ import annotations

from django.http import Http404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet

from apps.core.audit_mixins import PHIAccessAuditMixin
from apps.clients.models import Client
from apps.core.permissions import IsClinicalStaff
from apps.scheduling.models import Appointment

from .models import (
    BLSClientPreference,
    BLSOrgDefaults,
    BLSSession,
    BLSSessionStatus,
)
from .session_note import append_bls_auto_log_to_session_note
from .serializers import (
    BLSClientPreferenceSerializer,
    BLSOrgDefaultsSerializer,
    BLSSessionCreateSerializer,
    BLSSessionDetailSerializer,
    BLSSessionEndSerializer,
    BLSSessionInviteResponseSerializer,
    BLSSessionVerifyResponseSerializer,
)
from .tokens import (
    TOKEN_MAX_AGE_SECONDS,
    assign_short_code,
    generate_session_token,
    hash_token,
    resolve_session_from_short_code,
    resolve_session_from_token,
)


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _frontend_base(request) -> str:
    from django.conf import settings
    base = getattr(settings, 'FRONTEND_BASE_URL', '')
    if not base:
        scheme = 'https' if request.is_secure() else 'http'
        base = f'{scheme}://{request.get_host()}'
    return base.rstrip('/')


def _build_invite_url(request, code_or_token: str) -> str:
    """
    Compose the public client URL. Prefer the short code (6 chars) — the
    frontend resolves it back to the signed token before opening the WS.
    Falls back to the signed token when no code is provided.
    """
    return f'{_frontend_base(request)}/bls/c/{code_or_token}'


# ─── Session ViewSet (therapist) ──────────────────────────────────────────────

class BLSSessionViewSet(PHIAccessAuditMixin, ViewSet):
    """
    Clinician-facing actions on BLS sessions.

    Tagged with PHIAccessAuditMixin so reads/writes are recorded in the audit
    log under the bls_sessions table (record_id only — never PHI payload).
    """
    permission_classes = [IsAuthenticated, IsClinicalStaff]
    audit_table_name = 'bls_sessions'

    # POST /sessions/
    def create(self, request):
        serializer = BLSSessionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        client_id = serializer.validated_data['client_id']
        appointment_id = serializer.validated_data.get('appointment_id')

        try:
            client = Client.objects.get(id=client_id, organization=request.user.organization)
        except Client.DoesNotExist:
            return Response(
                {'detail': 'Client not found in this organization.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        appointment = None
        if appointment_id:
            try:
                appointment = Appointment.objects.get(
                    id=appointment_id,
                    organization=request.user.organization,
                )
            except Appointment.DoesNotExist:
                return Response(
                    {'detail': 'Appointment not found in this organization.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

        # Create the session, then generate the signed token using the new ID.
        # We persist only the SHA-256 hash so a DB dump can't be used to forge
        # live URLs.
        session = BLSSession.objects.create(
            organization=request.user.organization,
            appointment=appointment,
            client=client,
            therapist=request.user,
            status=BLSSessionStatus.CREATED,
            token_hash='pending',  # placeholder until we know the signed token
        )
        token = generate_session_token(str(session.id))
        session.token_hash = hash_token(token)
        session.save(update_fields=['token_hash', 'updated_at'])

        # Also assign a short opaque code (6 chars). The signed token stays
        # the source of truth; the code is just a display alias.
        short_code = assign_short_code(session)

        invite_url = _build_invite_url(request, short_code)
        response = BLSSessionInviteResponseSerializer({
            'session_id': session.id,
            'token': token,
            'short_code': short_code,
            'invite_url': invite_url,
            'expires_in_seconds': TOKEN_MAX_AGE_SECONDS,
        })
        return Response(response.data, status=status.HTTP_201_CREATED)

    # POST /sessions/{id}/end/
    @action(detail=True, methods=['post'], url_path='end')
    def end_session(self, request, pk=None):
        try:
            session = BLSSession.objects.get(
                id=pk,
                organization=request.user.organization,
            )
        except BLSSession.DoesNotExist:
            raise Http404
        if session.status == BLSSessionStatus.ENDED:
            return Response(
                {'detail': 'Session already ended.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = BLSSessionEndSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data

        session.status = BLSSessionStatus.ENDED
        session.ended_at = timezone.now()
        session.duration_seconds = v['duration_seconds']
        session.pass_count = v['pass_count']
        session.set_count = v['set_count']
        if 'settings_snapshot' in v:
            session.settings_snapshot = v['settings_snapshot']
        if 'modality' in v:
            session.modality = v['modality']
        if session.started_at is None and v['duration_seconds'] > 0:
            # Defensive — should always be set by START, but covers the case
            # where the consumer wasn't wired yet and the panel only ever
            # talked to REST.
            session.started_at = session.ended_at
        session.save()

        # Also refresh per-client preference with the final settings so the
        # next session for this client starts from where this one left off.
        if v.get('settings_snapshot'):
            BLSClientPreference.objects.update_or_create(
                client=session.client,
                defaults={
                    'config': v['settings_snapshot'],
                    'last_used_at': timezone.now(),
                },
            )

        # Append a structured BLS summary to the appointment's session note
        # (if one is linked). Best-effort — log + continue on failure rather
        # than blocking the end-session response. The local frontend history
        # already shows this session in the client chart regardless.
        try:
            append_bls_auto_log_to_session_note(session)
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception(
                'BLS auto-log failed for session %s', session.id
            )

        return Response(
            BLSSessionDetailSerializer(session).data,
            status=status.HTTP_200_OK,
        )


# ─── Public token verification ────────────────────────────────────────────────

class BLSTokenVerifyView(APIView):
    """
    GET /sessions/verify/?token=<token>

    Public — used by the client view on page load. Returns minimal info
    (validity + session id + status) so the WebSocket connection knows what
    to ask for. NEVER returns client name, therapist name, or any PHI.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        token = request.query_params.get('token', '')
        session = resolve_session_from_token(token)
        if session is None:
            return Response(
                BLSSessionVerifyResponseSerializer({'valid': False}).data,
                status=status.HTTP_200_OK,
            )
        return Response(
            BLSSessionVerifyResponseSerializer({
                'valid': True,
                'session_id': session.id,
                'status': session.status,
            }).data,
            status=status.HTTP_200_OK,
        )


class BLSShortCodeResolveThrottle(ScopedRateThrottle):
    """Tight cap on resolve attempts — defends the short_code space against
    brute force. 50 attempts/min/anon is well below the math that makes the
    6-char alphabet exhaustible inside a 4-hour window."""
    scope = 'bls_resolve'


class BLSShortCodeResolveView(APIView):
    """
    GET /sessions/resolve/?code=AB7K9Q

    Public — takes a short code and returns the signed token the client
    page actually needs to open the WebSocket. Rate-limited.

    The response intentionally mirrors the create-session response shape so
    the frontend can reuse the same downstream logic. NEVER includes PHI.
    Returns 404 (not 200 with valid=false) so brute-forcers get less signal.
    """
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [BLSShortCodeResolveThrottle]

    def get(self, request):
        code = request.query_params.get('code', '')
        session = resolve_session_from_short_code(code)
        if session is None:
            return Response(
                {'detail': 'Not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        # Mint a fresh token. The original token isn't stored (only its
        # hash is), so we generate a new signed token bound to the same
        # session id. Update the hash so the new token is recognised.
        token = generate_session_token(str(session.id))
        session.token_hash = hash_token(token)
        session.save(update_fields=['token_hash', 'updated_at'])
        return Response(
            {
                'session_id': session.id,
                'token': token,
                'status': session.status,
                'expires_in_seconds': TOKEN_MAX_AGE_SECONDS,
            },
            status=status.HTTP_200_OK,
        )


# ─── Per-client history ───────────────────────────────────────────────────────

class BLSClientHistoryView(PHIAccessAuditMixin, APIView):
    """
    GET /clients/{client_id}/history/

    List of past BLS sessions for a given client, newest first. Used by the
    BLS tab on the client chart.
    """
    permission_classes = [IsAuthenticated, IsClinicalStaff]
    audit_table_name = 'bls_sessions'

    def get(self, request, client_id):
        try:
            client = Client.objects.get(id=client_id, organization=request.user.organization)
        except Client.DoesNotExist:
            raise Http404

        qs = BLSSession.objects.filter(
            client=client,
            organization=request.user.organization,
            status=BLSSessionStatus.ENDED,
        ).order_by('-created_at')[:100]   # cap defensively

        serializer = BLSSessionDetailSerializer(qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


# ─── Per-client preferences ───────────────────────────────────────────────────

class BLSClientPreferenceView(PHIAccessAuditMixin, RetrieveUpdateAPIView):
    """
    GET/PUT /preferences/{client_id}/

    Returns the saved BLS preferences for a given client. Creates an empty row
    on first GET so the frontend doesn't have to special-case 404.
    """
    permission_classes = [IsAuthenticated, IsClinicalStaff]
    serializer_class = BLSClientPreferenceSerializer
    audit_table_name = 'bls_client_preferences'
    lookup_field = 'client_id'

    def get_object(self):
        client_id = self.kwargs[self.lookup_field]
        try:
            client = Client.objects.get(
                id=client_id,
                organization=self.request.user.organization,
            )
        except Client.DoesNotExist:
            raise Http404
        pref, _ = BLSClientPreference.objects.get_or_create(client=client)
        return pref

    def perform_update(self, serializer):
        serializer.save(last_used_at=timezone.now())


# ─── Org-wide defaults ────────────────────────────────────────────────────────

class BLSOrgDefaultsView(RetrieveUpdateAPIView):
    """
    GET/PUT /defaults/

    Org-wide BLS defaults — speed, sound, color, autostop policy, etc. Used
    by Settings → BLS Defaults on the frontend. Lazily created.
    """
    permission_classes = [IsAuthenticated, IsClinicalStaff]
    serializer_class = BLSOrgDefaultsSerializer

    def get_object(self):
        org = self.request.user.organization
        obj, _ = BLSOrgDefaults.objects.get_or_create(organization=org)
        return obj
