"""Rupiah amounts in figures and in Indonesian words, as contracts and invoices write them."""
from __future__ import annotations

_ONES = ["", "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh", "delapan", "sembilan", "sepuluh", "sebelas"]


def _words(n: int) -> str:
    if n < 12:
        return _ONES[n]
    if n < 20:
        return _words(n - 10) + " belas"
    if n < 100:
        return (_words(n // 10) + " puluh " + _words(n % 10)).strip()
    if n < 200:
        return ("seratus " + _words(n - 100)).strip()
    if n < 1000:
        return (_words(n // 100) + " ratus " + _words(n % 100)).strip()
    if n < 2000:
        return ("seribu " + _words(n - 1000)).strip()
    for size, name in ((10**12, "triliun"), (10**9, "miliar"), (10**6, "juta"), (10**3, "ribu")):
        if n >= size:
            return (_words(n // size) + f" {name} " + _words(n % size)).strip()
    return ""


def terbilang(n: int) -> str:
    """150000000 -> 'seratus lima puluh juta'."""
    n = int(n)
    if n == 0:
        return "nol"
    return ("minus " if n < 0 else "") + " ".join(_words(abs(n)).split())


def rupiah(n: int) -> str:
    """150000000 -> 'Rp150.000.000'."""
    return "Rp" + f"{int(n):,}".replace(",", ".")


def rupiah_words(n: int) -> str:
    """150000000 -> 'seratus lima puluh juta Rupiah'."""
    return f"{terbilang(n)} Rupiah"


def parse_amount(text: str) -> int:
    """'Rp 150.000.000' / '150jt' / '1,5 M' -> integer rupiah. Raises ValueError if unreadable."""
    t = str(text).strip().lower().replace("rp", "").replace(" ", "")
    mult = 1
    for suffix, m in (("miliar", 10**9), ("m", 10**9), ("juta", 10**6), ("jt", 10**6), ("rb", 10**3), ("k", 10**3)):
        if t.endswith(suffix):
            t, mult = t[: -len(suffix)], m
            break
    if mult > 1:
        return int(round(float(t.replace(",", ".")) * mult))
    digits = t.replace(".", "").replace(",", "")
    if not digits.isdigit():
        raise ValueError(f"not an amount: {text!r}")
    return int(digits)
