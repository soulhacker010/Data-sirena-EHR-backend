"""
Stub SMS provider.

Used in development, tests, and any environment where SMS_PROVIDER is unset or
``stub``. Logs the would-be send to stdout (no PHI in the body — the caller is
already responsible for that contract) and returns a fake message ID so the
rest of the pipeline can be exercised end-to-end without spending money or
risking a misdirected text.

The stub is deliberately a *real* provider implementation, not a no-op. We want
the same code path in dev as in prod so a regression doesn't hide behind a
mock-only branch.
"""
import logging
import uuid

from .base import MessageProvider, ProviderError, ProviderResult

logger = logging.getLogger(__name__)


class StubProvider(MessageProvider):
    name = 'stub'

    def send_sms(self, to_number: str, body: str) -> ProviderResult:
        if not to_number or not to_number.startswith('+'):
            # Mirror Twilio's error shape so swapping providers doesn't change
            # which exception type the caller catches.
            raise ProviderError(code='invalid_to_number', detail='must be E.164')
        if not body:
            raise ProviderError(code='empty_body')

        message_id = f'stub-{uuid.uuid4().hex[:16]}'
        logger.info(
            'StubProvider would send SMS: to=%s, len=%d, id=%s',
            to_number, len(body), message_id,
        )
        return ProviderResult(provider_name=self.name, provider_message_id=message_id)
