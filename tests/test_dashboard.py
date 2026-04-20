"""
Tests for Dashboard Stats — item 6.1 (Total Clients = 0 bug) and 6.3 (pending notes logic).
"""
import pytest
from django.urls import reverse
from rest_framework import status

from apps.accounts.models import Organization, User
from apps.clients.models import Client
from apps.scheduling.models import Appointment
from apps.clinical.models import SessionNote

import datetime


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def org(db):
    return Organization.objects.create(name='Test Clinic')


@pytest.fixture
def admin(org):
    return User.objects.create_user(
        email='admin@test.com',
        password='pass',
        first_name='Admin',
        last_name='User',
        role='admin',
        organization=org,
    )


@pytest.fixture
def clinician(org):
    return User.objects.create_user(
        email='clinician@test.com',
        password='pass',
        first_name='Dr',
        last_name='Jones',
        role='clinician',
        organization=org,
    )


@pytest.fixture
def client_record(org):
    return Client.objects.create(
        organization=org,
        first_name='Jane',
        last_name='Doe',
        date_of_birth='1990-01-01',
        is_active=True,
    )


@pytest.fixture
def appointment(org, clinician, client_record):
    return Appointment.objects.create(
        organization=org,
        client=client_record,
        provider=clinician,
        start_time=datetime.datetime(2026, 4, 10, 10, 0, tzinfo=datetime.timezone.utc),
        end_time=datetime.datetime(2026, 4, 10, 11, 0, tzinfo=datetime.timezone.utc),
        service_code='90837',
        status='attended',
    )


# ─── 6.1: Total Clients ───────────────────────────────────────────────────────

@pytest.mark.django_db
def test_dashboard_total_clients_returns_correct_count(api_client, admin, client_record, org):
    """Total Clients must reflect org-wide active clients, not 0."""
    Client.objects.create(
        organization=org, first_name='John', last_name='Smith',
        date_of_birth='1985-05-05', is_active=True,
    )
    api_client.force_authenticate(user=admin)
    response = api_client.get(reverse('dashboard-stats'))
    assert response.status_code == status.HTTP_200_OK
    assert response.data['total_clients'] == 2


@pytest.mark.django_db
def test_dashboard_excludes_inactive_clients(api_client, admin, org):
    """Inactive clients should not count toward total."""
    Client.objects.create(
        organization=org, first_name='Active', last_name='Client',
        date_of_birth='1990-01-01', is_active=True,
    )
    Client.objects.create(
        organization=org, first_name='Inactive', last_name='Client',
        date_of_birth='1990-01-01', is_active=False,
    )
    api_client.force_authenticate(user=admin)
    response = api_client.get(reverse('dashboard-stats'))
    assert response.data['total_clients'] == 1


@pytest.mark.django_db
def test_dashboard_org_scoping(api_client, org, admin):
    """Clients from a different org must not be counted."""
    other_org = Organization.objects.create(name='Other Clinic')
    Client.objects.create(
        organization=other_org, first_name='Other', last_name='Client',
        date_of_birth='1990-01-01', is_active=True,
    )
    api_client.force_authenticate(user=admin)
    response = api_client.get(reverse('dashboard-stats'))
    assert response.data['total_clients'] == 0


# ─── 6.3: Pending Notes ──────────────────────────────────────────────────────

@pytest.mark.django_db
def test_pending_notes_increments_after_attended_appointment(
    api_client, admin, appointment
):
    """An attended appointment with no note should appear in pending_notes."""
    api_client.force_authenticate(user=admin)
    response = api_client.get(reverse('dashboard-stats'))
    assert response.status_code == status.HTTP_200_OK
    assert response.data['pending_notes'] >= 1


@pytest.mark.django_db
def test_pending_notes_not_counted_when_signed(
    api_client, admin, appointment, clinician, client_record, org
):
    """An attended appointment with a signed note should NOT add to pending_notes."""
    SessionNote.objects.create(
        client=client_record,
        provider=clinician,
        appointment=appointment,
        status='signed',
        note_data={},
    )
    api_client.force_authenticate(user=admin)
    response = api_client.get(reverse('dashboard-stats'))
    assert response.status_code == status.HTTP_200_OK
    assert response.data['pending_notes'] == 0


@pytest.mark.django_db
def test_pending_notes_counts_draft_notes(
    api_client, admin, appointment, clinician, client_record, org
):
    """A draft note that exists should still count as pending."""
    SessionNote.objects.create(
        client=client_record,
        provider=clinician,
        appointment=appointment,
        status='draft',
        note_data={},
    )
    api_client.force_authenticate(user=admin)
    response = api_client.get(reverse('dashboard-stats'))
    assert response.data['pending_notes'] >= 1


# ─── No-org guard ────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_dashboard_returns_400_for_user_without_org(api_client, db):
    """If the user has no org, the dashboard should return 400, not crash."""
    no_org_user = User.objects.create_user(
        email='noorg@test.com',
        password='pass',
        first_name='No',
        last_name='Org',
        role='admin',
        organization=None,
    )
    api_client.force_authenticate(user=no_org_user)
    response = api_client.get(reverse('dashboard-stats'))
    assert response.status_code == status.HTTP_400_BAD_REQUEST
