"""Document numbers: one counter and format per document type (Kontrak, SPK, ...), and a register
of every number ever issued, including numbers taken outside the app and cancelled ones.

* Format placeholders: {seq:03d} {roman_month} {month} {year} {yy}
* Reset: yearly (counter starts at 1 each January), monthly, or never
* "Nomor terakhir": continue from the last number the office used before the app
* A number is never issued twice: the register has a UNIQUE constraint and external numbers
  bump the counter when they are higher
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time

from .db import Store

ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]
RESETS = {"yearly": "Setiap tahun", "monthly": "Setiap bulan", "never": "Tidak pernah"}
DEFAULT_TYPES = {
    "kontrak": {"label": "Kontrak", "format": "{seq:03d}/HRZ-KTR/{roman_month}/{year}", "reset": "yearly"},
    "spk": {"label": "SPK", "format": "{seq:03d}/HRZ-SPK/{roman_month}/{year}", "reset": "yearly"},
}
_FIELD = re.compile(r"\{(seq(?::0?\d+d)?|roman_month|month|year|yy)\}")


class NumberError(ValueError):
    pass


def render(fmt: str, seq: int, when: dt.date) -> str:
    try:
        return fmt.format(seq=seq, roman_month=ROMAN[when.month - 1], month=f"{when.month:02d}", year=when.year,
                          yy=f"{when.year % 100:02d}")
    except (KeyError, ValueError, IndexError) as e:
        raise NumberError(f"Format nomor tidak valid: {fmt}") from e


def pattern(fmt: str) -> re.Pattern:
    """Regex that reads a number written in this format back into its parts."""
    out, pos = "", 0
    for m in _FIELD.finditer(fmt):
        out += re.escape(fmt[pos:m.start()])
        name = m.group(1).split(":")[0]
        out += {"seq": r"(?P<seq>\d+)", "roman_month": r"(?P<roman_month>[IVX]+)", "month": r"(?P<month>\d{1,2})",
                "year": r"(?P<year>\d{4})", "yy": r"(?P<yy>\d{2})"}[name]
        pos = m.end()
    return re.compile("^" + out + re.escape(fmt[pos:]) + "$", re.I)


def validate_format(fmt: str) -> None:
    if "{seq" not in fmt:
        raise NumberError("Format harus berisi {seq} (nomor urut), mis. {seq:03d}/HRZ-SPK/{roman_month}/{year}")
    if re.sub(_FIELD, "", fmt).count("{") or re.sub(_FIELD, "", fmt).count("}"):
        raise NumberError("Format hanya boleh memakai {seq:03d}, {roman_month}, {month}, {year}, {yy}")
    render(fmt, 1, dt.date(2026, 11, 1))


class Numbering:
    def __init__(self, store: Store, tz, kontrak_format: str | None = None):
        self.store, self.tz = store, tz
        self.kontrak_format = kontrak_format

    # ---------------------------------------------------------------- types
    def types(self) -> dict[str, dict]:
        saved = json.loads(self.store.get_kv("doc_types", "{}") or "{}")
        types = {k: dict(v) for k, v in DEFAULT_TYPES.items()}
        if self.kontrak_format:
            types["kontrak"]["format"] = self.kontrak_format          # older installs: contracts.number_format
        for k, v in saved.items():
            types[k] = {**types.get(k, {}), **v}
        return types

    def type(self, key: str) -> dict:
        t = self.types().get(key)
        if not t:
            raise NumberError(f"Jenis dokumen tidak dikenal: {key}")
        return t

    def save_type(self, key: str, label: str, fmt: str, reset: str, who: str) -> None:
        key = re.sub(r"[^a-z0-9_]", "_", key.lower()).strip("_")
        if not key or not label.strip():
            raise NumberError("Kode dan nama jenis dokumen wajib diisi.")
        if reset not in RESETS:
            raise NumberError("Pilihan reset tidak dikenal.")
        validate_format(fmt)
        saved = json.loads(self.store.get_kv("doc_types", "{}") or "{}")
        saved[key] = {"label": label.strip()[:40], "format": fmt.strip(), "reset": reset}
        self.store.set_kv("doc_types", json.dumps(saved, ensure_ascii=False))
        self.store.audit(who, "doc_type_save", None, {"type": key, "format": fmt, "reset": reset})

    # ---------------------------------------------------------------- counters
    def _today(self, when: dt.date | None) -> dt.date:
        return when or dt.datetime.now(self.tz).date()

    @staticmethod
    def period(reset: str, when: dt.date) -> str:
        return {"yearly": f"{when.year}", "monthly": f"{when.year}-{when.month:02d}"}.get(reset, "all")

    def _key(self, doc_type: str, period: str) -> str:
        return f"docseq:{doc_type}:{period}"

    def last_seq(self, doc_type: str, when: dt.date | None = None) -> int:
        t, when = self.type(doc_type), self._today(when)
        per = self.period(t["reset"], when)
        val = self.store.get_kv(self._key(doc_type, per))
        if val is None and doc_type == "kontrak" and t["reset"] == "yearly":
            val = self.store.get_kv(f"contract_seq:{when.year}")         # counter of versions before 0.10
        return int(val or 0)

    def peek(self, doc_type: str, when: dt.date | None = None) -> str:
        when = self._today(when)
        seq = self.last_seq(doc_type, when) + 1
        fmt = self.type(doc_type)["format"]
        while self.store.one("SELECT 1 FROM doc_numbers WHERE number=?", (render(fmt, seq, when),)):
            seq += 1
        return render(fmt, seq, when)

    def take(self, doc_type: str, who: str, when: dt.date | None = None, document_id: int | None = None,
             note: str = "") -> str:
        """Issue the next number and record it. Safe against numbers already in the register."""
        t, when = self.type(doc_type), self._today(when)
        per = self.period(t["reset"], when)
        with self.store.tx():
            seq = self.last_seq(doc_type, when) + 1
            while True:
                number = render(t["format"], seq, when)
                if not self.store.one("SELECT 1 FROM doc_numbers WHERE number=?", (number,)):
                    break
                seq += 1
            self.store.set_kv(self._key(doc_type, per), str(seq))
            self.store.x("INSERT INTO doc_numbers(number,doc_type,seq,period,source,status,document_id,note,created_at,created_by) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (number, doc_type, seq, per, "app", "used", document_id, note[:200], int(time.time()), who))
        return number

    def claim(self, doc_type: str, number: str, who: str, document_id: int | None = None, note: str = "") -> str:
        """Use a number typed by hand on a document (e.g. the next one was edited). Must not be in use already."""
        number = number.strip()
        if not number:
            raise NumberError("Nomor kosong.")
        row = self.store.one("SELECT * FROM doc_numbers WHERE number=?", (number,))
        if row and row["status"] == "used" and (document_id is None or row["document_id"] != document_id):
            raise NumberError(f"Nomor {number} sudah dipakai ({row['note'] or row['source']}).")
        if row:
            self.store.x("UPDATE doc_numbers SET document_id=?, status='used' WHERE number=?", (document_id, number))
            return number
        self._record(doc_type, number, "manual", who, document_id, note)
        return number

    def set_last(self, doc_type: str, last: str, who: str, when: dt.date | None = None) -> int:
        """'The last number we used was 047' (or a full number like 047/HRZ-SPK/X/2026): continue after it."""
        t, when = self.type(doc_type), self._today(when)
        last = last.strip()
        if last.isdigit():
            seq = int(last)
        else:
            m = pattern(t["format"]).match(last)
            if not m:
                raise NumberError(f"“{last}” tidak cocok dengan format {t['format']}. Isi angka urutnya saja, mis. 47.")
            seq = int(m.group("seq"))
        per = self.period(t["reset"], when)
        self.store.set_kv(self._key(doc_type, per), str(seq))
        self.store.audit(who, "doc_seq_set", None, {"type": doc_type, "period": per, "last": seq})
        return seq

    def register_external(self, doc_type: str, number: str, who: str, note: str = "") -> None:
        """A number taken outside the app (client paper, manual Word file). The counter moves past it."""
        number = number.strip()
        if self.store.one("SELECT 1 FROM doc_numbers WHERE number=?", (number,)):
            raise NumberError(f"Nomor {number} sudah ada di daftar.")
        self._record(doc_type, number, "external", who, None, note)

    def cancel(self, number: str, who: str, reason: str = "") -> None:
        if not self.store.one("SELECT 1 FROM doc_numbers WHERE number=?", (number,)):
            raise NumberError("Nomor tidak ditemukan.")
        self.store.x("UPDATE doc_numbers SET status='batal', note=TRIM(COALESCE(note,'') || ' · batal: ' || ?) WHERE number=?",
                     (reason[:100], number))
        self.store.audit(who, "doc_number_cancel", None, {"number": number, "reason": reason})

    def _record(self, doc_type: str, number: str, source: str, who: str, document_id: int | None, note: str) -> None:
        t = self.type(doc_type)
        m = pattern(t["format"]).match(number)
        seq, per = None, None
        if m:
            seq = int(m.group("seq"))
            year = int(m.group("year")) if "year" in m.groupdict() and m.group("year") else \
                (2000 + int(m.group("yy")) if "yy" in m.groupdict() and m.group("yy") else None)
            month = None
            if "roman_month" in m.groupdict() and m.group("roman_month") and m.group("roman_month").upper() in ROMAN:
                month = ROMAN.index(m.group("roman_month").upper()) + 1
            elif "month" in m.groupdict() and m.group("month"):
                month = int(m.group("month"))
            today = self._today(None)
            ref = dt.date(year or today.year, month or 1, 1)
            per = self.period(t["reset"], ref)
            # A number used outside the app in the current period moves the counter past it. A number typed by
            # hand on one document does not (no gap in the sequence); the register alone keeps it from reuse.
            if source == "external" and per == self.period(t["reset"], today) and seq > self.last_seq(doc_type, today):
                self.store.set_kv(self._key(doc_type, per), str(seq))
        self.store.x("INSERT INTO doc_numbers(number,doc_type,seq,period,source,status,document_id,note,created_at,created_by) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (number, doc_type, seq, per, source, "used", document_id, note[:200], int(time.time()), who))
        self.store.audit(who, f"doc_number_{source}", None, {"number": number, "type": doc_type})

    # ---------------------------------------------------------------- register
    def register(self, doc_type: str | None = None, limit: int = 200):
        if doc_type:
            return self.store.q("SELECT * FROM doc_numbers WHERE doc_type=? ORDER BY created_at DESC, id DESC LIMIT ?",
                                (doc_type, limit))
        return self.store.q("SELECT * FROM doc_numbers ORDER BY created_at DESC, id DESC LIMIT ?", (limit,))
