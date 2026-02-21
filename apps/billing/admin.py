from django.contrib import admin
from .models import Invoice, InvoiceItem, Payment, Claim


class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ['invoice_number', 'client', 'total_amount', 'paid_amount', 'balance', 'status', 'invoice_date']
    list_filter = ['status']
    search_fields = ['invoice_number', 'client__first_name', 'client__last_name']
    inlines = [InvoiceItemInline]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['amount', 'payment_type', 'payer_type', 'invoice', 'claim', 'payment_date']
    list_filter = ['payment_type', 'payer_type']


@admin.register(Claim)
class ClaimAdmin(admin.ModelAdmin):
    list_display = ['claim_number', 'payer_name', 'client', 'billed_amount', 'insurance_paid', 'write_off_amount', 'status']
    list_filter = ['status']
    search_fields = ['claim_number', 'payer_name']
