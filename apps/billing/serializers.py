"""
Billing serializers — coordinated with frontend types/billing.ts.

Frontend expects:
- Invoice: organization_id, client_id, client_name (not nested FK defaults)
- InvoiceItem: invoice_id, appointment_id, session_date, provider_name
- Payment: invoice_id, claim_id, client_id
- Claim: invoice_id, client_id, service_code, session_date
"""
from decimal import Decimal

from django.db import transaction
from rest_framework import serializers
from .models import Invoice, InvoiceItem, Payment, Claim
from .service_catalog import get_service_description


class InvoiceItemSerializer(serializers.ModelSerializer):
    """Matches frontend InvoiceItem type."""
    invoice_id = serializers.UUIDField(source='invoice.id', read_only=True)
    appointment_id = serializers.UUIDField(source='appointment.id', read_only=True, allow_null=True)
    session_date = serializers.SerializerMethodField()
    provider_name = serializers.SerializerMethodField()

    class Meta:
        model = InvoiceItem
        fields = [
            'id', 'invoice_id', 'appointment_id', 'service_code',
            'description', 'units', 'rate', 'amount',
            'session_date', 'provider_name', 'created_at',
        ]
        read_only_fields = ['id', 'invoice_id', 'created_at']

    def get_session_date(self, obj):
        if obj.appointment:
            return obj.appointment.start_time.strftime('%Y-%m-%d')
        return None

    def get_provider_name(self, obj):
        if obj.appointment and obj.appointment.provider:
            return obj.appointment.provider.full_name
        return None


class InvoiceItemCreateSerializer(serializers.ModelSerializer):
    """For creating invoice items — accepts flat fields."""
    appointment_id = serializers.UUIDField(required=False, allow_null=True)

    class Meta:
        model = InvoiceItem
        fields = [
            'appointment_id', 'service_code', 'description',
            'units', 'rate', 'amount',
        ]

    def validate_service_code(self, value):
        service_code = (value or '').strip()
        if not service_code:
            raise serializers.ValidationError('Service code is required.')
        return service_code

    def validate_units(self, value):
        if value <= 0:
            raise serializers.ValidationError('Units must be greater than 0.')
        return value

    def validate_rate(self, value):
        if value <= 0:
            raise serializers.ValidationError('Rate must be greater than $0.00.')
        return value

    def validate(self, attrs):
        attrs['description'] = (attrs.get('description') or '').strip() or get_service_description(attrs['service_code'])
        attrs['amount'] = attrs['units'] * attrs['rate']
        return attrs


class PaymentSerializer(serializers.ModelSerializer):
    """Matches frontend Payment type."""
    invoice_id = serializers.UUIDField(source='invoice.id', read_only=True)
    invoice_number = serializers.SerializerMethodField()
    claim_id = serializers.UUIDField(source='claim.id', read_only=True, allow_null=True)
    client_id = serializers.UUIDField(source='client.id', read_only=True)

    class Meta:
        model = Payment
        fields = [
            'id', 'invoice_id', 'invoice_number', 'claim_id', 'client_id', 'amount',
            'payment_type', 'payer_type', 'payment_method',
            'stripe_payment_id', 'payment_date', 'reference_number', 'notes',
        ]
        read_only_fields = ['id', 'payment_date']

    def get_invoice_number(self, obj):
        if obj.invoice:
            return obj.invoice.invoice_number or f"INV-{str(obj.invoice.id).split('-')[0].upper()}"
        return None


class PaymentCreateSerializer(serializers.ModelSerializer):
    """
    For creating payments — accepts _id fields.

    FIX #10: Validates amount >= 0.01 to block zero/negative payments.
    """
    invoice_id = serializers.UUIDField()
    claim_id = serializers.UUIDField(required=False, allow_null=True)
    amount = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal('0.01'),
    )

    class Meta:
        model = Payment
        fields = [
            'invoice_id', 'claim_id', 'amount', 'payment_type',
            'payer_type', 'payment_method', 'notes',
        ]


class InvoiceSerializer(serializers.ModelSerializer):
    """Full invoice — matches frontend Invoice type."""
    organization_id = serializers.UUIDField(source='organization.id', read_only=True)
    client_id = serializers.UUIDField(source='client.id', read_only=True)
    client_name = serializers.SerializerMethodField()
    client_email = serializers.SerializerMethodField()
    invoice_number = serializers.SerializerMethodField()
    items = InvoiceItemSerializer(many=True, read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)

    class Meta:
        model = Invoice
        fields = [
            'id', 'organization_id', 'client_id', 'client_name', 'client_email',
            'invoice_number', 'invoice_date', 'total_amount',
            'paid_amount', 'balance', 'status', 'due_date',
            'items', 'payments', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'organization_id', 'created_at', 'updated_at']

    def get_client_name(self, obj):
        return obj.client.full_name if obj.client else None

    def get_client_email(self, obj):
        return obj.client.email if obj.client else None

    def get_invoice_number(self, obj):
        return obj.invoice_number or f"INV-{str(obj.id).split('-')[0].upper()}"


