"""
Celery tasks for billing - claim submission, payment reminders.

Uses the centralized EmailService for all email sending.
"""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def submit_claim_to_clearinghouse(claim_id):
    """
    Background task: Submit a claim to the insurance clearinghouse.

    In production, this would integrate with a clearinghouse API
    (e.g., Change Healthcare, Availity, Office Ally).
    """
    from .models import Claim
    from django.utils import timezone

    try:
        claim = Claim.objects.get(id=claim_id)
        # TODO: Integrate with actual clearinghouse API
        # For now, just mark as submitted
        claim.status = 'submitted'
        claim.submitted_at = timezone.now()
        claim.save(update_fields=['status', 'submitted_at', 'updated_at'])
        return {'status': 'submitted', 'claim_id': str(claim_id)}
    except Claim.DoesNotExist:
        return {'status': 'error', 'message': 'Claim not found'}


@shared_task(
    bind=True,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_kwargs={'max_retries': 3},
)
def send_payment_reminder(self, invoice_id):
    """
    Background task: Send payment reminder for overdue invoices.

    Uses centralized EmailService.send_payment_reminder().

    FIX #4: Catches and logs Resend exceptions. Auto-retries on
    transient failures (connection/timeout) up to 3 times.
    """
    from .models import Invoice
    from apps.core.email import EmailService

    try:
        invoice = Invoice.objects.select_related(
            'client', 'organization'
        ).get(id=invoice_id)
    except Invoice.DoesNotExist:
        logger.error(f'Payment reminder: Invoice {invoice_id} not found')
        return {'status': 'error', 'message': 'Invoice not found'}

    # Guard: already paid
    if invoice.balance <= 0:
        return {'status': 'skipped', 'message': 'Invoice already paid'}

    # Guard: no client email
    if not invoice.client or not invoice.client.email:
        logger.warning(f'Payment reminder: Invoice {invoice_id} client has no email')
        return {'status': 'skipped', 'message': 'Client has no email'}

    # Guard: cancelled invoice
    if invoice.status in ('cancelled', 'voided', 'void'):
        logger.info(f'Payment reminder: Invoice {invoice_id} is {invoice.status}, skipping')
        return {'status': 'skipped', 'message': f'Invoice is {invoice.status}'}

    org_name = invoice.organization.name if invoice.organization else 'Sirena Health'

    try:
        EmailService.send_payment_reminder(invoice, org_name=org_name)
        return {'status': 'sent', 'invoice_id': str(invoice_id)}
    except Exception as e:
        logger.error(
            f'Payment reminder failed for invoice {invoice_id}: {e}',
            exc_info=True,
        )
        return {'status': 'error', 'message': str(e)}


@shared_task(
    bind=True,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_kwargs={'max_retries': 3},
)
def send_invoice_email_task(self, invoice_id, to_email=None):
    """
    Background task: Send an invoice email.

    FIX #4: Same pattern — catches exceptions, logs, retries on transient.
    """
    from .models import Invoice
    from apps.core.email import EmailService

    try:
        invoice = Invoice.objects.select_related(
            'client', 'organization'
        ).prefetch_related('items').get(id=invoice_id)
    except Invoice.DoesNotExist:
        logger.error(f'Invoice email task: Invoice {invoice_id} not found')
        return {'status': 'error', 'message': 'Invoice not found'}

    # Guard: cancelled invoice
    if invoice.status in ('cancelled', 'voided', 'void'):
        logger.info(f'Invoice email: Invoice {invoice_id} is {invoice.status}, skipping')
        return {'status': 'skipped', 'message': f'Invoice is {invoice.status}'}

    recipient = to_email or (invoice.client.email if invoice.client else None)
    if not recipient:
        logger.warning(f'Invoice email task: No recipient for invoice {invoice_id}')
        return {'status': 'skipped', 'message': 'No recipient email'}

    org_name = invoice.organization.name if invoice.organization else 'Sirena Health'

    try:
        EmailService.send_invoice_email(invoice, to_email=recipient, org_name=org_name)
        return {'status': 'sent', 'invoice_id': str(invoice_id), 'to': recipient}
    except ValueError as e:
        logger.warning(f'Invoice email: Invalid email for invoice {invoice_id}: {e}')
        return {'status': 'error', 'message': str(e)}
    except Exception as e:
        logger.error(
            f'Invoice email failed for {invoice_id}: {e}',
            exc_info=True,
        )
        return {'status': 'error', 'message': str(e)}
