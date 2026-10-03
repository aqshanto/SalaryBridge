"""Money is stored as integer paisa (1 BDT = 100 paisa). Floats are never accepted."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

PAISA_PER_BDT = 100


def is_paisa(value: object) -> bool:
    return type(value) is int  # bool is a subclass of int, so compare the exact type


def bdt_to_paisa(value: int | str | Decimal) -> int:
    """Convert whole BDT (int), a decimal string ("250.50") or a Decimal to paisa."""
    if isinstance(value, float):
        raise TypeError("Use int, str or Decimal for money, never float")
    if is_paisa(value):
        return value * PAISA_PER_BDT
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"Not a money amount: {value!r}") from exc
    paisa = (amount * PAISA_PER_BDT).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return int(paisa)


def paisa_to_bdt(paisa: int) -> str:
    """Format paisa as a BDT string with two decimals, e.g. 12345 -> '123.45'."""
    if not is_paisa(paisa):
        raise TypeError("paisa must be int")
    sign = "-" if paisa < 0 else ""
    whole, frac = divmod(abs(paisa), PAISA_PER_BDT)
    return f"{sign}{whole}.{frac:02d}"