class InvoiceCreateSerializer(serializers.ModelSerializer):
    """For creating invoices — accepts client_id and nested items."""
    client_id = serializers.UUIDField()
    items = InvoiceItemCreateSerializer(many=True)

    class Meta:
        model = Invoice
        fields = [
            'client_id', 'invoice_date', 'due_date', 'items',
        ]

    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError('At least one invoice item is required.')
        return items

    def create(self, validated_data):
        """
        Create invoice with nested items.

        FIX #6: Wrapped in transaction.atomic() so if any item creation
        fails, the entire invoice + items are rolled back (no orphans).
        """
        items_data = validated_data.pop('items')
        # Auto-calculate total
        total = sum(item.get('amount', Decimal('0')) for item in items_data)

        with transaction.atomic():
            invoice = Invoice.objects.create(
                **validated_data,
                total_amount=total,
                balance=total,
            )
            for item_data in items_data:
                InvoiceItem.objects.create(invoice=invoice, **item_data)

        return invoice


class InvoiceListSerializer(serializers.ModelSerializer):
    """Lightweight for list views."""
    organization_id = serializers.UUIDField(source='organization.id', read_only=True)
    client_id = serializers.UUIDField(source='client.id', read_only=True)
    client_name = serializers.SerializerMethodField()
    invoice_number = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = [
            'id', 'organization_id', 'client_id', 'client_name',
            'invoice_number', 'invoice_date', 'total_amount',
            'paid_amount', 'balance', 'status', 'due_date',
            'created_at', 'updated_at',
        ]

    def get_client_name(self, obj):
        return obj.client.full_name if obj.client else None

    def get_invoice_number(self, obj):
        return obj.invoice_number or f"INV-{str(obj.id).split('-')[0].upper()}"


class ClaimSerializer(serializers.ModelSerializer):
    """Matches frontend Claim type."""
    invoice_id = serializers.UUIDField(source='invoice.id', read_only=True)
    client_id = serializers.UUIDField(source='client.id', read_only=True)
    service_code = serializers.SerializerMethodField()
    session_date = serializers.SerializerMethodField()
    remaining_balance = serializers.ReadOnlyField()

    class Meta:
        model = Claim
        fields = [
            'id', 'invoice_id', 'client_id', 'claim_number',
            'payer_name', 'payer_id', 'status',
            'billed_amount', 'allowed_amount',
            'insurance_paid', 'patient_responsibility', 'write_off_amount',
            'remaining_balance',
            'submitted_at', 'response_data', 'denial_reason',
            'resubmission_count', 'paid_at',
            'service_code', 'session_date',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_service_code(self, obj):
        """Pull service_code from the first invoice item if available."""
        if obj.invoice and obj.invoice.items.exists():
            return obj.invoice.items.first().service_code
        return None

    def get_session_date(self, obj):
        """Pull session date from the first invoice item's appointment."""
        if obj.invoice and obj.invoice.items.exists():
            item = obj.invoice.items.select_related('appointment').first()
            if item and item.appointment:
                return item.appointment.start_time.strftime('%Y-%m-%d')
        return None


class ClaimCreateSerializer(serializers.ModelSerializer):
    """For submitting claims — matches SubmitClaimPayload."""
    invoice_id = serializers.UUIDField()

    class Meta:
        model = Claim
        fields = ['id', 'invoice_id', 'payer_name', 'payer_id']
        read_only_fields = ['id']

    def validate(self, attrs):
        invoice_id = attrs.get('invoice_id')
        request = self.context.get('request')

        try:
            invoice = Invoice.objects.get(pk=invoice_id)
        except Invoice.DoesNotExist as exc:
            raise serializers.ValidationError({'invoice_id': 'Invoice not found.'}) from exc

        if request and invoice.organization_id != request.user.organization_id:
            raise serializers.ValidationError({'invoice_id': 'Invoice does not belong to your organization.'})

        if getattr(invoice, 'status', '') == 'cancelled':
            raise serializers.ValidationError({'invoice_id': 'Cancelled invoices cannot be submitted as claims.'})

        attrs['invoice'] = invoice
        return attrs

    def create(self, validated_data):
        invoice = validated_data.pop('invoice')
        validated_data.pop('invoice_id', None)
        if not validated_data.get('payer_name'):
            raise serializers.ValidationError({'payer_name': 'Payer name is required.'})
        validated_data['client'] = invoice.client
        validated_data['invoice'] = invoice
        validated_data['billed_amount'] = invoice.total_amount
        return super().create(validated_data)


class PostClaimPaymentSerializer(serializers.Serializer):
    """For posting a payment against a claim — matches PostClaimPaymentPayload."""
    insurance_paid = serializers.DecimalField(
        max_digits=10, decimal_places=2, default=0, min_value=Decimal('0'),
    )
    patient_responsibility = serializers.DecimalField(
        max_digits=10, decimal_places=2, default=0, min_value=Decimal('0'),
    )
    write_off_amount = serializers.DecimalField(
        max_digits=10, decimal_places=2, default=0, min_value=Decimal('0'),
    )
    reference_number = serializers.CharField(required=False, default='')
    notes = serializers.CharField(required=False, default='')


class WriteOffSerializer(serializers.Serializer):
    """For writing off a claim balance — matches WriteOffPayload."""
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    reason = serializers.CharField()
    notes = serializers.CharField(required=False, default='')


class BatchInvoiceSerializer(serializers.Serializer):
    """For batch invoice generation — matches BatchInvoicePayload."""
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    client_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
    )

    def validate(self, attrs):
        if attrs['start_date'] > attrs['end_date']:
            raise serializers.ValidationError({'end_date': 'End date must be on or after start date.'})
        return attrs


class StripePaymentSerializer(serializers.Serializer):
    """For creating Stripe payment intents — matches StripePaymentPayload."""
    invoice_id = serializers.UUIDField()
    amount = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal('0.50'),
    )  # Stripe minimum is $0.50
