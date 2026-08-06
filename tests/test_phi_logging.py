"""
Tests that PHI never reaches application logs.

HIPAA principle (and CLAUDE.md's hard rule): patient identifiers must not
appear in stdout/stderr, because Render captures process output and it is not
part of the audited, access-controlled PHI surface.

The specific trap these tests guard is model-repr interpolation. Several
__str__ methods embed the client:

    Client.__str__       -> "Last, First"
    Invoice.__str__      -> "Invoice #<n> — <client>"
    Appointment.__str__  -> "<client> — <provider> @ <time>"

so an innocent-looking f'...{invoice}...' log line silently emits a patient
name. These tests exercise the two skip-paths in EmailService that log when a
client has no email on file, and assert the patient's name is absent.
"""
import logging

import pytest


@pytest.mark.django_db
class TestNoPHIInEmailServiceLogs:

    def test_payment_reminder_skip_log_has_no_patient_name(
        self, org, sample_client, caplog,
    ):
        from apps.billing.models import Invoice
        from apps.core.email import EmailService

        # Client with no email on file triggers the skip-and-log branch.
        sample_client.email = ''
        sample_client.save(update_fields=['email'])

        invoice = Invoice.objects.create(
            organization=org,
            client=sample_client,
            invoice_number='INV-PHI-LOG-1',
            invoice_date='2026-03-01',
            status='sent',
            total_amount=100,
            balance=100,
        )

        # Guard the guard: if Invoice.__str__ ever stops embedding the client,
        # this test would pass vacuously. Assert the trap is still real.
        assert sample_client.last_name in str(invoice)

        with caplog.at_level(logging.WARNING, logger='apps.core.email'):
            result = EmailService.send_payment_reminder(invoice)

        assert result is None, 'expected the no-email path to short-circuit'
        assert caplog.records, 'expected a warning to be logged'

        logged = caplog.text
        assert sample_client.last_name not in logged
        assert sample_client.first_name not in logged
        # The opaque id is what should identify the record instead.
        assert str(invoice.id) in logged

    def test_appointment_email_skip_log_has_no_patient_name(
        self, sample_appointment, sample_client, caplog,
    ):
        from apps.core.email import EmailService

        sample_client.email = ''
        sample_client.save(update_fields=['email'])
        sample_appointment.refresh_from_db()

        # Same guard-the-guard check as above.
        assert sample_client.last_name in str(sample_appointment)

        with caplog.at_level(logging.WARNING, logger='apps.core.email'):
            result = EmailService.send_appointment_email(
                sample_appointment, event='scheduled',
            )

        assert result is None, 'expected the no-email path to short-circuit'
        assert caplog.records, 'expected a warning to be logged'

        logged = caplog.text
        assert sample_client.last_name not in logged
        assert sample_client.first_name not in logged
        assert str(sample_appointment.id) in logged
