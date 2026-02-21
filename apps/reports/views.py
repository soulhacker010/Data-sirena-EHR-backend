"""
Reports views — CSV-exportable reports.

Endpoints (coordinated with frontend api/reports.ts):
- GET /api/v1/reports/session-summary/   → SessionSummaryReport
- GET /api/v1/reports/billing-summary/   → BillingSummaryReport
- GET /api/v1/reports/authorizations/    → AuthorizationReport
- GET /api/v1/reports/missing-notes/     → MissingNotesReport

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
