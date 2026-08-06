"""
One-off remediation: strip PHI values out of historical audit_logs rows.

Context. Until commit 8ef874f the audit log captured PHI in its `changes`
payload two ways: note sign / co-sign / session-start handlers wrote
`client_name` explicitly, and AuditMiddleware stored whole request bodies with
a redaction list that missed names, contact details, addresses, diagnoses and
note content. New rows are clean. This command fixes the rows already written.

What it does NOT do. It never deletes a row and never touches who / what /
when / where / record_id. The audit trail stays complete and every entry
remains attributable — only the PHI *values* inside `changes` are replaced
with a redaction marker, exactly as the middleware now does at write time. The
log keeps answering "which fields did this user change?" without holding the
data.

Safety. Dry-run is the default. Nothing is written unless --commit is passed
explicitly. Take a database snapshot first regardless: this is a one-way
transformation and the original values are not recoverable afterwards. That is
the point.

Usage:
    python manage.py scrub_audit_phi                 # dry run, prints a plan
    python manage.py scrub_audit_phi --commit        # actually writes
    python manage.py scrub_audit_phi --batch-size 1000
"""
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.audit.models import AuditLog
from apps.core.sentry import REDACTED, scrub_phi


class Command(BaseCommand):
    help = 'Redact PHI values from historical audit_logs.changes payloads.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--commit',
            action='store_true',
            help='Actually write the changes. Without this the command only reports.',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=500,
            help='Rows per database transaction (default 500).',
        )

    def handle(self, *args, **options):
        commit = options['commit']
        batch_size = options['batch_size']

        total = AuditLog.objects.exclude(changes=None).count()
        self.stdout.write(f'Scanning {total:,} audit_logs rows with a payload...')

        # action -> set of PHI keys found; plus a per-action row counter.
        keys_by_action = defaultdict(set)
        rows_by_action = defaultdict(int)
        pending = []
        scanned = 0
        affected = 0

        queryset = (
            AuditLog.objects
            .exclude(changes=None)
            .only('id', 'action', 'changes')
            .order_by('timestamp')
            .iterator(chunk_size=batch_size)
        )

        for row in queryset:
            scanned += 1
            scrubbed = scrub_phi(row.changes)
            if scrubbed == row.changes:
                continue

            affected += 1
            rows_by_action[row.action] += 1
            keys_by_action[row.action].update(_redacted_keys(row.changes, scrubbed))

            if commit:
                row.changes = scrubbed
                pending.append(row)
                if len(pending) >= batch_size:
                    self._flush(pending)
                    pending = []

        if commit and pending:
            self._flush(pending)

        self._report(rows_by_action, keys_by_action, affected, commit)

    def _flush(self, rows):
        with transaction.atomic():
            AuditLog.objects.bulk_update(rows, ['changes'])

    def _report(self, rows_by_action, keys_by_action, affected, commit):
        self.stdout.write('')
        if not affected:
            self.stdout.write(self.style.SUCCESS(
                'No PHI found in any audit payload — nothing to do.'
            ))
            return

        for action in sorted(rows_by_action, key=lambda a: -rows_by_action[a]):
            keys = ', '.join(sorted(keys_by_action[action])) or '(nested)'
            self.stdout.write(
                f'  action={action:<18} {rows_by_action[action]:>6,} rows   keys: {keys}'
            )

        self.stdout.write('')
        if commit:
            self.stdout.write(self.style.SUCCESS(
                f'{affected:,} rows scrubbed.'
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f'{affected:,} rows WOULD be modified. No changes written.\n'
                'Re-run with --commit to apply (take a database snapshot first).'
            ))


def _redacted_keys(original, scrubbed, prefix=''):
    """
    Report which key paths changed, so the dry-run plan names the fields being
    redacted rather than just counting rows.
    """
    found = set()
    if isinstance(original, dict) and isinstance(scrubbed, dict):
        for key, before in original.items():
            after = scrubbed.get(key)
            path = f'{prefix}{key}'
            if after == REDACTED and before != REDACTED:
                found.add(path)
            else:
                found |= _redacted_keys(before, after, prefix=f'{path}.')
    elif isinstance(original, (list, tuple)) and isinstance(scrubbed, (list, tuple)):
        for before, after in zip(original, scrubbed):
            found |= _redacted_keys(before, after, prefix=prefix)
    return found
