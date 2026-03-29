"""
Billing models: Invoice, InvoiceItem, Payment, Claim.

Matches backend.md §3 Billing tables (including claim-level payment fields).
Coordinated with frontend types/billing.ts.
"""
import uuid
from django.db import models
from django.utils import timezone
from apps.core.models import OrganizationModel, BaseModel


class Invoice(OrganizationModel):
    """Invoice for billed services."""
    client = models.ForeignKey(
        'clients.Client',
        on_delete=models.PROTECT,  # FIX CD-1: Prevent accidental loss of billing records
        related_name='invoices',
    )
    invoice_number = models.CharField(max_length=100, unique=True)
    invoice_date = models.DateField()
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    balance = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=50, default='pending')
    due_date = models.DateField(null=True, blank=True)

    class Meta(OrganizationModel.Meta):
        ordering = ['-invoice_date']
        indexes = [
            models.Index(fields=['client']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"Invoice #{self.invoice_number} — {self.client}"

    @staticmethod
    def generate_invoice_number():
        while True:
            candidate = f"INV-{timezone.now():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"
            if not Invoice.objects.filter(invoice_number=candidate).exists():
                return candidate

    def save(self, *args, **kwargs):
        if not self.invoice_number:
            self.invoice_number = self.generate_invoice_number()
        super().save(*args, **kwargs)

    def recalculate_balance(self):
        """Recalculate balance from payments."""
        total_paid = self.payments.aggregate(
            total=models.Sum('amount')
        )['total'] or 0
        self.paid_amount = total_paid
        self.balance = self.total_amount - total_paid
        if self.balance <= 0:
            self.status = 'paid'
        self.save(update_fields=['paid_amount', 'balance', 'status', 'updated_at'])


class InvoiceItem(BaseModel):
    """Individual line item on an invoice."""
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name='items',
    )
    appointment = models.ForeignKey(
        'scheduling.Appointment',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    service_code = models.CharField(max_length=50)
    description = models.TextField(blank=True, default='')
    units = models.DecimalField(max_digits=5, decimal_places=2)
    rate = models.DecimalField(max_digits=10, decimal_places=2)
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta(BaseModel.Meta):
        ordering = ['created_at']

    def __str__(self):
        return f"{self.service_code} — ${self.amount}"


class Payment(BaseModel):
    """
    Payment against an invoice or claim.

    Supports payment types: payment, write_off, adjustment
    Supports payer types: insurance, patient
    """
    PAYMENT_TYPE_CHOICES = [
        ('payment', 'Payment'),
        ('write_off', 'Write-off'),
        ('adjustment', 'Adjustment'),
    ]
    PAYER_TYPE_CHOICES = [
        ('insurance', 'Insurance'),
        ('patient', 'Patient'),
    ]

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name='payments',
    )
    claim = models.ForeignKey(
        'Claim',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='payments',
    )
    client = models.ForeignKey(
        'clients.Client',
        on_delete=models.CASCADE,
        related_name='payments',
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_type = models.CharField(
        max_length=50, choices=PAYMENT_TYPE_CHOICES, default='payment'
    )
    payer_type = models.CharField(
        max_length=50, choices=PAYER_TYPE_CHOICES, blank=True, default=''
    )
    payment_method = models.CharField(max_length=50, blank=True, default='')
    stripe_payment_id = models.CharField(max_length=255, blank=True, default='')
    payment_date = models.DateTimeField(auto_now_add=True)
    reference_number = models.CharField(max_length=100, blank=True, default='')
    notes = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-payment_date']

    def __str__(self):
        return f"${self.amount} — {self.payment_type} ({self.payer_type})"


class Claim(BaseModel):
    """
    Insurance claim tied to an invoice.

    Includes claim-level payment tracking fields for the client profile view.
    """
    STATUS_CHOICES = [
        ('created', 'Created'),
        ('submitted', 'Submitted'),
        ('accepted', 'Accepted'),
        ('paid', 'Paid'),
        ('denied', 'Denied'),
        ('resubmitted', 'Resubmitted'),
    ]

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name='claims',
    )
    client = models.ForeignKey(
        'clients.Client',
        on_delete=models.CASCADE,
        related_name='claims',
    )
    claim_number = models.CharField(max_length=100, blank=True, default='')
    payer_name = models.CharField(max_length=255)
    payer_id = models.CharField(max_length=100, blank=True, default='')
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='created'
    )

    # Claim-level payment tracking
    billed_amount = models.DecimalField(max_digits=10, decimal_places=2)
    allowed_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    insurance_paid = models.DecimalField(
        max_digits=10, decimal_places=2, default=0
    )
    patient_responsibility = models.DecimalField(
        max_digits=10, decimal_places=2, default=0
    )
    write_off_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0
    )

    # Submission details
    submitted_at = models.DateTimeField(null=True, blank=True)
    response_data = models.JSONField(null=True, blank=True)
    denial_reason = models.TextField(blank=True, default='')
    resubmission_count = models.IntegerField(default=0)
    resubmission_notes = models.TextField(blank=True, default='')
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['client']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"Claim #{self.claim_number} — {self.payer_name} ({self.status})"

    @property
    def remaining_balance(self):
        return self.billed_amount - self.insurance_paid - self.write_off_amount
