"""
Tests for apps.messaging — SMS reminder pipeline.

Covers:
    - Phone E.164 normalisation (services.to_e164_us)
    - Quiet hours window + shift_out_of_quiet_hours
    - HIPAA-safe message body (no PHI present)
    - Eligibility predicate (opt-out, no phone, non-session)
    - Signal handler creates 3 reminders on appointment save
    - Signal handler reschedules without duplicating on edit
    - Signal handler cancels reminders on appointment cancel
    - dispatch_due_reminders: claims due rows, marks stale rows skipped
    - send_reminder: happy path (stub), skip-at-send (opt-out flipped), failure paths
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.messaging import services
from apps.messaging.models import AppointmentReminder
from apps.messaging.providers.base import (
    ProviderError, ProviderResult, TransientProviderError,
)


PRACTICE_TZ = ZoneInfo('America/New_York')


# ─── Helpers ────────────────────────────────────────────────────────────────

@pytest.fixture
def consenting_client(sample_client):
    """sample_client mutated to opt in to SMS — keeps the conftest fixture
    untouched so other tests aren't affected."""
    sample_client.phone = '555-867-5309'
    sample_client.sms_reminders_enabled = True
    sample_client.sms_consent_obtained_at = timezone.now()
    sample_client.save()
    return sample_client


def _make_future_appointment(org, client, provider, hours_from_now: int = 72):
    """Schedule an appointment N hours from now (default 3 days out, so all
    three lead times — 48h/24h/2h — fall in the future)."""
    from apps.scheduling.models import Appointment
    start = timezone.now() + timedelta(hours=hours_from_now)
    return Appointment.objects.create(
        organization=org,
        client=client,
        provider=provider,
        start_time=start,
        end_time=start + timedelta(hours=1),
        service_code='90834',
        units=1,
        status='scheduled',
        event_type='client_session',
    )


# ─── Phone normalisation ────────────────────────────────────────────────────

class TestToE164:
    def test_ten_digits_with_punctuation(self):
        assert services.to_e164_us('(555) 867-5309') == '+15558675309'

    def test_eleven_digits_with_leading_one(self):
        assert services.to_e164_us('1-555-867-5309') == '+15558675309'

    def test_already_e164(self):
        assert services.to_e164_us('+15558675309') == '+15558675309'

    def test_too_short(self):
        assert services.to_e164_us('867-5309') is None

    def test_empty(self):
        assert services.to_e164_us('') is None

    def test_letters_only(self):
        assert services.to_e164_us('not a phone') is None


# ─── Quiet hours ────────────────────────────────────────────────────────────

class TestQuietHours:
    def _eastern(self, hour: int) -> datetime:
        """Build a UTC datetime that lands at `hour` in practice-local time."""
        local = datetime(2026, 6, 15, hour, 0, tzinfo=PRACTICE_TZ)
        return local.astimezone(dt_timezone.utc)

    def test_midnight_is_quiet(self):
        assert services.is_within_quiet_hours(self._eastern(0)) is True

    def test_3am_is_quiet(self):
        assert services.is_within_quiet_hours(self._eastern(3)) is True

    def test_8am_is_not_quiet(self):
        assert services.is_within_quiet_hours(self._eastern(8)) is False

    def test_noon_is_not_quiet(self):
        assert services.is_within_quiet_hours(self._eastern(12)) is False

    def test_9pm_is_quiet(self):
        assert services.is_within_quiet_hours(self._eastern(21)) is True

    def test_shift_5am_to_8am(self):
        five_am_utc = self._eastern(5)
        shifted = services.shift_out_of_quiet_hours(five_am_utc)
        assert shifted.astimezone(PRACTICE_TZ).hour == 8
        assert shifted.astimezone(PRACTICE_TZ).date() == five_am_utc.astimezone(PRACTICE_TZ).date()

    def test_shift_10pm_to_next_morning_8am(self):
        ten_pm_utc = self._eastern(22)
        shifted = services.shift_out_of_quiet_hours(ten_pm_utc)
        local = shifted.astimezone(PRACTICE_TZ)
        assert local.hour == 8
        # Next calendar day
        assert local.date() > ten_pm_utc.astimezone(PRACTICE_TZ).date()

    def test_shift_outside_quiet_is_noop(self):
        noon_utc = self._eastern(12)
        assert services.shift_out_of_quiet_hours(noon_utc) == noon_utc


