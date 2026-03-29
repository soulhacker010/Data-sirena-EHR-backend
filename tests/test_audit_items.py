"""
Tests for all audit items completed during the codebase polish pass.

Items 1-3: Location API, Provider API, Client Search API
Item 4:    Navbar search (frontend-only, no backend test needed)
Item 5:    Notification preferences persistence
Item 6:    Profile phone field in /me endpoint
Item 7:    Clinician dashboard stats scoping
Item 9:    Billing resubmission notes field
"""
import pytest
from decimal import Decimal
from rest_framework import status
from apps.accounts.models import Location, User, NotificationPreference
from apps.clients.models import Client


# ─── Extra Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def locations(org):
    """Create a mix of active/inactive locations for the test org."""
    loc1 = Location.objects.create(
        organization=org,
        name='Main Office',
        address='100 Main St',
        city='Miami',
        state='FL',
        zip_code='33101',
        is_telehealth=False,
        is_active=True,
    )
    loc2 = Location.objects.create(
        organization=org,
        name='Telehealth',
        address='N/A',
        city='',
        state='',
        zip_code='',
        is_telehealth=True,
        is_active=True,
    )
    loc_inactive = Location.objects.create(
        organization=org,
        name='Old Office',
        address='999 Closed Rd',
        city='Tampa',
        state='FL',
        zip_code='33602',
        is_telehealth=False,
        is_active=False,
    )
    return loc1, loc2, loc_inactive


@pytest.fixture
def other_org_location(other_org):
    """Location belonging to a different organization."""
    return Location.objects.create(
        organization=other_org,
        name='Other Clinic Office',
        address='200 Other St',
        city='Orlando',
        state='FL',
        zip_code='32801',
        is_active=True,
    )


@pytest.fixture
def supervisor_user(org):
    """Create a supervisor user."""
    return User.objects.create_user(
        email='supervisor@testclinic.com',
        password='testpass123!',
        first_name='Sarah',
        last_name='Super',
        role='supervisor',
        organization=org,
    )


@pytest.fixture
def biller_user(org):
    """Create a biller user."""
    return User.objects.create_user(
        email='biller@testclinic.com',
        password='testpass123!',
        first_name='Bill',
        last_name='Biller',
        role='biller',
        organization=org,
    )


@pytest.fixture
def front_desk_user(org):
    """Create a front_desk user."""
    return User.objects.create_user(
        email='frontdesk@testclinic.com',
        password='testpass123!',
        first_name='Franny',
        last_name='Desk',
        role='front_desk',
        organization=org,
    )


@pytest.fixture
def biller_client(biller_user):
    """API client authenticated as biller."""
    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(user=biller_user)
    return client


@pytest.fixture
def front_desk_client(front_desk_user):
    """API client authenticated as front_desk."""
    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(user=front_desk_user)
    return client


@pytest.fixture
def multiple_clients(org):
    """Create several clients for search testing."""
    c1 = Client.objects.create(
        organization=org,
        first_name='Alice',
        last_name='Anderson',
        date_of_birth='2015-01-10',
        email='alice@example.com',
        phone='555-1001',
    )
    c2 = Client.objects.create(
        organization=org,
        first_name='Bob',
        last_name='Baker',
        date_of_birth='2016-06-20',
        email='bob@example.com',
        phone='555-1002',
    )
    c3 = Client.objects.create(
        organization=org,
        first_name='Alice',
        last_name='Zimmerman',
        date_of_birth='2017-11-05',
        email='alicez@example.com',
        phone='555-1003',
    )
    return c1, c2, c3


@pytest.fixture
def sample_invoice(org, sample_client):
    """Create a sample invoice for claim tests."""
    from apps.billing.models import Invoice
    return Invoice.objects.create(
        organization=org,
        client=sample_client,
        invoice_number='INV-AUDIT-001',
        invoice_date='2026-03-01',
        total_amount=1000,
        balance=1000,
    )


