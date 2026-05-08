"""Integration tests for `python manage.py seed_locations`.

Exercises the full validation + write pipeline against the real BSBH data file
that ships with the repo, plus negative cases.
"""
import json
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.models import Location, Organization


REPO_DATA_FILE = (
    Path(__file__).resolve().parents[1]
    / 'apps' / 'accounts' / 'data' / 'bsbh_locations.json'
)


@pytest.fixture
def bsbh_org(db):
    """Match the tax_id in apps/accounts/data/bsbh_locations.json."""
    return Organization.objects.create(
        name='Baker Street Behavioral Health',
        tax_id='83-2541331',
        contact_email='admin@bakerstreetpsych.com',
        contact_phone='201-381-6136',
    )


@pytest.fixture
def other_org(db):
    return Organization.objects.create(
        name='Some Other Clinic',
        tax_id='99-9999999',
    )


def test_seeds_all_eight_bsbh_locations(bsbh_org):
    call_command('seed_locations', f'--data-file={REPO_DATA_FILE}')

    qs = Location.objects.filter(organization=bsbh_org)
    assert qs.count() == 8

    names = set(qs.values_list('name', flat=True))
    assert names == {
        'Franklin Lakes',
        'Paramus',
        'Cedar Grove',
        'Fair Lawn',
        'Morristown',
        'Flemington',
        "The Dwelling Place at Saint Clare's",
        'Red Bank',
    }


def test_franklin_lakes_fields_match_theranest(bsbh_org):
    """Franklin Lakes is the billing/primary site — exact match matters."""
    call_command('seed_locations', f'--data-file={REPO_DATA_FILE}')

    franklin = Location.objects.get(organization=bsbh_org, name='Franklin Lakes')
    assert franklin.address == '851 Franklin Lake Road, Suite 204'
    assert franklin.city == 'Franklin Lakes'
    assert franklin.state == 'NJ'
    assert franklin.zip_code == '07417-2267'
    assert franklin.is_telehealth is False
    assert franklin.is_active is True


def test_idempotent_double_run_no_duplicates(bsbh_org):
    call_command('seed_locations', f'--data-file={REPO_DATA_FILE}')
    call_command('seed_locations', f'--data-file={REPO_DATA_FILE}')

    assert Location.objects.filter(organization=bsbh_org).count() == 8


def test_organization_must_exist(db):
    with pytest.raises(CommandError, match='Organization with tax_id'):
        call_command('seed_locations', f'--data-file={REPO_DATA_FILE}')


def test_clear_archives_existing(bsbh_org):
    Location.objects.create(
        organization=bsbh_org,
        name='Old Stale Office',
        address='1 Old Street',
        city='Trenton',
        state='NJ',
        zip_code='08608',
        is_active=True,
    )

    call_command('seed_locations', f'--data-file={REPO_DATA_FILE}', '--clear')

    stale = Location.objects.get(organization=bsbh_org, name='Old Stale Office')
    assert stale.is_active is False
    assert Location.objects.filter(
        organization=bsbh_org, is_active=True
    ).count() == 8


def test_dry_run_writes_nothing(bsbh_org):
    call_command('seed_locations', f'--data-file={REPO_DATA_FILE}', '--dry-run')
    assert Location.objects.filter(organization=bsbh_org).count() == 0


def test_does_not_touch_other_orgs(bsbh_org, other_org):
    """Seeding BSBH must not create or modify locations for unrelated orgs."""
    Location.objects.create(
        organization=other_org,
        name='Franklin Lakes',  # same name as BSBH location — must stay isolated
        address='999 Other Place',
        city='Other',
        state='CA',
        zip_code='90001',
    )

    call_command('seed_locations', f'--data-file={REPO_DATA_FILE}')

    other_loc = Location.objects.get(organization=other_org, name='Franklin Lakes')
    assert other_loc.address == '999 Other Place'
    assert other_loc.city == 'Other'
    assert other_loc.state == 'CA'
    assert Location.objects.filter(organization=other_org).count() == 1
    assert Location.objects.filter(organization=bsbh_org).count() == 8


def test_update_path_changes_existing_fields(bsbh_org):
    Location.objects.create(
        organization=bsbh_org,
        name='Franklin Lakes',
        address='WRONG ADDRESS — should be overwritten',
        city='Wrong City',
        state='CA',
        zip_code='99999',
        is_active=False,
    )

    call_command('seed_locations', f'--data-file={REPO_DATA_FILE}')

    franklin = Location.objects.get(organization=bsbh_org, name='Franklin Lakes')
    assert franklin.address == '851 Franklin Lake Road, Suite 204'
    assert franklin.city == 'Franklin Lakes'
    assert franklin.state == 'NJ'
    assert franklin.zip_code == '07417-2267'
    assert franklin.is_active is True


def test_validation_state_format(bsbh_org, tmp_path):
    bad_file = tmp_path / 'bad_state.json'
    bad_file.write_text(json.dumps({
        'organization': {'tax_id': '83-2541331'},
        'locations': [{
            'name': 'X', 'address': '1 St', 'city': 'Y',
            'state': 'NEWJERSEY', 'zip_code': '07417',
        }],
    }))

    with pytest.raises(CommandError, match=r'validation error'):
        call_command('seed_locations', f'--data-file={bad_file}')

    assert Location.objects.filter(organization=bsbh_org).count() == 0


def test_validation_zip_format(bsbh_org, tmp_path):
    bad_file = tmp_path / 'bad_zip.json'
    bad_file.write_text(json.dumps({
        'organization': {'tax_id': '83-2541331'},
        'locations': [{
            'name': 'X', 'address': '1 St', 'city': 'Y',
            'state': 'NJ', 'zip_code': '0741',
        }],
    }))

    with pytest.raises(CommandError, match=r'validation error'):
        call_command('seed_locations', f'--data-file={bad_file}')