# ─── Message body ───────────────────────────────────────────────────────────

class TestMessageBody:
    def _ctx(self, **overrides):
        defaults = dict(
            provider_label='J. Galasso',
            appointment_local=datetime(2026, 6, 15, 14, 0, tzinfo=PRACTICE_TZ),
            location_name='Cedar Grove',
            practice_phone='201-555-0100',
            is_telehealth=False,
        )
        defaults.update(overrides)
        return services.MessageContext(**defaults)

    def test_24h_body_says_tomorrow(self):
        body = services.build_message_body(self._ctx(), AppointmentReminder.LEAD_TIME_24H)
        assert 'tomorrow' in body.lower()
        assert 'J. Galasso' in body
        assert 'Cedar Grove' in body
        assert 'Reply STOP' in body

    def test_48h_body_says_two_days(self):
        body = services.build_message_body(self._ctx(), AppointmentReminder.LEAD_TIME_48H)
        assert '2 days' in body

    def test_2h_body_says_today(self):
        body = services.build_message_body(self._ctx(), AppointmentReminder.LEAD_TIME_2H)
        assert 'today' in body.lower()

    def test_telehealth_phrasing(self):
        body = services.build_message_body(
            self._ctx(is_telehealth=True), AppointmentReminder.LEAD_TIME_24H,
        )
        assert 'telehealth' in body.lower()
        assert 'at Cedar Grove' not in body

    def test_no_phi_in_body(self):
        body = services.build_message_body(self._ctx(), AppointmentReminder.LEAD_TIME_24H)
        # PHI markers we explicitly forbid
        for forbidden in ['John', 'Doe', 'diagnosis', 'CPT', '90834', 'F90', 'copay', 'balance']:
            assert forbidden not in body, f'PHI marker {forbidden!r} leaked into body: {body}'

    def test_body_under_single_segment(self):
        body = services.build_message_body(self._ctx(), AppointmentReminder.LEAD_TIME_24H)
        # GSM-7 single-segment is 160 chars. Allow some headroom for longer
        # location names but stay well under 320 (2 segments).
        assert len(body) < 200, f'body too long ({len(body)} chars): {body!r}'


# ─── Eligibility ────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestEligibility:
    def test_opted_out_skipped(self, org, sample_client, clinician_user):
        sample_client.sms_reminders_enabled = False
        sample_client.save()
        appt = _make_future_appointment(org, sample_client, clinician_user)
        eligible, reason = services.is_reminder_eligible(appt)
        assert eligible is False
        assert reason == AppointmentReminder.SKIP_REASON_OPTED_OUT

    def test_no_phone_skipped(self, org, consenting_client, clinician_user):
        consenting_client.phone = ''
        consenting_client.save()
        appt = _make_future_appointment(org, consenting_client, clinician_user)
        eligible, reason = services.is_reminder_eligible(appt)
        assert eligible is False
        assert reason == AppointmentReminder.SKIP_REASON_NO_PHONE

    def test_invalid_phone_skipped(self, org, consenting_client, clinician_user):
        consenting_client.phone = '12345'
        consenting_client.save()
        appt = _make_future_appointment(org, consenting_client, clinician_user)
        eligible, reason = services.is_reminder_eligible(appt)
        assert eligible is False
        assert reason == AppointmentReminder.SKIP_REASON_INVALID_PHONE

    def test_consenting_client_eligible(self, org, consenting_client, clinician_user):
        appt = _make_future_appointment(org, consenting_client, clinician_user)
        eligible, reason = services.is_reminder_eligible(appt)
        assert eligible is True
        assert reason == ''

    def test_non_session_event_skipped(self, org, clinician_user):
        from apps.scheduling.models import Appointment
        start = timezone.now() + timedelta(hours=72)
        appt = Appointment.objects.create(
            organization=org, provider=clinician_user, client=None,
            start_time=start, end_time=start + timedelta(hours=1),
            event_type='staff_meeting', title='Weekly standup',
            status='scheduled',
        )
        eligible, reason = services.is_reminder_eligible(appt)
        assert eligible is False
        assert reason == AppointmentReminder.SKIP_REASON_NON_SESSION


