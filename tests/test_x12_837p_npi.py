"""Regression tests for the X12 837P NPI lookup.

Background — bug fix verified here:
The generator previously read the NPI from a non-existent ``Organization.npi``
attribute (`getattr(org, 'npi', '')`), so it always sent `NM1*85` with an empty
NPI value and skipped Loop 2310B (rendering provider) entirely. The fix looks
up the active NPI via the proper FK (``org.npis.filter(is_active=True)``).
These tests pin the corrected behavior so it can't silently regress.
"""
from datetime import date

import pytest

from apps.accounts.models import NPI
from apps.billing.models import Claim, Invoice, InvoiceItem
from apps.billing.services.x12_837p import generate_837p


@pytest.fixture
def claim_with_real_data(org, sample_client, sample_appointment):
    """Build a minimal but realistic Claim that the X12 generator can serialize."""
    invoice = Invoice.objects.create(
        organization=org,
        client=sample_client,
        invoice_date=date(2026, 5, 4),
        total_amount=150,
        balance=150,
    )
    InvoiceItem.objects.create(
        invoice=invoice,
        appointment=sample_appointment,
        service_code='90834',
        units=1,
        rate=150,
        amount=150,
    )
    return Claim.objects.create(
        invoice=invoice,
        client=sample_client,
        payer_name='Aetna',
        payer_id='60054',
        billed_amount=150,
    )


def _split_segments(x12: str) -> list[str]:
    """X12 segments are newline-separated; each ends with `~`. Return them stripped."""
    return [line.rstrip('~') for line in x12.split('\n') if line.strip()]


