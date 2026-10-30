"""The SPK page: drop a job's documents → check the SOW table → one SPK per talent (+ brand contract) → ZIP."""
from __future__ import annotations

import datetime as dt
import io
import json
import re
import threading
import time
import zipfile

from fastapi import APIRouter, HTTPException, Request
from starlette.datastructures import UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from . import spk
from .safety import content_disposition
from .terbilang import parse_amount, rupiah
from .ui_common import e

STYLE = """<style>
.drop{border:2px dashed var(--line);border-radius:10px;padding:28px 16px;text-align:center;cursor:pointer;background:var(--bg)}
.drop.over{border-color:var(--accent);background:color-mix(in srgb,var(--accent) 8%,transparent)}
.drop b{display:block;font-size:16px;margin-bottom:4px}.files{margin:8px 0 0;padding-left:18px;text-align:left}
.sow input{min-width:70px;padding:6px}.sow input[list]{min-width:150px}.sow td{padding:4px 3px}.sow .num{text-align:right}
.steps{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 4px}.steps span{padding:4px 10px;border-radius:999px;border:1px solid var(--line);font-size:13px}
.steps span.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.tl{display:flex;justify-content:space-between;gap:12px;align-items:center;padding:10px 0;border-bottom:1px solid var(--line)}
.tl:last-child{border-bottom:0}.pill{font-size:12px;padding:2px 8px;border-radius:999px;background:var(--bg);border:1px solid var(--line)}
.pill.ok{border-color:var(--ok);color:var(--ok)}.pill.bad{border-color:var(--bad);color:var(--bad)}
</style>"""
ACCEPT = ".pdf,.docx,.xlsx,.csv,.pptx,.png,.jpg,.jpeg,.webp,.txt"


