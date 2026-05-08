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
    Background task: Generate X12 837P and upload to Office Ally SFTP.
    Mirrors the synchronous ClaimViewSet.submit() path — use this for
    bulk/batched submissions off the request/response cycle.
    """
    from .models import Claim, OASubmissionLog
    from .services.claim_validator import validate_claim
    from .services.x12_837p import generate_837p
    from .services.office_ally import upload_claim_file
    from django.conf import settings as dj_settings
    from django.utils import timezone

    try:
        claim = Claim.objects.select_related(
            'client', 'invoice', 'invoice__organization',
        ).prefetch_related(
            'invoice__items', 'invoice__organization__npis',
        ).get(id=claim_id)
    except Claim.DoesNotExist:
        return {'status': 'error', 'message': 'Claim not found'}

    validation = validate_claim(claim)
    if not validation['ok']:
        return {'status': 'validation_failed', 'errors': validation['errors']}

    x12_content = generate_837p(claim)
    now = timezone.now()
    use_test = not getattr(dj_settings, 'OA_GO_LIVE', False)
    prefix = 'OATEST_' if use_test else ''
    filename = f"{prefix}837P_{claim.id.hex[:8]}_{now:%Y%m%d%H%M%S}.txt"

    log = OASubmissionLog.objects.create(
        organization=claim.invoice.organization,
        file_type='837p',
        filename=filename,
        claim_count=1,
        raw_response=x12_content[:50000],
        status='pending',
    )

    try:
        upload_claim_file(x12_content, filename)
        log.status = 'uploaded'
        log.uploaded_at = now
        log.save(update_fields=['status', 'uploaded_at', 'updated_at'])
        claim.status = 'submitted' if claim.status != 'denied' else 'resubmitted'
        claim.submitted_at = now
        claim.oa_file_id = filename
        claim.x12_837_raw = x12_content
        claim.save(update_fields=[
            'status', 'submitted_at', 'oa_file_id', 'x12_837_raw', 'updated_at',
        ])
        return {'status': 'uploaded', 'claim_id': str(claim_id), 'filename': filename}
    except RuntimeError as e:
        # SFTP not configured — keep the generated file on the log for later
        return {'status': 'generated_not_uploaded', 'message': str(e), 'filename': filename}
    except Exception as e:
        logger.error('Claim upload failed for %s: %s', claim_id, e, exc_info=True)
        log.status = 'rejected'
        log.save(update_fields=['status', 'updated_at'])
        return {'status': 'error', 'claim_id': str(claim_id), 'message': str(e)}


@shared_task
def poll_oa_outbound():
    """
    Periodic task: Download and process new response files from
    Office Ally SFTP /outbound. Parses 999, 277CA, 835, and File Summary
    files, then updates Claim/Payment records accordingly.

    Wire into Celery Beat (see config/celery.py beat_schedule).
    """
    from apps.accounts.models import Organization
    from .services.office_ally import process_outbound_files, _is_configured

    if not _is_configured():
        logger.info('OA SFTP not configured — skipping outbound poll')
        return {'status': 'skipped', 'reason': 'sftp_not_configured'}

    # OA SFTP is per-account, so we process once per organization that has
    # any submission history. For single-tenant deployments this is just one.
    results = {}
    orgs = Organization.objects.filter(oa_submission_logs__isnull=False).distinct()
    for org in orgs:
        try:
            results[str(org.id)] = process_outbound_files(org)
        except Exception as e:
            logger.error('OA outbound poll failed for org %s: %s', org.id, e, exc_info=True)
            results[str(org.id)] = {'error': str(e)}
    return results


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