@pytest.fixture
def denied_claim(org, sample_client, sample_invoice):
    """Create a denied claim for resubmission testing."""
    from apps.billing.models import Claim
    return Claim.objects.create(
        invoice=sample_invoice,
        client=sample_client,
        claim_number='CLM-DENIED-001',
        payer_name='Aetna',
        payer_id='AET001',
        status='denied',
        billed_amount=Decimal('1000.00'),
        denial_reason='Missing modifier on CPT 97153',
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ITEM 1: Location List API — GET /api/v1/auth/locations/
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestLocationListAPI:
    url = '/api/v1/auth/locations/'

    def test_returns_active_locations_only(self, admin_client, locations):
        """Only active locations for the user's org should be returned."""
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        names = [loc['name'] for loc in resp.data]
        assert 'Main Office' in names
        assert 'Telehealth' in names
        assert 'Old Office' not in names  # inactive

    def test_org_isolation(self, admin_client, locations, other_org_location):
        """Locations from other orgs must not appear."""
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        names = [loc['name'] for loc in resp.data]
        assert 'Other Clinic Office' not in names

    def test_clinician_can_access(self, clinician_client, locations):
        """Any authenticated user (including clinician) can list locations."""
        resp = clinician_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) == 2  # 2 active

    def test_unauthenticated_denied(self, api_client, locations):
        """Unauthenticated → 401."""
        resp = api_client.get(self.url)
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_response_fields(self, admin_client, locations):
        """Response includes all expected fields."""
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        loc = resp.data[0]
        expected_fields = {'id', 'name', 'address', 'city', 'state', 'zip_code', 'is_telehealth', 'is_active'}
        assert expected_fields.issubset(set(loc.keys()))

    def test_no_pagination(self, admin_client, locations):
        """Response is a flat list (no pagination wrapper)."""
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        assert isinstance(resp.data, list)


# ═══════════════════════════════════════════════════════════════════════════════
# ITEM 2: Provider List API — GET /api/v1/auth/providers/
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestProviderListAPI:
    url = '/api/v1/auth/providers/'

    def test_returns_clinical_staff(self, admin_client, admin_user, clinician_user, supervisor_user):
        """Returns admin, clinician, and supervisor users."""
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        names = [p['name'] for p in resp.data]
        assert 'Admin User' in names
        assert 'Jane Therapist' in names
        assert 'Sarah Super' in names

    def test_excludes_non_clinical_roles(self, admin_client, admin_user, biller_user, front_desk_user):
        """Billers and front desk should not appear in provider list."""
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        names = [p['name'] for p in resp.data]
        assert 'Bill Biller' not in names
        assert 'Franny Desk' not in names

    def test_org_isolation(self, admin_client, admin_user, other_admin):
        """Providers from other orgs must not appear."""
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        names = [p['name'] for p in resp.data]
        assert 'Other Admin' not in names

    def test_clinician_can_access(self, clinician_client, clinician_user):
        """Any authenticated user can list providers."""
        resp = clinician_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK

    def test_unauthenticated_denied(self, api_client):
        """Unauthenticated → 401."""
        resp = api_client.get(self.url)
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_response_fields(self, admin_client, admin_user):
        """Response includes expected provider fields."""
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        provider = resp.data[0]
        expected_fields = {'id', 'name', 'first_name', 'last_name', 'role', 'credentials'}
        assert expected_fields.issubset(set(provider.keys()))

    def test_no_pagination(self, admin_client, admin_user):
        """Response is a flat list (no pagination wrapper)."""
        resp = admin_client.get(self.url)
        assert isinstance(resp.data, list)

    def test_excludes_inactive_users(self, admin_client, org):
        """Inactive users should not appear in provider list."""
        inactive = User.objects.create_user(
            email='inactive@testclinic.com',
            password='testpass123!',
            first_name='Gone',
            last_name='Provider',
            role='clinician',
            organization=org,
            is_active=False,
        )
        resp = admin_client.get(self.url)
        names = [p['name'] for p in resp.data]
        assert f'{inactive.first_name} {inactive.last_name}' not in names


