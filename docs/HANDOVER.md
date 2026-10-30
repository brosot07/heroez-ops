# Heroez Ops — technical handover

For the person who deploys and runs Heroez Ops. Written 30 Oct 2026, version 0.9.x.

## TL;DR

The application is built and tested. **What is left is deployment and setup, not code.**
There is **no `.env` file** in the web app version: every key is typed into the settings page and
stored encrypted on the server. Follow `docs/SETUP_SERVER.md` from top to bottom.

## 1. What it is

A single-process Python service for Heroez (KOL agency, Jakarta) on the existing WhatsApp Business
number, using **Meta's official Cloud API in coexistence mode** (no unofficial WhatsApp libraries):

| Area | What it does |
|---|---|
| Reminders | Tracks every 1:1 chat (waiting on us / on them / closed), AI checks whether the last client message needs a reply, escalates on Telegram (30 business min → hourly → 2 h group/supervisor → 18 h urgent → 24 h), batching, morning list, daily digest |
| Brand bot | Greets new contacts only: spam filter, asks what they need, answers KOL rates + availability from the KOL sheet and Google Calendar, cross-sells, hands over to the KOL's admin. Mode **Mati / Cek dulu (supervisor approves each message on Telegram) / Otomatis**; default *Cek dulu* |
| Drafts & learning | AI reply suggestions in alerts; learns from supervisor-approved admin replies |
| Promises | "besok saya kirim" in an admin reply becomes a due reminder |
| Deals | Pipeline Lead → … → Lunas, AI-spotted agreements confirmed by one tap, mirrored to a Google Sheet via Apps Script |
| Documents | Contracts and **SPK** from lawyer-approved Word templates (AI marks the variable fields once, a person approves); SPK page reads dropped documents into a SOW table → one SPK per talent; per-type numbering with a register |
| Web pages | Antrian, Deal, SPK, Kontrak, Template, Nomor, Pengetahuan (team, via Cloudflare Access) + Pengaturan (owner) |

## 2. Architecture

```
Meta webhooks ─┐                         ┌─ Telegram (long polling): alerts, buttons, commands
               ├─► Cloudflare Tunnel ──► │  one container (Docker, non-root, read-only FS, no published ports)
Browsers ──────┘   (+ Cloudflare Access) │  ├─ :8787 webhooks + team pages   (ops.<domain>)
                                         │  ├─ :8788 settings page            (opsadmin.<domain>)
                                         │  ├─ background ticker (30 s) + Telegram poller
                                         │  └─ cloudflared child process (pinned, checksummed)
                                         └─ SQLite (WAL) + documents in the /data volume
Outbound: Anthropic API (Claude), Meta Graph API (bot sends), Google Sheet/Calendar (read), Apps Script (deals)
```

- Language/stack: Python 3.13, FastAPI/uvicorn, SQLite, httpx, python-docx, openpyxl, anthropic SDK.
- Entry point: `heroez-ops run` (`heroez_ops/winmain.py`) — same code for the Windows installer and the server.
- Data (`/data` volume): `config/config.yaml`, `config/owner.json` (PBKDF2 owner password),
  `secrets.bin` (AES-256-GCM) + `master.key` (0600), `data/heroez.db`, `data/{templates,contracts,spk,kb,backups}`, `logs/`.
- AI models: Haiku for message checks and bot understanding, Sonnet for drafts and document reading,
  Opus for one-time template analysis. Daily call caps; cost shown in the digest and settings page.

## 3. Repository map

| Path | |
|---|---|
| `heroez_ops/` | application (`states.py` engine, `escalation.py`, `notifier.py`, `bot.py` Telegram, `intake.py` brand bot, `approvals.py`, `docs_ui.py` contracts/templates, `spk*.py`, `numbering*.py`, `deals*.py`, `setup_ui.py` settings, `secrets.py`, `winmain.py`) |
| `heroez_ops/prompts/` | versioned AI prompts |
| `heroez_ops/defaults/` | config shipped to new installs (a test keeps it equal to `config/config.yaml`) |
| `deploy/` | Dockerfile, docker-compose.yml, pinned requirements |
| `packaging/windows/`, `.github/workflows/` | Windows installer (PyInstaller + WinSW + Inno Setup), CI builds it after the test suite |
| `tests/` | 239 tests (pytest), all simulated: fake Meta payloads, fake AI, fake HTTP |
| `docs/` | setup guides, operations, admin guide (ID), data-flow note (ID), team deck |

