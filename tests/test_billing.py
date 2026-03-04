"""
Billing endpoint tests — invoices, payments, claims.
"""
import pytest
from rest_framework import status


@pytest.mark.django_db
class TestInvoiceCreate:
    url = '/api/v1/invoices/'

    def test_create_invoice(self, admin_client, sample_client):
        """Create invoice with items → 201."""
        resp = admin_client.post(self.url, {
            'client_id': str(sample_client.id),
            'invoice_date': '2026-03-01',
            'items': [
                {
                    'service_code': '97153',
                    'description': 'ABA Session',
                    'units': 8,
                    'rate': '62.50',
                    'amount': '500.00',
                }
            ],
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED

    def test_list_invoices(self, admin_client):
        """List invoices → 200."""
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestPayment:
    url = '/api/v1/payments/'

    def test_create_payment(self, admin_client, sample_client, org):
        """Create payment against an invoice → 201."""
        from apps.billing.models import Invoice
        invoice = Invoice.objects.create(
            organization=org,
            client=sample_client,
            invoice_number='INV-PAY-001',
            invoice_date='2026-03-01',
            total_amount=500,
            balance=500,
        )
        resp = admin_client.post(self.url, {
            'invoice_id': str(invoice.id),
            'amount': '100.00',
            'payment_type': 'payment',
            'payer_type': 'insurance',
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED


@pytest.mark.django_db
class TestClaims:
    url = '/api/v1/claims/'

    def test_create_claim(self, admin_client, sample_client, org):
        """Create insurance claim → 201."""
        from apps.billing.models import Invoice
        invoice = Invoice.objects.create(
            organization=org,
            client=sample_client,
            invoice_number='INV-CLM-001',
            invoice_date='2026-03-01',
            total_amount=1000,
            balance=1000,
        )
        resp = admin_client.post(self.url, {
            'invoice_id': str(invoice.id),
            'payer_name': 'Blue Cross',
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED

    def test_list_claims(self, admin_client):
        """List claims → 200."""
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
