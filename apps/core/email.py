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

# ─── Design Tokens (synced with sirena-frontend/src/index.css) ──────────────
PRIMARY        = '#0D9488'
PRIMARY_DARK   = '#0F766E'
PRIMARY_LIGHT  = '#14B8A6'
CYAN           = '#06B6D4'
BG             = '#F1F5F9'
CARD           = '#FFFFFF'
SURFACE        = '#F8FAFC'
SURFACE_TINT   = '#F0FDFA'
SURFACE_WARM   = '#FFFBEB'
BORDER         = '#E2E8F0'
TEXT           = '#0F172A'
TEXT_SECONDARY = '#475569'
TEXT_MUTED     = '#94A3B8'
SUCCESS        = '#10B981'
SUCCESS_BG     = '#ECFDF5'
WARNING        = '#F59E0B'
WARNING_BG     = '#FFFBEB'
ERROR          = '#EF4444'
INFO           = '#3B82F6'

EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')


def _validate_email(email: str) -> bool:
    return bool(EMAIL_RE.match(email))


def _esc(value) -> str:
    """Escape value for safe HTML rendering — prevents XSS via user data."""
    if value is None:
        return ''
    return html_mod.escape(str(value))


def _format_from(org_name: str = 'Sirena Health') -> str:
    raw_email = settings.DEFAULT_FROM_EMAIL
    if '<' in raw_email:
        return raw_email
    safe_name = re.sub(r'[<>"\\\r\n]', '', org_name).strip() or 'Sirena Health'
    return f'{safe_name} <{raw_email}>'


def _money(value) -> str:
    try:
        return f'{float(value or 0):.2f}'
    except (TypeError, ValueError):
        return '0.00'


def _datetime_label(value) -> str:
    if not value:
        return '\u2014'
    dt = timezone.localtime(value) if timezone.is_aware(value) else value
    return dt.strftime('%B %d, %Y at %I:%M %p').replace(' 0', ' ')


def _date_label(value) -> str:
    if not value:
        return '\u2014'
    return value.strftime('%B %d, %Y')


def _initials(value: str) -> str:
    parts = [part[:1].upper() for part in str(value or '').split() if part[:1]]
    return ''.join(parts[:2]) or 'SH'


# ─── Component Primitives ────────────────────────────────────────────────────

def _label(text: str) -> str:
    return (
        '<p style="margin:0 0 4px;color:' + TEXT_MUTED + ';font-size:10px;'
        'font-weight:700;text-transform:uppercase;letter-spacing:0.14em">'
        + _esc(text) + '</p>'
    )


def _info_row(label: str, value: str, *, emphasized: bool = False, last: bool = False) -> str:
    border = '' if last else 'border-bottom:1px solid ' + BORDER + ';'
    v_color = TEXT if emphasized else TEXT_SECONDARY
    v_weight = '700' if emphasized else '500'
    return (
        '<tr>'
        '<td style="padding:10px 0;' + border + 'color:' + TEXT_MUTED + ';font-size:11px;'
        'font-weight:700;text-transform:uppercase;letter-spacing:0.1em;width:40%">'
        + _esc(label) + '</td>'
        '<td style="padding:10px 0;' + border + 'text-align:right;color:' + v_color + ';'
        'font-size:13px;font-weight:' + v_weight + '">' + value + '</td>'
        '</tr>'
    )


def _section_card(content: str, *, accent: str = PRIMARY, background: str = CARD, title: str = '') -> str:
    title_row = ''
    if title:
        title_row = (
            '<p style="margin:0 0 14px;color:' + accent + ';font-size:10px;font-weight:700;'
            'text-transform:uppercase;letter-spacing:0.14em">' + _esc(title) + '</p>'
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="margin:0 0 20px;background:' + background + ';border:1px solid ' + BORDER + ';'
        'border-left:3px solid ' + accent + ';border-radius:8px">'
        '<tr><td style="padding:18px 20px">' + title_row + content + '</td></tr>'
        '</table>'
    )


def _highlight_card(label: str, value: str, *, accent: str = PRIMARY, background: str = SURFACE_TINT) -> str:
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="margin:0 0 20px;background:' + background + ';border:1px solid ' + BORDER + ';border-radius:8px">'
        '<tr><td style="padding:20px 24px;text-align:center">'
        '<p style="margin:0 0 4px;color:' + TEXT_MUTED + ';font-size:10px;font-weight:700;'
        'text-transform:uppercase;letter-spacing:0.14em">' + _esc(label) + '</p>'
        '<p style="margin:0;color:' + accent + ';font-size:36px;font-weight:700;'
        'letter-spacing:-0.02em;line-height:1.1">' + value + '</p>'
        '</td></tr></table>'
    )


