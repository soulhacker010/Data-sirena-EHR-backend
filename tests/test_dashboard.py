"""
Tests for Dashboard Stats — item 6.1 (Total Clients = 0 bug) and 6.3 (pending notes logic).
"""
import pytest
from django.urls import reverse
from rest_framework import status

from apps.accounts.models import Organization, User
from apps.clients.models import Client
from apps.scheduling.models import Appointment
from apps.clinical.models import SessionNote

import datetime


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def org(db):
    return Organization.objects.create(name='Test Clinic')


@pytest.fixture
def admin(org):
    return User.objects.create_user(
        email='admin@test.com',
        password='pass',
        first_name='Admin',
        last_name='User',
        role='admin',
        organization=org,
    )


@pytest.fixture
def clinician(org):
    return User.objects.create_user(
        email='clinician@test.com',
        password='pass',
        first_name='Dr',
        last_name='Jones',
        role='clinician',
        organization=org,
    )


@pytest.fixture
def client_record(org):
    return Client.objects.create(
        organization=org,
        first_name='Jane',
        last_name='Doe',
        date_of_birth='1990-01-01',
        is_active=True,
    )


@pytest.fixture
def appointment(org, clinician, client_record):
    return Appointment.objects.create(
        organization=org,
        client=client_record,
        provider=clinician,
        start_time=datetime.datetime(2026, 4, 10, 10, 0, tzinfo=datetime.timezone.utc),
        end_time=datetime.datetime(2026, 4, 10, 11, 0, tzinfo=datetime.timezone.utc),
        service_code='90837',
        status='attended',
    )


# ─── 6.1: Total Clients ───────────────────────────────────────────────────────

@pytest.mark.django_db
def test_dashboard_total_clients_returns_correct_count(api_client, admin, client_record, org):
    """Total Clients must reflect org-wide active clients, not 0."""
    Client.objects.create(
        organization=org, first_name='John', last_name='Smith',
        date_of_birth='1985-05-05', is_active=True,
    )
    api_client.force_authenticate(user=admin)
    response = api_client.get(reverse('dashboard-stats'))
    assert response.status_code == status.HTTP_200_OK
    assert response.data['total_clients'] == 2


@pytest.mark.django_db
def test_dashboard_excludes_inactive_clients(api_client, admin, org):
    """Inactive clients should not count toward total."""
    Client.objects.create(
        organization=org, first_name='Active', last_name='Client',
        date_of_birth='1990-01-01', is_active=True,
    )
    Client.objects.create(
        organization=org, first_name='Inactive', last_name='Client',
        date_of_birth='1990-01-01', is_active=False,
    )
    api_client.force_authenticate(user=admin)
    response = api_client.get(reverse('dashboard-stats'))
    assert response.data['total_clients'] == 1


@pytest.mark.django_db
def test_dashboard_org_scoping(api_client, org, admin):
    """Clients from a different org must not be counted."""
    other_org = Organization.objects.create(name='Other Clinic')
    Client.objects.create(
        organization=other_org, first_name='Other', last_name='Client',
        date_of_birth='1990-01-01', is_active=True,
    )
    api_client.force_authenticate(user=admin)
    response = api_client.get(reverse('dashboard-stats'))
    assert response.data['total_clients'] == 0


# ─── 6.3: Pending Notes ──────────────────────────────────────────────────────

@pytest.mark.django_db
def test_pending_notes_increments_after_attended_appointment(
    api_client, admin, appointment
):
    """An attended appointment with no note should appear in pending_notes."""
    api_client.force_authenticate(user=admin)
    response = api_client.get(reverse('dashboard-stats'))
    assert response.status_code == status.HTTP_200_OK
    assert response.data['pending_notes'] >= 1


@pytest.mark.django_db
def test_pending_notes_not_counted_when_signed(
    api_client, admin, appointment, clinician, client_record, org
):
    """An attended appointment with a signed note should NOT add to pending_notes."""
    SessionNote.objects.create(
        client=client_record,
        provider=clinician,
        appointment=appointment,
        status='signed',
        note_data={},
    )
    api_client.force_authenticate(user=admin)
    response = api_client.get(reverse('dashboard-stats'))
    assert response.status_code == status.HTTP_200_OK
    assert response.data['pending_notes'] == 0


