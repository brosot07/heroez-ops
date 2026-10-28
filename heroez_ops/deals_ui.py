"""The Deal page: pipeline table with stage buttons, mirrored to the ops Google Sheet."""
from __future__ import annotations

import datetime as dt
import json
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .deals import LOST, Deals
from .phone import display
from .terbilang import parse_amount, rupiah
from .ui_common import e

FINAL_STAGES_SUPERVISOR_ONLY = True


def deal_action(deals: Deals, user, deal_id: int, stage: str, value: str = "", note: str = "", at: int | None = None) -> None:
    """Move a deal (and/or set value, note). Lunas and Batal are for supervisors/owner only."""
    at = at or int(time.time())
    d = deals.get(deal_id)
    if not d:
        raise HTTPException(404, "Deal tidak ditemukan.")
    final = (LOST, deals.stages[-1])
    if stage and stage not in (*deals.stages, LOST):
        raise HTTPException(400, "Tahap tidak dikenal.")
    if stage in final and FINAL_STAGES_SUPERVISOR_ONLY and not user.can_manage_templates:
        raise HTTPException(403, "Lunas dan Batal hanya bisa ditandai supervisor.")
    amount = parse_amount(value) if value.strip() else None
    if stage and stage != d["stage"]:
        deals.set_stage(deal_id, stage, user.key, at, value=amount, note=note.strip() or None)
    elif amount is not None or note.strip():
        deals.set_stage(deal_id, d["stage"], user.key, at, value=amount, note=note.strip() or None)


