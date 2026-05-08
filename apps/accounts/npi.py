"""
NPI validation utility — CMS Luhn check.

Per the NPI Final Rule, the 10-digit National Provider Identifier's check
digit is computed by prepending the HIPAA namespace prefix `80840` to the
9-digit identifier portion and applying the standard Luhn algorithm.

Used by:
  - `seed_npis` management command (org-level NPI directory)
  - `UserCreateSerializer` / `UserUpdateSerializer` (provider-level NPI, E5)
  - `Client` / serializers if/when we ever store a payer NPI
"""
from __future__ import annotations

NPI_LUHN_PREFIX = '80840'


def luhn_validate_npi(npi: str) -> bool:
    """
    Return True iff `npi` is a 10-digit string whose check digit matches the
    Luhn computation over '80840' + first 9 digits.
    """
    if not isinstance(npi, str) or len(npi) != 10 or not npi.isdigit():
        return False

    base = NPI_LUHN_PREFIX + npi[:9]
    expected_check = int(npi[9])

    total = 0
    for i, ch in enumerate(reversed(base)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d

    computed_check = (10 - (total % 10)) % 10
    return computed_check == expected_check
