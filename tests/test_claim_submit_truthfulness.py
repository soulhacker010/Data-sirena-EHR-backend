"""
A claim may only read "submitted" if it actually reached Office Ally.

Why this matters more than it looks. The submit endpoint generates the X12
file, uploads it over SFTP, and then advances the claim's status. That status
advance used to run unconditionally — so when the SFTP upload failed, the claim
still showed `submitted` with a `submitted_at` timestamp. The clinician saw a
filed claim, the payer received nothing, and nobody would look again until the
timely-filing window had closed. Silent non-submission of insurance claims is a
money-losing failure, and it fails quietly by design unless something like this
test holds the line.

This file also carries the first fixture set that survives `validate_claim`.
The pre-existing claim tests all build incomplete claims and never get past
validation, so before this there was no passing test exercising the submission
path at all.
"""
from decimal import Decimal
from unittest.mock import patch

import pytest
from rest_framework import status


@pytest.fixture
def billable_claim(org, sample_client, admin_user):
    """
    A claim complete enough to pass `validate_claim`.

    Every piece here exists because the validator demands it: insurance
    identifiers, a payer that resolves in the Office Ally directory and accepts
    837P, at least one service line, and an active NPI for the practice.
    """
    from apps.accounts.models import NPI
    from apps.billing.models import Claim, Invoice, InvoiceItem, Payer

    sample_client.insurance_primary_name = 'Aetna'
    sample_client.insurance_primary_id = 'W123456789'
    sample_client.save(update_fields=[
        'insurance_primary_name', 'insurance_primary_id',
    ])

    NPI.objects.create(
        organization=org,
        npi_number='1234567893',
        business_name='Test ABA Clinic',
        is_active=True,
    )

    Payer.objects.create(
        name='Aetna',
        payer_id='AET001',
        available=True,
        enrollment_required=False,
        supports_837p=True,
    )

    invoice = Invoice.objects.create(
        organization=org,
        client=sample_client,
        invoice_number='INV-SUBMIT-001',
        invoice_date='2026-03-01',
        total_amount=Decimal('1000.00'),
        balance=Decimal('1000.00'),
    )
    InvoiceItem.objects.create(
        invoice=invoice,
        service_code='97153',
        units=Decimal('8.00'),
        rate=Decimal('125.00'),
        amount=Decimal('1000.00'),
    )

    return Claim.objects.create(
        invoice=invoice,
        client=sample_client,
        claim_number='CLM-SUBMIT-001',
        payer_name='Aetna',
        payer_id='AET001',
        status='created',
        billed_amount=Decimal('1000.00'),
    )


def _submit(client, claim):
    return client.post(f'/api/v1/claims/{claim.id}/submit/', format='json')


@pytest.mark.django_db
class TestClaimPassesValidation:
    """Guard the guard — if the fixture stops validating, everything below
    would pass vacuously by never reaching the upload step."""

    def test_fixture_claim_is_valid(self, billable_claim):
        from apps.billing.services.claim_validator import validate_claim

        result = validate_claim(billable_claim)
        assert result['ok'], f"fixture no longer valid: {result['errors']}"


@pytest.mark.django_db
class TestFailedUploadDoesNotMarkSubmitted:

    @patch('apps.billing.services.office_ally.upload_claim_file')
    def test_status_unchanged_when_upload_fails(
        self, mock_upload, admin_client, billable_claim,
    ):
        mock_upload.side_effect = Exception(
            'Authentication failed: transport shut down or saw EOF'
        )

        resp = _submit(admin_client, billable_claim)
        assert resp.status_code == status.HTTP_200_OK

        billable_claim.refresh_from_db()
        assert billable_claim.status == 'created', (
            'a claim that never reached Office Ally must not read as submitted'
        )
        assert billable_claim.submitted_at is None, (
            'submitted_at must not be stamped for a claim that was not sent'
        )

    @patch('apps.billing.services.office_ally.upload_claim_file')
    def test_response_reports_the_failure(
        self, mock_upload, admin_client, billable_claim,
    ):
        mock_upload.side_effect = Exception('Authentication failed')

        resp = _submit(admin_client, billable_claim)

        assert resp.data['_submission']['status'] == 'upload_failed'
        assert resp.data['status'] == 'created'

    @patch('apps.billing.services.office_ally.upload_claim_file')
    def test_generated_x12_is_still_saved(
        self, mock_upload, admin_client, billable_claim,
    ):
        """The file is real work — keep it so a retry can reuse it."""
        mock_upload.side_effect = Exception('Authentication failed')

        _submit(admin_client, billable_claim)

        billable_claim.refresh_from_db()
        assert billable_claim.x12_837_raw, 'generated X12 should be retained'
        assert billable_claim.oa_file_id, 'filename should be retained'


@pytest.mark.django_db
class TestSuccessfulUploadStillMarksSubmitted:
    """The fix must not break the path that actually works."""

    @patch('apps.billing.services.office_ally.upload_claim_file')
    def test_status_advances_on_success(
        self, mock_upload, admin_client, billable_claim,
    ):
        mock_upload.return_value = None  # upload succeeded

        resp = _submit(admin_client, billable_claim)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['_submission']['status'] == 'uploaded'

        billable_claim.refresh_from_db()
        assert billable_claim.status == 'submitted'
        assert billable_claim.submitted_at is not None

    @patch('apps.billing.services.office_ally.upload_claim_file')
    def test_denied_claim_becomes_resubmitted_on_success(
        self, mock_upload, admin_client, billable_claim,
    ):
        mock_upload.return_value = None
        billable_claim.status = 'denied'
        billable_claim.save(update_fields=['status'])

        resp = admin_client.post(
            f'/api/v1/claims/{billable_claim.id}/submit/',
            {'resubmission_notes': 'Added modifier 97'},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK

        billable_claim.refresh_from_db()
        assert billable_claim.status == 'resubmitted'
        assert billable_claim.resubmission_count == 1
        assert billable_claim.resubmission_notes == 'Added modifier 97'

    @patch('apps.billing.services.office_ally.upload_claim_file')
    def test_denied_claim_stays_denied_when_upload_fails(
        self, mock_upload, admin_client, billable_claim,
    ):
        """A failed resubmission must not consume the resubmission count."""
        mock_upload.side_effect = Exception('Authentication failed')
        billable_claim.status = 'denied'
        billable_claim.save(update_fields=['status'])

        _submit(admin_client, billable_claim)

        billable_claim.refresh_from_db()
        assert billable_claim.status == 'denied'
        assert billable_claim.resubmission_count == 0
