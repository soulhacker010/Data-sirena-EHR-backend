"""
Tests for BUILD 5: Calendar improvements
- series_id field for recurring appointments
- service_code and location filters
- cancel-series endpoint
"""
import pytest
import uuid
from datetime import datetime, timedelta
from django.utils import timezone
from apps.scheduling.models import Appointment
from apps.scheduling.services import RecurrenceGenerator


@pytest.mark.django_db
class TestBuild5Features:
    """Test BUILD 5 calendar improvements."""

    def test_series_id_field_exists(self):
        """Test that series_id field was added to Appointment model."""
        apt = Appointment(
            start_time=timezone.now(),
            end_time=timezone.now() + timedelta(hours=1),
            series_id=uuid.uuid4()
        )
        assert hasattr(apt, 'series_id')
        assert apt.series_id is not None

    def test_recurring_appointments_share_series_id(self, admin_client, org, admin_user, sample_client):
        """Test that recurring appointments generated share the same series_id."""
        # Create base appointment
        base_apt = Appointment.objects.create(
            organization=org,
            client=sample_client,
            provider=admin_user,
            start_time=timezone.now(),
            end_time=timezone.now() + timedelta(hours=1),
            service_code='97153',
            is_recurring=True,
            status='scheduled'
        )
        
        # Generate recurring instances
        pattern = {
            'frequency': 'weekly',
            'end_date': (timezone.now() + timedelta(days=21)).strftime('%Y-%m-%d')
        }
        instances = RecurrenceGenerator.generate(base_apt, pattern)
        
        # Verify base appointment got series_id
        base_apt.refresh_from_db()
        assert base_apt.series_id is not None
        
        # Verify all instances share the same series_id
        assert len(instances) > 0
        for instance in instances:
            assert instance.series_id == base_apt.series_id

    def test_appointment_filters(self, admin_client, org, admin_user, sample_client):
        """Test new appointment filters: service_code and location_id."""
        from apps.accounts.models import Location
        
        # Create locations
        loc1 = Location.objects.create(organization=org, name='Main Office')
        loc2 = Location.objects.create(organization=org, name='Satellite Office')
        
        # Create appointments with different service codes and locations
        apt1 = Appointment.objects.create(
            organization=org,
            client=sample_client,
            provider=admin_user,
            start_time=timezone.now(),
            end_time=timezone.now() + timedelta(hours=1),
            service_code='97153',
            location=loc1,
            status='scheduled'
        )
        apt2 = Appointment.objects.create(
            organization=org,
            client=sample_client,
            provider=admin_user,
            start_time=timezone.now() + timedelta(hours=2),
            end_time=timezone.now() + timedelta(hours=3),
            service_code='97156',
            location=loc2,
            status='scheduled'
        )
        
        # Test service_code filter
        response = admin_client.get('/api/v1/appointments/', {'service_code': '97153'})
        assert response.status_code == 200
        assert len(response.json()) == 1
        assert response.json()[0]['service_code'] == '97153'
        
        # Test location_id filter
        response = admin_client.get('/api/v1/appointments/', {'location_id': str(loc2.id)})
        assert response.status_code == 200
        assert len(response.json()) == 1
        assert response.json()[0]['location']['id'] == str(loc2.id)

    @pytest.mark.django_db
    def test_cancel_series_endpoint(self, admin_client, org, admin_user, sample_client):
        """Test the cancel-series endpoint cancels all future appointments in a series."""
        series_id = uuid.uuid4()
        now = timezone.now()
        
        # Create past, current, and future appointments in series
        past_apt = Appointment.objects.create(
            organization=org,
            client=sample_client,
            provider=admin_user,
            start_time=now - timedelta(days=7),
            end_time=now - timedelta(days=7) + timedelta(hours=1),
            service_code='97153',
            is_recurring=True,
            series_id=series_id,
            status='attended'
        )
        current_apt = Appointment.objects.create(
            organization=org,
            client=sample_client,
            provider=admin_user,
            start_time=now + timedelta(hours=1),
            end_time=now + timedelta(hours=2),
            service_code='97153',
            is_recurring=True,
            series_id=series_id,
            status='scheduled'
        )
        future_apt1 = Appointment.objects.create(
            organization=org,
            client=sample_client,
            provider=admin_user,
            start_time=now + timedelta(days=7),
            end_time=now + timedelta(days=7) + timedelta(hours=1),
            service_code='97153',
            is_recurring=True,
            series_id=series_id,
            status='scheduled'
        )
        future_apt2 = Appointment.objects.create(
            organization=org,
            client=sample_client,
            provider=admin_user,
            start_time=now + timedelta(days=14),
            end_time=now + timedelta(days=14) + timedelta(hours=1),
            service_code='97153',
            is_recurring=True,
            series_id=series_id,
            status='scheduled'
        )
        
        # Cancel series from current appointment
        response = admin_client.post(
            f'/api/v1/appointments/{current_apt.id}/cancel-series/'
        )
        assert response.status_code == 200
        assert response.json()['cancelled'] == 3  # current + 2 future
        
        # Verify statuses
        past_apt.refresh_from_db()
        current_apt.refresh_from_db()
        future_apt1.refresh_from_db()
        future_apt2.refresh_from_db()
        
        assert past_apt.status == 'attended'  # unchanged
        assert current_apt.status == 'cancelled'
        assert future_apt1.status == 'cancelled'
        assert future_apt2.status == 'cancelled'

    def test_cancel_series_non_recurring(self, admin_client, org, admin_user, sample_client):
        """Test cancel-series endpoint rejects non-recurring appointments."""
        apt = Appointment.objects.create(
            organization=org,
            client=sample_client,
            provider=admin_user,
            start_time=timezone.now(),
            end_time=timezone.now() + timedelta(hours=1),
            is_recurring=False,
            status='scheduled'
        )
        
        response = admin_client.post(
            f'/api/v1/appointments/{apt.id}/cancel-series/'
        )
        assert response.status_code == 400
        assert 'not part of a recurring series' in response.json()['detail']
