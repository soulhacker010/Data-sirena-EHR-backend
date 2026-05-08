"""
Appointment CRUD endpoint tests.

Tests scheduling, rescheduling, status updates, double-booking prevention,
and cross-org isolation.
"""
import pytest
from unittest.mock import patch
from rest_framework import status


@pytest.mark.django_db
class TestAppointmentCreate:
    url = '/api/v1/appointments/'

    @patch('apps.core.email.EmailService.send_appointment_email')
    def test_create_appointment(self, mock_send_appointment_email, admin_client, sample_client, admin_user):
        """Create appointment → 201."""
        resp = admin_client.post(self.url, {
            'client_id': str(sample_client.id),
            'provider_id': str(admin_user.id),
            'start_time': '2026-03-01T09:00:00Z',
            'end_time': '2026-03-01T11:00:00Z',
            'service_code': '97153',
            'units': 8,
        })
        assert resp.status_code == status.HTTP_201_CREATED
        assert 'client_id' in resp.data
        assert 'provider_id' in resp.data
        mock_send_appointment_email.assert_called_once()

    def test_create_appointment_missing_client(self, admin_client, admin_user):
        """Missing client_id → 400."""
        resp = admin_client.post(self.url, {
            'provider_id': str(admin_user.id),
            'start_time': '2026-03-01T09:00:00Z',
            'end_time': '2026-03-01T11:00:00Z',
        })
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_appointment_missing_provider(self, admin_client, sample_client):
        """Missing provider_id → 400."""
        resp = admin_client.post(self.url, {
            'client_id': str(sample_client.id),
            'start_time': '2026-03-01T09:00:00Z',
            'end_time': '2026-03-01T11:00:00Z',
        })
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_double_booking_prevention(self, admin_client, sample_client, admin_user):
        """Two appointments for same provider at same time → 400."""
        payload = {
            'client_id': str(sample_client.id),
            'provider_id': str(admin_user.id),
            'start_time': '2026-03-05T09:00:00Z',
            'end_time': '2026-03-05T11:00:00Z',
            'service_code': '97153',
            'units': 8,
        }
        # First booking succeeds
        resp1 = admin_client.post(self.url, payload)
        assert resp1.status_code == status.HTTP_201_CREATED

        # Second overlapping booking fails
        resp2 = admin_client.post(self.url, payload)
        assert resp2.status_code == status.HTTP_400_BAD_REQUEST

    def test_cross_org_client_rejected(self, admin_client, other_org, admin_user):
        """Can't schedule with a client from another org."""
        from apps.clients.models import Client
        other_client = Client.objects.create(
            organization=other_org,
            first_name='Other',
            last_name='Client',
            date_of_birth='2020-01-01',
        )
        resp = admin_client.post(self.url, {
            'client_id': str(other_client.id),
            'provider_id': str(admin_user.id),
            'start_time': '2026-03-10T09:00:00Z',
            'end_time': '2026-03-10T11:00:00Z',
        })
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_unauthenticated(self, api_client):
        """No auth → 401."""
        resp = api_client.post(self.url, {})
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestAppointmentList:
    url = '/api/v1/appointments/'

    def test_list_appointments_with_daterange(self, admin_client, sample_appointment):
        """List appointments with date filter → includes our appointment."""
        resp = admin_client.get(self.url, {
            'start_date': '2026-02-01',
            'end_date': '2026-04-01',
        })
        assert resp.status_code == status.HTTP_200_OK
        assert isinstance(resp.data, list)
        assert len(resp.data) >= 1

    def test_list_appointments_scoped_to_org(self, other_admin_client, sample_appointment):
        """Other org's admin can't see our appointments."""
        resp = other_admin_client.get(self.url, {
            'start_date': '2026-02-01',
            'end_date': '2026-04-01',
        })
        assert resp.status_code == status.HTTP_200_OK
        appt_ids = [a['id'] for a in resp.data]
        assert str(sample_appointment.id) not in appt_ids


@pytest.mark.django_db
class TestAppointmentUpdate:
    @patch('apps.core.email.EmailService.send_appointment_email')
    def test_update_appointment(self, mock_send_appointment_email, admin_client, sample_appointment):
        """Update appointment notes → 200."""
        url = f'/api/v1/appointments/{sample_appointment.id}/'
        resp = admin_client.put(url, {
            'client_id': str(sample_appointment.client_id),
            'provider_id': str(sample_appointment.provider_id),
            'start_time': '2026-03-01T10:00:00Z',
            'end_time': '2026-03-01T12:00:00Z',
            'service_code': '97153',
            'units': 8,
            'notes': 'Rescheduled',
        })
        assert resp.status_code == status.HTTP_200_OK
        mock_send_appointment_email.assert_called_once()

