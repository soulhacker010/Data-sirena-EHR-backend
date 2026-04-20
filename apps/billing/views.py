"""
Billing views — invoices, payments, claims, claim payment posting, write-offs.

Endpoints coordinated with frontend api/billing.ts:
- InvoiceViewSet:     /api/v1/invoices/              → CRUD
- InvoiceViewSet:     /api/v1/invoices/batch/         → batch generate
- PaymentViewSet:     /api/v1/payments/               → record payment
- ClaimViewSet:       /api/v1/claims/                 → CRUD + submit + post-payment + write-off
- ClientClaimsView:   /api/v1/clients/{id}/claims/    → client-scoped claims
"""
import logging
from collections import defaultdict
from decimal import Decimal
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework import viewsets, generics, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


class EmailRateThrottle(UserRateThrottle):
    """Limit email sending to prevent spam abuse. Rate set via DRF_THROTTLE_EMAIL env var."""
    scope = 'email'


def _build_batch_issue(appt, client_id, issue, service_code=''):
    issue_data = {
        'appointment_id': str(appt.id),
        'client_id': str(client_id),
        'client_name': appt.client.full_name,
        'issue': issue,
    }
    if service_code:
        issue_data['service_code'] = service_code
    return issue_data


def _prepare_batch_invoice_items(client_appts, organization_id):
    invalid_appointments = []
    prepared_items_by_client = defaultdict(list)

    for client_id, appts in client_appts.items():
        for appt in appts:
            service_code = (appt.service_code or '').strip()
            units = Decimal(str(appt.units or 0))
            defaults = resolve_billing_defaults(
                organization_id=organization_id,
                client_id=client_id,
                service_code=service_code,
            )
            rate = defaults['rate']

            if not service_code:
                invalid_appointments.append(
                    _build_batch_issue(appt, client_id, 'Missing CPT/service code.')
                )
                continue

            if units <= 0:
                invalid_appointments.append(
                    _build_batch_issue(appt, client_id, 'Units must be greater than 0.', service_code)
                )
                continue

            if rate is None or rate <= 0:
                invalid_appointments.append(
                    _build_batch_issue(
                        appt,
                        client_id,
                        'No billing rate found from existing invoice history for this service code.',
                        service_code,
                    )
                )
                continue

            prepared_items_by_client[client_id].append({
                'appointment': appt,
                'service_code': service_code,
                'description': defaults['description'],
                'units': units,
                'rate': rate,
                'amount': units * rate,
            })

    return prepared_items_by_client, invalid_appointments


def _create_batch_invoices(prepared_items_by_client, organization, invoice_date, due_date):
    created_invoices = []

    for client_id, prepared_items in prepared_items_by_client.items():
        total = Decimal('0.00')
        invoice = Invoice.objects.create(
            organization=organization,
            client_id=client_id,
            invoice_date=invoice_date,
            due_date=due_date,
            total_amount=Decimal('0.00'),
            balance=Decimal('0.00'),
        )

        for prepared_item in prepared_items:
            InvoiceItem.objects.create(
                invoice=invoice,
                appointment=prepared_item['appointment'],
                service_code=prepared_item['service_code'],
                description=prepared_item['description'],
                units=prepared_item['units'],
                rate=prepared_item['rate'],
                amount=prepared_item['amount'],
            )
            total += prepared_item['amount']

        invoice.total_amount = total
        invoice.balance = total
        invoice.save(update_fields=['total_amount', 'balance'])
        created_invoices.append(invoice)

    return created_invoices


from apps.core.permissions import IsBiller, IsClinicalStaff
from .models import Invoice, InvoiceItem, Payment, Claim
from .serializers import (
    InvoiceSerializer,
    InvoiceCreateSerializer,
    InvoiceListSerializer,
    PaymentSerializer,
    PaymentCreateSerializer,
    ClaimSerializer,
    ClaimCreateSerializer,
    PostClaimPaymentSerializer,
    WriteOffSerializer,
    BatchInvoiceSerializer,
)
from .service_catalog import resolve_billing_defaults
from .cpt_catalog import CPTCatalog

# Statuses that block financial operations
BLOCKED_STATUSES = ('cancelled', 'voided', 'void')


