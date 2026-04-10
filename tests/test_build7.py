"""
Tests for BUILD 7 — Search & Navigation

BUILD 7.1: Search by service type (filter session notes by service_code)
BUILD 7.2: Global search by client name (client search API)
"""

import pytest
from django.urls import reverse
from rest_framework import status

from apps.accounts.models import Organization, User
from apps.clients.models import Client
from apps.scheduling.models import Appointment
from apps.clinical.models import SessionNote


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def organization(db):
    return Organization.objects.create(name='Test Clinic')


@pytest.fixture
def provider(organization):
    return User.objects.create_user(
        email='provider@test.com',
        password='testpass123',
        first_name='Dr',
        last_name='Smith',
        role='clinician',
        organization=organization,
    )


@pytest.fixture
def admin_user(organization):
    return User.objects.create_user(
        email='admin@test.com',
        password='testpass123',
        first_name='Admin',
        last_name='User',
        role='admin',
        organization=organization,
    )


@pytest.fixture
def admin_client(admin_user):
    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.fixture
def clinician_client(provider):
    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(user=provider)
    return client


@pytest.fixture
def client_john(organization):
    return Client.objects.create(
        organization=organization,
        first_name='John',
        last_name='Doe',
        date_of_birth='2010-01-15',
    )


@pytest.fixture
def client_jane(organization):
    return Client.objects.create(
        organization=organization,
        first_name='Jane',
        last_name='Williams',
        date_of_birth='2012-06-20',
    )


@pytest.fixture
def client_maria(organization):
    return Client.objects.create(
        organization=organization,
        first_name='Maria',
        last_name='Johnson',
        date_of_birth='2015-03-10',
    )


@pytest.fixture
def appointment_aba(organization, client_john, provider):
    """ABA appointment with service_code 97153."""
    return Appointment.objects.create(
        organization=organization,
        client=client_john,
        provider=provider,
        start_time='2026-04-10T09:00:00Z',
        end_time='2026-04-10T11:00:00Z',
        service_code='97153',
        units=8,
        status='attended',
    )


@pytest.fixture
def appointment_therapy(organization, client_jane, provider):
    """Psychotherapy appointment with service_code 90834."""
    return Appointment.objects.create(
        organization=organization,
        client=client_jane,
        provider=provider,
        start_time='2026-04-10T13:00:00Z',
        end_time='2026-04-10T13:45:00Z',
        service_code='90834',
        units=1,
        status='attended',
    )


@pytest.fixture
def note_aba(client_john, provider, appointment_aba):
    """Session note linked to ABA appointment (97153)."""
    return SessionNote.objects.create(
        client=client_john,
        provider=provider,
        appointment=appointment_aba,
        note_data={'objectives': 'ABA session goals'},
        status='draft',
    )


@pytest.fixture
def note_therapy(client_jane, provider, appointment_therapy):
    """Session note linked to therapy appointment (90834)."""
    return SessionNote.objects.create(
        client=client_jane,
        provider=provider,
        appointment=appointment_therapy,
        note_data={'objectives': 'Therapy session goals'},
        status='draft',
    )


@pytest.fixture
def note_standalone(client_maria, provider):
    """Session note with service_code in note_data (no appointment)."""
    return SessionNote.objects.create(
        client=client_maria,
        provider=provider,
        appointment=None,
        note_data={'objectives': 'Family session', 'service_code': '90847'},
        status='draft',
    )


# ─── BUILD 7.1: Search by Service Type ───────────────────────────────────────

