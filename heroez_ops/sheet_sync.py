"""Hourly sync of the KOL list, availability and FAQ from one Google Sheet.

The sheet is shared as "anyone with the link can view"; the app downloads it as
.xlsx (no Google account needed) and reads the tabs it recognises:
  * KOL / Talent / Daftar KOL   -> the roster (same columns as an upload)
  * Jadwal / Schedule           -> busy dates (Nama, Mulai, Selesai)
  * FAQ                         -> Pertanyaan | Jawaban | Jawaban EN | Otomatis
Other tabs are ignored. The sheet becomes the source of truth: a manual upload is
overwritten by the next sync while a sheet URL is set.
"""
from __future__ import annotations

import io
import json
import logging
import re
import threading
import time
import uuid

import httpx

from . import talents as tl
from .db import Store
from .kb import KB
from .safety import is_public_https

log = logging.getLogger(__name__)

SYNC_EVERY_SECONDS = 60 * 60
MAX_BYTES = 15 * 1024 * 1024
_lock = threading.Lock()


def export_url(share_url: str) -> str | None:
    """https://docs.google.com/spreadsheets/d/<id>/edit... -> the xlsx export address."""
    m = re.search(r"docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]{20,})", share_url or "")
    return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=xlsx" if m else None


def read_workbook(data: bytes) -> dict[str, list[dict[str, str]]]:
    """{sheet title: rows} for every sheet, rows keyed by the header row."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out = {}
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        header = [str(h or "").strip() for h in rows[0]]
        out[ws.title] = [{header[i]: tl._cell(v) for i, v in enumerate(r) if i < len(header) and header[i]}
                         for r in rows[1:] if any(v not in (None, "") for v in r)]
    return out


def _find(sheets: dict, *patterns: str) -> list[dict] | None:
    for title, rows in sheets.items():
        if any(re.search(p, title, re.I) for p in patterns):
            return rows
    return None


def import_faq(store: Store, rows: list[dict[str, str]], who: str) -> tuple[int, list[str]]:
    """FAQ tab: Pertanyaan (variants separated by newlines or ';'), Jawaban, Jawaban EN, Otomatis (ya/yes/1)."""
    kb = KB(store)
    old = {f["id"]: f for f in kb.faqs()}
    by_q = {q.lower(): f["id"] for f in old.values() for q in f.get("questions", [])}
    out, problems = [], []
    header = list(dict.fromkeys(k for r in rows for k in r))
    qcol = next((h for h in header if re.search(r"pertanyaan|question", h, re.I)), None)
    acol = next((h for h in header if re.search(r"^jawaban$|^answer$|jawaban id|answer id", h, re.I)), None)
    ecol = next((h for h in header if re.search(r"en|english|inggris", h, re.I) and h != acol), None)
    ocol = next((h for h in header if re.search(r"otomatis|auto", h, re.I)), None)
    if not (qcol and acol):
        return 0, ["Tab FAQ butuh kolom “Pertanyaan” dan “Jawaban”."]
    for i, r in enumerate(rows, start=2):
        qs = [q.strip() for q in re.split(r"[\n;]", r.get(qcol, "")) if q.strip()]
        ans = (r.get(acol, "") or "").strip()
        if not qs or not ans:
            problems.append(f"FAQ baris {i}: pertanyaan atau jawaban kosong, dilewati")
            continue
        fid = next((by_q[q.lower()] for q in qs if q.lower() in by_q), None) or uuid.uuid4().hex[:8]
        auto = str(r.get(ocol, "") if ocol else "").strip().lower() in ("ya", "yes", "y", "1", "true", "✓", "otomatis")
        out.append({"id": fid, "questions": qs, "answer_id": ans, "answer_en": (r.get(ecol, "") or "").strip() if ecol else "",
                    "auto_send": auto, "updated_at": int(time.time())})
    kb.set("faq", out, who)
    return len(out), problems


def sync(store: Store, http: httpx.Client | None = None, now: float | None = None, check_host=is_public_https) -> dict:
    http = http or httpx.Client(timeout=30, follow_redirects=False)
    kb = KB(store)
    status = {"at": int(now or time.time()), "ok": False, "kol": 0, "jadwal": 0, "faq": 0, "errors": [], "tabs": []}
    url = export_url(kb.get("sheet_url", "") or "")
    if not url:
        status["errors"].append("alamat Google Sheet tidak dikenali")
    else:
        ok, why = check_host(url)
        if not ok:
            status["errors"].append(why)
        else:
            try:
                with http.stream("GET", url) as r:
                    if r.status_code in (302, 303, 307) or r.status_code >= 400:
                        raise ValueError("sheet belum dibagikan sebagai “siapa saja dengan link bisa melihat”")
                    buf = bytearray()
                    for chunk in r.iter_bytes():
                        buf.extend(chunk)
                        if len(buf) > MAX_BYTES:
                            raise ValueError("sheet terlalu besar (maks. 15 MB)")
                sheets = read_workbook(bytes(buf))
                status["tabs"] = list(sheets)
                kol = _find(sheets, r"kol", r"talent", r"roster")
                jadwal = _find(sheets, r"jadwal", r"schedule", r"kalender", r"availab")
                faq = _find(sheets, r"faq", r"tanya")
                if kol is not None:
                    n, problems = tl.import_roster(store, kol, "sheet")
                    status["kol"], status["errors"] = n, status["errors"] + problems[:5]
                else:
                    status["errors"].append("tab KOL tidak ditemukan (beri nama tab “KOL”)")
                if jadwal is not None:
                    n, problems = tl.import_availability(store, jadwal, "sheet")
                    status["jadwal"], status["errors"] = n, status["errors"] + problems[:5]
                if faq is not None:
                    n, problems = import_faq(store, faq, "sheet")
                    status["faq"], status["errors"] = n, status["errors"] + problems[:5]
                status["ok"] = kol is not None
            except (httpx.HTTPError, ValueError, KeyError) as e:
                status["errors"].append(str(e) if isinstance(e, ValueError) else f"tidak bisa diunduh ({type(e).__name__})")
            except Exception:
                log.exception("sheet sync failed")
                status["errors"].append("file sheet tidak bisa dibaca")
    store.set_kv("sheet_status", json.dumps(status, ensure_ascii=False))
    store.set_kv("sheet_last_try", str(int(now or time.time())))
    if status["ok"]:
        store.audit("system", "sheet_sync", None, {k: status[k] for k in ("kol", "jadwal", "faq")})
    return status


def maybe_sync(store: Store, now: float | None = None) -> None:
    now = now or time.time()
    if not (KB(store).get("sheet_url") or ""):
        return
    if now - int(store.get_kv("sheet_last_try", "0") or 0) < SYNC_EVERY_SECONDS:
        return
    if not _lock.acquire(blocking=False):
        return
    store.set_kv("sheet_last_try", str(int(now)))

    def run():
        try:
            sync(store, now=now)
        except Exception:
            log.exception("sheet sync thread failed")
        finally:
            _lock.release()

    threading.Thread(target=run, name="sheet-sync", daemon=True).start()
