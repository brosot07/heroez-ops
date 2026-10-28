"""Deal pipeline: one row per deal, moved through stages, mirrored to a Google Sheet.

Stages (editable in config, default): Lead → Proposal → Deal → Kontrak → Ditandatangani → Lunas,
plus Batal. Each stage is set by the most reliable source for it:
  * Lead: a brand inquiry handed over by the bot (or the first contract for a client)
  * Deal: the AI spots agreement language in a client message and an admin confirms with one tap
  * Kontrak: a contract is produced in the app (value from its total)
  * Ditandatangani / Lunas / Batal: a person marks it (Lunas and Batal: supervisors only)

The app's database is the source of truth; the sheet is the view ops works from. A Google
Apps Script web app (docs/deals_sheet.gs) receives the rows: key events are pushed at once,
everything else in the morning refresh.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time

import httpx

from .config import Config
from .db import Store
from .safety import is_public_https
from .terbilang import rupiah

log = logging.getLogger(__name__)

DEFAULT_STAGES = ["Lead", "Proposal", "Deal", "Kontrak", "Ditandatangani", "Lunas"]
LOST = "Batal"
PUSH_MIN_GAP = 300
ASK_AGAIN_AFTER = 3 * 86400
_push_lock = threading.Lock()


class Deals:
    def __init__(self, cfg: Config, store: Store, http: httpx.Client | None = None, check_host=is_public_https):
        self.cfg, self.store, self.check_host = cfg, store, check_host
        self._http = http

    @property
    def http(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=20, follow_redirects=False)
        return self._http

    @property
    def stages(self) -> list[str]:
        return list(self.cfg.deal_stages) or DEFAULT_STAGES

    def rank(self, stage: str) -> int:
        return self.stages.index(stage) if stage in self.stages else -1

    # ---------------------------------------------------------------- lookup
    def open_for(self, wa_id: str):
        return self.store.one("SELECT * FROM deals WHERE wa_id=? AND stage!=? AND stage!=? ORDER BY id DESC LIMIT 1",
                              (wa_id, LOST, self.stages[-1]))

    def get(self, deal_id: int):
        return self.store.one("SELECT * FROM deals WHERE id=?", (deal_id,))

    # ---------------------------------------------------------------- stage changes
    def ensure_lead(self, wa_id: str, brand: str, pic: str | None, at: int, talents: str = "", period: tuple[str, str] | None = None,
                    value: int = 0, source: str = "bot") -> int:
        """A brand inquiry: create the deal row at Lead if there is no open deal for this number."""
        d = self.open_for(wa_id) if wa_id else None
        if d:
            if brand and not d["brand"]:
                self.store.x("UPDATE deals SET brand=?, updated_at=? WHERE id=?", (brand[:120], at, d["id"]))
            return d["id"]
        contact = self.store.one("SELECT name FROM contacts WHERE wa_id=?", (wa_id,))
        did = self.store.x(
            "INSERT INTO deals(wa_id,brand,contact_name,pic,talents,period_start,period_end,value,stage,stages,source,"
            "last_activity,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (wa_id, (brand or "")[:120], (contact["name"] if contact else "") or "", pic, talents[:200],
             period[0] if period else None, period[1] if period else None, value or 0, self.stages[0],
             json.dumps({self.stages[0]: at}), source, at, at, at)).lastrowid
        self.store.audit("system", "deal_lead", wa_id, {"deal": did, "brand": brand}, at)
        self.push(reason="lead")
        return did

    def set_stage(self, deal_id: int, stage: str, who: str, at: int, value: int | None = None, note: str | None = None,
                  contract: tuple[int, str] | None = None) -> None:
        d = self.get(deal_id)
        if not d or (stage not in self.stages and stage != LOST):
            return
        stages = json.loads(d["stages"] or "{}")
        stages[stage] = at
        cols = {"stage": stage, "stages": json.dumps(stages), "updated_at": at, "last_activity": at}
        if value is not None:
            cols["value"] = int(value)
        if note:
            cols["notes"] = ((d["notes"] or "") + f"\n[{dt.datetime.fromtimestamp(at, self.cfg.tz):%d/%m %H:%M} {who}] {note}").strip()[:2000]
        if contract:
            cols["contract_id"], cols["contract_number"] = contract
        sets = ", ".join(f"{k}=?" for k in cols)
        self.store.x(f"UPDATE deals SET {sets} WHERE id=?", (*cols.values(), deal_id))
        self.store.audit(who, f"deal_stage:{stage}", d["wa_id"], {"deal": deal_id, "value": value}, at)
        self.push(reason=stage, force=stage in ("Deal", "Kontrak", "Ditandatangani", "Lunas", LOST))

    def touch(self, wa_id: str, at: int) -> None:
        self.store.x("UPDATE deals SET last_activity=? WHERE wa_id=? AND stage NOT IN (?, ?)", (at, wa_id, LOST, self.stages[-1]))

    def on_contract(self, wa_id: str | None, deal_id: int | None, client_name: str, total: int | None, contract_id: int,
                    number: str, who: str, at: int) -> int:
        """A contract was produced: the deal moves to Kontrak (created on the spot if needed)."""
        if deal_id:
            d = self.get(deal_id)
        elif wa_id:
            d = self.open_for(wa_id)
        else:
            d = None
        if not d:
            did = self.store.x(
                "INSERT INTO deals(wa_id,brand,contact_name,pic,talents,value,stage,stages,source,last_activity,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (wa_id or "", client_name[:120], "", who if self.cfg.admin(who) else None, "", total or 0, "Kontrak",
                 json.dumps({"Kontrak": at}), "contract", at, at, at)).lastrowid
            self.store.x("UPDATE deals SET contract_id=?, contract_number=? WHERE id=?", (contract_id, number, did))
            self.store.audit(who, "deal_from_contract", wa_id, {"deal": did, "contract": contract_id}, at)
            self.push(reason="Kontrak", force=True)
            return did
        if self.rank(d["stage"]) < self.rank("Kontrak"):
            self.set_stage(d["id"], "Kontrak", who, at, value=total, contract=(contract_id, number))
        else:
            self.store.x("UPDATE deals SET contract_id=?, contract_number=?, value=COALESCE(?, value), updated_at=? WHERE id=?",
                         (contract_id, number, total, at, d["id"]))
            self.push(reason="contract-update")
        return d["id"]

    # ---------------------------------------------------------------- handshake
    def propose_deal(self, wa_id: str, at: int, value: int = 0) -> int | None:
        """The AI saw agreement language. Returns the deal id to ask the admin about, or None if
        already past Deal or asked in the last few days."""
        d = self.open_for(wa_id)
        if d and (self.rank(d["stage"]) >= self.rank("Deal") or (d["deal_asked_at"] and at - d["deal_asked_at"] < ASK_AGAIN_AFTER)):
            return None
        if not d:
            contact = self.store.one("SELECT name FROM contacts WHERE wa_id=?", (wa_id,))
            conv = self.store.one("SELECT assignee FROM conversations WHERE wa_id=?", (wa_id,))
            did = self.ensure_lead(wa_id, contact["name"] if contact else "", conv["assignee"] if conv else None, at,
                                   value=value, source="chat")
        else:
            did = d["id"]
            if value and not d["value"]:
                self.store.x("UPDATE deals SET value=? WHERE id=?", (value, did))
        self.store.x("UPDATE deals SET deal_asked_at=? WHERE id=?", (at, did))
        return did

    def proposal_requested(self, wa_id: str, at: int, value: int = 0) -> int | None:
        """The client asked for a proposal/quotation: a Lead becomes Proposal (never moves a deal back)."""
        contact = self.store.one("SELECT name, kind FROM contacts WHERE wa_id=?", (wa_id,))
        if contact and contact["kind"] == "talent":
            return None
        conv = self.store.one("SELECT assignee FROM conversations WHERE wa_id=?", (wa_id,))
        did = self.ensure_lead(wa_id, contact["name"] if contact else "", conv["assignee"] if conv else None, at,
                               value=value, source="chat")
        d = self.get(did)
        if self.rank(d["stage"]) < self.rank("Proposal"):
            self.set_stage(did, "Proposal", "ai", at, value=value or None)
        return did

    def confirm_deal(self, deal_id: int, yes: bool, who: str, at: int) -> None:
        d = self.get(deal_id)
        if not d:
            return
        if yes:
            self.set_stage(deal_id, "Deal", who, at)
        else:
            self.store.audit(who, "deal_not_yet", d["wa_id"], {"deal": deal_id}, at)

    # ---------------------------------------------------------------- reporting
    def rows(self):
        return self.store.q("SELECT * FROM deals ORDER BY CASE WHEN stage=? THEN 1 ELSE 0 END, updated_at DESC", (LOST,))

    def summary_lines(self, start: int, end: int) -> list[str]:
        new = self.store.one("SELECT COUNT(*) n FROM deals WHERE created_at>=? AND created_at<?", (start, end))["n"]
        won = self.store.q("SELECT value FROM deals WHERE stage NOT IN ('Lead','Proposal',?) AND json_extract(stages,'$.Deal')>=?",
                           (LOST, start - 29 * 86400))
        by_stage = self.store.q("SELECT stage, COUNT(*) n, SUM(value) v FROM deals WHERE stage!=? AND stage!=? GROUP BY stage",
                                (LOST, self.stages[-1]))
        order = {s: i for i, s in enumerate(self.stages)}
        pipeline = " · ".join(f"{r['stage']} {r['n']}" + (f" ({rupiah(r['v'])})" if r["v"] else "")
                              for r in sorted(by_stage, key=lambda r: order.get(r["stage"], 99)))
        lines = [f"🤝 Deal: {new} lead baru kemarin · {len(won)} deal 30 hari terakhir ({rupiah(sum(w['value'] or 0 for w in won))})"]
        if pipeline:
            lines.append(f"Pipeline: {pipeline}")
        return lines

    # ---------------------------------------------------------------- Google Sheet mirror
    def sheet_rows(self) -> list[list]:
        head = ["ID", "Brand", "Kontak", "PIC", "Talent", "Mulai", "Selesai", "Nilai (Rp)", "Tahap", *self.stages, LOST,
                "Sumber", "Aktivitas terakhir", "Hari diam", "Nomor kontrak", "Catatan", "WhatsApp"]
        out = [head]
        now = int(time.time())
        for d in self.rows():
            st = json.loads(d["stages"] or "{}")
            fmt = lambda t: dt.datetime.fromtimestamp(t, self.cfg.tz).strftime("%Y-%m-%d") if t else ""  # noqa: E731
            out.append([d["id"], d["brand"], d["contact_name"], self.cfg.person_name(d["pic"]), d["talents"], d["period_start"] or "",
                        d["period_end"] or "", d["value"] or 0, d["stage"], *[fmt(st.get(s)) for s in self.stages], fmt(st.get(LOST)),
                        d["source"], fmt(d["last_activity"]), (now - (d["last_activity"] or now)) // 86400,
                        d["contract_number"] or "", d["notes"] or "", f"https://wa.me/{d['wa_id']}" if d["wa_id"] else ""])
        return out

    def push(self, reason: str = "", force: bool = False) -> bool:
        """Send all rows to the Apps Script web app (background). Debounced unless force."""
        url, token = self.cfg.deals_sheet_url, self.cfg.deals_sheet_token
        if not url or not token:
            return False
        last = int(self.store.get_kv("deals_push_at", "0") or 0)
        if not force and time.time() - last < PUSH_MIN_GAP:
            self.store.set_kv("deals_push_pending", "1")
            return False
        ok, why = self.check_host(url)
        if not ok or "script.google.com" not in url:
            self.store.set_kv("deals_sheet_status", json.dumps({"at": int(time.time()), "ok": False, "error": why or "bukan alamat Apps Script"}))
            return False
        rows = self.sheet_rows()
        self.store.set_kv("deals_push_at", str(int(time.time())))
        self.store.set_kv("deals_push_pending", "0")

        def run():
            if not _push_lock.acquire(timeout=1):
                return
            try:
                r = self.http.post(url, json={"token": token, "rows": rows, "reason": reason})
                ok = r.status_code in (200, 302) and "error" not in (r.text or "")[:200].lower()
                self.store.set_kv("deals_sheet_status", json.dumps({"at": int(time.time()), "ok": ok, "rows": len(rows) - 1,
                                                                    "error": "" if ok else f"HTTP {r.status_code}"}))
            except httpx.HTTPError as e:
                self.store.set_kv("deals_sheet_status", json.dumps({"at": int(time.time()), "ok": False, "error": type(e).__name__}))
            finally:
                _push_lock.release()

        threading.Thread(target=run, name="deals-push", daemon=True).start()
        return True

    def daily_refresh(self, now: dt.datetime) -> None:
        """Morning refresh (and catch-up of debounced pushes)."""
        local = now.astimezone(self.cfg.tz)
        key = f"deals_refresh:{local.date().isoformat()}"
        pending = self.store.get_kv("deals_push_pending") == "1"
        due = local.time() >= self.cfg.admin_morning_time and not self.store.get_kv(key)
        if due:
            self.store.set_kv(key, "1")
        if (due or pending) and time.time() - int(self.store.get_kv("deals_push_at", "0") or 0) >= PUSH_MIN_GAP:
            self.push(reason="daily", force=True)
