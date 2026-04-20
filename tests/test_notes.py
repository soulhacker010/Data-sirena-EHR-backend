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

    def test_supervisor_can_create_note(self, org, sample_client):
        """Supervisors must be able to create notes (was failing for some users in prod)."""
        from rest_framework.test import APIClient
        from apps.accounts.models import User
        supervisor = User.objects.create_user(
            email='sup@test.com', password='pass',
            first_name='Sup', last_name='Visor',
            role='supervisor', organization=org,
        )
        client = APIClient()
        client.force_authenticate(user=supervisor)
        resp = client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {'objectives': 'Supervision note'},
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED

    def test_admin_can_create_note(self, org, sample_client):
        """Admins must be able to create notes."""
        from rest_framework.test import APIClient
        from apps.accounts.models import User
        admin = User.objects.create_user(
            email='admin2@test.com', password='pass',
            first_name='Admin', last_name='Two',
            role='admin', organization=org,
        )
        client = APIClient()
        client.force_authenticate(user=admin)
        resp = client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {'objectives': 'Admin note'},
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED

    def test_front_desk_cannot_create_note(self, org, sample_client):
        """Front desk role must NOT create clinical notes — returns 403."""
        from rest_framework.test import APIClient
        from apps.accounts.models import User
        fd_user = User.objects.create_user(
            email='frontdesk@test.com', password='pass',
            first_name='Front', last_name='Desk',
            role='front_desk', organization=org,
        )
        client = APIClient()
        client.force_authenticate(user=fd_user)
        resp = client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {'objectives': 'Unauthorized note'},
        }, format='json')
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_biller_cannot_create_note(self, org, sample_client):
        """Biller role must NOT create clinical notes — returns 403."""
        from rest_framework.test import APIClient
        from apps.accounts.models import User
        biller = User.objects.create_user(
            email='biller@test.com', password='pass',
            first_name='Bill', last_name='Er',
            role='biller', organization=org,
        )
        client = APIClient()
        client.force_authenticate(user=biller)
        resp = client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {'objectives': 'Unauthorized note'},
        }, format='json')
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_provider_auto_set_to_current_user(self, clinician_client, clinician_user, sample_client):
        """Provider on a new note is always the logged-in user, regardless of payload."""
        resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {},
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data['provider_id'] == str(clinician_user.id)

    def test_cross_org_client_rejected(self, clinician_client, other_org):
        """Client from a different org → 400, not 403."""
        from apps.clients.models import Client
        other_client = Client.objects.create(
            organization=other_org,
            first_name='Other', last_name='Client',
            date_of_birth='1990-01-01',
        )
        resp = clinician_client.post(self.url, {
            'client_id': str(other_client.id),
            'note_data': {'objectives': 'Cross-org attempt'},
        }, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


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

    def test_sign_note_locks_it(self, clinician_client, sample_client):
        """Signing a note must set is_locked=True (BUILD 1.1)."""
        create_resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {'objectives': 'Lock test'},
        }, format='json')
        note_id = create_resp.data['id']

        resp = clinician_client.post(f'{self.url}{note_id}/sign/', {
            'signature_data': 'data:image/png;base64,test...',
        }, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['is_locked'] is True
        assert resp.data['status'] == 'signed'

    def test_locked_note_cannot_be_edited(self, clinician_client, sample_client):
        """Signed+locked note rejects updates (BUILD 1.1)."""
        create_resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {'objectives': 'Will lock'},
        }, format='json')
        note_id = create_resp.data['id']

        clinician_client.post(f'{self.url}{note_id}/sign/', {
            'signature_data': 'data:image/png;base64,test...',
        }, format='json')

        resp = clinician_client.patch(f'{self.url}{note_id}/', {
            'note_data': {'objectives': 'Trying to edit locked note'},
        }, format='json')
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_locked_note_cannot_be_signed_again(self, clinician_client, sample_client):
        """Already-signed note rejects double signing."""
        create_resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {'objectives': 'Double sign'},
        }, format='json')
        note_id = create_resp.data['id']

        clinician_client.post(f'{self.url}{note_id}/sign/', {
            'signature_data': 'data:image/png;base64,first...',
        }, format='json')

        resp = clinician_client.post(f'{self.url}{note_id}/sign/', {
            'signature_data': 'data:image/png;base64,second...',
        }, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestNoteUnlock:
    """Tests for admin-only note unlock (BUILD 1.2)."""
    url = '/api/v1/notes/'

    def _create_and_sign(self, clinician_client, sample_client):
        """Helper: create + sign a note, return note_id."""
        create_resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {'objectives': 'Unlock test'},
        }, format='json')
        note_id = create_resp.data['id']
        clinician_client.post(f'{self.url}{note_id}/sign/', {
            'signature_data': 'data:image/png;base64,sig...',
        }, format='json')
        return note_id

    def test_admin_can_unlock(self, clinician_client, admin_client, sample_client):
        """Admin unlocks a signed note → status=draft, is_locked=False."""
        note_id = self._create_and_sign(clinician_client, sample_client)

        resp = admin_client.post(f'{self.url}{note_id}/unlock/')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['is_locked'] is False
        assert resp.data['status'] == 'draft'

    def test_admin_unlock_has_audit_trail(self, clinician_client, admin_client, sample_client):
        """Unlock records audit entry in note_data."""
        note_id = self._create_and_sign(clinician_client, sample_client)

        resp = admin_client.post(f'{self.url}{note_id}/unlock/')
        assert resp.status_code == status.HTTP_200_OK
        audit_log = resp.data['note_data'].get('audit_log', [])
        assert len(audit_log) == 1
        assert audit_log[0]['action'] == 'unlocked'
        assert audit_log[0]['previous_status'] == 'signed'

    def test_clinician_cannot_unlock(self, clinician_client, sample_client):
        """Non-admin cannot unlock → 400."""
        note_id = self._create_and_sign(clinician_client, sample_client)

        resp = clinician_client.post(f'{self.url}{note_id}/unlock/')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_unlocked_note_can_be_edited(self, clinician_client, admin_client, sample_client):
        """After admin unlock, note is editable again."""
        note_id = self._create_and_sign(clinician_client, sample_client)
        admin_client.post(f'{self.url}{note_id}/unlock/')

        resp = clinician_client.patch(f'{self.url}{note_id}/', {
            'note_data': {'objectives': 'Revised after unlock'},
        }, format='json')
        assert resp.status_code == status.HTTP_200_OK

    def test_unlocked_note_can_be_re_signed(self, clinician_client, admin_client, sample_client):
        """After admin unlock, note can be signed again."""
        note_id = self._create_and_sign(clinician_client, sample_client)
        admin_client.post(f'{self.url}{note_id}/unlock/')

        resp = clinician_client.post(f'{self.url}{note_id}/sign/', {
            'signature_data': 'data:image/png;base64,newsig...',
        }, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['status'] == 'signed'
        assert resp.data['is_locked'] is True


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

    def test_cannot_delete_signed_note(self, clinician_client, sample_client):
        """Signed notes cannot be deleted."""
        create_resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {'objectives': 'Signed delete attempt'},
        }, format='json')
        note_id = create_resp.data['id']

        clinician_client.post(f'{self.url}{note_id}/sign/', {
            'signature_data': 'data:image/png;base64,sig...',
        }, format='json')

        resp = clinician_client.delete(f'{self.url}{note_id}/')
        assert resp.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestCopyFromLast:
    """Tests for Copy from Last endpoint (BUILD 2.2)."""
    url = '/api/v1/notes/'

    def test_last_note_returns_most_recent_signed(self, clinician_client, sample_client):
        """GET /notes/last-note/?client={id} returns most recent signed note."""
        # Create and sign first note
        resp1 = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {'objectives': 'First note', 'mse_mood': 'Euthymic'},
        }, format='json')
        note1_id = resp1.data['id']
        clinician_client.post(f'{self.url}{note1_id}/sign/', {
            'signature_data': 'data:image/png;base64,sig1...',
        }, format='json')

        # Create and sign second note (more recent)
        resp2 = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {'objectives': 'Second note', 'mse_mood': 'Anxious'},
        }, format='json')
        note2_id = resp2.data['id']
        clinician_client.post(f'{self.url}{note2_id}/sign/', {
            'signature_data': 'data:image/png;base64,sig2...',
        }, format='json')

        # Fetch last note
        resp = clinician_client.get(f'{self.url}last-note/', {'client': str(sample_client.id)})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['note_data']['objectives'] == 'Second note'
        assert resp.data['note_data']['mse_mood'] == 'Anxious'

    def test_last_note_requires_client_param(self, clinician_client):
        """GET /notes/last-note/ without client param returns 400."""
        resp = clinician_client.get(f'{self.url}last-note/')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_last_note_404_when_no_signed_notes(self, clinician_client, sample_client):
        """GET /notes/last-note/ returns 404 if no signed notes exist."""
        # Create draft note (not signed)
        clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {'objectives': 'Draft only'},
        }, format='json')

        resp = clinician_client.get(f'{self.url}last-note/', {'client': str(sample_client.id)})
        assert resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestClinicalDataPersistence:
    """Tests for MSE, Risk, ABA, Interventions data persistence (BUILD 2)."""
    url = '/api/v1/notes/'

    def test_mse_data_persists(self, clinician_client, sample_client):
        """MSE fields are saved and retrieved correctly."""
        mse_data = {
            'mse_appearance': 'Well-groomed',
            'mse_mood': 'Euthymic',
            'mse_affect': 'Appropriate',
            'mse_orientation': 'Oriented x4 (person, place, time, situation)',
        }
        resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {**mse_data, 'objectives': 'MSE test'},
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        note_id = resp.data['id']

        # Retrieve and verify
        get_resp = clinician_client.get(f'{self.url}{note_id}/')
        for key, value in mse_data.items():
            assert get_resp.data['note_data'][key] == value

    def test_risk_assessment_data_persists(self, clinician_client, sample_client):
        """Risk assessment fields are saved correctly."""
        risk_data = {
            'risk_suicide_level': 'Low',
            'risk_homicide_level': 'None',
            'risk_factors': ['History of trauma/abuse', 'Social isolation'],
            'risk_protective_factors': ['Strong social support'],
            'risk_actions_taken': ['Safety plan reviewed/updated'],
        }
        resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {**risk_data, 'objectives': 'Risk test'},
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        note_id = resp.data['id']

        get_resp = clinician_client.get(f'{self.url}{note_id}/')
        assert get_resp.data['note_data']['risk_suicide_level'] == 'Low'
        assert 'Social isolation' in get_resp.data['note_data']['risk_factors']

    def test_aba_data_persists(self, clinician_client, sample_client):
        """ABA session data fields are saved correctly."""
        aba_data = {
            'aba_goals': [
                {'goal': 'Manding', 'trials': '10', 'correct': '8', 'prompt_level': 'Verbal'}
            ],
            'aba_abc_data': 'A: Demand → B: Tantrum → C: Escape',
            'aba_reinforcers': 'iPad, stickers',
        }
        resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {**aba_data, 'objectives': 'ABA test'},
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        note_id = resp.data['id']

        get_resp = clinician_client.get(f'{self.url}{note_id}/')
        assert get_resp.data['note_data']['aba_goals'][0]['goal'] == 'Manding'
        assert get_resp.data['note_data']['aba_reinforcers'] == 'iPad, stickers'

    def test_interventions_checklist_persists(self, clinician_client, sample_client):
        """Interventions checklist is saved correctly."""
        resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {
                'objectives': 'Interventions test',
                'interventions_checklist': ['CBT', 'Psychoeducation', 'Mindfulness'],
            },
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        note_id = resp.data['id']

        get_resp = clinician_client.get(f'{self.url}{note_id}/')
        checklist = get_resp.data['note_data']['interventions_checklist']
        assert 'CBT' in checklist
        assert 'Psychoeducation' in checklist

    def test_medical_necessity_persists(self, clinician_client, sample_client):
        """Medical necessity statement is saved correctly."""
        resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {
                'objectives': 'Med necessity test',
                'medical_necessity': 'Services were medically necessary to address anxiety.',
            },
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        note_id = resp.data['id']

        get_resp = clinician_client.get(f'{self.url}{note_id}/')
        assert 'medically necessary' in get_resp.data['note_data']['medical_necessity']

    def test_authorization_units_persist(self, clinician_client, sample_client):
        """Authorization units are saved correctly."""
        resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {
                'objectives': 'Auth test',
                'auth_authorized': '40',
                'auth_used': '12',
            },
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        note_id = resp.data['id']

        get_resp = clinician_client.get(f'{self.url}{note_id}/')
        assert get_resp.data['note_data']['auth_authorized'] == '40'
        assert get_resp.data['note_data']['auth_used'] == '12'
