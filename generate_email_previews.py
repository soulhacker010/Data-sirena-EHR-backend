from pathlib import Path
from types import MethodType, SimpleNamespace
from datetime import date, datetime, timezone as dt_timezone
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

import django

django.setup()

from apps.core.email import EmailService


class PreviewItems:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self

    def exists(self):
        return bool(self._items)

    def __iter__(self):
        return iter(self._items)


class PreviewUser:
    def __init__(self, first_name, last_name, email, role, organization):
        self.first_name = first_name
        self.last_name = last_name
        self.email = email
        self.role = role
        self.organization = organization

    def get_role_display(self):
        return self.role.replace('_', ' ').title()


preview_dir = Path(__file__).resolve().parent / 'email_previews'
preview_dir.mkdir(exist_ok=True)

org = SimpleNamespace(name='Sirena Health')
client = SimpleNamespace(full_name='Jordan Avery', email='jordan.avery@example.com')
provider = SimpleNamespace(full_name='Dr. Maya Bennett')
location = SimpleNamespace(name='Sirena Downtown Clinic')
invoice_items = [
    SimpleNamespace(service_code='97153', description='ABA therapy session', units='4', rate='62.50', amount='250.00'),
    SimpleNamespace(service_code='97155', description='Care plan review', units='2', rate='75.00', amount='150.00'),
]
invoice = SimpleNamespace(
    invoice_number='INV-2026-0142',
    invoice_date=date(2026, 3, 18),
    due_date=date(2026, 4, 1),
    total_amount='400.00',
    paid_amount='150.00',
    balance='250.00',
    client=client,
    items=PreviewItems(invoice_items),
)
payment = SimpleNamespace(
    invoice=invoice,
    client=client,
    amount='150.00',
    payment_method='Visa ending in 4242',
    payer_type='patient',
    reference_number='PMT-2026-0098',
    payment_date=datetime(2026, 3, 18, 14, 30, tzinfo=dt_timezone.utc),
)
appointment = SimpleNamespace(
    client=client,
    provider=provider,
    location=location,
    start_time=datetime(2026, 3, 22, 9, 0, tzinfo=dt_timezone.utc),
    end_time=datetime(2026, 3, 22, 11, 0, tzinfo=dt_timezone.utc),
    service_code='97153',
)
user = PreviewUser(
    first_name='Amara',
    last_name='Cole',
    email='amara.cole@example.com',
    role='clinician',
    organization=org,
)

captured = {}
original_send_generic = EmailService.send_generic


def capture_send_generic(cls, to, subject, html, from_email=None, org_name='Sirena Health'):
    captured['subject'] = subject
    captured['html'] = html
    return html


EmailService.send_generic = MethodType(capture_send_generic, EmailService)


def render(filename, callback):
    captured.clear()
    callback()
    (preview_dir / filename).write_text(captured['html'], encoding='utf-8')


try:
    render('welcome_preview.html', lambda: EmailService.send_welcome_email(user, temp_password='Sirena!2026'))
    render('invoice_preview.html', lambda: EmailService.send_invoice_email(invoice, to_email=client.email, org_name=org.name))
    render('payment_reminder_preview.html', lambda: EmailService.send_payment_reminder(invoice, org_name=org.name))
    render('payment_receipt_preview.html', lambda: EmailService.send_payment_receipt(payment, org_name=org.name))
    render('appointment_scheduled_preview.html', lambda: EmailService.send_appointment_email(appointment, event='scheduled', org_name=org.name))
    render('appointment_updated_preview.html', lambda: EmailService.send_appointment_email(appointment, event='updated', org_name=org.name))
    render('appointment_cancelled_preview.html', lambda: EmailService.send_appointment_email(appointment, event='cancelled', org_name=org.name))
finally:
    EmailService.send_generic = original_send_generic

for path in sorted(preview_dir.glob('*preview.html')):
    print(path.name)
