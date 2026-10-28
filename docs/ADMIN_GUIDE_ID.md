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
| 🚫 **Bukan urusan kita** | Chat ini tidak perlu dibalas (spam, salah kirim, dll). |

Tombol ✅ dan 🚫 juga membantu sistem belajar. Pakai dengan jujur. 🙏

## 4. Perintah di bot

- `/antrian`: chat yang menunggu balasan **kamu**
- `/semua`: semua chat yang menunggu
- `/cari Rina` atau `/cari 0812…`: cari kontak
- `/bantuan`: bantuan singkat

## 5. Yang berubah di WhatsApp setelah tersambung

- **Broadcast list** tidak bisa dibuat lagi (yang lama hanya bisa dibaca).
- Pesan sementara (disappearing), *view once*, dan *live location* nonaktif di chat pribadi.
- **Grup tetap jalan**, tapi sistem tidak membaca grup. Notifikasi hanya untuk chat pribadi.
- Buka aplikasi WhatsApp Business di HP minimal sekali tiap 2 minggu (biasanya otomatis karena dipakai tiap hari).

## 6. Kalau bot diam / error

Tetap kerja seperti biasa di WhatsApp. Tidak ada yang rusak di WhatsApp kalau sistem mati.
Manajemen otomatis dapat peringatan kalau sistem mati lebih dari 10 menit.