Run the tests: `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' && .venv/bin/python -m pytest -q`

## 4. Verified vs not yet verified

**Verified**
- Full test suite on Linux and on GitHub's Windows runner; Windows installer builds and releases (v0.9.0).
- Docker image builds; container starts as non-root on a read-only FS; `heroez-ops init`, encrypted secrets,
  `/health` healthy; settings page answers on its own hostname.

**Not verified yet (needs the real accounts)**
- Meta coexistence onboarding and live webhooks (`messages`, `smb_message_echoes`, `history`,
  `smb_app_state_sync`) — the parser is built from Meta's documentation and simulated payloads.
- Cloudflare Tunnel and Access with the real domain (JWT verification is tested with fakes).
- Real Claude calls on Heroez's own documents: SPK extraction, template field detection on **Heroez's real
  SPK and contract templates** (please check the field mapping on the SPK page after approving them).
- Telegram with real users, Google Sheet/Calendar sync, the Apps Script deals sheet.

## 5. What is left — checklist

| # | Task | Who | Where |
|---|---|---|---|
| 1 | Rent the server (2 vCPU / 2 GB / 50 GB, Ubuntu 24.04, backups on) | owner pays, tech lead sets up | SETUP_SERVER §1–2 |
| 2 | Domain on Cloudflare (watch the MX records), tunnel + 2 hostnames | tech lead | §3 |
| 3 | Cloudflare Access app (One-time PIN), team + owner emails | tech lead | §4 |
| 4 | Deploy key (if the repo is private), clone, `init`, `up -d` | tech lead | §5 |
| 5 | Settings page: owner password, Telegram bot + people with emails, Access team domain + AUD, Claude key | owner + tech lead | §6 |
| 6 | Meta app, webhook, coexistence onboarding (QR scan on the Heroez phone), phone number ID, system-user token; start **business verification** early | owner (Meta login, phone) + tech lead | SETUP_RUNBOOK |
| 7 | Upload lawyer-approved SPK + contract templates, approve; set "last number used" per type on **Nomor** | owner / supervisor | web pages |
| 8 | KOL sheet link, Google Calendar iCal, FAQ, bot texts on **Pengetahuan** | ops | web pages |
| 9 | Uptime monitor on `/health`; weekly **Download cadangan** | tech lead | §6, §8 |
| 10 | Rollout: Shadow week → Review → Live; bot stays *Cek dulu* until the replies look right | owner | OPERATIONS §2 |

## 6. Things to know

- **Admins must reply from the phone or WhatsApp Web**, never the WhatsApp for Windows app: its replies produce
  no echo webhook, so the chat looks unanswered.
- Coexistence turns off broadcast lists and disappearing messages on the number; groups are not seen.
- One instance only (SQLite, in-process ticker). Don't run two containers on the same data.
- Until Cloudflare Access is configured, the settings page is protected by the owner password alone
  (it shows a warning). Do step 3 before step 5 if possible.
- `master.key` is not in the backup ZIP on purpose; after a restore, re-enter the keys on the settings page.
- Repository visibility: check it is **private** (GitHub → Settings → Danger Zone) before adding real data
  anywhere; the code contains no secrets.
- Costs: server ≈ US$5–10/month, Claude ≈ US$10–30/month at current volumes, Meta free up to 1,000 bot
  conversations/month, Cloudflare/Telegram/Google free.

## 7. Where to look

`docs/HANDOVER.md` (this file) · `docs/SETUP_SERVER.md` (deploy) · `docs/SETUP_RUNBOOK.md` (Meta) · `docs/OPERATIONS.md` (running it) ·
`docs/ADMIN_GUIDE_ID.md` + `docs/Asisten_Heroez_Panduan_Tim.pdf` (team) · `docs/DATA_FLOW_ID.md` (UU PDP note).
