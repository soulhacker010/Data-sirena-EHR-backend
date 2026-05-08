"""
Tests for Intake Assessment CRUD and signing (BUILD 3).
"""
import pytest
from rest_framework import status


@pytest.mark.django_db
class TestIntakeCreate:
    url = '/api/v1/intakes/'

    def test_create_intake(self, clinician_client, sample_client):
        """POST /intakes/ creates a draft intake."""
        resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'assessment_date': '2026-04-05',
            'intake_data': {
                'presenting_problem': 'Anxiety and depression',
                'primary_diagnosis': 'F41.1',
            },
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data['status'] == 'draft'
        assert resp.data['intake_data']['presenting_problem'] == 'Anxiety and depression'
        assert resp.data['intake_data']['primary_diagnosis'] == 'F41.1'

    def test_create_intake_missing_client(self, clinician_client):
        """POST /intakes/ without client_id returns 400."""
        resp = clinician_client.post(self.url, {
            'assessment_date': '2026-04-05',
            'intake_data': {},
        }, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_intake_unauthenticated(self, api_client):
        """POST /intakes/ without auth returns 401."""
        resp = api_client.post(self.url, {}, format='json')
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestIntakeList:
    url = '/api/v1/intakes/'

    def test_list_intakes(self, clinician_client, sample_client):
        """GET /intakes/ returns paginated results."""
        clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'assessment_date': '2026-04-05',
            'intake_data': {'presenting_problem': 'Test'},
        }, format='json')

        resp = clinician_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        assert 'results' in resp.data
        assert resp.data['count'] >= 1

    # ─── B10: date-range filter for the calendar overlay ───────────────────

    def _make_intake(self, clinician_client, sample_client, assessment_date):
        return clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'assessment_date': assessment_date,
            'intake_data': {'presenting_problem': 'Test'},
        }, format='json')

    def test_filter_by_start_date_inclusive(self, clinician_client, sample_client):
        """assessment_date == start_date is included."""
        self._make_intake(clinician_client, sample_client, '2026-04-05')
        self._make_intake(clinician_client, sample_client, '2026-03-15')

        resp = clinician_client.get(self.url + '?start_date=2026-04-01')
        assert resp.status_code == status.HTTP_200_OK
        dates = {i['assessment_date'] for i in resp.data['results']}
        assert dates == {'2026-04-05'}

    def test_filter_by_end_date_inclusive(self, clinician_client, sample_client):
        """assessment_date == end_date is included."""
        self._make_intake(clinician_client, sample_client, '2026-04-05')
        self._make_intake(clinician_client, sample_client, '2026-05-15')

        resp = clinician_client.get(self.url + '?end_date=2026-04-30')
        dates = {i['assessment_date'] for i in resp.data['results']}
        assert dates == {'2026-04-05'}

    def test_filter_by_full_date_range(self, clinician_client, sample_client):
        """Both bounds together return only intakes inside the window."""
        self._make_intake(clinician_client, sample_client, '2026-03-15')
        self._make_intake(clinician_client, sample_client, '2026-04-05')
        self._make_intake(clinician_client, sample_client, '2026-04-20')
        self._make_intake(clinician_client, sample_client, '2026-05-10')

        resp = clinician_client.get(
            self.url + '?start_date=2026-04-01&end_date=2026-04-30'
        )
        dates = {i['assessment_date'] for i in resp.data['results']}
        assert dates == {'2026-04-05', '2026-04-20'}

    def test_no_date_filter_returns_all(self, clinician_client, sample_client):
        self._make_intake(clinician_client, sample_client, '2026-03-15')
        self._make_intake(clinician_client, sample_client, '2026-04-05')

        resp = clinician_client.get(self.url)
        assert resp.data['count'] >= 2


