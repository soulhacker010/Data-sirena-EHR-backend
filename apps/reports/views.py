"""
Reports views — CSV-exportable reports.

Endpoints (coordinated with frontend api/reports.ts):
- GET /api/v1/reports/session-summary/   → SessionSummaryReport
- GET /api/v1/reports/billing-summary/   → BillingSummaryReport
- GET /api/v1/reports/authorizations/    → AuthorizationReport
- GET /api/v1/reports/missing-notes/     → MissingNotesReport
- GET /api/v1/reports/analytics/         → AnalyticsReport (client-requested KPIs)

All support ?format=csv query param for CSV download.
Frontend sends start_date / end_date (not date_from / date_to).
"""
import csv
from datetime import timedelta
from django.db.models import Sum, Count, Q, F
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsSupervisorOrAbove


class SessionSummaryView(APIView):
    """
    GET /api/v1/reports/session-summary/

    Frontend expects SessionSummaryReport:
    {
        total_sessions, total_hours, total_units, unique_clients,
        provider_breakdown: [{provider_name, sessions, hours, units}],
        service_breakdown: [{service_code, description, sessions, units}]
    }
    """
    permission_classes = [IsAuthenticated, IsSupervisorOrAbove]

    def get(self, request):
        from apps.scheduling.models import Appointment

        org = request.organization
        # Frontend sends start_date / end_date
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')

        qs = Appointment.objects.filter(organization=org, status='attended')
        if start_date:
            qs = qs.filter(start_time__gte=start_date)
        if end_date:
            qs = qs.filter(start_time__lte=end_date)

        # Totals
        totals = qs.aggregate(
            total_sessions=Count('id'),
            total_units=Sum('units'),
        )
        total_sessions = totals['total_sessions'] or 0
        total_units = float(totals['total_units'] or 0)
        # Estimate hours from units (1 unit = 15 min)
        total_hours = round(total_units * 0.25, 1)
        unique_clients = qs.values('client').distinct().count()

        # Provider breakdown
        by_provider = qs.values(
            'provider__first_name', 'provider__last_name'
        ).annotate(
            sessions=Count('id'),
            units=Sum('units'),
        ).order_by('provider__last_name')

        provider_breakdown = [
            {
                'provider_name': f"{row['provider__first_name']} {row['provider__last_name']}",
                'sessions': row['sessions'],
                'hours': round(float(row['units'] or 0) * 0.25, 1),
                'units': float(row['units'] or 0),
            }
            for row in by_provider
        ]

        # Service breakdown
        by_service = qs.values('service_code').annotate(
            sessions=Count('id'),
            units=Sum('units'),
        ).order_by('service_code')

        service_breakdown = [
            {
                'service_code': row['service_code'] or 'Unknown',
                'description': row['service_code'] or '',
                'sessions': row['sessions'],
                'units': float(row['units'] or 0),
            }
            for row in by_service
        ]

        result = {
            'total_sessions': total_sessions,
            'total_hours': total_hours,
            'total_units': total_units,
            'unique_clients': unique_clients,
            'provider_breakdown': provider_breakdown,
            'service_breakdown': service_breakdown,
        }

        # CSV export
        if request.query_params.get('format') == 'csv':
            return self._csv_response(provider_breakdown, 'session_summary')

        return Response(result)

    def _csv_response(self, data, filename):
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{filename}.csv"'
        if data:
            writer = csv.DictWriter(response, fieldnames=data[0].keys())
            writer.writeheader()
            writer.writerows(data)
        return response


class BillingSummaryView(APIView):
    """
    GET /api/v1/reports/billing-summary/

    Frontend expects BillingSummaryReport:
    {
        total_billed, total_collected, total_outstanding,
        collections_rate,
        payer_breakdown: [{payer_name, billed, collected, outstanding}]
    }
    """
    permission_classes = [IsAuthenticated, IsSupervisorOrAbove]

    def get(self, request):
        from apps.billing.models import Invoice, Payment

        org = request.organization
        # Frontend sends start_date / end_date
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')

        invoice_qs = Invoice.objects.filter(organization=org)
        if start_date:
            invoice_qs = invoice_qs.filter(invoice_date__gte=start_date)
        if end_date:
            invoice_qs = invoice_qs.filter(invoice_date__lte=end_date)

        totals = invoice_qs.aggregate(
            total_billed=Sum('total_amount'),
            total_collected=Sum('paid_amount'),
            total_outstanding=Sum('balance'),
        )

        total_billed = float(totals['total_billed'] or 0)
        total_collected = float(totals['total_collected'] or 0)

        # Payer breakdown (group by client for now, since payer tracking
        # may vary — this gives a useful per-client breakdown)
        payer_data = invoice_qs.values(
            'client__first_name', 'client__last_name'
        ).annotate(
            billed=Sum('total_amount'),
            collected=Sum('paid_amount'),
            outstanding=Sum('balance'),
        ).order_by('-billed')[:10]

        payer_breakdown = [
            {
                'payer_name': f"{row['client__first_name']} {row['client__last_name']}",
                'billed': float(row['billed'] or 0),
                'collected': float(row['collected'] or 0),
                'outstanding': float(row['outstanding'] or 0),
            }
            for row in payer_data
        ]

        return Response({
            'total_billed': total_billed,
            'total_collected': total_collected,
            'total_outstanding': float(totals['total_outstanding'] or 0),
            'collections_rate': round(total_collected / total_billed * 100, 1) if total_billed > 0 else 0,
            'payer_breakdown': payer_breakdown,
        })


