"""
Tests for the non-billable ContactNote endpoint (E19).

Distinct from SessionNote — no CPT, no billing, no signature workflow.
Lives in the patient record so phone calls / emails / missed-appointment
outreach are documented even when there's nothing to invoice.
"""
import datetime

import pytest
from rest_framework import status

from apps.clinical.models import ContactNote


URL = '/api/v1/contact-notes/'


@pytest.mark.django_db
class TestContactNoteCreate:
    def _payload(self, sample_client, **overrides):
        base = {
            'client_id': str(sample_client.id),
            'contact_date': '2026-04-15T14:30:00Z',
            'contact_type': 'phone_outbound',
            'summary': 'Followed up about missed Tuesday session.',
            'duration_minutes': 12,
        }
        base.update(overrides)
        return base

    def test_clinician_creates_contact_note(self, clinician_client, sample_client):
        resp = clinician_client.post(URL, self._payload(sample_client), format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data['contact_type'] == 'phone_outbound'
        assert resp.data['contact_type_display'] == 'Phone (Outbound)'
        assert resp.data['summary'] == 'Followed up about missed Tuesday session.'
        assert resp.data['provider_name']  # author server-set, not optional

    def test_summary_required(self, clinician_client, sample_client):
        resp = clinician_client.post(
            URL, self._payload(sample_client, summary='   '), format='json',
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_contact_type_must_be_in_choices(self, clinician_client, sample_client):
        resp = clinician_client.post(
            URL, self._payload(sample_client, contact_type='not_a_real_type'),
            format='json',
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_cannot_create_for_other_org_client(
        self, clinician_client, other_admin, other_org,
    ):
        """Cross-org leak guard: clinician can't post a contact note against
        a client they don't have access to (different organization)."""
        from apps.clients.models import Client
        outside_client = Client.objects.create(
            organization=other_org,
            first_name='Other',
            last_name='Person',
            date_of_birth='1980-01-01',
        )
        resp = clinician_client.post(
            URL, self._payload(outside_client), format='json',
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_unauthenticated_rejected(self, api_client, sample_client):
        resp = api_client.post(URL, self._payload(sample_client), format='json')
        assert resp.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )


@pytest.mark.django_db
class TestContactNoteList:
    def _seed(self, clinician_user, sample_client, *, count=3):
        for i in range(count):
            ContactNote.objects.create(
                client=sample_client,
                provider=clinician_user,
                contact_date=datetime.datetime(2026, 4, i + 1, 10, 0, tzinfo=datetime.timezone.utc),
                contact_type='phone_outbound',
                summary=f'Contact #{i}',
            )

    def test_list_filtered_by_client(
        self, clinician_client, clinician_user, sample_client,
    ):
        self._seed(clinician_user, sample_client)
        resp = clinician_client.get(f'{URL}?client={sample_client.id}')
        assert resp.status_code == status.HTTP_200_OK
        # Pagination wrapper
        items = resp.data.get('results', resp.data)
        assert len(items) == 3

    def test_default_ordering_newest_first(
        self, clinician_client, clinician_user, sample_client,
    ):
        self._seed(clinician_user, sample_client)
        resp = clinician_client.get(URL)
        items = resp.data.get('results', resp.data)
        dates = [it['contact_date'] for it in items]
        assert dates == sorted(dates, reverse=True)

    def test_clinician_only_sees_own_contacts(
        self, clinician_client, clinician_user, admin_user, sample_client,
    ):
        """Clinicians never see contacts authored by other staff in the org —
        admin/supervisor see everything."""
        # Clinician's own contact
        ContactNote.objects.create(
            client=sample_client, provider=clinician_user,
            contact_date='2026-04-10T10:00:00Z',
            contact_type='phone_outbound', summary='mine',
        )
        # Admin's contact
        ContactNote.objects.create(
            client=sample_client, provider=admin_user,
            contact_date='2026-04-11T10:00:00Z',
            contact_type='phone_outbound', summary='admins',
        )

        resp = clinician_client.get(URL)
        items = resp.data.get('results', resp.data)
        summaries = {it['summary'] for it in items}
        assert summaries == {'mine'}

    def test_admin_sees_all_org_contacts(
        self, admin_client, clinician_user, admin_user, sample_client,
    ):
        ContactNote.objects.create(
            client=sample_client, provider=clinician_user,
            contact_date='2026-04-10T10:00:00Z',
            contact_type='phone_outbound', summary='clinician one',
        )
        ContactNote.objects.create(
            client=sample_client, provider=admin_user,
            contact_date='2026-04-11T10:00:00Z',
            contact_type='phone_outbound', summary='admin one',
        )
        resp = admin_client.get(URL)
        items = resp.data.get('results', resp.data)
        summaries = {it['summary'] for it in items}
        assert summaries == {'clinician one', 'admin one'}


@pytest.mark.django_db
class TestContactNoteUpdate:
    def _make(self, clinician_user, sample_client):
        return ContactNote.objects.create(
            client=sample_client, provider=clinician_user,
            contact_date='2026-04-10T10:00:00Z',
            contact_type='phone_outbound', summary='Original',
        )

    def test_author_can_update(self, clinician_client, clinician_user, sample_client):
        cn = self._make(clinician_user, sample_client)
        resp = clinician_client.patch(
            f'{URL}{cn.id}/', {'summary': 'Edited'}, format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        cn.refresh_from_db()
        assert cn.summary == 'Edited'

    def test_admin_cannot_edit_other_provider_contact(
        self, admin_client, clinician_user, sample_client,
    ):
        """E19 design choice: admin can DELETE someone else's contact (cleanup)
        but cannot REWRITE the summary — that would falsify the record."""
        cn = self._make(clinician_user, sample_client)
        resp = admin_client.patch(
            f'{URL}{cn.id}/', {'summary': 'Forged by admin'}, format='json',
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        cn.refresh_from_db()
        assert cn.summary == 'Original'


@pytest.mark.django_db
class TestContactNoteDelete:
    def _make(self, provider, sample_client):
        return ContactNote.objects.create(
            client=sample_client, provider=provider,
            contact_date='2026-04-10T10:00:00Z',
            contact_type='phone_outbound', summary='to delete',
        )

    def test_author_can_delete(self, clinician_client, clinician_user, sample_client):
        cn = self._make(clinician_user, sample_client)
        resp = clinician_client.delete(f'{URL}{cn.id}/')
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        assert not ContactNote.objects.filter(pk=cn.pk).exists()

    def test_admin_can_delete_other_provider_contact(
        self, admin_client, clinician_user, sample_client,
    ):
        cn = self._make(clinician_user, sample_client)
        resp = admin_client.delete(f'{URL}{cn.id}/')
        assert resp.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.django_db
class TestContactNoteCrossOrg:
    def test_cross_org_invisible_in_list(
        self, clinician_client, other_org,
    ):
        from apps.clients.models import Client
        from apps.accounts.models import User
        outside_client = Client.objects.create(
            organization=other_org, first_name='Out', last_name='Side',
            date_of_birth='1990-01-01',
        )
        outside_provider = User.objects.create_user(
            email='outside@other.com', password='pass',
            first_name='Out', last_name='Side',
            role='clinician', organization=other_org,
        )
        ContactNote.objects.create(
            client=outside_client, provider=outside_provider,
            contact_date='2026-04-10T10:00:00Z',
            contact_type='phone_outbound', summary='leaks',
        )

        resp = clinician_client.get(URL)
        items = resp.data.get('results', resp.data)
        assert items == []
