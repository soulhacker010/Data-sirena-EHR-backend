"""
Client CRUD endpoint tests.

Tests client creation, listing, detail, update, delete, and org isolation.
"""
import pytest
from rest_framework import status


@pytest.mark.django_db
class TestClientCreate:
    url = '/api/v1/clients/'

    def test_create_client(self, admin_client):
        """Create client with valid data → 201 + client data returned."""
        resp = admin_client.post(self.url, {
            'first_name': 'Alice',
            'last_name': 'Smith',
            'date_of_birth': '2016-03-20',
            'gender': 'female',
            'phone': '555-1234',
            'email': 'alice@example.com',
            'address': '789 Oak St',
            'city': 'Tampa',
            'state': 'FL',
            'zip_code': '33602',
        })
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data['first_name'] == 'Alice'
        assert resp.data['last_name'] == 'Smith'
        assert 'id' in resp.data

    def test_create_client_missing_required(self, admin_client):
        """Missing required fields → 400."""
        resp = admin_client.post(self.url, {
            'first_name': 'Alice',
            # Missing last_name, date_of_birth
        })
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_client_unauthenticated(self, api_client):
        """No auth → 401."""
        resp = api_client.post(self.url, {
            'first_name': 'X',
            'last_name': 'Y',
            'date_of_birth': '2020-01-01',
        })
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestClientList:
    url = '/api/v1/clients/'

    def test_list_clients(self, admin_client, sample_client):
        """Lists clients in the user's org."""
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        assert 'results' in resp.data
        assert resp.data['count'] >= 1

    def test_list_clients_scoped_to_org(self, other_admin_client, sample_client):
        """Other org's admin can't see our clients."""
        resp = other_admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        # sample_client belongs to a different org
        client_ids = [c['id'] for c in resp.data['results']]
        assert str(sample_client.id) not in client_ids


@pytest.mark.django_db
class TestClientDetail:
    def test_get_client_detail(self, admin_client, sample_client):
        """Get client detail → rich data with authorizations."""
        url = f'/api/v1/clients/{sample_client.id}/'
        resp = admin_client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['first_name'] == 'John'
        assert resp.data['last_name'] == 'Doe'
        assert 'authorizations' in resp.data

    def test_get_client_wrong_org(self, other_admin_client, sample_client):
        """Can't access client from another org → 404."""
        url = f'/api/v1/clients/{sample_client.id}/'
        resp = other_admin_client.get(url)
        assert resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestClientUpdate:
    def test_update_client(self, admin_client, sample_client):
        """Update client fields → 200."""
        url = f'/api/v1/clients/{sample_client.id}/'
        resp = admin_client.patch(url, {
            'phone': '555-9999',
            'city': 'Miami',
        })
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['phone'] == '555-9999'
        assert resp.data['city'] == 'Miami'


