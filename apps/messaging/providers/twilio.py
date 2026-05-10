"""
Twilio SMS provider.

Activated by setting SMS_PROVIDER=twilio plus the three Twilio env vars:
    TWILIO_ACCOUNT_SID
    TWILIO_AUTH_TOKEN
    TWILIO_FROM_NUMBER

Until those env vars exist *and* the ``twilio`` Python package is installed,
this module imports cleanly but raises ProviderError at the first call. That
keeps the whole codebase importable in environments where Twilio isn't yet
provisioned (CI, local dev without creds, the current production state).

Twilio error reference (numeric `code` returned in the API response):
    https://www.twilio.com/docs/api/errors

Permanent (no retry):
    21211  Invalid 'To' phone number
    21408  Permission to send to this region disabled
    21610  Recipient is unsubscribed (replied STOP)
    21614  'To' number is not a mobile number
    30003  Unreachable destination handset
    30005  Unknown destination handset
    30006  Landline or unreachable carrier
    30007  Carrier filtering blocked
    30008  Unknown error (treat as permanent — Twilio gave up)

Transient (retry with backoff):
    20429  Too Many Requests (rate limit)
    20003  Authentication failure (treat as transient until ops fixes creds)
    HTTP 5xx
    Connection / timeout exceptions
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.conf import settings

from .base import MessageProvider, ProviderError, ProviderResult, TransientProviderError

if TYPE_CHECKING:  # pragma: no cover
    from twilio.rest import Client as TwilioClient

logger = logging.getLogger(__name__)


PERMANENT_TWILIO_CODES = {
    21211, 21408, 21610, 21614,
    30003, 30005, 30006, 30007, 30008,
}
TRANSIENT_TWILIO_CODES = {
    20003,  # auth failure — treat as transient; do not loop forever, ops will fix
    20429,  # rate limit
}


class TwilioProvider(MessageProvider):
    name = 'twilio'

    def __init__(self):
        self.account_sid = settings.TWILIO_ACCOUNT_SID
        self.auth_token = settings.TWILIO_AUTH_TOKEN
        self.from_number = settings.TWILIO_FROM_NUMBER
        if not (self.account_sid and self.auth_token and self.from_number):
            # We deliberately fail at construction, not at module import. Other
            # code (admin, migrations, tests) can import this file with no creds.
            raise ProviderError(
                code='not_configured',
                detail='TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM_NUMBER missing',
            )

        try:
            from twilio.rest import Client
        except ImportError as e:
            raise ProviderError(
                code='package_not_installed',
                detail='pip install twilio',
            ) from e

        self._client: 'TwilioClient' = Client(self.account_sid, self.auth_token)

    def send_sms(self, to_number: str, body: str) -> ProviderResult:
        if not to_number or not to_number.startswith('+'):
            raise ProviderError(code='invalid_to_number', detail='must be E.164')
        if not body:
            raise ProviderError(code='empty_body')

        try:
            message = self._client.messages.create(
                to=to_number,
                from_=self.from_number,
                body=body,
            )
        except Exception as e:
            self._raise_classified(e)
            raise  # pragma: no cover — _raise_classified always raises

        return ProviderResult(
            provider_name=self.name,
            provider_message_id=message.sid,
        )

    @staticmethod
    def _raise_classified(exc: Exception) -> None:
        """
        Translate a Twilio exception into the abstract Provider exception
        hierarchy. We only import twilio's exception class lazily so the rest
        of the system stays importable when twilio isn't installed.
        """
        try:
            from twilio.base.exceptions import TwilioRestException
        except ImportError:
            raise TransientProviderError(code='twilio_import_failed', detail=str(exc)) from exc

        if isinstance(exc, TwilioRestException):
            code = exc.code or 0
            # Strip PHI defensively — Twilio's `msg` shouldn't contain client
            # data, but the message body we passed in echoes back on some errors.
            detail = (exc.msg or '')[:120].replace('\n', ' ')
            if code in PERMANENT_TWILIO_CODES:
                raise ProviderError(code=f'twilio:{code}', detail=detail) from exc
            if code in TRANSIENT_TWILIO_CODES or (exc.status and exc.status >= 500):
                raise TransientProviderError(code=f'twilio:{code}', detail=detail) from exc
            # Unknown code — be conservative and treat as permanent so we don't
            # spend the retry budget on something that will never succeed.
            raise ProviderError(code=f'twilio:{code}', detail=detail) from exc

        # Connection errors, timeouts, generic exceptions → transient
        raise TransientProviderError(
            code='transport_error',
            detail=type(exc).__name__,
        ) from exc