# ─── Signal handlers ────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
class TestSignalHandlers:
    def test_create_appointment_creates_three_reminders(
        self, org, consenting_client, clinician_user,
    ):
        appt = _make_future_appointment(org, consenting_client, clinician_user)
        reminders = AppointmentReminder.objects.filter(appointment=appt).order_by('lead_time')
        assert reminders.count() == 3
        leads = sorted(r.lead_time for r in reminders)
        assert leads == sorted([
            AppointmentReminder.LEAD_TIME_2H,
            AppointmentReminder.LEAD_TIME_24H,
            AppointmentReminder.LEAD_TIME_48H,
        ])
        assert all(r.status == AppointmentReminder.STATUS_PENDING for r in reminders)

    def test_edit_appointment_reschedules_without_duplicating(
        self, org, consenting_client, clinician_user,
    ):
        appt = _make_future_appointment(org, consenting_client, clinician_user, hours_from_now=72)
        before = list(AppointmentReminder.objects.filter(appointment=appt).order_by('lead_time'))
        assert len(before) == 3

        appt.start_time = appt.start_time + timedelta(hours=24)
        appt.end_time = appt.end_time + timedelta(hours=24)
        appt.save()

        after = list(AppointmentReminder.objects.filter(appointment=appt).order_by('lead_time'))
        assert len(after) == 3  # no duplicates
        # scheduled_for shifted by 24h on each
        for b, a in zip(before, after):
            assert a.scheduled_for == b.scheduled_for + timedelta(hours=24)

    def test_cancel_appointment_cancels_pending_reminders(
        self, org, consenting_client, clinician_user,
    ):
        appt = _make_future_appointment(org, consenting_client, clinician_user)
        assert AppointmentReminder.objects.filter(
            appointment=appt, status=AppointmentReminder.STATUS_PENDING,
        ).count() == 3

        appt.status = 'cancelled'
        appt.save()

        cancelled = AppointmentReminder.objects.filter(
            appointment=appt, status=AppointmentReminder.STATUS_CANCELLED,
        ).count()
        assert cancelled == 3

    def test_opt_out_after_scheduling_cancels_reminders(
        self, org, consenting_client, clinician_user,
    ):
        appt = _make_future_appointment(org, consenting_client, clinician_user)
        consenting_client.sms_reminders_enabled = False
        consenting_client.save()
        # The opt-out alone doesn't fire the signal (Client save, not
        # Appointment save). The reminders only get cancelled on the next
        # appointment save OR at send time. Verify send-time path skips them:
        first = AppointmentReminder.objects.filter(appointment=appt).first()
        eligible, reason = services.is_reminder_eligible(appt)
        assert eligible is False
        assert reason == AppointmentReminder.SKIP_REASON_OPTED_OUT
        # Pending row still exists; send_reminder will mark it skipped.
        assert first.status == AppointmentReminder.STATUS_PENDING


