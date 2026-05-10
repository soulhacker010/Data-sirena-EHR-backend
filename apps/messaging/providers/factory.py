"""
Provider selection.

Resolves SMS_PROVIDER → concrete MessageProvider instance. Cached per-process
to avoid reconstructing the Twilio HTTP client on every send.
"""
from functools import lru_cache

from django.conf import settings

from .base import MessageProvider, ProviderError
from .stub import StubProvider


@lru_cache(maxsize=1)
def get_sms_provider() -> MessageProvider:
    name = getattr(settings, 'SMS_PROVIDER', 'stub').lower()

    if name == 'stub':
        return StubProvider()

    if name == 'twilio':
        # Imported lazily so a misconfigured environment variable doesn't crash
        # at process start — only when something actually tries to send.
        from .twilio import TwilioProvider
        return TwilioProvider()

    raise ProviderError(
        code='unknown_provider',
        detail=f'SMS_PROVIDER={name!r} not recognised (expected stub | twilio)',
    )
