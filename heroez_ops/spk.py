"""SPK automation: drop a job's documents, the AI fills a SOW table, a person checks it, and the app
makes one SPK per talent (and optionally the brand contract) from approved templates.

The AI only reads the documents into the table. Documents are made by filling the approved Word
templates (doctemplate.py); clause text never comes from the AI. Numbers come from numbering.py.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
from pathlib import Path

from .db import Store

log = logging.getLogger(__name__)

PROMPT = (Path(__file__).resolve().parent / "prompts" / "spk_extract_v1.md").read_text(encoding="utf-8")
MODEL = "claude-sonnet-5-5"
MAX_FILES, MAX_JOB_BYTES = 10, 25 * 1024 * 1024
ROW_KEYS = ("talent", "platform", "deliverable", "qty", "posting_date", "client_fee", "talent_fee", "notes")

SCHEMA = {
    "type": "object",
    "properties": {
        "client_name": {"type": "string"},
        "campaign": {"type": "string"},
        "period_start": {"type": "string"},
        "period_end": {"type": "string"},
        "payment_terms": {"type": "string"},
        "rows": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "talent": {"type": "string"}, "platform": {"type": "string"}, "deliverable": {"type": "string"},
                "qty": {"type": "integer"}, "posting_date": {"type": "string"},
                "client_fee_idr": {"type": "integer"}, "talent_fee_idr": {"type": "integer"}, "notes": {"type": "string"},
            },
            "required": ["talent", "platform", "deliverable", "qty", "posting_date", "client_fee_idr", "talent_fee_idr", "notes"],
            "additionalProperties": False}},
        "uncertain": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["client_name", "campaign", "period_start", "period_end", "payment_terms", "rows", "uncertain"],
    "additionalProperties": False,
}


class SpkError(Exception):
    pass


# ------------------------------------------------------------------ AI reading

def analyze(blocks: list[dict], api_key: str, store: Store, client=None) -> dict:
    """Content blocks (doc_import.extract) → job data + notes. Raises SpkError with a message for people."""
    import anthropic
    from . import doc_import
    client = client or anthropic.Anthropic(api_key=api_key, timeout=600.0, max_retries=2)
    if not any(b["type"] != "text" or b.get("text", "").strip() for b in blocks):
        raise SpkError("Dokumen kosong atau tidak terbaca.")
    content = blocks + [{"type": "text", "text": "Isi struktur pekerjaan (SOW) dari dokumen-dokumen di atas."}]
    try:
        resp = client.beta.messages.create(
            model=MODEL, max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=PROMPT,
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.AuthenticationError as e:
        raise SpkError("Claude API key ditolak. Cek di halaman Pengaturan.") from e
    except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APIStatusError) as e:
        raise SpkError(f"Claude tidak bisa dihubungi ({type(e).__name__}). Coba lagi.") from e
    doc_import._meter(store, resp.usage)
    if resp.stop_reason != "end_turn":
        raise SpkError(f"AI berhenti tanpa hasil ({resp.stop_reason}).")
    return clean(json.loads(next(b.text for b in resp.content if b.type == "text")))


def _date(v) -> str:
    v = str(v or "").strip()
    try:
        return dt.date.fromisoformat(v).isoformat()
    except ValueError:
        return ""


def _int(v) -> int:
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def clean(raw: dict) -> dict:
    rows = []
    for r in raw.get("rows", [])[:100]:
        row = {"talent": str(r.get("talent", "")).strip()[:80], "platform": str(r.get("platform", "")).strip()[:30],
               "deliverable": str(r.get("deliverable", "")).strip()[:120], "qty": _int(r.get("qty")) or 1,
               "posting_date": _date(r.get("posting_date")),
               "client_fee": _int(r.get("client_fee_idr", r.get("client_fee"))),
               "talent_fee": _int(r.get("talent_fee_idr", r.get("talent_fee"))),
               "notes": str(r.get("notes", "")).strip()[:200]}
        if row["talent"] or row["deliverable"]:
            rows.append(row)
    return {"client_name": str(raw.get("client_name", "")).strip()[:160], "campaign": str(raw.get("campaign", "")).strip()[:160],
            "period_start": _date(raw.get("period_start")), "period_end": _date(raw.get("period_end")),
            "payment_terms": str(raw.get("payment_terms", "")).strip()[:600], "rows": rows,
            "uncertain": [str(u).strip()[:200] for u in raw.get("uncertain", []) if str(u).strip()][:20]}


# ------------------------------------------------------------------ job data

def talents_of(rows: list[dict]) -> list[str]:
    seen: dict[str, str] = {}
    for r in rows:
        name = (r.get("talent") or "").strip()
        if name and name.lower() not in seen:
            seen[name.lower()] = name
    return list(seen.values())


def apply_commission(rows: list[dict], pct: float) -> int:
    """Fill empty talent fees as client fee minus Heroez's commission. Returns how many were filled."""
    n = 0
    for r in rows:
        if not r.get("talent_fee") and r.get("client_fee") and 0 <= pct < 100:
            r["talent_fee"] = int(round(r["client_fee"] * (100 - pct) / 100))
            n += 1
    return n


