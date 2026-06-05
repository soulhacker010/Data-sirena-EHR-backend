"""
End-to-end WebSocket lifecycle tests for the BLS module.

Exercises the full Phase-1 flow:
  1. Therapist creates a session via REST
  2. Therapist connects to /ws/bls/therapist/<session_id>/
  3. Client connects to /ws/bls/client/<token>/
  4. Therapist publishes STATE → client receives the same STATE
  5. Therapist sends COMMAND_END_SESSION → client receives SESSION_END
  6. Session row in DB has status=ended with the right counters

Uses Channels' WebsocketCommunicator so the entire ASGI pipeline runs
in-process. The InMemoryChannelLayer (set via override_settings below) lets
us run the channel group pub/sub without needing Redis in CI.
"""
import pytest
from channels.layers import channel_layers
from channels.testing import WebsocketCommunicator
from django.test import override_settings

from apps.bls.models import BLSSession, BLSSessionStatus
from apps.bls.routing import websocket_urlpatterns
from apps.bls.tokens import generate_session_token, hash_token
from apps.core.channels_auth import JWTAuthMiddlewareStack

from channels.routing import URLRouter


# Force every test in this module onto the in-memory channel layer. Without
# this, the production RedisChannelLayer is used and `group_add` hangs since
# Redis isn't running in the test environment.
@pytest.fixture(autouse=True)
def _in_memory_channel_layer(settings):
    settings.CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels.layers.InMemoryChannelLayer',
        },
    }
    # Clear any cached layer so the new setting takes effect.
    channel_layers.backends = {}
    yield
    channel_layers.backends = {}


# Silence unused-import warnings for override_settings — kept in case we
# need it for future test cases.
_ = override_settings


# A bare URLRouter — no AuthMiddlewareStack — lets us test the consumers
# directly while injecting an authenticated user into the scope manually.
# AuthMiddlewareStack reads from the request's session cookie, which isn't
# easy to fake in a WebsocketCommunicator test; bypassing it keeps the test
# focused on the consumer's own behavior.
bls_application = URLRouter(websocket_urlpatterns)


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _create_session(org, sample_client, clinician_user):
    """
    Create a BLSSession + matching signed token in one shot. Returns the
    session + token so individual tests can connect both ends.
    """
    session = BLSSession.objects.create(
        organization=org,
        client=sample_client,
        therapist=clinician_user,
        status=BLSSessionStatus.CREATED,
        token_hash='pending',
    )
    token = generate_session_token(str(session.id))
    session.token_hash = hash_token(token)
    session.save(update_fields=['token_hash'])
    return session, token


