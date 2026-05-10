"""
SMS provider abstraction.

The send_sms() boundary is the *only* place that touches a provider SDK. Tests
mock this; tasks call it; nothing else knows whether we're on Twilio, Bandwidth,
or the in-process stub. Swapping providers is a one-file change in
``providers/factory.py``.

Errors split into two categories on purpose:

    * ``TransientProviderError``  — rate limit, 5xx, connection drop, timeout.
      Celery autoretry catches this and backs off.
    * ``ProviderError``           — permanent failure: invalid number, unsubscribed,
      account suspended, message body rejected. No retry; mark the row failed
      and move on.

This split is what keeps the retry policy honest. Without it we'd either retry
permanent errors forever, or fail-fast on transient ones we should have retried.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class ProviderError(Exception):
    """Permanent failure from the SMS provider — do not retry."""

    def __init__(self, code: str, detail: str = ''):
        super().__init__(f'{code}: {detail}' if detail else code)
        self.code = code
        self.detail = detail


class TransientProviderError(Exception):
    """Transient failure — Celery should retry with backoff."""

    def __init__(self, code: str, detail: str = ''):
        super().__init__(f'{code}: {detail}' if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ProviderResult:
    """Successful send. Failures raise instead of returning."""

    provider_name: str
    provider_message_id: str


class MessageProvider(ABC):
    """Interface every SMS provider implementation conforms to."""

    name: str = 'abstract'

    @abstractmethod
    def send_sms(self, to_number: str, body: str) -> ProviderResult:
        """
        Send `body` to `to_number` (must be E.164, e.g. +15551234567).

        Returns ProviderResult on success.
        Raises ProviderError on permanent failure.
        Raises TransientProviderError on retryable failure.
        """
        raise NotImplementedError
