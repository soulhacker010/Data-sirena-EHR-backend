"""
Session note endpoint tests.

Tests note creation, listing, updating, signing, and deletion.
"""
import pytest
from rest_framework import status


@pytest.mark.django_db
class TestNoteCreate:
    url = '/api/v1/notes/'

    def test_create_note(self, clinician_client, sample_client):
        """Create a draft session note → 201."""
        resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {
                'objectives': 'Test objectives',
                'interventions': 'Test interventions',
                'client_response': 'Positive response',
                'notes': 'Session went well',
            },
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        assert 'id' in resp.data
        assert resp.data['client_id'] == str(sample_client.id)

    def test_create_note_missing_client(self, clinician_client):
        """Missing client_id → 400."""
        resp = clinician_client.post(self.url, {
            'note_data': {'objectives': 'Test'},
        }, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_note_unauthenticated(self, api_client):
        """No auth → 401."""
        resp = api_client.post(self.url, {}, format='json')
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestNoteList:
    url = '/api/v1/notes/'

    def test_list_notes(self, clinician_client, sample_client):
        """List notes → paginated results."""
        # Create a note first
        clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {'objectives': 'Test'},
        }, format='json')

        resp = clinician_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        assert 'results' in resp.data
        assert resp.data['count'] >= 1


@pytest.mark.django_db
class TestNoteUpdate:
    url = '/api/v1/notes/'

    def test_update_note(self, clinician_client, sample_client):
        """Update note_data → 200."""
        create_resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {'objectives': 'Original'},
        }, format='json')
        assert create_resp.status_code == status.HTTP_201_CREATED
        note_id = create_resp.data['id']

        resp = clinician_client.patch(f'{self.url}{note_id}/', {
            'note_data': {'objectives': 'Updated objectives'},
        }, format='json')
        assert resp.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestNoteSign:
    url = '/api/v1/notes/'

    def test_sign_note(self, clinician_client, sample_client):
        """Sign a draft note → 200 + status becomes 'signed'."""
        create_resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {'objectives': 'Complete session'},
        }, format='json')
        assert create_resp.status_code == status.HTTP_201_CREATED
        note_id = create_resp.data['id']

        resp = clinician_client.post(f'{self.url}{note_id}/sign/', {
            'signature_data': 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUg...',
        }, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['status'] == 'signed'


@pytest.mark.django_db
class TestNoteDelete:
    url = '/api/v1/notes/'

    def test_delete_draft_note(self, clinician_client, sample_client):
        """Delete draft note → 204."""
        create_resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {'objectives': 'To delete'},
        }, format='json')
        assert create_resp.status_code == status.HTTP_201_CREATED
        note_id = create_resp.data['id']

        resp = clinician_client.delete(f'{self.url}{note_id}/')
        assert resp.status_code == status.HTTP_204_NO_CONTENT
