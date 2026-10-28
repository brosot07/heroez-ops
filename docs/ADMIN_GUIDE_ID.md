# Panduan Admin: Asisten Heroez di Telegram

Cara kerja kamu **tidak berubah**: tetap balas klien dan talent di WhatsApp seperti biasa.
Yang baru hanya satu: bot Telegram yang mengingatkan kalau ada chat yang belum dibalas.

## 1. Balas dari mana? (PENTING)

| Boleh ✅ | Jangan ❌ |
|---|---|
| WhatsApp Business di HP | **Aplikasi WhatsApp untuk Windows** (yang di-install di PC) |
| **WhatsApp Web** di Chrome/Edge (web.whatsapp.com) | |

**Kenapa?** Balasan dari aplikasi WhatsApp Windows **tidak terlihat** oleh sistem. Akibatnya kamu
akan terus dapat pengingat untuk chat yang sebenarnya sudah kamu balas.
Di PC, buka WhatsApp lewat **web.whatsapp.com**, bukan aplikasi Windows.

## 2. Notifikasi yang akan kamu terima

| Tanda | Artinya |
|---|---|
| 🟠 **Belum dibalas** | Chat yang dipegang kamu belum dibalas selama 30 menit jam kerja |
| 🟠 **Masih belum dibalas** | Pengingat ulang tiap jam untuk chat yang sama |
| 🟠 **Belum dibalas >2 jam kerja** | Dikirim juga ke grup admin dan supervisor |
| 🔴 **URGENT: hampir 24 jam** | Klien sudah menunggu 18 jam. Segera balas. |
| 🔴 **Lewat 24 jam** | Sudah lebih dari sehari. Balas secepatnya. |
| ☀️ **Selamat pagi** (09:30) | Daftar chat yang masih menunggu, dari yang paling lama |
| 🆕 **Lead brand baru** | Bot sudah menjawab brand baru (rate & ketersediaan KOL) dan menyerahkannya ke kamu |
| ⏰ **Janji jatuh tempo** | Kamu menulis "besok saya kirim…" ke klien; ini pengingatnya |
| 🤝 **Catat sebagai Deal?** | AI melihat klien setuju; tekan ✅ kalau benar deal, "Belum" kalau belum |

Jam kerja: Senin–Jumat 09:30–18:30 WIB. Notifikasi dikirim setiap hari **09:30–21:00 WIB**;
pesan yang masuk setelah 21:00 baru diingatkan besok pagi (kecuali yang urgent).
Paling banyak **1 notifikasi per jam**. Sabtu, Minggu dan tanggal merah: **1 per 6 jam**.
Semua chat yang menunggu dikumpulkan jadi satu daftar.
Chat yang belum dibalas akan muncul lagi tiap jam sampai dibalas.

**AI:** sebelum notifikasi dikirim, AI membaca pesan terakhir klien dan menentukan apakah itu pertanyaan,
permintaan, atau penutup ("baik kak, kami review dulu"). Kalau penutup, tidak ada notifikasi.
Baris 🤖 di notifikasi adalah ringkasan dari AI. AI tidak pernah membalas klien.

Setiap notifikasi berisi nama, nomor, sudah berapa lama menunggu, potongan pesan terakhir, dan
link **Buka chat** (langsung membuka chat di WhatsApp).

## 3. Tombol

| Tombol | Kapan dipakai |
|---|---|
| ✋ **Saya ambil** | Chat ini kamu yang pegang. Chat berikutnya dari kontak ini juga akan ke kamu. |
| ✅ **Sudah dibalas** | Kamu sudah balas tapi sistem masih mengingatkan (misalnya balas lewat telepon). |
| ⏰ **Tunda 1 jam** | Sedang menunggu info internal; ingatkan lagi 1 jam lagi. |
| → **Nama admin** | (supervisor) tugaskan lead brand baru ke admin itu; **✋ Saya tangani sendiri** kalau supervisor yang ambil |
| ✅ **Sudah** / ⏰ **2 jam lagi** | Pada pengingat janji: sudah dikirim, atau ingatkan lagi. Balasan kamu ke klien juga otomatis menutup janji. |
| ✅ **Ya, deal** / **Belum** | Pada pertanyaan deal: mencatat deal di pipeline (halaman Deal & Google Sheet ops). |

