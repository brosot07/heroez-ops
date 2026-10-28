# Ringkasan alur data (untuk klien, talent, dan tim) — UU PDP

Dokumen ini menjelaskan data apa yang disimpan sistem Heroez Ops, di mana, berapa lama, dan siapa yang bisa melihat.
Gunakan sebagai lampiran pemberitahuan ke klien dan talent.

## Apa yang diproses
- **Chat WhatsApp pribadi** ke nomor WhatsApp Business Heroez: salinan pesan masuk dan balasan admin (teks, jenis
  lampiran, waktu). Grup WhatsApp **tidak** dibaca. Lampiran (foto/dokumen) tidak diunduh.
- **Nama dan nomor** kontak, status percakapan (menunggu balasan / sudah dibalas), siapa admin yang menangani.
- **Pipeline deal** (brand, talent, periode, nilai, tahap) dan **kontrak** yang dibuat di aplikasi.
- **Jadwal talent**: hanya sibuk/kosong per tanggal dari Google Calendar; alasan sibuk tidak pernah disimpan atau disebut.

## Ke mana data mengalir
| Tujuan | Data | Alasan |
|---|---|---|
| Meta (WhatsApp Cloud API) | pesan chat (sudah ada di WhatsApp) | sambungan resmi "coexistence" |
| Komputer host Heroez (Windows, kantor) | semua data di atas, terenkripsi akses hanya Administrator | penyimpanan utama |
| Telegram (akun admin/supervisor) | nama, nomor, potongan pesan terakhir, ringkasan AI | pengingat chat belum dibalas |
| Anthropic (Claude API) | ±8 pesan terakhir sebuah chat, nomor panjang disamarkan; dokumen template/daftar KOL yang diunggah | memeriksa apakah pesan perlu dibalas; bot menjawab brand; saran balasan; membaca template |
| Cloudflare | lalu lintas terenkripsi ke komputer host; email kantor untuk login | alamat publik dan login halaman web |
| Google (Sheet/Calendar milik Heroez) | daftar KOL, jadwal, FAQ masuk; pipeline deal keluar | data operasional tim |

Tidak ada data yang dijual atau dipakai untuk melatih model. Claude API tidak menyimpan data untuk pelatihan.

## Berapa lama
- Isi pesan: **90 hari**, lalu dihapus otomatis. Status/waktu balas (tanpa isi) disimpan untuk statistik.
- Payload mentah dari Meta: 7 hari. Cadangan database: 14 hari di komputer host.
- Kontrak dan deal: selama hubungan bisnis berlangsung (kewajiban pembukuan).
- Permintaan hapus: perintah `/hapus <nomor>` oleh supervisor menghapus semua chat, status, dan janji untuk nomor itu dan mencatat
  permintaannya; catatan deal/kontrak disimpan tanpa nomor dan nama kontak (kewajiban pembukuan).

## Siapa yang bisa melihat
- Admin: chat yang ia tangani (halaman Antrian/Telegram), deal, kontrak.
- Supervisor: semua chat yang menunggu, pipeline, template, pengetahuan, catatan audit.
- Pemilik sistem: pengaturan dan rahasia (token) — hanya di komputer host dengan password.
- Bot WhatsApp hanya menjawab hal seputar layanan Heroez (KOL, rate, ketersediaan); pertanyaan lain dialihkan ke admin.

## Keamanan singkat
Sambungan resmi Meta (tanpa aplikasi tidak resmi), tanda tangan webhook diverifikasi, rahasia dienkripsi Windows (DPAPI),
link di pesan **tidak pernah dibuka** oleh sistem maupun AI, login halaman web lewat email kantor (Cloudflare Access),
log audit untuk setiap tindakan admin/supervisor.

Kontak untuk pertanyaan atau permintaan data: supervisor Heroez (isi nama/email di sini saat dibagikan).