@pytest.mark.django_db
def test_pending_notes_counts_draft_notes(
    api_client, admin, appointment, clinician, client_record, org
):
    """A draft note that exists should still count as pending."""
    SessionNote.objects.create(
        client=client_record,
        provider=clinician,
        appointment=appointment,
        status='draft',
        note_data={},
    )
    api_client.force_authenticate(user=admin)
    response = api_client.get(reverse('dashboard-stats'))
    assert response.data['pending_notes'] >= 1


# ─── B6: Sessions This Month should count signed notes ─────────────────────

@pytest.mark.django_db
def test_sessions_this_month_counts_appointment_with_signed_note_even_if_status_scheduled(
    api_client, admin, clinician, client_record, org,
):
    """Dr. Joe's bug: signed note exists, but appointment status was never
    flipped to 'attended' → sessions_this_month was 0 despite the session
    obviously having happened. The fix: count attended OR has-signed-note.
    """
    from django.utils import timezone
    now = timezone.now()
    month_start = now.replace(day=1, hour=10, minute=0, second=0, microsecond=0)

    appt = Appointment.objects.create(
        organization=org,
        client=client_record,
        provider=clinician,
        start_time=month_start,
        end_time=month_start + datetime.timedelta(hours=1),
        service_code='90837',
        status='scheduled',  # NOT attended — but the note IS signed
    )
    SessionNote.objects.create(
        client=client_record,
        provider=clinician,
        appointment=appt,
        status='signed',
        note_data={},
    )

    api_client.force_authenticate(user=admin)
    response = api_client.get(reverse('dashboard-stats'))
    assert response.status_code == status.HTTP_200_OK
    assert response.data['sessions_this_month'] >= 1, (
        'Signed note implies session happened — must count even when '
        'appointment.status is still "scheduled"'
    )


@pytest.mark.django_db
def test_sessions_this_month_still_counts_attended_without_note(
    api_client, admin, clinician, client_record, org,
):
    """Backwards-compat: appointments marked attended still count even with no note."""
    from django.utils import timezone
    now = timezone.now()
    Appointment.objects.create(
        organization=org, client=client_record, provider=clinician,
        start_time=now.replace(day=1, hour=10, minute=0, second=0, microsecond=0),
        end_time=now.replace(day=1, hour=11, minute=0, second=0, microsecond=0),
        service_code='90837', status='attended',
    )
    api_client.force_authenticate(user=admin)
    response = api_client.get(reverse('dashboard-stats'))
    assert response.data['sessions_this_month'] >= 1


@pytest.mark.django_db
def test_sessions_this_month_does_not_double_count(
    api_client, admin, clinician, client_record, org,
):
    """An appointment that is BOTH attended AND has a signed note counts once."""
    from django.utils import timezone
    now = timezone.now()
    appt = Appointment.objects.create(
        organization=org, client=client_record, provider=clinician,
        start_time=now.replace(day=1, hour=10, minute=0, second=0, microsecond=0),
        end_time=now.replace(day=1, hour=11, minute=0, second=0, microsecond=0),
        service_code='90837', status='attended',
    )
    SessionNote.objects.create(
        client=client_record, provider=clinician, appointment=appt,
        status='signed', note_data={},
    )
    api_client.force_authenticate(user=admin)
    response = api_client.get(reverse('dashboard-stats'))
    assert response.data['sessions_this_month'] == 1


@pytest.mark.django_db
def test_pending_notes_counts_past_scheduled_appointment_with_no_note(
    api_client, admin, clinician, client_record, org,
):
    """E23 (Dr. Joe): "I had an appointment 2 days ago and it didnt pop up"
    in Pending Notes. Provider didn't flip to 'attended' — system must still
    count past scheduled appointments without a note as pending."""
    from django.utils import timezone
    two_days_ago = timezone.now() - datetime.timedelta(days=2)

    Appointment.objects.create(
        organization=org, client=client_record, provider=clinician,
        start_time=two_days_ago,
        end_time=two_days_ago + datetime.timedelta(hours=1),
        service_code='90834',
        status='scheduled',  # Never manually flipped — that's the bug
    )

    api_client.force_authenticate(user=admin)
    response = api_client.get(reverse('dashboard-stats'))
    assert response.status_code == status.HTTP_200_OK
    assert response.data['pending_notes'] >= 1