@pytest.mark.django_db
class TestIntakeUpdate:
    url = '/api/v1/intakes/'

    def test_update_intake(self, clinician_client, sample_client):
        """PUT /intakes/{id}/ updates intake data."""
        create_resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'assessment_date': '2026-04-05',
            'intake_data': {'presenting_problem': 'Original'},
        }, format='json')
        intake_id = create_resp.data['id']

        update_resp = clinician_client.put(f'{self.url}{intake_id}/', {
            'client_id': str(sample_client.id),
            'assessment_date': '2026-04-05',
            'intake_data': {'presenting_problem': 'Updated problem'},
        }, format='json')
        assert update_resp.status_code == status.HTTP_200_OK
        assert update_resp.data['intake_data']['presenting_problem'] == 'Updated problem'


@pytest.mark.django_db
class TestIntakeSign:
    url = '/api/v1/intakes/'

    def test_sign_intake(self, clinician_client, sample_client):
        """POST /intakes/{id}/sign/ signs and locks the intake."""
        create_resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'assessment_date': '2026-04-05',
            'intake_data': {'presenting_problem': 'Sign test'},
        }, format='json')
        intake_id = create_resp.data['id']

        sign_resp = clinician_client.post(f'{self.url}{intake_id}/sign/', {
            'signature_data': 'data:image/png;base64,sig...',
        }, format='json')
        assert sign_resp.status_code == status.HTTP_200_OK
        assert sign_resp.data['status'] == 'signed'
        assert sign_resp.data['is_locked'] is True
        assert sign_resp.data['signed_at'] is not None

    def test_sign_intake_without_signature(self, clinician_client, sample_client):
        """POST /intakes/{id}/sign/ without signature returns 400."""
        create_resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'assessment_date': '2026-04-05',
            'intake_data': {},
        }, format='json')
        intake_id = create_resp.data['id']

        sign_resp = clinician_client.post(f'{self.url}{intake_id}/sign/', {}, format='json')
        assert sign_resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_locked_intake_cannot_be_edited(self, clinician_client, sample_client):
        """PUT on a signed intake returns 403."""
        create_resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'assessment_date': '2026-04-05',
            'intake_data': {'presenting_problem': 'Lock test'},
        }, format='json')
        intake_id = create_resp.data['id']

        clinician_client.post(f'{self.url}{intake_id}/sign/', {
            'signature_data': 'data:image/png;base64,sig...',
        }, format='json')

        update_resp = clinician_client.put(f'{self.url}{intake_id}/', {
            'client_id': str(sample_client.id),
            'assessment_date': '2026-04-05',
            'intake_data': {'presenting_problem': 'Should fail'},
        }, format='json')
        assert update_resp.status_code == status.HTTP_403_FORBIDDEN

    def test_signed_intake_cannot_be_signed_again(self, clinician_client, sample_client):
        """POST /intakes/{id}/sign/ on a signed intake returns 400."""
        create_resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'assessment_date': '2026-04-05',
            'intake_data': {},
        }, format='json')
        intake_id = create_resp.data['id']

        clinician_client.post(f'{self.url}{intake_id}/sign/', {
            'signature_data': 'data:image/png;base64,sig...',
        }, format='json')

        second_sign = clinician_client.post(f'{self.url}{intake_id}/sign/', {
            'signature_data': 'data:image/png;base64,sig2...',
        }, format='json')
        assert second_sign.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestIntakeClientSign:
    url = '/api/v1/intakes/'

    def test_client_sign(self, clinician_client, sample_client):
        """POST /intakes/{id}/client-sign/ records client signature."""
        create_resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'assessment_date': '2026-04-05',
            'intake_data': {},
        }, format='json')
        intake_id = create_resp.data['id']

        resp = clinician_client.post(f'{self.url}{intake_id}/client-sign/', {
            'signature_data': 'data:image/png;base64,clientsig...',
        }, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['client_signed_at'] is not None


@pytest.mark.django_db
class TestIntakeDelete:
    url = '/api/v1/intakes/'

    def test_delete_draft_intake(self, clinician_client, sample_client):
        """DELETE /intakes/{id}/ on a draft returns 204."""
        create_resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'assessment_date': '2026-04-05',
            'intake_data': {},
        }, format='json')
        intake_id = create_resp.data['id']

        resp = clinician_client.delete(f'{self.url}{intake_id}/')
        assert resp.status_code == status.HTTP_204_NO_CONTENT

    def test_cannot_delete_signed_intake(self, clinician_client, sample_client):
        """DELETE /intakes/{id}/ on a signed intake returns 403."""
        create_resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'assessment_date': '2026-04-05',
            'intake_data': {},
        }, format='json')
        intake_id = create_resp.data['id']

        clinician_client.post(f'{self.url}{intake_id}/sign/', {
            'signature_data': 'data:image/png;base64,sig...',
        }, format='json')

        resp = clinician_client.delete(f'{self.url}{intake_id}/')
        assert resp.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestIntakeDataPersistence:
    """Tests for intake data field persistence (BUILD 3.2-3.11)."""
    url = '/api/v1/intakes/'

    def test_diagnosis_data_persists(self, clinician_client, sample_client):
        """Diagnosis fields (3.3) saved and retrieved correctly."""
        resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'assessment_date': '2026-04-05',
            'intake_data': {
                'primary_diagnosis': 'F41.1',
                'secondary_diagnoses': [
                    {'code': 'F32.1', 'label': 'MDD, single episode, moderate'},
                    {'code': 'F43.10', 'label': 'PTSD, unspecified'},
                ],
            },
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        intake_id = resp.data['id']

        get_resp = clinician_client.get(f'{self.url}{intake_id}/')
        assert get_resp.data['intake_data']['primary_diagnosis'] == 'F41.1'
        assert len(get_resp.data['intake_data']['secondary_diagnoses']) == 2

    def test_history_fields_persist(self, clinician_client, sample_client):
        """History fields (3.4) saved correctly."""
        resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'assessment_date': '2026-04-05',
            'intake_data': {
                'psychiatric_history': 'Previous MDD episode 2020',
                'medical_history': 'Asthma, no current medications',
                'trauma_history': 'Witnessed DV as a child',
                'substance_use_history': 'Social alcohol use only',
            },
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        intake_id = resp.data['id']

        get_resp = clinician_client.get(f'{self.url}{intake_id}/')
        assert get_resp.data['intake_data']['psychiatric_history'] == 'Previous MDD episode 2020'
        assert 'Witnessed DV' in get_resp.data['intake_data']['trauma_history']

    def test_safety_plan_persists(self, clinician_client, sample_client):
        """Safety plan fields (3.8) saved correctly."""
        resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'assessment_date': '2026-04-05',
            'intake_data': {
                'safety_plan_in_place': 'yes',
                'safety_plan_details': 'Call 988, contact therapist, go to ER',
            },
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        intake_id = resp.data['id']

        get_resp = clinician_client.get(f'{self.url}{intake_id}/')
        assert get_resp.data['intake_data']['safety_plan_in_place'] == 'yes'
        assert 'Call 988' in get_resp.data['intake_data']['safety_plan_details']

    def test_treatment_goals_persist(self, clinician_client, sample_client):
        """Strengths/goals/treatment fields (3.9-3.10) saved correctly."""
        resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'assessment_date': '2026-04-05',
            'intake_data': {
                'client_strengths': 'Motivated, good insight',
                'tentative_goals': 'Reduce anxiety symptoms',
                'treatment_frequency': 'Weekly',
                'treatment_duration': '6 months',
            },
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        intake_id = resp.data['id']

        get_resp = clinician_client.get(f'{self.url}{intake_id}/')
        assert get_resp.data['intake_data']['treatment_frequency'] == 'Weekly'
        assert get_resp.data['intake_data']['client_strengths'] == 'Motivated, good insight'
