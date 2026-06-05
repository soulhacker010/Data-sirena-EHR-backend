"""
WebSocket URL routing for the BLS module. Imported by config/asgi.py and
mounted under the ProtocolTypeRouter's 'websocket' branch.


Two consumer surfaces:

  ws/bls/therapist/<session_id>/
      Authenticated via Channels' AuthMiddlewareStack (Django session
      cookie + JWT). Only the therapist who created the session — or
      another clinician in the same organization — may connect.

  ws/bls/client/<token>/
      Public, token-gated. The client view connects here using the signed
      token from the invite URL. Auth happens inside the consumer by
      verifying the token against the BLSSession record.
"""
from django.urls import re_path

from .consumers import BLSClientConsumer, BLSTherapistConsumer


websocket_urlpatterns = [
    re_path(
        r'^ws/bls/therapist/(?P<session_id>[0-9a-f-]+)/$',
        BLSTherapistConsumer.as_asgi(),
    ),
    re_path(
        r'^ws/bls/client/(?P<token>[^/]+)/$',
        BLSClientConsumer.as_asgi(),
    ),
]
