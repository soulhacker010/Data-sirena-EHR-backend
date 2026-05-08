"""Regression tests for location wiring in the X12 837P generator.

Pins three behaviors:

1.  Loop 2010AA Billing Provider N3/N4 use the **organization's primary Location**
    (not the org's free-text `address` field, which has no city/state/zip split).
2.  Loop 2310C Service Facility Location is emitted when the appointment's
    location differs from the billing primary (and only then — payers reject
    the file if you redundantly repeat the billing address).
3.  The CLM segment's place-of-service code comes from the appointment, not a
    hardcoded `11`.
"""
from datetime import date

import pytest

from apps.accounts.models import Location, NPI
from apps.billing.models import Claim, Invoice, InvoiceItem
from apps.billing.services.x12_837p import generate_837p


def _split_segments(x12: str) -> list[str]:
    return [line.rstrip('~') for line in x12.split('\n') if line.strip()]


@pytest.fixture
def primary_loc(org):
    """Org's primary billing location (Franklin Lakes pattern)."""
    return Location.objects.create(
        organization=org,
        name='Franklin Lakes',
        address='851 Franklin Lake Road, Suite 204',
        city='Franklin Lakes',
        state='NJ',
        zip_code='07417-2267',
        is_primary=True,
        is_active=True,
    )


@pytest.fixture
def cedar_grove(org):
    """A non-primary service location."""
    return Location.objects.create(
        organization=org,
        name='Cedar Grove',
        address='874 Pompton Ave, Unit B1',
        city='Cedar Grove',
        state='NJ',
        zip_code='07009-1264',
        is_primary=False,
        is_active=True,
    )


@pytest.fixture
def org_with_npi(org):
    NPI.objects.create(
        organization=org,
        npi_number='1659841096',
        business_name='BSBH',
        is_active=True,
    )
    return org


def _make_claim(org, client, appointment, *, billed=150, cpt='90834'):
    invoice = Invoice.objects.create(
        organization=org,
        client=client,
        invoice_date=date(2026, 5, 5),
        total_amount=billed,
        balance=billed,
    )
    InvoiceItem.objects.create(
        invoice=invoice,
        appointment=appointment,
        service_code=cpt,
        units=1,
        rate=billed,
        amount=billed,
    )
    return Claim.objects.create(
        invoice=invoice,
        client=client,
        payer_name='Aetna',
        payer_id='60054',
        billed_amount=billed,
    )


# ─── Loop 2010AA — Billing Provider address ─────────────────────────────────

class TestBillingProviderAddress:
    def test_n3_n4_use_primary_location(
        self, org_with_npi, sample_client, sample_appointment, primary_loc
    ):
        claim = _make_claim(org_with_npi, sample_client, sample_appointment)

        x12 = generate_837p(claim)
        segments = _split_segments(x12)

        # Find the billing provider block (NM1*85, then N3, then N4).
        i_85 = next(i for i, s in enumerate(segments) if s.startswith('NM1*85*'))
        n3 = segments[i_85 + 1]
        n4 = segments[i_85 + 2]

        assert n3 == 'N3*851 Franklin Lake Road, Suite 204'
        # N4 = city, state, zip-without-dash
        assert n4 == 'N4*Franklin Lakes*NJ*074172267'

    def test_n3_n4_omitted_when_no_primary_and_no_org_address(
        self, org_with_npi, sample_client, sample_appointment
    ):
        """No primary location, no org.address → no N3/N4 in billing provider block."""
        org_with_npi.address = ''
        org_with_npi.save()

        claim = _make_claim(org_with_npi, sample_client, sample_appointment)
        segments = _split_segments(generate_837p(claim))

        i_85 = next(i for i, s in enumerate(segments) if s.startswith('NM1*85*'))
        # The next segment after NM1*85 should be REF*EI (tax id), not N3.
        assert segments[i_85 + 1].startswith('REF*EI*')


# ─── Loop 2310C — Service Facility Location ─────────────────────────────────

class TestServiceFacilityLocation:
    def test_service_facility_emitted_when_appt_location_differs_from_primary(
        self, org_with_npi, sample_client, sample_appointment, primary_loc, cedar_grove
    ):
        sample_appointment.location = cedar_grove
        sample_appointment.save()

        claim = _make_claim(org_with_npi, sample_client, sample_appointment)
        segments = _split_segments(generate_837p(claim))

        # Loop 2310C: NM1*77 followed by N3/N4 with the service location.
        nm1_77 = next((s for s in segments if s.startswith('NM1*77*')), None)
        assert nm1_77 is not None, 'Expected NM1*77 (Service Facility Location) segment'
        assert 'Cedar Grove' in nm1_77

        # The N3 right after must be the service location address, not billing.
        i_77 = segments.index(nm1_77)
        assert segments[i_77 + 1] == 'N3*874 Pompton Ave, Unit B1'
        assert segments[i_77 + 2] == 'N4*Cedar Grove*NJ*070091264'

    def test_service_facility_skipped_when_appt_at_primary_location(
        self, org_with_npi, sample_client, sample_appointment, primary_loc
    ):
        """Don't repeat the billing address — payers may reject."""
        sample_appointment.location = primary_loc
        sample_appointment.save()

        claim = _make_claim(org_with_npi, sample_client, sample_appointment)
        segments = _split_segments(generate_837p(claim))

        nm1_77_lines = [s for s in segments if s.startswith('NM1*77*')]
        assert nm1_77_lines == [], (
            'Service Facility Location must be omitted when service happened '
            'at the billing provider address'
        )

    def test_service_facility_skipped_when_appt_has_no_location(
        self, org_with_npi, sample_client, sample_appointment, primary_loc
    ):
        # sample_appointment fixture leaves location=None
        assert sample_appointment.location is None
        claim = _make_claim(org_with_npi, sample_client, sample_appointment)
        segments = _split_segments(generate_837p(claim))
        assert not any(s.startswith('NM1*77*') for s in segments)


# ─── CLM POS code from appointment ──────────────────────────────────────────

class TestPlaceOfService:
    def test_pos_code_pulls_from_appointment(
        self, org_with_npi, sample_client, sample_appointment, primary_loc
    ):
        sample_appointment.place_of_service = '02'  # Telehealth
        sample_appointment.save()

        claim = _make_claim(org_with_npi, sample_client, sample_appointment)
        segments = _split_segments(generate_837p(claim))

        clm = next(s for s in segments if s.startswith('CLM*'))
        # CLM*<acct>*<charge>***02:B:1*Y*A*Y*I
        assert '*02:B:1*' in clm

    def test_pos_code_defaults_to_office_when_appt_missing_pos(
        self, org_with_npi, sample_client, sample_appointment, primary_loc
    ):
        sample_appointment.place_of_service = ''
        sample_appointment.save()

        claim = _make_claim(org_with_npi, sample_client, sample_appointment)
        segments = _split_segments(generate_837p(claim))

        clm = next(s for s in segments if s.startswith('CLM*'))
        assert '*11:B:1*' in clm