# ------------------------------------------------------------------ template filling
# Template fields are found by the AI per template, with free keys and labels. Map them to the job
# by meaning; anything not recognised stays empty for the person to fill in.

_IDENTITY = ("alamat", "address", "npwp", "nik", "ktp", "rekening", "account", "bank", "email", "telepon", "phone",
             "hp", "whatsapp", "jabatan", "title", "position", "wakil", "representative", "direktur", "director")
# "Pihak pertama/kedua" is deliberately not mapped: which party is Heroez differs per template.
_PARTIES = [
    ("talent", ("talent", "kol", "influencer", "kreator", "creator")),
    ("client", ("client", "klien", "brand", "perusahaan_klien", "customer", "pelanggan", "advertiser")),
]
_DATES = [
    ("period_start", ("mulai", "start", "awal", "from")),
    ("period_end", ("selesai", "end", "akhir", "berakhir", "until", "sampai")),
]
_TEXTS = [
    ("period", ("periode", "period", "durasi", "duration", "jangka")),
    ("campaign", ("campaign", "kampanye", "project", "proyek", "program", "judul")),
    ("payment", ("payment", "pembayaran", "termin", "terms", "bayar")),
]
_COLS = [
    ("no", ("no", "nomor", "number", "#")),
    ("talent", ("talent", "kol", "influencer", "kreator", "creator", "nama")),
    ("platform", ("platform", "media", "channel", "kanal")),
    ("qty", ("qty", "jumlah", "quantity", "kuantitas", "jml", "pcs")),
    ("posting_date", ("tanggal", "date", "posting", "tayang", "upload", "jadwal", "schedule")),
    ("fee", ("fee", "harga", "rate", "biaya", "nilai", "amount", "price", "total", "honor", "remunerasi")),
    ("notes", ("catatan", "keterangan", "notes", "note", "remark")),
    ("deliverable", ("deliverable", "konten", "content", "jenis", "item", "deskripsi", "description", "format",
                     "pekerjaan", "scope", "output")),
]


def _tokens(*texts: str) -> set[str]:
    return {t for s in texts for t in re.split(r"[^a-z0-9#]+", (s or "").lower()) if t}


def field_meaning(key: str, label: str, group: str, ftype: str = "text") -> str | None:
    tok = _tokens(key, label)
    joined = f"{key} {label}".lower()
    if ftype == "date":
        for g, words in _DATES:
            if tok & set(words):
                return g
        return None                                    # e.g. signing date: today
    party = next((g for g, words in _PARTIES if any(w in joined for w in words)), None)
    if party is None and group == "client":
        party = "client"
    if tok & set(_IDENTITY) or any(w in joined for w in ("alamat", "address", "rekening", "npwp")):
        if party == "talent" and tok & {"telepon", "phone", "hp", "whatsapp"}:
            return "talent_phone"
        return None                                    # identity details: a person fills them in
    for g, words in _TEXTS[:1]:
        if any(w in joined for w in words):
            return g
    if party:
        return party
    for g, words in _TEXTS[1:]:
        if any(w in tok or w in joined for w in words):
            return g
    return None


def column_meaning(key: str, label: str) -> str | None:
    tok = _tokens(key, label)
    for g, words in _COLS:
        if tok & set(words):
            return g
    return None


