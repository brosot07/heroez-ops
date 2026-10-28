# Heroez Ops: WhatsApp reply reminders

Listen-only service: reads the Heroez WhatsApp Business number (Meta Cloud API, coexistence),
works out which chats are waiting on us, and reminds admins on Telegram. It never messages
clients. Admins keep replying from the WhatsApp Business app or WhatsApp Web.

**Status:** reminders with optional AI check, built and tested against simulated Meta payloads.
Not yet connected to a real number. See `docs/PHASE0_REPORT.md` for open items.

## What it does
- Stores inbound messages, admin replies (`smb_message_echoes`) and history backfill; dedupes by message id.
- One state per chat: `AWAITING_US`, `AWAITING_THEM`, `CLOSED`, `POSSIBLY_REPLIED` (client thanked us for a reply we never saw).
  "ok makasih 🙏", stickers and reactions never trigger alerts.
- Assigns an admin: roster handler for talents, the last owner (sticky after "Saya ambil"), else round-robin.
- Escalation (all in `config/config.yaml`): 30 business min → assignee DM; 2 h → admin group + supervisor;
  P1 4 h → management; 18 h real time → urgent (on-call admin outside hours); 24 h → overdue.
- Batching: at most one Telegram message per person per hour (weekends: per 6 hours); unreplied chats repeat hourly. Notifications 09:30–21:00 WIB; work hours 09:30–18:30 Mon–Fri (used for response-time metrics); holidays count as weekend. 09:30 morning list.
- AI check (Claude Haiku, optional): reads the last messages and decides question / inquiry / closure before any alert; adds a one-line summary.
- Telegram buttons ✋ ✅ ⏰ 🚫, commands `/antrian /semua /cari /bantuan`, whitelist by Telegram user ID.
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

## Deploy on a Linux server (alternative)
```bash
cp .env.example .env   # fill in secrets
docker compose -f deploy/docker-compose.yml up -d --build
```
Point Meta's webhook to `https://$DOMAIN/webhook/meta` and Telegram's to `https://$DOMAIN/webhook/telegram`.
Add a free uptime monitor on `https://$DOMAIN/health` that alerts management after 10 minutes.

## Layout
`heroez_ops/sources/` inbox adapters (`InboxSource`), `alerts/` channels (`AlertChannel`),
`states.py` state engine, `escalation.py` ladder, `notifier.py` batching and text, `bot.py` Telegram
commands and buttons, `replay.py` replay tool, `app.py` HTTP entry point.
