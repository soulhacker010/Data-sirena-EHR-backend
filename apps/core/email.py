"""
Centralized email service using Resend.

All email sending flows through this module. If RESEND_API_KEY is empty,
emails are logged instead of sent (dev mode).

Usage:
    from apps.core.email import EmailService

    EmailService.send_invoice_email(invoice, to_email='client@example.com')
    EmailService.send_welcome_email(user, temp_password='abc123')
    EmailService.send_payment_reminder(invoice)
    EmailService.send_generic(to, subject, html)
"""
import html as html_mod
import logging
import re
from typing import List, Optional

import resend
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# ─── Frontend Design Tokens ─────────────────────────────────────────────
# Must stay in sync with sirena-frontend/src/index.css @theme variables
PRIMARY = '#0D9488'
PRIMARY_DARK = '#0F766E'
PRIMARY_LIGHT = '#14B8A6'
CYAN = '#0891B2'
BG = '#F8FAFC'
CARD = '#FFFFFF'
SURFACE = '#F1F5F9'
SURFACE_TINT = '#ECFEFF'
SURFACE_WARM = '#FFFBEB'
BORDER = '#E2E8F0'
TEXT = '#0F172A'
TEXT_SECONDARY = '#475569'
TEXT_MUTED = '#94A3B8'
SUCCESS = '#10B981'
WARNING = '#F59E0B'
ERROR = '#EF4444'
INFO = '#3B82F6'
FONT_STACK = "'Nexa', system-ui, -apple-system, sans-serif"

# Email validation pattern
EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')


def _validate_email(email: str) -> bool:
    """Basic email format validation."""
    return bool(EMAIL_RE.match(email))


def _esc(value) -> str:
    """
    Escape a value for safe HTML rendering in email templates.

    FIX #1: Prevents XSS / HTML injection via user-provided data
    (client names, service codes, descriptions, org names).
    """
    if value is None:
        return ''
    return html_mod.escape(str(value))


def _format_from(org_name: str = 'Sirena Health') -> str:
    """
    Format the 'from' field for Resend.

    Resend requires 'Display Name <email@domain.com>' format.
    FIX #6: Strip < > from org name so it doesn't corrupt the from header.
    """
    raw_email = settings.DEFAULT_FROM_EMAIL
    # If already has display name format, return as-is
    if '<' in raw_email:
        return raw_email
    # Strip chars that break RFC 5322 display name
    safe_name = re.sub(r'[<>"\\\r\n]', '', org_name).strip() or 'Sirena Health'
    return f'{safe_name} <{raw_email}>'


def _money(value) -> str:
    """
    Safely format a decimal value as currency string.

    FIX #11: Guards against None values that would crash :.2f formatting.
    """
    try:
        return f'{float(value or 0):.2f}'
    except (TypeError, ValueError):
        return '0.00'


def _datetime_label(value) -> str:
    if not value:
        return '-'
    dt = timezone.localtime(value) if timezone.is_aware(value) else value
    return dt.strftime('%B %d, %Y at %I:%M %p').replace(' 0', ' ')


def _date_label(value) -> str:
    if not value:
        return '-'
    return value.strftime('%B %d, %Y')


def _initials(value: str) -> str:
    parts = [part[:1].upper() for part in str(value or '').split() if part[:1]]
    return ''.join(parts[:2]) or 'SH'