def prefill(spec: dict, job: dict, talent: str | None, doc_type: str, phone_of=None,
            today: dt.date | None = None, profile: dict | None = None) -> tuple[dict, list[dict]]:
    """Values and SOW rows for one document. SPK (talent given): that talent's rows, talent fees.
    Brand contract: all rows, client fees."""
    today = today or dt.date.today()
    rows = [r for r in job.get("rows", []) if talent is None or (r.get("talent") or "").strip().lower() == talent.lower()]
    fee_key = "talent_fee" if talent is not None and doc_type != "kontrak" else "client_fee"
    values: dict = {}
    for fd in spec.get("fields", []):
        if fd["type"] in ("money_words", "contract_number"):
            continue
        meaning = field_meaning(fd["key"], fd.get("label", ""), fd.get("group", ""), fd["type"])
        v = ""
        if meaning == "talent" and talent:
            v = talent
        elif meaning == "talent_phone" and talent and phone_of:
            v = phone_of(talent) or ""
        elif meaning == "client":
            v = job.get("client_name", "")
        elif meaning == "period" and job.get("period_start"):
            from .doctemplate import tanggal
            a, b = job["period_start"], job.get("period_end") or job["period_start"]
            v = tanggal(a) if a == b else f"{tanggal(a)} – {tanggal(b)}"
        elif meaning == "campaign":
            v = job.get("campaign", "")
        elif meaning == "period_start":
            v = job.get("period_start", "")
        elif meaning == "period_end":
            v = job.get("period_end", "")
        elif meaning == "payment":
            v = job.get("payment_terms", "")
        if not v and profile and profile.get(fd["key"]) and fd["type"] not in ("date", "money"):
            v = profile[fd["key"]]                     # remembered from this talent's earlier SPK
        if fd["type"] == "date":
            v = _date(v) or (today.isoformat() if meaning is None else "")
        elif fd["type"] == "money":
            v = ""                                     # totals are added up from the SOW rows when left empty
        values[fd["key"]] = v
    out_rows = []
    d = spec.get("deliverables")
    if d:
        for r in rows:
            row = {}
            for c in d["columns"]:
                m = column_meaning(c["key"], c.get("label", ""))
                if m == "no":
                    continue
                if m == "fee" or (c["type"] == "money" and m is None):
                    row[c["key"]] = r.get(fee_key) or 0
                elif m == "talent":
                    row[c["key"]] = r.get("talent", "")
                elif m == "platform":
                    row[c["key"]] = r.get("platform", "")
                elif m == "qty":
                    row[c["key"]] = str(r.get("qty") or "")
                elif m == "posting_date":
                    row[c["key"]] = r.get("posting_date", "")
                elif m == "notes":
                    row[c["key"]] = r.get("notes", "")
                else:
                    row[c["key"]] = " ".join(x for x in (r.get("deliverable", ""),
                                                         f"x{r['qty']}" if (r.get("qty") or 1) > 1 and not any(
                                                             column_meaning(cc["key"], cc.get("label", "")) == "qty"
                                                             for cc in d["columns"]) else "") if x)
            out_rows.append(row)
    return values, out_rows


# ------------------------------------------------------------------ talent details remembered from earlier SPKs

def remember_talent(store: Store, talent: str, spec: dict, values: dict) -> None:
    """Keep what was typed for this talent (address, bank account, ID...) so the next SPK is filled in."""
    keep = {fd["key"]: values.get(fd["key"]) for fd in spec.get("fields", [])
            if fd["type"] in ("text", "longtext", "number") and str(values.get(fd["key"], "") or "").strip()
            and field_meaning(fd["key"], fd.get("label", ""), fd.get("group", ""), fd["type"]) in (None, "talent_phone")
            and _is_talent_detail(fd)}
    if not keep:
        return
    row = store.one("SELECT data FROM talent_profiles WHERE name_key=?", (talent.lower(),))
    data = {**(json.loads(row["data"]) if row else {}), **keep}
    import time
    store.x("INSERT INTO talent_profiles(name_key,name,data,updated_at) VALUES(?,?,?,?) ON CONFLICT(name_key) DO UPDATE SET "
            "name=excluded.name, data=excluded.data, updated_at=excluded.updated_at",
            (talent.lower(), talent, json.dumps(data, ensure_ascii=False), int(time.time())))


def _is_talent_detail(fd: dict) -> bool:
    joined = f"{fd['key']} {fd.get('label', '')}".lower()
    return any(w in joined for w in ("talent", "kol", "influencer", "kreator", "creator")) or \
        (fd.get("group") == "other" and any(w in joined for w in _IDENTITY))


