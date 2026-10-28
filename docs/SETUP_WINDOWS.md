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