def add_routes(r: APIRouter, ui, who, form, show) -> None:
    def deals() -> Deals:
        return ui._deals()

    def page_html(user) -> str:
        dl = deals()
        cfg = ui.cfg()
        stages = [*dl.stages, LOST]
        rows = dl.rows()
        status = json.loads(ui.store.get_kv("deals_sheet_status", "{}") or "{}")
        if not cfg.deals_sheet_url:
            sheet = '<p class="muted">Google Sheet belum dihubungkan (Pengaturan → 8. Kontrak & deal).</p>'
        elif status:
            when = dt.datetime.fromtimestamp(status.get("at", 0), cfg.tz).strftime("%d/%m %H:%M")
            sheet = (f'<p class="muted">Sheet: {"✅ terkirim" if status.get("ok") else "❌ gagal: " + e(str(status.get("error", "")))} '
                     f'({when}, {status.get("rows", 0)} baris).</p>')
        else:
            sheet = '<p class="muted">Sheet terhubung; belum ada pengiriman.</p>'
        csrf = f'<input type="hidden" name="csrf" value="{ui.csrf}">'
        can_final = user.can_manage_templates

        def options(cur: str) -> str:
            out = []
            for s in stages:
                dis = " disabled" if (s in (LOST, dl.stages[-1]) and not can_final) else ""
                out.append(f'<option value="{e(s)}"{" selected" if s == cur else ""}{dis}>{e(s)}</option>')
            return "".join(out)

        trs = []
        for d in rows:
            last = dt.datetime.fromtimestamp(d["last_activity"], cfg.tz).strftime("%d/%m") if d["last_activity"] else "-"
            idle = (int(time.time()) - (d["last_activity"] or int(time.time()))) // 86400
            contract = f'<a href="/kontrak/{d["contract_id"]}">{e(d["contract_number"] or "")}</a>' if d["contract_id"] else ""
            done = d["stage"] in (LOST, dl.stages[-1])
            trs.append(
                f'<tr{" class=muted" if done else ""}><td>{d["id"]}</td><td><b>{e(d["brand"] or "-")}</b><br><small>{e(d["contact_name"] or "")} '
                f'{e(display(d["wa_id"])) if d["wa_id"] else ""}</small></td><td>{e(cfg.person_name(d["pic"]))}</td>'
                f'<td>{e(d["talents"] or "")}<br><small>{e(d["period_start"] or "")}{" – " + e(d["period_end"]) if d["period_end"] else ""}</small></td>'
                f'<td>{e(rupiah(d["value"])) if d["value"] else "-"}</td><td>{e(d["stage"])}<br><small>{last} · diam {idle} hr</small></td>'
                f'<td>{contract}</td><td><form method="post" action="/deal/aksi" class="inline">{csrf}<input type="hidden" name="id" value="{d["id"]}">'
                f'<select name="tahap">{options(d["stage"])}</select> <input type="text" name="nilai" placeholder="Nilai Rp" size="12"> '
                f'<input type="text" name="catatan" placeholder="Catatan" size="16"> <button type="submit" class="secondary">Simpan</button></form></td></tr>')
        table = ("<table><tr><th>#</th><th>Brand</th><th>PIC</th><th>Talent · periode</th><th>Nilai</th><th>Tahap</th><th>Kontrak</th><th>Ubah</th></tr>"
                 + "".join(trs) + "</table>") if trs else '<p class="muted">Belum ada deal. Lead dibuat otomatis saat bot menyerahkan brand ke admin, saat AI melihat kesepakatan di chat, atau saat kontrak dibuat.</p>'
        new = (f'<form method="post" action="/deal/baru" class="card">{csrf}<h2>Tambah deal manual</h2><div class="row">'
               f'<div><label>Brand</label><input type="text" name="brand" required></div>'
               f'<div><label>Nomor WhatsApp klien <small>(opsional)</small></label><input type="text" name="wa" placeholder="62812..."></div>'
               f'<div><label>Nilai (Rp)</label><input type="text" name="nilai"></div></div><button type="submit">Tambah sebagai Lead</button></form>')
        push = (f'<form method="post" action="/deal/sheet" class="inline">{csrf}<button type="submit" class="secondary">Kirim ke Google Sheet sekarang</button></form>'
                if cfg.deals_sheet_url else "")
        legend = " → ".join(e(s) for s in dl.stages) + f" · {e(LOST)}"
        return (f'<h1>Deal</h1><p class="muted">Tahap: {legend}. Lunas dan Batal hanya ditandai supervisor. '
                f'Nilai diambil dari kontrak yang dibuat di aplikasi.</p>{sheet}{push}<div class="card scroll">{table}</div>{new}')

    @r.get("/deal", response_class=HTMLResponse)
    async def deals_page(request: Request, msg: str = ""):
        user = await who(request)
        return show("Deal", page_html(user), user, "/deal", msg)

    @r.post("/deal/aksi")
    async def deals_action(request: Request):
        user = await who(request)
        f = await form(request)
        deal_action(deals(), user, int(f.get("id") or 0), str(f.get("tahap", "")), str(f.get("nilai", "")), str(f.get("catatan", "")))
        return RedirectResponse("/deal?msg=Tersimpan.", status_code=303)

    @r.post("/deal/baru")
    async def deals_new(request: Request):
        user = await who(request)
        f = await form(request)
        brand = str(f.get("brand", "")).strip()
        if not brand:
            raise HTTPException(400, "Nama brand wajib diisi.")
        wa = "".join(ch for ch in str(f.get("wa", "")) if ch.isdigit())
        value = parse_amount(str(f.get("nilai", ""))) if str(f.get("nilai", "")).strip() else 0
        deals().ensure_lead(wa, brand, user.key if user.role == "admin" else None, int(time.time()), value=value or 0,
                            source=f"manual:{user.key}")
        return RedirectResponse("/deal?msg=Lead ditambahkan.", status_code=303)

    @r.post("/deal/sheet")
    async def deals_sheet(request: Request):
        await who(request)
        await form(request)
        ok = deals().push(reason="manual", force=True)
        return RedirectResponse("/deal?msg=" + ("Dikirim ke Sheet." if ok else "Sheet belum dihubungkan."), status_code=303)
