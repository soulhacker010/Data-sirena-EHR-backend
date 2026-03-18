"""
Dashboard stats view — coordinated with frontend api/billing.ts → dashboardApi (or DashboardPage).

GET /api/v1/dashboard/stats/ → DashboardPage loads
"""
from datetime import timedelta
from django.db.models import Sum, Q, F, DecimalField, ExpressionWrapper
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
        from apps.audit.models import AuditLog

        # Core stats
        total_clients = Client.objects.filter(organization=org, is_active=True).count()
        sessions_this_month = Appointment.objects.filter(
            organization=org,
            start_time__gte=month_start,
            status='attended',
        ).count()
        if request.user.role == 'clinician':
            pending_notes = SessionNote.objects.filter(
                client__organization=org,
            ).filter(
                Q(provider=request.user, status__in=['draft', 'completed'])
                | Q(status='signed', note_data__co_sign_request__recipient_id=str(request.user.id))
            ).count()
        else:
            pending_notes = SessionNote.objects.filter(
                client__organization=org,
            ).filter(
                Q(status__in=['draft', 'completed'])
                | Q(status='signed', note_data__co_sign_request__recipient_id=str(request.user.id))
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
        outstanding_balance_expression = ExpressionWrapper(
            F('total_amount') - F('paid_amount'),
            output_field=DecimalField(max_digits=10, decimal_places=2),
        )
        outstanding_invoices = Invoice.objects.filter(
            organization=org,
        ).exclude(status='cancelled').annotate(
            computed_balance=outstanding_balance_expression,
        ).filter(
            computed_balance__gt=0,
        )
        invoices_pending = outstanding_invoices.count()
        outstanding_balance = outstanding_invoices.aggregate(
            total=Sum('computed_balance')
        )['total'] or 0
        claims_submitted = Claim.objects.filter(
            invoice__organization=org,
            status__in=['submitted', 'resubmitted', 'accepted', 'paid'],
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

        # Recent activity feed from audit log
        recent_logs = AuditLog.objects.filter(
            organization=org,
        ).select_related('user').order_by('-timestamp')[:10]

        action_labels = {
            'create': 'Created',
            'update': 'Updated',
            'partial_update': 'Updated',
            'delete': 'Deleted',
        }

        recent_activity = [
            {
                'id': str(log.id),
                'user_name': log.user.full_name if log.user else 'System',
                'action': action_labels.get(log.action, log.action),
                'target': log.table_name.replace('-', ' ').replace('_', ' ').title(),
                'timestamp': log.timestamp.isoformat(),
            }
            for log in recent_logs
        ]

        return Response({
            'total_clients': total_clients,
            'sessions_this_month': sessions_this_month,
            'pending_notes': pending_notes,
            'revenue_mtd': float(revenue_mtd),
            'upcoming_appointments': upcoming_data,
            'recent_activity': recent_activity,
            'billing_overview': {
                'invoices_pending': invoices_pending,
                'outstanding_balance': float(outstanding_balance),
                'claims_submitted': claims_submitted,
                'claims_denied': claims_denied,
                'collections_rate': collections_rate,
            },
        })
