"""
Tests for Treatment Plan CRUD, signing, copy, and intake pull (BUILD 4).
"""
import pytest
from rest_framework import status


@pytest.mark.django_db
class TestTreatmentPlanCreate:
    url = '/api/v1/treatment-plans/'

    def test_create_plan(self, clinician_client, sample_client):
        """POST /treatment-plans/ creates a draft plan."""
        resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'start_date': '2026-04-06',
            'goals': [
                {
                    'id': 'g1',
                    'problem': 'Anxiety symptoms',
                    'long_term_goal': 'Reduce GAD-7 score to <5',
                    'objectives': 'Learn 3 coping strategies',
                    'target_date': '2026-10-06',
                    'progress': '',
                    'notes': '',
                    'goal_type': 'Symptom Reduction',
                    'status': 'new',
                    'linked_note_ids': [],
                }
            ],
            'plan_data': {
                'frequency': 'Weekly',
                'session_duration': '60 min',
                'primary_diagnosis': 'F41.1',
            },
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data['status'] == 'draft'
        assert len(resp.data['goals']) == 1
        assert resp.data['goals'][0]['goal_type'] == 'Symptom Reduction'

    def test_create_plan_missing_client(self, clinician_client):
        """POST /treatment-plans/ without client_id returns 400."""
        resp = clinician_client.post(self.url, {
            'start_date': '2026-04-06',
            'goals': [],
            'plan_data': {},
        }, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_plan_unauthenticated(self, api_client):
        """POST /treatment-plans/ without auth returns 401."""
        resp = api_client.post(self.url, {}, format='json')
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestTreatmentPlanList:
    url = '/api/v1/treatment-plans/'

    def test_list_plans(self, clinician_client, sample_client):
        """GET /treatment-plans/ returns paginated results."""
        clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'start_date': '2026-04-06',
            'goals': [],
            'plan_data': {},
        }, format='json')

        resp = clinician_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        assert 'results' in resp.data
        assert resp.data['count'] >= 1


@pytest.mark.django_db
class TestTreatmentPlanUpdate:
    url = '/api/v1/treatment-plans/'

    def test_update_plan_goals(self, clinician_client, sample_client):
        """PUT /treatment-plans/{id}/ updates goals."""
        create_resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'start_date': '2026-04-06',
            'goals': [{'id': 'g1', 'problem': 'Original', 'long_term_goal': '', 'objectives': '',
                        'target_date': '', 'progress': '', 'notes': '', 'goal_type': '',
                        'status': 'new', 'linked_note_ids': []}],
            'plan_data': {},
        }, format='json')
        plan_id = create_resp.data['id']

        update_resp = clinician_client.put(f'{self.url}{plan_id}/', {
            'client_id': str(sample_client.id),
            'start_date': '2026-04-06',
            'goals': [{'id': 'g1', 'problem': 'Updated problem', 'long_term_goal': 'New LTG',
                        'objectives': 'Obj 1', 'target_date': '2026-10-06', 'progress': '50%',
                        'notes': 'Progressing well', 'goal_type': 'Skill Acquisition',
                        'status': 'continued', 'linked_note_ids': []}],
            'plan_data': {'frequency': 'Biweekly'},
        }, format='json')
        assert update_resp.status_code == status.HTTP_200_OK
        assert update_resp.data['goals'][0]['problem'] == 'Updated problem'
        assert update_resp.data['goals'][0]['status'] == 'continued'


