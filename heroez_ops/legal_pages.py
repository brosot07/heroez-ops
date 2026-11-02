"""Public privacy policy and data-deletion pages (Meta requires both before an app can go Live).

Served without login at /privasi and /hapus-data on the team address. Bilingual (Bahasa Indonesia first).
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from .ui_common import CSS

_PRIVACY = """
<h1>Kebijakan Privasi · Privacy Policy</h1>
<p class="muted">PT Heroez Sinar Sanjaya ("Heroez") · Heroez Ops · berlaku sejak November 2026</p>

<h2>Ringkasan</h2>
<p>Heroez Ops adalah sistem internal Heroez yang membantu tim kami membalas pesan WhatsApp yang masuk ke nomor
WhatsApp Business Heroez tepat waktu. Sistem ini terhubung ke WhatsApp melalui WhatsApp Business Platform resmi dari Meta.</p>

<h2>Data yang diproses</h2>
<ul>
<li>Pesan pribadi (1:1) yang dikirim ke dan dari nomor WhatsApp Business Heroez: teks, jenis lampiran, waktu. Grup tidak dibaca;
lampiran tidak diunduh.</li>
<li>Nama profil dan nomor WhatsApp pengirim, status percakapan (menunggu balasan / sudah dibalas), admin yang menangani.</li>
<li>Untuk klien: data kerja sama (brand, talent, periode, nilai) dan dokumen kontrak/SPK yang dibuat oleh tim Heroez.</li>
</ul>

<h2>Tujuan</h2>
<p>Mengingatkan tim Heroez tentang pesan yang belum dibalas, membantu menjawab pertanyaan tentang layanan Heroez (KOL, rate,
ketersediaan), dan menyiapkan dokumen kerja sama. Data tidak dijual, tidak dipakai untuk iklan, dan tidak dipakai untuk melatih
model AI.</p>

<h2>Pihak yang memproses</h2>
<ul>
<li><b>Meta</b> (WhatsApp Business Platform): pengiriman pesan.</li>
<li><b>Server Heroez</b> (DigitalOcean, Singapura): penyimpanan utama, terenkripsi.</li>
<li><b>Telegram</b>: notifikasi internal ke staf Heroez (potongan pesan dan ringkasan).</li>
<li><b>Anthropic</b> (Claude API): membaca beberapa pesan terakhir sebuah percakapan untuk menilai apakah perlu dibalas;
nomor panjang disamarkan. Anthropic tidak memakai data API untuk pelatihan.</li>
</ul>

<h2>Penyimpanan</h2>
<p>Isi pesan dihapus otomatis setelah 90 hari. Catatan kerja sama dan kontrak disimpan selama diwajibkan oleh hukum
(pembukuan dan perpajakan).</p>

<h2>Hak Anda</h2>
<p>Anda dapat meminta salinan, perbaikan, atau penghapusan data Anda kapan saja sesuai UU No. 27 Tahun 2022 tentang
Pelindungan Data Pribadi. Lihat <a href="/hapus-data">cara menghapus data</a>.</p>

<h2>Kontak</h2>
<p>PT Heroez Sinar Sanjaya, Treasury Tower, Jl. Jenderal Sudirman Kav 52–53, Jakarta Selatan 12190, Indonesia ·
<a href="mailto:business@beheroez.gg">business@beheroez.gg</a></p>

<hr>
<h2>English summary</h2>
<p>Heroez Ops is an internal tool of PT Heroez Sinar Sanjaya. It receives copies of one-to-one messages to and from the Heroez
WhatsApp Business number through Meta's official WhatsApp Business Platform, so our staff are reminded about unanswered
messages and can answer questions about our services. We process message text, sender name and number, and conversation
status. Processors: Meta, our server (DigitalOcean, Singapore), Telegram (internal staff notifications) and Anthropic (Claude API,
message classification; not used for training). Message text is deleted after 90 days. We never sell data or use it for
advertising. To access or delete your data see <a href="/hapus-data">data deletion</a> or email
<a href="mailto:business@beheroez.gg">business@beheroez.gg</a>.</p>
"""

_DELETION = """
<h1>Penghapusan Data · Data Deletion</h1>
<p class="muted">PT Heroez Sinar Sanjaya · Heroez Ops</p>
<h2>Cara meminta penghapusan</h2>
<ol>
<li>Kirim email ke <a href="mailto:business@beheroez.gg">business@beheroez.gg</a> dengan judul <b>"Hapus data"</b>, atau kirim pesan
WhatsApp ke nomor Heroez yang biasa Anda hubungi dengan tulisan <b>"Hapus data saya"</b>.</li>
<li>Sebutkan nomor WhatsApp yang datanya ingin dihapus.</li>
<li>Kami menghapus semua pesan, status percakapan, dan catatan terkait nomor tersebut dalam 7 hari kerja dan mengonfirmasi
kepada Anda. Catatan yang wajib disimpan secara hukum (kontrak, pembukuan) disimpan tanpa nomor dan nama Anda.</li>
</ol>
<hr>
<h2>English</h2>
<p>Email <a href="mailto:business@beheroez.gg">business@beheroez.gg</a> with the subject <b>"Delete data"</b> (or send "Delete my
data" to the Heroez WhatsApp number) and include the WhatsApp number concerned. We delete all messages, conversation status
and related records for that number within 7 business days and confirm to you. Records we must keep by law (contracts,
accounting) are kept without your number and name.</p>
"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f'<!doctype html><html lang="id"><head><meta charset="utf-8"><meta name="viewport" '
                        f'content="width=device-width,initial-scale=1"><title>{title}</title><style>{CSS}</style></head>'
                        f'<body><main style="max-width:760px">{body}</main></body></html>')


def router() -> APIRouter:
    r = APIRouter()

    @r.get("/privasi", response_class=HTMLResponse, include_in_schema=False)
    @r.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
    def privacy():
        return _page("Kebijakan Privasi · Heroez", _PRIVACY)

    @r.get("/hapus-data", response_class=HTMLResponse, include_in_schema=False)
    @r.get("/data-deletion", response_class=HTMLResponse, include_in_schema=False)
    def deletion():
        return _page("Penghapusan Data · Heroez", _DELETION)

    return r
