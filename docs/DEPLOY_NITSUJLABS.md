# Going live on nitsujlabs.app — exact steps

`docs/SETUP_SERVER.md` with every value filled in. Tick the boxes as you go.

| | Value |
|---|---|
| Team pages + Meta webhooks | **heroez.nitsujlabs.app** → `localhost:8787` |
| Owner settings page | **heroezadmin.nitsujlabs.app** → `localhost:8788` |
| Server | Ubuntu 24.04 · 2 vCPU · 2 GB · 50 GB · backups on |

---

## 1. Domain on Cloudflare (10 min)
- [ ] Log in at <https://dash.cloudflare.com>. If **nitsujlabs.app** is already listed, skip to step 2.
- [ ] Otherwise **Add a domain** → `nitsujlabs.app` → Free plan.
- [ ] On the DNS records page, check every existing record was imported (website, and **MX** records if the
      domain receives email). Add any that are missing **before** the next step.
- [ ] At the registrar where you bought nitsujlabs.app, replace the nameservers with the two Cloudflare shows.
      Wait until Cloudflare says the domain is **Active** (minutes to a few hours).

## 2. Rent the server (15 min)
- [ ] Biznet Gio / IDCloudHost (Jakarta) or DigitalOcean (Singapore): Ubuntu **24.04**, 2 vCPU, 2 GB RAM,
      40–50 GB SSD, **backups on**, add your SSH key if offered.
- [ ] Note the server IP: `____________`

## 3. Prepare the server (5 min)
From your laptop (PowerShell / Terminal), replace the IP:
```bash
ssh root@SERVER_IP
```
On the server:
```bash
apt update && apt -y upgrade && apt -y install unattended-upgrades ufw git docker.io docker-compose-v2
```
```bash
ufw allow OpenSSH && ufw --force enable && systemctl enable --now docker
```

## 4. Cloudflare Tunnel (5 min)
- [ ] Cloudflare → **Zero Trust** → **Networks** → **Tunnels** → **Create a tunnel** → *Cloudflared* → name `heroez-ops`.
- [ ] On the install screen copy only the **token** (the long text after `--token`). Don't run their command.
- [ ] **Public hostnames** → add two:

  | Subdomain | Domain | Type | URL |
  |---|---|---|---|
  | `heroez` | nitsujlabs.app | HTTP | `localhost:8787` |
  | `heroezadmin` | nitsujlabs.app | HTTP | `localhost:8788` |

## 5. Cloudflare Access — who can log in (10 min)
- [ ] Zero Trust → **Access** → **Applications** → **Add an application** → *Self-hosted*, name `Heroez Ops`,
      session duration `12 hours`.
- [ ] Destinations:
  - `heroezadmin.nitsujlabs.app` (whole site)
  - `heroez.nitsujlabs.app` paths: `antrian`, `deal`, `spk`, `kontrak`, `template`, `nomor`, `pengetahuan`
  - ❗ **Not** `/webhook` or `/health` (Meta, Telegram and the uptime monitor must reach those).
- [ ] Policy: **Allow** → **Emails** → your email + every admin's and supervisor's work email.
- [ ] Login method: **One-time PIN**.
- [ ] Save, open the app again and copy:
  - Application Audience (**AUD**) tag: `____________`
  - Team domain (Zero Trust → Settings): `____________.cloudflareaccess.com`

## 6. Install the app (15 min)
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
Answer the four questions:

| Question | Answer |
|---|---|
| Cloudflare Tunnel token | the token from step 4 |
| Public address | `heroez.nitsujlabs.app` |
| Settings page address | `heroezadmin.nitsujlabs.app` |
| Owner email(s) | your email (the one you added in step 5) |

- [ ] Write down the **First owner password** it prints: `____________`

```bash
docker compose -f deploy/docker-compose.yml up -d
```
After a minute this should say `healthy`, and Cloudflare → Tunnels should show the tunnel as **Healthy**:
```bash
docker compose -f deploy/docker-compose.yml ps
```

## 7. Finish in the browser (20 min)
- [ ] Open **https://heroezadmin.nitsujlabs.app** → email code → owner password from step 6.
- [ ] Bottom of the page: **change the password**.
- [ ] Fill in and **Simpan & mulai ulang**:
  - 2 · Telegram bot token (from @BotFather); everyone sends `/start` to the bot, their IDs appear on this page
  - 3–4 · supervisors and admins with Telegram ID and **work email** (same emails as step 5)
  - 5 · hours (defaults are Heroez's: 09:30–21:00 notifications, Mon–Fri work 09:30–18:30)
  - 6 · Claude API key
  - 7 · bot mode: keep **Cek dulu**
  - 8 · Access team domain + AUD tag from step 5, owner email
  - 9 · WhatsApp number (Meta keys come in step 8)
- [ ] **Kirim pesan tes** → everyone receives a Telegram message.

## 8. Connect WhatsApp (Meta) — `docs/SETUP_RUNBOOK.md`
- [ ] Callback URL: `https://heroez.nitsujlabs.app/webhook/meta`
- [ ] Verify token: shown at the top of the settings page
- [ ] Subscribe: `messages`, `smb_message_echoes`, `history`, `smb_app_state_sync`
- [ ] Coexistence onboarding with the QR code on the Heroez phone; paste App Secret, Phone number ID and
      system-user token into settings section 9. Start **business verification** early.

## 9. Before the team uses it
- [ ] Uptime monitor (UptimeRobot, free): `https://heroez.nitsujlabs.app/health`, every 5 min, alert after 10 min.
- [ ] **Template** page: upload the lawyer-approved SPK (type *SPK*) and contract (type *Kontrak*) → check → approve.
- [ ] **Nomor** page: enter the last SPK and contract numbers used before the app.
- [ ] **Pengetahuan** page: KOL Google Sheet link, Google Calendar iCal, FAQ, bot texts.
- [ ] Keep **Shadow** mode for a week, then **Review**, then **Live** (settings section 1).
- [ ] Send the team `https://heroez.nitsujlabs.app/antrian` and the deck `docs/Asisten_Heroez_Panduan_Tim.pdf`.
- [ ] Weekly: settings page → **Download cadangan**, keep it in Google Drive.

Updates later, on the server:
```bash
cd /opt/heroez-ops && git pull && docker compose -f deploy/docker-compose.yml up -d --build
```