def talent_profile(store: Store, talent: str) -> dict:
    row = store.one("SELECT data FROM talent_profiles WHERE name_key=?", (talent.lower(),))
    return json.loads(row["data"]) if row else {}


def as_form(spec: dict, values: dict, rows: list[dict]) -> dict:
    """The same shape the contract form posts, so the normal checks and totals apply."""
    f = {f"f_{k}": ("" if v is None else str(v)) for k, v in values.items()}
    d = spec.get("deliverables")
    for i, r in enumerate(rows):
        for c in (d["columns"] if d else []):
            if c["key"] != "no":
                f[f"r_{c['key']}_{i}"] = "" if r.get(c["key"]) in (None, 0) and c["type"] == "money" else str(r.get(c["key"], ""))
    return f


def missing_fields(spec: dict, values: dict) -> list[str]:
    return [fd["label"] for fd in spec.get("fields", []) if fd["type"] not in ("money_words", "contract_number")
            and not str(values.get(fd["key"], "") or "").strip()]


# ------------------------------------------------------------------ SOW as plain text (copy, fill in, paste back)

SOW_TEMPLATE = """Klien: PT Nama Klien
Campaign: Nama campaign
Periode: 10/11/2026 - 30/11/2026
Termin: 50% di muka, pelunasan 14 hari setelah tayang
Komisi: 20%

Talent: Nama Talent
- TikTok | Video 60 detik | 2 | 20/11/2026 | 30jt | 24jt | catatan
- Instagram | Story 3 frame | 1 | 21/11/2026 | 8jt |  |
Talent: Nama Talent Lain
- Instagram | Reels | 1 | 22/11/2026 | 18jt | 15jt | usage 3 bulan
"""
SOW_HELP = ("Satu baris per deliverable di bawah nama talent: platform | deliverable | jumlah | tanggal tayang | "
            "fee klien | fee talent | catatan. Bagian yang tidak diketahui boleh dikosongkan. "
            "Komisi (opsional) mengisi fee talent yang kosong.")

_MONTHS = {m.lower(): i for i, m in enumerate(
    ["januari", "februari", "maret", "april", "mei", "juni", "juli", "agustus", "september", "oktober", "november", "desember"], 1)}
_MONTHS.update({m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)})
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})
_MONTHS.update({"agu": 8, "okt": 10, "des": 12, "mei": 5})
_KEYS = {"klien": "client_name", "client": "client_name", "brand": "client_name",
         "campaign": "campaign", "kampanye": "campaign", "project": "campaign", "proyek": "campaign",
         "periode": "period", "period": "period", "mulai": "period_start", "start": "period_start",
         "selesai": "period_end", "end": "period_end", "termin": "payment_terms", "pembayaran": "payment_terms",
         "payment": "payment_terms", "komisi": "commission_pct", "commission": "commission_pct"}


def parse_date_text(text: str, default_year: int | None = None) -> str:
    """'20/11/2026', '20-11-26', '2026-11-20', '20 Nov 2026', '20 November' → ISO date ('' if not a date)."""
    t = (text or "").strip().lower().replace(",", " ")
    if not t:
        return ""
    iso = _date(t)
    if iso:
        return iso
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?", t)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
    else:
        m = re.fullmatch(r"(\d{1,2})\s+([a-z]+)\.?(?:\s+(\d{2,4}))?", t)
        if not m or m.group(2) not in _MONTHS:
            return ""
        d, mo, y = int(m.group(1)), _MONTHS[m.group(2)], m.group(3)
    year = int(y) + (2000 if y and len(y) == 2 else 0) if y else (default_year or dt.date.today().year)
    try:
        return dt.date(year, mo, d).isoformat()
    except ValueError:
        return ""


