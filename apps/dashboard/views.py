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
        org = request.user.organization
        now = timezone.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        if org is None:
            return Response({'error': 'User has no organization assigned.'}, status=400)

        # Import models here to avoid circular imports
        from apps.clients.models import Client
        from apps.scheduling.models import Appointment
        from apps.clinical.models import SessionNote
        from apps.billing.models import Invoice, Claim, Payment
        from apps.audit.models import AuditLog

        # Clinicians see only their own data; others see org-wide
        is_clinician = request.user.role == 'clinician'

        total_clients = Client.objects.filter(organization=org, is_active=True).count()

        # "Sessions this month" should reflect what actually happened. A session
        # counts if EITHER the appointment was manually marked attended OR a
        # SessionNote linked to it has been signed/co-signed (a signed note is
        # ground truth that the session occurred — it's why Dr. Joe was seeing
        # 0 even though he had signed sessions).
        sessions_qs = Appointment.objects.filter(
            organization=org,
            start_time__gte=month_start,
        ).filter(
            Q(status='attended')
            | Q(session_note__status__in=['signed', 'co_signed'])
        ).distinct()
        if is_clinician:
            sessions_qs = sessions_qs.filter(provider=request.user)
        sessions_this_month = sessions_qs.count()

        # Pending notes = (a) past appointments without a signed note +
        #                 (b) draft/completed (unsigned) notes +
        #                 (c) signed notes pending co-sign on the user.
        #
        # E23 fix: an appointment whose start_time has passed but is still
        # marked 'scheduled' (provider didn't manually flip to 'attended')
        # must STILL count — Dr. Joe's complaint was that he had a session
        # 2 days ago and pending_notes didn't reflect it. Same root-cause as
        # B6 (sessions_this_month): the system can't rely on manual status
        # transitions, so it derives "did this happen" from start_time
        # passing or from a signed note appearing.
        attended_no_note_qs = Appointment.objects.filter(
            organization=org,
        ).filter(
            # Either explicitly attended, OR scheduled-and-time-has-passed.
            # Cancelled / no_show are deliberately excluded — those don't
            # need a clinical note.
            Q(status='attended')
            | Q(status='scheduled', start_time__lt=now)
        ).exclude(
            session_note__status='signed'
        )
        if is_clinician:
            attended_no_note_qs = attended_no_note_qs.filter(provider=request.user)

        unsigned_notes_qs = SessionNote.objects.filter(
            client__organization=org,
            status__in=['draft', 'completed'],
        )
        if is_clinician:
            unsigned_notes_qs = unsigned_notes_qs.filter(provider=request.user)

        cosign_pending = SessionNote.objects.filter(
            client__organization=org,
            status='signed',
            note_data__co_sign_request__recipient_id=str(request.user.id),
        ).count()

        pending_notes = attended_no_note_qs.count() + unsigned_notes_qs.count() + cosign_pending

        # Revenue MTD
        revenue_mtd = Payment.objects.filter(
            invoice__organization=org,
            payment_date__gte=month_start,
            payment_type='payment',
        ).aggregate(total=Sum('amount'))['total'] or 0

        # Upcoming appointments (next 7 days)
        upcoming_qs = Appointment.objects.filter(
            organization=org,
            start_time__gte=now,
            start_time__lte=now + timedelta(days=7),
            status='scheduled',
        )
        if is_clinician:
            upcoming_qs = upcoming_qs.filter(provider=request.user)
        upcoming = upcoming_qs.select_related('client', 'provider').order_by('start_time')[:5]

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

        # Recent activity feed from audit log.
        # E29 (Dr. Joe 2026-05-04): clinicians must see ONLY their own activity
        # so other staff's actions (and the implicated clients) don't bleed
        # into someone else's chart view. Admins/supervisors continue to see
        # the full org activity. This is a per-user privacy filter — broader
        # caseload-based filtering would need an explicit AuditLog→Client
        # link, which is a separate schema decision.
        recent_logs_qs = AuditLog.objects.filter(
            organization=org,
        ).select_related('user')
        if is_clinician:
            recent_logs_qs = recent_logs_qs.filter(user=request.user)
        recent_logs = recent_logs_qs.order_by('-timestamp')[:10]

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

        # E22 (Dr. Joe 2026-05-04): "If I stop doing my intake or treatment
        # plan, will it save progress? Will it notify me that it is incomplete?"
        # Auto-save is already in place across all three editors. The missing
        # piece was a single place that surfaces "drafts you started but
        # haven't completed". We expose them on the dashboard as a unified
        # list of three doc types — clinicians get their own; admins org-wide.
        from apps.clinical.models import IntakeAssessment, TreatmentPlan

        incomplete_qs = {
            'session_notes': SessionNote.objects.filter(
                client__organization=org,
                status__in=['draft', 'completed'],
            ).select_related('client'),
            'intakes': IntakeAssessment.objects.filter(
                client__organization=org,
                status__in=['draft', 'completed'],
            ).select_related('client'),
            'treatment_plans': TreatmentPlan.objects.filter(
                client__organization=org,
                status__in=['draft', 'active'],  # 'active' here = unsigned working version
                is_locked=False,
            ).select_related('client'),
        }
        if is_clinician:
            incomplete_qs = {
                k: q.filter(provider=request.user)
                for k, q in incomplete_qs.items()
            }

        incomplete_drafts = []
        for kind, qs in incomplete_qs.items():
            for obj in qs.order_by('-updated_at')[:5]:
                incomplete_drafts.append({
                    'kind': kind,  # 'session_notes' | 'intakes' | 'treatment_plans'
                    'id': str(obj.id),
                    'client_name': obj.client.full_name if obj.client else '—',
                    'status': obj.status,
                    'updated_at': obj.updated_at.isoformat(),
                })
        # Surface the most recently touched incomplete docs first across types.
        incomplete_drafts.sort(key=lambda d: d['updated_at'], reverse=True)
        incomplete_drafts = incomplete_drafts[:8]  # cap so the widget stays small

        return Response({
            'total_clients': total_clients,
            'sessions_this_month': sessions_this_month,
            'pending_notes': pending_notes,
            'revenue_mtd': float(revenue_mtd),
            'upcoming_appointments': upcoming_data,
            'recent_activity': recent_activity,
            'incomplete_drafts': incomplete_drafts,
            'billing_overview': {
                'invoices_pending': invoices_pending,
                'outstanding_balance': float(outstanding_balance),
                'claims_submitted': claims_submitted,
                'claims_denied': claims_denied,
                'collections_rate': collections_rate,
            },
        })