Tombol ✅ juga membantu sistem belajar. Pakai dengan jujur. 🙏

💡 **Saran balasan**: usulan balasan dari AI. Kalau cocok, salin ke WhatsApp dan kirim sendiri; boleh diubah dulu.

## 4. Perintah di bot

- `/antrian`: chat yang menunggu balasan **kamu**
- `/semua`: semua chat yang menunggu
- `/cari Rina` atau `/cari 0812…`: cari kontak
- `/janji`: janji ke klien yang belum ditandai selesai
- `/spam`: pesan yang diduga spam (tombol *Bukan spam* meneruskannya ke admin)
- `/bantuan`: bantuan singkat

## 5. Yang berubah di WhatsApp setelah tersambung

- **Broadcast list** tidak bisa dibuat lagi (yang lama hanya bisa dibaca).
- Pesan sementara (disappearing), *view once*, dan *live location* nonaktif di chat pribadi.
- **Grup tetap jalan**, tapi sistem tidak membaca grup. Notifikasi hanya untuk chat pribadi.
- Buka aplikasi WhatsApp Business di HP minimal sekali tiap 2 minggu (biasanya otomatis karena dipakai tiap hari).

## 6. Link di pesan klien

Sistem **tidak pernah membuka** link dari pesan klien. Di notifikasi, link ditulis seperti `hxxps://bit[.]ly/abc`
supaya tidak terbuka tanpa sengaja. Kalau ada tanda **⚠️ Link mencurigakan**: jangan dibuka, jangan isi data apa pun,
dan laporkan ke supervisor. Link yang tidak dikenal (🔗) hanya dibuka kalau kamu yakin siapa pengirimnya.
Ingat: Meta/WhatsApp tidak pernah minta verifikasi akun lewat link di chat.

## 7. Bot WhatsApp untuk brand baru

Nomor baru yang menghubungi Heroez dijawab dulu oleh bot: menyaring spam, menanyakan kebutuhan dan KOL yang
diminati, lalu memberi rate dan ketersediaan dari daftar KOL & kalender. Setelah itu chat diserahkan ke kamu
(🆕 di Telegram, lengkap dengan ringkasan). Bot berhenti begitu kamu membalas. Bot tidak menjawab hal di luar
layanan Heroez, tidak pernah menyebut alasan talent sibuk, dan tidak pernah membuka link.
Kalau bot salah jawab, laporkan ke supervisor: teks dan data bot diubah di halaman **Pengetahuan**.

## 8. Halaman web (Antrian, Deal, Kontrak)

Buka `https://ops.heroez.id/antrian` dari laptop: masuk dengan kode sekali pakai yang dikirim ke email kantor kamu
(login diminta lagi setiap hari). Halaman **Antrian** menampilkan chat yang menunggu balasan kamu, dengan tombol
yang sama seperti di Telegram. Balasnya tetap lewat WhatsApp Web / HP.
**Deal**: pipeline brand (Lead → Proposal → Deal → Kontrak → Ditandatangani → Lunas). Ubah tahap, nilai, catatan;
Lunas/Batal hanya supervisor. Isinya juga ada di Google Sheet ops tiap pagi.
**Kontrak**: pilih template yang sudah disetujui, isi data klien, SOW dan fee → dokumen Word jadi. Pilih deal
terkait supaya pipeline ikut terisi nilainya. Kontrak dari kertas klien tetap dibuat di luar aplikasi.

## 9. Kalau bot diam / error

Tetap kerja seperti biasa di WhatsApp. Tidak ada yang rusak di WhatsApp kalau sistem mati.
Manajemen otomatis dapat peringatan kalau sistem mati lebih dari 10 menit.
