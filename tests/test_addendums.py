"""
Tests for the universal addendum endpoint (E11 + E18).

Addendums are immutable, time-stamped amendments attached to a parent
clinical document (session note, intake, or treatment plan). Per Dr. Joe's
2026-05-04 feedback every clinical doc must support them.

The same `AddendumActionMixin` powers the action on three viewsets, so we
exercise it through each parent type to make sure the mixin's
`addendum_parent_field` wiring is right and to catch any cross-parent bleed.
"""
import datetime

import pytest
from rest_framework import status

from apps.clinical.models import (
    Addendum, IntakeAssessment, SessionNote, TreatmentPlan,
)


@pytest.fixture
def signed_note(clinician_user, sample_client):
    """A session note that's already been signed — addendums are most often
    written against signed notes (you can't edit them in place)."""
    return SessionNote.objects.create(
        client=sample_client,
        provider=clinician_user,
        status='signed',
        is_locked=True,
        note_data={'objectives': 'Initial goals'},
    )


@pytest.fixture
def signed_intake(clinician_user, sample_client):
    return IntakeAssessment.objects.create(
        client=sample_client,
        provider=clinician_user,
        assessment_date=datetime.date(2026, 4, 15),
        status='signed',
        is_locked=True,
        intake_data={'presenting_problem': 'Anxiety'},
    )


@pytest.fixture
def treatment_plan(clinician_user, sample_client):
    return TreatmentPlan.objects.create(
        client=sample_client,
        provider=clinician_user,
        start_date=datetime.date(2026, 4, 20),
        goals=[],
        status='signed',
        is_locked=True,
    )


# ─── Session note addendums ────────────────────────────────────────────────