def _section_card(content: str, *, accent: str = BORDER, background: str = CARD) -> str:
    return f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 22px;background:{background};border:1px solid {BORDER};border-top:3px solid {accent};border-radius:18px;box-shadow:0 10px 24px rgba(15,23,42,0.04)">
        <tr>
            <td style="padding:22px 24px">{content}</td>
        </tr>
    </table>'''


def _info_row(label: str, value: str, *, emphasized: bool = False) -> str:
    value_color = TEXT if emphasized else TEXT_SECONDARY
    value_weight = '700' if emphasized else '600'
    return f'''<tr>
        <td style="padding:0 0 14px;color:{TEXT_MUTED};font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.12em">{_esc(label)}</td>
        <td style="padding:0 0 14px;text-align:right;color:{value_color};font-size:14px;font-weight:{value_weight}">{value}</td>
    </tr>'''


def _cta_button(label: str, url: str) -> str:
    return f'''<table role="presentation" cellpadding="0" cellspacing="0" style="margin:24px 0 0">
        <tr>
            <td align="center" style="border-radius:12px;background:{PRIMARY_DARK}">
                <a href="{_esc(url)}" style="display:inline-block;padding:14px 22px;border-radius:12px;background:{PRIMARY_DARK};color:{CARD};font-size:14px;font-weight:700;letter-spacing:0.01em;text-decoration:none">{_esc(label)}</a>
            </td>
        </tr>
    </table>'''


def _base_template(header_text: str, body_html: str, org_name: str = 'Sirena Health') -> str:
    """
    Base email template matching the Sirena Health frontend design.

    Uses the same teal primary (#0D9488), border (#E2E8F0), background (#F8FAFC),
    and text colors (#0F172A, #475569) as the frontend CSS.
    """
    safe_org = _esc(org_name)
    safe_header = _esc(header_text)
    frontend_url = getattr(settings, 'FRONTEND_BASE_URL', '').rstrip('/') or ''
    logo_url = f'{frontend_url}/images/EHRlogo.png' if frontend_url else ''

    # Logo image tag for the header (falls back to text initials if no URL)
    if logo_url:
        logo_cell = f'<td style="width:52px;height:52px;text-align:center;vertical-align:middle"><img src="{logo_url}" alt="{safe_org}" width="52" height="52" style="display:block;width:52px;height:52px;object-fit:contain;border:0" /></td>'
        top_badge = f'<img src="{logo_url}" alt="Sirena Health EHR" width="160" height="40" style="display:inline-block;width:160px;height:40px;object-fit:contain;border:0" />'
    else:
        org_initials = _initials(org_name)
        logo_cell = f'<td style="width:52px;height:52px;border-radius:16px;background:{SURFACE_TINT};border:1px solid rgba(13,148,136,0.18);text-align:center;color:{PRIMARY_DARK};font-size:20px;font-weight:700">{org_initials}</td>'
        top_badge = f'<span style="display:inline-block;padding:7px 12px;border-radius:999px;background:{CARD};border:1px solid rgba(13,148,136,0.12);color:{PRIMARY_DARK};font-size:11px;font-weight:700;letter-spacing:0.14em;text-transform:uppercase">Sirena Health EHR</span>'

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{safe_header}</title>
</head>
<body style="margin:0;padding:0;background-color:{BG};font-family:{FONT_STACK};-webkit-font-smoothing:antialiased;color:{TEXT}">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BG}">
        <tr>
            <td align="center" style="padding:40px 16px 28px">
                <table role="presentation" width="620" cellpadding="0" cellspacing="0" style="max-width:620px;width:100%">
                    <tr>
                        <td style="padding:0 0 20px;text-align:center">
                            {top_badge}
                        </td>
                    </tr>
                    <tr>
                        <td style="background:{CARD};padding:28px 30px;border:1px solid {BORDER};border-top:4px solid {PRIMARY};border-radius:28px 28px 0 0">
                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                                <tr>
                                    <td style="vertical-align:top">
                                        <table role="presentation" cellpadding="0" cellspacing="0">
                                            <tr>
                                                {logo_cell}
                                                <td style="width:16px"></td>
                                                <td>
                                                    <p style="margin:0 0 6px;color:{TEXT_MUTED};font-size:12px;font-weight:700;letter-spacing:0.12em;text-transform:uppercase">Care coordination</p>
                                                    <h1 style="margin:0;color:{TEXT};font-size:28px;line-height:1.15;font-weight:700;letter-spacing:-0.02em">{safe_org}</h1>
                                                    <p style="margin:8px 0 0;color:{TEXT_SECONDARY};font-size:15px;font-weight:600">{safe_header}</p>
                                                </td>
                                            </tr>
                                        </table>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <tr>
                        <td style="background:{CARD};padding:36px 34px;border:1px solid {BORDER};border-top:none;border-radius:0 0 28px 28px;box-shadow:0 18px 40px rgba(15,23,42,0.08)">
                            {body_html}
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:18px 18px 0;text-align:center">
                            <p style="margin:0;color:{TEXT_MUTED};font-size:12px;font-weight:600;line-height:1.6">
                                This message was sent by {safe_org}. If you need help, contact your organization administrator.
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>'''


