"""
One-off script: Replay all succeeded Stripe PaymentIntents that are missing from DB.
Run from sirena-backend directory: python replay_payments.py
"""
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

import django
django.setup()

import stripe
from django.conf import settings
from apps.billing.webhooks import _handle_payment_succeeded
from apps.billing.models import Invoice, Payment

stripe.api_key = settings.STRIPE_SECRET_KEY

pis = stripe.PaymentIntent.list(limit=20)
handled = 0
for pi in pis.data:
    if pi.status == 'succeeded' and pi.metadata.get('invoice_id'):
        if not Payment.objects.filter(reference_number=pi.id).exists():
            print(f'Recording: {pi.id}  amount=${pi.amount_received/100:.2f}')
            _handle_payment_succeeded({
                'id': pi.id,
                'amount_received': pi.amount_received,
                'metadata': dict(pi.metadata),
            })
            handled += 1
        else:
            print(f'Already exists: {pi.id}')

print(f'\nNewly processed: {handled}')
print('\n=== INVOICES NOW ===')
for inv in Invoice.objects.all().order_by('-created_at'):
    print(f'  {inv.invoice_number}: total=${inv.total_amount}, paid=${inv.paid_amount}, status={inv.status}')
