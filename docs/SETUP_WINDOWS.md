# Installing Heroez Ops on a Windows PC

The program runs on **one** computer that stays on. Admins and supervisors install nothing:
they only use Telegram (and WhatsApp Web / the phone app to reply).

## 0. Pick the host computer
- Windows 10/11 64-bit, plugged in, connected to the internet, **left on**.
- Best: a desktop or spare laptop that stays in one place. Not a laptop that gets carried home or closed.
- If it is a laptop: in Windows *Power & battery* settings, set "When I close the lid (plugged in)" to **Do nothing**.
- Work laptops managed by IT may block installing services. Ask IT first if unsure.

## 1. Install
1. Run `HeroezOps-Setup-<version>.exe` (needs administrator rights).
2. Leave **"Jangan sleep / hibernate saat dicolok"** ticked.
3. At the end, the settings page opens: <http://127.0.0.1:8788> (also in the Start menu: *Heroez Ops – Pengaturan*).

The program starts by itself every time Windows starts, even before anyone logs in.

## 2. Telegram bot (free, 5 minutes)
1. In Telegram, open **@BotFather** → `/newbot` → name it e.g. *Heroez Asisten* → copy the token.
2. Paste the token in the settings page (section 2) → **Simpan & mulai ulang**.
3. Each admin and supervisor opens the bot and sends `/start`. Their name and ID appear in the settings page under section 2.
4. Fill sections 3 (supervisors) and 4 (admins) with those IDs → Simpan. Then tick which supervisor(s) belong to each admin → Simpan.
5. Click **Kirim pesan tes**. Everyone should receive a test message.

## 3. Public address (Cloudflare Tunnel, free)
Meta must be able to reach this computer. The tunnel does that without opening ports on your router.
1. Create a free Cloudflare account and add a domain you control (e.g. heroez.id) to it.
2. Cloudflare dashboard → *Zero Trust* → *Networks* → *Tunnels* → **Create a tunnel** (type *Cloudflared*) → name it `heroez-ops`.
3. Copy the **token** shown in the install command (the long string after `--token`). Do **not** run their installer; ours already includes cloudflared.
4. Add a *Public hostname*: e.g. `ops.heroez.id` → Service: `HTTP` → URL: `localhost:8787`.
5. In our settings page, section 6: paste the token, type the hostname → Simpan. Status should show *Tunnel: berjalan*.

## 3b. AI check (Claude, optional but recommended)
1. Create an account at <https://console.anthropic.com>, add a payment method or prepaid credit, and create an **API key**.
2. Paste it in the settings page, section 6 (AI), and keep "Pakai AI" ticked → Simpan.
3. Status then shows *AI: aktif* with the number of messages checked this month and the estimated cost.
Expected cost: about US$1–5 per month. Without a key, the word rules alone decide.
Privacy: the last ~8 messages of a chat (long numbers masked) are sent to Anthropic's API to be classified.
Mention this in the data-flow note for clients and talents.

## 3c. Contracts (approved templates only)
1. On the host PC open *Heroez Ops – Pengaturan* → **Template** → upload the lawyer-approved `.docx` and give it a name.
2. The AI marks the parts that change per client (about 1–2 minutes). Check the yellow parts in **Cek dokumen**:
   each must cover the whole value, and no old client data may remain outside them. Fix with the field list or
   *Tambah field manual*. The page only lets you approve once the self-check is green.
3. Click **Setujui & pakai template ini**. Only approved templates can be used; editing one sends it back for approval.
4. **Kontrak** → choose the template → fill in client details, SOW rows and fees → **Simpan & buat dokumen** → download Word.
   Totals left blank are added up from the SOW rows; PPN follows the PKP setting (section 7).
5. Contracts from a client's own paper stay outside the app.

**From other laptops (optional):** in Cloudflare Zero Trust → *Access* → *Applications* → add a *Self-hosted* app for
`ops.heroez.id` with paths `/kontrak` and `/template`, policy *Allow* → *Emails* → the admins' and supervisors' work emails
(login by one-time email code). Copy the team domain and the **Application Audience (AUD) tag** into section 7 of the
settings page, and fill in everyone's email in sections 3 and 4. Admins can make contracts; only supervisors (and this PC)
can upload or approve templates. Never add `/webhook/*` to the Access app (Meta and Telegram must reach it).

## 3d. WhatsApp bot for new brands (Phase 2)
1. **Pengetahuan** page: upload the KOL list (Excel/CSV, one row per KOL; columns starting with "Rate" are prices;
   phone/handler/internal-note columns are never shown), the availability sheet (Nama, Mulai, Selesai: booked dates only),
   the rate card PDF, FAQ answers and the bot's texts. *Download contoh* gives an example sheet.
2. Settings, section 7: tick the bot options, set the reply-time promise and the monthly message limit.
   Section 9: the Meta **Phone number ID** and a permanent **System user access token** (from the Meta app).
3. Keep **Shadow** mode for the first week: the bot's would-be messages are written to
   `data\shadow_alerts.jsonl` (type `whatsapp_send`) so you can read exactly what it would have said.
4. What the bot does: ignores spam (listed daily, `/spam` in Telegram to rescue), asks brands what they need and which
   KOLs, replies with each KOL's rate and ✅/❌ availability straight from your sheets, then hands over to an admin.
   Talents and existing clients never get the bot; it stops as soon as an admin replies.

## 4. Meta (WhatsApp)
Follow `SETUP_RUNBOOK.md` for the Meta app and coexistence onboarding. In the Meta app's WhatsApp → Configuration:
- **Callback URL** and **Verify token**: copy them from the top of our settings page.
- Subscribe to: `messages`, `smb_message_echoes`, `history`, `smb_app_state_sync`.
- Paste the Meta **App Secret** into section 6 of our settings page.

## 5. Health alert if the computer goes off (free)
Create a free monitor at UptimeRobot (or Healthchecks.io): HTTP check of `https://ops.heroez.id/health` every 5 minutes,
alert after 10 minutes down, notify management by Telegram or email.

## 6. Shadow week, then live
The program starts in **Shadow** mode: it works out reminders and logs them to
`C:\ProgramData\HeroezOps\data\shadow_alerts.jsonl` but sends nothing. After a week of checking, switch section 1 to **Live**.

## Where things are kept
`C:\ProgramData\HeroezOps` (readable only by Administrators):
`config\` settings · `data\heroez.db` chat states and history · `data\backups\` daily copies (14 days) ·
`logs\` program logs · `secrets.bin` tokens (encrypted by Windows).
Message texts are erased after 90 days; states and timings stay. Uninstalling keeps this folder.
Backups sit on the same disk: copy `data\backups` elsewhere now and then if the history matters to you.

## Updating
Run the newer `HeroezOps-Setup-<version>.exe`. It stops the service, replaces the program and starts it again.
Settings and history are kept.

## Troubleshooting
- Settings page doesn't open → Windows *Services* → *Heroez Ops* → Start. Logs: `C:\ProgramData\HeroezOps\logs`.
- No Telegram messages → are we still in Shadow mode? Run the test button. Has the person sent `/start` to the bot?
- If the program stops, the admins keep working in WhatsApp as usual. Nothing in WhatsApp depends on it.