class AuthorizationReportView(APIView):
    """
    GET /api/v1/reports/authorizations/

    Frontend expects AuthorizationReport:
    {
        authorizations: [{
            id, client_name, insurance_name, authorization_number,
            service_code, units_approved, units_used, units_remaining,
            start_date, end_date, utilization_percent, is_expired
        }]
    }
    """
    permission_classes = [IsAuthenticated, IsSupervisorOrAbove]

    def get(self, request):
        from apps.clients.models import Authorization

        org = request.organization

        qs = Authorization.objects.filter(
            client__organization=org,
        ).select_related('client')

        # Optional: only show active (not expired)
        show_expired = request.query_params.get('show_expired', 'false') == 'true'
        if not show_expired:
            qs = qs.filter(end_date__gte=timezone.now().date())

        results = [
            {
                'id': str(auth.id),
                'client_name': auth.client.full_name,
                'insurance_name': auth.insurance_name or '',
                'authorization_number': auth.authorization_number,
                'service_code': auth.service_code,
                'units_approved': auth.units_approved,
                'units_used': auth.units_used,
                'units_remaining': auth.units_remaining,
                'start_date': str(auth.start_date),
                'end_date': str(auth.end_date),
                'utilization_percent': round(
                    auth.units_used / auth.units_approved * 100, 1
                ) if auth.units_approved > 0 else 0,
                'is_expired': auth.is_expired,
            }
            for auth in qs
        ]

        if request.query_params.get('format') == 'csv':
            return self._csv_response(results, 'authorization_report')

        return Response({'authorizations': results})

    def _csv_response(self, data, filename):
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{filename}.csv"'
        if data:
            writer = csv.DictWriter(response, fieldnames=data[0].keys())
            writer.writeheader()
            writer.writerows(data)
        return response


class MissingNotesView(APIView):
    """
    GET /api/v1/reports/missing-notes/

    Frontend expects MissingNotesReport:
    {
        missing_notes: [{
            id, client_name, provider_name, session_date,
            service_code, days_overdue
        }]
    }
    """
    permission_classes = [IsAuthenticated, IsSupervisorOrAbove]

    def get(self, request):
        from apps.scheduling.models import Appointment

        org = request.organization
        now = timezone.now()
        # Frontend sends start_date / end_date
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')

        qs = Appointment.objects.filter(
            organization=org,
            status='attended',
            session_note__isnull=True,
        ).select_related('client', 'provider')

        if start_date:
            qs = qs.filter(start_time__gte=start_date)
        if end_date:
            qs = qs.filter(start_time__lte=end_date)

        results = [
            {
                'id': str(appt.id),
                'client_id': str(appt.client_id),
                'client_name': appt.client.full_name,
                'provider_name': appt.provider.full_name,
                'session_date': appt.start_time.strftime('%Y-%m-%d'),
                'service_code': appt.service_code,
                'days_overdue': (now - appt.start_time).days,
            }
            for appt in qs.order_by('-start_time')
        ]

        if request.query_params.get('format') == 'csv':
            return self._csv_response(results, 'missing_notes')

        return Response({'missing_notes': results})

    def _csv_response(self, data, filename):
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{filename}.csv"'
        if data:
            writer = csv.DictWriter(response, fieldnames=data[0].keys())
            writer.writeheader()
            writer.writerows(data)
        return response


