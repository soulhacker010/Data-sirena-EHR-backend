"""
Shared test fixtures for the Sirena EHR backend test suite.

Provides:
- Organization, admin user, clinician user
- JWT auth tokens via api_client helper
- Sample client, appointment, invoice data
"""
import pytest
from rest_framework.test import APIClient
from apps.accounts.models import Organization, User


# ─── Organization & Users ──────────────────────────────────────────────────────

@pytest.fixture
def org(db):
    """Create a test organization."""
    return Organization.objects.create(
        name='Test ABA Clinic',
        tax_id='12-3456789',
        contact_email='admin@testclinic.com',
        contact_phone='555-0100',
        address='123 Test St',
    )


@pytest.fixture
def admin_user(org):
    """Create an admin user."""
    user = User.objects.create_user(
        email='admin@testclinic.com',
        password='testpass123!',
        first_name='Admin',
        last_name='User',
        role='admin',
        organization=org,
    )
    return user


@pytest.fixture
def clinician_user(org):
    """Create a clinician user."""
    user = User.objects.create_user(
        email='clinician@testclinic.com',
        password='testpass123!',
        first_name='Jane',
        last_name='Therapist',
        role='clinician',
        organization=org,
    )
    return user


@pytest.fixture
def other_org(db):
    """Create a second organization for cross-org tests."""
    return Organization.objects.create(
        name='Other Clinic',
        contact_email='other@clinic.com',
    )


@pytest.fixture
def other_admin(other_org):
    """Admin of the other org — for isolation tests."""
    return User.objects.create_user(
        email='other@clinic.com',
        password='testpass123!',
        first_name='Other',
        last_name='Admin',
        role='admin',
        organization=other_org,
    )


# ─── API Clients ───────────────────────────────────────────────────────────────

@pytest.fixture
def api_client():
    """Unauthenticated API client."""
    return APIClient()


@pytest.fixture
def admin_client(admin_user):
    """API client authenticated as admin."""
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.fixture
def clinician_client(clinician_user):
    """API client authenticated as clinician."""
    client = APIClient()
    client.force_authenticate(user=clinician_user)
    return client


@pytest.fixture
def other_admin_client(other_admin):
    """API client authenticated as admin of a different org."""
    client = APIClient()
    client.force_authenticate(user=other_admin)
    return client


# ─── Sample Data ───────────────────────────────────────────────────────────────

@pytest.fixture
def sample_client(org):
    """Create a sample client in the test org."""
    from apps.clients.models import Client
    return Client.objects.create(
        organization=org,
        first_name='John',
        last_name='Doe',
        date_of_birth='2018-05-15',
        gender='male',
        phone='555-0101',
        email='john@example.com',
        address='456 Client Ave',
        city='Testville',
        state='FL',
        zip_code='33101',
    )


@pytest.fixture
def sample_appointment(org, sample_client, clinician_user):
    """Create a sample appointment."""
    from apps.scheduling.models import Appointment
    return Appointment.objects.create(
        organization=org,
        client=sample_client,
        provider=clinician_user,
        start_time='2026-03-01T09:00:00Z',
        end_time='2026-03-01T11:00:00Z',
        service_code='97153',
        units=8,
        status='scheduled',
    )
