"""
JWT-aware Channels middleware.

The React app authenticates via simplejwt Bearer tokens, not Django session
cookies. Channels' built-in AuthMiddlewareStack only reads session cookies, so
WebSocket consumers see scope['user'] as AnonymousUser even when the user is
logged in. This middleware looks for `?token=<access_token>` on the WS URL
and populates scope['user'] from the JWT before the consumer runs.

Falls through to whatever inner middleware decides (typically
AuthMiddlewareStack reading the session cookie) when no token is present or
the token is invalid — that keeps the door open for cookie-based clients
without breaking JWT clients.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken


@database_sync_to_async
def _user_from_raw_token(raw_token: str) -> object:
    """
    Validate `raw_token` and return the matching active user, else AnonymousUser.

    Two checks beyond signature and expiry:

      1. It must be an ACCESS token. `AccessToken` enforces `token_type`, where
         the previous `UntypedToken` accepted anything that verified — including
         a *refresh* token, whose 7-day life and refresh-endpoint-only purpose
         make it entirely wrong as a socket credential.

      2. Its `jti` must not be blacklisted. Logout blacklists the refresh token,
         but signature verification alone never consults that list, so a
         logged-out credential could still open a socket until it expired.

    One indexed lookup per upgrade. WebSocket connects are rare here — roughly
    one per BLS session — so this is not a hot path.
    """
    try:
        validated = AccessToken(raw_token)
    except TokenError:
        return AnonymousUser()

    jti = validated.get('jti')
    if jti:
        from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
        if BlacklistedToken.objects.filter(token__jti=jti).exists():
            return AnonymousUser()

    user_id = validated.get('user_id')
    if not user_id:
        return AnonymousUser()

    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return AnonymousUser()
    if not user.is_active:
        return AnonymousUser()
    return user


class JWTAuthMiddleware(BaseMiddleware):
    """
    Reads ?token=<jwt> from the WS query string. If valid, populates
    scope['user']; otherwise leaves scope['user'] untouched (likely set by
    a downstream middleware to AnonymousUser).
    """

    async def __call__(self, scope, receive, send):
        query_string = scope.get('query_string', b'').decode('utf-8', errors='ignore')
        params = parse_qs(query_string)
        raw_token = (params.get('token') or [None])[0]

        if raw_token:
            # Invalid, expired, wrong-type or blacklisted tokens all resolve to
            # AnonymousUser; the consumer rejects when auth is required.
            scope['user'] = await _user_from_raw_token(raw_token)
        else:
            scope.setdefault('user', AnonymousUser())

        return await super().__call__(scope, receive, send)


def JWTAuthMiddlewareStack(inner):
    """Drop-in replacement for AuthMiddlewareStack with JWT support."""
    from channels.auth import AuthMiddlewareStack
    # Wrap AuthMiddlewareStack so the session-cookie path still works for
    # any browser tab that happens to have a session.
    return JWTAuthMiddleware(AuthMiddlewareStack(inner))
