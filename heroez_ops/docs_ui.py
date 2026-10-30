"""Contract pages: approved templates in, filled contracts out.

Only templates with status "approved" can be used. Staff fill in client details, SOW
rows and remuneration; the approved wording is never edited here. Each saved contract
is stored as the exact .docx that was produced.
"""
from __future__ import annotations

import contextvars
import datetime as dt
import hashlib
import hmac
import json
import logging
import re
import secrets as pysecrets
import threading
import time
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from . import contract_ai
from . import doctemplate as dtp
from .config import Config
from .db import Store
from .terbilang import parse_amount, rupiah
from .ui_common import e, page
from .owner_auth import COOKIE as OWNER_COOKIE
from .safety import UnsafeFile, check_zip, content_disposition
from .webauth import OWNER, AccessVerifier, User, remote_user

_csrf_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("csrf")

log = logging.getLogger(__name__)

GROUP_LABELS = {"client": "Data klien", "contract": "Kontrak", "campaign": "Kampanye", "sow": "Ruang lingkup (SOW)",
                "fee": "Remunerasi", "other": "Lainnya"}
TYPE_LABELS = {"text": "Teks", "longtext": "Teks panjang", "date": "Tanggal", "money": "Nominal (Rp)",
               "money_words": "Terbilang (otomatis)", "number": "Angka", "contract_number": "Nomor kontrak (otomatis)"}
STATUS_LABELS = {"analyzing": "⏳ AI sedang membaca", "review": "🟠 Perlu dicek", "approved": "🟢 Disetujui",
                 "archived": "Diarsipkan", "failed": "🔴 Gagal"}
ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]
MAX_UPLOAD = 10 * 1024 * 1024


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "field"