@pytest.mark.django_db
class TestSessionNoteAddendums:
    def test_create_returns_201_with_body_author_timestamp(
        self, clinician_client, signed_note,
    ):
        resp = clinician_client.post(
            f'/api/v1/notes/{signed_note.id}/addendums/',
            {'body': 'Patient disclosed prior diagnosis at follow-up.'},
            format='json',
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data['body'] == 'Patient disclosed prior diagnosis at follow-up.'
        assert resp.data['created_by_name']
        assert resp.data['created_at']

    def test_can_addend_a_signed_locked_note(self, clinician_client, signed_note):
        """The whole point: you can't edit a sealed note in place — but you
        CAN attach an addendum to it."""
        assert signed_note.is_locked is True
        resp = clinician_client.post(
            f'/api/v1/notes/{signed_note.id}/addendums/',
            {'body': 'Late-arriving lab result attached.'},
            format='json',
        )
        assert resp.status_code == status.HTTP_201_CREATED

    def test_list_returns_chronological(self, clinician_client, signed_note):
        for i in range(3):
            clinician_client.post(
                f'/api/v1/notes/{signed_note.id}/addendums/',
                {'body': f'Addendum {i}'},
                format='json',
            )
        resp = clinician_client.get(f'/api/v1/notes/{signed_note.id}/addendums/')
        bodies = [a['body'] for a in resp.data]
        assert bodies == ['Addendum 0', 'Addendum 1', 'Addendum 2']

    def test_empty_body_rejected(self, clinician_client, signed_note):
        resp = clinician_client.post(
            f'/api/v1/notes/{signed_note.id}/addendums/',
            {'body': '   '},  # whitespace only
            format='json',
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_missing_body_rejected(self, clinician_client, signed_note):
        resp = clinician_client.post(
            f'/api/v1/notes/{signed_note.id}/addendums/',
            {},
            format='json',
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


# ─── Intake addendums ──────────────────────────────────────────────────────

@pytest.mark.django_db
class TestIntakeAddendums:
    """E11 specifically: 'Intake: -need a place for an addendum as you learn
    more info or want to change diagnosis'."""

    def test_create_addendum_on_intake(self, clinician_client, signed_intake):
        resp = clinician_client.post(
            f'/api/v1/intakes/{signed_intake.id}/addendums/',
            {'body': 'Diagnosis updated to F41.1 after Session 4.'},
            format='json',
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert 'F41.1' in resp.data['body']

    def test_list_intake_addendums(self, clinician_client, signed_intake):
        clinician_client.post(
            f'/api/v1/intakes/{signed_intake.id}/addendums/',
            {'body': 'First amendment'}, format='json',
        )
        resp = clinician_client.get(f'/api/v1/intakes/{signed_intake.id}/addendums/')
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) == 1


# ─── Treatment plan addendums ──────────────────────────────────────────────

@pytest.mark.django_db
class TestTreatmentPlanAddendums:
    def test_create_addendum_on_treatment_plan(
        self, clinician_client, treatment_plan,
    ):
        resp = clinician_client.post(
            f'/api/v1/treatment-plans/{treatment_plan.id}/addendums/',
            {'body': 'Added LTG #3 per supervision feedback.'},
            format='json',
        )
        assert resp.status_code == status.HTTP_201_CREATED


# ─── Cross-parent isolation ───────────────────────────────────────────────

@pytest.mark.django_db
class TestAddendumIsolation:
    def test_note_addendums_not_returned_for_intake(
        self, clinician_client, signed_note, signed_intake,
    ):
        """An addendum on a session note must NOT appear under an intake's
        addendum list — that's the whole reason we have explicit FK fields."""
        clinician_client.post(
            f'/api/v1/notes/{signed_note.id}/addendums/',
            {'body': 'Note addendum'}, format='json',
        )
        resp = clinician_client.get(f'/api/v1/intakes/{signed_intake.id}/addendums/')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data == []

    def test_db_constraint_blocks_zero_parents(self, clinician_user):
        """Defensive: the DB-level CheckConstraint must reject zero-parent rows
        even if someone bypasses the API."""
        from django.db.utils import IntegrityError
        with pytest.raises(IntegrityError):
            Addendum.objects.create(
                body='Orphan addendum', created_by=clinician_user,
            )

    def test_db_constraint_blocks_two_parents(
        self, clinician_user, signed_note, signed_intake,
    ):
        from django.db.utils import IntegrityError
        with pytest.raises(IntegrityError):
            Addendum.objects.create(
                body='Two-parent addendum',
                created_by=clinician_user,
                parent_session_note=signed_note,
                parent_intake=signed_intake,
            )


# ─── Permissions ──────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestAddendumPermissions:
    def test_unauthenticated_rejected(self, api_client, signed_note):
        resp = api_client.post(
            f'/api/v1/notes/{signed_note.id}/addendums/',
            {'body': 'x'}, format='json',
        )
        assert resp.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_other_org_clinician_cannot_addend(
        self, other_admin_client, signed_note,
    ):
        """A clinician from a different org gets 404 (not even visible).
        get_object filters by org via the parent viewset's get_queryset."""
        resp = other_admin_client.post(
            f'/api/v1/notes/{signed_note.id}/addendums/',
            {'body': 'Cross-org leak attempt'}, format='json',
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ─── Immutability ─────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestAddendumImmutable:
    def test_no_update_endpoint_exposed(self, clinician_client, signed_note):
        """Addendums are immutable. There's no PUT/PATCH/DELETE on the
        nested action — only GET (list) and POST (create)."""
        resp = clinician_client.post(
            f'/api/v1/notes/{signed_note.id}/addendums/',
            {'body': 'First'}, format='json',
        )
        addendum_id = resp.data['id']
        # PUT to the same nested URL should hit 405 Method Not Allowed
        # (the @action only allows ['get', 'post']).
        put_resp = clinician_client.put(
            f'/api/v1/notes/{signed_note.id}/addendums/',
            {'body': 'Mutated'}, format='json',
        )
        assert put_resp.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

        # Verify the row didn't change.
        addendum = Addendum.objects.get(pk=addendum_id)
        assert addendum.body == 'First'
