# Operations guide (supervisors / system owner)

What runs where, what the app does by itself, and what a person does. Admin-facing instructions are in
`ADMIN_GUIDE_ID.md`; installation in `SETUP_WINDOWS.md`; Meta in `SETUP_RUNBOOK.md`.

## 1. Pieces
| Piece | Where | Notes |
|---|---|---|
| Heroez Ops service | host PC (Windows service, starts with Windows) | one instance; the only machine that stores data |
| Settings page | host PC only, <http://127.0.0.1:8788>, owner password | mode, people, hours, AI, bot, contracts, deals, Meta |
| Web pages: Antrian, Deal, Kontrak, Template, Pengetahuan | host PC (same address) **and** other laptops via `https://ops.<domain>/…` behind Cloudflare Access (email code login) | admins: queue, deals, contracts; supervisors also: templates, knowledge, approvals |
| Telegram bot | everyone's phone/laptop | reminders, buttons, `/antrian /semua /cari /spam /janji /hapus` |
| Meta Cloud API (coexistence) | Meta → tunnel → host PC | copies of 1:1 chats and admin replies; bot replies go out through it |
| Claude API | host PC → Anthropic | message check, bot understanding, reply drafts, template/document reading |
| Google Sheet (ops) | Google | KOL list / availability / FAQ pulled in; deal pipeline pushed out |
| Google Calendar | Google | talent availability (busy/free only) |

## 2. Modes (settings section 1)
- **Shadow**: everything is computed and logged to `data\shadow_alerts.jsonl`; nothing is sent to anyone.
- **Review**: Telegram alerts go only to supervisors, each with 👍/👎 to judge accuracy; the bot still sends nothing.
- **Live**: alerts to admins/groups per the ladder; the bot replies to new brands according to its own setting (below).

**Bot replies (settings section 7, or `/bot` in Telegram):**
- **Mati**: the bot sends nothing.
- **Cek dulu** (default): every message the bot wants to send goes to the supervisors on Telegram with
  ✅ Kirim / ❌ Jangan. Nothing reaches the client before ✅. Not approved within 30 minutes (setting) → not sent,
  the chat goes to an admin. An admin replying first cancels it. In Shadow/Review mode the cards are previews
  (👍/👎) and nothing is ever sent — use this to read the bot's answers before going live.
- **Otomatis**: the bot sends by itself, 24/7 (Live mode only). Can only be switched on from the settings page;
  supervisors can switch back with `/bot cek` or `/bot mati` from Telegram at any time.

## 3. What happens automatically
| Event | The app does |
|---|---|
| Client message arrives | stores it; word rules set the state; AI check within 10 min (question / inquiry / closing); links checked by address only |
| New number, bot on | spam filter → menu → questions → KOL rates & availability from the sheets → hand-over to the right admin (KOL handler) or supervisor triage (no KOL named, nano, solutions) |
| Unreplied chat | nudge after 30 business min → group after 2 h → management P1 after 4 h → urgent at 18 h real → overdue 24 h; repeats hourly; 09:30–21:00 WIB, 6-hourly on weekends/holidays; urgent 24/7 |
| Admin reply (echo/API) | closes the wait; reply becomes a style example candidate; compared with the AI draft; promises ("besok saya kirim") tracked |
| Promise due | reminder to the admin, supervisors 2 business hours later if still open |
| Agreement language in a client message | the assignee is asked on Telegram "Catat sebagai Deal?"; nothing is recorded without the tap |
| Contract made in the app | linked deal → *Kontrak* with the contract's total |
| 09:30 daily | morning list to admins; digest to supervisors/management (response times, pipeline, AI cost, draft acceptance, promises) |
| Daily after 03:00 | message texts older than 90 days erased; raw payloads older than 7 days deleted; backup to `data\backups` (14 kept); candidates/outcomes pruned |
| Every ~6 h | Google Calendar and Sheet re-synced (limits: 2 MB / 15 MB, https only, public hosts only) |
| Morning | deal rows pushed to the ops Google Sheet (key events pushed at once) |
| 3 h without webhooks in business time | warning to the management group |
| Bot sends reach 80% of the monthly budget | warning to supervisors; the bot stops at 100% |

## 4. What a person does
| Who | Task | Where |
|---|---|---|
| Admin | reply clients (phone / WhatsApp Web), tap ✋ ✅ ⏰, confirm deals, mark promises done, make contracts | WhatsApp, Telegram, web |
| Supervisor | assign triaged leads (→ admin buttons), judge alerts in review mode, approve templates, review documents the AI read, approve reply examples, mark deals Lunas/Batal, `/hapus` on client request | Telegram, web |
| Owner (host PC) | settings, secrets, mode switch, updates, backups off-disk, password resets | settings page |

## 5. Weekly checks (10 minutes)
1. Settings page status: tunnel running, AI active, last webhook recent, bot sends vs budget.
2. `/spam` in Telegram: anything real caught? *Bukan spam* rescues it.
3. Pengetahuan page: documents waiting for review; reply examples waiting for approval; calendar/sheet last sync OK.
4. Deal page: stale rows (idle days), anything to mark Lunas/Batal; the Google Sheet matches.
5. Digest: response-time trend, draft acceptance, promises kept.
6. Copy `C:\ProgramData\HeroezOps\data\backups` to another disk/cloud once a week.

## 6. Incidents
| Symptom | Do |
|---|---|
| No Telegram messages | mode is Shadow? person `/start`-ed the bot? *Kirim pesan tes* on the settings page; Windows Services → Heroez Ops running |
| Reminders for chats already answered | admin replied from the WhatsApp **Windows app** (no echo) — use the phone or WhatsApp Web; ✅ *Sudah dibalas* clears it |
| *Tunnel: berhenti* | restart the service; check the Cloudflare tunnel token (section 9) |
| Meta webhook errors | `SETUP_RUNBOOK.md` §6; Meta app → Webhooks → subscription on the WABA |
| Bot said something wrong | `/bot mati` (or `/bot cek`) in Telegram → fix texts/KOL data on Pengetahuan → back on after checking previews |
| AI cost jumps | settings section 6: daily cap; digest shows the month's calls; the cap is 3,000 calls/day by default |
| Lost owner password | Start menu → *Reset password Heroez Ops* (Windows admin) |
| Client asks to be forgotten | `/hapus <nomor>` (supervisor) — erases messages, states, promises for that number, strips the number/name from deals, logs the request |
| Host PC replaced | install on the new PC, stop the old service, copy `C:\ProgramData\HeroezOps` (config, data, secrets are tied to the Windows machine — re-enter the secrets on the new PC), point the Cloudflare tunnel at it |

## 7. Costs (as of setup)
Telegram, Cloudflare Tunnel/Access (≤50 users), Google Sheets/Calendar, Apps Script: free.
Meta: receiving free; bot sends free to 1,000 conversations/month, then ≈IDR 357 each.
Claude: ≈US$1–5/month for checks and bot understanding (Haiku), plus drafts and document reading (Sonnet) —
typically US$10–30/month in total at current volumes; the digest reports the running cost.

## 8. Updating
Run the newer `HeroezOps-Setup-<version>.exe` on the host PC. Settings, secrets and data are kept; database
migrations run on start. Release notes: GitHub releases of the repository.
