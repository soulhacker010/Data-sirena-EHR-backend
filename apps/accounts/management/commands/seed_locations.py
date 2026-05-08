"""
Seed Location records for an organization from a JSON data file.

This command is the single source of truth for office-location data. It is
deterministic, transactional, and idempotent — re-running it never duplicates
records and either creates or updates rows to match the data file.

Usage:
    python manage.py seed_locations
    python manage.py seed_locations --data-file path/to/locations.json
    python manage.py seed_locations --clear           # archive existing locs first
    python manage.py seed_locations --dry-run         # validate only, no writes

Data file schema (JSON):
    {
      "organization": {"tax_id": "83-2541331", "name": "..."},
      "locations": [
        {
          "name": "Franklin Lakes",
          "address": "851 Franklin Lake Road, Suite 204",
          "city": "Franklin Lakes",
          "state": "NJ",
          "zip_code": "07417-2267",
          "is_telehealth": false,
          "is_active": true
        },
        ...
      ]
    }

Lookup is by `tax_id` if present, else by `name`. Required string fields are
validated (state must be 2 uppercase letters; zip must be NNNNN or NNNNN-NNNN)
before any database write. The whole load runs inside a single transaction.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import Location, Organization


DEFAULT_DATA_FILE: Path = (
    Path(__file__).resolve().parents[2] / 'data' / 'bsbh_locations.json'
)

REQUIRED_STRING_FIELDS = ('name', 'address', 'city', 'state', 'zip_code')
OPTIONAL_BOOL_FIELDS = ('is_telehealth', 'is_active', 'is_primary')

STATE_RE = re.compile(r'^[A-Z]{2}$')
ZIP_RE = re.compile(r'^\d{5}(-\d{4})?$')


class Command(BaseCommand):
    help = 'Seed Location records for an organization from a JSON data file.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--data-file',
            default=str(DEFAULT_DATA_FILE),
            help=f'Path to JSON data file (default: {DEFAULT_DATA_FILE}).',
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Soft-archive (is_active=False) all existing locations for the '
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
        locations_raw = payload.get('locations')
        if not isinstance(locations_raw, list) or not locations_raw:
            raise CommandError('Data file must include a non-empty "locations" array.')

        cleaned = self._validate_all(locations_raw)

        self.stdout.write(self.style.HTTP_INFO(
            f'Target organization: {org.name} (tax_id={org.tax_id or "-"})'
        ))
        self.stdout.write(f'Locations in data file: {len(cleaned)}')

        if options['dry_run']:
            self.stdout.write(self.style.WARNING(
                '[--dry-run] No changes will be written.'
            ))
            for row in cleaned:
                self.stdout.write(
                    f'  - {row["name"]} -> {row["address"]}, '
                    f'{row["city"]}, {row["state"]} {row["zip_code"]}'
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
                    f'Create it (admin or `python manage.py seed_demo`) '
                    f'before seeding locations.'
                )

        try:
            return Organization.objects.get(name=org_name)
        except Organization.DoesNotExist:
            raise CommandError(
                f'Organization with name={org_name!r} not found.'
            )
        except Organization.MultipleObjectsReturned:
            raise CommandError(
                f'Multiple organizations match name={org_name!r}; '
                f'specify "tax_id" instead to disambiguate.'
            )

    # ─── Validation ────────────────────────────────────────────────────────

    def _validate_all(self, rows: list[Any]) -> list[dict[str, Any]]:
        errors: list[str] = []
        cleaned: list[dict[str, Any]] = []
        seen_names: set[str] = set()

        for idx, raw in enumerate(rows):
            row_errors, row_clean = self._validate_row(raw, idx)
            errors.extend(row_errors)
            if row_errors or 'name' not in row_clean:
                continue
            if row_clean['name'] in seen_names:
                errors.append(
                    f'row {idx}: duplicate name {row_clean["name"]!r} in data file'
                )
                continue
            seen_names.add(row_clean['name'])
            cleaned.append(row_clean)

        # At most one row may be marked is_primary=True per data file.
        # The DB also enforces this with a partial unique constraint.
        primary_rows = [r for r in cleaned if r.get('is_primary') is True]
        if len(primary_rows) > 1:
            names = ', '.join(repr(r['name']) for r in primary_rows)
            errors.append(
                f'data file declares {len(primary_rows)} primary locations '
                f'({names}); at most one is allowed per organization.'
            )

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

        if 'state' in clean and not STATE_RE.fullmatch(clean['state']):
            errors.append(
                f'row {idx} (state): {clean["state"]!r} is not a 2-letter '
                f'uppercase state code'
            )
        if 'zip_code' in clean and not ZIP_RE.fullmatch(clean['zip_code']):
            errors.append(
                f'row {idx} (zip_code): {clean["zip_code"]!r} is not in '
                f'NNNNN or NNNNN-NNNN format'
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
            archived = Location.objects.filter(organization=org).update(is_active=False)
            self.stdout.write(self.style.WARNING(
                f'[--clear] Archived {archived} existing location(s) for {org.name}.'
            ))

        # If the data file declares a new primary, demote any existing primary
        # for this org first. The DB partial-unique-constraint would otherwise
        # reject the update_or_create that flips a second row to is_primary=True.
        new_primary_name = next(
            (r['name'] for r in rows if r.get('is_primary') is True),
            None,
        )
        if new_primary_name is not None:
            demoted = Location.objects.filter(
                organization=org, is_primary=True,
            ).exclude(name=new_primary_name).update(is_primary=False)
            if demoted:
                self.stdout.write(self.style.WARNING(
                    f'  Demoted {demoted} previously-primary location(s) '
                    f'so {new_primary_name!r} can become primary.'
                ))

        created = updated = 0
        for row in rows:
            defaults = {
                'address': row['address'],
                'city': row['city'],
                'state': row['state'],
                'zip_code': row['zip_code'],
                'is_telehealth': row.get('is_telehealth', False),
                'is_active': row.get('is_active', True),
                'is_primary': row.get('is_primary', False),
            }
            obj, was_created = Location.objects.update_or_create(
                organization=org,
                name=row['name'],
                defaults=defaults,
            )
            if was_created:
                created += 1
                self.stdout.write(f'  [CREATED] {obj.name}{" *PRIMARY*" if obj.is_primary else ""}')
            else:
                updated += 1
                self.stdout.write(f'  [UPDATED] {obj.name}{" *PRIMARY*" if obj.is_primary else ""}')

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Done. Created={created} Updated={updated} Total={created + updated}'
        ))
