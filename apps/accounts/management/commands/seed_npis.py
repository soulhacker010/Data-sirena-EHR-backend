"""
Seed NPI records for an organization from a JSON data file.

NPIs (National Provider Identifiers) are the federal identifiers used on every
837P claim and on CMS-1500 forms. We validate each NPI with the CMS Luhn check
(prefix 80840 + 9-digit identifier + check digit) before any database write.

Usage:
    python manage.py seed_npis
    python manage.py seed_npis --data-file path/to/npis.json
    python manage.py seed_npis --clear     # archive existing NPIs (is_active=False)
    python manage.py seed_npis --dry-run   # validate only

Data file schema (JSON):
    {
      "organization": {"tax_id": "83-2541331", "name": "..."},
      "npis": [
        {
          "npi_number": "1659841096",
          "business_name": "Baker Street Behavioral Health",
          "is_active": true
        }
      ]
    }

Idempotent: NPIs are looked up globally by `npi_number` (model has unique=True).
If an NPI exists for a *different* organization the seed aborts loudly rather
than silently re-assigning it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import NPI, Organization
from apps.accounts.npi import luhn_validate_npi


DEFAULT_DATA_FILE: Path = (
    Path(__file__).resolve().parents[2] / 'data' / 'bsbh_npis.json'
)

REQUIRED_STRING_FIELDS = ('npi_number', 'business_name')
OPTIONAL_BOOL_FIELDS = ('is_active',)


class Command(BaseCommand):
    help = 'Seed NPI records for an organization from a JSON data file.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--data-file',
            default=str(DEFAULT_DATA_FILE),
            help=f'Path to JSON data file (default: {DEFAULT_DATA_FILE}).',
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Soft-archive (is_active=False) all existing NPIs for the '
                 'org before loading.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Validate the data file and report what would happen, '
                 'without writing to the database.',
        )

    def handle(self, *args, **options):
        data_path = Path(options['data_file']).resolve()
        if not data_path.exists():
            raise CommandError(f'Data file not found: {data_path}')

        try:
            with data_path.open(encoding='utf-8') as f:
                payload = json.load(f)
        except json.JSONDecodeError as exc:
            raise CommandError(f'Invalid JSON in {data_path}: {exc}') from exc

        if not isinstance(payload, dict):
            raise CommandError('Data file must be a JSON object.')

        org = self._resolve_organization(payload.get('organization') or {})
        npis_raw = payload.get('npis')
        if not isinstance(npis_raw, list) or not npis_raw:
            raise CommandError('Data file must include a non-empty "npis" array.')

        cleaned = self._validate_all(npis_raw)

        self.stdout.write(self.style.HTTP_INFO(
            f'Target organization: {org.name} (tax_id={org.tax_id or "-"})'
        ))
        self.stdout.write(f'NPIs in data file: {len(cleaned)}')

        if options['dry_run']:
            self.stdout.write(self.style.WARNING(
                '[--dry-run] No changes will be written.'
            ))
            for row in cleaned:
                active = '' if row.get('is_active', True) else ' [INACTIVE]'
                self.stdout.write(
                    f'  - {row["npi_number"]} ({row["business_name"]}){active}'
                )
            return

        self._apply(org, cleaned, clear=options['clear'])

    # ─── Organization lookup ───────────────────────────────────────────────

    def _resolve_organization(self, spec: dict[str, Any]) -> Organization:
        tax_id = (spec.get('tax_id') or '').strip()
        org_name = (spec.get('name') or '').strip()

        if not tax_id and not org_name:
            raise CommandError(
                'Data file "organization" must include "tax_id" or "name".'
            )

        if tax_id:
            try:
                return Organization.objects.get(tax_id=tax_id)
            except Organization.DoesNotExist:
                raise CommandError(
                    f'Organization with tax_id={tax_id!r} not found. '
                    f'Create the org first, then re-run.'
                )

        try:
            return Organization.objects.get(name=org_name)
        except Organization.DoesNotExist:
            raise CommandError(f'Organization with name={org_name!r} not found.')
        except Organization.MultipleObjectsReturned:
            raise CommandError(
                f'Multiple organizations match name={org_name!r}; '
                f'specify "tax_id" instead to disambiguate.'
            )

    # ─── Validation ────────────────────────────────────────────────────────

    def _validate_all(self, rows: list[Any]) -> list[dict[str, Any]]:
        errors: list[str] = []
        cleaned: list[dict[str, Any]] = []
        seen_npis: set[str] = set()

        for idx, raw in enumerate(rows):
            row_errors, row_clean = self._validate_row(raw, idx)
            errors.extend(row_errors)
            if row_errors or 'npi_number' not in row_clean:
                continue
            if row_clean['npi_number'] in seen_npis:
                errors.append(
                    f'row {idx}: duplicate npi_number '
                    f'{row_clean["npi_number"]!r} in data file'
                )
                continue
            seen_npis.add(row_clean['npi_number'])
            cleaned.append(row_clean)

        if errors:
            for err in errors:
                self.stderr.write(self.style.ERROR(err))
            raise CommandError(
                f'Aborting: {len(errors)} validation error(s) in data file.'
            )

        return cleaned

    def _validate_row(self, raw: Any, idx: int) -> tuple[list[str], dict[str, Any]]:
        errors: list[str] = []
        clean: dict[str, Any] = {}

        if not isinstance(raw, dict):
            return [f'row {idx}: must be an object'], clean

        for field in REQUIRED_STRING_FIELDS:
            val = raw.get(field)
            if val is None:
                errors.append(f'row {idx}: missing required field {field!r}')
                continue
            if not isinstance(val, str):
                errors.append(f'row {idx} ({field!r}): must be a string')
                continue
            stripped = val.strip()
            if not stripped:
                errors.append(f'row {idx} ({field!r}): must not be empty')
                continue
            clean[field] = stripped

        if 'npi_number' in clean:
            npi = clean['npi_number']
            if len(npi) != 10 or not npi.isdigit():
                errors.append(
                    f'row {idx} (npi_number): {npi!r} must be exactly 10 digits'
                )
            elif not luhn_validate_npi(npi):
                errors.append(
                    f'row {idx} (npi_number): {npi!r} fails the CMS Luhn '
                    f'check (NPI prefix 80840). This is not a valid NPI.'
                )

        for field in OPTIONAL_BOOL_FIELDS:
            if field in raw:
                if not isinstance(raw[field], bool):
                    errors.append(
                        f'row {idx} ({field!r}): must be a boolean if present'
                    )
                else:
                    clean[field] = raw[field]

        return errors, clean

    # ─── Apply (DB writes) ─────────────────────────────────────────────────

    @transaction.atomic
    def _apply(self, org: Organization, rows: list[dict[str, Any]], *, clear: bool) -> None:
        if clear:
            archived = NPI.objects.filter(organization=org).update(is_active=False)
            self.stdout.write(self.style.WARNING(
                f'[--clear] Archived {archived} existing NPI(s) for {org.name}.'
            ))

        # NPI.npi_number has unique=True (global). Look up cross-org to detect
        # conflicts before writing.
        created = updated = 0
        for row in rows:
            npi_num = row['npi_number']
            existing = NPI.objects.filter(npi_number=npi_num).first()

            if existing is None:
                NPI.objects.create(
                    organization=org,
                    npi_number=npi_num,
                    business_name=row['business_name'],
                    is_active=row.get('is_active', True),
                )
                created += 1
                self.stdout.write(f'  [CREATED] {npi_num} ({row["business_name"]})')
                continue

            if existing.organization_id != org.id:
                raise CommandError(
                    f'NPI {npi_num} already exists in the database but is '
                    f'assigned to organization '
                    f'{existing.organization.name!r} '
                    f'(id={existing.organization_id}), not '
                    f'{org.name!r} (id={org.id}). '
                    f'Refusing to silently re-assign.'
                )

            existing.business_name = row['business_name']
            existing.is_active = row.get('is_active', True)
            existing.save(update_fields=['business_name', 'is_active', 'updated_at'])
            updated += 1
            self.stdout.write(f'  [UPDATED] {npi_num} ({row["business_name"]})')

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Done. Created={created} Updated={updated} Total={created + updated}'
        ))
