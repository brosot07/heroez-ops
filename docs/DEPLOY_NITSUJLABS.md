# Going live on heroez.nitsujlabs.app — exact steps

nitsujlabs.app stays on **Vercel** (Onic Studio is not touched). The app runs on a small server in
**direct mode**: Caddy gets free HTTPS certificates, and people log in with a code from the Heroez
Telegram bot. No Cloudflare account is needed.

| | Value |
|---|---|
| Team pages + Meta webhooks | **heroez.nitsujlabs.app** |
| Owner settings page | **heroezadmin.nitsujlabs.app** |
| Server | Ubuntu 24.04 · 2 vCPU · 2 GB · 50 GB · backups on |

Time: about 1 hour, plus Meta (step 7).

---

## 1. Rent the server (15 min)
- [ ] Biznet Gio / IDCloudHost (Jakarta) or DigitalOcean (Singapore): Ubuntu **24.04**, 2 vCPU, 2 GB RAM,
      40–50 GB SSD, **backups on**, add your SSH key if offered.
- [ ] Note the server IP: `____________`

## 2. Two DNS records in Vercel (2 min) — adding records does not touch Onic Studio
Vercel → **Domains** → `nitsujlabs.app` → **DNS Records** → add:

| Name | Type | Value | TTL |
|---|---|---|---|
| `heroez` | A | server IP | 60 |
| `heroezadmin` | A | server IP | 60 |

Leave every existing record as it is.

## Shortcut: steps 3 and 4 in one paste
DigitalOcean → your Droplet → **Access** → **Launch Droplet Console**, then paste and press Enter:
```bash
curl -fsSL https://raw.githubusercontent.com/brosot07/heroez-ops/main/deploy/install.sh -o install.sh && bash install.sh
```
Wait for **DONE** (about 10 minutes), copy the first password it shows, continue with step 5.
(If the repository is private, use the manual steps below.)

## 3. Prepare the server (5 min)
From your laptop (PowerShell / Terminal):
```bash
ssh root@SERVER_IP
```
On the server — updates, Docker, and a firewall that only opens SSH and web (80 for the certificate check, 443 for HTTPS):
```bash
apt update && apt -y upgrade && apt -y install unattended-upgrades ufw git docker.io docker-compose-v2
```
```bash
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable && systemctl enable --now docker
```
If the provider has its own firewall in its dashboard, allow ports 22, 80 and 443 there too.

## 4. Install the app (15 min)
If the GitHub repository is **private**, give the server a read-only deploy key first:
```bash
ssh-keygen -t ed25519 -N "" -C heroez-server -f /root/.ssh/heroez_deploy && cat /root/.ssh/heroez_deploy.pub
```
GitHub → repository → **Settings** → **Deploy keys** → **Add deploy key** → paste → leave *write access* unticked. Then:
```bash
printf 'Host github.com\n  IdentityFile /root/.ssh/heroez_deploy\n  IdentitiesOnly yes\n' >> /root/.ssh/config && ssh-keyscan github.com >> /root/.ssh/known_hosts
```
```bash
git clone git@github.com:brosot07/heroez-ops.git /opt/heroez-ops
```
(If the repository is **public**: `git clone https://github.com/brosot07/heroez-ops.git /opt/heroez-ops`.)

Build and first start:
```bash
cd /opt/heroez-ops && docker compose -f deploy/docker-compose.yml build
```
```bash
docker compose -f deploy/docker-compose.yml run --rm app heroez-ops init
```

| Question | Answer |
|---|---|
| Cloudflare Tunnel token | **press Enter (leave empty)** → direct mode |
| Public address | `heroez.nitsujlabs.app` |
| Settings page address | `heroezadmin.nitsujlabs.app` |
| Owner email(s) | your email |

- [ ] Write down the **First owner password** it prints: `____________`

Start the app **and** Caddy (`--profile direct`):
```bash
docker compose -f deploy/docker-compose.yml --profile direct up -d
```
After a minute both should be running and the app `healthy`:
```bash
docker compose -f deploy/docker-compose.yml --profile direct ps
```
Check from your laptop: <https://heroez.nitsujlabs.app/health> shows `{"ok":true}` with a valid padlock.
(The first visit to each address takes a few seconds while Caddy gets the certificate.)

## 5. Finish in the browser (20 min)
- [ ] Open **https://heroezadmin.nitsujlabs.app** → owner password from step 4.
- [ ] Bottom of the page: **change the password**.
- [ ] Section 2: Telegram bot token (from @BotFather). Everyone, including you, sends `/start` to the bot;
      their IDs appear on this page.
- [ ] Section 8: your own Telegram ID in **Telegram ID pemilik**. From now on the settings page asks for the
      password **and** a code from the bot.
- [ ] Sections 3–4: supervisors and admins with Telegram ID and **work email** (that email is how they log in).
- [ ] Section 5 hours (defaults are Heroez's), 6 Claude API key, 7 bot mode **Cek dulu**. Leave the Cloudflare
      Access fields in section 8 empty.
- [ ] **Simpan & mulai ulang**, then **Kirim pesan tes** → everyone receives a Telegram message.
- [ ] Try the team login yourself: <https://heroez.nitsujlabs.app/antrian> → email → code in Telegram.

## 6. Before the team uses it
- [ ] Uptime monitor (UptimeRobot, free): `https://heroez.nitsujlabs.app/health`, every 5 min, alert after 10 min.
- [ ] **Template**: upload the lawyer-approved SPK (type *SPK*) and contract (type *Kontrak*) → check → approve.
- [ ] **Nomor**: enter the last SPK and contract numbers used before the app.
- [ ] **Pengetahuan**: KOL Google Sheet link, Google Calendar iCal, FAQ, bot texts.
- [ ] Send the team `https://heroez.nitsujlabs.app/antrian` and the deck `docs/Asisten_Heroez_Panduan_Tim.pdf`.
      They log in with their work email + the code the bot sends them.
- [ ] Weekly: settings page → **Download cadangan**, keep it in Google Drive.

## 7. Connect WhatsApp (Meta) — `docs/SETUP_RUNBOOK.md`
- [ ] Callback URL: `https://heroez.nitsujlabs.app/webhook/meta`
- [ ] Verify token: shown at the top of the settings page
- [ ] Subscribe: `messages`, `smb_message_echoes`, `history`, `smb_app_state_sync`
- [ ] Coexistence onboarding with the QR code on the Heroez phone; paste App Secret, Phone number ID and
      system-user token into settings section 9. Start **business verification** early.
- [ ] Keep **Shadow** mode for a week, then **Review**, then **Live** (settings section 1).

---

**Updates later**, on the server:
```bash
cd /opt/heroez-ops && git pull && docker compose -f deploy/docker-compose.yml --profile direct up -d --build
```

**Logs** if something is wrong:
```bash
docker compose -f deploy/docker-compose.yml --profile direct logs --tail 50
```

**Forgot the owner password:**
```bash
docker compose -f deploy/docker-compose.yml exec app heroez-ops reset-password
```

**Moving to a Heroez domain later:** add the two A records on the new domain, change both addresses in
settings section 9, update the Meta callback URL. Caddy gets new certificates
by itself.
