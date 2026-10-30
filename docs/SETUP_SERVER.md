# Heroez Ops as a web app on a cloud server

One small Linux server runs everything, 24/7. Nobody installs anything on their laptop: the team uses
Telegram and the web pages in a browser, the owner uses the settings page in a browser.

There are **no open ports and no `.env` file**. All traffic comes in through a Cloudflare Tunnel that the
app starts itself, and every key (Telegram, Claude, Meta…) is typed into the settings page, where it is
stored encrypted on the server.

Time needed: about 1 hour, once. You need: the Heroez domain, a Cloudflare account, and an SSH client
(Windows Terminal / PowerShell has `ssh` built in).

---

## 1. Rent the server

Any provider works. Pick the plan closest to:

| | |
|---|---|
| CPU / memory / disk | 2 vCPU · 2 GB · 40–50 GB SSD |
| System | **Ubuntu 24.04 LTS** |
| Location | Jakarta (data stays in Indonesia) or Singapore |
| Extras | turn on **automatic backups / snapshots** |
| Login | add your **SSH key** if the provider offers it (safer than a password) |

Indonesian providers with Jakarta data centres: Biznet Gio (NEO Lite), IDCloudHost. International:
DigitalOcean (Singapore). Note the server's **IP address** when it is created.

## 2. Prepare the server (copy-paste)

Log in (replace the IP):
```bash
ssh root@203.0.113.10
```

Updates, automatic security updates, firewall (only SSH open: the app needs no incoming ports), Docker:
```bash
apt update && apt -y upgrade && apt -y install unattended-upgrades ufw git docker.io docker-compose-v2
```
```bash
ufw allow OpenSSH && ufw --force enable
```
```bash
systemctl enable --now docker
```

## 3. Cloudflare: domain, tunnel, addresses

1. **Domain.** Add the Heroez domain to Cloudflare (free plan) and switch its nameservers at the registrar.
   ⚠️ If the domain also carries company email or a website, check in Cloudflare → DNS that all existing
   records were imported (especially the **MX** records for email) *before* switching nameservers.
2. **Tunnel.** Cloudflare dashboard → **Zero Trust** → **Networks** → **Tunnels** → **Create a tunnel** →
   type *Cloudflared* → name `heroez-ops`. On the install screen, copy only the **token** (the long text
   after `--token`). Do not run Cloudflare's install command: the app runs the tunnel itself.
3. **Public hostnames** of that tunnel (two entries):

   | Subdomain | Service |
   |---|---|
   | `ops` (e.g. ops.heroez.id) | `HTTP` · `localhost:8787` |
   | `opsadmin` (e.g. opsadmin.heroez.id) | `HTTP` · `localhost:8788` |

   Keep both names one level under the domain (`opsadmin.heroez.id`, not `admin.ops.heroez.id`): the free
   certificate covers only one level.

## 4. Cloudflare Access: who may log in

Zero Trust → **Access** → **Applications** → **Add an application** → *Self-hosted*:

- Name: `Heroez Ops`. Session duration: `12 hours`.
- **Destinations** (add each):
  - `opsadmin.heroez.id` (whole site)
  - `ops.heroez.id` with paths `antrian`, `deal`, `spk`, `kontrak`, `template`, `nomor`, `pengetahuan`
  - ❗ Do **not** add `ops.heroez.id/webhook` or `/health`: Meta, Telegram and the uptime monitor must reach those.
- **Policy**: *Allow* → *Emails* → the owner, supervisors' and admins' work emails.
- **Login method**: *One-time PIN* (a code by email; no passwords to manage).
- After saving, open the application → copy the **Application Audience (AUD) tag**. Your **team domain** is shown
  under Zero Trust → Settings (e.g. `heroez.cloudflareaccess.com`).

The app checks the Access login itself and maps each email to a person, so a missing policy never exposes
the pages. The settings page additionally allows only the **owner emails** and always asks the owner password.

## 5. Install and start the app

```bash
git clone https://github.com/brosot07/heroez-ops.git /opt/heroez-ops
```
```bash
cd /opt/heroez-ops && docker compose -f deploy/docker-compose.yml build
```
First start: it asks for the tunnel token, the two addresses and the owner email(s), and prints the
**first owner password** (used once):
```bash
docker compose -f deploy/docker-compose.yml run --rm app heroez-ops init
```
```bash
docker compose -f deploy/docker-compose.yml up -d
```
Check that it runs (wait a minute; `healthy` is good):
```bash
docker compose -f deploy/docker-compose.yml ps
```

## 6. Finish in the browser

1. Open `https://opsadmin.heroez.id` → Cloudflare asks your email → code → then the owner password from step 5.
2. **Change the password** (bottom of the page).
3. Fill the settings page, top to bottom (the same page as the Windows version, see `SETUP_WINDOWS.md` §2–3e):
   Telegram bot token and people (with their **work emails**), hours, Claude API key, bot mode, and in
   section 8 the **Access team domain** and **AUD tag** from step 4. Save.
4. Section 9 shows the Meta **Callback URL** and **Verify token** for `SETUP_RUNBOOK.md` (Meta onboarding).
5. Click **Kirim pesan tes** — everyone should get a Telegram message.
6. Team pages for everyone: `https://ops.heroez.id/antrian` (also `/spk`, `/kontrak`, `/deal`, `/nomor`).

Free uptime monitor (UptimeRobot or Healthchecks.io): check `https://ops.heroez.id/health` every 5 minutes,
alert management after 10 minutes down.

## 7. Updating

```bash
cd /opt/heroez-ops && git pull && docker compose -f deploy/docker-compose.yml up -d --build
```
Settings, keys and data are kept (they live in the `heroez_data` Docker volume).

## 8. Backups

- **Provider snapshots** (step 1) protect against a broken server.
- **Off the server, weekly:** settings page → **Download cadangan** (database + contracts, SPKs, templates,
  knowledge documents). Keep it in the office Google Drive. Keys are not in it on purpose.
- **Restore** onto a fresh install: stop the app, copy the files back into the volume, start it:
  ```bash
  docker compose -f deploy/docker-compose.yml stop app
  ```
  ```bash
  unzip heroez-cadangan-2026-11-30.zip -d /tmp/restore && docker run --rm -v heroez-ops_heroez_data:/data -v /tmp/restore:/r alpine sh -c "cp -r /r/* /data/ && chown -R 10001 /data"
  ```
  ```bash
  docker compose -f deploy/docker-compose.yml start app
  ```
  Then enter the keys again on the settings page (they are not in the backup).

## 9. Troubleshooting

| Problem | Do |
|---|---|
| Settings page unreachable | `docker compose -f deploy/docker-compose.yml logs --tail 50 app`; Cloudflare → Tunnels shows *Healthy*? |
| Forgot the owner password | `docker compose -f deploy/docker-compose.yml exec app heroez-ops reset-password` prints a one-time password |
| "belum terdaftar sebagai admin/supervisor" | add that person's email in settings sections 3–4 |
| Moving to another server | install on the new one, restore a backup (§8), re-enter the keys, point the tunnel token at it |

If the server is down, WhatsApp keeps working normally; only reminders, the bot and the web pages pause.