# ═══════════════════════════════════════════════════════════════════════════════
# ITEM 3: Client Search API — GET /api/v1/clients/?search=
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestClientSearchAPI:
    url = '/api/v1/clients/'

    def test_search_by_first_name(self, admin_client, multiple_clients):
        """Search by first name returns matching clients."""
        resp = admin_client.get(self.url, {'search': 'Alice'})
        assert resp.status_code == status.HTTP_200_OK
        results = resp.data['results']
        assert len(results) == 2  # Alice Anderson + Alice Zimmerman

    def test_search_by_last_name(self, admin_client, multiple_clients):
        """Search by last name returns matching client."""
        resp = admin_client.get(self.url, {'search': 'Baker'})
        assert resp.status_code == status.HTTP_200_OK
        results = resp.data['results']
        assert len(results) == 1
        assert results[0]['last_name'] == 'Baker'

    def test_search_by_email(self, admin_client, multiple_clients):
        """Search by email returns matching client."""
        resp = admin_client.get(self.url, {'search': 'bob@example.com'})
        assert resp.status_code == status.HTTP_200_OK
        results = resp.data['results']
        assert len(results) == 1
        assert results[0]['first_name'] == 'Bob'

    def test_search_by_phone(self, admin_client, multiple_clients):
        """Search by phone returns matching client."""
        resp = admin_client.get(self.url, {'search': '555-1003'})
        assert resp.status_code == status.HTTP_200_OK
        results = resp.data['results']
        assert len(results) == 1
        assert results[0]['last_name'] == 'Zimmerman'

    def test_search_no_match(self, admin_client, multiple_clients):
        """Search with no match returns empty results."""
        resp = admin_client.get(self.url, {'search': 'Nonexistent'})
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data['results']) == 0

    def test_search_empty_query(self, admin_client, multiple_clients):
        """Empty search returns all clients."""
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data['results']) == 3

    def test_search_org_isolation(self, admin_client, multiple_clients, other_org):
        """Clients from other orgs should not appear in search."""
        Client.objects.create(
            organization=other_org,
            first_name='Alice',
            last_name='OtherOrg',
            date_of_birth='2018-01-01',
        )
        resp = admin_client.get(self.url, {'search': 'Alice'})
        results = resp.data['results']
        last_names = [c['last_name'] for c in results]
        assert 'OtherOrg' not in last_names

    def test_search_unauthenticated(self, api_client):
        """Unauthenticated → 401."""
        resp = api_client.get(self.url, {'search': 'test'})
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ═══════════════════════════════════════════════════════════════════════════════
# ITEM 5: Notification Preferences Persistence
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestNotificationPreferences:
    url = '/api/v1/auth/notifications/preferences/'

    def test_get_creates_defaults(self, admin_client, admin_user):
        """GET auto-creates default preferences if none exist."""
        assert not NotificationPreference.objects.filter(user=admin_user).exists()
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['email_appointments'] is True
        assert resp.data['email_billing'] is True
        assert resp.data['email_notes'] is False
        assert resp.data['sms_reminders'] is True
        assert resp.data['auth_alerts'] is True
        assert resp.data['denial_alerts'] is True
        assert NotificationPreference.objects.filter(user=admin_user).exists()

    def test_update_preferences(self, admin_client, admin_user):
        """PUT updates preferences and persists them."""
        resp = admin_client.put(self.url, {
            'email_appointments': False,
            'email_notes': True,
        }, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['email_appointments'] is False
        assert resp.data['email_notes'] is True

        # Verify persistence
        prefs = NotificationPreference.objects.get(user=admin_user)
        assert prefs.email_appointments is False
        assert prefs.email_notes is True
        # Unchanged fields keep defaults
        assert prefs.email_billing is True

    def test_partial_update(self, admin_client, admin_user):
        """PUT with partial data only updates specified fields."""
        # First create defaults
        admin_client.get(self.url)

        resp = admin_client.put(self.url, {
            'denial_alerts': False,
        }, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['denial_alerts'] is False
        assert resp.data['email_appointments'] is True  # untouched

    def test_unauthenticated_denied(self, api_client):
        """Unauthenticated → 401."""
        resp = api_client.get(self.url)
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_per_user_isolation(self, admin_client, clinician_client, admin_user, clinician_user):
        """Each user has their own preferences."""
        admin_client.put(self.url, {'email_notes': True}, format='json')
        resp = clinician_client.get(self.url)
        assert resp.data['email_notes'] is False  # clinician gets defaults


# ═══════════════════════════════════════════════════════════════════════════════
# ITEM 6: Profile Phone Field in /me Endpoint
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestProfilePhoneField:
    url = '/api/v1/auth/me/'

    def test_me_includes_phone(self, org):
        """GET /me should include the phone field from the user model."""
        from rest_framework.test import APIClient
        user = User.objects.create_user(
            email='phoneguy@testclinic.com',
            password='testpass123!',
            first_name='Phone',
            last_name='Guy',
            role='clinician',
            organization=org,
            phone='555-9999',
        )
        client = APIClient()
        client.force_authenticate(user=user)
        resp = client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data.get('phone') == '555-9999'

    def test_me_phone_empty(self, admin_client, admin_user):
        """Phone field is present even when empty."""
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        assert 'phone' in resp.data


# ═══════════════════════════════════════════════════════════════════════════════
# ITEM 7: Clinician Dashboard Stats Scoping
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestDashboardStatsScoping:
    url = '/api/v1/dashboard/stats/'

    def test_admin_sees_all_clients(self, admin_client, org):
        """Admin sees all active clients in the org."""
        Client.objects.create(organization=org, first_name='A', last_name='One', date_of_birth='2020-01-01')
        Client.objects.create(organization=org, first_name='B', last_name='Two', date_of_birth='2020-01-01')
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['total_clients'] == 2

    def test_clinician_sees_only_own_clients(self, clinician_client, clinician_user, org):
        """Clinician sees only clients they have appointments with."""
        from apps.scheduling.models import Appointment
        c1 = Client.objects.create(organization=org, first_name='My', last_name='Client', date_of_birth='2020-01-01')
        Client.objects.create(organization=org, first_name='Not', last_name='Mine', date_of_birth='2020-01-01')
        Appointment.objects.create(
            organization=org,
            client=c1,
            provider=clinician_user,
            start_time='2026-03-01T09:00:00Z',
            end_time='2026-03-01T10:00:00Z',
            service_code='97153',
            units=4,
            status='scheduled',
        )
        resp = clinician_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['total_clients'] == 1

    def test_clinician_sees_own_upcoming(self, clinician_client, clinician_user, admin_user, org):
        """Clinician only sees their own upcoming appointments."""
        from apps.scheduling.models import Appointment
        from django.utils import timezone
        from datetime import timedelta
        now = timezone.now()
        c = Client.objects.create(organization=org, first_name='Shared', last_name='Client', date_of_birth='2020-01-01')

        # Clinician's appointment
        Appointment.objects.create(
            organization=org, client=c, provider=clinician_user,
            start_time=now + timedelta(days=1), end_time=now + timedelta(days=1, hours=1),
            service_code='97153', units=4, status='scheduled',
        )
        # Admin's appointment (clinician should NOT see this)
        Appointment.objects.create(
            organization=org, client=c, provider=admin_user,
            start_time=now + timedelta(days=2), end_time=now + timedelta(days=2, hours=1),
            service_code='97153', units=4, status='scheduled',
        )

        resp = clinician_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        upcoming = resp.data['upcoming_appointments']
        assert len(upcoming) == 1
        assert upcoming[0]['provider_name'] == clinician_user.full_name

    def test_admin_sees_all_upcoming(self, admin_client, admin_user, clinician_user, org):
        """Admin sees all upcoming appointments in the org."""
        from apps.scheduling.models import Appointment
        from django.utils import timezone
        from datetime import timedelta
        now = timezone.now()
        c = Client.objects.create(organization=org, first_name='Test', last_name='Kid', date_of_birth='2020-01-01')

        Appointment.objects.create(
            organization=org, client=c, provider=clinician_user,
            start_time=now + timedelta(days=1), end_time=now + timedelta(days=1, hours=1),
            service_code='97153', units=4, status='scheduled',
        )
        Appointment.objects.create(
            organization=org, client=c, provider=admin_user,
            start_time=now + timedelta(days=2), end_time=now + timedelta(days=2, hours=1),
            service_code='97153', units=4, status='scheduled',
        )

        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data['upcoming_appointments']) == 2

    def test_dashboard_unauthenticated(self, api_client):
        """Unauthenticated → 401."""
        resp = api_client.get(self.url)
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ═══════════════════════════════════════════════════════════════════════════════
# ITEM 9: Billing Resubmission Notes
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestClaimResubmissionNotes:
    def submit_url(self, claim_id):
        return f'/api/v1/claims/{claim_id}/submit/'

    def test_resubmit_denied_claim_with_notes(self, admin_client, denied_claim):
        """Resubmitting a denied claim with notes saves them."""
        resp = admin_client.post(self.submit_url(denied_claim.id), {
            'resubmission_notes': 'Added modifier 97 to CPT code',
        }, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['status'] == 'resubmitted'
        assert resp.data['resubmission_notes'] == 'Added modifier 97 to CPT code'
        assert resp.data['resubmission_count'] == 1

    def test_resubmit_without_notes(self, admin_client, denied_claim):
        """Resubmitting without notes still works (notes stay empty)."""
        resp = admin_client.post(self.submit_url(denied_claim.id), format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['status'] == 'resubmitted'
        assert resp.data['resubmission_notes'] == ''

    def test_submit_created_claim_ignores_notes(self, admin_client, sample_invoice, sample_client):
        """Submitting a created claim ignores resubmission_notes."""
        from apps.billing.models import Claim
        created_claim = Claim.objects.create(
            invoice=sample_invoice,
            client=sample_client,
            claim_number='CLM-NEW-001',
            payer_name='Cigna',
            status='created',
            billed_amount=Decimal('500.00'),
        )
        resp = admin_client.post(self.submit_url(created_claim.id), {
            'resubmission_notes': 'should be ignored',
        }, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['status'] == 'submitted'
        assert resp.data['resubmission_notes'] == ''  # not saved for initial submit

    def test_resubmission_notes_in_claim_detail(self, admin_client, denied_claim):
        """resubmission_notes field is present in claim list/detail responses."""
        resp = admin_client.get('/api/v1/claims/')
        assert resp.status_code == status.HTTP_200_OK
        if resp.data['results']:
            assert 'resubmission_notes' in resp.data['results'][0]

    def test_cannot_submit_paid_claim(self, admin_client, sample_invoice, sample_client):
        """Submitting a paid claim returns 400."""
        from apps.billing.models import Claim
        paid_claim = Claim.objects.create(
            invoice=sample_invoice,
            client=sample_client,
            claim_number='CLM-PAID-001',
            payer_name='UHC',
            status='paid',
            billed_amount=Decimal('500.00'),
        )
        resp = admin_client.post(self.submit_url(paid_claim.id), format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
