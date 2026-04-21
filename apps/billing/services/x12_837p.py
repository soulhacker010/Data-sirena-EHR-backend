"""
X12 837P (Professional) claim file generator for Office Ally.

Spec: Office Ally 837P Companion Guide (005010X222A1)
Receiver: Office Ally Tax ID 330897513

Usage:
    from apps.billing.services.x12_837p import generate_837p
    x12_string = generate_837p(claim)
"""
import uuid
from datetime import datetime, date
from django.utils import timezone


# ─── Office Ally Hardcoded Constants ────────────────────────────────────────
OA_RECEIVER_ID = '330897513'      # Office Ally Tax ID — goes in ISA08 and Loop 1000B
OA_RECEIVER_NAME = 'OFFICE ALLY'
X12_VERSION = '005010X222A1'


def _seg(*elements) -> str:
    """Build an X12 segment — join with * delimiter, end with ~."""
    return '*'.join(str(e) for e in elements) + '~'


def _pad(value: str, length: int, left: bool = False) -> str:
    """Pad a string to exact length."""
    s = str(value or '')[:length]
    return s.ljust(length) if not left else s.rjust(length)


def _date(dt) -> str:
    """Format date as YYYYMMDD."""
    if isinstance(dt, datetime):
        return dt.strftime('%Y%m%d')
    if isinstance(dt, date):
        return dt.strftime('%Y%m%d')
    return ''


def _time(dt) -> str:
    """Format datetime as HHMM."""
    if isinstance(dt, datetime):
        return dt.strftime('%H%M')
    return '0000'


def _money(value) -> str:
    """Format decimal as money string without $ sign."""
    try:
        return f'{float(value or 0):.2f}'
    except (TypeError, ValueError):
        return '0.00'


