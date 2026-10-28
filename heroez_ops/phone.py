"""Phone number normalisation to digits-only E.164 (no '+'), Indonesian-aware."""
from __future__ import annotations


def normalize(number: str, local: bool = False, default_country: str = "62") -> str:
    """Digits-only E.164 without '+'. '' if not a number.

    Meta already sends full international numbers, so the default only strips
    formatting. With local=True (numbers typed by people: roster sheet, /cari),
    Indonesian local forms are expanded: '0812-3456 789' -> '628123456789'.
    """
    digits = "".join(ch for ch in str(number) if ch.isdigit())
    if not digits:
        return ""
    if digits.startswith("00"):
        return digits[2:]
    if local and not str(number).strip().startswith("+"):
        if digits.startswith("0"):
            return default_country + digits[1:]
        if digits.startswith("8"):  # '812...' typed without the leading 0
            return default_country + digits
    return digits


def display(wa_id: str) -> str:
    return "+" + wa_id if wa_id else ""
