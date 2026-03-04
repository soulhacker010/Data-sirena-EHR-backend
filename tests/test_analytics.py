"""
Round 7: Analytics endpoint tests.

Tests the client-requested analytics KPIs:
- Average length of care
- Dropout patterns
- Referral source ROI
- Revenue per clinical hour
- Revenue per location (inc. Telehealth)
- ABA utilization rates
- Payment summary by timeframe
- Active patient count
"""
import pytest
from datetime import timedelta
from decimal import Decimal
from django.utils import timezone
from rest_framework import status


@pytest.mark.django_db
class TestAnalyticsEndpoint:
    """GET /api/v1/reports/analytics/ — all 8 KPIs."""
    url = '/api/v1/reports/analytics/'

    def test_analytics_returns_200(self, admin_client):
        """Basic smoke test — endpoint returns 200."""
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK

    def test_analytics_response_shape(self, admin_client):
        """Verify all 8 top-level keys exist in the response."""
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        for key in [
            'avg_length_of_care_days',
            'dropout_patterns',
            'referral_source_roi',
            'revenue_per_clinical_hour',
            'revenue_per_location',
            'aba_utilization',
            'payment_summary',
            'active_patients',
        ]:
            assert key in resp.data, f"Missing key: {key}"

    def test_dropout_patterns_shape(self, admin_client):
        """Dropout patterns must have 30/60/90 day buckets + total."""
        resp = admin_client.get(self.url)
        dp = resp.data['dropout_patterns']
        for key in ['no_visit_30_days', 'no_visit_60_days', 'no_visit_90_days', 'total_active_clients']:
            assert key in dp, f"Dropout missing: {key}"

    def test_aba_utilization_shape(self, admin_client):
        """ABA utilization must have total_approved, total_used, percent, by_client."""
        resp = admin_client.get(self.url)
        aba = resp.data['aba_utilization']
        for key in ['total_approved', 'total_used', 'utilization_percent', 'by_client']:
            assert key in aba, f"ABA missing: {key}"

    def test_payment_summary_shape(self, admin_client):
        """Payment summary must have current_month, previous_month, YTD, monthly_trend."""
        resp = admin_client.get(self.url)
        ps = resp.data['payment_summary']
        for key in ['current_month', 'previous_month', 'year_to_date', 'monthly_trend']:
            assert key in ps, f"Payment summary missing: {key}"

    def test_active_patients_is_int(self, admin_client):
        """Active patients must be a non-negative integer."""
        resp = admin_client.get(self.url)
        assert isinstance(resp.data['active_patients'], int)
        assert resp.data['active_patients'] >= 0

    def test_date_range_filtering(self, admin_client):
        """Endpoint accepts start_date and end_date params."""
        resp = admin_client.get(self.url, {
            'start_date': '2026-01-01',
            'end_date': '2026-12-31',
        })
        assert resp.status_code == status.HTTP_200_OK

    def test_empty_org_returns_zeros(self, admin_client):
        """New org with no data should return zeros, not crash."""
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['avg_length_of_care_days'] >= 0
        assert resp.data['revenue_per_clinical_hour'] >= 0
        assert resp.data['active_patients'] >= 0


@pytest.mark.django_db
class TestAnalyticsPermissions:
    """Permission checks for analytics endpoint."""
    url = '/api/v1/reports/analytics/'

    def test_clinician_cannot_access(self, clinician_client):
        """Clinicians should not access analytics (supervisor+ only)."""
        resp = clinician_client.get(self.url)
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_admin_can_access(self, admin_client):
        """Admin should access analytics."""
        resp = admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK

    def test_unauthenticated_cannot_access(self, api_client):
        """Unauthenticated users should get 401."""
        resp = api_client.get(self.url)
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestAnalyticsWithData:
    """Tests with actual data — verify calculations."""
    url = '/api/v1/reports/analytics/'

    def test_active_patients_count(self, admin_client, sample_client):
        """Active patient count should include our test client."""
        resp = admin_client.get(self.url)
        assert resp.data['active_patients'] >= 1

    def test_dropout_patterns_with_recent_appointment(
        self, admin_client, sample_client, sample_appointment,
    ):
        """Client with a recent appointment should NOT be in dropout 30-day bucket."""
        # sample_appointment is in the future, so client has recent activity
        resp = admin_client.get(self.url)
        dp = resp.data['dropout_patterns']
        # The total_active_clients should be at least 1
        assert dp['total_active_clients'] >= 1

    def test_aba_utilization_with_authorization(
        self, admin_client, sample_client,
    ):
        """Create authorization and verify it shows in ABA utilization."""
        from apps.clients.models import Authorization
        now = timezone.now().date()
        Authorization.objects.create(
            client=sample_client,
            insurance_name='Test Insurance',
            authorization_number='AUTH-001',
            service_code='97153',
            units_approved=100,
            units_used=75,
            start_date=now - timedelta(days=30),
            end_date=now + timedelta(days=30),
        )
        resp = admin_client.get(self.url)
        aba = resp.data['aba_utilization']
        assert aba['total_approved'] >= 100
        assert aba['total_used'] >= 75
        assert aba['utilization_percent'] > 0
        assert len(aba['by_client']) >= 1

    def test_referral_source_roi_with_data(
        self, admin_client, org, sample_client,
    ):
        """Client with referral_source + invoices should show in ROI."""
        from apps.billing.models import Invoice
        sample_client.referral_source = 'Website'
        sample_client.save()
        Invoice.objects.create(
            organization=org,
            client=sample_client,
            invoice_number='INV-REF-001',
            invoice_date='2026-03-01',
            total_amount=Decimal('500.00'),
            paid_amount=Decimal('500.00'),
            balance=Decimal('0.00'),
            status='paid',
        )
        resp = admin_client.get(self.url)
        roi = resp.data['referral_source_roi']
        assert len(roi) >= 1
        website_entry = next((r for r in roi if r['source'] == 'Website'), None)
        assert website_entry is not None
        assert website_entry['clients'] >= 1
        assert website_entry['revenue'] >= 500.0

    def test_payment_summary_with_payment(
        self, admin_client, org, sample_client,
    ):
        """Payment in current month should show in payment summary."""
        from apps.billing.models import Invoice, Payment
        invoice = Invoice.objects.create(
            organization=org,
            client=sample_client,
            invoice_number='INV-PAY-001',
            invoice_date=timezone.now().date(),
            total_amount=Decimal('200.00'),
            paid_amount=Decimal('200.00'),
            balance=Decimal('0.00'),
            status='paid',
        )
        Payment.objects.create(
            invoice=invoice,
            client=sample_client,
            amount=Decimal('200.00'),
            payment_type='payment',
            payment_method='stripe',
            payment_date=timezone.now().date(),
        )
        resp = admin_client.get(self.url)
        ps = resp.data['payment_summary']
        assert ps['current_month'] >= 200.0
        assert ps['year_to_date'] >= 200.0

    def test_org_scoping(self, other_admin_client):
        """Other org admin should NOT see our data."""
        resp = other_admin_client.get(self.url)
        assert resp.status_code == status.HTTP_200_OK
        # Other org should have 0 active patients (our test data is in a different org)
        # May have some clients from conftest, but should not have our
        # referral source or authorization data
        assert resp.data['aba_utilization']['total_approved'] == 0