class AnalyticsView(APIView):
    """
    GET /api/v1/reports/analytics/

    Client-requested KPI analytics dashboard:
    1. Average length of care (days from first to last appointment)
    2. Dropout patterns (clients with no visits in 30/60/90 days)
    3. Referral source ROI (revenue grouped by referral_source)
    4. Revenue per clinical hour
    5. Revenue per location (inc. Telehealth)
    6. ABA utilization rates (billed/approved from Authorization)
    7. Payment totals by timeframe (monthly trend)
    8. Active patient count

    Supports ?start_date= and ?end_date= for filtering.
    Coordinated with frontend api/reports.ts → AnalyticsReport.
    """
    permission_classes = [IsAuthenticated, IsSupervisorOrAbove]

    def get(self, request):
        from apps.clients.models import Client, Authorization
        from apps.scheduling.models import Appointment
        from apps.billing.models import Invoice, Payment
        from apps.accounts.models import Location

        org = request.organization
        now = timezone.now()
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')

        return Response({
            'avg_length_of_care_days': self._avg_length_of_care(
                Appointment, org, start_date, end_date,
            ),
            'dropout_patterns': self._dropout_patterns(
                Client, Appointment, org, now,
            ),
            'referral_source_roi': self._referral_source_roi(
                Client, Invoice, org, start_date, end_date,
            ),
            'revenue_per_clinical_hour': self._revenue_per_hour(
                Appointment, Payment, org, start_date, end_date,
            ),
            'revenue_per_location': self._revenue_per_location(
                Appointment, Invoice, Location, org, start_date, end_date,
            ),
            'aba_utilization': self._aba_utilization(
                Authorization, org,
            ),
            'payment_summary': self._payment_summary(
                Payment, org, now,
            ),
            'active_patients': Client.objects.filter(
                organization=org, is_active=True,
            ).count(),
        })

    # ── 1. Average length of care ─────────────────────────────────────────
    def _avg_length_of_care(self, Appointment, org, start_date, end_date):
        """Average days between first and last appointment per active client."""
        from django.db.models import Min, Max
        from apps.clients.models import Client

        qs = Appointment.objects.filter(
            organization=org, status='attended',
        )
        if start_date:
            qs = qs.filter(start_time__gte=start_date)
        if end_date:
            qs = qs.filter(start_time__lte=end_date)

        spans = qs.values('client').annotate(
            first=Min('start_time'), last=Max('start_time'),
        ).filter(first__isnull=False, last__isnull=False)

        durations = []
        for row in spans:
            days = (row['last'] - row['first']).days
            if days > 0:
                durations.append(days)

        return round(sum(durations) / len(durations), 1) if durations else 0

    # ── 2. Dropout patterns ───────────────────────────────────────────────
    def _dropout_patterns(self, Client, Appointment, org, now):
        """Count active clients with no appointments in 30/60/90 days."""
        from django.db.models import Max

        active_clients = Client.objects.filter(
            organization=org, is_active=True,
        )
        total = active_clients.count()

        # For each active client, find their most recent appointment
        latest_appts = Appointment.objects.filter(
            organization=org, status='attended',
        ).values('client').annotate(last_visit=Max('start_time'))

        last_visit_map = {
            row['client']: row['last_visit'] for row in latest_appts
        }

        no_30, no_60, no_90 = 0, 0, 0
        day_30 = now - timedelta(days=30)
        day_60 = now - timedelta(days=60)
        day_90 = now - timedelta(days=90)

        for client in active_clients:
            last = last_visit_map.get(client.id)
            if last is None:
                # Never attended — count as 90+ day dropout
                no_30 += 1
                no_60 += 1
                no_90 += 1
            elif last < day_90:
                no_30 += 1
                no_60 += 1
                no_90 += 1
            elif last < day_60:
                no_30 += 1
                no_60 += 1
            elif last < day_30:
                no_30 += 1

        return {
            'no_visit_30_days': no_30,
            'no_visit_60_days': no_60,
            'no_visit_90_days': no_90,
            'total_active_clients': total,
        }

    # ── 3. Referral source ROI ────────────────────────────────────────────
    def _referral_source_roi(self, Client, Invoice, org, start_date, end_date):
        """Revenue and client count grouped by referral_source."""
        from django.db.models import Sum, Count

        qs = Client.objects.filter(
            organization=org,
            referral_source__gt='',  # Only clients with a source set
        )

        invoice_filter = {}
        if start_date:
            invoice_filter['invoices__invoice_date__gte'] = start_date
        if end_date:
            invoice_filter['invoices__invoice_date__lte'] = end_date

        results = qs.values('referral_source').annotate(
            clients=Count('id', distinct=True),
            revenue=Sum('invoices__paid_amount', **invoice_filter),
        ).order_by('-revenue')

        return [
            {
                'source': row['referral_source'],
                'clients': row['clients'],
                'revenue': float(row['revenue'] or 0),
            }
            for row in results
        ]

    # ── 4. Revenue per clinical hour ──────────────────────────────────────
    def _revenue_per_hour(self, Appointment, Payment, org, start_date, end_date):
        """Total revenue divided by total clinical hours."""
        from django.db.models import Sum

        appt_qs = Appointment.objects.filter(
            organization=org, status='attended',
        )
        pay_qs = Payment.objects.filter(
            invoice__organization=org, payment_type='payment',
        )

        if start_date:
            appt_qs = appt_qs.filter(start_time__gte=start_date)
            pay_qs = pay_qs.filter(payment_date__gte=start_date)
        if end_date:
            appt_qs = appt_qs.filter(start_time__lte=end_date)
            pay_qs = pay_qs.filter(payment_date__lte=end_date)

        total_units = float(appt_qs.aggregate(u=Sum('units'))['u'] or 0)
        total_hours = total_units * 0.25  # 1 unit = 15 min
        total_revenue = float(pay_qs.aggregate(r=Sum('amount'))['r'] or 0)

        if total_hours > 0:
            return round(total_revenue / total_hours, 2)
        return 0

    # ── 5. Revenue per location ───────────────────────────────────────────
    def _revenue_per_location(self, Appointment, Invoice, Location, org, start_date, end_date):
        """Revenue and session count grouped by location (inc. Telehealth)."""
        from django.db.models import Sum, Count

        appt_qs = Appointment.objects.filter(
            organization=org, status='attended',
            location__isnull=False,
        )
        if start_date:
            appt_qs = appt_qs.filter(start_time__gte=start_date)
        if end_date:
            appt_qs = appt_qs.filter(start_time__lte=end_date)

        # Session count per location (Appointment → Location is a direct FK)
        by_loc = appt_qs.values(
            'location__id', 'location__name', 'location__is_telehealth',
        ).annotate(
            sessions=Count('id'),
        ).order_by('location__name')

        # Revenue per location: sum payments for clients who had appointments
        # at each location. Appointment has no direct FK to Invoice, so we
        # aggregate through Payment → Invoice → Client → Appointments.
        from apps.billing.models import Payment
        results = []
        for row in by_loc:
            loc_id = row['location__id']
            # Find clients who had attended appointments at this location
            client_ids = appt_qs.filter(location_id=loc_id).values_list(
                'client_id', flat=True,
            ).distinct()
            revenue = Payment.objects.filter(
                invoice__organization=org,
                invoice__client_id__in=list(client_ids),
                payment_type='payment',
            ).aggregate(total=Sum('amount'))['total'] or 0

            results.append({
                'location_name': row['location__name'] or 'Unknown',
                'is_telehealth': row['location__is_telehealth'] or False,
                'revenue': float(revenue),
                'sessions': row['sessions'],
            })

        return results

    # ── 6. ABA utilization rates ──────────────────────────────────────────
    def _aba_utilization(self, Authorization, org):
        """Billed units vs approved units from active authorizations."""
        from django.db.models import Sum

        active_auths = Authorization.objects.filter(
            client__organization=org,
            end_date__gte=timezone.now().date(),
        ).select_related('client')

        totals = active_auths.aggregate(
            total_approved=Sum('units_approved'),
            total_used=Sum('units_used'),
        )

        total_approved = totals['total_approved'] or 0
        total_used = totals['total_used'] or 0

        by_client = []
        for auth in active_auths.order_by('client__last_name'):
            pct = round(auth.units_used / auth.units_approved * 100, 1) \
                if auth.units_approved > 0 else 0
            by_client.append({
                'client_name': auth.client.full_name,
                'authorization_number': auth.authorization_number,
                'approved': auth.units_approved,
                'used': auth.units_used,
                'percent': pct,
            })

        return {
            'total_approved': total_approved,
            'total_used': total_used,
            'utilization_percent': round(
                total_used / total_approved * 100, 1,
            ) if total_approved > 0 else 0,
            'by_client': by_client,
        }

    # ── 7. Payment summary by timeframe ───────────────────────────────────
    def _payment_summary(self, Payment, org, now):
        """Current month, previous month, YTD, and 12-month trend."""
        from django.db.models import Sum
        from django.db.models.functions import TruncMonth

        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        prev_month_start = (month_start - timedelta(days=1)).replace(day=1)
        year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        twelve_months_ago = now - timedelta(days=365)

        base_qs = Payment.objects.filter(
            invoice__organization=org, payment_type='payment',
        )

        current_month = float(
            base_qs.filter(payment_date__gte=month_start)
            .aggregate(t=Sum('amount'))['t'] or 0
        )
        previous_month = float(
            base_qs.filter(
                payment_date__gte=prev_month_start,
                payment_date__lt=month_start,
            ).aggregate(t=Sum('amount'))['t'] or 0
        )
        year_to_date = float(
            base_qs.filter(payment_date__gte=year_start)
            .aggregate(t=Sum('amount'))['t'] or 0
        )

        # Monthly trend (last 12 months)
        trend = base_qs.filter(
            payment_date__gte=twelve_months_ago,
        ).annotate(
            month=TruncMonth('payment_date'),
        ).values('month').annotate(
            total=Sum('amount'),
        ).order_by('month')

        monthly_trend = [
            {
                'month': row['month'].strftime('%Y-%m'),
                'total': float(row['total'] or 0),
            }
            for row in trend
        ]

        return {
            'current_month': current_month,
            'previous_month': previous_month,
            'year_to_date': year_to_date,
            'monthly_trend': monthly_trend,
        }

