"""Integration tests for `python manage.py seed_npis`.

Covers the BSBH data file plus the Luhn check and edge cases (cross-org
collision, idempotency, archive-and-reload).
"""
import json
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.models import NPI, Organization
from apps.accounts.npi import luhn_validate_npi


REPO_DATA_FILE = (
    Path(__file__).resolve().parents[1]
    / 'apps' / 'accounts' / 'data' / 'bsbh_npis.json'
)


@pytest.fixture
def bsbh_org(db):
    return Organization.objects.create(
        name='Baker Street Behavioral Health',
        tax_id='83-2541331',
        contact_email='admin@bakerstreetpsych.com',
    )


@pytest.fixture
def other_org(db):
    return Organization.objects.create(
        name='Other Clinic',
        tax_id='99-9999999',
    )


# ─── Luhn validator unit tests ─────────────────────────────────────────────

class TestLuhnNPI:
    def test_known_valid_npi(self):
        # Dr. Joe's BSBH NPI — independently verified during onboarding.
        assert luhn_validate_npi('1659841096') is True

    def test_check_digit_off_by_one_invalid(self):
        # Tweak last digit to bust the Luhn check.
        assert luhn_validate_npi('1659841090') is False
        assert luhn_validate_npi('1659841097') is False

    def test_too_short(self):
        assert luhn_validate_npi('123') is False
        assert luhn_validate_npi('123456789') is False

    def test_too_long(self):
        assert luhn_validate_npi('12345678901') is False

    def test_non_digits(self):
        assert luhn_validate_npi('165984109A') is False
        assert luhn_validate_npi('1659-84109') is False

    def test_non_string(self):
        assert luhn_validate_npi(1659841096) is False  # int not str
        assert luhn_validate_npi(None) is False


# ─── Seed command — happy path ─────────────────────────────────────────────

def test_seeds_bsbh_npi_from_repo_file(bsbh_org):
    call_command('seed_npis', f'--data-file={REPO_DATA_FILE}')

    qs = NPI.objects.filter(organization=bsbh_org)
    assert qs.count() == 1
    npi = qs.get()
    assert npi.npi_number == '1659841096'
    assert npi.business_name == 'Baker Street Behavioral Health'
    assert npi.is_active is True


def test_idempotent_double_run(bsbh_org):
    call_command('seed_npis', f'--data-file={REPO_DATA_FILE}')
    call_command('seed_npis', f'--data-file={REPO_DATA_FILE}')
    assert NPI.objects.filter(organization=bsbh_org).count() == 1


def test_dry_run_writes_nothing(bsbh_org):
    call_command('seed_npis', f'--data-file={REPO_DATA_FILE}', '--dry-run')
    assert NPI.objects.filter(organization=bsbh_org).count() == 0


def test_organization_must_exist(db):
    with pytest.raises(CommandError, match=r'Organization with tax_id'):
        call_command('seed_npis', f'--data-file={REPO_DATA_FILE}')


# ─── Cross-org collision is a hard error (not silent re-assignment) ────────

def test_cross_org_npi_collision_aborts(bsbh_org, other_org):
    """If the NPI already exists under a different org, refuse to re-assign."""
    NPI.objects.create(
        organization=other_org,
        npi_number='1659841096',
        business_name='Other Clinic',
        is_active=True,
    )

    with pytest.raises(CommandError, match=r'already.*assigned to organization'):
        call_command('seed_npis', f'--data-file={REPO_DATA_FILE}')

    # Ensure the existing record was NOT touched.
    other_npi = NPI.objects.get(npi_number='1659841096')
    assert other_npi.organization_id == other_org.id
    assert other_npi.business_name == 'Other Clinic'
    assert NPI.objects.filter(organization=bsbh_org).count() == 0


# ─── Update path ───────────────────────────────────────────────────────────