class TestX12NPIWiring:
    """The X12 generator must fetch the NPI from the proper FK relation."""

    def test_billing_provider_segment_carries_active_org_npi(
        self, org, claim_with_real_data
    ):
        NPI.objects.create(
            organization=org,
            npi_number='1659841096',
            business_name='Baker Street Behavioral Health',
            is_active=True,
        )

        x12 = generate_837p(claim_with_real_data)
        segments = _split_segments(x12)

        billing_provider = next(s for s in segments if s.startswith('NM1*85*'))
        # NM1*85*<entity_type>*<last>*<first>****XX*<npi>
        assert billing_provider.endswith('*XX*1659841096'), (
            f'Expected NM1*85 to end with *XX*1659841096; got: {billing_provider!r}'
        )

    def test_rendering_provider_segment_appears_with_org_npi_fallback(
        self, org, claim_with_real_data
    ):
        """When the provider has no own NPI, Loop 2310B inherits the org's."""
        NPI.objects.create(
            organization=org,
            npi_number='1659841096',
            business_name='Baker Street Behavioral Health',
            is_active=True,
        )

        x12 = generate_837p(claim_with_real_data)
        segments = _split_segments(x12)

        nm1_82_lines = [s for s in segments if s.startswith('NM1*82*')]
        assert len(nm1_82_lines) == 1, (
            f'Expected exactly one NM1*82 (Loop 2310B); got {len(nm1_82_lines)}'
        )
        assert nm1_82_lines[0].endswith('*XX*1659841096')

    def test_inactive_npi_is_ignored(self, org, claim_with_real_data):
        """Archived (is_active=False) NPIs must not appear on outgoing claims."""
        NPI.objects.create(
            organization=org,
            npi_number='1659841096',
            business_name='Old NPI',
            is_active=False,
        )

        x12 = generate_837p(claim_with_real_data)
        segments = _split_segments(x12)

        billing_provider = next(s for s in segments if s.startswith('NM1*85*'))
        # No active NPI — segment must still emit but with no NPI ID.
        # Format: NM1*85*<type>*<last>*<first>**** with no XX/NPI tail.
        assert '1659841096' not in billing_provider
        assert not billing_provider.endswith('*XX*1659841096')

        # Loop 2310B should be skipped entirely when prov_npi is empty.
        nm1_82_lines = [s for s in segments if s.startswith('NM1*82*')]
        assert nm1_82_lines == [], (
            'Loop 2310B (NM1*82) should be omitted when no active NPI exists'
        )

    def test_oldest_active_npi_wins_when_multiple_present(
        self, org, claim_with_real_data
    ):
        """Deterministic selection — oldest active NPI by created_at."""
        old_npi = NPI.objects.create(
            organization=org,
            npi_number='1659841096',
            business_name='First on file',
            is_active=True,
        )
        # A second, newer active NPI (different valid Luhn).
        NPI.objects.create(
            organization=org,
            npi_number='1003999400',
            business_name='Newer registration',
            is_active=True,
        )

        x12 = generate_837p(claim_with_real_data)
        segments = _split_segments(x12)

        billing_provider = next(s for s in segments if s.startswith('NM1*85*'))
        # Sanity: the older NPI was created first.
        assert NPI.objects.filter(organization=org).order_by(
            'created_at'
        ).first().pk == old_npi.pk
        assert billing_provider.endswith('*XX*1659841096')

    def test_no_npis_at_all_does_not_crash(self, org, claim_with_real_data):
        """Generator must still produce a file even if the org has no NPIs."""
        x12 = generate_837p(claim_with_real_data)
        segments = _split_segments(x12)
        # ISA envelope must still be present.
        assert segments[0].startswith('ISA*')
        # Loop 2310B (NM1*82) must be skipped when no NPI is available.
        assert not any(s.startswith('NM1*82*') for s in segments)

    def test_billing_provider_uses_org_entity_type_2(
        self, org, claim_with_real_data,
    ):
        """E5: Loop 2010AA's qualifier should be 2 (non-person/practice) and
        the name should be the organization, not the rendering clinician.
        Previously the segment emitted as a person with the clinician's name —
        wrong for 837P billing semantics."""
        NPI.objects.create(
            organization=org, npi_number='1659841096',
            business_name='BSBH', is_active=True,
        )
        x12 = generate_837p(claim_with_real_data)
        segments = _split_segments(x12)

        billing_provider = next(s for s in segments if s.startswith('NM1*85*'))
        # NM1*85*2*<org_name>****XX*<org_npi>
        parts = billing_provider.split('*')
        assert parts[2] == '2', f'Expected entity_type 2 (org); got {parts[2]}'
        # The org name slot — should NOT be the clinician's last name.
        assert parts[3] == org.name[:35]

    def test_rendering_provider_uses_individual_npi_when_set(
        self, org, sample_client, sample_appointment, clinician_user,
    ):
        """E5: when the rendering provider's User.npi is set, Loop 2310B uses
        THAT npi — not the org NPI. Org NPI still goes on Loop 2010AA."""
        NPI.objects.create(
            organization=org, npi_number='1659841096',
            business_name='Org', is_active=True,
        )
        clinician_user.npi = '1003999400'  # different valid NPI for the user
        clinician_user.save()

        invoice = Invoice.objects.create(
            organization=org, client=sample_client,
            invoice_date=date(2026, 5, 7),
            total_amount=150, balance=150,
        )
        InvoiceItem.objects.create(
            invoice=invoice, appointment=sample_appointment,
            service_code='90834', units=1, rate=150, amount=150,
        )
        claim = Claim.objects.create(
            invoice=invoice, client=sample_client,
            payer_name='Aetna', payer_id='60054',
            billed_amount=150,
        )

        x12 = generate_837p(claim)
        segments = _split_segments(x12)

        billing = next(s for s in segments if s.startswith('NM1*85*'))
        rendering = next(s for s in segments if s.startswith('NM1*82*'))

        # Billing provider = org NPI. Rendering provider = individual NPI.
        assert billing.endswith('*XX*1659841096')
        assert rendering.endswith('*XX*1003999400')

    def test_x12_envelope_smoke(self, org, claim_with_real_data):
        """Defensive: the file is a complete X12 envelope (ISA…IEA)."""
        NPI.objects.create(
            organization=org,
            npi_number='1659841096',
            business_name='BSBH',
            is_active=True,
        )
        x12 = generate_837p(claim_with_real_data)
        segments = _split_segments(x12)
        starts = [s.split('*', 1)[0] for s in segments]

        assert starts[0] == 'ISA'
        assert 'GS' in starts
        assert 'ST' in starts
        # Trailers must close the envelope in order.
        assert 'SE' in starts
        assert 'GE' in starts
        assert starts[-1] == 'IEA'
        # Tax ID / EIN propagates into the REF*EI segment.
        assert any(s == f'REF*EI*{org.tax_id}' for s in segments)
