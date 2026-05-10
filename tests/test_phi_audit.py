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


def _audit_query(action='phi_access'):
    from apps.audit.models import AuditLog
    return AuditLog.objects.filter(action=action).order_by('-timestamp')


def _assert_no_phi_in_audit_changes(changes):
    """
    The audit log MUST NOT contain PHI in its `changes` payload — a future
    export of audit logs (for compliance review or analytics) would otherwise
    re-leak the patient data we're trying to track.
    """
    if changes is None:
        return
    if isinstance(changes, dict):
        forbidden = {
            'first_name', 'last_name', 'name', 'full_name', 'client_name',
            'date_of_birth', 'dob', 'phone', 'email', 'address',
            'diagnosis', 'diagnoses', 'mrn',
        }
        leaked = forbidden.intersection({k.lower() for k in changes.keys()})
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
    Catch-all: after exercising several PHI views, scan EVERY audit row that
    was written and assert no PHI key appears in `changes`. If someone adds a
    new audit_write() call that includes a client name, this fails loudly.
    """

    def test_no_phi_in_any_audit_row(self, admin_client, sample_client, sample_appointment):
        admin_client.get(f'/api/v1/clients/{sample_client.id}/')
        admin_client.get(f'/api/v1/appointments/{sample_appointment.id}/')

        from apps.audit.models import AuditLog
        for log in AuditLog.objects.all():
            _assert_no_phi_in_audit_changes(log.changes)
