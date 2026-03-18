from typing import Any


SERVICE_CATALOG: dict[str, dict[str, str]] = {
    '97151': {'description': 'Behavior identification assessment'},
    '97153': {'description': 'Adaptive behavior treatment by protocol'},
    '97155': {'description': 'Adaptive behavior treatment with protocol modification'},
    '97156': {'description': 'Family adaptive behavior treatment guidance'},
    '97157': {'description': 'Multiple-family group adaptive behavior treatment guidance'},
}


def get_service_description(service_code: str) -> str:
    code = (service_code or '').strip()
    entry = SERVICE_CATALOG.get(code)
    return entry['description'] if entry else ''


def resolve_billing_defaults(*, organization_id: Any, client_id: Any, service_code: str) -> dict[str, Any]:
    from .models import InvoiceItem

    code = (service_code or '').strip()
    description = get_service_description(code)

    client_history = InvoiceItem.objects.filter(
        invoice__organization_id=organization_id,
        invoice__client_id=client_id,
        service_code=code,
    ).order_by('-created_at')

    client_description_item = client_history.exclude(description='').first()
    client_rate_item = client_history.filter(rate__gt=0).first()

    if client_description_item:
        description = client_description_item.description
    if client_rate_item:
        return {
            'description': description,
            'rate': client_rate_item.rate,
        }

    org_history = InvoiceItem.objects.filter(
        invoice__organization_id=organization_id,
        service_code=code,
    ).order_by('-created_at')

    org_description_item = org_history.exclude(description='').first()
    org_rate_item = org_history.filter(rate__gt=0).first()

    if org_description_item:
        description = org_description_item.description

    return {
        'description': description,
        'rate': org_rate_item.rate if org_rate_item else None,
    }
