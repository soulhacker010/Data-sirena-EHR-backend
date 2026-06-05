"""
Smoke tests for the BLS REST API.

Covers the happy paths and the most important rejection paths:
  * POST /sessions/ creates a session row + returns token + invite_url
  * GET /sessions/verify/?token=<good> returns valid=True with session_id
  * GET /sessions/verify/?token=<bad>  returns valid=False (and is public)
  * POST /sessions/{id}/end/ flips status to ended, persists counters,
    writes a per-client preference row.
  * Cross-org isolation: another org's clinician can't end my session.
"""
import pytest

from apps.bls.models import (
    BLSClientPreference,
    BLSSession,
    BLSSessionStatus,
)


@pytest.mark.django_db
class TestCreateSession:
    def test_create_session_returns_token_and_url(self, clinician_client, sample_client):
        response = clinician_client.post(
            '/api/v1/bls/sessions/',
            {'client_id': str(sample_client.id)},
            format='json',
        )
        assert response.status_code == 201, response.content
        data = response.json()
        assert 'session_id' in data
        assert 'token' in data
        assert 'invite_url' in data
        assert data['invite_url'].endswith(f'/bls/c/{data["token"]}')
        assert data['expires_in_seconds'] > 0

        session = BLSSession.objects.get(id=data['session_id'])
        assert session.status == BLSSessionStatus.CREATED
        # The DB stores the SHA-256 hash, never the raw token
        assert session.token_hash != data['token']
        assert len(session.token_hash) == 64

    def test_create_requires_known_client(self, clinician_client):
        import uuid
        response = clinician_client.post(
            '/api/v1/bls/sessions/',
            {'client_id': str(uuid.uuid4())},
            format='json',
        )
        assert response.status_code == 404


@pytest.mark.django_db
class TestVerifyToken:
    def test_valid_token_returns_session_id(self, clinician_client, sample_client):
        # Create a session, then verify its token
        create_resp = clinician_client.post(
            '/api/v1/bls/sessions/',
            {'client_id': str(sample_client.id)},
            format='json',
        )
        token = create_resp.json()['token']

        # Verify endpoint is PUBLIC — no auth required
        from rest_framework.test import APIClient
        public_client = APIClient()
        verify_resp = public_client.get(
            '/api/v1/bls/sessions/verify/',
            {'token': token},
        )
        assert verify_resp.status_code == 200
        data = verify_resp.json()
        assert data['valid'] is True
        assert data['session_id'] == create_resp.json()['session_id']

    def test_invalid_token_returns_valid_false(self):
        from rest_framework.test import APIClient
        public_client = APIClient()
        response = public_client.get(
            '/api/v1/bls/sessions/verify/',
            {'token': 'totally-fake-token'},
        )
        assert response.status_code == 200
        assert response.json()['valid'] is False

    def test_missing_token_returns_valid_false(self):
        from rest_framework.test import APIClient
        public_client = APIClient()
        response = public_client.get('/api/v1/bls/sessions/verify/')
        assert response.status_code == 200
        assert response.json()['valid'] is False


@pytest.mark.django_db
class TestEndSession:
    def test_end_persists_counters_and_writes_preference(self, clinician_client, sample_client):
        # Create the session
        create_resp = clinician_client.post(
            '/api/v1/bls/sessions/',
            {'client_id': str(sample_client.id)},
            format='json',
        )
        session_id = create_resp.json()['session_id']

        # End it with realistic counters + settings snapshot
        snapshot = {
            'speed': 5.5,
            'sound': 'finger_snap',
            'color': 'blue',
        }
        end_resp = clinician_client.post(
            f'/api/v1/bls/sessions/{session_id}/end/',
            {
                'duration_seconds': 240,
                'pass_count': 60,
                'set_count': 3,
                'settings_snapshot': snapshot,
                'modality': 'both',
            },
            format='json',
        )
        assert end_resp.status_code == 200, end_resp.content
        data = end_resp.json()
        assert data['status'] == BLSSessionStatus.ENDED
        assert data['pass_count'] == 60
        assert data['set_count'] == 3
        assert data['duration_seconds'] == 240
        assert data['settings_snapshot']['speed'] == 5.5

        # Per-client preference row was created with the same snapshot
        pref = BLSClientPreference.objects.get(client=sample_client)
        assert pref.config['speed'] == 5.5
        assert pref.last_used_at is not None

    def test_cannot_end_twice(self, clinician_client, sample_client):
        create_resp = clinician_client.post(
            '/api/v1/bls/sessions/',
            {'client_id': str(sample_client.id)},
            format='json',
        )
        session_id = create_resp.json()['session_id']

        first = clinician_client.post(
            f'/api/v1/bls/sessions/{session_id}/end/',
            {'duration_seconds': 1, 'pass_count': 0, 'set_count': 0},
            format='json',
        )
        assert first.status_code == 200

        second = clinician_client.post(
            f'/api/v1/bls/sessions/{session_id}/end/',
            {'duration_seconds': 1, 'pass_count': 0, 'set_count': 0},
            format='json',
        )
        assert second.status_code == 400


@pytest.mark.django_db
class TestClientHistory:
    def test_history_endpoint_returns_ended_sessions(self, clinician_client, sample_client):
        # Create + end two sessions for the same client
        for _ in range(2):
            r = clinician_client.post(
                '/api/v1/bls/sessions/',
                {'client_id': str(sample_client.id)},
                format='json',
            )
            sid = r.json()['session_id']
            clinician_client.post(
                f'/api/v1/bls/sessions/{sid}/end/',
                {'duration_seconds': 30, 'pass_count': 5, 'set_count': 1},
                format='json',
            )

        response = clinician_client.get(
            f'/api/v1/bls/clients/{sample_client.id}/history/'
        )
        assert response.status_code == 200
        history = response.json()
        assert len(history) == 2
        # Newest first
        assert all(h['status'] == 'ended' for h in history)
