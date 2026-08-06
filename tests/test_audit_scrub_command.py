"""
Tests for `manage.py scrub_audit_phi`.

This command rewrites rows in the audit table, which is the one table HIPAA
expects to be tamper-evident. So the contract is narrow and worth pinning down
precisely:

    * dry run is the default and must write nothing
    * --commit redacts PHI *values* only
    * who / what / when / where / record_id are never touched
    * no row is ever deleted
    * it is idempotent — running twice changes nothing the second time
"""
from io import StringIO

import pytest
from django.core.management import call_command

from apps.core.sentry import REDACTED


def _run(*args):
    out = StringIO()
    call_command('scrub_audit_phi', *args, stdout=out, stderr=StringIO())
    return out.getvalue()


@pytest.fixture
def dirty_rows(org, admin_user):
    """Audit rows in the shape the old code produced."""
    from apps.audit.models import AuditLog

    explicit = AuditLog.objects.create(
        organization=org,
        user=admin_user,
        action='sign',
        table_name='notes',
        ip_address='198.51.100.7',
        user_agent='pytest',
        changes={
            'client_id': 'bafc1373-06dd-4683-bdc0-f2e27eecc907',
            'client_name': 'Doe, John',
            'signed_by': 'Jane Therapist',
        },
    )
    nested = AuditLog.objects.create(
        organization=org,
        user=admin_user,
        action='create',
        table_name='notes',
        ip_address='198.51.100.7',
        user_agent='pytest',
        changes={
            'client_id': 'bafc1373-06dd-4683-bdc0-f2e27eecc907',
            'note_data': {
                'objectives': 'Session goals',
                'subjective': 'Client reports panic episodes.',
                'diagnosis': 'F41.1',
            },
        },
    )
    clean = AuditLog.objects.create(
        organization=org,
        user=admin_user,
        action='phi_access',
        table_name='clients',
        ip_address='198.51.100.7',
        user_agent='pytest',
        changes=None,
    )
    return explicit, nested, clean


@pytest.mark.django_db
class TestScrubAuditPHI:

    def test_dry_run_writes_nothing(self, dirty_rows):
        explicit, nested, _ = dirty_rows

        output = _run()

        explicit.refresh_from_db()
        nested.refresh_from_db()
        assert explicit.changes['client_name'] == 'Doe, John'
        assert nested.changes['note_data']['subjective'].startswith('Client reports')
        assert 'WOULD be modified' in output
        assert 'client_name' in output

    def test_commit_redacts_phi_values(self, dirty_rows):
        explicit, nested, _ = dirty_rows

        _run('--commit')

        explicit.refresh_from_db()
        nested.refresh_from_db()
        assert explicit.changes['client_name'] == REDACTED
        assert nested.changes['note_data']['subjective'] == REDACTED
        assert nested.changes['note_data']['diagnosis'] == REDACTED

    def test_non_phi_and_metadata_survive(self, dirty_rows):
        explicit, nested, _ = dirty_rows
        original_ts = explicit.timestamp

        _run('--commit')

        explicit.refresh_from_db()
        nested.refresh_from_db()
        # Non-PHI content is untouched.
        assert explicit.changes['client_id'] == 'bafc1373-06dd-4683-bdc0-f2e27eecc907'
        assert explicit.changes['signed_by'] == 'Jane Therapist'
        assert nested.changes['note_data']['objectives'] == 'Session goals'
        # The audit trail itself is intact — this is the whole point.
        assert explicit.action == 'sign'
        assert explicit.table_name == 'notes'
        assert explicit.user_id is not None
        assert str(explicit.ip_address) == '198.51.100.7'
        assert explicit.timestamp == original_ts

    def test_no_rows_deleted(self, dirty_rows):
        from apps.audit.models import AuditLog
        before = AuditLog.objects.count()

        _run('--commit')

        assert AuditLog.objects.count() == before

    def test_idempotent(self, dirty_rows):
        _run('--commit')
        second = _run()
        assert 'nothing to do' in second.lower()
