"""Regression tests for office locations on invoice PDFs.

Pins the deliverable from Dr. Joe's 2026-05-04 email — every invoice PDF must
show where services were rendered, plus use the primary office address as the
practice header (instead of the org's free-text `address` field, which has no
city/state/zip split).

We extract real text from the rendered PDF bytes via `pypdf` rather than
asserting on raw bytes, because ReportLab can split words across PDF text
operators in ways that make naive substring searches flaky.
"""
import io
from datetime import date

import pytest
from pypdf import PdfReader

from apps.accounts.models import Location
from apps.billing.models import Invoice, InvoiceItem
from apps.billing.pdf import generate_invoice_pdf


def _extract_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return '\n'.join(page.extract_text() or '' for page in reader.pages)


@pytest.fixture
def franklin_lakes(org):
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
def paramus(org):
    return Location.objects.create(
        organization=org,
        name='Paramus',
        address='12 Madison Ave, Suite 306',
        city='Paramus',
        state='NJ',
        zip_code='07652-5741',
        is_primary=False,
        is_active=True,
    )


def _make_invoice(org, client, lines):
    """`lines` = list of (service_code, amount, appointment_or_None)."""
    invoice = Invoice.objects.create(
        organization=org,
        client=client,
        invoice_date=date(2026, 5, 5),
        total_amount=sum(amount for _, amount, _ in lines),
        balance=sum(amount for _, amount, _ in lines),
    )
    for service_code, amount, appt in lines:
        InvoiceItem.objects.create(
            invoice=invoice,
            appointment=appt,
            service_code=service_code,
            units=1,
            rate=amount,
            amount=amount,
            description=f'Therapy session {service_code}',
        )
    return invoice


# ─── Header: practice address ──────────────────────────────────────────────

def test_pdf_header_uses_primary_location_when_present(
    org, sample_client, franklin_lakes
):
    invoice = _make_invoice(org, sample_client, [('90834', 150, None)])

    pdf_bytes = generate_invoice_pdf(invoice, organization=org)
    text = _extract_text(pdf_bytes)

    # Structured primary address (with city/state/zip) should appear in header.
    assert '851 Franklin Lake Road, Suite 204' in text
    assert 'Franklin Lakes' in text
    assert 'NJ' in text
    assert '07417-2267' in text


def test_pdf_header_falls_back_to_org_address_when_no_primary(
    org, sample_client
):
    """When no primary Location exists, fall back to Organization.address."""
    org.address = '123 Fallback St, Anywhere, NY 10001'
    org.save()

    invoice = _make_invoice(org, sample_client, [('90834', 150, None)])

    pdf_bytes = generate_invoice_pdf(invoice, organization=org)
    text = _extract_text(pdf_bytes)

    assert '123 Fallback St' in text


# ─── Service Location section ──────────────────────────────────────────────

def test_pdf_lists_single_service_location(
    org, sample_client, sample_appointment, franklin_lakes, cedar_grove
):
    sample_appointment.location = cedar_grove
    sample_appointment.save()

    invoice = _make_invoice(
        org, sample_client, [('90834', 150, sample_appointment)]
    )

    text = _extract_text(generate_invoice_pdf(invoice, organization=org))

    assert 'Service Location' in text
    assert 'Cedar Grove' in text
    assert '874 Pompton Ave, Unit B1' in text


def test_pdf_lists_multiple_distinct_service_locations(
    org, sample_client, clinician_user, franklin_lakes, cedar_grove, paramus
):
    """Two appointments at different locations → both listed in the PDF."""
    from apps.scheduling.models import Appointment

    appt_cedar = Appointment.objects.create(
        organization=org,
        client=sample_client,
        provider=clinician_user,
        location=cedar_grove,
        start_time='2026-05-04T13:00:00Z',
        end_time='2026-05-04T13:45:00Z',
        service_code='90834',
        units=1,
        status='attended',
    )
    appt_paramus = Appointment.objects.create(
        organization=org,
        client=sample_client,
        provider=clinician_user,
        location=paramus,
        start_time='2026-05-05T10:00:00Z',
        end_time='2026-05-05T10:45:00Z',
        service_code='90837',
        units=1,
        status='attended',
    )

    invoice = _make_invoice(
        org, sample_client,
        [('90834', 150, appt_cedar), ('90837', 200, appt_paramus)],
    )

    text = _extract_text(generate_invoice_pdf(invoice, organization=org))

    assert 'Cedar Grove' in text
    assert '874 Pompton Ave, Unit B1' in text
    assert 'Paramus' in text
    assert '12 Madison Ave, Suite 306' in text


def test_pdf_dedupes_repeated_service_locations(
    org, sample_client, clinician_user, cedar_grove
):
    """Two appointments at the same location → location listed once."""
    from apps.scheduling.models import Appointment

    a1 = Appointment.objects.create(
        organization=org, client=sample_client, provider=clinician_user,
        location=cedar_grove,
        start_time='2026-05-04T13:00:00Z',
        end_time='2026-05-04T13:45:00Z',
        service_code='90834', units=1, status='attended',
    )
    a2 = Appointment.objects.create(
        organization=org, client=sample_client, provider=clinician_user,
        location=cedar_grove,
        start_time='2026-05-05T13:00:00Z',
        end_time='2026-05-05T13:45:00Z',
        service_code='90834', units=1, status='attended',
    )

    invoice = _make_invoice(
        org, sample_client,
        [('90834', 150, a1), ('90834', 150, a2)],
    )

    text = _extract_text(generate_invoice_pdf(invoice, organization=org))

    # Cedar Grove appears in the section AND would normally appear once. Counting
    # full-name + address pair occurrences — both should each appear once.
    assert text.count('874 Pompton Ave, Unit B1') == 1


def test_pdf_omits_section_when_no_appointment_locations(
    org, sample_client, franklin_lakes
):
    """Items with no appointment.location → no 'Service Location' section."""
    invoice = _make_invoice(
        org, sample_client, [('90834', 150, None)]
    )

    text = _extract_text(generate_invoice_pdf(invoice, organization=org))

    assert 'Service Location' not in text
