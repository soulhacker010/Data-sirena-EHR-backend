"""
ASGI config for Sirena Health EHR.

Routes HTTP requests to the standard Django app and WebSocket connections
through Channels' ProtocolTypeRouter to the BLS consumer URL map.

The ordering of imports below is deliberate: the Django ASGI app must be
constructed BEFORE the Channels routing/consumers are imported, because the
consumers reference Django models. Importing them before Django is set up
raises AppRegistryNotReady.
"""
import os
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.production')

# Initialise Django apps first.
django_asgi_app = get_asgi_application()

# Now safe to import Channels + project routing.
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator, OriginValidator  # noqa: E402
from django.conf import settings  # noqa: E402

from apps.core.channels_auth import JWTAuthMiddlewareStack  # noqa: E402
from apps.bls.routing import websocket_urlpatterns as bls_websocket_urlpatterns  # noqa: E402


# Origin validation for WebSocket upgrades.
#
# AllowedHostsOriginValidator only accepts Origin headers whose host appears
# in ALLOWED_HOSTS — that breaks the production split where the React app
# lives on Vercel and the API lives on Render (different hosts). So when
# WS_ALLOWED_ORIGINS is set, we use OriginValidator with an explicit list
# (comma-separated, including scheme). Falls back to the strict
# AllowedHostsOriginValidator for dev (where everything is localhost).
_ws_allowed_origins_raw = os.getenv('WS_ALLOWED_ORIGINS', '').strip()
if _ws_allowed_origins_raw:
    _ws_allowed_origins = [o.strip() for o in _ws_allowed_origins_raw.split(',') if o.strip()]

    def _wrap_origins(inner):
        return OriginValidator(inner, _ws_allowed_origins)
else:
    def _wrap_origins(inner):
        return AllowedHostsOriginValidator(inner)


# JWTAuthMiddlewareStack reads ?token=<jwt> from the WS query string and
# populates scope['user'], with fallthrough to AuthMiddlewareStack
# (session cookie). The React app uses JWT bearer tokens for REST and now
# sends the same token on WS upgrade — without this layer the therapist
# consumer would always see AnonymousUser and close with 4401.
application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': _wrap_origins(
        JWTAuthMiddlewareStack(
            URLRouter(bls_websocket_urlpatterns),
        )
    ),
})

# Silence unused-import (settings is intentionally imported for side-effect
# parity with future expansions; tools sometimes flag it otherwise).
_ = settings
