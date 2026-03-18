"""
Stripe webhook handler.

Receives Stripe webhook events and processes payment confirmations,
failures, and refunds. Verifies signatures using STRIPE_WEBHOOK_SECRET.

Endpoint: POST /api/v1/payments/webhook/
"""
import logging
from decimal import Decimal

import stripe
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny

from .models import Invoice, Payment

logger = logging.getLogger(__name__)


def _notify_payment_recorded(payment):
    if not payment:
        return
    try:
        from apps.notifications.services import notify_payment_recorded
        notify_payment_recorded(payment)
    except Exception:
        pass


@csrf_exempt
@api_view(['POST'])
@authentication_classes([])   # FIX WH-1: Bypass DRF JWT auth — security comes from Stripe signature
@permission_classes([AllowAny])  # Stripe cannot send auth headers
def stripe_webhook(request):
    """
    POST /api/v1/payments/webhook/

    Processes Stripe webhook events:
    - payment_intent.succeeded  → Record payment, update invoice status
    - payment_intent.payment_failed → Log failure (no DB changes)
    - charge.refunded → Record refund, adjust invoice status
    """
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE', '')
    webhook_secret = getattr(settings, 'STRIPE_WEBHOOK_SECRET', '')

    if not webhook_secret:
        logger.warning('Stripe webhook received but STRIPE_WEBHOOK_SECRET is not configured')
        return HttpResponse('Webhook secret not configured', status=503)

    # Verify signature
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, webhook_secret,
        )
    except ValueError:
        logger.warning('Stripe webhook: invalid payload')
        return HttpResponse('Invalid payload', status=400)
    except stripe.error.SignatureVerificationError:
        logger.warning('Stripe webhook: signature verification failed')
        return HttpResponse('Invalid signature', status=400)

    event_type = event['type']
    data_object = event['data']['object']

    logger.info(f'Stripe webhook received: {event_type} ({event.get("id", "?")})')

    # ── payment_intent.succeeded ──
    if event_type == 'payment_intent.succeeded':
        _handle_payment_succeeded(data_object)

    # ── payment_intent.payment_failed ──
    elif event_type == 'payment_intent.payment_failed':
        _handle_payment_failed(data_object)

    # ── charge.refunded ──
    elif event_type == 'charge.refunded':
        _handle_refund(data_object)

    return HttpResponse('OK', status=200)


def _handle_payment_succeeded(payment_intent):
    """
    Record a successful payment against the invoice.

    Uses the metadata stored during PaymentIntent creation
    (invoice_id, organization_id).
    """
    metadata = payment_intent.get('metadata', {})
    invoice_id = metadata.get('invoice_id')

    if not invoice_id:
        logger.warning(f'Stripe payment succeeded but no invoice_id in metadata: {payment_intent["id"]}')
        return

    try:
        with transaction.atomic():
            invoice = Invoice.objects.select_for_update().get(pk=invoice_id)

            # Convert from cents to dollars
            amount = Decimal(str(payment_intent['amount_received'])) / 100

            # Check for duplicate payment (idempotency)
            if Payment.objects.filter(
                reference_number=payment_intent['id'],
            ).exists():
                logger.info(f'Duplicate Stripe payment ignored: {payment_intent["id"]}')
                return

            # Create payment record
            created_payment = Payment.objects.create(
                invoice=invoice,
                client=invoice.client,
                amount=amount,
                payment_type='payment',
                payment_method='stripe',
                reference_number=payment_intent['id'],
                payment_date=invoice.updated_at,  # Or timezone.now()
                notes=f'Stripe payment confirmed via webhook',
            )

            # Update invoice totals
            invoice.paid_amount = F('paid_amount') + amount
            invoice.save(update_fields=['paid_amount', 'updated_at'])

            # Refresh and update status
            invoice.refresh_from_db()
            if invoice.paid_amount >= invoice.total_amount:
                invoice.status = 'paid'
            elif invoice.paid_amount > 0:
                invoice.status = 'partial'
            invoice.balance = max(invoice.total_amount - invoice.paid_amount, Decimal('0'))
            invoice.save(update_fields=['status', 'balance', 'updated_at'])

        logger.info(
            f'Stripe payment recorded: ${amount} for invoice {invoice.invoice_number} '
            f'(PI: {payment_intent["id"]})'
        )
        _notify_payment_recorded(created_payment)

    except Invoice.DoesNotExist:
        logger.error(f'Stripe webhook: invoice {invoice_id} not found for PI {payment_intent["id"]}')
    except Exception as e:
        logger.error(f'Stripe webhook handler failed: {e}', exc_info=True)


def _handle_payment_failed(payment_intent):
    """Log failed payment (no DB changes needed)."""
    metadata = payment_intent.get('metadata', {})
    invoice_id = metadata.get('invoice_id')
    failure_message = payment_intent.get('last_payment_error', {}).get('message', 'Unknown error')

    logger.warning(
        f'Stripe payment failed: invoice={invoice_id}, '
        f'PI={payment_intent["id"]}, reason={failure_message}'
    )


def _handle_refund(charge):
    """
    Process a refund from Stripe.

    Creates a negative 'refund' payment record and adjusts the invoice balance.
    """
    payment_intent_id = charge.get('payment_intent')
    if not payment_intent_id:
        return

    # Find the original payment
    try:
        original_payment = Payment.objects.select_related('invoice').get(
            reference_number=payment_intent_id,
            payment_type='payment',
        )
    except Payment.DoesNotExist:
        logger.warning(f'Stripe refund for unknown payment: {payment_intent_id}')
        return

    # Check for duplicate refund processing
    refund_ref = f'refund_{charge["id"]}'
    if Payment.objects.filter(reference_number=refund_ref).exists():
        logger.info(f'Duplicate refund ignored: {refund_ref}')
        return

    try:
        with transaction.atomic():
            # Refund amount (from cents)
            refund_amount = Decimal(str(charge.get('amount_refunded', 0))) / 100

            # Create refund record
            Payment.objects.create(
                invoice=original_payment.invoice,
                client=original_payment.invoice.client,
                amount=refund_amount,
                payment_type='refund',
                payment_method='stripe',
                reference_number=refund_ref,
                payment_date=original_payment.payment_date,
                notes=f'Stripe refund for PI {payment_intent_id}',
            )

            # Adjust invoice balance
            invoice = original_payment.invoice
            invoice.paid_amount = F('paid_amount') - refund_amount
            invoice.save(update_fields=['paid_amount', 'updated_at'])

            # Refresh and update status
            invoice.refresh_from_db()
            if invoice.paid_amount <= 0:
                invoice.status = 'pending'
                invoice.paid_amount = 0
            elif invoice.paid_amount < invoice.total_amount:
                invoice.status = 'partial'
            else:
                invoice.status = 'paid'
            invoice.balance = max(invoice.total_amount - invoice.paid_amount, Decimal('0'))
            invoice.save(update_fields=['status', 'paid_amount', 'balance', 'updated_at'])

        logger.info(
            f'Stripe refund processed: ${refund_amount} for invoice '
            f'{original_payment.invoice.invoice_number}'
        )

    except Exception as e:
        logger.error(f'Stripe refund handler failed: {e}', exc_info=True)
