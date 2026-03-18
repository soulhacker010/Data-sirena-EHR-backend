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

logger = logging.getLogger(__name__)

# ─── Frontend Design Tokens ─────────────────────────────────────────────
# Must stay in sync with sirena-frontend/src/index.css @theme variables
PRIMARY = '#0D9488'
PRIMARY_DARK = '#0F766E'
PRIMARY_LIGHT = '#14B8A6'
BG = '#F8FAFC'
CARD = '#FFFFFF'
BORDER = '#E2E8F0'
TEXT = '#0F172A'
TEXT_SECONDARY = '#475569'
TEXT_MUTED = '#94A3B8'
SUCCESS = '#10B981'
WARNING = '#F59E0B'
ERROR = '#EF4444'
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


def _base_template(header_text: str, body_html: str, org_name: str = 'Sirena Health') -> str:
    """
    Base email template matching the Sirena Health frontend design.

    Uses the same teal primary (#0D9488), border (#E2E8F0), background (#F8FAFC),
    and text colors (#0F172A, #475569) as the frontend CSS.
    """
    safe_org = _esc(org_name)
    safe_header = _esc(header_text)

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{safe_header}</title>
</head>
<body style="margin:0;padding:0;background-color:{BG};font-family:{FONT_STACK};-webkit-font-smoothing:antialiased">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:{BG}">
        <tr>
            <td align="center" style="padding:32px 16px">
                <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%">

                    <!-- Header -->
                    <tr>
                        <td style="background:{PRIMARY};padding:28px 32px;border-radius:12px 12px 0 0;text-align:center">
                            <h1 style="margin:0;color:{CARD};font-size:20px;font-weight:700;letter-spacing:0.5px">{safe_org}</h1>
                            <p style="margin:6px 0 0;color:rgba(255,255,255,0.8);font-size:13px;font-weight:400">{safe_header}</p>
                        </td>
                    </tr>

                    <!-- Body -->
                    <tr>
                        <td style="background:{CARD};padding:32px;border:1px solid {BORDER};border-top:none;border-radius:0 0 12px 12px">
                            {body_html}
                        </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                        <td style="padding:20px 32px;text-align:center">
                            <p style="margin:0;color:{TEXT_MUTED};font-size:12px;font-weight:400">
                                This email was sent by {safe_org}. Please do not reply to this email.
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

        body = f'''
            <p style="margin:0 0 16px;color:{TEXT};font-size:14px">Dear {client_name},</p>
            <p style="margin:0 0 20px;color:{TEXT_SECONDARY};font-size:14px">Please find your invoice details below.</p>

            <!-- Invoice Info -->
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:20px">
                <tr>
                    <td style="padding:8px 0;color:{TEXT_MUTED};font-size:12px;text-transform:uppercase;letter-spacing:0.5px">Invoice Number</td>
                    <td style="padding:8px 0;text-align:right;color:{TEXT};font-size:14px;font-weight:600">#{safe_inv_num}</td>
                </tr>
                <tr>
                    <td style="padding:8px 0;color:{TEXT_MUTED};font-size:12px;text-transform:uppercase;letter-spacing:0.5px">Invoice Date</td>
                    <td style="padding:8px 0;text-align:right;color:{TEXT};font-size:14px">{invoice_date_str}</td>
                </tr>
                <tr>
                    <td style="padding:8px 0;color:{TEXT_MUTED};font-size:12px;text-transform:uppercase;letter-spacing:0.5px">Due Date</td>
                    <td style="padding:8px 0;text-align:right;color:{TEXT};font-size:14px">{due_date_str}</td>
                </tr>
            </table>

            <!-- Line Items Table -->
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER};border-radius:8px;overflow:hidden;margin-bottom:20px">
                <tr style="background:{BG}">
                    <th style="padding:10px 12px;text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;color:{TEXT_MUTED};font-weight:600;border-bottom:2px solid {BORDER}">Code</th>
                    <th style="padding:10px 12px;text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;color:{TEXT_MUTED};font-weight:600;border-bottom:2px solid {BORDER}">Description</th>
                    <th style="padding:10px 12px;text-align:center;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;color:{TEXT_MUTED};font-weight:600;border-bottom:2px solid {BORDER}">Units</th>
                    <th style="padding:10px 12px;text-align:right;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;color:{TEXT_MUTED};font-weight:600;border-bottom:2px solid {BORDER}">Rate</th>
                    <th style="padding:10px 12px;text-align:right;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;color:{TEXT_MUTED};font-weight:600;border-bottom:2px solid {BORDER}">Amount</th>
                </tr>
                {items_rows}
            </table>

            <!-- Totals -->
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BG};border:1px solid {BORDER};border-radius:8px;overflow:hidden">
                <tr>
                    <td style="padding:12px 16px;color:{TEXT_SECONDARY};font-size:14px">Total</td>
                    <td style="padding:12px 16px;text-align:right;color:{TEXT};font-size:14px;font-weight:600">${_money(invoice.total_amount)}</td>
                </tr>
                <tr>
                    <td style="padding:12px 16px;color:{SUCCESS};font-size:14px">Paid</td>
                    <td style="padding:12px 16px;text-align:right;color:{SUCCESS};font-size:14px">${_money(invoice.paid_amount)}</td>
                </tr>
                <tr style="border-top:2px solid {BORDER}">
                    <td style="padding:14px 16px;color:{PRIMARY_DARK};font-size:16px;font-weight:700">Balance Due</td>
                    <td style="padding:14px 16px;text-align:right;color:{PRIMARY_DARK};font-size:16px;font-weight:700">${_money(invoice.balance)}</td>
                </tr>
            </table>

            <p style="margin:24px 0 0;color:{TEXT_MUTED};font-size:13px">
                If you have any questions about this invoice, please don't hesitate to contact us.
            </p>
            <p style="margin:12px 0 0;color:{TEXT_MUTED};font-size:13px">
                Thank you,<br><span style="color:{TEXT};font-weight:600">{safe_org}</span>
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

        password_section = ''
        if temp_password:
            # FIX #9: Only show masked password in logs, full in email
            logger.info(f'Welcome email to {user.email} includes temp password (masked)')
            password_section = f'''
            <div style="background:{BG};border:1px solid {BORDER};border-left:4px solid {WARNING};padding:16px;border-radius:0 8px 8px 0;margin:20px 0">
                <p style="margin:0;font-size:12px;text-transform:uppercase;letter-spacing:0.5px;color:{TEXT_MUTED};font-weight:600">Temporary Password</p>
                <p style="margin:8px 0 0;font-family:monospace;font-size:16px;color:{TEXT};font-weight:700;letter-spacing:1px">{_esc(temp_password)}</p>
                <p style="margin:8px 0 0;font-size:12px;color:{TEXT_MUTED}">Please change this after your first login.</p>
            </div>
            '''

        role_display = user.get_role_display() if hasattr(user, 'get_role_display') else str(user.role)

        body = f'''
            <p style="margin:0 0 8px;color:{TEXT};font-size:18px;font-weight:700">Welcome, {safe_first}!</p>
            <p style="margin:0 0 20px;color:{TEXT_SECONDARY};font-size:14px">
                Your account has been created for <span style="color:{PRIMARY};font-weight:600">{safe_org}</span>.
            </p>

            <!-- Account Details -->
            <div style="background:{BG};border:1px solid {BORDER};border-radius:8px;padding:16px;margin:0 0 20px">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                    <tr>
                        <td style="padding:6px 0;color:{TEXT_MUTED};font-size:12px;text-transform:uppercase;letter-spacing:0.5px">Email</td>
                        <td style="padding:6px 0;text-align:right;color:{TEXT};font-size:14px;font-weight:600">{safe_email}</td>
                    </tr>
                    <tr>
                        <td style="padding:6px 0;color:{TEXT_MUTED};font-size:12px;text-transform:uppercase;letter-spacing:0.5px">Role</td>
                        <td style="padding:6px 0;text-align:right;color:{TEXT};font-size:14px">{_esc(role_display)}</td>
                    </tr>
                </table>
            </div>

            {password_section}

            <p style="margin:20px 0 0;color:{TEXT_MUTED};font-size:13px">
                If you have any questions, please contact your administrator.
            </p>
            <p style="margin:12px 0 0;color:{TEXT_MUTED};font-size:13px">
                Best regards,<br><span style="color:{TEXT};font-weight:600">{safe_org}</span>
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

        body = f'''
            <p style="margin:0 0 16px;color:{TEXT};font-size:14px">Dear {client_name},</p>
            <p style="margin:0 0 20px;color:{TEXT_SECONDARY};font-size:14px">
                This is a friendly reminder that the following invoice has an outstanding balance.
            </p>

            <!-- Invoice Summary -->
            <div style="background:{BG};border:1px solid {BORDER};border-left:4px solid {PRIMARY};padding:16px;border-radius:0 8px 8px 0;margin:0 0 20px">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                    <tr>
                        <td style="padding:6px 0;color:{TEXT_MUTED};font-size:12px;text-transform:uppercase;letter-spacing:0.5px">Invoice</td>
                        <td style="padding:6px 0;text-align:right;color:{TEXT};font-size:14px;font-weight:600">#{safe_inv_num}</td>
                    </tr>
                    <tr>
                        <td style="padding:6px 0;color:{TEXT_MUTED};font-size:12px;text-transform:uppercase;letter-spacing:0.5px">Total</td>
                        <td style="padding:6px 0;text-align:right;color:{TEXT};font-size:14px">${_money(invoice.total_amount)}</td>
                    </tr>
                    <tr>
                        <td style="padding:6px 0;color:{TEXT_MUTED};font-size:12px;text-transform:uppercase;letter-spacing:0.5px">Paid</td>
                        <td style="padding:6px 0;text-align:right;color:{SUCCESS};font-size:14px">${_money(invoice.paid_amount)}</td>
                    </tr>
                </table>
            </div>

            <!-- Balance Due - Highlighted -->
            <div style="background:{PRIMARY};border-radius:8px;padding:16px;text-align:center;margin:0 0 20px">
                <p style="margin:0;color:rgba(255,255,255,0.8);font-size:12px;text-transform:uppercase;letter-spacing:0.5px">Balance Due</p>
                <p style="margin:6px 0 0;color:{CARD};font-size:24px;font-weight:700">${_money(invoice.balance)}</p>
            </div>

            <p style="margin:0 0 0;color:{TEXT_SECONDARY};font-size:14px">
                Please contact us if you have any questions or need to arrange a payment plan.
            </p>
            <p style="margin:12px 0 0;color:{TEXT_MUTED};font-size:13px">
                Thank you,<br><span style="color:{TEXT};font-weight:600">{safe_org}</span>
            </p>
        '''

        return cls.send_generic(
            to=[invoice.client.email],
            subject=f'Payment Reminder - Invoice #{invoice.invoice_number}',
            html=_base_template('Payment Reminder', body, org_name),
            org_name=org_name,
        )
