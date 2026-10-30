"""The Nomor page: number format per document type, "continue from our last number", numbers taken
outside the app, and the register of every number issued (supervisors edit; everyone can look)."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .numbering import RESETS, NumberError
from .ui_common import e

SOURCE_LABELS = {"app": "aplikasi", "manual": "diketik manual", "external": "di luar aplikasi"}


def add_routes(r: APIRouter, ui, who, form, show) -> None:
    def page_html(user, only: str = "") -> str:
        n = ui.numbering()
        types = n.types()
        can = user.can_manage_templates
        dis = "" if can else "disabled"
        csrf = f'<input type="hidden" name="csrf" value="{ui.csrf}">'
        cards = []
        for key, t in types.items():
            reset_opts = "".join(f'<option value="{k}"{" selected" if k == t["reset"] else ""}>{v}</option>' for k, v in RESETS.items())
            cards.append(
                f'<div class="card"><h2>{e(t["label"])} <span class="muted" style="font-weight:400">· berikutnya: '
                f'<code>{e(n.peek(key))}</code></span></h2>'
                f'<form method="post" action="/nomor/jenis">{csrf}<input type="hidden" name="key" value="{e(key)}">'
                f'<div class="row"><div><label>Nama</label><input type="text" name="label" value="{e(t["label"])}" {dis}></div>'
                f'<div><label>Format <small>{{seq:03d}} urut · {{roman_month}} XI · {{month}} 11 · {{year}} 2026 · {{yy}} 26</small></label>'
                f'<input type="text" name="format" value="{e(t["format"])}" {dis}></div>'
                f'<div><label>Nomor urut mulai lagi dari 1</label><select name="reset" {dis}>{reset_opts}</select></div></div>'
                f'<button type="submit" class="secondary" {dis}>Simpan format</button></form>'
                f'<form method="post" action="/nomor/terakhir" style="margin-top:14px">{csrf}<input type="hidden" name="key" value="{e(key)}">'
                f'<label>Nomor terakhir yang sudah dipakai <small>(angka urutnya, mis. 47, atau nomor lengkapnya; '
                f'nomor berikutnya melanjutkan dari sini)</small></label>'
                f'<div class="row"><div><input type="text" name="last" placeholder="47" {dis}></div>'
                f'<div><button type="submit" class="secondary" {dis}>Lanjutkan dari nomor ini</button></div></div></form>'
                f'<form method="post" action="/nomor/luar" style="margin-top:14px">{csrf}<input type="hidden" name="key" value="{e(key)}">'
                f'<label>Catat nomor yang dipakai di luar aplikasi <small>(mis. kontrak dari kertas klien); nomor ini tidak akan dipakai lagi</small></label>'
                f'<div class="row"><div><input type="text" name="number" placeholder="{e(n.peek(key))}" {dis}></div>'
                f'<div><input type="text" name="note" placeholder="Keterangan (klien, alasan)" {dis}></div>'
                f'<div><button type="submit" class="secondary" {dis}>Catat</button></div></div></form></div>')
        new_type = ("" if not can else
                    f'<div class="card"><h2>Tambah jenis dokumen</h2><form method="post" action="/nomor/jenis">{csrf}'
                    f'<div class="row"><div><label>Kode <small>(mis. quotation)</small></label><input type="text" name="key" required></div>'
                    f'<div><label>Nama</label><input type="text" name="label" required placeholder="Quotation"></div>'
                    f'<div><label>Format</label><input type="text" name="format" required placeholder="{{seq:03d}}/HRZ-QUO/{{roman_month}}/{{year}}"></div>'
                    f'<div><label>Reset</label><select name="reset">'
                    + "".join(f'<option value="{k}">{v}</option>' for k, v in RESETS.items())
                    + '</select></div></div><button type="submit" class="secondary">Tambah</button></form></div>')
        rows = n.register(only or None)
        filt = " ".join(f'<a class="btn{"" if k == only else " secondary"}" href="/nomor?jenis={e(k)}">{e(t["label"])}</a>'
                        for k, t in types.items())
        filt += f' <a class="btn{"" if not only else " secondary"}" href="/nomor">Semua</a>'
        trs = []
        for x in rows:
            when = dt.datetime.fromtimestamp(x["created_at"], ui.cfg().tz).strftime("%d/%m/%Y")
            doc = f'<a href="/kontrak/{x["document_id"]}">buka</a>' if x["document_id"] else ""
            cancel = (f'<form method="post" action="/nomor/batal" class="inline">{csrf}<input type="hidden" name="number" value="{e(x["number"])}">'
                      f'<input type="text" name="reason" placeholder="alasan" size="10"> <button type="submit" class="secondary">Batalkan</button></form>'
                      if can and x["status"] == "used" else "")
            trs.append(f'<tr{" class=muted" if x["status"] == "batal" else ""}><td><code>{e(x["number"])}</code></td>'
                       f'<td>{e(types.get(x["doc_type"], {}).get("label", x["doc_type"]))}</td><td>{e(SOURCE_LABELS.get(x["source"], x["source"]))}</td>'
                       f'<td>{"Batal" if x["status"] == "batal" else "Dipakai"}</td><td>{e(x["note"] or "")}</td>'
                       f'<td>{when} · {e(x["created_by"] or "")}</td><td>{doc}</td><td>{cancel}</td></tr>')
        table = ("<table><tr><th>Nomor</th><th>Jenis</th><th>Sumber</th><th>Status</th><th>Keterangan</th><th>Tanggal</th><th></th><th></th></tr>"
                 + "".join(trs) + "</table>") if trs else '<p class="muted">Belum ada nomor yang dikeluarkan.</p>'
        note = "" if can else '<p class="muted">Hanya supervisor yang bisa mengubah format dan nomor.</p>'
        return (f'<h1>Nomor dokumen</h1><p class="muted">Setiap jenis dokumen punya penghitung dan format sendiri. '
                f'Nomor tidak pernah dipakai dua kali: nomor yang dicatat dari luar aplikasi juga dilewati.</p>{note}'
                + "".join(cards) + new_type
                + f'<div class="card scroll"><h2>Daftar nomor</h2><p>{filt}</p>{table}</div>')

    def manager(user) -> None:
        if not user.can_manage_templates:
            raise HTTPException(403, "Hanya supervisor yang bisa mengubah nomor dokumen.")

    def back(msg: str, ok: bool = True) -> RedirectResponse:
        from urllib.parse import quote
        return RedirectResponse(f"/nomor?msg={quote(msg)}&ok={int(ok)}", status_code=303)

    @r.get("/nomor", response_class=HTMLResponse)
    async def numbers_page(request: Request, jenis: str = "", msg: str = "", ok: int = 1):
        user = await who(request)
        return show("Nomor", page_html(user, jenis), user, "/nomor", msg, bool(ok))

    @r.post("/nomor/jenis")
    async def save_type(request: Request):
        user = await who(request)
        manager(user)
        f = await form(request)
        try:
            ui.numbering().save_type(str(f.get("key", "")), str(f.get("label", "")), str(f.get("format", "")),
                                     str(f.get("reset", "yearly")), user.key)
        except NumberError as ex:
            return back(str(ex), False)
        return back("Format disimpan.")

    @r.post("/nomor/terakhir")
    async def set_last(request: Request):
        user = await who(request)
        manager(user)
        f = await form(request)
        n = ui.numbering()
        try:
            seq = n.set_last(str(f.get("key", "")), str(f.get("last", "")), user.key)
        except NumberError as ex:
            return back(str(ex), False)
        return back(f"Disimpan: nomor urut terakhir {seq}. Berikutnya {n.peek(str(f.get('key')))}.")

    @r.post("/nomor/luar")
    async def external(request: Request):
        user = await who(request)
        manager(user)
        f = await form(request)
        try:
            ui.numbering().register_external(str(f.get("key", "")), str(f.get("number", "")), user.key, str(f.get("note", "")))
        except NumberError as ex:
            return back(str(ex), False)
        return back("Nomor dicatat; tidak akan dikeluarkan lagi.")

    @r.post("/nomor/batal")
    async def cancel(request: Request):
        user = await who(request)
        manager(user)
        f = await form(request)
        try:
            ui.numbering().cancel(str(f.get("number", "")), user.key, str(f.get("reason", "")))
        except NumberError as ex:
            return back(str(ex), False)
        return back("Nomor ditandai batal (tidak dipakai ulang).")