@pytest.mark.django_db
class TestTreatmentPlanSign:
    url = '/api/v1/treatment-plans/'

    def test_sign_plan(self, clinician_client, sample_client):
        """POST /treatment-plans/{id}/sign/ signs and locks."""
        create_resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'start_date': '2026-04-06',
            'goals': [],
            'plan_data': {},
        }, format='json')
        plan_id = create_resp.data['id']

        sign_resp = clinician_client.post(f'{self.url}{plan_id}/sign/', {
            'signature_data': 'data:image/png;base64,sig...',
        }, format='json')
        assert sign_resp.status_code == status.HTTP_200_OK
        assert sign_resp.data['status'] == 'signed'
        assert sign_resp.data['is_locked'] is True

    def test_sign_without_signature(self, clinician_client, sample_client):
        """POST /treatment-plans/{id}/sign/ without signature returns 400."""
        create_resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'start_date': '2026-04-06',
            'goals': [],
            'plan_data': {},
        }, format='json')
        plan_id = create_resp.data['id']

        resp = clinician_client.post(f'{self.url}{plan_id}/sign/', {}, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_locked_plan_cannot_be_edited(self, clinician_client, sample_client):
        """PUT on a signed plan returns 403."""
        create_resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'start_date': '2026-04-06',
            'goals': [],
            'plan_data': {},
        }, format='json')
        plan_id = create_resp.data['id']

        clinician_client.post(f'{self.url}{plan_id}/sign/', {
            'signature_data': 'data:image/png;base64,sig...',
        }, format='json')

        update_resp = clinician_client.put(f'{self.url}{plan_id}/', {
            'client_id': str(sample_client.id),
            'start_date': '2026-04-06',
            'goals': [],
            'plan_data': {},
        }, format='json')
        assert update_resp.status_code == status.HTTP_403_FORBIDDEN

    def test_double_sign_rejected(self, clinician_client, sample_client):
        """POST /sign/ on already-signed plan returns 400."""
        create_resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'start_date': '2026-04-06',
            'goals': [],
            'plan_data': {},
        }, format='json')
        plan_id = create_resp.data['id']

        clinician_client.post(f'{self.url}{plan_id}/sign/', {
            'signature_data': 'data:image/png;base64,sig...',
        }, format='json')

        resp = clinician_client.post(f'{self.url}{plan_id}/sign/', {
            'signature_data': 'data:image/png;base64,sig2...',
        }, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestTreatmentPlanCoSign:
    url = '/api/v1/treatment-plans/'

    def test_co_sign_after_sign(self, clinician_client, sample_client):
        """POST /co-sign/ after signing succeeds."""
        create_resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'start_date': '2026-04-06',
            'goals': [],
            'plan_data': {},
        }, format='json')
        plan_id = create_resp.data['id']

        clinician_client.post(f'{self.url}{plan_id}/sign/', {
            'signature_data': 'data:image/png;base64,sig...',
        }, format='json')

        co_resp = clinician_client.post(f'{self.url}{plan_id}/co-sign/', {
            'signature_data': 'data:image/png;base64,cosig...',
        }, format='json')
        assert co_resp.status_code == status.HTTP_200_OK
        assert co_resp.data['status'] == 'co_signed'
        assert co_resp.data['co_signed_at'] is not None

    def test_co_sign_before_sign_rejected(self, clinician_client, sample_client):
        """POST /co-sign/ on unsigned plan returns 400."""
        create_resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'start_date': '2026-04-06',
            'goals': [],
            'plan_data': {},
        }, format='json')
        plan_id = create_resp.data['id']

        resp = clinician_client.post(f'{self.url}{plan_id}/co-sign/', {
            'signature_data': 'data:image/png;base64,cosig...',
        }, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestTreatmentPlanDelete:
    url = '/api/v1/treatment-plans/'

    def test_delete_draft(self, clinician_client, sample_client):
        """DELETE on a draft plan succeeds."""
        create_resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'start_date': '2026-04-06',
            'goals': [],
            'plan_data': {},
        }, format='json')
        plan_id = create_resp.data['id']

        resp = clinician_client.delete(f'{self.url}{plan_id}/')
        assert resp.status_code == status.HTTP_204_NO_CONTENT

    def test_cannot_delete_signed(self, clinician_client, sample_client):
        """DELETE on a signed plan returns 403."""
        create_resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'start_date': '2026-04-06',
            'goals': [],
            'plan_data': {},
        }, format='json')
        plan_id = create_resp.data['id']

        clinician_client.post(f'{self.url}{plan_id}/sign/', {
            'signature_data': 'data:image/png;base64,sig...',
        }, format='json')

        resp = clinician_client.delete(f'{self.url}{plan_id}/')
        assert resp.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestCopyFromPrevious:
    url = '/api/v1/treatment-plans/'

    def test_copy_returns_previous_signed(self, clinician_client, sample_client):
        """GET /copy-from-previous/?client=X returns most recent signed plan."""
        create_resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'start_date': '2026-01-01',
            'goals': [{'id': 'g1', 'problem': 'Original goal', 'long_term_goal': 'LTG',
                        'objectives': '', 'target_date': '', 'progress': '', 'notes': '',
                        'goal_type': 'Symptom Reduction', 'status': 'new', 'linked_note_ids': []}],
            'plan_data': {'frequency': 'Weekly'},
        }, format='json')
        plan_id = create_resp.data['id']

        clinician_client.post(f'{self.url}{plan_id}/sign/', {
            'signature_data': 'data:image/png;base64,sig...',
        }, format='json')

        resp = clinician_client.get(f'{self.url}copy-from-previous/', {'client': str(sample_client.id)})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['goals'][0]['problem'] == 'Original goal'
        assert resp.data['plan_data']['frequency'] == 'Weekly'

    def test_copy_requires_client_param(self, clinician_client):
        """GET /copy-from-previous/ without client returns 400."""
        resp = clinician_client.get(f'{self.url}copy-from-previous/')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_copy_404_when_no_signed(self, clinician_client, sample_client):
        """GET /copy-from-previous/ with no signed plans returns 404."""
        resp = clinician_client.get(f'{self.url}copy-from-previous/', {'client': str(sample_client.id)})
        assert resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestPlanDataPersistence:
    """Tests for plan_data field persistence (4.6-4.10)."""
    url = '/api/v1/treatment-plans/'

    def test_interventions_persist(self, clinician_client, sample_client):
        """Plan data interventions saved and retrieved."""
        resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'start_date': '2026-04-06',
            'goals': [],
            'plan_data': {
                'interventions_checklist': ['CBT', 'DBT', 'Motivational Interviewing'],
                'frequency': 'Weekly',
                'session_duration': '60 min',
            },
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        plan_id = resp.data['id']

        get_resp = clinician_client.get(f'{self.url}{plan_id}/')
        assert get_resp.data['plan_data']['frequency'] == 'Weekly'
        assert 'CBT' in get_resp.data['plan_data']['interventions_checklist']

    def test_involvement_fields_persist(self, clinician_client, sample_client):
        """Involvement of others (4.8) saved correctly."""
        resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'start_date': '2026-04-06',
            'goals': [],
            'plan_data': {
                'family_involvement': 'Monthly family sessions',
                'school_iep_involvement': 'Quarterly IEP meetings',
                'other_provider_involvement': 'Psychiatrist coordination',
            },
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        plan_id = resp.data['id']

        get_resp = clinician_client.get(f'{self.url}{plan_id}/')
        assert get_resp.data['plan_data']['family_involvement'] == 'Monthly family sessions'
        assert 'IEP' in get_resp.data['plan_data']['school_iep_involvement']

    def test_goal_review_statuses_persist(self, clinician_client, sample_client):
        """Goal review statuses (4.12) saved correctly."""
        resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'start_date': '2026-04-06',
            'goals': [
                {'id': 'g1', 'problem': 'Anxiety', 'long_term_goal': '', 'objectives': '',
                 'target_date': '', 'progress': '', 'notes': '', 'goal_type': 'Symptom Reduction',
                 'status': 'continued', 'linked_note_ids': []},
                {'id': 'g2', 'problem': 'Depression', 'long_term_goal': '', 'objectives': '',
                 'target_date': '', 'progress': '', 'notes': '', 'goal_type': 'Skill Acquisition',
                 'status': 'met', 'linked_note_ids': []},
            ],
            'plan_data': {},
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        plan_id = resp.data['id']

        get_resp = clinician_client.get(f'{self.url}{plan_id}/')
        assert get_resp.data['goals'][0]['status'] == 'continued'
        assert get_resp.data['goals'][1]['status'] == 'met'
