"""
Pre-submission claim validator.

Runs a claim through the checks that would cause an Office Ally rejection
BEFORE the X12 file is generated. Returns a structured list of problems
the user must fix (errors) and optional concerns (warnings).

Usage:
    from apps.billing.services.claim_validator import validate_claim
    result = validate_claim(claim)
    if not result['ok']:
        for err in result['errors']:
            ...
"""
from decimal import Decimal


REQUIRED_CLIENT_FIELDS = [
    ('first_name',    'Client first name'),
    ('last_name',     'Client last name'),
    ('date_of_birth', 'Client date of birth'),
    ('gender',        'Client gender'),
    ('address',       'Client address'),
    ('city',          'Client city'),
    ('state',         'Client state'),
    ('zip_code',      'Client ZIP code'),
]

REQUIRED_CLIENT_INSURANCE_FIELDS = [
    ('insurance_primary_name', 'Primary insurance name'),
    ('insurance_primary_id',   'Primary insurance member ID'),
]

REQUIRED_ORG_FIELDS = [
    ('name',    'Practice name'),
    ('tax_id',  'Practice Tax ID (EIN)'),
    ('address', 'Practice address'),
]


def _err(field: str, message: str) -> dict:
    return {'field': field, 'message': message, 'severity': 'error'}


def _warn(field: str, message: str) -> dict:
    return {'field': field, 'message': message, 'severity': 'warning'}


def validate_claim(claim) -> dict:
    """
    Check a claim for everything Office Ally needs before we generate the
    837P file. Returns a dict:
        {
          'ok': bool,                       # True if no errors
          'errors':   [{field, message}],   # must-fix — block submission
          'warnings': [{field, message}],   # advisory — allow but surface
        }
    """
    errors: list = []
    warnings: list = []

    client = getattr(claim, 'client', None)
    invoice = getattr(claim, 'invoice', None)
    org = getattr(invoice, 'organization', None) if invoice else None

    # ── Client demographics ─────────────────────────────────────────────────
    if not client:
        errors.append(_err('client', 'Claim has no client attached.'))
    else:
        for attr, label in REQUIRED_CLIENT_FIELDS:
            value = getattr(client, attr, None)
            if not value or (isinstance(value, str) and not value.strip()):
                errors.append(_err(f'client.{attr}', f'Missing: {label}.'))

        for attr, label in REQUIRED_CLIENT_INSURANCE_FIELDS:
            value = getattr(client, attr, '') or ''
            if not value.strip():
                errors.append(_err(f'client.{attr}', f'Missing: {label}.'))

    # ── Payer (must match the Office Ally directory) ────────────────────────
    if not (claim.payer_name or '').strip():
        errors.append(_err('claim.payer_name', 'Payer is required.'))
    elif not (claim.payer_id or '').strip():
        errors.append(_err(
            'claim.payer_id',
            'Payer ID is missing. Re-select the payer from the directory '
            'to auto-fill it — free-text payer names cannot be submitted.',
        ))
    else:
        # Verify the payer_id actually exists in the OA directory
        from apps.billing.models import Payer
        payer = Payer.objects.filter(payer_id=claim.payer_id).first()
        if not payer:
            errors.append(_err(
                'claim.payer_id',
                f'Payer ID "{claim.payer_id}" is not in the Office Ally directory. '
                f'Re-select the payer from the search dropdown.',
            ))
        elif not payer.supports_837p:
            errors.append(_err(
                'claim.payer_id',
                f'"{payer.name}" does not accept electronic claims (837P) '
                f'through Office Ally.',
            ))
        elif payer.enrollment_required:
            warnings.append(_warn(
                'claim.payer_id',
                f'"{payer.name}" requires enrollment with Office Ally before '
                f'claims can be submitted. Confirm enrollment is active.',
            ))

    # ── Billed amount ───────────────────────────────────────────────────────
    if not claim.billed_amount or Decimal(str(claim.billed_amount)) <= 0:
        errors.append(_err(
            'claim.billed_amount',
            'Billed amount must be greater than $0.00.',
        ))

    # ── Invoice items / service lines ───────────────────────────────────────
    if not invoice:
        errors.append(_err('invoice', 'Claim has no invoice attached.'))
    else:
        items = list(invoice.items.all()) if hasattr(invoice, 'items') else []
        if not items:
            errors.append(_err(
                'invoice.items',
                'Invoice has no line items — at least one service is required.',
            ))
        for idx, item in enumerate(items, start=1):
            prefix = f'invoice.item_{idx}'
            if not (item.service_code or '').strip():
                errors.append(_err(f'{prefix}.service_code',
                                   f'Line {idx}: CPT/service code is required.'))
            if not item.units or Decimal(str(item.units)) <= 0:
                errors.append(_err(f'{prefix}.units',
                                   f'Line {idx}: units must be greater than 0.'))
            if not item.rate or Decimal(str(item.rate)) <= 0:
                errors.append(_err(f'{prefix}.rate',
                                   f'Line {idx}: rate must be greater than $0.00.'))
            if not item.appointment_id:
                warnings.append(_warn(
                    f'{prefix}.appointment',
                    f'Line {idx}: no appointment linked — session date '
                    f'may not appear on the claim.',
                ))

    # ── Organization (billing provider) ─────────────────────────────────────
    if not org:
        errors.append(_err('organization', 'Invoice has no organization attached.'))
    else:
        for attr, label in REQUIRED_ORG_FIELDS:
            value = getattr(org, attr, '') or ''
            if isinstance(value, str) and not value.strip():
                errors.append(_err(f'organization.{attr}', f'Missing: {label}.'))

        # Billing provider NPI comes from the NPI model linked to the org
        npi_qs = getattr(org, 'npis', None)
        has_npi = False
        if npi_qs is not None:
            has_npi = npi_qs.filter(is_active=True).exists()
        if not has_npi:
            errors.append(_err(
                'organization.npi',
                'No active NPI on file for the practice. Add one in '
                'Settings before submitting claims.',
            ))

    return {
        'ok': len(errors) == 0,
        'errors': errors,
        'warnings': warnings,
    }
