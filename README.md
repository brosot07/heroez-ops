# Heroez Ops: WhatsApp reply reminders, brand bot, contracts and deals

Reads the Heroez WhatsApp Business number (Meta Cloud API, coexistence), works out which chats are
waiting on us, and reminds admins on Telegram. Admins keep replying from the WhatsApp Business app or
WhatsApp Web. Phase 2 adds a bot that answers new brands (KOL rates and availability from the ops
sheets) before handing over to a person; Phase 3 adds contracts from approved templates and a deal
pipeline mirrored to the ops Google Sheet.

**Status:** built and tested against simulated Meta payloads (214 tests). Not yet connected to a real
number. See `docs/PHASE0_REPORT.md` for open items.

## Documents
- `docs/SETUP_SERVER.md` — **web app on a cloud server** (recommended): server, Cloudflare, Access, install, backups
- `docs/SETUP_WINDOWS.md` — install on a Windows host PC, Telegram, tunnel, AI, contracts, bot, deals
- `docs/SETUP_RUNBOOK.md` — Meta app, webhook, coexistence onboarding, tokens, go-live checklist
- `docs/OPERATIONS.md` — what runs where, modes, automatic behaviour, weekly checks, incidents, costs
- `docs/ADMIN_GUIDE_ID.md` — for admins (Bahasa Indonesia)
- `docs/DATA_FLOW_ID.md` — data-flow note for clients and talents (UU PDP)
- `docs/deals_sheet.gs` — Apps Script for the deals tab of the ops Google Sheet

## What it does
- Stores inbound messages, admin replies (`smb_message_echoes`) and history backfill; dedupes by message id.
- One state per chat: `AWAITING_US`, `AWAITING_THEM`, `CLOSED`, `POSSIBLY_REPLIED` (client thanked us for a reply we never saw).
  "ok makasih 🙏", stickers and reactions never trigger alerts.
- Assigns an admin: roster handler for talents, the last owner (sticky after "Saya ambil"), else round-robin.
- Escalation (all in `config/config.yaml`): 30 business min → assignee DM; 2 h → admin group + supervisor;
  P1 4 h → management; 18 h real time → urgent (on-call admin outside hours); 24 h → overdue.
- Batching: at most one Telegram message per person per hour (weekends: per 6 hours); unreplied chats repeat hourly. Notifications 09:30–21:00 WIB; work hours 09:30–18:30 Mon–Fri (used for response-time metrics); holidays count as weekend. 09:30 morning list.
- AI check (Claude Haiku, optional): reads the last messages and decides question / inquiry / closure before any alert; adds a one-line summary.
- Telegram buttons ✋ ✅ ⏰ (→ admin for supervisor triage, 👍/👎 in review mode), commands `/antrian /semua /cari /spam /janji /hapus /bantuan`, whitelist by Telegram user ID.
- Promise tracker: "besok saya kirim" in an admin reply becomes a due reminder; deal pipeline with one-tap
  confirmation of AI-spotted agreements; reply drafts that learn from approved admin replies.
- Shadow mode (default): alerts are only written to `data/shadow_alerts.jsonl`.
- Each admin can have their own supervisor(s), who get that admin's 2 h / 18 h / 24 h escalations.
- Message texts erased after 90 days; daily DB backups (14 kept).

## Run locally
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
pytest
python -m heroez_ops.replay examples/sample_day.jsonl --config examples/demo_config.yaml --until 2026-11-03T10:00:00+07:00
```

## Windows installer (one always-on PC)
See `docs/SETUP_WINDOWS.md`. The installer is built by `.github/workflows/windows-installer.yml`
on a Windows runner (PyInstaller + WinSW service + bundled cloudflared + Inno Setup).
Settings page: http://127.0.0.1:8788 (local only). Data: `C:\ProgramData\HeroezOps`.

## Web app on a Linux server (recommended)
One container runs everything and is reached only through a Cloudflare Tunnel (no open ports, no `.env`):
```bash
docker compose -f deploy/docker-compose.yml build
docker compose -f deploy/docker-compose.yml run --rm app heroez-ops init   # tunnel token, addresses, first password
docker compose -f deploy/docker-compose.yml up -d
```
Then open the settings address in a browser and enter the remaining keys there. Step by step: `docs/SETUP_SERVER.md`.

## Layout
`heroez_ops/sources/` inbox adapters (`InboxSource`), `alerts/` channels (`AlertChannel`),
`states.py` state engine, `escalation.py` ladder, `notifier.py` batching and text, `bot.py` Telegram
commands and buttons, `replay.py` replay tool, `app.py` HTTP entry point.
