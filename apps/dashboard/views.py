"""
Dashboard stats view — coordinated with frontend api/billing.ts → dashboardApi (or DashboardPage).

GET /api/v1/dashboard/stats/ → DashboardPage loads
"""
from datetime import timedelta
from django.db.models import Sum, Count, Q
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


class DashboardStatsView(APIView):
    """
    GET /api/v1/dashboard/stats/

    Aggregates key metrics for the dashboard:
    - total_clients, sessions_this_month, pending_notes, revenue_mtd
    - upcoming_appointments, recent_activity, billing_overview
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org = request.organization
        now = timezone.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # Import models here to avoid circular imports
        from apps.clients.models import Client
        from apps.scheduling.models import Appointment
        from apps.clinical.models import SessionNote
        from apps.billing.models import Invoice, Claim, Payment

        # Core stats
        total_clients = Client.objects.filter(organization=org, is_active=True).count()
        sessions_this_month = Appointment.objects.filter(
            organization=org,
            start_time__gte=month_start,
            status='attended',
        ).count()
        pending_notes = SessionNote.objects.filter(
            client__organization=org,
            status__in=['draft', 'completed'],
        ).count()

        # Revenue MTD
        revenue_mtd = Payment.objects.filter(
            invoice__organization=org,
            payment_date__gte=month_start,
            payment_type='payment',
        ).aggregate(total=Sum('amount'))['total'] or 0

        # Upcoming appointments (next 7 days)
        upcoming = Appointment.objects.filter(
            organization=org,
            start_time__gte=now,
            start_time__lte=now + timedelta(days=7),
            status='scheduled',
        ).select_related('client', 'provider').order_by('start_time')[:5]

        upcoming_data = [
            {
                'id': str(appt.id),
                'client_name': appt.client.full_name,
                'provider_name': appt.provider.full_name,
                'start_time': appt.start_time.isoformat(),
                'end_time': appt.end_time.isoformat(),
                'service_code': appt.service_code,
                'status': appt.status,
            }
            for appt in upcoming
        ]

        # Billing overview
        invoices_pending = Invoice.objects.filter(
            organization=org, status='pending'
        ).count()
        claims_submitted = Claim.objects.filter(
            invoice__organization=org, status='submitted'
        ).count()
        claims_denied = Claim.objects.filter(
            invoice__organization=org, status='denied'
        ).count()

        # Collections rate
        total_billed = Invoice.objects.filter(
            organization=org,
            invoice_date__gte=month_start,
        ).aggregate(total=Sum('total_amount'))['total'] or 1  # avoid div/0
        collections_rate = round(float(revenue_mtd) / float(total_billed) * 100, 1)

        return Response({
            'total_clients': total_clients,
            'sessions_this_month': sessions_this_month,
            'pending_notes': pending_notes,
            'revenue_mtd': float(revenue_mtd),
            'upcoming_appointments': upcoming_data,
            'recent_activity': [],  # TODO: pull from audit log
            'billing_overview': {
                'invoices_pending': invoices_pending,
                'claims_submitted': claims_submitted,
                'claims_denied': claims_denied,
                'collections_rate': collections_rate,
            },
        })
