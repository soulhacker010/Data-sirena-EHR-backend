"""
Invoice PDF generation service using ReportLab.

Generates a professional CMS-1500 style invoice PDF with:
- Practice/organization header
- Client billing info
- Line items (CPT code, description, units, rate, amount)
- Payment history
- Totals section
"""
import io
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable,
)
from reportlab.lib.enums import TA_RIGHT, TA_CENTER


# Brand colors matching frontend theme
BRAND_PRIMARY = colors.HexColor('#0D9488')
BRAND_DARK = colors.HexColor('#0F172A')
BRAND_MUTED = colors.HexColor('#64748B')
BRAND_LIGHT = colors.HexColor('#F1F5F9')
BRAND_BORDER = colors.HexColor('#E2E8F0')


def generate_invoice_pdf(invoice, organization=None):
    """
    Generate a PDF for the given Invoice model instance.

    Args:
        invoice: Invoice model instance with prefetched items, payments, client
        organization: Organization model instance for practice header

    Returns:
        bytes — the PDF file content
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )

    styles = getSampleStyleSheet()

    # Custom styles
    styles.add(ParagraphStyle(
        'BrandTitle',
        parent=styles['Title'],
        fontSize=22,
        textColor=BRAND_PRIMARY,
        spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        'SectionHeader',
        parent=styles['Heading2'],
        fontSize=12,
        textColor=BRAND_DARK,
        spaceBefore=16,
        spaceAfter=8,
        borderPadding=(0, 0, 4, 0),
    ))
    styles.add(ParagraphStyle(
        'InfoLabel',
        parent=styles['Normal'],
        fontSize=9,
        textColor=BRAND_MUTED,
    ))
    styles.add(ParagraphStyle(
        'InfoValue',
        parent=styles['Normal'],
        fontSize=10,
        textColor=BRAND_DARK,
    ))
    styles.add(ParagraphStyle(
        'RightAligned',
        parent=styles['Normal'],
        alignment=TA_RIGHT,
        fontSize=10,
    ))

    elements = []

    # ── Header: Practice Info + Invoice Meta ──
    org_name = organization.name if organization else 'Sirena Health'
    org_address = organization.address if organization else ''
    org_email = organization.contact_email if organization else ''
    org_phone = organization.contact_phone if organization else ''

    header_left = []
    header_left.append(Paragraph(org_name, styles['BrandTitle']))
    if org_address:
        header_left.append(Paragraph(org_address, styles['InfoLabel']))
    if org_email:
        header_left.append(Paragraph(org_email, styles['InfoLabel']))
    if org_phone:
        header_left.append(Paragraph(org_phone, styles['InfoLabel']))

    status_color = {
        'paid': '#10B981',
        'partial': '#F59E0B',
        'overdue': '#EF4444',
        'pending': '#64748B',
    }.get(invoice.status, '#64748B')

    header_right = []
    header_right.append(Paragraph('INVOICE', ParagraphStyle(
        'InvoiceLabel', parent=styles['Normal'],
        fontSize=28, textColor=colors.HexColor('#CBD5E1'),
        alignment=TA_RIGHT,
    )))
    header_right.append(Paragraph(
        f'#{invoice.invoice_number}', ParagraphStyle(
            'InvoiceNum', parent=styles['Normal'],
            fontSize=12, alignment=TA_RIGHT, textColor=BRAND_DARK,
        )
    ))
    header_right.append(Paragraph(
        f'Status: {invoice.status.upper()}', ParagraphStyle(
            'InvoiceStatus', parent=styles['Normal'],
            fontSize=10, alignment=TA_RIGHT,
            textColor=colors.HexColor(status_color),
        )
    ))

    header_table = Table(
        [[header_left, header_right]],
        colWidths=[3.5 * inch, 3.5 * inch],
    )
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 16))
    elements.append(HRFlowable(
        width='100%', thickness=1, color=BRAND_BORDER,
        spaceAfter=16,
    ))

    # ── Bill To + Invoice Details ──
    client = invoice.client
    bill_to = [
        Paragraph('BILL TO', styles['InfoLabel']),
        Paragraph(f'{client.first_name} {client.last_name}', styles['InfoValue']),
    ]
    if client.address:
        bill_to.append(Paragraph(client.address, styles['InfoLabel']))
    if client.email:
        bill_to.append(Paragraph(client.email, styles['InfoLabel']))
    if client.phone:
        bill_to.append(Paragraph(client.phone, styles['InfoLabel']))
    # Insurance info
    if client.insurance_primary_name:
        bill_to.append(Spacer(1, 6))
        bill_to.append(Paragraph(
            f'Insurance: {client.insurance_primary_name}', styles['InfoLabel'],
        ))
        if client.insurance_primary_id:
            bill_to.append(Paragraph(
                f'Member ID: {client.insurance_primary_id}', styles['InfoLabel'],
            ))

    invoice_details = [
        Paragraph('INVOICE DETAILS', styles['InfoLabel']),
        Paragraph(f'Date: {invoice.invoice_date.strftime("%B %d, %Y")}', styles['InfoValue']),
    ]
    if invoice.due_date:
        invoice_details.append(Paragraph(
            f'Due: {invoice.due_date.strftime("%B %d, %Y")}', styles['InfoValue'],
        ))

    details_table = Table(
        [[bill_to, invoice_details]],
        colWidths=[3.5 * inch, 3.5 * inch],
    )
    details_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    elements.append(details_table)
    elements.append(Spacer(1, 20))

    # ── Line Items Table ──
    elements.append(Paragraph('Services', styles['SectionHeader']))

    items = list(invoice.items.all())
    item_data = [['CPT Code', 'Description', 'Units', 'Rate', 'Amount']]
    for item in items:
        item_data.append([
            item.service_code,
            item.description or '—',
            f'{item.units:.1f}',
            f'${item.rate:.2f}',
            f'${item.amount:.2f}',
        ])

    item_table = Table(
        item_data,
        colWidths=[1.0 * inch, 2.8 * inch, 0.8 * inch, 1.0 * inch, 1.0 * inch],
        repeatRows=1,
    )
    item_table.setStyle(TableStyle([
        # Header row
        ('BACKGROUND', (0, 0), (-1, 0), BRAND_PRIMARY),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('TOPPADDING', (0, 0), (-1, 0), 8),
        # Data rows
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
        ('TOPPADDING', (0, 1), (-1, -1), 6),
        # Alternating row colors
        *[
            ('BACKGROUND', (0, i), (-1, i), BRAND_LIGHT)
            for i in range(2, len(item_data), 2)
        ],
        # Alignment
        ('ALIGN', (2, 0), (-1, -1), 'RIGHT'),
        # Grid
        ('LINEBELOW', (0, 0), (-1, 0), 1, BRAND_PRIMARY),
        ('LINEBELOW', (0, -1), (-1, -1), 1, BRAND_BORDER),
    ]))
    elements.append(item_table)
    elements.append(Spacer(1, 12))

    # ── Totals ──
    totals_data = [
        ['', '', 'Subtotal:', f'${invoice.total_amount:.2f}'],
        ['', '', 'Paid:', f'${invoice.paid_amount:.2f}'],
        ['', '', 'Balance Due:', f'${invoice.balance:.2f}'],
    ]
    totals_table = Table(
        totals_data,
        colWidths=[2.8 * inch, 1.8 * inch, 1.2 * inch, 1.2 * inch],
    )
    totals_table.setStyle(TableStyle([
        ('ALIGN', (2, 0), (-1, -1), 'RIGHT'),
        ('FONTNAME', (2, -1), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (2, 0), (-1, -1), 10),
        ('TEXTCOLOR', (2, -1), (-1, -1), BRAND_PRIMARY),
        ('LINEABOVE', (2, -1), (-1, -1), 1, BRAND_BORDER),
        ('TOPPADDING', (2, -1), (-1, -1), 8),
    ]))
    elements.append(totals_table)

    # ── Payment History (if any) ──
    payments = list(invoice.payments.all().order_by('-payment_date'))
    if payments:
        elements.append(Spacer(1, 20))
        elements.append(Paragraph('Payment History', styles['SectionHeader']))

        pay_data = [['Date', 'Method', 'Reference', 'Amount']]
        for pay in payments:
            pay_data.append([
                pay.payment_date.strftime('%m/%d/%Y'),
                pay.payment_method or pay.payment_type,
                pay.reference_number or '—',
                f'${pay.amount:.2f}',
            ])

        pay_table = Table(
            pay_data,
            colWidths=[1.5 * inch, 2.0 * inch, 2.0 * inch, 1.5 * inch],
            repeatRows=1,
        )
        pay_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), BRAND_LIGHT),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('ALIGN', (3, 0), (3, -1), 'RIGHT'),
            ('LINEBELOW', (0, 0), (-1, 0), 1, BRAND_BORDER),
        ]))
        elements.append(pay_table)

    # ── Footer ──
    elements.append(Spacer(1, 30))
    elements.append(HRFlowable(
        width='100%', thickness=0.5, color=BRAND_BORDER,
        spaceAfter=8,
    ))
    elements.append(Paragraph(
        f'Generated by {org_name} • Sirena Health EHR',
        ParagraphStyle(
            'Footer', parent=styles['Normal'],
            fontSize=8, textColor=BRAND_MUTED, alignment=TA_CENTER,
        ),
    ))

    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