def _cta_button(label: str, url: str) -> str:
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:28px auto 0">'
        '<tr><td align="center" style="border-radius:8px;background:' + PRIMARY + '">'
        '<a href="' + _esc(url) + '" style="display:inline-block;padding:14px 32px;border-radius:8px;'
        'background:' + PRIMARY + ';color:#FFFFFF;font-size:14px;font-weight:700;'
        'letter-spacing:0.02em;text-decoration:none;font-family:Arial,Helvetica,sans-serif">'
        + _esc(label) + '</a></td></tr></table>'
    )


# ─── Base Template ───────────────────────────────────────────────────────────

def _base_template(
    header_text: str,
    body_html: str,
    org_name: str = 'Sirena Health',
) -> str:
    """
    Premium email template — gradient header banner, white body card, clean footer.
    Table-based layout for universal email client support.
    """
    safe_org    = _esc(org_name)
    safe_header = _esc(header_text)
    frontend_url = getattr(settings, 'FRONTEND_BASE_URL', '').rstrip('/') or ''
    logo_url = f'{frontend_url}/images/EHRlogo.png' if frontend_url else ''
    current_year = timezone.now().year

    if logo_url:
        logo_html = (
            '<img src="' + logo_url + '" alt="' + safe_org + '" width="44" height="44" '
            'style="display:block;width:44px;height:44px;object-fit:contain;border:0;'
            'border-radius:8px;background:#fff;padding:4px" />'
        )
    else:
        org_initials = _initials(org_name)
        logo_html = (
            '<div style="width:44px;height:44px;border-radius:8px;background:rgba(255,255,255,0.2);'
            'color:#fff;font-size:18px;font-weight:700;line-height:44px;text-align:center">'
            + org_initials + '</div>'
        )

    return (
        '<!DOCTYPE html>\n'
        '<html lang="en" xmlns="http://www.w3.org/1999/xhtml">\n'
        '<head>\n'
        '    <meta charset="utf-8">\n'
        '    <meta name="viewport" content="width=device-width,initial-scale=1.0">\n'
        '    <meta name="x-apple-disable-message-reformatting">\n'
        '    <title>' + safe_header + '</title>\n'
        '    <style>\n'
        '        body{margin:0;padding:0;background-color:' + BG + ';-webkit-text-size-adjust:100%}\n'
        '        table{border-collapse:collapse;mso-table-lspace:0;mso-table-rspace:0}\n'
        '        img{border:0;outline:none;text-decoration:none}\n'
        '        a{color:' + PRIMARY + '}\n'
        '        @media only screen and (max-width:640px){\n'
        '            .ew{width:100% !important}\n'
        '            .eb{padding:24px 18px !important}\n'
        '            .eh{padding:22px 20px !important}\n'
        '        }\n'
        '    </style>\n'
        '</head>\n'
        '<body style="margin:0;padding:0;background-color:' + BG + ';font-family:Arial,Helvetica,sans-serif">\n\n'

        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:' + BG + '">\n'
        '<tr><td align="center" style="padding:32px 16px 40px">\n\n'

        '    <table role="presentation" class="ew" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%">\n\n'

        # Gradient header banner
        '        <tr>\n'
        '            <td style="border-radius:12px 12px 0 0;background:' + PRIMARY + ';background:linear-gradient(135deg,' + PRIMARY + ' 0%,' + CYAN + ' 100%);padding:0">\n'
        '                <table role="presentation" width="100%" cellpadding="0" cellspacing="0">\n'
        '                <tr><td class="eh" style="padding:28px 32px">\n'
        '                    <table role="presentation" cellpadding="0" cellspacing="0">\n'
        '                    <tr>\n'
        '                        <td style="vertical-align:middle;padding-right:16px">' + logo_html + '</td>\n'
        '                        <td style="vertical-align:middle">\n'
        '                            <p style="margin:0 0 1px;color:rgba(255,255,255,0.75);font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.16em">' + safe_org + '</p>\n'
        '                            <p style="margin:0;color:#FFFFFF;font-size:22px;font-weight:700;letter-spacing:-0.01em;line-height:1.2">' + safe_header + '</p>\n'
        '                        </td>\n'
        '                    </tr>\n'
        '                    </table>\n'
        '                </td></tr>\n'
        '                </table>\n'
        '            </td>\n'
        '        </tr>\n\n'

        # White body card
        '        <tr>\n'
        '            <td class="eb" style="background:' + CARD + ';padding:36px 40px;border:1px solid ' + BORDER + ';border-top:none;border-radius:0 0 12px 12px;box-shadow:0 4px 24px rgba(15,23,42,0.07)">\n'
        + body_html +
        '            </td>\n'
        '        </tr>\n\n'

        # Footer
        '        <tr>\n'
        '            <td style="padding:24px 8px 8px;text-align:center">\n'
        '                <p style="margin:0 0 6px;color:' + TEXT_MUTED + ';font-size:11px;line-height:1.6">'
        'Sent by <strong style="color:' + TEXT_SECONDARY + '">' + safe_org + '</strong> via Sirena Health EHR.</p>\n'
        '                <p style="margin:0;color:' + TEXT_MUTED + ';font-size:11px;line-height:1.6">'
        'This message is intended only for the named recipient. If you received it in error, please disregard it.</p>\n'
        '                <p style="margin:8px 0 0;color:' + BORDER + ';font-size:11px">'
        '&copy; ' + str(current_year) + ' Sirena Health</p>\n'
        '            </td>\n'
        '        </tr>\n\n'

        '    </table>\n'
        '</td></tr>\n'
        '</table>\n\n'
        '</body>\n'
        '</html>'
    )


