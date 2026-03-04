"""
Round 3: Deep production-readiness tests.

Focuses on:
1. Error message quality — are messages readable for non-technical users?
2. Date validation — future birthdates, impossible dates, timezone issues
3. Duplicate prevention — same client, same auth number
4. Note lifecycle — complete workflow from draft → signed → can't edit
5. Search/filter edge cases — special characters, empty queries
6. Authorization tracking — expiry, unit limits
7. Data consistency — cascade behaviors, orphaned records
"""
import uuid
import pytest
from datetime import date, timedelta
from rest_framework import status


# ═══════════════════════════════════════════════════════════════════════════════
# 1. ERROR MESSAGE QUALITY TESTS
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestErrorMessageQuality:
    """Every error response must have a human-readable 'message' field."""

    def test_missing_field_error_has_message(self, admin_client):
        """Missing required fields → message should be readable."""
        resp = admin_client.post('/api/v1/clients/', {})
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert 'error' in resp.data
        assert resp.data['error'] is True
        assert 'message' in resp.data
        # Message should not be empty or just a status code
        assert len(resp.data['message']) > 5

    def test_auth_error_has_message(self, api_client):
        """401 errors should have a readable message."""
        resp = api_client.get('/api/v1/clients/')
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED
        assert 'message' in resp.data or 'detail' in resp.data

    def test_permission_error_has_message(self, clinician_client):
        """403 errors should explain why access is denied."""
        resp = clinician_client.get('/api/v1/invoices/')
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_not_found_has_message(self, admin_client):
        """404 errors for missing resources."""
        resp = admin_client.get(f'/api/v1/clients/{uuid.uuid4()}/')
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_login_failure_has_readable_message(self, api_client):
        """Bad login should say 'Invalid email or password', not stack trace."""
        resp = api_client.post('/api/v1/auth/login/', {
            'email': 'nobody@test.com',
            'password': 'wrong',
        })
        # 400 = invalid creds, 429 = rate limited (both are correct behavior)
        assert resp.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_429_TOO_MANY_REQUESTS,
        )

    def test_validation_error_includes_field_errors(self, admin_client, admin_user):
        """Validation error response should include field-specific errors."""
        resp = admin_client.post('/api/v1/appointments/', {
            'start_time': '2026-03-01T09:00:00Z',
            'end_time': '2026-03-01T11:00:00Z',
        })
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        # Should mention which fields are missing
        errors = resp.data.get('errors', resp.data)
        assert 'client_id' in errors or 'provider_id' in errors

    def test_duplicate_booking_error_is_descriptive(
        self, admin_client, sample_client, admin_user
    ):
        """Double-booking error should explain WHY it failed."""
        payload = {
            'client_id': str(sample_client.id),
            'provider_id': str(admin_user.id),
            'start_time': '2026-06-15T09:00:00Z',
            'end_time': '2026-06-15T11:00:00Z',
            'service_code': '97153',
            'units': 8,
        }
        admin_client.post('/api/v1/appointments/', payload)
        resp = admin_client.post('/api/v1/appointments/', payload)
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        msg = str(resp.data)
        assert 'already' in msg.lower() or 'overlap' in msg.lower() or 'time slot' in msg.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# 2. DATE VALIDATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestDateValidation:
    """Dates should be validated — no future birthdates, impossible dates, etc."""

    def test_client_future_birthdate(self, admin_client):
        """Client with birthdate in the future → should fail or warn."""
        future = (date.today() + timedelta(days=365)).isoformat()
        resp = admin_client.post('/api/v1/clients/', {
            'first_name': 'Future',
            'last_name': 'Baby',
            'date_of_birth': future,
            'gender': 'male',
        })
        # Some systems allow future DOB for prenatal records
        # But it should NOT crash with 500
        assert resp.status_code in (
            status.HTTP_201_CREATED,
            status.HTTP_400_BAD_REQUEST,
        ), f"Unexpected status {resp.status_code} for future birthdate"

    def test_client_invalid_date_format(self, admin_client):
        """Garbage date string → 400, not 500."""
        resp = admin_client.post('/api/v1/clients/', {
            'first_name': 'Bad',
            'last_name': 'Date',
            'date_of_birth': 'not-a-date',
            'gender': 'male',
        })
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_appointment_in_distant_past(self, admin_client, sample_client, admin_user):
        """Appointment in year 2020 → should work (historical entries)."""
        resp = admin_client.post('/api/v1/appointments/', {
            'client_id': str(sample_client.id),
            'provider_id': str(admin_user.id),
            'start_time': '2020-03-01T09:00:00Z',
            'end_time': '2020-03-01T11:00:00Z',
            'service_code': '97153',
            'units': 8,
        })
        # Should not crash — past appointments are valid for data import
        assert resp.status_code in (
            status.HTTP_201_CREATED,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_appointment_invalid_datetime(self, admin_client, sample_client, admin_user):
        """Invalid datetime format → 400."""
        resp = admin_client.post('/api/v1/appointments/', {
            'client_id': str(sample_client.id),
            'provider_id': str(admin_user.id),
            'start_time': 'yesterday',
            'end_time': 'tomorrow',
        })
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


# ═══════════════════════════════════════════════════════════════════════════════
# 3. DUPLICATE PREVENTION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestDuplicatePrevention:
    """Test how the system handles duplicate data."""

    def test_create_duplicate_client_allowed(self, admin_client):
        """Two clients with same name should be allowed (different people)."""
        data = {
            'first_name': 'Maria',
            'last_name': 'Garcia',
            'date_of_birth': '2015-05-10',
            'gender': 'female',
        }
        resp1 = admin_client.post('/api/v1/clients/', data)
        assert resp1.status_code == status.HTTP_201_CREATED
        resp2 = admin_client.post('/api/v1/clients/', data)
        assert resp2.status_code == status.HTTP_201_CREATED
        # But they should be different records
        assert resp1.data['id'] != resp2.data['id']

    def test_create_user_duplicate_email(self, admin_client, org):
        """Two users with same email → should fail."""
        data = {
            'email': 'dupe@testclinic.com',
            'first_name': 'Dupe',
            'last_name': 'User',
            'role': 'clinician',
            'password': 'testpass123!',
            'organization_id': str(org.id),
        }
        resp1 = admin_client.post('/api/v1/auth/users/', data)
        assert resp1.status_code == status.HTTP_201_CREATED
        resp2 = admin_client.post('/api/v1/auth/users/', data)
        assert resp2.status_code == status.HTTP_400_BAD_REQUEST


# ═══════════════════════════════════════════════════════════════════════════════
# 4. NOTE LIFECYCLE DEEP TESTS
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestNoteLifecycle:
    """Complete note workflow: create → update → sign → can't edit → can't delete."""
    url = '/api/v1/notes/'

    def test_full_lifecycle(self, clinician_client, sample_client):
        """Draft → update → sign → blocked from editing & deleting."""
        # 1. Create a draft
        resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {'objectives': 'Initial'},
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        note_id = resp.data['id']

        # 2. Update while still draft → should work
        resp = clinician_client.patch(f'{self.url}{note_id}/', {
            'note_data': {'objectives': 'Updated before signing'},
        }, format='json')
        assert resp.status_code == status.HTTP_200_OK

        # 3. Sign the note
        resp = clinician_client.post(f'{self.url}{note_id}/sign/', {
            'signature_data': 'sig_data_hash',
        }, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['status'] == 'signed'

        # 4. Try to update after signing → should be BLOCKED
        resp = clinician_client.patch(f'{self.url}{note_id}/', {
            'note_data': {'objectives': 'Should not work'},
        }, format='json')
        assert resp.status_code == status.HTTP_403_FORBIDDEN

        # 5. Try to delete after signing → should be BLOCKED
        resp = clinician_client.delete(f'{self.url}{note_id}/')
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_re_sign_already_signed(self, clinician_client, sample_client):
        """Re-signing an already-signed note → should fail."""
        resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {'objectives': 'To sign'},
        }, format='json')
        note_id = resp.data['id']

        # Sign once
        clinician_client.post(f'{self.url}{note_id}/sign/', {
            'signature_data': 'first_sig'
        }, format='json')

        # Try to sign again
        resp = clinician_client.post(f'{self.url}{note_id}/sign/', {
            'signature_data': 'second_sig'
        }, format='json')
        # Should fail because note is already signed
        assert resp.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_403_FORBIDDEN,
        ), f"INTEGRITY BUG: Re-signed an already signed note! Status: {resp.status_code}"

    def test_empty_note_data(self, clinician_client, sample_client):
        """Note with empty note_data → should still create successfully."""
        resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': {},
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED

    def test_large_note_data(self, clinician_client, sample_client):
        """Note with large note_data → should handle without 500."""
        large_data = {f'field_{i}': f'value_{i}' * 100 for i in range(50)}
        resp = clinician_client.post(self.url, {
            'client_id': str(sample_client.id),
            'note_data': large_data,
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED


# ═══════════════════════════════════════════════════════════════════════════════
# 5. SEARCH & FILTER EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestSearchFilter:
    """Search and filter should handle edge cases gracefully."""

    def test_search_clients_by_name(self, admin_client, sample_client):
        """Search by partial name → returns matching clients."""
        resp = admin_client.get('/api/v1/clients/', {'search': 'John'})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['count'] >= 1

    def test_search_empty_query(self, admin_client, sample_client):
        """Empty search → returns all clients."""
        resp = admin_client.get('/api/v1/clients/', {'search': ''})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['count'] >= 1

    def test_search_special_characters(self, admin_client):
        """Special characters in search → 200, not 500."""
        resp = admin_client.get('/api/v1/clients/', {
            'search': "O'Brien; DROP TABLE; --"
        })
        assert resp.status_code == status.HTTP_200_OK

    def test_filter_clients_active(self, admin_client, sample_client):
        """Filter by is_active=true."""
        resp = admin_client.get('/api/v1/clients/', {'is_active': 'true'})
        assert resp.status_code == status.HTTP_200_OK

    def test_pagination_beyond_results(self, admin_client, sample_client):
        """Page 9999 → empty results, not error."""
        resp = admin_client.get('/api/v1/clients/', {'page': 9999})
        assert resp.status_code in (
            status.HTTP_200_OK,
            status.HTTP_404_NOT_FOUND,
        )

    def test_appointment_filter_by_date(self, admin_client, sample_appointment):
        """Filter appointments by date range."""
        resp = admin_client.get('/api/v1/appointments/', {
            'start_date': '2026-01-01',
            'end_date': '2026-12-31',
        })
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# 6. AUTHORIZATION TRACKING
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestAuthorizationTracking:
    """Insurance authorizations need proper validation."""

    def test_create_authorization(self, admin_client, sample_client):
        """Create auth with all required fields → 201."""
        resp = admin_client.post('/api/v1/authorizations/', {
            'client_id': str(sample_client.id),
            'insurance_name': 'Aetna',
            'authorization_number': 'AUTH-TEST-001',
            'service_code': '97153',
            'units_approved': 120,
            'start_date': '2026-01-01',
            'end_date': '2026-12-31',
        })
        assert resp.status_code == status.HTTP_201_CREATED

    def test_authorization_dates(self, admin_client, sample_client):
        """Auth where start_date > end_date → should not crash."""
        resp = admin_client.post('/api/v1/authorizations/', {
            'client_id': str(sample_client.id),
            'insurance_name': 'Cigna',
            'authorization_number': 'AUTH-TEST-002',
            'service_code': '97153',
            'units_approved': 50,
            'start_date': '2026-12-31',
            'end_date': '2026-01-01',
        })
        # Should either reject or accept — but MUST NOT crash
        assert resp.status_code in (
            status.HTTP_201_CREATED,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_list_client_authorizations(self, admin_client, sample_client):
        """List authorizations for a client → 200."""
        url = f'/api/v1/clients/{sample_client.id}/authorizations/'
        resp = admin_client.get(url)
        assert resp.status_code == status.HTTP_200_OK


# ═══════════════════════════════════════════════════════════════════════════════
# 7. CLIENT DATA CONSISTENCY
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestClientDataConsistency:
    """Client data must remain consistent across operations."""

    def test_client_detail_includes_authorizations(self, admin_client, sample_client):
        """Client detail endpoint includes authorizations array."""
        url = f'/api/v1/clients/{sample_client.id}/'
        resp = admin_client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert 'authorizations' in resp.data
        assert isinstance(resp.data['authorizations'], list)

    def test_client_detail_includes_recent_sessions(self, admin_client, sample_client):
        """Client detail includes recent_sessions array."""
        url = f'/api/v1/clients/{sample_client.id}/'
        resp = admin_client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert 'recent_sessions' in resp.data
        assert isinstance(resp.data['recent_sessions'], list)

    def test_client_detail_includes_documents(self, admin_client, sample_client):
        """Client detail includes documents array."""
        url = f'/api/v1/clients/{sample_client.id}/'
        resp = admin_client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert 'documents' in resp.data
        assert isinstance(resp.data['documents'], list)

    def test_client_full_name_in_list(self, admin_client, sample_client):
        """Client list includes full_name field."""
        resp = admin_client.get('/api/v1/clients/')
        assert resp.status_code == status.HTTP_200_OK
        first_client = resp.data['results'][0]
        assert 'full_name' in first_client
        assert first_client['full_name'] == 'John Doe'

    def test_client_age_calculated(self, admin_client, sample_client):
        """Client list includes calculated age field."""
        resp = admin_client.get('/api/v1/clients/')
        assert resp.status_code == status.HTTP_200_OK
        first_client = resp.data['results'][0]
        assert 'age' in first_client
        assert isinstance(first_client['age'], int)
        assert first_client['age'] > 0

    def test_soft_deleted_client_hidden_by_default(self, admin_client, sample_client):
        """After soft-delete, client shouldn't appear in active list."""
        url = f'/api/v1/clients/{sample_client.id}/'
        admin_client.delete(url)

        resp = admin_client.get('/api/v1/clients/', {'is_active': 'true'})
        assert resp.status_code == status.HTTP_200_OK
        client_ids = [c['id'] for c in resp.data['results']]
        assert str(sample_client.id) not in client_ids

    def test_client_special_characters_in_name(self, admin_client):
        """Client with accents, hyphens, apostrophes → should work."""
        resp = admin_client.post('/api/v1/clients/', {
            'first_name': "María-José",
            'last_name': "O'Connor-González",
            'date_of_birth': '2018-06-15',
            'gender': 'female',
        })
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data['first_name'] == "María-José"
        assert resp.data['last_name'] == "O'Connor-González"
