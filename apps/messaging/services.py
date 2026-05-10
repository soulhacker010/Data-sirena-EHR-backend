"""
Pure functions for the SMS reminder pipeline.

Kept separate from signals/tasks/providers so each piece is unit-testable in
isolation. Anything timezone-aware lives here so test fixtures can pin "now"
and verify quiet-hours behavior without freezing the whole world.

CLAUDE.md timezone rules apply throughout: practice timezone is
``America/New_York``; Eastern is what staff and clients see, regardless of
where the server runs. UTC is for storage only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone as dt_timezone
from typing import Iterable
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from .models import AppointmentReminder

PRACTICE_TZ = ZoneInfo('America/New_York')

# Phone parsing: strip everything that isn't a digit, then prefix +1 if the
# result is a 10-digit US number. Anything else (international, malformed) is
# returned as None and the reminder is skipped with reason=invalid_phone. We
# don't try to parse international here — the practice is US-only and Twilio
# A2P 10DLC compliance is per-region anyway.
_NON_DIGIT = re.compile(r'\D')


def to_e164_us(phone: str) -> str | None:
    if not phone:
        return None
    digits = _NON_DIGIT.sub('', phone)
    if len(digits) == 10:
        return f'+1{digits}'
    if len(digits) == 11 and digits.startswith('1'):
        return f'+{digits}'
    return None


# ─── Quiet hours ────────────────────────────────────────────────────────────

def quiet_hours_window() -> tuple[int, int]:
    """
    Returns (quiet_start_hour, quiet_end_hour) in practice-local 24h time.
    Quiet runs from quiet_start (inclusive) to quiet_end (exclusive) the next
    morning, e.g. (21, 8) means "no sends from 9pm through 7:59am".
    """
    return tuple(getattr(settings, 'SMS_QUIET_HOURS_LOCAL', (21, 8)))


def is_within_quiet_hours(when_utc: datetime) -> bool:
    quiet_start, quiet_end = quiet_hours_window()
    local_hour = when_utc.astimezone(PRACTICE_TZ).hour
    if quiet_start < quiet_end:  # daytime quiet window (unusual but support it)
        return quiet_start <= local_hour < quiet_end
    # Overnight window: e.g. 21..08 → quiet if hour >= 21 OR hour < 8
    return local_hour >= quiet_start or local_hour < quiet_end


def shift_out_of_quiet_hours(when_utc: datetime) -> datetime:
    """
    If `when_utc` falls inside the quiet window, return the first instant of
    the next non-quiet hour in practice time. Otherwise return as-is.

    Example: quiet (21, 8). A 2h reminder for a 7am appointment computes to
    5am — inside quiet — so this returns 8am same day. The dispatch loop will
    pick it up at 8am.
    """
    if not is_within_quiet_hours(when_utc):
        return when_utc
    _, quiet_end = quiet_hours_window()
    local = when_utc.astimezone(PRACTICE_TZ)
    target_local = datetime.combine(local.date(), time(hour=quiet_end), tzinfo=PRACTICE_TZ)
    if target_local <= local:
        # Quiet end already passed today → next morning
        target_local = target_local + timedelta(days=1)
    return target_local.astimezone(dt_timezone.utc)


# ─── Message body (HIPAA-safe) ──────────────────────────────────────────────

@dataclass(frozen=True)
class MessageContext:
    provider_label: str          # "Dr. J. Galasso" — first initial + last name
    appointment_local: datetime  # localised to practice time
    location_name: str           # "Cedar Grove" or "Telehealth"
    practice_phone: str          # callback number
    is_telehealth: bool


def practice_provider_label(user) -> str:
    """
    Build a low-PHI provider label.

    Practitioner name on a reminder is *not* PHI when stripped to first
    initial + last name (HIPAA Safe Harbor §164.514(b)(2)(i)(A) addresses
    patients, not providers — but we minimise anyway out of an abundance of
    caution and so the message stays under 160 chars).
    """
    first = (user.first_name or '').strip()
    last = (user.last_name or '').strip()
    if first and last:
        return f'{first[0]}. {last}'
    return last or first or 'your provider'


def build_message_body(ctx: MessageContext, lead_time: str) -> str:
    """
    HIPAA-safe reminder body. NEVER includes:
        - client name
        - diagnosis or CPT
        - session content
        - MRN
        - copay or balance amounts

    Format kept under ~160 chars to fit in a single SMS segment (multi-segment
    messages cost 2x and increase carrier filtering risk).
    """
    # %-d / %-I aren't portable across Windows + Linux. Use zero-padded format
    # then strip the leading zero from day-of-month and hour for readability.
    when_str = ctx.appointment_local.strftime('%a %b %d, %I:%M %p')
    # Replace the first " 0" with " " — only matches the day field; the time
    # field has a colon directly after the hour so " 0:" wouldn't appear here,
    # but be conservative and strip both occurrences if present.
    if ', 0' in when_str:
        when_str = when_str.replace(', 0', ', ', 1)
    if ' 0' in when_str[:7]:  # day-of-month region
        when_str = when_str[:7].replace(' 0', ' ', 1) + when_str[7:]

    location_phrase = (
        'via telehealth' if ctx.is_telehealth else f'at {ctx.location_name}'
    )

    if lead_time == AppointmentReminder.LEAD_TIME_48H:
        lede = 'Reminder: appointment in 2 days'
    elif lead_time == AppointmentReminder.LEAD_TIME_24H:
        lede = 'Reminder: appointment tomorrow'
    elif lead_time == AppointmentReminder.LEAD_TIME_2H:
        lede = 'Reminder: appointment today'
    else:
        lede = 'Reminder: appointment'

    body = (
        f'{lede} with {ctx.provider_label} on {when_str} {location_phrase}. '
        f'Reply STOP to opt out. Questions: {ctx.practice_phone}.'
    )
    return body


# ─── Reminder scheduling ────────────────────────────────────────────────────

def lead_times() -> Iterable[str]:
    return [
        AppointmentReminder.LEAD_TIME_48H,
        AppointmentReminder.LEAD_TIME_24H,
        AppointmentReminder.LEAD_TIME_2H,
    ]


def compute_scheduled_for(appointment_start_utc: datetime, lead_time: str) -> datetime:
    """
    appointment_start - lead_time, then shift out of quiet hours if needed.
    """
    hours = AppointmentReminder.LEAD_TIME_HOURS[lead_time]
    raw = appointment_start_utc - timedelta(hours=hours)
    return shift_out_of_quiet_hours(raw)


def is_reminder_eligible(appointment) -> tuple[bool, str]:
    """
    Returns (eligible, skip_reason). skip_reason is '' when eligible.

    Used both at signal time (so we don't create reminders that will just be
    skipped) and at send time (so opt-outs after creation are honoured).
    """
    if appointment.event_type != 'client_session':
        return False, AppointmentReminder.SKIP_REASON_NON_SESSION
    if appointment.status in ('cancelled', 'no_show'):
        return False, AppointmentReminder.SKIP_REASON_PAST  # not strictly past, but skip
    client = appointment.client
    if client is None:
        return False, AppointmentReminder.SKIP_REASON_NON_SESSION
    if not client.sms_reminders_enabled:
        return False, AppointmentReminder.SKIP_REASON_OPTED_OUT
    if not client.phone:
        return False, AppointmentReminder.SKIP_REASON_NO_PHONE
    if to_e164_us(client.phone) is None:
        return False, AppointmentReminder.SKIP_REASON_INVALID_PHONE
    return True, ''
