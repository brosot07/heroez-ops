"""KOL roster with rate cards, and their availability.

Uploaded as Excel (.xlsx) or CSV from the "Pengetahuan" page. Column names are
matched loosely, in Bahasa or English. Private columns (phone, handler, internal
notes) are kept for routing only and never shown to the AI or to clients.

The availability sheet needs only talent name, start date and end date. Any
other column (brand, job, notes) is ignored and never stored, so the AI can say
"available / not available" but cannot reveal what a talent is working on.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import time

from .db import Store
from .doctemplate import BULAN
from .phone import normalize
from .terbilang import parse_amount, rupiah

PRIVATE = re.compile(r"(hp|phone|telp|telepon|wa\b|whatsapp|no\.? ?wa|handler|pic|internal|private|rahasia|email|rekening|bank|npwp|ktp|nik|alamat|kalender|calendar|ical)", re.I)
CALENDAR = re.compile(r"(kalender|calendar|ical)", re.I)
NAME = re.compile(r"^(nama|name|talent|kol)( talent| kol)?$", re.I)
RATE = re.compile(r"(rate|harga|fee|tarif)", re.I)
FOLLOW = re.compile(r"(follower|subscriber|subs)", re.I)
HANDLER = re.compile(r"^(handler|pic|admin)$", re.I)
PHONE = re.compile(r"(hp|phone|telp|whatsapp|no\.? ?wa|^wa$)", re.I)


def read_table(data: bytes, filename: str) -> list[dict[str, str]]:
    """Rows of an .xlsx (first sheet) or .csv as dicts keyed by header."""
    if filename.lower().endswith(".xlsx"):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        rows = list(wb.worksheets[0].iter_rows(values_only=True))
        if not rows:
            return []
        header = [str(h or "").strip() for h in rows[0]]
        out = []
        for r in rows[1:]:
            if any(v not in (None, "") for v in r):
                out.append({header[i]: _cell(v) for i, v in enumerate(r) if i < len(header) and header[i]})
        return out
    text = data.decode("utf-8-sig", errors="replace")
    dialect = csv.Sniffer().sniff(text[:2000], delimiters=",;\t") if text.strip() else csv.excel
    return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(io.StringIO(text), dialect=dialect)]


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (dt.datetime, dt.date)):
        return (v.date() if isinstance(v, dt.datetime) else v).isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def parse_date(text: str) -> dt.date | None:
    t = (text or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d.%m.%Y"):
        try:
            return dt.datetime.strptime(t, fmt).date()
        except ValueError:
            pass
    m = re.match(r"^(\d{1,2}) (\w+) (\d{4})$", t)
    if m and m.group(2).capitalize() in BULAN:
        return dt.date(int(m.group(3)), BULAN.index(m.group(2).capitalize()) + 1, int(m.group(1)))
    return None


def import_roster(store: Store, rows: list[dict[str, str]], who: str) -> tuple[int, list[str]]:
    """Replace the roster. Returns (count, problems in plain language)."""
    problems, talents = [], []
    if not rows:
        return 0, ["File kosong."]
    header = list(dict.fromkeys(k for r in rows for k in r))   # every column in the file
    name_col = next((h for h in header if NAME.match(h.strip())), None)
    if not name_col:
        return 0, ["Kolom nama tidak ditemukan (beri judul kolom “Nama”)."]
    phone_col = next((h for h in header if PHONE.search(h)), None)
    handler_col = next((h for h in header if HANDLER.match(h.strip())), None)
    cal_col = next((h for h in header if CALENDAR.search(h)), None)
    for i, r in enumerate(rows, start=2):
        name = r.get(name_col, "").strip()
        if not name:
            continue
        public, rates = {}, {}
        for h, v in r.items():
            if h in (name_col, phone_col, handler_col) or not str(v).strip():
                continue
            if PRIVATE.search(h):
                continue                           # never shown to AI or clients
            if RATE.search(h):
                try:
                    rates[h] = parse_amount(v)
                except ValueError:
                    problems.append(f"Baris {i} ({name}): {h} “{v}” bukan nominal, dilewati")
            else:
                public[h] = v
        talents.append((name, json.dumps({"info": public, "rates": rates}, ensure_ascii=False),
                        normalize(r.get(phone_col, ""), local=True) if phone_col else "",
                        (r.get(handler_col, "") or "").strip() if handler_col else "",
                        (r.get(cal_col, "") or "").strip() if cal_col else ""))
    with store.tx():
        store.x("DELETE FROM talents")
        for name, data, phone, handler, cal in talents:
            store.x("INSERT INTO talents(name,data,phone,handler,calendar_url,updated_at) VALUES(?,?,?,?,?,?)",
                    (name, data, phone, handler, cal, int(time.time())))
        store.x("DELETE FROM talent_busy WHERE talent_id NOT IN (SELECT id FROM talents)")
        store.x("DELETE FROM kv WHERE key='calendar_last_try'")
    store.audit(who, "talents_import", None, {"count": len(talents)})
    return len(talents), problems


def import_availability(store: Store, rows: list[dict[str, str]], who: str) -> tuple[int, list[str]]:
    """Replace booked periods. Only talent name, start and end are read and stored."""
    problems = []
    if not rows:
        return 0, ["File kosong."]
    header = list(dict.fromkeys(k for r in rows for k in r))   # every column in the file
    name_col = next((h for h in header if NAME.match(h.strip()) or re.search(r"talent|kol|nama", h, re.I)), None)
    start_col = next((h for h in header if re.search(r"mulai|start|dari|from|tanggal", h, re.I)), None)
    end_col = next((h for h in header if re.search(r"selesai|end|sampai|until|to$", h, re.I) and h != start_col), None)
    if not (name_col and start_col):
        return 0, ["Butuh kolom “Nama”, “Mulai” (dan “Selesai” untuk lebih dari satu hari)."]
    ids = {r["name"].strip().lower(): r["id"] for r in store.q("SELECT id, name FROM talents")}
    periods = []
    for i, r in enumerate(rows, start=2):
        name = r.get(name_col, "").strip()
        if not name:
            continue
        tid = ids.get(name.lower())
        start = parse_date(r.get(start_col, ""))
        end = parse_date(r.get(end_col, "")) if end_col and r.get(end_col) else start
        if not tid:
            problems.append(f"Baris {i}: talent “{name}” tidak ada di daftar KOL")
        elif not start or not end or end < start:
            problems.append(f"Baris {i} ({name}): tanggal tidak terbaca")
        else:
            periods.append((tid, start.isoformat(), end.isoformat()))
    with store.tx():
        store.x("DELETE FROM talent_busy WHERE source='upload'")        # Google Calendar dates are kept
        for p in periods:
            store.x("INSERT INTO talent_busy(talent_id,start,end,source) VALUES(?,?,?,'upload')", p)
    store.audit(who, "availability_import", None, {"periods": len(periods)})
    return len(periods), problems


def _range(a: dt.date, b: dt.date) -> str:
    if a == b:
        return f"{a.day} {BULAN[a.month - 1]} {a.year}"
    if a.year == b.year and a.month == b.month:
        return f"{a.day}–{b.day} {BULAN[a.month - 1]} {a.year}"
    return f"{a.day} {BULAN[a.month - 1]} {a.year} – {b.day} {BULAN[b.month - 1]} {b.year}"


def context_for_ai(store: Store, today: dt.date, days_ahead: int = 120) -> str:
    """Roster for AI drafts: public info, rates and busy dates only (no reasons, no contacts)."""
    talents = store.q("SELECT id, name, data FROM talents ORDER BY name")
    if not talents:
        return ""
    horizon = today + dt.timedelta(days=days_ahead)
    busy: dict[int, list[str]] = {}
    for b in store.q("SELECT talent_id, start, end FROM talent_busy WHERE end>=? AND start<=? ORDER BY start",
                     (today.isoformat(), horizon.isoformat())):
        busy.setdefault(b["talent_id"], []).append(_range(dt.date.fromisoformat(b["start"]), dt.date.fromisoformat(b["end"])))
    lines = []
    for t in talents:
        d = json.loads(t["data"])
        info = "; ".join(f"{k}: {v}" for k, v in d["info"].items())
        rates = "; ".join(f"{k}: {rupiah(v)}" for k, v in d["rates"].items()) or "-"
        not_free = ", ".join(busy.get(t["id"], [])) or "tidak ada jadwal terisi"
        lines.append(f"- {t['name']} | {info} | Rate: {rates} | Tidak tersedia: {not_free}")
    return (f"## Daftar KOL (hari ini {_range(today, today)}; jadwal sampai {_range(horizon, horizon)})\n"
            "Ketersediaan hanya boleh disebut tersedia / tidak tersedia. Jangan pernah menyebut alasannya.\n"
            + "\n".join(lines))


def roster_for_routing(store: Store) -> dict[str, str]:
    """phone -> handler (admin key) for talents in the uploaded roster."""
    return {r["phone"]: r["handler"] for r in store.q("SELECT phone, handler FROM talents WHERE phone!=''")}


# -- answering brands (deterministic: numbers come from the sheets, never from the AI) --

CATEGORY_WORDS = {"beauty": ["beauty", "skincare", "makeup", "make up", "kecantikan", "cosmetic"],
                  "fnb": ["food", "f&b", "fnb", "kuliner", "makanan", "minuman", "culinary"],
                  "gaming": ["gaming", "game", "esport"], "fashion": ["fashion", "style", "ootd", "outfit"],
                  "tech": ["tech", "gadget", "teknologi", "technology"]}
PLATFORM_WORDS = {"instagram": ["ig", "instagram", "reels", "feed", "story"], "tiktok": ["tiktok", "tt"],
                  "youtube": ["youtube", "yt", "shorts"]}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower().replace("@", " ")).strip()


def _handles(info: dict) -> list[str]:
    return [str(v) for k, v in info.items() if re.search(r"handle|username|akun|ig|instagram|tiktok|youtube|channel", k, re.I)]


def find_talents(store: Store, queries: list[str]) -> tuple[list, list[str]]:
    """Match names/@handles the client wrote to the KOL list. Returns (rows, names not found)."""
    rows = store.q("SELECT id, name, data FROM talents")
    found, missing = [], []
    for q in queries:
        nq = _norm(q)
        if not nq:
            continue
        hit = None
        for r in rows:
            info = json.loads(r["data"])["info"]
            names = [_norm(r["name"])] + [_norm(h) for h in _handles(info)]
            if any(nq == n or (len(nq) >= 4 and nq in n) or (len(n) >= 4 and n in nq) for n in names if n):
                hit = r
                break
        if hit and hit["id"] not in {f["id"] for f in found}:
            found.append(hit)
        elif not hit:
            missing.append(q)
    return found, missing


def available(store: Store, talent_id: int, start: dt.date, end: dt.date) -> bool:
    return not store.one("SELECT 1 FROM talent_busy WHERE talent_id=? AND start<=? AND end>=?",
                         (talent_id, end.isoformat(), start.isoformat()))


def _followers(info: dict) -> int:
    """Largest follower count in the row: '120rb', '120K', '1,2M', '1.2jt', '120000'."""
    best = 0
    for k, v in info.items():
        if not FOLLOW.search(k):
            continue
        t = re.sub(r"\s+", "", str(v).lower())
        t = re.sub(r"m$", "jt", t)                # followers: M = million, not miliar
        try:
            n = parse_amount(t)
        except ValueError:
            n = int(re.sub(r"\D", "", t) or 0)
        best = max(best, n)
    return best


def recommend(store: Store, category: str, platforms: list[str], dates: tuple[dt.date, dt.date] | None, limit: int = 5) -> list:
    words = CATEGORY_WORDS.get(category, [])
    if not words:
        return []
    picks = []
    for r in store.q("SELECT id, name, data FROM talents"):
        d = json.loads(r["data"])
        text = " ".join(str(v) for v in d["info"].values()).lower()
        if not any(w in text for w in words):
            continue
        if dates and not available(store, r["id"], *dates):
            continue
        picks.append((_followers(d["info"]), r))
    picks.sort(key=lambda p: -p[0])
    return [r for _, r in picks[:limit]]


def _niche_words(info: dict) -> set[str]:
    words = set()
    for k, v in info.items():
        if re.search(r"niche|kategori|category|genre|topik|topic", k, re.I):
            words |= {w.strip().lower() for w in re.split(r"[,/;&]| dan ", str(v)) if w.strip()}
    return words


def alternatives(store: Store, unavailable: list, category: str, dates: tuple[dt.date, dt.date],
                 exclude: set[int], limit: int = 3) -> list:
    """Cross-sell: KOLs in the same niche (or the brand's category) who ARE free on those dates."""
    wanted = set(CATEGORY_WORDS.get(category, []))
    for r in unavailable:
        wanted |= _niche_words(json.loads(r["data"])["info"])
    if not wanted:
        return []
    picks = []
    for r in store.q("SELECT id, name, data FROM talents"):
        if r["id"] in exclude or not available(store, r["id"], *dates):
            continue
        info = json.loads(r["data"])["info"]
        text = " ".join(str(v) for v in info.values()).lower()
        if _niche_words(info) & wanted or any(w in text for w in wanted):
            picks.append((_followers(info), r))
    picks.sort(key=lambda p: -p[0])
    return [r for _, r in picks[:limit]]


def _rate_label(col: str) -> str:
    return re.sub(r"^\s*(rate|harga|fee|tarif)\s*[:\-]?\s*", "", col, flags=re.I).strip() or col


def talent_line(store: Store, row, platforms: list[str], dates: tuple[dt.date, dt.date] | None, lang: str = "id") -> str:
    d = json.loads(row["data"])
    rates = d["rates"]
    if platforms:
        wanted = [w for p in platforms for w in PLATFORM_WORDS.get(p, [])]
        chosen = {k: v for k, v in rates.items() if any(re.search(rf"\b{w}\b", k, re.I) for w in wanted)}
        rates = chosen or rates
    price = " · ".join(f"{_rate_label(k)} {rupiah(v)}" for k, v in rates.items()) or (
        "rate menyusul dari admin" if lang == "id" else "rate to follow from our admin")
    handle = next((h for h in _handles(d["info"]) if h.strip()), "")
    line = f"• *{row['name']}*" + (f" ({handle})" if handle else "") + f": {price}"
    if dates:
        span = _range(*dates)
        ok = available(store, row["id"], *dates)
        if lang == "en":
            line += f" — ✅ available {span}" if ok else " — ❌ not available on those dates"
        else:
            line += f" — ✅ tersedia {span}" if ok else " — ❌ tidak tersedia di tanggal tersebut"
    return line