@pytest.mark.django_db
class TestBuild71_SearchByServiceType:
    """Test filtering session notes by service_code."""

    def test_filter_notes_by_appointment_service_code(
        self, admin_client, note_aba, note_therapy
    ):
        """Filter notes by service_code on the linked appointment."""
        url = reverse('session-note-list')
        response = admin_client.get(url, {'service_code': '97153'})
        assert response.status_code == 200
        ids = [n['id'] for n in response.data['results']]
        assert str(note_aba.id) in ids
        assert str(note_therapy.id) not in ids

    def test_filter_notes_by_notedata_service_code(
        self, admin_client, note_aba, note_standalone
    ):
        """Filter notes by service_code stored in note_data JSON."""
        url = reverse('session-note-list')
        response = admin_client.get(url, {'service_code': '90847'})
        assert response.status_code == 200
        ids = [n['id'] for n in response.data['results']]
        assert str(note_standalone.id) in ids
        assert str(note_aba.id) not in ids

    def test_filter_returns_both_sources(
        self, admin_client, note_aba, note_therapy, note_standalone
    ):
        """No service_code filter returns all notes."""
        url = reverse('session-note-list')
        response = admin_client.get(url)
        assert response.status_code == 200
        assert len(response.data['results']) == 3

    def test_filter_nonexistent_service_code(self, admin_client, note_aba):
        """Filtering by a service_code that no note has returns empty."""
        url = reverse('session-note-list')
        response = admin_client.get(url, {'service_code': '99999'})
        assert response.status_code == 200
        assert len(response.data['results']) == 0

    def test_service_code_filter_combined_with_status(
        self, admin_client, note_aba, note_therapy
    ):
        """Combine service_code with status filter."""
        url = reverse('session-note-list')
        response = admin_client.get(url, {
            'service_code': '97153',
            'status': 'draft',
        })
        assert response.status_code == 200
        ids = [n['id'] for n in response.data['results']]
        assert str(note_aba.id) in ids

    def test_service_code_in_serializer_response(
        self, admin_client, note_aba
    ):
        """Verify service_code is included in the list response."""
        url = reverse('session-note-list')
        response = admin_client.get(url, {'service_code': '97153'})
        assert response.status_code == 200
        note_data = response.data['results'][0]
        assert note_data['service_code'] == '97153'

    def test_clinician_sees_only_own_notes_with_filter(
        self, clinician_client, note_aba, note_therapy, provider
    ):
        """Clinician filtering by service_code only sees their own notes."""
        url = reverse('session-note-list')
        response = clinician_client.get(url, {'service_code': '97153'})
        assert response.status_code == 200
        # Provider owns note_aba so it should appear
        ids = [n['id'] for n in response.data['results']]
        assert str(note_aba.id) in ids


# ─── BUILD 7.2: Global Search by Client Name ─────────────────────────────────

@pytest.mark.django_db
class TestBuild72_GlobalClientSearch:
    """Test the client search API used by the global navbar search."""

    def test_search_client_by_first_name(
        self, admin_client, client_john, client_jane
    ):
        """Search clients by first name."""
        url = reverse('client-list')
        response = admin_client.get(url, {'search': 'John'})
        assert response.status_code == 200
        names = [f"{c['first_name']} {c['last_name']}" for c in response.data['results']]
        assert 'John Doe' in names
        assert 'Jane Williams' not in names

    def test_search_client_by_last_name(
        self, admin_client, client_john, client_jane, client_maria
    ):
        """Search clients by last name."""
        url = reverse('client-list')
        response = admin_client.get(url, {'search': 'Williams'})
        assert response.status_code == 200
        names = [f"{c['first_name']} {c['last_name']}" for c in response.data['results']]
        assert 'Jane Williams' in names
        assert len(names) == 1

    def test_search_client_partial_match(
        self, admin_client, client_john, client_jane, client_maria
    ):
        """Partial search term matches."""
        url = reverse('client-list')
        response = admin_client.get(url, {'search': 'Jo'})
        assert response.status_code == 200
        names = [f"{c['first_name']} {c['last_name']}" for c in response.data['results']]
        # "Jo" matches John Doe and Maria Johnson
        assert 'John Doe' in names
        assert 'Maria Johnson' in names

    def test_search_returns_empty_for_no_match(
        self, admin_client, client_john
    ):
        """Search with no match returns empty results."""
        url = reverse('client-list')
        response = admin_client.get(url, {'search': 'ZZZZZ'})
        assert response.status_code == 200
        assert len(response.data['results']) == 0

    def test_search_with_page_size(
        self, admin_client, client_john, client_jane, client_maria
    ):
        """Search respects page_size parameter."""
        url = reverse('client-list')
        response = admin_client.get(url, {'search': '', 'page_size': 2})
        assert response.status_code == 200
        assert len(response.data['results']) <= 2

    def test_search_returns_expected_fields(
        self, admin_client, client_john
    ):
        """Verify search response contains fields needed by navbar dropdown."""
        url = reverse('client-list')
        response = admin_client.get(url, {'search': 'John'})
        assert response.status_code == 200
        assert len(response.data['results']) > 0
        client_data = response.data['results'][0]
        # Navbar needs: id, first_name, last_name, date_of_birth
        assert 'id' in client_data
        assert 'first_name' in client_data
        assert 'last_name' in client_data
        assert 'date_of_birth' in client_data

    def test_search_scoped_to_organization(
        self, admin_client, client_john
    ):
        """Clients from other orgs should not appear in search."""
        other_org = Organization.objects.create(name='Other Clinic')
        Client.objects.create(
            organization=other_org,
            first_name='John',
            last_name='OtherOrg',
            date_of_birth='2010-01-01',
        )
        url = reverse('client-list')
        response = admin_client.get(url, {'search': 'John'})
        assert response.status_code == 200
        names = [f"{c['first_name']} {c['last_name']}" for c in response.data['results']]
        assert 'John OtherOrg' not in names
        assert 'John Doe' in names