def test_validation_missing_required_field(bsbh_org, tmp_path):
    bad_file = tmp_path / 'missing_field.json'
    bad_file.write_text(json.dumps({
        'organization': {'tax_id': '83-2541331'},
        'locations': [{'name': 'X', 'address': '1 St', 'city': 'Y', 'state': 'NJ'}],
    }))

    with pytest.raises(CommandError, match=r'validation error'):
        call_command('seed_locations', f'--data-file={bad_file}')


def test_validation_duplicate_names_in_data_file(bsbh_org, tmp_path, capsys):
    bad_file = tmp_path / 'dupes.json'
    bad_file.write_text(json.dumps({
        'organization': {'tax_id': '83-2541331'},
        'locations': [
            {
                'name': 'Same Name', 'address': '1 St', 'city': 'A',
                'state': 'NJ', 'zip_code': '07000',
            },
            {
                'name': 'Same Name', 'address': '2 St', 'city': 'B',
                'state': 'NJ', 'zip_code': '07000',
            },
        ],
    }))

    with pytest.raises(CommandError, match=r'validation error'):
        call_command('seed_locations', f'--data-file={bad_file}')

    captured = capsys.readouterr()
    assert 'duplicate name' in captured.err
    assert 'Same Name' in captured.err
    assert Location.objects.filter(organization=bsbh_org).count() == 0


def test_validation_writes_nothing_on_partial_failure(bsbh_org, tmp_path):
    """Even with a single bad row, nothing is written (transactional)."""
    bad_file = tmp_path / 'one_bad.json'
    bad_file.write_text(json.dumps({
        'organization': {'tax_id': '83-2541331'},
        'locations': [
            {
                'name': 'Good One', 'address': '1 St', 'city': 'A',
                'state': 'NJ', 'zip_code': '07000',
            },
            {
                'name': 'Bad One', 'address': '2 St', 'city': 'B',
                'state': 'XX', 'zip_code': 'BAD',
            },
        ],
    }))

    with pytest.raises(CommandError):
        call_command('seed_locations', f'--data-file={bad_file}')

    assert Location.objects.filter(organization=bsbh_org).count() == 0


def test_missing_data_file_errors_clean(bsbh_org, tmp_path):
    with pytest.raises(CommandError, match=r'Data file not found'):
        call_command(
            'seed_locations',
            f'--data-file={tmp_path / "does_not_exist.json"}',
        )


def test_invalid_json_errors_clean(bsbh_org, tmp_path):
    bad = tmp_path / 'broken.json'
    bad.write_text('this is not { valid json')

    with pytest.raises(CommandError, match=r'Invalid JSON'):
        call_command('seed_locations', f'--data-file={bad}')


def test_franklin_lakes_marked_primary(bsbh_org):
    call_command('seed_locations', f'--data-file={REPO_DATA_FILE}')
    primary = Location.objects.get(organization=bsbh_org, is_primary=True)
    assert primary.name == 'Franklin Lakes'
    # And exactly one primary, per the data file and DB constraint.
    assert Location.objects.filter(
        organization=bsbh_org, is_primary=True,
    ).count() == 1


def test_seeding_demotes_old_primary(bsbh_org):
    """If a different location is already primary, the seeder demotes it."""
    Location.objects.create(
        organization=bsbh_org,
        name='Old Primary',
        address='1 Old St',
        city='Trenton',
        state='NJ',
        zip_code='08608',
        is_primary=True,
    )
    call_command('seed_locations', f'--data-file={REPO_DATA_FILE}')
    old = Location.objects.get(organization=bsbh_org, name='Old Primary')
    assert old.is_primary is False
    new_primary = Location.objects.get(organization=bsbh_org, is_primary=True)
    assert new_primary.name == 'Franklin Lakes'


def test_two_primaries_in_data_file_aborts(bsbh_org, tmp_path, capsys):
    bad = tmp_path / 'two_primaries.json'
    bad.write_text(json.dumps({
        'organization': {'tax_id': '83-2541331'},
        'locations': [
            {
                'name': 'A', 'address': '1 St', 'city': 'A', 'state': 'NJ',
                'zip_code': '07000', 'is_primary': True,
            },
            {
                'name': 'B', 'address': '2 St', 'city': 'B', 'state': 'NJ',
                'zip_code': '07001', 'is_primary': True,
            },
        ],
    }))
    with pytest.raises(CommandError, match=r'validation error'):
        call_command('seed_locations', f'--data-file={bad}')
    captured = capsys.readouterr()
    assert 'primary location' in captured.err
    assert Location.objects.filter(organization=bsbh_org).count() == 0


def test_db_constraint_blocks_two_primaries(bsbh_org):
    """Belt-and-suspenders: even bypassing the seeder, the DB rejects 2 primaries."""
    from django.db import IntegrityError
    Location.objects.create(
        organization=bsbh_org, name='A', address='1', city='X',
        state='NJ', zip_code='07000', is_primary=True,
    )
    with pytest.raises(IntegrityError):
        Location.objects.create(
            organization=bsbh_org, name='B', address='2', city='X',
            state='NJ', zip_code='07000', is_primary=True,
        )


def test_lookup_by_name_when_tax_id_absent(bsbh_org, tmp_path):
    data_file = tmp_path / 'by_name.json'
    data_file.write_text(json.dumps({
        'organization': {'name': 'Baker Street Behavioral Health'},
        'locations': [{
            'name': 'Solo', 'address': '1 St', 'city': 'A',
            'state': 'NJ', 'zip_code': '07000',
        }],
    }))

    call_command('seed_locations', f'--data-file={data_file}')

    assert Location.objects.filter(organization=bsbh_org, name='Solo').exists()