# ─── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_client_connects_with_valid_token(org, sample_client, clinician_user):
    from asgiref.sync import sync_to_async
    session, token = await sync_to_async(_create_session)(org, sample_client, clinician_user)

    client_comm = WebsocketCommunicator(bls_application, f'/ws/bls/client/{token}/')
    connected, _ = await client_comm.connect()
    assert connected, 'Client should be able to connect with a valid token'

    await client_comm.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_client_rejected_with_bad_token():
    client_comm = WebsocketCommunicator(
        bls_application,
        '/ws/bls/client/totally-fake-token/',
    )
    connected, code = await client_comm.connect()
    assert not connected, 'Bad token should be rejected at connect time'
    # We close with 4403 (custom code in BLSClientConsumer)
    assert code == 4403


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_therapist_to_client_state_roundtrip(org, sample_client, clinician_user):
    """
    Open both sockets, push STATE from the therapist, confirm the client tab
    receives it via the channel group. This is the demo's golden path.
    """
    from asgiref.sync import sync_to_async
    session, token = await sync_to_async(_create_session)(org, sample_client, clinician_user)

    # Therapist side. The AuthMiddlewareStack isn't in our test URLRouter, so
    # we set scope['user'] directly. The consumer guards on
    # `user.is_authenticated`, so we pass our real user.
    therapist_comm = WebsocketCommunicator(
        bls_application,
        f'/ws/bls/therapist/{session.id}/',
    )
    therapist_comm.scope['user'] = clinician_user
    therapist_connected, _ = await therapist_comm.connect()
    assert therapist_connected

    # Drain the welcome STATE message the consumer sends on connect.
    welcome = await therapist_comm.receive_json_from()
    assert welcome['type'] == 'STATE'

    # Client side — opens via token.
    client_comm = WebsocketCommunicator(bls_application, f'/ws/bls/client/{token}/')
    client_connected, _ = await client_comm.connect()
    assert client_connected

    # When the client connects, the therapist should receive a CLIENT_CONNECTED.
    notification = await therapist_comm.receive_json_from()
    assert notification['type'] == 'CLIENT_CONNECTED'

    # The CLIENT_CONNECTED broadcast also lands on the client itself (it just
    # joined the same group). The client's transport handler ignores it, but
    # in tests we have to drain it before the next assert.
    drained = await client_comm.receive_json_from()
    assert drained['type'] == 'CLIENT_CONNECTED'

    # Therapist publishes STATE (the existing frontend pattern). The consumer's
    # STATE pass-through should persist + broadcast it.
    # startedAt must be >= the session's created_at (CHECK constraint).
    import time
    await therapist_comm.send_json_to({
        'type': 'STATE',
        'config': {'speed': 7.0, 'sound': 'heartbeat'},
        'runState': 'running',
        'startedAt': int(time.time() * 1000),
    })

    # Client should receive the same STATE relayed by the server.
    relayed = await client_comm.receive_json_from()
    assert relayed['type'] == 'STATE'
    assert relayed['config']['speed'] == 7.0
    assert relayed['config']['sound'] == 'heartbeat'
    assert relayed['runState'] == 'running'

    # Therapist also receives the echo (their own publish bounced through the
    # group). That's the server-authoritative model.
    echo = await therapist_comm.receive_json_from()
    assert echo['type'] == 'STATE'

    await therapist_comm.disconnect()
    await client_comm.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_command_end_session_persists_and_broadcasts(org, sample_client, clinician_user):
    """
    Therapist sends COMMAND_END_SESSION → DB row transitions to ended +
    client tab gets a SESSION_END message.
    """
    from asgiref.sync import sync_to_async
    session, token = await sync_to_async(_create_session)(org, sample_client, clinician_user)

    therapist_comm = WebsocketCommunicator(
        bls_application,
        f'/ws/bls/therapist/{session.id}/',
    )
    therapist_comm.scope['user'] = clinician_user
    therapist_connected, _ = await therapist_comm.connect()
    assert therapist_connected
    await therapist_comm.receive_json_from()  # drain welcome STATE

    client_comm = WebsocketCommunicator(bls_application, f'/ws/bls/client/{token}/')
    client_connected, _ = await client_comm.connect()
    assert client_connected
    await therapist_comm.receive_json_from()  # drain CLIENT_CONNECTED on therapist
    await client_comm.receive_json_from()     # drain CLIENT_CONNECTED on client (echo)

    # Send the END command.
    await therapist_comm.send_json_to({'type': 'COMMAND_END_SESSION'})

    # Both sides should receive SESSION_END.
    end_msg_client = await client_comm.receive_json_from()
    assert end_msg_client['type'] == 'SESSION_END'

    end_msg_therapist = await therapist_comm.receive_json_from()
    assert end_msg_therapist['type'] == 'SESSION_END'

    # And the DB row reflects it.
    refreshed = await sync_to_async(BLSSession.objects.get)(pk=session.id)
    assert refreshed.status == BLSSessionStatus.ENDED
    assert refreshed.ended_at is not None

    await therapist_comm.disconnect()
    await client_comm.disconnect()


# ─── JWT auth middleware tests ────────────────────────────────────────────────
#
# These tests wrap the routing in JWTAuthMiddlewareStack so the same path the
# real ASGI app uses is exercised: the middleware parses ?token=<jwt> from the
# query string, validates it, and populates scope['user']. Without this layer
# wired in, the frontend's WS upgrade arrives with no session cookie and the
# therapist consumer would close 4401.

bls_application_with_auth = JWTAuthMiddlewareStack(URLRouter(websocket_urlpatterns))


def _access_token_for(user) -> str:
    from rest_framework_simplejwt.tokens import AccessToken
    return str(AccessToken.for_user(user))


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_therapist_connects_with_jwt_query_param(org, sample_client, clinician_user):
    """A valid ?token=<jwt> in the WS URL authenticates the therapist."""
    from asgiref.sync import sync_to_async
    session, _ = await sync_to_async(_create_session)(org, sample_client, clinician_user)
    jwt = await sync_to_async(_access_token_for)(clinician_user)

    comm = WebsocketCommunicator(
        bls_application_with_auth,
        f'/ws/bls/therapist/{session.id}/?token={jwt}',
    )
    connected, _ = await comm.connect()
    assert connected, 'Valid JWT in query string should authenticate the therapist'

    welcome = await comm.receive_json_from()
    assert welcome['type'] == 'STATE'

    await comm.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_therapist_rejected_without_jwt(org, sample_client, clinician_user):
    """No ?token= at all — consumer must reject with 4401."""
    from asgiref.sync import sync_to_async
    session, _ = await sync_to_async(_create_session)(org, sample_client, clinician_user)

    comm = WebsocketCommunicator(
        bls_application_with_auth,
        f'/ws/bls/therapist/{session.id}/',
    )
    connected, code = await comm.connect()
    assert not connected
    assert code == 4401


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_therapist_rejected_with_garbage_jwt(org, sample_client, clinician_user):
    """A malformed token in the query string falls through to AnonymousUser."""
    from asgiref.sync import sync_to_async
    session, _ = await sync_to_async(_create_session)(org, sample_client, clinician_user)

    comm = WebsocketCommunicator(
        bls_application_with_auth,
        f'/ws/bls/therapist/{session.id}/?token=not-a-real-jwt',
    )
    connected, code = await comm.connect()
    assert not connected
    assert code == 4401