class EmailService:
    """Centralized email service wrapping the Resend SDK."""

    @classmethod
    def _get_configured(cls) -> bool:
        """Initialize Resend with API key. Returns True if configured."""
        if not getattr(settings, 'RESEND_API_KEY', ''):
            logger.warning('RESEND_API_KEY not set - email will be logged only')
            return False
        resend.api_key = settings.RESEND_API_KEY
        return True

    @classmethod
    def send_generic(
        cls,
        to: List[str],
        subject: str,
        html: str,
        from_email: Optional[str] = None,
        org_name: str = 'Sirena Health',
    ):
        """
        Send a generic email via Resend.

        Args:
            to: List of recipient email addresses
            subject: Email subject line
            html: HTML body content
            from_email: Optional full sender override (e.g. 'Name <email>')
            org_name: Organization name for 'from' display name

        Raises:
            ValueError: If any recipient email address is invalid
        """
        # Validate all recipient emails
        invalid = [e for e in to if not _validate_email(e)]
        if invalid:
            raise ValueError(f'Invalid email address(es): {", ".join(invalid)}')

        if not cls._get_configured():
            # FIX #9: Don't log sensitive content (passwords etc.) in dev mode
            logger.info(f'[DEV MODE] Would send to {to}: {subject}')
            return None

        sender = from_email or _format_from(org_name)

        payload = {
            'from': sender,
            'to': to,
            'subject': subject,
            'html': html,
        }
        reply_to = getattr(settings, 'RESEND_REPLY_TO', '')
        if reply_to:
            payload['reply_to'] = reply_to

        try:
            response = resend.Emails.send(payload)
            # Resend v2 returns a SendResponse object with .id attribute
            email_id = getattr(response, 'id', None) or str(response)
            logger.info(f'Email sent to {to}: {subject} (id={email_id})')
            return response
        except Exception as e:
            logger.error(f'Failed to send email to {to}: {e}', exc_info=True)
            raise

    # ─── Invoice Email ───────────────────────────────────────────────────

    @classmethod
    def send_invoice_email(cls, invoice, to_email: str, org_name: str = 'Sirena Health'):
        """
        Send an invoice email to a client.

        Args:
            invoice: Invoice model instance (queryset should have prefetched items)
            to_email: Recipient email
            org_name: Organization name for branding
        """
        items = invoice.items.all()

        # FIX #10: Handle empty items table gracefully
        if items.exists():
            items_rows = ''.join(
                f'''<tr>
                    <td style="padding:10px 12px;border-bottom:1px solid {BORDER};color:{TEXT};font-size:13px">{_esc(item.service_code)}</td>
                    <td style="padding:10px 12px;border-bottom:1px solid {BORDER};color:{TEXT_SECONDARY};font-size:13px">{_esc(item.description) or '-'}</td>
                    <td style="padding:10px 12px;border-bottom:1px solid {BORDER};text-align:center;color:{TEXT};font-size:13px">{_esc(item.units)}</td>
                    <td style="padding:10px 12px;border-bottom:1px solid {BORDER};text-align:right;color:{TEXT};font-size:13px">${_money(item.rate)}</td>
                    <td style="padding:10px 12px;border-bottom:1px solid {BORDER};text-align:right;color:{TEXT};font-size:13px;font-weight:600">${_money(item.amount)}</td>
                </tr>'''
                for item in items
            )
        else:
            items_rows = f'''<tr>
                <td colspan="5" style="padding:20px 12px;text-align:center;color:{TEXT_MUTED};font-size:13px;font-style:italic">
                    No line items on this invoice
                </td>
            </tr>'''

        due_date_str = invoice.due_date.strftime('%B %d, %Y') if invoice.due_date else 'Upon Receipt'
        invoice_date_str = invoice.invoice_date.strftime('%B %d, %Y') if invoice.invoice_date else '-'
        client_name = _esc(invoice.client.full_name) if invoice.client else 'Valued Client'
        safe_org = _esc(org_name)
        safe_inv_num = _esc(invoice.invoice_number)

        invoice_summary = _section_card(
            f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                {_info_row('Invoice number', f'#{safe_inv_num}', emphasized=True)}
                {_info_row('Invoice date', invoice_date_str)}
                {_info_row('Due date', due_date_str)}
                {_info_row('Client', client_name)}
            </table>''',
            accent=PRIMARY,
            background=SURFACE,
        )
        totals_summary = _section_card(
            f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                {_info_row('Total', f'${_money(invoice.total_amount)}', emphasized=True)}
                {_info_row('Paid', f'${_money(invoice.paid_amount)}')}
                <tr>
                    <td style="padding:16px 0 0;color:{PRIMARY_DARK};font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:0.12em;border-top:1px solid {BORDER}">Balance due</td>
                    <td style="padding:16px 0 0;text-align:right;color:{PRIMARY_DARK};font-size:26px;font-weight:700;border-top:1px solid {BORDER}">${_money(invoice.balance)}</td>
                </tr>
            </table>''',
            accent=CYAN,
            background=SURFACE_TINT,
        )

        body = f'''
            <p style="margin:0 0 6px;color:{TEXT_MUTED};font-size:11px;font-weight:700;letter-spacing:0.14em;text-transform:uppercase">Billing summary</p>
            <p style="margin:0 0 8px;color:{TEXT};font-size:28px;line-height:1.15;font-weight:700;letter-spacing:-0.02em">Invoice #{safe_inv_num}</p>
            <p style="margin:0 0 24px;color:{TEXT_SECONDARY};font-size:15px;line-height:1.7;font-weight:600">Hello {client_name}, here is your invoice summary from {safe_org}. The details below are organized for quick review.</p>
            {invoice_summary}
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 20px;border:1px solid {BORDER};border-radius:16px;overflow:hidden;background:{CARD}">
                <tr style="background:{BG}">
                    <th style="padding:14px 14px;text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:0.12em;color:{TEXT_MUTED};font-weight:700;border-bottom:1px solid {BORDER}">Code</th>
                    <th style="padding:14px 14px;text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:0.12em;color:{TEXT_MUTED};font-weight:700;border-bottom:1px solid {BORDER}">Description</th>
                    <th style="padding:14px 14px;text-align:center;font-size:11px;text-transform:uppercase;letter-spacing:0.12em;color:{TEXT_MUTED};font-weight:700;border-bottom:1px solid {BORDER}">Units</th>
                    <th style="padding:14px 14px;text-align:right;font-size:11px;text-transform:uppercase;letter-spacing:0.12em;color:{TEXT_MUTED};font-weight:700;border-bottom:1px solid {BORDER}">Rate</th>
                    <th style="padding:14px 14px;text-align:right;font-size:11px;text-transform:uppercase;letter-spacing:0.12em;color:{TEXT_MUTED};font-weight:700;border-bottom:1px solid {BORDER}">Amount</th>
                </tr>
                {items_rows}
            </table>
            {totals_summary}
            <p style="margin:0;color:{TEXT_SECONDARY};font-size:14px;line-height:1.7;font-weight:600">If you have any questions about this invoice, please reach out to your care team and they can help you review the details.</p>
            <p style="margin:18px 0 0;color:{TEXT_MUTED};font-size:13px;line-height:1.7;font-weight:600">Thank you,<br><span style="color:{TEXT};font-weight:700">{safe_org}</span>
            </p>
        '''

        return cls.send_generic(
            to=[to_email],
            subject=f'Invoice #{invoice.invoice_number} from {org_name}',
            html=_base_template('Invoice', body, org_name),
            org_name=org_name,
        )

    # ─── Welcome Email ───────────────────────────────────────────────────

    @classmethod
    def send_welcome_email(cls, user, temp_password: Optional[str] = None):
        """
        Send a welcome email to a newly created user.

        Args:
            user: User model instance
            temp_password: Temporary password (if applicable)
        """
        if not user.email:
            logger.warning(f'User {user} has no email - skipping welcome email')
            return None

        org_name = user.organization.name if getattr(user, 'organization', None) else 'Sirena Health'
        safe_org = _esc(org_name)
        safe_first = _esc(user.first_name)
        safe_email = _esc(user.email)
        workspace_url = getattr(settings, 'FRONTEND_BASE_URL', '').rstrip('/') or '#'

        password_section = ''
        if temp_password:
            # FIX #9: Only show masked password in logs, full in email
            logger.info(f'Welcome email to {user.email} includes temp password (masked)')
            password_section = _section_card(
                f'''<p style="margin:0 0 8px;color:{WARNING};font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.12em">Temporary password</p>
                <p style="margin:0 0 10px;padding:14px 16px;border-radius:12px;background:{CARD};border:1px solid rgba(245,158,11,0.25);font-family:Consolas,Monaco,monospace;font-size:24px;line-height:1.2;color:{TEXT};font-weight:700;letter-spacing:0.08em">{_esc(temp_password)}</p>
                <p style="margin:0;color:{TEXT_SECONDARY};font-size:13px;line-height:1.6;font-weight:600">Use this once, then change it immediately after your first sign-in.</p>''',
                accent=WARNING,
                background=SURFACE_WARM,
            )

        role_display = user.get_role_display() if hasattr(user, 'get_role_display') else str(user.role)
        account_summary = _section_card(
            f'''<p style="margin:0 0 16px;color:{TEXT};font-size:16px;font-weight:700">Account details</p>
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                {_info_row('Email', safe_email, emphasized=True)}
                {_info_row('Role', _esc(role_display))}
            </table>''',
            accent=PRIMARY,
            background=SURFACE,
        )

        body = f'''
            <p style="margin:0 0 10px;color:{TEXT};font-size:28px;line-height:1.15;font-weight:700;letter-spacing:-0.02em">Welcome, {safe_first}.</p>
            <p style="margin:0 0 24px;color:{TEXT_SECONDARY};font-size:15px;line-height:1.7;font-weight:600">Your account for <span style="color:{PRIMARY_DARK};font-weight:700">{safe_org}</span> is ready. Below is everything you need to sign in and get started.</p>
            {account_summary}
            {password_section}
            {_cta_button('Open Workspace', workspace_url)}
            <p style="margin:22px 0 0;color:{TEXT_SECONDARY};font-size:14px;line-height:1.7;font-weight:600">If you need access help or your temporary password has expired, please contact your administrator.</p>
            <p style="margin:18px 0 0;color:{TEXT_MUTED};font-size:13px;line-height:1.7;font-weight:600">Best regards,<br><span style="color:{TEXT};font-weight:700">{safe_org}</span>
            </p>
        '''

        return cls.send_generic(
            to=[user.email],
            subject=f'Welcome to {org_name} - Your Account is Ready',
            html=_base_template('Welcome', body, org_name),
            org_name=org_name,
        )

    # ─── Payment Reminder ────────────────────────────────────────────────

    @classmethod
    def send_payment_reminder(cls, invoice, org_name: str = 'Sirena Health'):
        """
        Send a payment reminder for an overdue invoice.

        Args:
            invoice: Invoice model instance with client relation
            org_name: Organization name for branding
        """
        if not invoice.client or not invoice.client.email:
            logger.warning(f'Invoice {invoice} — client has no email, skipping reminder')
            return None

        client_name = _esc(invoice.client.full_name)
        safe_org = _esc(org_name)
        safe_inv_num = _esc(invoice.invoice_number)

        reminder_summary = _section_card(
            f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                {_info_row('Invoice', f'#{safe_inv_num}', emphasized=True)}
                {_info_row('Total', f'${_money(invoice.total_amount)}')}
                {_info_row('Paid', f'${_money(invoice.paid_amount)}')}
            </table>''',
            accent=PRIMARY,
            background=SURFACE,
        )
        balance_summary = _section_card(
            f'''<p style="margin:0 0 8px;color:{WARNING};font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.12em">Balance due</p>
            <p style="margin:0;color:{TEXT};font-size:32px;line-height:1.1;font-weight:700">${_money(invoice.balance)}</p>
            <p style="margin:10px 0 0;color:{TEXT_SECONDARY};font-size:13px;line-height:1.6;font-weight:600">Please review the balance and contact the clinic if you need help arranging payment.</p>''',
            accent=WARNING,
            background=SURFACE_WARM,
        )

        body = f'''
            <p style="margin:0 0 6px;color:{TEXT_MUTED};font-size:11px;font-weight:700;letter-spacing:0.14em;text-transform:uppercase">Account notice</p>
            <p style="margin:0 0 10px;color:{TEXT};font-size:28px;line-height:1.15;font-weight:700;letter-spacing:-0.02em">Payment reminder</p>
            <p style="margin:0 0 24px;color:{TEXT_SECONDARY};font-size:15px;line-height:1.7;font-weight:600">Hello {client_name}, this is a friendly reminder that invoice <span style="color:{TEXT};font-weight:700">#{safe_inv_num}</span> still has an outstanding balance.</p>
            {reminder_summary}
            {balance_summary}
            <p style="margin:0;color:{TEXT_SECONDARY};font-size:14px;line-height:1.7;font-weight:600">If you have any questions or need to arrange a payment plan, please contact {safe_org} and the team will help you.</p>
            <p style="margin:18px 0 0;color:{TEXT_MUTED};font-size:13px;line-height:1.7;font-weight:600">Thank you,<br><span style="color:{TEXT};font-weight:700">{safe_org}</span>
            </p>
        '''

        return cls.send_generic(
            to=[invoice.client.email],
            subject=f'Payment Reminder - Invoice #{invoice.invoice_number}',
            html=_base_template('Payment Reminder', body, org_name),
            org_name=org_name,
        )

    @classmethod
    def send_payment_receipt(cls, payment, org_name: str = 'Sirena Health'):
        if not getattr(payment, 'client', None) or not payment.client.email:
            logger.warning(f'Payment {payment} — client has no email, skipping receipt')
            return None

        invoice = payment.invoice
        client_name = _esc(payment.client.full_name)
        safe_org = _esc(org_name)
        safe_invoice = _esc(invoice.invoice_number if invoice else '')
        method_label = _esc(payment.payment_method or payment.payer_type or 'Recorded payment')

        receipt_summary = _section_card(
            f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                {_info_row('Invoice', f'#{safe_invoice}', emphasized=True)}
                {_info_row('Payment date', _datetime_label(payment.payment_date))}
                {_info_row('Method', method_label)}
                {_info_row('Reference', _esc(payment.reference_number) or '-')}
            </table>''',
            accent=PRIMARY,
            background=SURFACE,
        )
        balance_summary = _section_card(
            f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                {_info_row('Amount received', f'${_money(payment.amount)}', emphasized=True)}
                {_info_row('Total paid', f'${_money(invoice.paid_amount if invoice else payment.amount)}')}
                {_info_row('Remaining balance', f'${_money(invoice.balance if invoice else 0)}')}
            </table>''',
            accent=SUCCESS,
            background=SURFACE_TINT,
        )

        body = f'''
            <p style="margin:0 0 6px;color:{TEXT_MUTED};font-size:11px;font-weight:700;letter-spacing:0.14em;text-transform:uppercase">Payment receipt</p>
            <p style="margin:0 0 10px;color:{TEXT};font-size:28px;line-height:1.15;font-weight:700;letter-spacing:-0.02em">Payment received</p>
            <p style="margin:0 0 24px;color:{TEXT_SECONDARY};font-size:15px;line-height:1.7;font-weight:600">Hello {client_name}, we recorded your payment for invoice <span style="color:{TEXT};font-weight:700">#{safe_invoice}</span>. Your updated balance details are below.</p>
            {receipt_summary}
            {balance_summary}
            <p style="margin:0;color:{TEXT_SECONDARY};font-size:14px;line-height:1.7;font-weight:600">If you believe anything looks incorrect, please contact {safe_org} so the billing team can review it with you.</p>
            <p style="margin:18px 0 0;color:{TEXT_MUTED};font-size:13px;line-height:1.7;font-weight:600">Thank you,<br><span style="color:{TEXT};font-weight:700">{safe_org}</span></p>
        '''

        return cls.send_generic(
            to=[payment.client.email],
            subject=f'Payment Receipt - Invoice #{invoice.invoice_number}',
            html=_base_template('Payment Receipt', body, org_name),
            org_name=org_name,
        )

    @classmethod
    def send_appointment_email(cls, appointment, *, event: str, org_name: str = 'Sirena Health'):
        if not getattr(appointment, 'client', None) or not appointment.client.email:
            logger.warning(f'Appointment {appointment} — client has no email, skipping appointment email')
            return None

        client_name = _esc(appointment.client.full_name)
        provider_name = _esc(getattr(appointment.provider, 'full_name', '') or 'Care team')
        location_name = _esc(getattr(appointment.location, 'name', '') or 'Clinic location will be confirmed')
        safe_org = _esc(org_name)
        service_code = _esc(appointment.service_code) or 'Scheduled visit'
        status_copy = {
            'scheduled': ('Appointment Scheduled', 'Your appointment has been scheduled.'),
            'updated': ('Appointment Updated', 'Your appointment details were updated.'),
            'cancelled': ('Appointment Cancelled', 'Your appointment has been cancelled.'),
        }
        header_text, intro = status_copy.get(event, ('Appointment Update', 'Your appointment information has changed.'))

        appointment_summary = _section_card(
            f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                {_info_row('Date and time', _datetime_label(appointment.start_time), emphasized=True)}
                {_info_row('Ends', _datetime_label(appointment.end_time))}
                {_info_row('Provider', provider_name)}
                {_info_row('Location', location_name)}
                {_info_row('Service', service_code)}
            </table>''',
            accent=PRIMARY,
            background=SURFACE,
        )

        closing = 'If you have any questions, please contact your care team.'
        if event == 'cancelled':
            closing = 'If you need to reschedule, please contact your care team and they will help arrange a new time.'

        body = f'''
            <p style="margin:0 0 6px;color:{TEXT_MUTED};font-size:11px;font-weight:700;letter-spacing:0.14em;text-transform:uppercase">Schedule update</p>
            <p style="margin:0 0 10px;color:{TEXT};font-size:28px;line-height:1.15;font-weight:700;letter-spacing:-0.02em">{_esc(header_text)}</p>
            <p style="margin:0 0 24px;color:{TEXT_SECONDARY};font-size:15px;line-height:1.7;font-weight:600">Hello {client_name}, {intro} Please review the appointment details below from {safe_org}.</p>
            {appointment_summary}
            <p style="margin:0;color:{TEXT_SECONDARY};font-size:14px;line-height:1.7;font-weight:600">{closing}</p>
            <p style="margin:18px 0 0;color:{TEXT_MUTED};font-size:13px;line-height:1.7;font-weight:600">Thank you,<br><span style="color:{TEXT};font-weight:700">{safe_org}</span></p>
        '''

        return cls.send_generic(
            to=[appointment.client.email],
            subject=header_text,
            html=_base_template(header_text, body, org_name),
            org_name=org_name,
        )