class InvoiceViewSet(viewsets.ModelViewSet):
    """
    Invoice CRUD.

    GET    /api/v1/invoices/         → BillingPage list
    POST   /api/v1/invoices/         → Create invoice with line items
    GET    /api/v1/invoices/{id}/    → InvoiceDetailPage
    POST   /api/v1/invoices/batch/   → Batch generate invoices
    """
    permission_classes = [IsAuthenticated, IsBiller]
    search_fields = ['invoice_number', 'client__first_name', 'client__last_name']
    ordering_fields = ['invoice_date', 'total_amount', 'created_at']

    def get_queryset(self):
        qs = Invoice.objects.filter(
            organization=self.request.user.organization
        ).select_related('client').prefetch_related('items', 'payments')

        # Frontend filters: status, client_id, start_date, end_date
        inv_status = self.request.query_params.get('status')
        client_id = self.request.query_params.get('client_id')
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')

        if inv_status:
            qs = qs.filter(status=inv_status)
        if client_id:
            qs = qs.filter(client_id=client_id)
        if start_date:
            qs = qs.filter(invoice_date__gte=start_date)
        if end_date:
            qs = qs.filter(invoice_date__lte=end_date)

        return qs

    def get_serializer_class(self):
        if self.action == 'list':
            return InvoiceListSerializer
        if self.action == 'create':
            return InvoiceCreateSerializer
        return InvoiceSerializer

    def perform_create(self, serializer):
        serializer.save(organization=self.request.user.organization)

    @action(detail=False, methods=['post'], url_path='batch')
    def batch_generate(self, request):
        """
        POST /api/v1/invoices/batch/ — batch generate invoices.

        Creates an invoice for each client with attended appointments
        in the given date range.

        FIX #1:  Wrapped in transaction.atomic() — all or nothing
        FIX #2:  Uses appointment rate field instead of hardcoded 0
        FIX #13: Checks for duplicate invoices before creating
        """
        serializer = BatchInvoiceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from apps.scheduling.models import Appointment

        start = serializer.validated_data['start_date']
        end = serializer.validated_data['end_date']
        client_ids = serializer.validated_data.get('client_ids')
        invoice_date = timezone.now().date()
        due_date = invoice_date + timedelta(days=14)

        # Find attended appointments in the date range
        appointments = Appointment.objects.filter(
            organization=request.organization,
            status='attended',
            start_time__date__gte=start,
            start_time__date__lte=end,
        ).select_related('client', 'provider')

        if client_ids:
            appointments = appointments.filter(client_id__in=client_ids)

        # FIX #13: Exclude appointments that already have an invoice item
        # This prevents duplicate invoices when batch is run twice
        appointments = appointments.exclude(
            id__in=InvoiceItem.objects.filter(
                appointment__isnull=False
            ).values_list('appointment_id', flat=True)
        )

        # Group by client
        client_appts = defaultdict(list)
        for appt in appointments:
            client_appts[appt.client_id].append(appt)

        if not client_appts:
            return Response({
                'created': 0,
                'invoices': [],
                'message': 'No uninvoiced attended appointments found in this date range.',
            })

        prepared_items_by_client, invalid_appointments = _prepare_batch_invoice_items(
            client_appts=client_appts,
            organization_id=request.organization.id,
        )

        if invalid_appointments:
            raise ValidationError({
                'message': 'Batch generation blocked. Fix missing CPT codes, units, or billing rates before retrying.',
                'invalid_appointments': invalid_appointments,
            })

        # FIX #1: Wrap entire batch in a transaction — all or nothing
        try:
            with transaction.atomic():
                created_invoices = _create_batch_invoices(
                    prepared_items_by_client=prepared_items_by_client,
                    organization=request.organization,
                    invoice_date=invoice_date,
                    due_date=due_date,
                )

        except Exception as e:
            logger.error(f'Batch invoice generation failed: {e}', exc_info=True)
            return Response(
                {'error': 'Batch generation failed. Please try again or contact support.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response({
            'created': len(created_invoices),
            'invoices': InvoiceSerializer(created_invoices, many=True).data,
        })

    @action(detail=True, methods=['post'], url_path='email',
            throttle_classes=[EmailRateThrottle])
    def email_invoice(self, request, pk=None):
        """
        POST /api/v1/invoices/{id}/email/

        Send an invoice email to the specified recipient.
        Body: { "to_email": "client@example.com" }
        Falls back to client.email if to_email is not provided.
        """
        invoice = self.get_object()

        # Block emailing cancelled/voided invoices
        if invoice.status in BLOCKED_STATUSES:
            return Response(
                {'error': f'Cannot email a {invoice.status} invoice.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        to_email = (
            request.data.get('to_email', '').strip()
            or getattr(invoice.client, 'email', '')
            or ''
        )

        if not to_email:
            return Response(
                {'error': 'No recipient email provided and client has no email on file.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.core.email import EmailService

        try:
            EmailService.send_invoice_email(invoice, to_email=to_email, org_name=org_name)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Exception as e:
            return Response(
                {'detail': f'Failed to write off claim: {str(e)}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=['get'], url_path='download-pdf',
            throttle_classes=[EmailRateThrottle])  # Reuse 10/min limit
    def download_pdf(self, request, pk=None):
        """
        GET /api/v1/invoices/{id}/download-pdf/

        Generate and return a PDF for this invoice.
        """
        from django.http import HttpResponse
        from .pdf import generate_invoice_pdf

        invoice = self.get_object()
        pdf_bytes = generate_invoice_pdf(
            invoice,
            organization=request.organization,
        )
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = (
            f'attachment; filename="invoice_{invoice.invoice_number}.pdf"'
        )
        return response

class PaymentViewSet(viewsets.ModelViewSet):
    """
    Payment CRUD.

    POST /api/v1/payments/        → Record payment against invoice
    POST /api/v1/payments/stripe/  → Create Stripe payment intent
    """
    permission_classes = [IsAuthenticated, IsBiller]

    def get_queryset(self):
        qs = Payment.objects.filter(
            invoice__organization=self.request.user.organization
        ).select_related('invoice', 'client', 'claim')

        # Frontend filter: invoice_id
        invoice_id = self.request.query_params.get('invoice_id')
        if invoice_id:
            qs = qs.filter(invoice_id=invoice_id)

        # Frontend filter: client_id (used by ClientDetailPage billing tab)
        client_id = self.request.query_params.get('client_id')
        if client_id:
            qs = qs.filter(invoice__client_id=client_id)

        return qs

    def get_serializer_class(self):
        if self.action == 'create':
            return PaymentCreateSerializer
        return PaymentSerializer

    def perform_create(self, serializer):
        """
        Record a payment against an invoice.

        FIX #3:  Validates payment amount doesn't exceed invoice balance (overpayment guard)
        FIX #4:  Blocks payments on cancelled/voided invoices
        FIX CT-4: Validates invoice belongs to user's organization
        """
        from apps.billing.models import Invoice

        invoice_id = serializer.validated_data.get('invoice_id')
        try:
            invoice = Invoice.objects.get(pk=invoice_id)
        except Invoice.DoesNotExist:
            raise ValidationError({'invoice_id': 'Invoice not found.'})

        # FIX CT-4: Cross-tenant isolation — verify invoice belongs to this org
        if invoice.organization_id != self.request.user.organization.id:
            raise ValidationError(
                {'invoice_id': 'Invoice does not belong to your organization.'}
            )

        # FIX #4: Block payment on cancelled/voided invoices
        if invoice.status in BLOCKED_STATUSES:
            raise ValidationError(
                {'invoice_id': f'Cannot record payment on a {invoice.status} invoice.'}
            )

        # FIX #3: Overpayment guard — re-read balance from DB to avoid stale data
        invoice.refresh_from_db(fields=['balance', 'status'])
        payment_amount = serializer.validated_data.get('amount', Decimal('0'))

        if payment_amount > invoice.balance:
            raise ValidationError(
                {'amount': f'Payment of ${payment_amount} exceeds invoice balance of ${invoice.balance}.'}
            )

        payment = serializer.save(invoice=invoice, client=invoice.client)
        # Recalculate invoice balance after payment
        payment.invoice.recalculate_balance()
        payment = Payment.objects.select_related(
            'invoice', 'client', 'invoice__organization'
        ).get(pk=payment.pk)
        try:
            from apps.notifications.services import notify_payment_recorded
            notify_payment_recorded(payment)
        except Exception:
            pass
        try:
            from apps.core.email import EmailService
            org_name = payment.invoice.organization.name if payment.invoice and payment.invoice.organization else 'Sirena Health'
            EmailService.send_payment_receipt(payment, org_name=org_name)
        except Exception:
            logger.exception('Payment receipt email failed')



class ClaimViewSet(viewsets.ModelViewSet):
    """
    Claim CRUD + submit + post-payment + write-off.

    GET/POST    /api/v1/claims/                   → list/create
    POST        /api/v1/claims/{id}/submit/       → submit to payer
    POST        /api/v1/claims/{id}/post-payment/ → post insurance/patient payment
    POST        /api/v1/claims/{id}/write-off/    → write off balance
    """
    permission_classes = [IsAuthenticated, IsBiller]
    search_fields = ['claim_number', 'payer_name']
    ordering_fields = ['created_at', 'submitted_at']

    def get_queryset(self):
        qs = Claim.objects.filter(
            invoice__organization=self.request.user.organization
        ).select_related('invoice', 'client')

        # Frontend filters: status, payer_name, start_date, end_date
        claim_status = self.request.query_params.get('status')
        payer_name = self.request.query_params.get('payer_name')
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')

        if claim_status:
            qs = qs.filter(status=claim_status)
        if payer_name:
            qs = qs.filter(payer_name__icontains=payer_name)
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)

        return qs

    def get_serializer_class(self):
        if self.action == 'create':
            return ClaimCreateSerializer
        return ClaimSerializer

    def perform_update(self, serializer):
        """Detect status changes and trigger notifications."""
        claim = self.get_object()
        old_status = claim.status
        instance = serializer.save()

        # Auto-notify on denial
        if instance.status == 'denied' and old_status != 'denied':
            try:
                from apps.notifications.services import notify_claim_denied
                notify_claim_denied(instance)
            except Exception:
                pass  # Never break main flow for notifications

    @action(detail=True, methods=['post'], url_path='submit')
    def submit(self, request, pk=None):
        """POST /api/v1/claims/{id}/submit/ — mark as submitted."""
        claim = self.get_object()
        if claim.status not in ('created', 'denied'):
            return Response(
                {'error': True, 'message': 'Claim cannot be submitted from current status'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if claim.status == 'denied':
            claim.resubmission_count += 1
            claim.status = 'resubmitted'
            notes = request.data.get('resubmission_notes', '')
            if notes:
                claim.resubmission_notes = notes
        else:
            claim.status = 'submitted'

        claim.submitted_at = timezone.now()
        claim.save(update_fields=[
            'status', 'submitted_at', 'resubmission_count',
            'resubmission_notes', 'updated_at',
        ])
        return Response(ClaimSerializer(claim).data)

    @action(detail=True, methods=['post'], url_path='post-payment')
    def post_payment(self, request, pk=None):
        """
        POST /api/v1/claims/{id}/post-payment/ — post insurance/patient payment.

        FIX #5: Uses F() expressions for atomic increment to prevent race conditions.
                Two concurrent requests both incrementing insurance_paid will now
                correctly add both amounts instead of overwriting each other.
        """
        claim = self.get_object()

        # Block posting payment on paid claims
        if claim.status == 'paid':
            return Response(
                {'error': True, 'message': 'Claim is already fully paid.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = PostClaimPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data

        # FIX #5: Use F() expressions for atomic increment — race-condition safe
        payment = None
        with transaction.atomic():
            # Atomic update using F() — DB does the math, not Python
            Claim.objects.filter(pk=claim.pk).update(
                insurance_paid=F('insurance_paid') + data['insurance_paid'],
                patient_responsibility=data['patient_responsibility'],
                write_off_amount=F('write_off_amount') + data['write_off_amount'],
            )

            # Re-read the claim to get updated values
            claim.refresh_from_db()

            # Auto-set status to paid if fully covered
            total_applied = claim.insurance_paid + claim.write_off_amount
            if total_applied >= claim.billed_amount:
                claim.status = 'paid'
                claim.paid_at = timezone.now()
                claim.save(update_fields=['status', 'paid_at', 'updated_at'])

            # Create a Payment record for the invoice
            if data['insurance_paid'] > 0:
                payment = Payment.objects.create(
                    invoice=claim.invoice,
                    claim=claim,
                    client=claim.client,
                    amount=data['insurance_paid'],
                    payment_type='payment',
                    payer_type='insurance',
                    reference_number=data.get('reference_number', ''),
                    notes=data.get('notes', ''),
                )
                claim.invoice.recalculate_balance()

        if payment:
            try:
                from apps.notifications.services import notify_payment_recorded
                notify_payment_recorded(payment)
            except Exception:
                pass

        return Response(ClaimSerializer(claim).data)

    @action(detail=True, methods=['post'], url_path='write-off')
    def write_off(self, request, pk=None):
        """
        POST /api/v1/claims/{id}/write-off/ — write off remaining balance.

        FIX #9: Validates that write-off doesn't exceed remaining balance.
        """
        claim = self.get_object()

        # FIX #9: Block write-off on fully paid claims
        if claim.status == 'paid':
            return Response(
                {'error': True, 'message': 'Claim is already fully paid. Nothing to write off.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = WriteOffSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data

        # FIX #9: Check if write-off amount exceeds remaining balance
        remaining = claim.remaining_balance
        if data['amount'] > remaining:
            return Response(
                {
                    'error': True,
                    'message': f'Write-off amount ${data["amount"]} exceeds remaining '
                               f'balance of ${remaining}.',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            # Atomic increment for race-condition safety
            Claim.objects.filter(pk=claim.pk).update(
                write_off_amount=F('write_off_amount') + data['amount'],
            )
            claim.refresh_from_db()

            # Create a write-off Payment record
            Payment.objects.create(
                invoice=claim.invoice,
                claim=claim,
                client=claim.client,
                amount=data['amount'],
                payment_type='write_off',
                reference_number=f"WO: {data['reason']}",
                notes=data.get('notes', ''),
            )
            claim.invoice.recalculate_balance()

        return Response(ClaimSerializer(claim).data)


class ClientClaimsView(generics.ListAPIView):
    """
    GET /api/v1/clients/{id}/claims/ — client-scoped claims list.

    Triggered by ClientDetailPage → Billing tab → Claims section.
    """
    permission_classes = [IsAuthenticated, IsClinicalStaff]
    serializer_class = ClaimSerializer
    pagination_class = None  # Frontend expects array, not paginated

    def get_queryset(self):
        return Claim.objects.filter(
            client_id=self.kwargs['client_id'],
            client__organization=self.request.user.organization,
        ).select_related('invoice')


class CPTSuggestionView(APIView):
    """
    BUILD 6.1: Auto-suggest CPT codes based on specialty, duration, and type.
    
    GET /api/v1/billing/cpt-suggestions/
    Query params:
        - specialty: Provider specialty (e.g., 'aba', 'psychiatry')
        - duration: Appointment duration in minutes
        - service_type: Type of service (e.g., 'evaluation', 'individual_therapy')
        - is_initial: Whether this is an initial visit (true/false)
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        """Get CPT code suggestions based on criteria."""
        specialty = request.query_params.get('specialty')
        duration = request.query_params.get('duration')
        service_type = request.query_params.get('service_type')
        is_initial = request.query_params.get('is_initial', 'false').lower() == 'true'
        
        # Convert duration to int if provided
        duration_minutes = None
        if duration:
            try:
                duration_minutes = int(duration)
            except ValueError:
                return Response(
                    {'detail': 'Duration must be a number'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # Get suggestions
        suggestions = CPTCatalog.suggest_codes(
            specialty=specialty,
            duration_minutes=duration_minutes,
            service_type=service_type,
            is_initial=is_initial
        )
        
        # Remove internal score field from response
        for suggestion in suggestions:
            suggestion.pop('score', None)
        
        return Response(suggestions)


class ModifierSuggestionView(APIView):
    """
    BUILD 6.2: Auto-suggest CPT modifiers based on service type and delivery method.
    
    GET /api/v1/billing/modifier-suggestions/
    Query params:
        - code: CPT code
        - is_telehealth: Whether service is via telehealth (true/false)
        - telehealth_type: Type of telehealth (default, audio_only, store_forward, cms)
        - provider_type: Provider specialty (ot, pt, st)
        - is_assistant: Whether provider is an assistant (true/false)
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        """Get modifier suggestions based on criteria."""
        code = request.query_params.get('code', '')
        is_telehealth = request.query_params.get('is_telehealth', 'false').lower() == 'true'
        telehealth_type = request.query_params.get('telehealth_type', 'default')
        provider_type = request.query_params.get('provider_type')
        is_assistant = request.query_params.get('is_assistant', 'false').lower() == 'true'
        
        if not code:
            return Response(
                {'detail': 'CPT code is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        modifiers = CPTCatalog.suggest_modifiers(
            code=code,
            is_telehealth=is_telehealth,
            telehealth_type=telehealth_type,
            provider_type=provider_type,
            is_assistant=is_assistant
        )
        
        return Response({'modifiers': modifiers})