@pytest.mark.django_db
class TestServiceCategories:
    """E21 (Dr. Joe): clients can carry multiple service-category labels
    (Psych, OT, Speech, etc.) so the same person doesn't need duplicate rows
    when they receive multiple services. Backend validates the slug list and
    dedupes."""

    def test_create_client_with_service_categories(self, admin_client, org):
        resp = admin_client.post('/api/v1/clients/', {
            'first_name': 'Multi',
            'last_name': 'Service',
            'date_of_birth': '2010-01-01',
            'gender': 'female',
            'service_categories': ['psychotherapy', 'occupational'],
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data['service_categories'] == ['psychotherapy', 'occupational']

    def test_update_client_service_categories(self, admin_client, sample_client):
        resp = admin_client.patch(
            f'/api/v1/clients/{sample_client.id}/',
            {'service_categories': ['behavior', 'speech']},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['service_categories'] == ['behavior', 'speech']

    def test_unknown_category_rejected(self, admin_client, sample_client):
        resp = admin_client.patch(
            f'/api/v1/clients/{sample_client.id}/',
            {'service_categories': ['psychotherapy', 'not_a_real_category']},
            format='json',
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_dedupes_repeated_categories(self, admin_client, sample_client):
        resp = admin_client.patch(
            f'/api/v1/clients/{sample_client.id}/',
            {'service_categories': ['psychotherapy', 'psychotherapy', 'occupational']},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['service_categories'] == ['psychotherapy', 'occupational']

    def test_empty_list_allowed(self, admin_client, sample_client):
        resp = admin_client.patch(
            f'/api/v1/clients/{sample_client.id}/',
            {'service_categories': []},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['service_categories'] == []


@pytest.mark.django_db
class TestClientDelete:
    def test_delete_client_soft(self, admin_client, sample_client):
        """Delete client → soft delete (is_active=False), returns 204."""
        url = f'/api/v1/clients/{sample_client.id}/'
        resp = admin_client.delete(url)
        assert resp.status_code == status.HTTP_204_NO_CONTENT

        # Client still exists but is deactivated
        sample_client.refresh_from_db()
        assert sample_client.is_active is False


@pytest.mark.django_db
class TestChartNumberGeneration:
    """E2: Each new client should have a chart number assigned automatically
    with initials first then chart number like XX000001.

    Numeric max — not string max — must drive the suffix; otherwise high-
    initial MRNs (Z*) outrank earlier high-suffix MRNs (A*999) alphabetically
    and the next client collides with an existing one.
    """

    def _make_client(self, org, first, last, *, mrn=''):
        from apps.clients.models import Client
        return Client.objects.create(
            organization=org,
            first_name=first,
            last_name=last,
            date_of_birth='1990-01-01',
            mrn=mrn,
        )

    def test_first_client_gets_initials_plus_000001(self, org):
        c = self._make_client(org, 'John', 'Smith')
        assert c.mrn == 'JS000001'

    def test_format_is_initials_then_six_digits(self, org):
        c = self._make_client(org, 'Jane', 'Doe')
        assert len(c.mrn) == 8
        assert c.mrn[:2].isalpha()
        assert c.mrn[:2].isupper()
        assert c.mrn[2:].isdigit()

    def test_sequence_increments_globally_within_org(self, org):
        c1 = self._make_client(org, 'John', 'Smith')   # JS000001
        c2 = self._make_client(org, 'Jane', 'Doe')     # JD000002
        c3 = self._make_client(org, 'Mary', 'Adams')   # MA000003
        assert c1.mrn == 'JS000001'
        assert c2.mrn == 'JD000002'
        assert c3.mrn == 'MA000003'

    def test_no_collision_when_string_order_disagrees_with_numeric(self, org):
        """The bug fix: previously 'JS000001' (J > A alphabetically) would beat
        a later 'AA000002' in string ordering, leading the next client to be
        assigned suffix 000002 — colliding with the existing AA000002. The
        numeric-max approach must avoid this.
        """
        self._make_client(org, 'John', 'Smith')   # JS000001
        self._make_client(org, 'Alice', 'Adams')  # AA000002
        next_client = self._make_client(org, 'Zeb', 'Zorro')
        assert next_client.mrn == 'ZZ000003'  # not ZZ000002 — would collide

        # No duplicate suffixes anywhere in the org.
        from apps.clients.models import Client
        suffixes = [
            c.mrn[-6:] for c in Client.objects.filter(organization=org)
        ]
        assert len(suffixes) == len(set(suffixes)), (
            f'Duplicate MRN suffixes in org: {suffixes}'
        )

    def test_explicit_mrn_is_preserved(self, org):
        """If a client already has an MRN (e.g. imported), don't overwrite."""
        c = self._make_client(org, 'John', 'Smith', mrn='LEGACY-99999')
        assert c.mrn == 'LEGACY-99999'

    def test_org_scope_isolates_sequences(self, org, other_org):
        """Two orgs run independent chart number sequences."""
        a = self._make_client(org, 'John', 'Smith')
        b = self._make_client(other_org, 'John', 'Smith')
        assert a.mrn == 'JS000001'
        assert b.mrn == 'JS000001'  # different org, sequence resets

    def test_missing_initials_uses_x_placeholder(self, org):
        """A client missing first/last name (edge case) still gets a valid MRN."""
        c = self._make_client(org, '', '')
        assert c.mrn == 'XX000001'

    def test_increments_correctly_after_legacy_imports(self, org):
        """If existing rows have non-standard MRN formats, skip them when
        computing max but still increment from the highest numeric suffix
        we can recognise."""
        self._make_client(org, 'Old', 'Import', mrn='LEGACY-001')   # ignored
        self._make_client(org, 'A', 'B', mrn='AB000050')             # numeric
        c = self._make_client(org, 'C', 'D')
        assert c.mrn == 'CD000051'
