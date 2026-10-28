"""The "Pengetahuan" page: KOL list, availability, rate card, FAQ, company info and bot texts.

Everyone can view it; only supervisors (and the host PC) can change it.
"""
from __future__ import annotations

import io
import json
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from . import talents as tl
from .kb import DEFAULT_TEXTS, INFO_SECTIONS, KB, TEXT_LABELS
from .sources.meta_send import month_sends
from .terbilang import rupiah
from .ui_common import e

MAX_UPLOAD = 10 * 1024 * 1024


def example_roster_xlsx() -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "KOL"
    ws.append(["Nama", "Handle IG", "Handle TikTok", "Followers IG", "Followers TikTok", "Niche", "Domisili",
               "Rate IG Reels", "Rate IG Story", "Rate TikTok", "No HP", "Handler", "Catatan internal"])
    ws.append(["Dewi Lestari", "@dewi.lestari", "@dewilestari", "120rb", "300rb", "beauty, skincare", "Jakarta",
               15000000, 5000000, 12000000, "0812xxxxxxx", "admin1", "tidak ditampilkan ke klien"])
    ws2 = wb.create_sheet("Jadwal")
    ws2.append(["Nama", "Mulai", "Selesai"])
    ws2.append(["Dewi Lestari", "2026-11-10", "2026-11-12"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def add_routes(r: APIRouter, ui, who, form, show) -> None:
    """Register the knowledge pages on the contracts router (shared login and look)."""
    kb = KB(ui.store)
    kb_dir = ui.home / "data" / "kb"

    def manager(user) -> None:
        if not user.can_manage_templates:
            raise HTTPException(403, "Hanya supervisor yang bisa mengubah pengetahuan.")

    @r.get("/pengetahuan", response_class=HTMLResponse)
    def knowledge(request: Request, msg: str = "", ok: int = 1):
        user = who(request)
        return show("Pengetahuan", page(user), user, "/pengetahuan", msg, bool(ok))

    def page(user) -> str:
        cfg = ui.cfg()
        can = user.can_manage_templates
        dis = "" if can else "disabled"
        csrf = f'<input type="hidden" name="csrf" value="{ui.csrf}">'
        n_kol = ui.store.one("SELECT COUNT(*) n FROM talents")["n"]
        n_busy = ui.store.one("SELECT COUNT(*) n FROM talent_busy")["n"]
        sends = month_sends(ui.store)
        status = (f'<p>Bot menu brand: <b>{"aktif" if cfg.intake_enabled else "mati"}</b> · FAQ otomatis: '
                  f'<b>{"aktif" if cfg.faq_auto_send else "mati"}</b> · Saran balasan AI: <b>{"aktif" if cfg.drafts_enabled else "mati"}</b> · '
                  f'Mode: <b>{"LIVE" if cfg.live else "SHADOW (tidak ada yang terkirim)"}</b><br>'
                  f'Pesan dikirim bot bulan ini: <b>{sends}</b> / batas {cfg.monthly_send_budget} '
                  f'<small>(gratis dari Meta sampai 1.000 per bulan)</small></p>'
                  f'<p class="muted">Nyalakan/matikan di Pengaturan. Bot hanya menyapa kontak baru (bukan talent / klien lama) '
                  f'dan berhenti begitu admin membalas.</p>')

        rows = ui.store.q("SELECT id, name, data FROM talents ORDER BY name LIMIT 300")
        kol_rows = "".join(
            f"<tr><td>{e(t['name'])}</td><td>{e('; '.join(f'{k}: {v}' for k, v in json.loads(t['data'])['info'].items()))}</td>"
            f"<td>{e('; '.join(f'{k}: {rupiah(v)}' for k, v in json.loads(t['data'])['rates'].items()))}</td></tr>"
            for t in rows) or '<tr><td colspan="3" class="muted">Belum ada daftar KOL.</td></tr>'
        upload = lambda action, label, accept: (f'<form method="post" action="{action}" enctype="multipart/form-data">{csrf}'  # noqa: E731
                                                f'<input type="file" name="file" accept="{accept}" required {dis}> '
                                                f'<button type="submit" {dis}>{label}</button></form>')
        kol = (f'<div class="card"><h2>Daftar KOL & rate ({n_kol})</h2>'
               f'<p class="muted">Excel (.xlsx) atau CSV, satu baris per KOL. Kolom bebas, misalnya: Nama, Handle IG, Followers IG, '
               f'Niche, Rate IG Reels, Rate TikTok… Kolom berawalan “Rate/Harga” dibaca sebagai harga. Kolom No HP, Handler, '
               f'Email, Rekening, dan “Catatan internal” <b>tidak pernah</b> ditampilkan ke AI atau klien. Upload baru mengganti daftar lama. '
               f'<a href="/pengetahuan/contoh.xlsx">Download contoh</a>.</p>{upload("/pengetahuan/kol", "Upload daftar KOL", ".xlsx,.csv")}'
               f'<div class="scroll" style="max-height:320px;overflow:auto;margin-top:12px"><table><tr><th>Nama</th><th>Info (bisa dilihat klien)</th>'
               f'<th>Rate</th></tr>{kol_rows}</table></div></div>')
        cal = (f'<div class="card"><h2>Jadwal tambahan dari Excel ({n_busy} periode terisi total)</h2>'
               f'<p class="muted">Excel/CSV dengan kolom <b>Nama</b>, <b>Mulai</b>, <b>Selesai</b> untuk tanggal talent <b>tidak tersedia</b>. '
               f'Kolom lain (brand, pekerjaan, catatan) diabaikan dan tidak disimpan, jadi bot hanya bisa bilang '
               f'“tersedia / tidak tersedia”, tanpa alasan.</p>{upload("/pengetahuan/jadwal", "Upload jadwal", ".xlsx,.csv")}</div>')
        cal_status = json.loads(ui.store.get_kv("calendar_status", "{}") or "{}")
        urls = kb.get("calendar_urls", []) or []
        n_own = ui.store.one("SELECT COUNT(*) n FROM talents WHERE calendar_url LIKE 'http%'")["n"]
        if cal_status:
            when = time.strftime("%d/%m %H:%M", time.localtime(cal_status["at"]))
            sync_line = (f'Terakhir sinkron {when}: {cal_status["calendars"]} kalender, {cal_status["events"]} acara, '
                         f'{cal_status["matched"]} cocok dengan talent'
                         + (f', <b>{cal_status["unmatched"]} tidak cocok</b> (nama talent tidak ada di judul acara)' if cal_status["unmatched"] else "")
                         + ("".join(f'<br>⚠️ {e(x)}' for x in cal_status["errors"]) if cal_status["errors"] else ""))
        else:
            sync_line = "Belum pernah sinkron."
        gcal = (f'<div class="card"><h2>Google Calendar (otomatis tiap 15 menit)</h2>'
                f'<p class="muted">Google Calendar → Setelan kalender → <b>Alamat rahasia dalam format iCal</b> → salin. '
                f'Satu alamat per baris. Di kalender bersama, judul acara harus memuat <b>nama</b> atau <b>@handle</b> talent '
                f'(mis. “Dewi – shooting”). Kalender per talent: isi kolom “Kalender” di daftar KOL. '
                f'Yang disimpan hanya tanggalnya; judul dan isi acara tidak disimpan. Acara bertanda “Free/Tersedia” diabaikan.</p>'
                f'<p>{len(urls)} kalender bersama · {n_own} kalender per talent · {sync_line}</p>'
                f'<form method="post" action="/pengetahuan/kalender">{csrf}'
                f'<label>Alamat iCal kalender bersama <small>(kosongkan untuk tidak mengubah)</small></label>'
                f'<textarea name="urls" rows="3" placeholder="https://calendar.google.com/calendar/ical/.../private-.../basic.ics" {dis}></textarea>'
                f'<label style="font-weight:400"><input type="checkbox" name="clear" {dis}> Hapus semua alamat kalender bersama</label><br>'
                f'<button type="submit" {dis}>Simpan & sinkron sekarang</button></form></div>')
        has_pdf = (kb_dir / "ratecard.pdf").exists()
        rc = (f'<div class="card"><h2>File rate card (PDF)</h2><p class="muted">Dikirim bot ke brand yang belum menyebut talent tertentu. '
              f'{"✅ Sudah ada. " if has_pdf else "Belum ada. "}</p>{upload("/pengetahuan/ratecard", "Upload PDF", ".pdf")}</div>')

        faqs = kb.faqs()
        faq_rows = "".join(
            f'<div class="card" style="margin:8px 0"><input type="hidden" name="faq_id_{i}" value="{e(f.get("id"))}">'
            f'<label>Pertanyaan (satu variasi per baris)</label><textarea name="faq_q_{i}" rows="2" {dis}>{e(chr(10).join(f.get("questions", [])))}</textarea>'
            f'<label>Jawaban (Bahasa Indonesia)</label><textarea name="faq_a_{i}" rows="3" {dis}>{e(f.get("answer_id"))}</textarea>'
            f'<label>Jawaban (English, opsional)</label><textarea name="faq_en_{i}" rows="2" {dis}>{e(f.get("answer_en"))}</textarea>'
            f'<label style="font-weight:400"><input type="checkbox" name="faq_auto_{i}" {"checked" if f.get("auto_send") else ""} {dis}> '
            f'Boleh dikirim otomatis (hanya jika pertanyaan klien persis sama)</label>'
            f'<label style="font-weight:400"><input type="checkbox" name="faq_del_{i}" {dis}> Hapus</label></div>'
            for i, f in enumerate(faqs))
        new_faq = (f'<div class="card" style="margin:8px 0"><b>FAQ baru</b><input type="hidden" name="faq_id_{len(faqs)}" value="">'
                   f'<label>Pertanyaan</label><textarea name="faq_q_{len(faqs)}" rows="2" {dis}></textarea>'
                   f'<label>Jawaban</label><textarea name="faq_a_{len(faqs)}" rows="3" {dis}></textarea>'
                   f'<label>Jawaban (English)</label><textarea name="faq_en_{len(faqs)}" rows="2" {dis}></textarea>'
                   f'<label style="font-weight:400"><input type="checkbox" name="faq_auto_{len(faqs)}" {dis}> Boleh dikirim otomatis</label></div>')
        faq = (f'<form method="post" action="/pengetahuan/faq">{csrf}<div class="card"><h2>FAQ ({len(faqs)})</h2>'
               f'<p class="muted">Jawaban yang sudah disetujui. Yang dicentang “otomatis” dikirim bot apa adanya. Jangan tulis rate per talent di sini.</p>'
               f'{faq_rows}{new_faq}<button type="submit" {dis}>Simpan FAQ</button></div></form>')
        info = kb.get("info", {}) or {}
        info_html = "".join(f'<label>{e(label)}</label><textarea name="info_{k}" rows="4" {dis}>{e(info.get(k, ""))}</textarea>'
                            for k, label in INFO_SECTIONS.items())
        texts = kb.get("texts", {}) or {}
        texts_html = "".join(f'<label>{e(TEXT_LABELS.get(k, k))}</label><textarea name="text_{k}" rows="2" {dis}>{e(texts.get(k) or v)}</textarea>'
                             for k, v in DEFAULT_TEXTS.items())
        rest = (f'<form method="post" action="/pengetahuan/teks">{csrf}<div class="card"><h2>Info untuk AI</h2>'
                f'<p class="muted">Dipakai AI untuk saran balasan. Tulis fakta saja; AI tidak boleh mengarang di luar ini.</p>{info_html}</div>'
                f'<div class="card"><h2>Kalimat bot</h2><p class="muted">Dikirim bot apa adanya. {{sla}} diganti dengan janji waktu balas '
                f'dari Pengaturan; {{names}} dengan nama talent.</p>{texts_html}</div>'
                f'<button type="submit" {dis}>Simpan</button></form>')
        return f'<h1>Pengetahuan</h1><div class="card"><h2>Status bot</h2>{status}</div>{kol}{gcal}{cal}{rc}{faq}{rest}'

    @r.get("/pengetahuan/contoh.xlsx")
    def example(request: Request):
        who(request)
        return Response(example_roster_xlsx(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": 'attachment; filename="contoh-daftar-kol.xlsx"'})

    async def upload(request: Request) -> tuple[bytes, str]:
        f = await form(request)
        file = f.get("file")
        data = await file.read() if file else b""
        if not data or len(data) > MAX_UPLOAD:
            raise HTTPException(400, "File kosong atau terlalu besar (maks. 10 MB).")
        return data, file.filename or ""

    def back(msg: str, ok: bool = True) -> RedirectResponse:
        from urllib.parse import quote
        return RedirectResponse(f"/pengetahuan?msg={quote(msg)}&ok={int(ok)}", status_code=303)

    @r.post("/pengetahuan/kol")
    async def kol_upload(request: Request):
        user = who(request)
        manager(user)
        data, name = await upload(request)
        try:
            rows = tl.read_table(data, name)
        except Exception:
            return back("File tidak bisa dibaca. Pakai .xlsx atau .csv.", False)
        n, problems = tl.import_roster(ui.store, rows, user.key)
        return back(f"{n} KOL tersimpan." + (" Catatan: " + "; ".join(problems[:5]) if problems else ""), not problems)

    @r.post("/pengetahuan/jadwal")
    async def busy_upload(request: Request):
        user = who(request)
        manager(user)
        data, name = await upload(request)
        try:
            rows = tl.read_table(data, name)
        except Exception:
            return back("File tidak bisa dibaca. Pakai .xlsx atau .csv.", False)
        n, problems = tl.import_availability(ui.store, rows, user.key)
        return back(f"{n} periode jadwal tersimpan." + (" Catatan: " + "; ".join(problems[:5]) if problems else ""), not problems)

    @r.post("/pengetahuan/kalender")
    async def calendars(request: Request):
        user = who(request)
        manager(user)
        f = await form(request)
        urls = [u.strip() for u in str(f.get("urls", "")).split() if u.strip()]
        bad = [u for u in urls if not u.startswith("https://")]
        if bad:
            return back("Alamat kalender harus diawali https://", False)
        if f.get("clear"):
            kb.set("calendar_urls", [], user.key)
        elif urls:
            kb.set("calendar_urls", urls, user.key)
        from . import calendar_sync
        st = calendar_sync.sync(ui.store, ui.cfg().tz)
        ok = not st["errors"]
        return back(f"Sinkron: {st['events']} acara, {st['matched']} cocok dengan talent." +
                    (" " + "; ".join(st["errors"][:3]) if st["errors"] else ""), ok)

    @r.post("/pengetahuan/ratecard")
    async def ratecard_upload(request: Request):
        user = who(request)
        manager(user)
        data, _ = await upload(request)
        if not data.startswith(b"%PDF"):
            return back("File rate card harus PDF.", False)
        kb_dir.mkdir(parents=True, exist_ok=True)
        (kb_dir / "ratecard.pdf").write_bytes(data)
        kb.set("ratecard_media", {}, user.key)        # upload again to Meta on next use
        return back("Rate card tersimpan.")

    @r.post("/pengetahuan/faq")
    async def faq_save(request: Request):
        user = who(request)
        manager(user)
        f = await form(request)
        out, i = [], 0
        while f"faq_q_{i}" in f:
            qs = [q.strip() for q in str(f.get(f"faq_q_{i}", "")).splitlines() if q.strip()]
            ans = str(f.get(f"faq_a_{i}", "")).strip()
            if qs and ans and not f.get(f"faq_del_{i}"):
                out.append({"id": str(f.get(f"faq_id_{i}") or uuid.uuid4().hex[:8]), "questions": qs, "answer_id": ans,
                            "answer_en": str(f.get(f"faq_en_{i}", "")).strip(), "auto_send": bool(f.get(f"faq_auto_{i}")),
                            "updated_at": int(time.time())})
            i += 1
        kb.set("faq", out, user.key)
        return back(f"{len(out)} FAQ tersimpan.")

    @r.post("/pengetahuan/teks")
    async def texts_save(request: Request):
        user = who(request)
        manager(user)
        f = await form(request)
        kb.set("info", {k: str(f.get(f"info_{k}", "")).strip() for k in INFO_SECTIONS}, user.key)
        kb.set("texts", {k: str(f.get(f"text_{k}", "")).strip() or v for k, v in DEFAULT_TEXTS.items()}, user.key)
        return back("Tersimpan.")