@pytest.mark.django_db
class TestAppointmentStatus:
    def test_update_status(self, admin_client, sample_appointment):
        """Mark appointment as attended → 200."""
        url = f'/api/v1/appointments/{sample_appointment.id}/status/'
        resp = admin_client.post(url, {'status': 'attended'})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['status'] == 'attended'

    def test_invalid_status(self, admin_client, sample_appointment):
        """Invalid status value → 400."""
        url = f'/api/v1/appointments/{sample_appointment.id}/status/'
        resp = admin_client.post(url, {'status': 'bogus'})
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestAppointmentDelete:
    @patch('apps.core.email.EmailService.send_appointment_email')
    def test_delete_appointment(self, mock_send_appointment_email, admin_client, sample_appointment):
        """Delete → 204."""
        url = f'/api/v1/appointments/{sample_appointment.id}/'
        resp = admin_client.delete(url)
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        mock_send_appointment_email.assert_called_once()


@pytest.mark.django_db
class TestNonclientAppointments:
    """E31 Half A: appointments without a client (staff meetings, personal
    blocks, training). Lets directors and clinicians block off time without
    needing to fake a client row, which was Dr. Joe's specific workflow gap."""
    url = '/api/v1/appointments/'

    def _base(self, **overrides):
        base = {
            'provider_id': '<set-in-test>',
            'start_time': '2026-05-15T13:00:00Z',
            'end_time': '2026-05-15T14:00:00Z',
        }
        base.update(overrides)
        return base

    def test_create_staff_meeting_without_client(self, admin_client, admin_user):
        resp = admin_client.post(self.url, {
            'provider_id': str(admin_user.id),
            'start_time': '2026-05-15T13:00:00Z',
            'end_time': '2026-05-15T14:00:00Z',
            'event_type': 'staff_meeting',
            'title': 'Q3 Directors Meeting',
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data['event_type'] == 'staff_meeting'
        assert resp.data['title'] == 'Q3 Directors Meeting'
        # AppointmentCreateSerializer surfaces client_id (the FK column),
        # which should be null for non-session events.
        assert resp.data.get('client_id') in (None, '')

    def test_client_session_still_requires_client(self, admin_client, admin_user):
        """Backwards compat: omitting client for the default event_type
        (client_session) is rejected — exactly as before."""
        resp = admin_client.post(self.url, {
            'provider_id': str(admin_user.id),
            'start_time': '2026-05-15T13:00:00Z',
            'end_time': '2026-05-15T14:00:00Z',
            # no client_id, no event_type → defaults to client_session
        }, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert 'client_id' in (resp.data.get('errors') or resp.data)

    def test_staff_meeting_with_client_rejected(
        self, admin_client, admin_user, sample_client,
    ):
        """A non-session event must NOT carry a client — caught at serializer
        level so we surface a clean 400, not an IntegrityError."""
        resp = admin_client.post(self.url, {
            'client_id': str(sample_client.id),
            'provider_id': str(admin_user.id),
            'start_time': '2026-05-15T13:00:00Z',
            'end_time': '2026-05-15T14:00:00Z',
            'event_type': 'staff_meeting',
            'title': 'Should not have client',
        }, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_staff_meeting_without_title_rejected(self, admin_client, admin_user):
        """Non-session events must have a title — otherwise the calendar has
        nothing to render."""
        resp = admin_client.post(self.url, {
            'provider_id': str(admin_user.id),
            'start_time': '2026-05-15T13:00:00Z',
            'end_time': '2026-05-15T14:00:00Z',
            'event_type': 'staff_meeting',
            # no title
        }, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_invalid_event_type_rejected(self, admin_client, admin_user):
        resp = admin_client.post(self.url, {
            'provider_id': str(admin_user.id),
            'start_time': '2026-05-15T13:00:00Z',
            'end_time': '2026-05-15T14:00:00Z',
            'event_type': 'not_a_real_type',
            'title': 'X',
        }, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_db_constraint_blocks_session_without_client(
        self, admin_user, org,
    ):
        """Belt-and-suspenders: even bypassing the API, a client_session row
        with null client must be rejected by the DB."""
        from apps.scheduling.models import Appointment
        from django.db.utils import IntegrityError
        with pytest.raises(IntegrityError):
            Appointment.objects.create(
                organization=org, provider=admin_user,
                start_time='2026-05-15T13:00:00Z',
                end_time='2026-05-15T14:00:00Z',
                event_type='client_session',
                client=None,
            )

    def test_calendar_list_includes_nonclient_events(
        self, admin_client, admin_user, org,
    ):
        """Calendar fetches everything in the org — staff events appear
        alongside client sessions in the same payload."""
        from apps.scheduling.models import Appointment
        Appointment.objects.create(
            organization=org, provider=admin_user,
            start_time='2026-05-15T13:00:00Z',
            end_time='2026-05-15T14:00:00Z',
            event_type='staff_meeting',
            title='Visible Staff Event',
        )
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        titles = [a.get('title') for a in resp.data]
        assert 'Visible Staff Event' in titles