class ContractsUI:
    def __init__(self, home: Path, store: Store, cfg: Callable[[], Config],
                 analyzer: Callable[[bytes, str], tuple[dict, list[str]]] | None = None):
        self.home, self.store, self.cfg = home, store, cfg
        self.tdir = home / "data" / "templates"
        self.cdir = home / "data" / "contracts"
        self._csrf_secret = pysecrets.token_bytes(32)
        self.analyzer = analyzer or (lambda data, key: contract_ai.analyze(data, key))
        from . import doc_import
        self.doc_analyzer = doc_import.analyze     # (blocks, api_key, store) -> table; tests replace it
        self._verifier: tuple[tuple[str, str], AccessVerifier] | None = None

    # -- CSRF: one token per user (and per owner session), never shared across users ---
    def csrf_for(self, user: User, request: Request | None) -> str:
        session = request.cookies.get(OWNER_COOKIE, "") if (request is not None and user.role == "owner") else ""
        msg = f"{user.role}:{user.key}:{user.email}:{session}".encode()
        return hmac.new(self._csrf_secret, msg, hashlib.sha256).hexdigest()[:32]

    @property
    def csrf(self) -> str:
        return _csrf_ctx.get(None) or self.csrf_for(OWNER, None)

    # -- identity -------------------------------------------------------------
    def verifier(self) -> AccessVerifier | None:
        c = self.cfg()
        if not (c.access_team_domain and c.access_audience):
            return None
        k = (c.access_team_domain, c.access_audience)
        if not self._verifier or self._verifier[0] != k:
            self._verifier = (k, AccessVerifier(*k))
        return self._verifier[1]

    def router(self, local: bool) -> APIRouter:
        r = APIRouter()
        nav = ([("Antrian", "/antrian"), ("Deal", "/deal"), ("SPK", "/spk"), ("Kontrak", "/kontrak"), ("Template", "/template"),
                ("Nomor", "/nomor"), ("Pengetahuan", "/pengetahuan"), ("Pengaturan", "/")] if local
               else [("Antrian", "/antrian"), ("Deal", "/deal"), ("SPK", "/spk"), ("Kontrak", "/kontrak"), ("Template", "/template"),
                     ("Nomor", "/nomor"), ("Pengetahuan", "/pengetahuan")])

        async def who(request: Request) -> User:
            user = OWNER if local else remote_user(request, self.cfg(), self.verifier())
            _csrf_ctx.set(self.csrf_for(user, request))
            return user

        async def form(request: Request) -> dict:
            f = await request.form()
            if not pysecrets.compare_digest(str(f.get("csrf", "")), self.csrf):
                raise HTTPException(403, "Halaman kedaluwarsa, buka ulang.")
            return f

        def show(title: str, body: str, user: User, active: str, message: str = "", ok: bool = True) -> HTMLResponse:
            return HTMLResponse(page(title, body, nav, active, user.name, message, ok, logout=local))

        from . import deals_ui, kb_ui, numbering_ui
        kb_ui.add_routes(r, self, who, form, show)
        deals_ui.add_routes(r, self, who, form, show)
        numbering_ui.add_routes(r, self, who, form, show)

        # -- queue ----------------------------------------------------------
        @r.get("/antrian", response_class=HTMLResponse)
        async def queue(request: Request, semua: int = 0, msg: str = ""):
            user = await who(request)
            return show("Antrian", self.queue_page(user, bool(semua)), user, "/antrian", msg)

        @r.post("/antrian/aksi")
        async def queue_action(request: Request):
            user = await who(request)
            f = await form(request)
            wa_id, action = str(f.get("wa", "")), str(f.get("aksi", ""))
            if not self.store.one("SELECT 1 FROM conversations WHERE wa_id=?", (wa_id,)):
                raise HTTPException(404)
            engine = self._engine()
            at = int(time.time())
            if action == "ambil" and user.role == "admin":
                engine.claim(wa_id, user.key, at)
            elif action == "dibalas":
                engine.mark_replied(wa_id, user.key, at)
            elif action == "tunda":
                engine.snooze(wa_id, user.key, at)
            else:
                raise HTTPException(400)
            return RedirectResponse(f"/antrian?semua={int(bool(f.get('semua')))}", status_code=303)

        # -- contracts ------------------------------------------------------
        @r.get("/kontrak", response_class=HTMLResponse)
        def contracts(user: User = Depends(who)):
            rows = self.store.q("SELECT c.*, t.name tname FROM contracts c LEFT JOIN contract_templates t ON t.id=c.template_id "
                                "ORDER BY c.id DESC LIMIT 200")
            tpl = self.store.q("SELECT id, name FROM contract_templates WHERE status='approved' ORDER BY name")
            start = ("".join(f'<a class="btn" href="/kontrak/baru?t={t["id"]}">+ {e(t["name"])}</a> ' for t in tpl)
                     or '<p class="muted">Belum ada template yang disetujui. Upload di menu Template.</p>')
            table = "".join(
                f'<tr><td><a href="/kontrak/{c["id"]}">{e(c["number"])}</a></td><td>{e(c["client_name"])}</td>'
                f'<td>{rupiah(c["total"]) if c["total"] else "-"}</td><td>{e(c["tname"])}</td>'
                f'<td>{dt.datetime.fromtimestamp(c["created_at"]).strftime("%d/%m/%Y")}</td><td>{e(c["created_by"])}</td></tr>'
                for c in rows) or '<tr><td colspan="6" class="muted">Belum ada kontrak.</td></tr>'
            body = (f'<h1>Kontrak</h1><div class="card"><h2>Buat kontrak baru</h2>{start}</div>'
                    f'<div class="card scroll"><table><tr><th>Nomor</th><th>Klien</th><th>Nilai</th><th>Template</th>'
                    f'<th>Tanggal</th><th>Dibuat oleh</th></tr>{table}</table></div>')
            return show("Kontrak", body, user, "/kontrak")

        @r.get("/kontrak/baru", response_class=HTMLResponse)
        def new_contract(t: int, dari: int = 0, user: User = Depends(who)):
            tpl = self._approved(t)
            values, rows = {}, []
            if dari:
                c = self._contract(dari)
                data = json.loads(c["data"])
                values, rows = data["values"], data["rows"]
                values.pop("contract_number", None)
            return show("Kontrak baru", self.contract_form(tpl, values, rows, None), user, "/kontrak")

        @r.get("/kontrak/{cid}/ubah", response_class=HTMLResponse)
        def edit_contract(cid: int, user: User = Depends(who)):
            c = self._contract(cid)
            data = json.loads(c["data"])
            return show("Ubah kontrak", self.contract_form(self._approved(c["template_id"], allow_archived=True),
                                                           data["values"], data["rows"], c), user, "/kontrak")

        @r.post("/kontrak/simpan")
        async def save_contract(request: Request, user: User = Depends(who)):
            f = await form(request)
            cid = int(f.get("cid") or 0)
            if cid:
                existing = self._contract(cid)                       # 404 if it doesn't exist
                if int(f["tid"]) != existing["template_id"]:
                    raise HTTPException(400, "Template tidak sesuai dengan kontrak ini.")
                tpl = self._approved(existing["template_id"], allow_archived=True)
            else:
                tpl = self._approved(int(f["tid"]))
            try:
                values, rows = self.parse_contract_form(tpl, f)
            except ValueError as ex:
                spec = json.loads(tpl["spec"])
                values = {k[2:]: v for k, v in f.items() if k.startswith("f_")}
                return show("Kontrak", self.contract_form(tpl, values, self._rows_from_form(spec, f), None), user,
                            "/kontrak", str(ex), ok=False)
            from .numbering import NumberError
            try:
                cid = self.store_contract(tpl, values, rows, user, cid, deal_id=int(f.get("deal_id") or 0) or None)
            except NumberError as ex:
                return show("Kontrak", self.contract_form(tpl, values, rows, self._contract(cid) if cid else None), user,
                            "/kontrak", str(ex), ok=False)
            return RedirectResponse(f"/kontrak/{cid}", status_code=303)

        @r.get("/kontrak/{cid}", response_class=HTMLResponse)
        def view_contract(cid: int, user: User = Depends(who)):
            c = self._contract(cid)
            tpl = self.store.one("SELECT * FROM contract_templates WHERE id=?", (c["template_id"],))
            spec = json.loads(tpl["spec"]) if tpl else {"fields": []}
            data = json.loads(c["data"])
            rows_html = ""
            for fdef in spec["fields"]:
                v = data["values"].get(fdef["key"], "")
                shown = dtp.format_value(fdef["type"], v, data["values"], fdef.get("source"), fdef.get("format"))
                rows_html += f'<tr><td>{e(fdef["label"])}</td><td>{e(shown) or "<span class=muted>(kosong)</span>"}</td></tr>'
            sow = ""
            d = spec.get("deliverables")
            if d and data["rows"]:
                head = "".join(f"<th>{e(col['label'])}</th>" for col in d["columns"])
                body_rows = "".join("<tr>" + "".join(
                    f"<td>{e(str(i + 1) if col['key'] == 'no' else (rupiah(row.get(col['key']) or 0) if col['type'] == 'money' else row.get(col['key'], '')))}</td>"
                    for col in d["columns"]) + "</tr>" for i, row in enumerate(data["rows"]))
                sow = f'<div class="card scroll"><h2>SOW</h2><table><tr>{head}</tr>{body_rows}</table></div>'
            empty = [fd["label"] for fd in spec["fields"] if fd["type"] not in ("money_words",)
                     and not str(data["values"].get(fd["key"], "")).strip()]
            warn = f'<div class="msg bad">Masih kosong: {e(", ".join(empty))}</div>' if empty else ""
            body = (f'<h1>Kontrak {e(c["number"])}</h1><p class="muted">{e(tpl["name"] if tpl else "")} · dibuat {e(c["created_by"])}'
                    f'{" · diubah " + e(c["updated_by"]) if c["updated_by"] else ""}</p>{warn}'
                    f'<p><a class="btn" href="/kontrak/{cid}/word">⬇ Download Word</a> '
                    f'<a class="btn secondary" href="/kontrak/{cid}/ubah">Ubah</a> '
                    f'<a class="btn secondary" href="/kontrak/baru?t={c["template_id"]}&dari={cid}">Duplikat untuk klien lain</a></p>'
                    f'<div class="card"><table>{rows_html}</table></div>{sow}'
                    f'<p class="muted">Isi pasal mengikuti template yang disetujui. Untuk PDF: buka file Word → Simpan sebagai PDF.</p>')
            return show(f"Kontrak {c['number']}", body, user, "/kontrak")

        @r.get("/kontrak/{cid}/word")
        def download(cid: int, user: User = Depends(who)):
            c = self._contract(cid)
            path = self.cdir / f"{cid}.docx"
            if not path.exists():
                raise HTTPException(404, "File kontrak tidak ditemukan.")
            name = re.sub(r"[^\w .-]+", "-", f"{c['number']} {c['client_name'] or ''}").strip(" -") + ".docx"
            self.store.audit(user.key, "contract_download", None, {"contract": cid})
            return Response(path.read_bytes(), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            headers={"Content-Disposition": content_disposition(name)})

        # -- templates ------------------------------------------------------
        @r.get("/template", response_class=HTMLResponse)
        def templates(user: User = Depends(who)):
            rows = self.store.q("SELECT * FROM contract_templates WHERE status!='archived' ORDER BY id DESC")
            types = self.numbering().types()
            table = "".join(f'<tr><td><a href="/template/{t["id"]}">{e(t["name"])}</a></td>'
                            f'<td>{e(types.get(self.doc_type(t), {}).get("label", self.doc_type(t)))}</td>'
                            f'<td>{STATUS_LABELS.get(t["status"], t["status"])}</td>'
                            f'<td>{e(t["approved_by"] or "")}</td></tr>' for t in rows) or '<tr><td colspan="4" class="muted">Belum ada template.</td></tr>'
            upload = ""
            if user.can_manage_templates:
                upload = (f'<div class="card"><h2>Upload template yang sudah disetujui legal</h2>'
                          f'<p class="muted">File Word (.docx) versi final dari legal. AI akan menandai bagian yang berubah per klien '
                          f'(nama klien, SOW, fee...). Teks pasal tidak diubah. Kamu mengecek hasilnya sebelum dipakai.</p>'
                          f'<form method="post" action="/template/upload" enctype="multipart/form-data">'
                          f'<input type="hidden" name="csrf" value="{self.csrf}">'
                          f'<label>Nama template</label><input type="text" name="name" required placeholder="Perjanjian Kampanye Brand v2026">'
                          f'<label>Jenis dokumen <small>(menentukan penomoran)</small></label><select name="doc_type">'
                          + "".join(f'<option value="{e(k)}">{e(v["label"])}</option>' for k, v in self.numbering().types().items())
                          + '</select>'
                          f'<label>File .docx</label><input type="file" name="file" accept=".docx" required><br><br>'
                          f'<button type="submit">Upload & baca dengan AI</button></form></div>')
            body = (f'<h1>Template dokumen</h1><div class="card"><table><tr><th>Nama</th><th>Jenis</th><th>Status</th>'
                    f'<th>Disetujui oleh</th></tr>{table}</table></div>{upload}')
            return show("Template", body, user, "/template")

        @r.post("/template/upload")
        async def upload(request: Request, user: User = Depends(who)):
            self._require_manager(user)
            f = await form(request)
            file: UploadFile = f.get("file")
            data = await file.read() if file else b""
            if not data or len(data) > MAX_UPLOAD or not data.startswith(b"PK"):
                raise HTTPException(400, "File harus .docx (maks. 10 MB).")
            try:
                check_zip(data)
            except UnsafeFile as ex:
                raise HTTPException(400, str(ex)) from ex
            try:
                dtp.load(data)
            except Exception as ex:
                raise HTTPException(400, "File tidak bisa dibaca sebagai dokumen Word (.docx).") from ex
            dtype = str(f.get("doc_type") or "kontrak")
            if dtype not in self.numbering().types():
                raise HTTPException(400, "Jenis dokumen tidak dikenal.")
            tid = self.store.x("INSERT INTO contract_templates(name,status,created_at,created_by,doc_type) VALUES(?,?,?,?,?)",
                               (str(f.get("name") or file.filename)[:120], "analyzing", int(time.time()), user.key, dtype)).lastrowid
            d = self.tdir / str(tid)
            d.mkdir(parents=True, exist_ok=True)
            (d / "original.docx").write_bytes(data)
            self.store.audit(user.key, "template_upload", None, {"template": tid})
            self.start_analysis(tid)
            return RedirectResponse(f"/template/{tid}", status_code=303)

        @r.get("/template/{tid}", response_class=HTMLResponse)
        def review(tid: int, user: User = Depends(who)):
            return show("Template", self.review_page(tid, user), user, "/template")

        @r.post("/template/{tid}/simpan", response_class=HTMLResponse)
        async def save_fields(tid: int, request: Request, user: User = Depends(who)):
            self._require_manager(user)
            f = await form(request)
            msg, ok = self.update_fields(tid, f, user)
            return show("Template", self.review_page(tid, user), user, "/template", msg, ok)

        @r.post("/template/{tid}/setujui")
        async def approve(tid: int, request: Request, user: User = Depends(who)):
            self._require_manager(user)
            await form(request)
            t = self._template(tid)
            if not t["check_ok"]:
                raise HTTPException(400, "Template belum lolos pengecekan.")
            self.store.x("UPDATE contract_templates SET status='approved', approved_at=?, approved_by=? WHERE id=?",
                         (int(time.time()), user.name, tid))
            self.store.audit(user.key, "template_approve", None, {"template": tid})
            return RedirectResponse(f"/template/{tid}", status_code=303)

        @r.post("/template/{tid}/arsip")
        async def archive(tid: int, request: Request, user: User = Depends(who)):
            self._require_manager(user)
            await form(request)
            self.store.x("UPDATE contract_templates SET status='archived' WHERE id=?", (tid,))
            self.store.audit(user.key, "template_archive", None, {"template": tid})
            return RedirectResponse("/template", status_code=303)

        @r.post("/template/{tid}/ulang")
        async def retry(tid: int, request: Request, user: User = Depends(who)):
            self._require_manager(user)
            await form(request)
            self.store.x("UPDATE contract_templates SET status='analyzing' WHERE id=?", (tid,))
            self.start_analysis(tid)
            return RedirectResponse(f"/template/{tid}", status_code=303)

        @r.get("/template/{tid}/asli")
        def original(tid: int, user: User = Depends(who)):
            t = self._template(tid)
            return Response((self.tdir / str(tid) / "original.docx").read_bytes(),
                            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            headers={"Content-Disposition": content_disposition(f"{slug(t['name'])}-asli.docx")})

        return r

    # -- queue ----------------------------------------------------------------------
    def _engine(self):
        from .states import Engine
        return Engine(self.cfg(), self.store)

    def _deals(self):
        from .deals import Deals
        if not hasattr(self, "_deals_http"):
            import httpx
            self._deals_http = httpx.Client(timeout=20, follow_redirects=False)
        return Deals(self.cfg(), self.store, http=self._deals_http)

    def queue_page(self, user: User, everything: bool) -> str:
        from .bizhours import BusinessClock
        from .links import alert_line, defang
        from .textrules import redact
        from .notifier import human_duration
        from .phone import display
        mine_only = user.role == "admin" and not everything
        sql = ("SELECT c.*, ct.name, ct.kind FROM conversations c JOIN contacts ct USING(wa_id) WHERE c.state='AWAITING_US' "
               "AND c.wa_id NOT IN (SELECT wa_id FROM intake WHERE status='active')")
        params: tuple = ()
        if mine_only:
            sql += " AND c.assignee=?"
            params = (user.key,)
        rows = self.store.q(sql + " ORDER BY c.waiting_since", params)
        clock = BusinessClock(self.cfg())
        now = dt.datetime.now(dt.timezone.utc)
        kinds = {"talent": "Talent", "existing_client": "Klien lama", "new": "Kontak baru"}
        items = ""
        for c in rows:
            since = dt.datetime.fromtimestamp(c["waiting_since"], dt.timezone.utc)
            last = self.store.one("SELECT id, text, type FROM messages WHERE wa_id=? AND direction='in' ORDER BY ts DESC LIMIT 1",
                                  (c["wa_id"],))
            snippet = defang(redact((last["text"] or f"[{last['type']}]")[:160])) if last else ""
            warn = alert_line(self.store, last["id"]) if last else ""
            pic = self.cfg().admin(c["assignee"]).name if c["assignee"] and self.cfg().admin(c["assignee"]) else "-"
            btn = lambda a, label: (f'<form method="post" action="/antrian/aksi" style="display:inline">'  # noqa: E731
                                    f'<input type="hidden" name="csrf" value="{self.csrf}"><input type="hidden" name="wa" value="{e(c["wa_id"])}">'
                                    f'<input type="hidden" name="aksi" value="{a}"><input type="hidden" name="semua" value="{int(everything)}">'
                                    f'<button class="secondary" type="submit">{label}</button></form> ')
            actions = (btn("ambil", "✋ Saya ambil") if user.role == "admin" else "") + btn("dibalas", "✅ Sudah dibalas") + btn("tunda", "⏰ Tunda 1 jam")
            items += (f'<div class="card"><b>{e(c["name"] or display(c["wa_id"]))}</b> ({e(display(c["wa_id"]))}) · '
                      f'{e(kinds.get(c["kind"], ""))}{" · " + e(c["priority"]) if c["priority"] else ""}<br>'
                      f'⏱ {human_duration((now - since).total_seconds() / 60)} '
                      f'({human_duration(clock.business_minutes_between(since, now))} jam notifikasi) · PIC: {e(pic)}<br>'
                      f'{"🤖 " + e(defang(c["ai_summary"])) + "<br>" if c["ai_summary"] else ""}'
                      f'💬 “{e(snippet)}”<br>{("<b>" + e(warn) + "</b><br>") if warn else ""}'
                      f'<a class="btn" href="https://wa.me/{e(c["wa_id"])}" target="_blank" rel="noopener">Buka chat</a> {actions}</div>')
        toggle = ""
        if user.role == "admin":
            toggle = ('<a href="/antrian?semua=1">Lihat semua</a>' if mine_only else '<a href="/antrian">Hanya punya saya</a>')
        title = "Antrian saya" if mine_only else "Semua chat yang menunggu"
        empty = '<div class="card">🟢 Tidak ada chat yang menunggu balasan.</div>'
        return (f'<meta http-equiv="refresh" content="120"><h1>{title} ({len(rows)})</h1><p class="muted">Terlama di atas. '
                f'Balas dari WhatsApp Web atau HP; halaman ini memuat ulang tiap 2 menit. {toggle}</p>{items or empty}')

    # -- helpers ------------------------------------------------------------------
    def _require_manager(self, user: User) -> None:
        if not user.can_manage_templates:
            raise HTTPException(403, "Hanya supervisor yang bisa mengubah template.")

    def _template(self, tid: int):
        t = self.store.one("SELECT * FROM contract_templates WHERE id=?", (tid,))
        if not t:
            raise HTTPException(404, "Template tidak ditemukan.")
        return t

    def _approved(self, tid: int, allow_archived: bool = False):
        t = self._template(tid)
        if t["status"] != "approved" and not (allow_archived and t["status"] == "archived"):
            raise HTTPException(400, "Template ini belum disetujui, jadi belum bisa dipakai.")
        return t

    def _contract(self, cid: int):
        c = self.store.one("SELECT * FROM contracts WHERE id=?", (cid,))
        if not c:
            raise HTTPException(404, "Kontrak tidak ditemukan.")
        return c

    # -- template analysis & review --------------------------------------------
    def start_analysis(self, tid: int) -> threading.Thread:
        t = threading.Thread(target=self._analyze, args=(tid,), name=f"template-{tid}", daemon=True)
        t.start()
        return t

    def _analyze(self, tid: int) -> None:
        original = (self.tdir / str(tid) / "original.docx").read_bytes()
        key = self.cfg().anthropic_api_key
        notes: list[str] = []
        if key:
            try:
                spec, notes = self.analyzer(original, key)
            except contract_ai.AnalysisError as ex:
                spec, notes = {"fields": [], "deliverables": None}, [str(ex), "Tambahkan field secara manual di bawah."]
            except Exception:
                log.exception("template analysis failed")
                spec, notes = {"fields": [], "deliverables": None}, ["AI gagal membaca template. Tambahkan field secara manual."]
        else:
            spec, notes = {"fields": [], "deliverables": None}, ["Claude API key belum diisi: AI tidak dipakai. Tambahkan field secara manual."]
        self._save_spec(tid, spec, notes, status="review")

    def _save_spec(self, tid: int, spec: dict, notes: list[str], status: str = "review") -> tuple[bool, str]:
        original = (self.tdir / str(tid) / "original.docx").read_bytes()
        ok, msg = contract_ai.self_check(original, spec) if spec["fields"] or spec.get("deliverables") else (False, "Belum ada field.")
        if ok:
            template, _ = dtp.mark(original, spec)
            (self.tdir / str(tid) / "template.docx").write_bytes(template)
        self.store.x("UPDATE contract_templates SET spec=?, notes=?, check_ok=?, check_msg=?, status=? WHERE id=?",
                     (json.dumps(spec, ensure_ascii=False), json.dumps(notes, ensure_ascii=False), int(ok), msg, status, tid))
        return ok, msg

    def update_fields(self, tid: int, f, user: User) -> tuple[str, bool]:
        t = self._template(tid)
        spec = json.loads(t["spec"] or '{"fields": [], "deliverables": null}')
        original = (self.tdir / str(tid) / "original.docx").read_bytes()
        kept = []
        for i, fd in enumerate(spec["fields"]):
            if not f.get(f"keep_{i}"):
                continue
            fd["label"] = str(f.get(f"label_{i}") or fd["label"])[:80]
            ftype = str(f.get(f"type_{i}") or fd["type"])
            if ftype != fd["type"]:
                fd["type"], fd["format"] = ftype, dtp.infer_format(ftype, fd.get("example", ""))
            fd["group"] = str(f.get(f"group_{i}") or fd["group"])
            kept.append(fd)
        spec["fields"] = kept
        if spec.get("deliverables") and not f.get("keep_sow"):
            spec["deliverables"] = None
        notes = []
        new_text, new_label = str(f.get("new_text") or ""), str(f.get("new_label") or "").strip()
        if new_text and new_label:
            doc = dtp.load(original)
            occs = [{"block": b.id, "text": new_text, "whole": False, "remove": False}
                    for b in dtp.blocks(doc) if new_text in dtp.run_text(b.paragraph)]
            if not occs:
                notes.append(f"Teks “{new_text[:50]}” tidak ditemukan persis di dokumen (cek huruf besar/kecil dan spasi).")
            else:
                key = slug(new_label)
                while key in {x["key"] for x in spec["fields"]}:
                    key += "_2"
                ftype = str(f.get("new_type") or "text")
                source = next((x["key"] for x in spec["fields"] if x["type"] == "money"), "") if ftype == "money_words" else ""
                spec["fields"].append({"key": key, "label": new_label, "type": ftype, "group": str(f.get("new_group") or "other"),
                                       "source": source, "example": new_text, "format": dtp.infer_format(ftype, new_text),
                                       "occurrences": occs})
        status = "review"  # any change needs approval again
        ok, msg = self._save_spec(tid, spec, notes, status=status)
        self.store.audit(user.key, "template_edit", None, {"template": tid, "check_ok": ok})
        return (msg if not notes else " ".join(notes) + " " + msg), ok and not notes

    def review_page(self, tid: int, user: User) -> str:
        t = self._template(tid)
        if t["status"] == "analyzing":
            return ('<meta http-equiv="refresh" content="5"><h1>⏳ AI sedang membaca template…</h1>'
                    '<p class="muted">Biasanya 1–2 menit. Halaman ini memuat ulang sendiri.</p>')
        spec = json.loads(t["spec"] or '{"fields": [], "deliverables": null}')
        original = (self.tdir / str(tid) / "original.docx").read_bytes()
        notes = json.loads(t["notes"] or "[]")
        manage = user.can_manage_templates
        dis = "" if manage else "disabled"
        def readable(m):
            where = m.group(1) or ""
            t = re.match(r"T(\d+) R(\d+) C(\d+)", where)
            return (f"[Tabel {int(t.group(1)) + 1} · baris {int(t.group(2)) + 1}] " if t
                    else f"[{where}] " if where else "")
        preview = re.sub(r"(?m)^B\d+(?: \[(.*?)\])?: ", readable, contract_ai.marked_preview(original, spec))
        preview = e(preview)
        preview = re.sub(r"\[\[(.+?)\]\]", r"<mark>\1</mark>", preview)
        opts = lambda d, cur: "".join(f'<option value="{k}" {"selected" if k == cur else ""}>{e(v)}</option>' for k, v in d.items())  # noqa: E731
        rows = "".join(
            f'<tr><td><input type="checkbox" name="keep_{i}" checked {dis}></td>'
            f'<td><input type="text" name="label_{i}" value="{e(fd["label"])}" {dis}></td>'
            f'<td><select name="type_{i}" {dis}>{opts(TYPE_LABELS, fd["type"])}</select></td>'
            f'<td><select name="group_{i}" {dis}>{opts(GROUP_LABELS, fd["group"])}</select></td>'
            f'<td><code>{e(fd.get("example", "")[:60])}</code><br><small>{len(fd["occurrences"])}× di dokumen</small></td></tr>'
            for i, fd in enumerate(spec["fields"]))
        d = spec.get("deliverables")
        sow = ""
        if d:
            cols = ", ".join(f'{e(c["label"])} ({e(TYPE_LABELS.get(c["type"], c["type"]))})' for c in d["columns"])
            sow = (f'<p><label style="display:inline;font-weight:400"><input type="checkbox" name="keep_sow" checked {dis}> '
                   f'Tabel SOW: tabel ke-{d["table"] + 1}, satu baris per deliverable. Kolom: {cols}</label></p>')
        check = (f'<div class="msg {"ok" if t["check_ok"] else "bad"}">{"✅" if t["check_ok"] else "⚠️"} {e(t["check_msg"])}</div>')
        note_html = "".join(f"<li>{e(n)}</li>" for n in notes)
        actions = ""
        if manage:
            approve = (f'<form method="post" action="/template/{tid}/setujui" style="display:inline"><input type="hidden" name="csrf" value="{self.csrf}">'
                       f'<button type="submit" {"" if t["check_ok"] and t["status"] != "approved" else "disabled"}>✅ Setujui & pakai template ini</button></form> ')
            actions = (approve +
                       f'<form method="post" action="/template/{tid}/ulang" style="display:inline"><input type="hidden" name="csrf" value="{self.csrf}">'
                       f'<button class="secondary" type="submit">Baca ulang dengan AI</button></form> '
                       f'<form method="post" action="/template/{tid}/arsip" style="display:inline"><input type="hidden" name="csrf" value="{self.csrf}">'
                       f'<button class="danger" type="submit">Arsipkan</button></form>')
        add = "" if not manage else (
            '<h2 style="margin-top:16px">Tambah field manual</h2><div class="row">'
            '<div><label>Teks persis di dokumen</label><input type="text" name="new_text" placeholder="PT Contoh Brand"></div>'
            '<div><label>Label di formulir</label><input type="text" name="new_label" placeholder="Nama perusahaan klien"></div></div>'
            f'<div class="row"><div><label>Jenis</label><select name="new_type">{opts(TYPE_LABELS, "text")}</select></div>'
            f'<div><label>Kelompok</label><select name="new_group">{opts(GROUP_LABELS, "client")}</select></div></div>')
        return (f'<h1>{e(t["name"])}</h1><p class="muted">{STATUS_LABELS.get(t["status"], t["status"])}'
                f'{" · disetujui " + e(t["approved_by"]) if t["approved_by"] and t["status"] == "approved" else ""} · '
                f'<a href="/template/{tid}/asli">download file asli</a></p>{check}'
                f'{"<div class=card><b>Catatan</b><ul>" + note_html + "</ul></div>" if notes else ""}'
                f'<div class="card"><h2>Cek dokumen</h2><p class="muted">Bagian <mark>kuning</mark> akan diisi lewat formulir. '
                f'Pastikan setiap bagian kuning mencakup seluruh nilainya (mis. seluruh nama perusahaan), dan tidak ada '
                f'data klien lama yang tertinggal di luar bagian kuning.</p><div class="doc">{preview}</div></div>'
                f'<form method="post" action="/template/{tid}/simpan"><input type="hidden" name="csrf" value="{self.csrf}">'
                f'<div class="card scroll"><h2>Field</h2><table><tr><th>Pakai</th><th>Label</th><th>Jenis</th><th>Kelompok</th>'
                f'<th>Contoh di template</th></tr>{rows}</table>{sow}{add}'
                f'{"<br><button type=submit>Simpan perubahan & cek ulang</button>" if manage else ""}</div></form>'
                f'<p>{actions}</p>')

    # -- contract form ------------------------------------------------------------
    def numbering(self):
        from .numbering import Numbering
        c = self.cfg()
        return Numbering(self.store, c.tz, c.contract_number_format)

    @staticmethod
    def doc_type(tpl) -> str:
        return (tpl["doc_type"] if "doc_type" in tpl.keys() else None) or "kontrak"

    def next_number(self, consume: bool, doc_type: str = "kontrak", who: str = "system") -> str:
        """Preview (consume=False) or issue the next number of this document type."""
        n = self.numbering()
        return n.take(doc_type, who) if consume else n.peek(doc_type)

    def assign_number(self, tpl, typed: str, user: User, cid: int, note: str) -> str:
        """The number a saved document gets: the next one, or one typed by hand (checked against the register)."""
        n, dtype = self.numbering(), self.doc_type(tpl)
        typed = (typed or "").strip()
        if cid:
            current = self._contract(cid)["number"]
            if not typed or typed == current:
                return current
            return n.claim(dtype, typed, user.key, document_id=cid, note=note)
        if not typed or typed == n.peek(dtype):
            return n.take(dtype, user.key, note=note)
        return n.claim(dtype, typed, user.key, note=note)

    def contract_form(self, tpl, values: dict, rows: list[dict], existing) -> str:
        spec = json.loads(tpl["spec"])
        groups: dict[str, list[str]] = {}
        for fd in spec["fields"]:
            if fd["type"] == "money_words":
                continue
            k, v = fd["key"], values.get(fd["key"], "")
            if fd["type"] == "contract_number" and not v:
                v = self.next_number(consume=False, doc_type=self.doc_type(tpl))
            if fd["type"] == "money" and isinstance(v, int):
                v = f"{v:,}".replace(",", ".") if v else ""
            if fd["type"] == "longtext":
                inp = f'<textarea name="f_{k}" rows="4">{e(v)}</textarea>'
            else:
                t = {"date": "date", "number": "number"}.get(fd["type"], "text")
                ph = ' placeholder="mis. 150.000.000 atau 150jt"' if fd["type"] == "money" else ""
                inp = f'<input type="{t}" name="f_{k}" value="{e(v)}"{ph}>'
            hint = ""
            if fd["type"] == "money" and self._auto_kind(fd["key"]):
                hint = " <small>(kosongkan = dihitung otomatis)</small>"
            groups.setdefault(fd["group"], []).append(f'<label>{e(fd["label"])}{hint}</label>{inp}')
        cards = "".join(f'<div class="card"><h2>{e(GROUP_LABELS.get(g, g))}</h2>{"".join(items)}</div>'
                        for g in GROUP_LABELS if (items := groups.get(g)))
        sow = ""
        d = spec.get("deliverables")
        if d:
            cols = [c for c in d["columns"] if c["key"] != "no"]
            head = "".join(f"<th>{e(c['label'])}</th>" for c in cols)
            def row_html(i: int, row: dict) -> str:
                cells = ""
                for c in cols:
                    v = row.get(c["key"], "")
                    if c["type"] == "money" and isinstance(v, int):
                        v = f"{v:,}".replace(",", ".") if v else ""
                    t = "date" if c["type"] == "date" else "text"
                    cells += f'<td><input type="{t}" name="r_{c["key"]}_{i}" value="{e(v)}"></td>'
                return f"<tr>{cells}</tr>"
            existing_rows = rows or [{} for _ in range(3)]
            body_rows = "".join(row_html(i, r) for i, r in enumerate(existing_rows))
            template_row = row_html(9999, {}).replace("9999", "__I__")
            sow = (f'<div class="card scroll"><h2>SOW / deliverables</h2><p class="muted">Satu baris per deliverable. Baris kosong diabaikan.</p>'
                   f'<table id="sow"><tr>{head}</tr>{body_rows}</table><br>'
                   f'<button type="button" class="secondary" onclick="addRow()">+ Tambah baris</button></div>'
                   f'<script>let n={len(existing_rows)};const tpl={json.dumps(template_row)};'
                   f'function addRow(){{document.querySelector("#sow tbody").insertAdjacentHTML("beforeend",tpl.replaceAll("__I__",n++));}}</script>')
        tax = "" if not any(self._auto_kind(fd["key"]) == "tax" for fd in spec["fields"]) else (
            f'<p class="muted">PPN: {"dihitung " + str(round(self.cfg().ppn_rate * 100, 2)) + "% (Heroez PKP)" if self.cfg().pkp else "0 (Heroez bukan PKP, ubah di Pengaturan)"}</p>')
        title = f"Ubah kontrak {e(existing['number'])}" if existing else "Kontrak baru"
        from .deals import LOST
        linked = self.store.one("SELECT id FROM deals WHERE contract_id=?", (existing["id"],)) if existing else None
        open_deals = self.store.q("SELECT id, brand, contact_name, stage FROM deals WHERE stage!=? AND (contract_id IS NULL OR id=?) "
                                  "ORDER BY updated_at DESC LIMIT 50", (LOST, linked["id"] if linked else 0))
        opts = "".join(f'<option value="{d["id"]}"{" selected" if linked and d["id"] == linked["id"] else ""}>'
                       f'#{d["id"]} {e(d["brand"] or d["contact_name"] or "-")} · {e(d["stage"])}</option>' for d in open_deals)
        deal = (f'<div class="card"><h2>Deal terkait</h2><p class="muted">Kontrak ini memindahkan deal ke tahap Kontrak dan mengisi nilainya. '
                f'Pilih "(buat deal baru)" bila belum ada di pipeline.</p><select name="deal_id"><option value="0">(buat deal baru)</option>{opts}</select></div>')
        return (f'<h1>{title}</h1><p class="muted">Template: {e(tpl["name"])}. Isi pasal tidak bisa diubah di sini.</p>'
                f'<form method="post" action="/kontrak/simpan"><input type="hidden" name="csrf" value="{self.csrf}">'
                f'<input type="hidden" name="tid" value="{tpl["id"]}"><input type="hidden" name="cid" value="{existing["id"] if existing else 0}">'
                f'{cards}{sow}{tax}{deal}<button type="submit">Simpan & buat dokumen</button></form>')

    @staticmethod
    def _auto_kind(key: str) -> str | None:
        k = key.lower()
        if any(w in k for w in ("ppn", "pajak", "tax", "vat")):
            return "tax"
        if "grand" in k or "incl" in k or "termasuk" in k:
            return "grand"
        if "total" in k or k in ("fee", "nilai_kontrak", "contract_value"):
            return "total"
        return None

    def _rows_from_form(self, spec: dict, f) -> list[dict]:
        d = spec.get("deliverables")
        if not d:
            return []
        cols = [c for c in d["columns"] if c["key"] != "no"]
        idx = sorted({int(m.group(1)) for k in f.keys() if (m := re.match(r"r_.+_(\d+)$", k))})
        rows = []
        for i in idx:
            row = {c["key"]: str(f.get(f"r_{c['key']}_{i}") or "").strip() for c in cols}
            if any(row.values()):
                rows.append(row)
        return rows

    def parse_contract_form(self, tpl, f) -> tuple[dict, list[dict]]:
        spec = json.loads(tpl["spec"])
        values: dict = {}
        for fd in spec["fields"]:
            raw = str(f.get(f"f_{fd['key']}") or "").strip()
            if fd["type"] == "money":
                try:
                    values[fd["key"]] = parse_amount(raw) if raw else None
                except ValueError as ex:
                    raise ValueError(f"{fd['label']}: nominal “{raw}” tidak terbaca (contoh: 150.000.000 atau 150jt).") from ex
            elif fd["type"] == "number":
                values[fd["key"]] = int(raw) if raw.isdigit() else raw
            elif fd["type"] != "money_words":
                values[fd["key"]] = raw
        rows = self._rows_from_form(spec, f)
        d = spec.get("deliverables")
        for c in (d["columns"] if d else []):
            if c["type"] == "money":
                for r in rows:
                    try:
                        r[c["key"]] = parse_amount(r[c["key"]]) if r.get(c["key"]) else 0
                    except ValueError as ex:
                        raise ValueError(f"SOW {c['label']}: nominal “{r[c['key']]}” tidak terbaca.") from ex
        # Totals left blank are worked out: sum of SOW fees, PPN when PKP, grand total.
        money = [fd for fd in spec["fields"] if fd["type"] == "money"]
        row_money = [c["key"] for c in (d["columns"] if d else []) if c["type"] == "money"]
        base_fd = next((fd for fd in money if self._auto_kind(fd["key"]) == "total"), None)
        if base_fd and values.get(base_fd["key"]) is None and row_money:
            values[base_fd["key"]] = sum(int(r.get(row_money[0]) or 0) for r in rows)
        base = values.get(base_fd["key"]) if base_fd else None
        for fd in money:
            kind = self._auto_kind(fd["key"])
            if values.get(fd["key"]) is None and base is not None:
                if kind == "tax":
                    values[fd["key"]] = round(base * self.cfg().ppn_rate) if self.cfg().pkp else 0
                elif kind == "grand":
                    tax = next((values.get(x["key"]) or 0 for x in money if self._auto_kind(x["key"]) == "tax"), 0)
                    values[fd["key"]] = base + tax
        for fd in money:
            if values.get(fd["key"]) is None:
                values[fd["key"]] = 0
        return values, rows

    def store_contract(self, tpl, values: dict, rows: list[dict], user: User, cid: int = 0, deal_id: int | None = None) -> int:
        spec = json.loads(tpl["spec"])
        now = int(time.time())
        num_fd = next((fd for fd in spec["fields"] if fd["type"] == "contract_number"), None)
        client = next((values.get(fd["key"]) for fd in spec["fields"] if fd["group"] == "client" and fd["type"] == "text"), "")
        number = self.assign_number(tpl, values.get(num_fd["key"], "") if num_fd else "", user, cid,
                                    note=f"{tpl['name']} · {client or ''}".strip(" ·"))
        if num_fd:
            values[num_fd["key"]] = number
        totals = [values.get(fd["key"]) for fd in spec["fields"] if fd["type"] == "money" and self._auto_kind(fd["key"]) in ("grand", "total")]
        total = next((t for t in reversed(totals) if t), None)
        data = json.dumps({"values": values, "rows": rows}, ensure_ascii=False, default=str)
        if cid:
            self.store.x("UPDATE contracts SET number=?, client_name=?, total=?, data=?, updated_at=?, updated_by=? WHERE id=?",
                         (number, client, total, data, now, user.name, cid))
        else:
            cid = self.store.x("INSERT INTO contracts(template_id,number,client_name,total,data,created_at,created_by,updated_at,doc_type) "
                               "VALUES(?,?,?,?,?,?,?,?,?)", (tpl["id"], number, client, total, data, now, user.name, now,
                                                            self.doc_type(tpl))).lastrowid
        self.store.x("UPDATE doc_numbers SET document_id=? WHERE number=? AND document_id IS NULL", (cid, number))
        template = (self.tdir / str(tpl["id"]) / "template.docx").read_bytes()
        self.cdir.mkdir(parents=True, exist_ok=True)
        (self.cdir / f"{cid}.docx").write_bytes(dtp.render(template, spec, values, rows))
        self.store.audit(user.key, "contract_save", None, {"contract": cid, "number": number})
        try:
            deals = self._deals()
            d = deals.get(deal_id) if deal_id else None
            deals.on_contract(d["wa_id"] if d else None, deal_id or None, client or "", total, cid, number, user.key, now)
        except Exception:
            log.exception("deal update after contract %s failed", cid)
        return cid