def test_update_path_changes_business_name(bsbh_org):
    NPI.objects.create(
        organization=bsbh_org,
        npi_number='1659841096',
        business_name='OLD WRONG NAME',
        is_active=False,
    )

    call_command('seed_npis', f'--data-file={REPO_DATA_FILE}')

    npi = NPI.objects.get(npi_number='1659841096')
    assert npi.business_name == 'Baker Street Behavioral Health'
    assert npi.is_active is True


# ─── Clear / archive ───────────────────────────────────────────────────────

def test_clear_archives_existing(bsbh_org):
    NPI.objects.create(
        organization=bsbh_org,
        npi_number='1003999400',  # different valid NPI for this fixture
        business_name='Old Group',
        is_active=True,
    )

    call_command('seed_npis', f'--data-file={REPO_DATA_FILE}', '--clear')

    old = NPI.objects.get(npi_number='1003999400')
    assert old.is_active is False
    new = NPI.objects.get(npi_number='1659841096')
    assert new.is_active is True


# ─── Validation failures ───────────────────────────────────────────────────

def test_invalid_luhn_aborts(bsbh_org, tmp_path):
    bad = tmp_path / 'bad_luhn.json'
    bad.write_text(json.dumps({
        'organization': {'tax_id': '83-2541331'},
        'npis': [{
            'npi_number': '1659841090',  # check digit busted
            'business_name': 'X',
        }],
    }))

    with pytest.raises(CommandError, match=r'validation error'):
        call_command('seed_npis', f'--data-file={bad}')

    assert NPI.objects.filter(organization=bsbh_org).count() == 0


def test_non_numeric_npi_aborts(bsbh_org, tmp_path):
    bad = tmp_path / 'non_digit.json'
    bad.write_text(json.dumps({
        'organization': {'tax_id': '83-2541331'},
        'npis': [{
            'npi_number': '165984109A',
            'business_name': 'X',
        }],
    }))

    with pytest.raises(CommandError, match=r'validation error'):
        call_command('seed_npis', f'--data-file={bad}')


def test_wrong_length_npi_aborts(bsbh_org, tmp_path):
    bad = tmp_path / 'short.json'
    bad.write_text(json.dumps({
        'organization': {'tax_id': '83-2541331'},
        'npis': [{
            'npi_number': '12345',
            'business_name': 'X',
        }],
    }))

    with pytest.raises(CommandError, match=r'validation error'):
        call_command('seed_npis', f'--data-file={bad}')


def test_duplicate_npi_in_data_file_aborts(bsbh_org, tmp_path, capsys):
    bad = tmp_path / 'dupes.json'
    bad.write_text(json.dumps({
        'organization': {'tax_id': '83-2541331'},
        'npis': [
            {'npi_number': '1659841096', 'business_name': 'A'},
            {'npi_number': '1659841096', 'business_name': 'B'},
        ],
    }))

    with pytest.raises(CommandError, match=r'validation error'):
        call_command('seed_npis', f'--data-file={bad}')

    captured = capsys.readouterr()
    assert 'duplicate npi_number' in captured.err
    assert NPI.objects.filter(organization=bsbh_org).count() == 0


def test_partial_failure_writes_nothing(bsbh_org, tmp_path):
    bad = tmp_path / 'one_bad.json'
    bad.write_text(json.dumps({
        'organization': {'tax_id': '83-2541331'},
        'npis': [
            {'npi_number': '1659841096', 'business_name': 'Good One'},
            {'npi_number': '0000000000', 'business_name': 'Bad Luhn'},
        ],
    }))

    with pytest.raises(CommandError):
        call_command('seed_npis', f'--data-file={bad}')

    assert NPI.objects.filter(organization=bsbh_org).count() == 0


def test_missing_data_file_errors_clean(bsbh_org, tmp_path):
    with pytest.raises(CommandError, match=r'Data file not found'):
        call_command(
            'seed_npis',
            f'--data-file={tmp_path / "no.json"}',
        )


def test_invalid_json_errors_clean(bsbh_org, tmp_path):
    bad = tmp_path / 'broken.json'
    bad.write_text('not { valid')

    with pytest.raises(CommandError, match=r'Invalid JSON'):
        call_command('seed_npis', f'--data-file={bad}')