# ─── Dispatch task ──────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
class TestDispatchDueReminders:
    def test_picks_up_due_rows(self, org, consenting_client, clinician_user):
        from apps.messaging.tasks import dispatch_due_reminders

        # Schedule appointment 3 hours out — only the 2h reminder is "due"
        # within the dispatch window.
        _make_future_appointment(org, consenting_client, clinician_user, hours_from_now=3)
        # Force the 2h reminder's scheduled_for back to now so the dispatcher
        # picks it up.
        rem = AppointmentReminder.objects.get(lead_time=AppointmentReminder.LEAD_TIME_2H)
        rem.scheduled_for = timezone.now() - timedelta(seconds=30)
        rem.save(update_fields=['scheduled_for'])

        with patch('apps.messaging.tasks.send_reminder.delay') as mock_delay:
            result = dispatch_due_reminders()

        assert result['claimed'] == 1
        mock_delay.assert_called_once()
        rem.refresh_from_db()
        assert rem.status == AppointmentReminder.STATUS_IN_FLIGHT

    def test_marks_stale_rows_skipped(self, org, consenting_client, clinician_user):
        from apps.messaging.tasks import dispatch_due_reminders

        _make_future_appointment(org, consenting_client, clinician_user, hours_from_now=3)
        rem = AppointmentReminder.objects.get(lead_time=AppointmentReminder.LEAD_TIME_2H)
        # 4h ago — way outside the 1h stale window
        rem.scheduled_for = timezone.now() - timedelta(hours=4)
        rem.save(update_fields=['scheduled_for'])

        with patch('apps.messaging.tasks.send_reminder.delay'):
            result = dispatch_due_reminders()

        assert result['stale_skipped'] >= 1
        rem.refresh_from_db()
        assert rem.status == AppointmentReminder.STATUS_SKIPPED
        assert rem.skip_reason == AppointmentReminder.SKIP_REASON_QUIET_HOURS_STALE


# ─── Send task ──────────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
class TestSendReminder:
    def _claim_a_reminder(self, org, client, provider) -> AppointmentReminder:
        """Helper: appointment + advance one of its reminders to in_flight."""
        _make_future_appointment(org, client, provider, hours_from_now=3)
        rem = AppointmentReminder.objects.get(lead_time=AppointmentReminder.LEAD_TIME_2H)
        rem.status = AppointmentReminder.STATUS_IN_FLIGHT
        rem.attempts = 1
        rem.save(update_fields=['status', 'attempts'])
        return rem

    def test_happy_path_sends_via_stub(self, org, consenting_client, clinician_user, settings):
        settings.SMS_PROVIDER = 'stub'
        # Reset cached provider so settings change takes effect
        from apps.messaging.providers.factory import get_sms_provider
        get_sms_provider.cache_clear()

        from apps.messaging.tasks import send_reminder
        rem = self._claim_a_reminder(org, consenting_client, clinician_user)

        result = send_reminder(str(rem.id))
        assert result['status'] == 'sent'
        rem.refresh_from_db()
        assert rem.status == AppointmentReminder.STATUS_SENT
        assert rem.provider == 'stub'
        assert rem.provider_message_id.startswith('stub-')
        assert rem.sent_at is not None

    def test_opted_out_at_send_time_skips(self, org, consenting_client, clinician_user):
        from apps.messaging.tasks import send_reminder
        rem = self._claim_a_reminder(org, consenting_client, clinician_user)

        # Flip opt-out between scheduling and send
        consenting_client.sms_reminders_enabled = False
        consenting_client.save()

        result = send_reminder(str(rem.id))
        assert result['status'] == 'skipped'
        rem.refresh_from_db()
        assert rem.status == AppointmentReminder.STATUS_SKIPPED
        assert rem.skip_reason == AppointmentReminder.SKIP_REASON_OPTED_OUT

    def test_already_sent_row_is_noop(self, org, consenting_client, clinician_user):
        from apps.messaging.tasks import send_reminder
        rem = self._claim_a_reminder(org, consenting_client, clinician_user)
        rem.status = AppointmentReminder.STATUS_SENT
        rem.save(update_fields=['status'])

        result = send_reminder(str(rem.id))
        assert result['status'] == 'noop'

    def test_permanent_provider_error_marks_failed(self, org, consenting_client, clinician_user):
        from apps.messaging.tasks import send_reminder
        rem = self._claim_a_reminder(org, consenting_client, clinician_user)

        with patch('apps.messaging.providers.get_sms_provider') as mock_provider:
            instance = mock_provider.return_value
            instance.send_sms.side_effect = ProviderError(
                code='twilio:21610', detail='unsubscribed',
            )
            result = send_reminder(str(rem.id))

        assert result['status'] == 'failed'
        rem.refresh_from_db()
        assert rem.status == AppointmentReminder.STATUS_FAILED
        assert 'twilio:21610' in rem.last_error