def generate_837p(claim, *, submitter_id: str = '', test: bool = False) -> str:
    """
    Generate a complete X12 837P file for a single claim.

    Args:
        claim: Claim model instance (must have .invoice, .client, .invoice.items relations)
        submitter_id: Tax ID or NPI of submitting org (falls back to org tax ID)
        test: If True, marks ISA15 as T (test) — but for OA testing use OATEST in filename instead

    Returns:
        Full X12 837P file as a string.
    """
    now = timezone.now()
    ctrl_num = str(uuid.uuid4().int)[:9].zfill(9)
    group_ctrl = str(uuid.uuid4().int)[:4].zfill(4)
    trans_ctrl = str(uuid.uuid4().int)[:4].zfill(4)
    interchange_date = now.strftime('%y%m%d')
    interchange_time = now.strftime('%H%M')
    full_date = now.strftime('%Y%m%d')
    full_time = now.strftime('%H%M')

    # Pull data from claim / org
    org = claim.invoice.organization if hasattr(claim.invoice, 'organization') else None
    client = claim.client
    invoice = claim.invoice

    sub_id = submitter_id or (getattr(org, 'tax_id', '') or OA_RECEIVER_ID)
    org_name = getattr(org, 'name', 'SIRENA HEALTH') if org else 'SIRENA HEALTH'
    billing_npi = getattr(org, 'npi', '') or ''
    billing_tax_id = getattr(org, 'tax_id', '') or sub_id
    billing_address = getattr(org, 'address', '') or ''
    billing_city = getattr(org, 'city', '') or ''
    billing_state = getattr(org, 'state', '') or ''
    billing_zip = (getattr(org, 'zip_code', '') or '').replace('-', '')

    usage = 'T' if test else 'P'

    segments = []

    # ── Interchange Envelope ─────────────────────────────────────────────────
    segments.append(_seg(
        'ISA', '00', '          ', '00', '          ',
        'ZZ', _pad(sub_id, 15),
        'ZZ', _pad(OA_RECEIVER_ID, 15),
        interchange_date, interchange_time,
        '^', '00501', ctrl_num, '0', usage, ':'
    ))

    # ── Functional Group Header ──────────────────────────────────────────────
    segments.append(_seg('GS', 'HC', sub_id, OA_RECEIVER_ID, full_date, full_time, group_ctrl, 'X', X12_VERSION))

    # ── Transaction Set Header ───────────────────────────────────────────────
    segment_count = 0
    segments.append(_seg('ST', '837', trans_ctrl, X12_VERSION))

    # BHT — Beginning of Hierarchical Transaction
    segments.append(_seg('BHT', '0019', '00', trans_ctrl, full_date, full_time, 'CH'))

    # ── Loop 1000A — Submitter (Practice / Org) ──────────────────────────────
    segments.append(_seg('NM1', '41', '2', org_name[:35], '', '', '', '', '46', sub_id))
    segments.append(_seg('PER', 'IC', org_name[:35], 'TE', getattr(org, 'phone', '0000000000') or '0000000000'))

    # ── Loop 1000B — Receiver (Office Ally) ─────────────────────────────────
    segments.append(_seg('NM1', '40', '2', OA_RECEIVER_NAME, '', '', '', '', '46', OA_RECEIVER_ID))

    # ── HL — Billing Provider Level ─────────────────────────────────────────
    hl_counter = 1
    segments.append(_seg('HL', str(hl_counter), '', '20', '1'))

    # ── Loop 2010AA — Billing Provider ───────────────────────────────────────
    provider = getattr(claim, 'rendering_provider', None) or (
        invoice.items.first().appointment.provider if invoice.items.exists() and invoice.items.first().appointment else None
    )
    prov_first = getattr(provider, 'first_name', '') or ''
    prov_last = getattr(provider, 'last_name', '') or org_name
    prov_npi = getattr(provider, 'npi', '') or billing_npi or ''

    segments.append(_seg('NM1', '85', '1' if prov_first else '2', prov_last[:35], prov_first[:35], '', '', '', 'XX', prov_npi))
    if billing_address:
        segments.append(_seg('N3', billing_address[:55]))
        segments.append(_seg('N4', billing_city[:30], billing_state[:2], billing_zip[:15]))
    segments.append(_seg('REF', 'EI', billing_tax_id))

    # ── HL — Subscriber Level ────────────────────────────────────────────────
    hl_counter += 1
    hl_sub = hl_counter
    segments.append(_seg('HL', str(hl_counter), '1', '22', '0'))
    segments.append(_seg('SBR', 'P', '18', '', '', '', '', '', '', 'BL'))

    # ── Loop 2010BA — Subscriber (Insured) ───────────────────────────────────
    sub_last = (client.last_name or '').upper()
    sub_first = (client.first_name or '').upper()
    member_id = getattr(client, 'insurance_primary_id', '') or ''
    sub_dob = _date(client.date_of_birth) if getattr(client, 'date_of_birth', None) else ''
    sub_address = getattr(client, 'address', '') or ''
    sub_city = getattr(client, 'city', '') or ''
    sub_state = getattr(client, 'state', '') or ''
    sub_zip = (getattr(client, 'zip_code', '') or '').replace('-', '')

    segments.append(_seg('NM1', 'IL', '1', sub_last[:60], sub_first[:35], '', '', '', 'MI', member_id))
    if sub_address:
        segments.append(_seg('N3', sub_address[:55]))
        segments.append(_seg('N4', sub_city[:30], sub_state[:2], sub_zip[:15]))
    if sub_dob:
        segments.append(_seg('DMG', 'D8', sub_dob))

    # ── Loop 2010BB — Payer ───────────────────────────────────────────────────
    segments.append(_seg('NM1', 'PR', '2', (claim.payer_name or '')[:35], '', '', '', '', 'PI', claim.payer_id or ''))

    # ── Loop 2300 — Claim ─────────────────────────────────────────────────────
    acct_num = str(claim.id)[:20]
    total_charge = _money(claim.billed_amount)

    # Place of service: default 11 (office), 02 for telehealth
    pos_code = '11'

    # Date of service — from first invoice item's appointment
    dos = ''
    items = list(invoice.items.select_related('appointment').all())
    if items and items[0].appointment:
        dos = _date(items[0].appointment.start_time)
    if not dos:
        dos = _date(invoice.invoice_date)

    # ICD-10 diagnosis codes from client
    dx_codes = []
    raw_dx = getattr(client, 'diagnosis_codes', None)
    if raw_dx:
        if isinstance(raw_dx, list):
            dx_codes = [str(d).replace('.', '') for d in raw_dx[:12]]
        elif isinstance(raw_dx, str):
            dx_codes = [raw_dx.replace('.', '')]

    segments.append(_seg('CLM', acct_num, total_charge, '', '', f'{pos_code}:B:1', 'Y', 'A', 'Y', 'I'))
    if dos:
        segments.append(_seg('DTP', '472', 'D8', dos))

    # HI — Diagnosis codes (ICD-10)
    if dx_codes:
        hi_elements = ['ABK:' + dx_codes[0]]
        for code in dx_codes[1:]:
            hi_elements.append('ABF:' + code)
        segments.append('HI*' + '*'.join(hi_elements) + '~')

    # Prior auth number if present
    auth_num = getattr(client, 'insurance_primary_group', '') or ''
    if auth_num:
        segments.append(_seg('REF', 'G1', auth_num))

    # ── Loop 2310B — Rendering Provider (if we have NPI) ─────────────────────
    if prov_npi:
        segments.append(_seg('NM1', '82', '1', prov_last[:60], prov_first[:35], '', '', '', 'XX', prov_npi))

    # ── Loop 2400 — Service Lines ─────────────────────────────────────────────
    line_num = 0
    for item in items:
        line_num += 1
        cpt = item.service_code or ''
        charge = _money(item.amount)
        units = str(int(float(item.units or 1)))
        item_dos = dos
        if item.appointment:
            item_dos = _date(item.appointment.start_time) or dos

        segments.append(_seg('LX', str(line_num)))
        # SV1: HC:CPT*charge*UN*units***diag_pointer
        segments.append(_seg('SV1', f'HC:{cpt}', charge, 'UN', units, '', '', '1'))
        segments.append(_seg('DTP', '472', 'D8', item_dos))

    # ── Transaction Set Trailer ───────────────────────────────────────────────
    seg_count = len(segments) - 2  # exclude ISA and GS
    segments.append(_seg('SE', str(seg_count + 1), trans_ctrl))

    # ── Functional Group Trailer ─────────────────────────────────────────────
    segments.append(_seg('GE', '1', group_ctrl))

    # ── Interchange Trailer ───────────────────────────────────────────────────
    segments.append(_seg('IEA', '1', ctrl_num))

    return '\n'.join(segments)


def build_filename(claim, *, test: bool = False) -> str:
    """
    Build the upload filename for Office Ally.
    Include OATEST for test submissions — OA parses but does NOT send to payers.
    """
    ts = datetime.now().strftime('%Y%m%d%H%M%S')
    suffix = 'OATEST' if test else ''
    return f'SIRENA_{claim.id}_{ts}{suffix}.837'