def parse_sow_text(text: str) -> tuple[dict, list[str]]:
    """The plain-text SOW → job data (same shape as the AI's), plus problems found, line by line."""
    from .terbilang import parse_amount

    def money(v: str, where: str) -> int:
        v = v.strip()
        if not v or v in ("-", "?"):
            return 0
        try:
            return parse_amount(v)
        except ValueError:
            errors.append(f"{where}: nominal “{v}” tidak terbaca (contoh: 30jt atau 30.000.000)")
            return 0

    data: dict = {"rows": []}
    errors: list[str] = []
    talent = ""
    for n, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, val = line.partition(":")
        k = key.strip().lower()
        if sep and not line.startswith(("-", "•", "*")) and "|" not in key:
            if k in ("talent", "kol", "influencer", "nama talent"):
                talent = val.strip()[:80]
                continue
            field = _KEYS.get(k)
            if field == "period":
                parts = re.split(r"\s+(?:-|–|s/d|sd|sampai|to)\s+|\s*[–]\s*", val.strip(), maxsplit=1)
                start = parse_date_text(parts[0])
                end = parse_date_text(parts[1], int(start[:4]) if start else None) if len(parts) > 1 else start
                if not start:
                    errors.append(f"baris {n}: periode “{val.strip()}” tidak terbaca (contoh: 10/11/2026 - 30/11/2026)")
                data["period_start"], data["period_end"] = start, end or start
            elif field in ("period_start", "period_end"):
                data[field] = parse_date_text(val)
                if not data[field]:
                    errors.append(f"baris {n}: tanggal “{val.strip()}” tidak terbaca")
            elif field == "commission_pct":
                try:
                    data["commission_pct"] = float(val.strip().rstrip("%").replace(",", ".") or 0)
                except ValueError:
                    errors.append(f"baris {n}: komisi “{val.strip()}” tidak terbaca (contoh: 20%)")
            elif field:
                data[field] = val.strip()[:600]
            continue
        if "|" in line or line.startswith(("-", "•", "*")):
            parts = [p.strip() for p in line.lstrip("-•* ").split("|")]
            row_talent = talent              # always from the "Talent:" line above (never guessed from a column)
            parts += [""] * (7 - len(parts))
            platform, deliverable, qty, date, cfee, tfee, notes = parts[:7]
            where = f"baris {n}"
            if not row_talent:
                errors.append(f"{where}: belum ada “Talent: nama” di atas baris ini")
            posting = parse_date_text(date, int(data.get("period_start", "")[:4]) if data.get("period_start") else None)
            if date and not posting:
                errors.append(f"{where}: tanggal “{date}” tidak terbaca")
            data["rows"].append({"talent": row_talent[:80], "platform": platform[:30], "deliverable": deliverable[:120],
                                 "qty": int(qty) if qty.isdigit() and int(qty) > 0 else 1, "posting_date": posting,
                                 "client_fee": money(cfee, where), "talent_fee": money(tfee, where), "notes": notes[:200]})
            continue
        errors.append(f"baris {n}: tidak dipahami: “{line[:60]}”")
    if data.get("commission_pct") is not None:
        apply_commission(data["rows"], data["commission_pct"])
    return data, errors


def looks_like_sow_text(text: str) -> bool:
    t = (text or "").lower()
    return bool(re.search(r"^\s*talent\s*:", t, re.M)) and "|" in t


def to_sow_text(data: dict) -> str:
    """Job data → the same plain-text format (to copy into WhatsApp, edit, paste back)."""
    def d(iso: str) -> str:
        return dt.date.fromisoformat(iso).strftime("%d/%m/%Y") if iso else ""

    def m(v) -> str:
        return f"{int(v):,}".replace(",", ".") if v else ""
    lines = [f"Klien: {data.get('client_name', '')}", f"Campaign: {data.get('campaign', '')}"]
    if data.get("period_start"):
        lines.append(f"Periode: {d(data['period_start'])} - {d(data.get('period_end') or data['period_start'])}")
    else:
        lines.append("Periode: ")
    lines.append(f"Termin: {data.get('payment_terms', '')}")
    if data.get("commission_pct") not in (None, ""):
        lines.append(f"Komisi: {data['commission_pct']:g}%")
    for t in talents_of(data.get("rows", [])):
        lines += ["", f"Talent: {t}"]
        for r in data["rows"]:
            if (r.get("talent") or "").lower() == t.lower():
                lines.append("- " + " | ".join([r.get("platform", ""), r.get("deliverable", ""), str(r.get("qty") or 1),
                                               d(r.get("posting_date", "")), m(r.get("client_fee")), m(r.get("talent_fee")),
                                               r.get("notes", "")]).rstrip(" |"))
    return "\n".join(lines) + "\n"