def add_routes(r: APIRouter, ui, who, form, show) -> None:
    sdir = ui.home / "data" / "spk"

    def job_row(jid: int):
        j = ui.store.one("SELECT * FROM spk_jobs WHERE id=?", (jid,))
        if not j:
            raise HTTPException(404, "Pekerjaan SPK tidak ditemukan.")
        return j

    def docs_of(jid: int):
        return ui.store.q("SELECT c.*, t.name tname FROM contracts c LEFT JOIN contract_templates t ON t.id=c.template_id "
                          "WHERE c.batch_id=? AND c.status='aktif' ORDER BY c.id", (jid,))

    def templates(doc_type: str):
        return [t for t in ui.store.q("SELECT * FROM contract_templates WHERE status='approved' ORDER BY name")
                if ui.doc_type(t) == doc_type]

    def steps(n: int) -> str:
        names = ["1. Upload dokumen", "2. Cek tabel SOW", "3. Buat SPK"]
        return '<div class="steps">' + "".join(f'<span class="{"on" if i == n else ""}">{s}</span>' for i, s in enumerate(names)) + "</div>"

    # ---------------------------------------------------------------- AI reading in the background
    def start_reading(jid: int) -> None:
        def run():
            from . import doc_import
            j = job_row(jid)
            names = json.loads(j["files"] or "[]")
            blocks, notes = [], []
            for name in names:
                p = sdir / str(jid) / name
                try:
                    blocks += [{"type": "text", "text": f"--- File: {name} ---"}] + doc_import.extract(p.read_bytes(), name)
                except Exception as ex:                      # one bad file must not stop the others
                    notes.append(f"{name}: {ex}")
            pasted = sdir / str(jid) / "_teks.txt"
            if pasted.exists():
                blocks.append({"type": "text", "text": "--- Teks yang ditempel ---\n" + pasted.read_text(encoding="utf-8")})
            key = ui.cfg().anthropic_api_key
            if not key:
                data, status = {"rows": []}, "review"
                notes.append("AI belum aktif (Claude API key kosong di Pengaturan): isi tabel di bawah secara manual.")
            else:
                try:
                    data = ui.spk_analyzer(blocks, key, ui.store)
                    notes += data.pop("uncertain", [])
                    status = "review"
                except Exception as ex:
                    data, status = {"rows": []}, "failed"
                    notes.append(str(ex) if isinstance(ex, spk.SpkError) else f"Gagal membaca dokumen ({type(ex).__name__}).")
            old = json.loads(j["data"] or "{}")
            data = {**data, **{k: old[k] for k in ("commission_pct",) if k in old}}
            ui.store.x("UPDATE spk_jobs SET status=?, data=?, notes=?, updated_at=? WHERE id=?",
                       (status, json.dumps(data, ensure_ascii=False), json.dumps(notes, ensure_ascii=False), int(time.time()), jid))
        threading.Thread(target=run, name=f"spk-{jid}", daemon=True).start()

    # ---------------------------------------------------------------- pages
    def list_page() -> str:
        deals = ui.store.q("SELECT id, brand, stage FROM deals WHERE stage!='Batal' ORDER BY updated_at DESC LIMIT 50")
        deal_opts = "".join(f'<option value="{d["id"]}">#{d["id"]} {e(d["brand"] or "-")} · {e(d["stage"])}</option>' for d in deals)
        new = (f'<form method="post" action="/spk/baru" enctype="multipart/form-data" class="card" id="newjob">'
               f'<input type="hidden" name="csrf" value="{ui.csrf}"><h2>Pekerjaan baru</h2>{steps(0)}'
               f'<p class="muted">Masukkan semua yang ada untuk satu pekerjaan: brief brand, SOW, PO, email, chat WhatsApp, '
               f'rate sheet, deck atau screenshot. AI menyalin isinya ke tabel SOW; kamu mengecek sebelum dokumen dibuat.</p>'
               f'<label class="drop" id="drop"><input type="file" name="files" id="files" multiple accept="{ACCEPT}" hidden>'
               f'<b>⬆ Tarik file ke sini</b><span class="muted">atau klik untuk memilih · PDF, Word, Excel, PowerPoint, CSV, '
               f'gambar · maks. {spk.MAX_FILES} file, total 25 MB</span><ul class="files" id="flist"></ul></label>'
               f'<label>Atau tempel teks <small>(email / chat WhatsApp / catatan)</small></label>'
               f'<textarea name="text" rows="5" placeholder="Halo kak, untuk campaign serum Glowskin kami mau ambil Dewi 2 TikTok tayang 20 Nov ..."></textarea>'
               f'<div class="row"><div><label>Nama pekerjaan <small>(opsional)</small></label>'
               f'<input type="text" name="title" placeholder="Glowskin – launch serum Nov 2026"></div>'
               f'<div><label>Deal terkait <small>(opsional)</small></label><select name="deal_id"><option value="0">-</option>{deal_opts}</select></div></div>'
               f'<br><button type="submit">Baca dengan AI →</button></form>'
               '<script>(()=>{const d=document.getElementById("drop"),i=document.getElementById("files"),l=document.getElementById("flist");'
               'const show=()=>{l.innerHTML="";[...i.files].forEach(f=>{const li=document.createElement("li");'
               'li.textContent=f.name+" ("+Math.ceil(f.size/1024)+" KB)";l.appendChild(li)})};'
               '["dragenter","dragover"].forEach(ev=>d.addEventListener(ev,x=>{x.preventDefault();d.classList.add("over")}));'
               '["dragleave","drop"].forEach(ev=>d.addEventListener(ev,x=>{x.preventDefault();d.classList.remove("over")}));'
               'd.addEventListener("drop",x=>{const t=new DataTransfer();[...i.files,...x.dataTransfer.files].forEach(f=>t.items.add(f));'
               'i.files=t.files;show()});i.addEventListener("change",show)})()</script>')
        rows = ui.store.q("SELECT * FROM spk_jobs ORDER BY id DESC LIMIT 100")
        trs = []
        for j in rows:
            data = json.loads(j["data"] or "{}")
            n_t = len(spk.talents_of(data.get("rows", [])))
            n_d = ui.store.one("SELECT COUNT(*) n FROM contracts WHERE batch_id=? AND status='aktif'", (j["id"],))["n"]
            st = {"reading": "⏳ AI membaca", "review": "🟠 Perlu dicek" if not n_d else f"🟢 {n_d} dokumen", "failed": "🔴 Gagal"}.get(j["status"], j["status"])
            trs.append(f'<tr><td><a href="/spk/{j["id"]}">{e(j["title"])}</a></td><td>{e(data.get("client_name", ""))}</td>'
                       f'<td>{n_t}</td><td>{st}</td><td>{dt.datetime.fromtimestamp(j["created_at"], ui.cfg().tz):%d/%m/%Y} · {e(j["created_by"] or "")}</td></tr>')
        table = ('<div class="card scroll"><h2>Pekerjaan</h2><table><tr><th>Nama</th><th>Klien</th><th>Talent</th><th>Status</th><th>Dibuat</th></tr>'
                 + "".join(trs) + "</table></div>") if trs else ""
        if not templates("spk"):
            warn = ('<div class="msg bad">Belum ada template SPK yang disetujui. Upload template SPK dari legal di menu '
                    '<a href="/template">Template</a> (jenis dokumen: SPK). Tabel SOW tetap bisa disiapkan dulu.</div>')
        else:
            warn = ""
        return f'{STYLE}<h1>SPK</h1><p class="muted">Dari dokumen pekerjaan ke SPK per talent, bernomor otomatis.</p>{warn}{new}{table}'

    def job_page(j, user) -> str:
        jid = j["id"]
        if j["status"] == "reading":
            return (f'{STYLE}<meta http-equiv="refresh" content="4"><h1>{e(j["title"])}</h1>{steps(0)}'
                    f'<div class="card"><h2>⏳ AI sedang membaca dokumen…</h2><p class="muted">Biasanya kurang dari satu menit. '
                    f'Halaman ini memuat ulang sendiri.</p></div>')
        data = json.loads(j["data"] or "{}")
        notes = json.loads(j["notes"] or "[]")
        csrf = f'<input type="hidden" name="csrf" value="{ui.csrf}">'
        files = json.loads(j["files"] or "[]")
        note_html = ("<div class=\"msg bad\"><b>Perlu dicek:</b><ul>" + "".join(f"<li>{e(n)}</li>" for n in notes) + "</ul></div>") if notes else ""
        retry = (f'<form method="post" action="/spk/{jid}/ulang" class="inline">{csrf}<button class="secondary" type="submit">↻ Baca ulang dengan AI</button></form>'
                 if files or (sdir / str(jid) / "_teks.txt").exists() else "")
        if j["status"] == "failed":
            return (f'{STYLE}<h1>{e(j["title"])}</h1>{steps(0)}{note_html}<p>{retry}</p>'
                    f'<p class="muted">Atau isi tabelnya manual:</p>' + editor(jid, data, csrf, files))
        return (f'{STYLE}<h1>{e(j["title"])}</h1>{steps(1 if not docs_of(jid) else 2)}{note_html}'
                + editor(jid, data, csrf, files) + f'<p>{retry}</p>' + generate(jid, data, csrf))

    def editor(jid: int, data: dict, csrf: str, files: list[str]) -> str:
        rows = data.get("rows") or []
        roster = [t["name"] for t in ui.store.q("SELECT name FROM talents ORDER BY name")]
        datalist = '<datalist id="roster">' + "".join(f'<option value="{e(n)}">' for n in roster) + "</datalist>"

        def money(v) -> str:
            return f"{int(v):,}".replace(",", ".") if v else ""

        def tr(i: int, r: dict) -> str:
            return (f'<tr><td><input type="text" name="r_talent_{i}" value="{e(r.get("talent", ""))}" list="roster"></td>'
                    f'<td><input type="text" name="r_platform_{i}" value="{e(r.get("platform", ""))}" size="9"></td>'
                    f'<td><input type="text" name="r_deliverable_{i}" value="{e(r.get("deliverable", ""))}"></td>'
                    f'<td><input type="text" name="r_qty_{i}" value="{e(r.get("qty", ""))}" size="3" class="num"></td>'
                    f'<td><input type="date" name="r_posting_date_{i}" value="{e(r.get("posting_date", ""))}"></td>'
                    f'<td><input type="text" name="r_client_fee_{i}" value="{money(r.get("client_fee"))}" class="num"></td>'
                    f'<td><input type="text" name="r_talent_fee_{i}" value="{money(r.get("talent_fee"))}" class="num"></td>'
                    f'<td><input type="text" name="r_notes_{i}" value="{e(r.get("notes", ""))}"></td></tr>')
        body = "".join(tr(i, x) for i, x in enumerate(rows or [{}, {}]))
        tpl_row = tr(9999, {}).replace("9999", "__I__")
        total_c = sum(int(x.get("client_fee") or 0) for x in rows)
        total_t = sum(int(x.get("talent_fee") or 0) for x in rows)
        margin = f" · selisih {rupiah(total_c - total_t)}" if total_c and total_t else ""
        src = ("<p class=\"muted\">Sumber: " + ", ".join(e(f) for f in files) + "</p>") if files else ""
        return (f'<form method="post" action="/spk/{jid}/simpan" class="card">{csrf}{datalist}<h2>Data pekerjaan</h2>{src}'
                f'<div class="row"><div><label>Klien / brand</label><input type="text" name="client_name" value="{e(data.get("client_name", ""))}"></div>'
                f'<div><label>Campaign</label><input type="text" name="campaign" value="{e(data.get("campaign", ""))}"></div></div>'
                f'<div class="row"><div><label>Mulai</label><input type="date" name="period_start" value="{e(data.get("period_start", ""))}"></div>'
                f'<div><label>Selesai</label><input type="date" name="period_end" value="{e(data.get("period_end", ""))}"></div></div>'
                f'<label>Termin pembayaran</label><textarea name="payment_terms" rows="2">{e(data.get("payment_terms", ""))}</textarea>'
                f'<h2 style="margin-top:18px">SOW</h2><p class="muted">Satu baris per deliverable. <b>Fee klien</b> masuk ke kontrak brand; '
                f'<b>fee talent</b> masuk ke SPK talent. Baris kosong diabaikan.</p>'
                f'<div class="scroll"><table class="sow" id="sow"><tr><th>Talent</th><th>Platform</th><th>Deliverable</th><th>Qty</th>'
                f'<th>Tanggal tayang</th><th>Fee klien (Rp)</th><th>Fee talent (Rp)</th><th>Catatan</th></tr>{body}</table></div>'
                f'<p class="muted">Total fee klien {rupiah(total_c)} · fee talent {rupiah(total_t)}{margin}</p>'
                f'<button type="button" class="secondary" onclick="addRow()">+ Tambah baris</button> '
                f'<label style="display:inline;font-weight:400;margin-left:12px">Isi fee talent yang kosong = fee klien dikurangi komisi '
                f'<input type="number" name="commission_pct" min="0" max="99" step="0.5" style="width:80px" '
                f'value="{e(data.get("commission_pct", ""))}"> %</label>'
                f'<br><br><button type="submit">Simpan tabel</button>'
                f'<script>let n={len(rows or [{}, {}])};const tpl={json.dumps(tpl_row)};'
                f'function addRow(){{document.querySelector("#sow tbody").insertAdjacentHTML("beforeend",tpl.replaceAll("__I__",n++));}}</script></form>')

    def generate(jid: int, data: dict, csrf: str) -> str:
        talents = spk.talents_of(data.get("rows") or [])
        spk_tpls, k_tpls = templates("spk"), templates("kontrak")
        docs = docs_of(jid)
        by_talent = {(d["talent"] or "").lower(): d for d in docs if ui.doc_type(d) != "kontrak"}
        if not talents:
            return '<div class="card"><h2>3. Buat SPK</h2><p class="muted">Isi minimal satu baris dengan nama talent dulu.</p></div>'
        if not spk_tpls:
            lines = '<p class="msg bad">Belum ada template SPK yang disetujui (menu Template, jenis SPK).</p>'
            tpl0 = None
        else:
            tpl0 = spk_tpls[0]
            lines = ""
        items = []
        for t in talents:
            rows = [x for x in data.get("rows", []) if (x.get("talent") or "").lower() == t.lower()]
            fee = sum(int(x.get("talent_fee") or 0) for x in rows)
            d = by_talent.get(t.lower())
            if d:
                status = (f'<span class="pill ok">✓ {e(d["number"])}</span> <a href="/kontrak/{d["id"]}">lihat</a> · '
                          f'<a href="/kontrak/{d["id"]}/word">⬇ Word</a>')
            else:
                warn = '<span class="pill bad">fee talent kosong</span> ' if not fee else ""
                fill = f'<a class="btn secondary" href="/kontrak/baru?t={tpl0["id"]}&job={jid}&talent={e(t)}">Isi & buat</a>' if tpl0 else ""
                status = f'{warn}<span class="pill">belum dibuat</span> {fill}'
            pieces = sum(int(x.get("qty") or 1) for x in rows)
            what = ", ".join(f'{x.get("qty") or 1}× {x.get("platform", "")} {x.get("deliverable", "")}'.strip() for x in rows)
            items.append(f'<div class="tl"><div><b>{e(t)}</b><br><small>{pieces} konten ({e(what)}) · fee talent {rupiah(fee)}</small></div>'
                         f'<div>{status}</div></div>')
        tpl_select = ("" if len(spk_tpls) < 2 else
                      '<label>Template SPK</label><select name="tid">' +
                      "".join(f'<option value="{t["id"]}">{e(t["name"])}</option>' for t in spk_tpls) + "</select>")
        batch = (f'<form method="post" action="/spk/{jid}/buat" style="margin-top:12px">{csrf}'
                 f'{tpl_select}<input type="hidden" name="tid" value="{tpl0["id"]}">'
                 f'<button type="submit">Buat semua SPK yang belum ada</button>'
                 f'<span class="muted"> · yang datanya belum lengkap dilewati dan disebutkan</span></form>' if tpl0 and len(by_talent) < len(talents) else "")
        if len(spk_tpls) >= 2:
            batch = batch.replace(f'<input type="hidden" name="tid" value="{tpl0["id"]}">', "")
        brand = ""
        k_doc = next((d for d in docs if ui.doc_type(d) == "kontrak"), None)
        if k_doc:
            brand = (f'<div class="tl"><div><b>Kontrak brand</b><br><small>{e(data.get("client_name", ""))}</small></div>'
                     f'<div><span class="pill ok">✓ {e(k_doc["number"])}</span> <a href="/kontrak/{k_doc["id"]}">lihat</a></div></div>')
        elif k_tpls:
            brand = (f'<div class="tl"><div><b>Kontrak brand</b> <small>(opsional)</small><br><small>{e(data.get("client_name", ""))} · '
                     f'fee klien {rupiah(sum(int(x.get("client_fee") or 0) for x in data.get("rows", [])))}</small></div>'
                     f'<div>' + " ".join(f'<a class="btn secondary" href="/kontrak/baru?t={t["id"]}&job={jid}">{e(t["name"])}</a>' for t in k_tpls)
                     + '</div></div>')
        zipb = f'<a class="btn" href="/spk/{jid}/zip">⬇ Download semua (ZIP)</a>' if docs else ""
        return (f'<div class="card"><h2>3. Buat SPK</h2>{lines}<p class="muted">Satu SPK per talent dari template yang disetujui legal, '
                f'nomor dari penghitung SPK (menu Nomor). Bagian yang tidak ada di tabel (alamat, NIK, rekening talent) '
                f'diisi di formulir “Isi & buat”.</p>{"".join(items)}{brand}{batch}<p>{zipb}</p></div>')

    # ---------------------------------------------------------------- routes
    @r.get("/spk", response_class=HTMLResponse)
    async def spk_list(request: Request, msg: str = ""):
        user = await who(request)
        return show("SPK", list_page(), user, "/spk", msg)

    @r.post("/spk/baru")
    async def spk_new(request: Request):
        user = await who(request)
        f = await form(request)
        uploads = [x for x in f.getlist("files") if isinstance(x, UploadFile) and x.filename] if hasattr(f, "getlist") else []
        text = str(f.get("text") or "").strip()
        if not uploads and not text:
            return RedirectResponse("/spk?msg=" + "Pilih file atau tempel teks dulu.", status_code=303)
        if len(uploads) > spk.MAX_FILES:
            raise HTTPException(400, f"Maksimal {spk.MAX_FILES} file per pekerjaan.")
        datas, total = [], 0
        for u in uploads:
            b = await u.read()
            total += len(b)
            if total > spk.MAX_JOB_BYTES:
                raise HTTPException(400, "Total file terlalu besar (maks. 25 MB).")
            name = re.sub(r"[^\w .()-]+", "_", u.filename).strip(" .")[:120] or "file"
            datas.append((name, b))
        now = int(time.time())
        title = str(f.get("title") or "").strip()[:120] or (
            datas[0][0].rsplit(".", 1)[0] if datas else f"Pekerjaan {dt.datetime.now(ui.cfg().tz):%d/%m/%Y %H:%M}")
        deal_id = int(f.get("deal_id") or 0) or None
        jid = ui.store.x("INSERT INTO spk_jobs(title,status,data,files,deal_id,created_at,created_by,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                         (title, "reading", "{}", json.dumps([n for n, _ in datas], ensure_ascii=False), deal_id, now, user.key, now)).lastrowid
        d = sdir / str(jid)
        d.mkdir(parents=True, exist_ok=True)
        seen = set()
        for name, b in datas:
            while name in seen:
                name = "_" + name
            seen.add(name)
            (d / name).write_bytes(b)
        ui.store.x("UPDATE spk_jobs SET files=? WHERE id=?", (json.dumps(sorted(seen), ensure_ascii=False), jid))
        if text:
            (d / "_teks.txt").write_text(text[:100_000], encoding="utf-8")
        ui.store.audit(user.key, "spk_job_new", None, {"job": jid, "files": len(datas)})
        start_reading(jid)
        return RedirectResponse(f"/spk/{jid}", status_code=303)

    @r.get("/spk/{jid}", response_class=HTMLResponse)
    async def spk_job(jid: int, request: Request, msg: str = "", ok: int = 1):
        user = await who(request)
        j = job_row(jid)
        return show("SPK", job_page(j, user), user, "/spk", msg, bool(ok))

    @r.post("/spk/{jid}/ulang")
    async def spk_retry(jid: int, request: Request):
        await who(request)
        await form(request)
        job_row(jid)
        ui.store.x("UPDATE spk_jobs SET status='reading', updated_at=? WHERE id=?", (int(time.time()), jid))
        start_reading(jid)
        return RedirectResponse(f"/spk/{jid}", status_code=303)

    @r.post("/spk/{jid}/simpan")
    async def spk_save(jid: int, request: Request):
        user = await who(request)
        f = await form(request)
        j = job_row(jid)
        data = json.loads(j["data"] or "{}")
        for k in ("client_name", "campaign", "payment_terms"):
            data[k] = str(f.get(k) or "").strip()[:600]
        for k in ("period_start", "period_end"):
            data[k] = spk._date(f.get(k))
        rows, i, problems = [], 0, []
        while f"r_talent_{i}" in f or f"r_deliverable_{i}" in f or i < 3:
            if f"r_talent_{i}" not in f and f"r_deliverable_{i}" not in f:
                i += 1
                continue
            row = {"talent": str(f.get(f"r_talent_{i}") or "").strip()[:80], "platform": str(f.get(f"r_platform_{i}") or "").strip()[:30],
                   "deliverable": str(f.get(f"r_deliverable_{i}") or "").strip()[:120],
                   "qty": int(str(f.get(f"r_qty_{i}") or "1").strip() or 1) if str(f.get(f"r_qty_{i}") or "1").strip().isdigit() else 1,
                   "posting_date": spk._date(f.get(f"r_posting_date_{i}")), "notes": str(f.get(f"r_notes_{i}") or "").strip()[:200]}
            for k in ("client_fee", "talent_fee"):
                raw = str(f.get(f"r_{k}_{i}") or "").strip()
                try:
                    row[k] = parse_amount(raw) if raw else 0
                except ValueError:
                    row[k] = 0
                    problems.append(f"baris {i + 1}: “{raw}” tidak terbaca")
            if row["talent"] or row["deliverable"]:
                rows.append(row)
            i += 1
        pct = str(f.get("commission_pct") or "").strip()
        filled = 0
        if pct:
            try:
                data["commission_pct"] = float(pct)
                filled = spk.apply_commission(rows, float(pct))
            except ValueError:
                problems.append("komisi tidak terbaca")
        else:
            data.pop("commission_pct", None)
        data["rows"] = rows
        ui.store.x("UPDATE spk_jobs SET data=?, status='review', updated_at=? WHERE id=?",
                   (json.dumps(data, ensure_ascii=False), int(time.time()), jid))
        ui.store.audit(user.key, "spk_job_save", None, {"job": jid, "rows": len(rows)})
        msg = "Tersimpan." + (f" {filled} fee talent diisi dari komisi {pct}%." if filled else "") + \
              (" Periksa: " + "; ".join(problems) if problems else "")
        from urllib.parse import quote
        return RedirectResponse(f"/spk/{jid}?msg={quote(msg)}&ok={int(not problems)}", status_code=303)

    @r.post("/spk/{jid}/buat")
    async def spk_make_all(jid: int, request: Request):
        user = await who(request)
        f = await form(request)
        j = job_row(jid)
        tpl = ui._approved(int(f.get("tid") or 0))
        if ui.doc_type(tpl) != "spk":
            raise HTTPException(400, "Pilih template SPK.")
        spec = json.loads(tpl["spec"])
        data = json.loads(j["data"] or "{}")
        done = {(d["talent"] or "").lower() for d in docs_of(jid) if ui.doc_type(d) != "kontrak"}
        made, skipped = [], []
        from .numbering import NumberError
        for t in spk.talents_of(data.get("rows") or []):
            if t.lower() in done:
                continue
            if not sum(int(x.get("talent_fee") or 0) for x in data["rows"] if (x.get("talent") or "").lower() == t.lower()):
                skipped.append(f"{t} (fee talent kosong)")
                continue
            values, rows = ui.job_prefill(tpl, jid, t)
            try:
                values, rows = ui.parse_contract_form(tpl, spk.as_form(spec, values, rows))
            except ValueError as ex:
                skipped.append(f"{t} ({ex})")
                continue
            missing = spk.missing_fields(spec, values)
            if missing:
                skipped.append(f"{t} (belum ada: {', '.join(missing[:4])}{'…' if len(missing) > 4 else ''})")
                continue
            try:
                cid = ui.store_contract(tpl, values, rows, user, 0, job=jid, talent=t)
            except NumberError as ex:
                skipped.append(f"{t} ({ex})")
                continue
            made.append(ui._contract(cid)["number"])
        msg = (f"{len(made)} SPK dibuat: {', '.join(made)}." if made else "Tidak ada SPK baru.") + \
              (f" Dilewati, lengkapi lewat “Isi & buat”: {'; '.join(skipped)}." if skipped else "")
        from urllib.parse import quote
        return RedirectResponse(f"/spk/{jid}?msg={quote(msg)}&ok={int(not skipped)}", status_code=303)

    @r.get("/spk/{jid}/zip")
    async def spk_zip(jid: int, request: Request):
        user = await who(request)
        j = job_row(jid)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for d in docs_of(jid):
                p = ui.cdir / f"{d['id']}.docx"
                if p.exists():
                    who_for = d["talent"] or d["client_name"] or ""
                    z.writestr(re.sub(r"[^\w .-]+", "-", f"{d['number']} {who_for}").strip(" -") + ".docx", p.read_bytes())
        ui.store.audit(user.key, "spk_zip", None, {"job": jid})
        name = re.sub(r"[^\w .-]+", "-", j["title"]).strip(" -") or f"spk-{jid}"
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": content_disposition(f"{name}.zip")})