@pytest.mark.django_db
def test_pending_notes_does_not_count_future_scheduled(
    api_client, admin, clinician, client_record, org,
):
    """Future appointments shouldn't be in pending — they haven't happened."""
    from django.utils import timezone
    in_three_days = timezone.now() + datetime.timedelta(days=3)
    Appointment.objects.create(
        organization=org, client=client_record, provider=clinician,
        start_time=in_three_days,
        end_time=in_three_days + datetime.timedelta(hours=1),
        service_code='90834',
        status='scheduled',
    )

    api_client.force_authenticate(user=admin)
    response = api_client.get(reverse('dashboard-stats'))
    # Whatever the baseline (from other fixtures), this future appointment
    # specifically must not be in pending — assert by ensuring count is 0
    # for this isolated test setup.
    assert response.data['pending_notes'] == 0


@pytest.mark.django_db
def test_pending_notes_does_not_count_cancelled(
    api_client, admin, clinician, client_record, org,
):
    """Cancelled appointments don't need notes."""
    from django.utils import timezone
    yesterday = timezone.now() - datetime.timedelta(days=1)
    Appointment.objects.create(
        organization=org, client=client_record, provider=clinician,
        start_time=yesterday,
        end_time=yesterday + datetime.timedelta(hours=1),
        service_code='90834',
        status='cancelled',
    )

    api_client.force_authenticate(user=admin)
    response = api_client.get(reverse('dashboard-stats'))
    assert response.data['pending_notes'] == 0


@pytest.mark.django_db
def test_sessions_this_month_does_not_count_co_signed_only(
    api_client, admin, clinician, client_record, org,
):
    """Co-signed notes should also count (signed by clinician, then co-signed)."""
    from django.utils import timezone
    now = timezone.now()
    month_start = now.replace(day=1, hour=10, minute=0, second=0, microsecond=0)
    appt = Appointment.objects.create(
        organization=org, client=client_record, provider=clinician,
        start_time=month_start,
        end_time=month_start + datetime.timedelta(hours=1),
        service_code='90837', status='scheduled',
    )
    SessionNote.objects.create(
        client=client_record, provider=clinician, appointment=appt,
        status='co_signed', note_data={},
    )
    api_client.force_authenticate(user=admin)
    response = api_client.get(reverse('dashboard-stats'))
    assert response.data['sessions_this_month'] >= 1


# ─── E29: per-clinician Recent Activity privacy filter ─────────────────────

@pytest.mark.django_db
def test_recent_activity_clinician_sees_only_own_actions(
    api_client, admin, clinician, client_record, org,
):
    """E29 (Dr. Joe): "each clinician should only see activity for their own
    client". Implementation: clinicians see only audit entries where user=self;
    admins see all. This prevents another clinician's client info bleeding
    into someone else's chart view."""
    from apps.audit.models import AuditLog

    AuditLog.objects.create(
        organization=org, user=clinician,
        action='create', table_name='session_notes',
    )
    AuditLog.objects.create(
        organization=org, user=admin,
        action='update', table_name='clients',
    )

    api_client.force_authenticate(user=clinician)
    response = api_client.get(reverse('dashboard-stats'))
    assert response.status_code == status.HTTP_200_OK
    activities = response.data['recent_activity']
    user_names = {a['user_name'] for a in activities}
    # Only the clinician's own action — admin's action is not visible.
    assert user_names == {clinician.full_name}


@pytest.mark.django_db
def test_recent_activity_admin_sees_all_org_activity(
    api_client, admin, clinician, org,
):
    from apps.audit.models import AuditLog
    AuditLog.objects.create(
        organization=org, user=clinician,
        action='create', table_name='session_notes',
    )
    AuditLog.objects.create(
        organization=org, user=admin,
        action='update', table_name='clients',
    )

    api_client.force_authenticate(user=admin)
    response = api_client.get(reverse('dashboard-stats'))
    activities = response.data['recent_activity']
    user_names = {a['user_name'] for a in activities}
    assert {clinician.full_name, admin.full_name}.issubset(user_names)