# ─── Email Service ───────────────────────────────────────────────────────────

class EmailService:
    """Centralized email service wrapping the Resend SDK."""

    @classmethod
    def _get_configured(cls) -> bool:
        if not getattr(settings, 'RESEND_API_KEY', ''):
            logger.warning('RESEND_API_KEY not set — email will be logged only')
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

        Raises ValueError if any recipient address is invalid.
        """
        invalid = [e for e in to if not _validate_email(e)]
        if invalid:
            raise ValueError(f'Invalid email address(es): {", ".join(invalid)}')

        if not cls._get_configured():
            logger.info(f'[DEV MODE] Would send to {to}: {subject}')
            return None

        sender = from_email or _format_from(org_name)
        payload = {'from': sender, 'to': to, 'subject': subject, 'html': html}
        reply_to = getattr(settings, 'RESEND_REPLY_TO', '')
        if reply_to:
            payload['reply_to'] = reply_to

        try:
            response = resend.Emails.send(payload)
            email_id = getattr(response, 'id', None) or str(response)
            logger.info(f'Email sent to {to}: {subject} (id={email_id})')
            return response
        except Exception as e:
            logger.error(f'Failed to send email to {to}: {e}', exc_info=True)
            raise

    # ─── Invoice Email ───────────────────────────────────────────────────────

    @classmethod
    def send_invoice_email(cls, invoice, to_email: str, org_name: str = 'Sirena Health'):
        items = invoice.items.all()

        if items.exists():
            items_rows = ''.join(
                '<tr>'
                '<td style="padding:10px 12px;border-bottom:1px solid ' + BORDER + ';color:' + TEXT + ';font-size:13px;font-weight:600">' + _esc(item.service_code) + '</td>'
                '<td style="padding:10px 12px;border-bottom:1px solid ' + BORDER + ';color:' + TEXT_SECONDARY + ';font-size:13px">' + (_esc(item.description) or '\u2014') + '</td>'
                '<td style="padding:10px 12px;border-bottom:1px solid ' + BORDER + ';text-align:center;color:' + TEXT + ';font-size:13px">' + _esc(item.units) + '</td>'
                '<td style="padding:10px 12px;border-bottom:1px solid ' + BORDER + ';text-align:right;color:' + TEXT + ';font-size:13px">$' + _money(item.rate) + '</td>'
                '<td style="padding:10px 12px;border-bottom:1px solid ' + BORDER + ';text-align:right;color:' + TEXT + ';font-size:13px;font-weight:700">$' + _money(item.amount) + '</td>'
                '</tr>'
                for item in items
            )
        else:
            items_rows = (
                '<tr><td colspan="5" style="padding:20px 12px;text-align:center;color:' + TEXT_MUTED + ';'
                'font-size:13px;font-style:italic">No line items on this invoice.</td></tr>'
            )

        due_date_str = invoice.due_date.strftime('%B %d, %Y') if invoice.due_date else 'Upon Receipt'
        invoice_date = invoice.invoice_date.strftime('%B %d, %Y') if invoice.invoice_date else '\u2014'
        client_name  = _esc(invoice.client.full_name) if invoice.client else 'Valued Client'
        safe_org     = _esc(org_name)
        safe_inv     = _esc(invoice.invoice_number)

        detail_rows = (
            _info_row('Invoice number', '<strong>#' + safe_inv + '</strong>', emphasized=True)
            + _info_row('Invoice date', invoice_date)
            + _info_row('Due date', '<strong>' + _esc(due_date_str) + '</strong>')
            + _info_row('Client', client_name, last=True)
        )
        detail_card = _section_card(
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">' + detail_rows + '</table>',
            accent=PRIMARY, title='Invoice Details',
        )

        balance_rows = (
            _info_row('Subtotal', '$' + _money(invoice.total_amount))
            + _info_row('Paid', '$' + _money(invoice.paid_amount))
            + _info_row(
                'Balance Due',
                '<strong style="color:' + PRIMARY + ';font-size:18px">$' + _money(invoice.balance) + '</strong>',
                emphasized=True, last=True,
            )
        )
        balance_card = _section_card(
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">' + balance_rows + '</table>',
            accent=CYAN, background=SURFACE_TINT, title='Summary',
        )

        items_table = (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="margin:0 0 20px;border:1px solid ' + BORDER + ';border-radius:8px;overflow:hidden">'
            '<tr style="background:' + SURFACE + '">'
            '<th style="padding:10px 12px;text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:0.1em;color:' + TEXT_MUTED + ';font-weight:700;border-bottom:1px solid ' + BORDER + '">Code</th>'
            '<th style="padding:10px 12px;text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:0.1em;color:' + TEXT_MUTED + ';font-weight:700;border-bottom:1px solid ' + BORDER + '">Description</th>'
            '<th style="padding:10px 12px;text-align:center;font-size:10px;text-transform:uppercase;letter-spacing:0.1em;color:' + TEXT_MUTED + ';font-weight:700;border-bottom:1px solid ' + BORDER + '">Units</th>'
            '<th style="padding:10px 12px;text-align:right;font-size:10px;text-transform:uppercase;letter-spacing:0.1em;color:' + TEXT_MUTED + ';font-weight:700;border-bottom:1px solid ' + BORDER + '">Rate</th>'
            '<th style="padding:10px 12px;text-align:right;font-size:10px;text-transform:uppercase;letter-spacing:0.1em;color:' + TEXT_MUTED + ';font-weight:700;border-bottom:1px solid ' + BORDER + '">Amount</th>'
            '</tr>'
            + items_rows +
            '</table>'
        )

        body = (
            _label('Billing Summary')
            + '<p style="margin:0 0 6px;color:' + TEXT + ';font-size:26px;font-weight:700;letter-spacing:-0.02em">Invoice #' + safe_inv + '</p>'
            '<p style="margin:0 0 28px;color:' + TEXT_SECONDARY + ';font-size:14px;line-height:1.7">'
            'Hello ' + client_name + ', here is your invoice from <strong style="color:' + TEXT + '">' + safe_org + '</strong>. The details are below.</p>'
            + detail_card
            + items_table
            + balance_card
            + '<p style="margin:0;color:' + TEXT_SECONDARY + ';font-size:13px;line-height:1.7">'
            'If you have any questions, please contact your care team and they can review the details with you.</p>'
            '<p style="margin:18px 0 0;color:' + TEXT_MUTED + ';font-size:13px;line-height:1.7">'
            'Thank you,<br><strong style="color:' + TEXT + '">' + safe_org + '</strong></p>'
        )

        return cls.send_generic(
            to=[to_email],
            subject='Invoice #' + invoice.invoice_number + ' \u2014 ' + org_name,
            html=_base_template('Invoice', body, org_name),
            org_name=org_name,
        )

    # ─── Welcome Email ───────────────────────────────────────────────────────

    @classmethod
    def send_welcome_email(cls, user, temp_password: Optional[str] = None):
        if not user.email:
            logger.warning(f'User {user} has no email \u2014 skipping welcome email')
            return None

        org_name      = user.organization.name if getattr(user, 'organization', None) else 'Sirena Health'
        safe_org      = _esc(org_name)
        safe_first    = _esc(user.first_name or 'there')
        safe_email    = _esc(user.email)
        workspace_url = getattr(settings, 'FRONTEND_BASE_URL', '').rstrip('/') or '#'
        role_display  = user.get_role_display() if hasattr(user, 'get_role_display') else str(user.role)

        account_rows = (
            _info_row('Email / Username', '<strong>' + safe_email + '</strong>', emphasized=True)
            + _info_row('Role', _esc(role_display), last=True)
        )
        account_card = _section_card(
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">' + account_rows + '</table>',
            accent=PRIMARY, title='Your Account',
        )

        password_section = ''
        if temp_password:
            logger.info(f'Welcome email to {user.email} includes temp password (masked)')
            pw_content = (
                '<p style="margin:0 0 12px;color:' + WARNING + ';font-size:13px;font-weight:600">'
                'Use this temporary password to sign in, then change it immediately.</p>'
                '<p style="margin:0;padding:14px 18px;border-radius:6px;background:' + CARD + ';'
                'border:1px solid rgba(245,158,11,0.3);font-family:Consolas,Monaco,Courier New,monospace;'
                'font-size:22px;font-weight:700;color:' + TEXT + ';letter-spacing:0.08em;text-align:center">'
                + _esc(temp_password) + '</p>'
            )
            password_section = _section_card(pw_content, accent=WARNING, background=WARNING_BG, title='Temporary Password')

        body = (
            '<p style="margin:0 0 8px;color:' + TEXT + ';font-size:26px;font-weight:700;letter-spacing:-0.02em">'
            'Welcome, ' + safe_first + '.</p>'
            '<p style="margin:0 0 28px;color:' + TEXT_SECONDARY + ';font-size:14px;line-height:1.7">'
            'Your account at <strong style="color:' + TEXT + '">' + safe_org + '</strong> is ready. Below is everything you need to get started.</p>'
            + account_card
            + password_section
            + _cta_button('Open Workspace', workspace_url)
            + '<p style="margin:24px 0 0;color:' + TEXT_SECONDARY + ';font-size:13px;line-height:1.7">'
            'If you need help signing in or your temporary password has expired, contact your administrator.</p>'
            '<p style="margin:16px 0 0;color:' + TEXT_MUTED + ';font-size:13px;line-height:1.7">'
            'Best regards,<br><strong style="color:' + TEXT + '">' + safe_org + '</strong></p>'
        )

        return cls.send_generic(
            to=[user.email],
            subject='Welcome to ' + org_name + ' \u2014 Your account is ready',
            html=_base_template('Welcome', body, org_name),
            org_name=org_name,
        )

    # ─── Payment Reminder ────────────────────────────────────────────────────

    @classmethod
    def send_payment_reminder(cls, invoice, org_name: str = 'Sirena Health'):
        if not invoice.client or not invoice.client.email:
            # Log the opaque invoice id, never the model repr \u2014 Invoice.__str__
            # interpolates Client.__str__ ("Last, First"), which would put a
            # patient name into stdout / Render logs.
            logger.warning('Invoice %s \u2014 client has no email, skipping reminder', invoice.id)
            return None

        client_name  = _esc(invoice.client.full_name)
        safe_org     = _esc(org_name)
        safe_inv     = _esc(invoice.invoice_number)
        due_str      = invoice.due_date.strftime('%B %d, %Y') if invoice.due_date else 'Upon Receipt'

        summary_rows = (
            _info_row('Invoice', '<strong>#' + safe_inv + '</strong>', emphasized=True)
            + _info_row('Due date', _esc(due_str))
            + _info_row('Total', '$' + _money(invoice.total_amount))
            + _info_row('Paid', '$' + _money(invoice.paid_amount), last=True)
        )
        summary_card = _section_card(
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">' + summary_rows + '</table>',
            accent=PRIMARY, title='Invoice Summary',
        )
        balance_card = _highlight_card('Balance Due', '$' + _money(invoice.balance), accent=WARNING, background=WARNING_BG)

        body = (
            _label('Account Notice')
            + '<p style="margin:0 0 8px;color:' + TEXT + ';font-size:26px;font-weight:700;letter-spacing:-0.02em">Payment reminder</p>'
            '<p style="margin:0 0 28px;color:' + TEXT_SECONDARY + ';font-size:14px;line-height:1.7">'
            'Hello ' + client_name + ', this is a friendly reminder that invoice '
            '<strong style="color:' + TEXT + '">#' + safe_inv + '</strong> has an outstanding balance.</p>'
            + summary_card
            + balance_card
            + '<p style="margin:0;color:' + TEXT_SECONDARY + ';font-size:13px;line-height:1.7">'
            'Please contact ' + safe_org + ' if you have any questions or need to discuss payment arrangements.</p>'
            '<p style="margin:18px 0 0;color:' + TEXT_MUTED + ';font-size:13px;line-height:1.7">'
            'Thank you,<br><strong style="color:' + TEXT + '">' + safe_org + '</strong></p>'
        )

        return cls.send_generic(
            to=[invoice.client.email],
            subject='Payment Reminder \u2014 Invoice #' + invoice.invoice_number,
            html=_base_template('Payment Reminder', body, org_name),
            org_name=org_name,
        )

    # ─── Payment Receipt ─────────────────────────────────────────────────────

    @classmethod
    def send_payment_receipt(cls, payment, org_name: str = 'Sirena Health'):
        if not getattr(payment, 'client', None) or not payment.client.email:
            logger.warning(f'Payment {payment} \u2014 client has no email, skipping receipt')
            return None

        invoice      = payment.invoice
        client_name  = _esc(payment.client.full_name)
        safe_org     = _esc(org_name)
        safe_invoice = _esc(invoice.invoice_number if invoice else '')
        method_label = _esc(payment.payment_method or payment.payer_type or 'Recorded payment')

        detail_rows = (
            _info_row('Invoice', '<strong>#' + safe_invoice + '</strong>', emphasized=True)
            + _info_row('Payment date', _datetime_label(payment.payment_date))
            + _info_row('Method', method_label)
            + _info_row('Reference', _esc(payment.reference_number) or '\u2014', last=True)
        )
        detail_card = _section_card(
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">' + detail_rows + '</table>',
            accent=PRIMARY, title='Payment Details',
        )

        balance_rows = (
            _info_row(
                'Amount received',
                '<strong style="color:' + SUCCESS + '">$' + _money(payment.amount) + '</strong>',
                emphasized=True,
            )
            + _info_row('Total paid', '$' + _money(invoice.paid_amount if invoice else payment.amount))
            + _info_row('Remaining balance', '$' + _money(invoice.balance if invoice else 0), last=True)
        )
        balance_card = _section_card(
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">' + balance_rows + '</table>',
            accent=SUCCESS, background=SUCCESS_BG, title='Balance Summary',
        )

        inv_num = invoice.invoice_number if invoice else ''
        body = (
            _label('Payment Receipt')
            + '<p style="margin:0 0 8px;color:' + TEXT + ';font-size:26px;font-weight:700;letter-spacing:-0.02em">Payment received</p>'
            '<p style="margin:0 0 28px;color:' + TEXT_SECONDARY + ';font-size:14px;line-height:1.7">'
            'Hello ' + client_name + ', we recorded your payment for invoice '
            '<strong style="color:' + TEXT + '">#' + safe_invoice + '</strong>. Your updated balance is below.</p>'
            + detail_card
            + balance_card
            + '<p style="margin:0;color:' + TEXT_SECONDARY + ';font-size:13px;line-height:1.7">'
            'If anything looks incorrect, please contact ' + safe_org + ' so the billing team can review it with you.</p>'
            '<p style="margin:18px 0 0;color:' + TEXT_MUTED + ';font-size:13px;line-height:1.7">'
            'Thank you,<br><strong style="color:' + TEXT + '">' + safe_org + '</strong></p>'
        )

        return cls.send_generic(
            to=[payment.client.email],
            subject='Payment Receipt \u2014 Invoice #' + inv_num,
            html=_base_template('Payment Received', body, org_name),
            org_name=org_name,
        )

    # ─── Password Reset ──────────────────────────────────────────────────────

    @classmethod
    def send_password_reset_email(cls, user, reset_url: str, org_name: str = 'Sirena Health'):
        if not user.email:
            return None

        safe_first = _esc(user.first_name or 'there')
        safe_org   = _esc(org_name)

        security_content = (
            '<p style="margin:0 0 6px;color:' + WARNING + ';font-size:13px;font-weight:600">'
            'This link expires in <strong>1 hour</strong>.</p>'
            '<p style="margin:0;color:' + TEXT_SECONDARY + ';font-size:13px;line-height:1.6">'
            'If you did not request a password reset, you can safely ignore this email \u2014 your password will not change.</p>'
        )
        security_card = _section_card(security_content, accent=WARNING, background=WARNING_BG, title='Security Notice')

        body = (
            '<p style="margin:0 0 8px;color:' + TEXT + ';font-size:26px;font-weight:700;letter-spacing:-0.02em">Reset your password</p>'
            '<p style="margin:0 0 28px;color:' + TEXT_SECONDARY + ';font-size:14px;line-height:1.7">'
            'Hi ' + safe_first + ', we received a request to reset your password for '
            '<strong style="color:' + TEXT + '">' + safe_org + '</strong>. Click the button below to choose a new one.</p>'
            + security_card
            + _cta_button('Reset Password', reset_url)
            + '<p style="margin:24px 0 0;color:' + TEXT_MUTED + ';font-size:13px;line-height:1.7">'
            'Thank you,<br><strong style="color:' + TEXT + '">' + safe_org + '</strong></p>'
        )

        return cls.send_generic(
            to=[user.email],
            subject='Reset your ' + org_name + ' password',
            html=_base_template('Password Reset', body, org_name),
            org_name=org_name,
        )

    # ─── Appointment Email ───────────────────────────────────────────────────

    @classmethod
    def send_appointment_email(cls, appointment, *, event: str, org_name: str = 'Sirena Health'):
        if not getattr(appointment, 'client', None) or not appointment.client.email:
            # Opaque id only \u2014 Appointment.__str__ interpolates the client,
            # which would leak a patient name into stdout / Render logs.
            logger.warning(
                'Appointment %s \u2014 client has no email, skipping appointment email',
                appointment.id,
            )
            return None

        client_name   = _esc(appointment.client.full_name)
        provider_name = _esc(getattr(appointment.provider, 'full_name', '') or 'Care team')
        location_name = _esc(getattr(appointment.location, 'name', '') or 'To be confirmed')
        safe_org      = _esc(org_name)
        service_code  = _esc(appointment.service_code) or 'Scheduled visit'

        event_map = {
            'scheduled': ('Appointment Confirmed', 'Your appointment has been confirmed.', PRIMARY, SURFACE_TINT),
            'updated':   ('Appointment Updated',   'Your appointment details have been updated.', CYAN, SURFACE_TINT),
            'cancelled': ('Appointment Cancelled', 'Your appointment has been cancelled.', ERROR, '#FEF2F2'),
        }
        header_text, intro, accent, bg = event_map.get(
            event, ('Appointment Update', 'Your appointment information has changed.', PRIMARY, SURFACE_TINT)
        )

        closing = (
            'If you need to reschedule, please contact your care team and they will arrange a new time for you.'
            if event == 'cancelled' else
            'If you have any questions about your appointment, please contact your care team.'
        )

        appt_rows = (
            _info_row('Date & Time', '<strong>' + _datetime_label(appointment.start_time) + '</strong>', emphasized=True)
            + _info_row('End Time', _datetime_label(appointment.end_time))
            + _info_row('Provider', provider_name)
            + _info_row('Location', location_name)
            + _info_row('Service', service_code, last=True)
        )
        appt_card = _section_card(
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">' + appt_rows + '</table>',
            accent=accent, background=bg, title='Appointment Details',
        )

        body = (
            _label('Schedule Update')
            + '<p style="margin:0 0 8px;color:' + TEXT + ';font-size:26px;font-weight:700;letter-spacing:-0.02em">' + _esc(header_text) + '</p>'
            '<p style="margin:0 0 28px;color:' + TEXT_SECONDARY + ';font-size:14px;line-height:1.7">'
            'Hello ' + client_name + ', ' + intro + ' See the details from '
            '<strong style="color:' + TEXT + '">' + safe_org + '</strong> below.</p>'
            + appt_card
            + '<p style="margin:0;color:' + TEXT_SECONDARY + ';font-size:13px;line-height:1.7">' + closing + '</p>'
            '<p style="margin:18px 0 0;color:' + TEXT_MUTED + ';font-size:13px;line-height:1.7">'
            'Thank you,<br><strong style="color:' + TEXT + '">' + safe_org + '</strong></p>'
        )

        return cls.send_generic(
            to=[appointment.client.email],
            subject=header_text,
            html=_base_template(header_text, body, org_name),
            org_name=org_name,
        )
