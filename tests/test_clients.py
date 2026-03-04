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
class TestClientDelete:
    def test_delete_client_soft(self, admin_client, sample_client):
        """Delete client → soft delete (is_active=False), returns 204."""
        url = f'/api/v1/clients/{sample_client.id}/'
        resp = admin_client.delete(url)
        assert resp.status_code == status.HTTP_204_NO_CONTENT

        # Client still exists but is deactivated
        sample_client.refresh_from_db()
        assert sample_client.is_active is False
