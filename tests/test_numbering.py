import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from heroez_ops.db import Store
from heroez_ops.numbering import Numbering, NumberError, pattern, render

WIB = ZoneInfo("Asia/Jakarta")
NOV = dt.date(2026, 11, 3)


@pytest.fixture
def n():
    return Numbering(Store(":memory:"), WIB)


def test_each_type_has_its_own_counter_and_format(n):
    assert n.peek("kontrak", NOV) == "001/HRZ-KTR/XI/2026"
    assert n.take("kontrak", "rina", NOV) == "001/HRZ-KTR/XI/2026"
    assert n.take("kontrak", "rina", NOV) == "002/HRZ-KTR/XI/2026"
    assert n.take("spk", "rina", NOV) == "001/HRZ-SPK/XI/2026"           # SPK counts separately
    assert [r["number"] for r in n.register("kontrak")] == ["002/HRZ-KTR/XI/2026", "001/HRZ-KTR/XI/2026"]


def test_continue_from_the_last_number_used_before_the_app(n):
    assert n.set_last("kontrak", "047", "sari", NOV) == 47
    assert n.peek("kontrak", NOV) == "048/HRZ-KTR/XI/2026"
    assert n.set_last("spk", "112/HRZ-SPK/X/2026", "sari", NOV) == 112          # a full number works too
    assert n.take("spk", "sari", NOV) == "113/HRZ-SPK/XI/2026"
    with pytest.raises(NumberError):
        n.set_last("spk", "SPK nomor sekian", "sari", NOV)


def test_external_numbers_are_never_issued_again(n):
    n.register_external("kontrak", "005/HRZ-KTR/XI/2026", "sari", "kontrak kertas klien")
    assert n.peek("kontrak", NOV) == "006/HRZ-KTR/XI/2026"
    with pytest.raises(NumberError):
        n.register_external("kontrak", "005/HRZ-KTR/XI/2026", "sari")
    # An odd external number that doesn't follow the format is still blocked from reuse.
    n.register_external("kontrak", "KTR-LAMA-9", "sari")
    assert n.store.one("SELECT seq FROM doc_numbers WHERE number='KTR-LAMA-9'")["seq"] is None


def test_hand_typed_number_is_checked_and_cancel_keeps_it_reserved(n):
    first = n.take("kontrak", "rina", NOV)
    with pytest.raises(NumberError):
        n.claim("kontrak", first, "budi")                                   # already used
    assert n.claim("kontrak", "099/HRZ-KTR/XI/2026", "budi") == "099/HRZ-KTR/XI/2026"
    n.cancel(first, "sari", "salah klien")
    row = n.store.one("SELECT * FROM doc_numbers WHERE number=?", (first,))
    assert row["status"] == "batal" and "salah klien" in row["note"]
    assert n.peek("kontrak", NOV) == "002/HRZ-KTR/XI/2026"                   # a cancelled number is not reused


def test_reset_rules_and_custom_types(n):
    n.save_type("spk", "SPK", "SPK-{yy}{month}-{seq:04d}", "monthly", "sari")
    assert n.take("spk", "x", NOV) == "SPK-2611-0001"
    assert n.take("spk", "x", dt.date(2026, 12, 1)) == "SPK-2612-0001"      # new month, back to 1
    assert n.take("kontrak", "x", dt.date(2026, 12, 31)) == "001/HRZ-KTR/XII/2026"
    assert n.take("kontrak", "x", dt.date(2027, 1, 2)) == "001/HRZ-KTR/I/2027"   # yearly reset
    n.save_type("quotation", "Quotation", "Q/{year}/{seq}", "never", "sari")
    assert n.take("quotation", "x", NOV) == "Q/2026/1" and n.take("quotation", "x", dt.date(2027, 3, 1)) == "Q/2027/2"
    for bad in ("HRZ/{year}", "{seq}/{foo}", "{seq:03d}/{"):
        with pytest.raises(NumberError):
            n.save_type("spk", "SPK", bad, "yearly", "sari")


def test_old_counter_and_old_format_carry_over():
    store = Store(":memory:")
    store.set_kv("contract_seq:2026", "12")                                  # counter of earlier versions
    n = Numbering(store, WIB, kontrak_format="{seq:03d}/KTR/{year}")
    assert n.peek("kontrak", NOV) == "013/KTR/2026"


def test_pattern_round_trip():
    fmt = "{seq:03d}/HRZ-SPK/{roman_month}/{year}"
    m = pattern(fmt).match(render(fmt, 7, NOV))
    assert m and m.group("seq") == "007" and m.group("roman_month") == "XI" and m.group("year") == "2026"