@pytest.mark.django_db
def test_recent_activity_does_not_leak_other_orgs(
    api_client, admin, org,
):
    """Sanity: org-scoping was already there; this test pins it so the E29
    user-filter we added doesn't accidentally widen the query later."""
    from apps.audit.models import AuditLog
    other_org = Organization.objects.create(name='Other Clinic')
    other_user = User.objects.create_user(
        email='other-act@test.com', password='pass',
        first_name='Other', last_name='Person',
        role='admin', organization=other_org,
    )
    AuditLog.objects.create(
        organization=other_org, user=other_user,
        action='create', table_name='clients',
    )
    AuditLog.objects.create(
        organization=org, user=admin,
        action='create', table_name='clients',
    )

    api_client.force_authenticate(user=admin)
    response = api_client.get(reverse('dashboard-stats'))
    activities = response.data['recent_activity']
    user_names = {a['user_name'] for a in activities}
    assert 'Other Person' not in user_names
    assert admin.full_name in user_names


# ─── E22: Incomplete-drafts widget ─────────────────────────────────────────

@pytest.mark.django_db
def test_incomplete_drafts_lists_unfinished_docs(
    api_client, admin, clinician, client_record, org,
):
    """E22 (Dr. Joe): the dashboard must surface drafts the user started but
    didn't complete — across notes, intakes, and treatment plans."""
    from apps.clinical.models import IntakeAssessment, TreatmentPlan
    SessionNote.objects.create(
        client=client_record, provider=clinician,
        status='draft', note_data={'objectives': 'wip'},
    )
    IntakeAssessment.objects.create(
        client=client_record, provider=clinician,
        assessment_date='2026-04-15',
        status='draft', intake_data={},
    )
    TreatmentPlan.objects.create(
        client=client_record, provider=clinician,
        start_date='2026-04-20',
        status='draft', goals=[],
    )

    api_client.force_authenticate(user=admin)
    response = api_client.get(reverse('dashboard-stats'))
    assert response.status_code == status.HTTP_200_OK
    drafts = response.data['incomplete_drafts']
    kinds = {d['kind'] for d in drafts}
    assert {'session_notes', 'intakes', 'treatment_plans'} == kinds


@pytest.mark.django_db
def test_incomplete_drafts_excludes_signed(
    api_client, admin, clinician, client_record, org,
):
    SessionNote.objects.create(
        client=client_record, provider=clinician,
        status='signed', is_locked=True, note_data={},
    )
    api_client.force_authenticate(user=admin)
    response = api_client.get(reverse('dashboard-stats'))
    assert response.data['incomplete_drafts'] == []


@pytest.mark.django_db
def test_incomplete_drafts_clinician_only_sees_own(
    api_client, admin, clinician, client_record, org,
):
    SessionNote.objects.create(
        client=client_record, provider=clinician,
        status='draft', note_data={},
    )
    SessionNote.objects.create(
        client=client_record, provider=admin,
        status='draft', note_data={},
    )

    api_client.force_authenticate(user=clinician)
    response = api_client.get(reverse('dashboard-stats'))
    drafts = response.data['incomplete_drafts']
    # Two drafts in DB but the clinician only sees their own.
    assert len(drafts) == 1


@pytest.mark.django_db
def test_incomplete_drafts_admin_sees_all_org(
    api_client, admin, clinician, client_record, org,
):
    SessionNote.objects.create(
        client=client_record, provider=clinician,
        status='draft', note_data={},
    )
    SessionNote.objects.create(
        client=client_record, provider=admin,
        status='draft', note_data={},
    )

    api_client.force_authenticate(user=admin)
    response = api_client.get(reverse('dashboard-stats'))
    assert len(response.data['incomplete_drafts']) == 2


# ─── No-org guard ────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_dashboard_returns_400_for_user_without_org(api_client, db):
    """If the user has no org, the dashboard should return 400, not crash."""
    no_org_user = User.objects.create_user(
        email='noorg@test.com',
        password='pass',
        first_name='No',
        last_name='Org',
        role='admin',
        organization=None,
    )
    api_client.force_authenticate(user=no_org_user)
    response = api_client.get(reverse('dashboard-stats'))
    assert response.status_code == status.HTTP_400_BAD_REQUEST
