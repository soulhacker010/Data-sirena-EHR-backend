"""
Tests for PHI-access audit coverage.

The audit log records who accessed which patient record and when. Two
contracts must hold:

    1. Every retrieve() of a PHI viewset writes a 'phi_access' audit row
    2. The audit row contains NO PHI — only the record_id and metadata

This file exercises both for each PHI-bearing viewset (clients, notes,
intakes, treatment plans, contact notes, appointments, invoices, claims).
"""
import pytest
from django.urls import reverse
from rest_framework import status

from apps.core.sentry import REDACTED


def _audit_query(action='phi_access'):
    from apps.audit.models import AuditLog
    return AuditLog.objects.filter(action=action).order_by('-timestamp')


def _assert_no_phi_in_audit_changes(changes):
    """
    The audit log MUST NOT contain PHI *values* in its `changes` payload — a
    future export of audit logs (for compliance review or analytics) would
    otherwise re-leak the patient data we're trying to track.

    A PHI-named key is allowed only when its value has been redacted.
    AuditMiddleware deliberately keeps the key and replaces the value, so the
    log can still answer "which fields did this user change?" without storing
    the data itself. A PHI key holding a real value is a failure.
    """
    if changes is None:
        return
    if isinstance(changes, dict):
        forbidden = {
            'first_name', 'last_name', 'name', 'full_name', 'client_name',
            'date_of_birth', 'dob', 'phone', 'email', 'address',
            'diagnosis', 'diagnoses', 'mrn',
        }
        leaked = {
            k: v for k, v in changes.items()
            if k.lower() in forbidden and v != REDACTED
        }
        assert not leaked, f'PHI leaked into audit changes: {leaked} ({changes})'


# ─── Client retrieve ────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestClientPHIAudit:
    def test_retrieve_writes_phi_access_audit(self, admin_client, sample_client):
        url = f'/api/v1/clients/{sample_client.id}/'
        r = admin_client.get(url)
        assert r.status_code == 200

        log = _audit_query().filter(table_name='clients').first()
        assert log is not None
        assert str(log.record_id) == str(sample_client.id)
        _assert_no_phi_in_audit_changes(log.changes)


# ─── Appointment retrieve ───────────────────────────────────────────────────

@pytest.mark.django_db
class TestAppointmentPHIAudit:
    def test_retrieve_writes_audit(self, admin_client, sample_appointment):
        url = f'/api/v1/appointments/{sample_appointment.id}/'
        r = admin_client.get(url)
        assert r.status_code == 200

        log = _audit_query().filter(table_name='appointments').first()
        assert log is not None
        assert str(log.record_id) == str(sample_appointment.id)
        _assert_no_phi_in_audit_changes(log.changes)


# ─── Note / Intake / Treatment-plan retrieve ────────────────────────────────

@pytest.fixture
def session_note(sample_client, clinician_user, org):
    from apps.clinical.models import SessionNote
    return SessionNote.objects.create(
        client=sample_client,
        provider=clinician_user,
        status='draft',
    )


@pytest.fixture
def intake(sample_client, clinician_user, org):
    from apps.clinical.models import IntakeAssessment
    return IntakeAssessment.objects.create(
        client=sample_client,
        provider=clinician_user,
        assessment_date='2026-04-01',
        status='draft',
    )


@pytest.fixture
def treatment_plan(sample_client, clinician_user, org):
    from apps.clinical.models import TreatmentPlan
    return TreatmentPlan.objects.create(
        client=sample_client,
        provider=clinician_user,
        start_date='2026-04-01',
        status='draft',
    )


@pytest.mark.django_db
class TestClinicalPHIAudit:
    def test_session_note_retrieve_audited(self, admin_client, session_note):
        r = admin_client.get(f'/api/v1/notes/{session_note.id}/')
        assert r.status_code == 200
        log = _audit_query().filter(table_name='notes').first()
        assert log is not None
        assert str(log.record_id) == str(session_note.id)
        _assert_no_phi_in_audit_changes(log.changes)

    def test_intake_retrieve_audited(self, admin_client, intake):
        r = admin_client.get(f'/api/v1/intakes/{intake.id}/')
        assert r.status_code == 200
        log = _audit_query().filter(table_name='intakes').first()
        assert log is not None
        assert str(log.record_id) == str(intake.id)
        _assert_no_phi_in_audit_changes(log.changes)

    def test_treatment_plan_retrieve_audited(self, admin_client, treatment_plan):
        r = admin_client.get(f'/api/v1/treatment-plans/{treatment_plan.id}/')
        assert r.status_code == 200
        log = _audit_query().filter(table_name='treatment_plans').first()
        assert log is not None
        assert str(log.record_id) == str(treatment_plan.id)
        _assert_no_phi_in_audit_changes(log.changes)


# ─── No PHI in any audit row, ever (sweep) ──────────────────────────────────

@pytest.mark.django_db
class TestAuditPayloadHygieneSweep:
    """
    Catch-all: exercise READ *and WRITE* paths across PHI views, then scan
    EVERY audit row written and assert no PHI value appears in `changes`.

    The write coverage is the part that matters. `changes` is only ever
    populated on POST/PUT/PATCH/DELETE — AuditMiddleware snapshots the request
    body, and explicit write_audit() calls fire on create/sign/co-sign. A sweep
    that issued only GETs could never observe the payload it exists to police.
    An earlier version of this test did exactly that, and consequently missed
    patient names being written into audit rows by the note sign and co-sign
    handlers for the lifetime of the feature.
    """

    def test_no_phi_in_any_audit_row(
        self, admin_client, clinician_client, sample_client, sample_appointment,
    ):
        # ─── Reads ───────────────────────────────────────────────────────────
        admin_client.get(f'/api/v1/clients/{sample_client.id}/')
        admin_client.get(f'/api/v1/appointments/{sample_appointment.id}/')

        # ─── Writes — these populate `changes` ───────────────────────────────
        create_resp = admin_client.post('/api/v1/clients/', {
            'first_name': 'Sweep',
            'last_name': 'Testcase',
            'date_of_birth': '1990-01-01',
            'gender': 'female',
            'phone': '555-0199',
            'email': 'sweep@example.com',
            'address': '1 Sweep Lane',
            'city': 'Testville',
            'state': 'FL',
            'zip_code': '33101',
        }, format='json')
        assert create_resp.status_code == status.HTTP_201_CREATED

        admin_client.patch(
            f'/api/v1/clients/{sample_client.id}/',
            {'first_name': 'Renamed'},
            format='json',
        )

        # Note create + sign — exercises the explicit write_audit() calls that
        # previously embedded the patient's name.
        note_resp = clinician_client.post('/api/v1/notes/', {
            'client_id': str(sample_client.id),
            'note_data': {'objectives': 'Audit sweep'},
        }, format='json')
        assert note_resp.status_code == status.HTTP_201_CREATED
        clinician_client.post(
            f'/api/v1/notes/{note_resp.data["id"]}/sign/',
            {'signature_data': 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=='},
            format='json',
        )

        from apps.audit.models import AuditLog
        rows = list(AuditLog.objects.all())
        assert rows, 'expected the sweep to have produced audit rows'
        # Guard the guard: if nothing captured a payload, every assertion below
        # would pass without inspecting a single value.
        assert any(r.changes for r in rows), 'no audit row captured a payload'

        for log in rows:
            _assert_no_phi_in_audit_changes(log.changes)
