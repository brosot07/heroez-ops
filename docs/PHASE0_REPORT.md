# Phase 0 Report: Verify and Decide

Date: 28 Oct 2026 · Status: **waiting on your decisions. No code has been written.**

## How this was verified (read first)

This session's network blocks `developers.facebook.com`, so I could not read Meta's own docs directly. Everything below about Meta comes from web search results that summarise Meta's docs, plus vendor documentation (360dialog, YCloud, Twilio, Wati, ChakraHQ and others). Where several sources agree, I mark the finding **likely**. Where they conflict or only one source covers it, I mark it **unverified**. Before we build, each item marked ⚠️ needs one of two checks: you allow `developers.facebook.com` in this environment's network settings so I can read the docs, or we test it live on day 1 of onboarding (see "Day-1 test plan").

---

## 1. Findings

### 1.1 Coexistence eligibility for an Indonesian number: **likely OK**
- Every source that names countries lists Indonesia as supported. One vendor says all countries have been supported since May 2026. Meta does not publish a fixed list; eligibility is checked per number during signup.
- Requirements: WhatsApp Business app version ≥ 2.24.17. Several vendors add that the number should have been actively used on the app for at least 7 days. Our number qualifies easily.
- The app must be opened at least every ~13–14 days, or the API link lapses. The admins use it daily, so this isn't a problem, but it goes into OPERATIONS.md.

### 1.2 Onboarding with our own Meta app and no paid vendor: ⚠️ **the biggest open risk**
- The coexistence path is **Embedded Signup for Business app users**. Meta documents it as a flow run by a *Solution Partner or Tech Provider*. Becoming a Tech Provider is free, and Meta's Tech Provider page has an "onboard without a partner" option. It requires:
  1. Business verification of Heroez's Meta business portfolio (legal name, address and documents; usually days, sometimes weeks)
  2. A Meta app with the WhatsApp use case
  3. **App Review** for advanced access to `whatsapp_business_management` and `whatsapp_business_messaging`, with a screencast of our use case (typically 1–4 weeks)
  4. Hosting a small Embedded Signup web page (Facebook JS SDK) that you click through once, to onboard our own number
- **Unverified:** whether Meta approves a Tech Provider that will only ever onboard its own number. I found no source either way. Options if Meta blocks or delays it are in Decision Q1.
- Webhook fields to subscribe: `messages`, `smb_message_echoes`, `history`, `smb_app_state_sync`. The brief's list is correct.
- History sync must finish within 24 h of onboarding; otherwise the number is offboarded and the signup has to be repeated.
- Companion devices are unlinked during onboarding and must be re-linked (max 4).

### 1.3 Which desktop apps generate `smb_message_echoes`: **WhatsApp Web and Mac likely yes, Windows NO**
| Companion | Supported under coexistence | Echo webhook |
|---|---|---|
| WhatsApp Business app (phone) | Yes | Yes |
| **WhatsApp Web** (browser) | Yes (likely) | Yes (likely) ⚠️ |
| **WhatsApp for Mac** | Yes (likely) | Yes (likely) ⚠️ |
| **WhatsApp for Windows** | **No**, named as unsupported in several sources | **No** |
| WearOS | No | No |

**Consequence:** if the admins' desktops run Windows, they must use **WhatsApp Web in Chrome or Edge, not the Windows app**. Replies sent from the Windows app would be invisible to us, so the system would keep alerting on chats that were already answered. The admin guide will make this rule 1. We confirm it on day 1 by sending a test from each device.

### 1.4 What stops working in the Business app under coexistence (goes into the admin guide)
- **Broadcast lists:** you can't create new ones, and existing ones become read-only. ❗ If admins use broadcast lists to reach talents, that workflow breaks (Q4).
- **Disappearing messages, view-once and live location** are switched off in 1:1 chats.
- **Deleting a sent message ("delete for everyone")** in 1:1 chats: one source says it is no longer supported.
- **Groups** keep working in the app but are **not synced to the API**. The system is blind to group chats (Q4).
- Calls still work in the app; the system never sees them.
- Away messages still work. The greeting message must be turned off by us (as the brief already says).

### 1.5 History sync: **yes, up to 180 days, one time**
- During onboarding the business is asked to share chat history. If you approve, 1:1 messages from the last ~180 days and contacts are delivered through the `history` webhook. Groups are excluded.
- It happens **once**, the choice can't be changed later without redoing onboarding, and it must complete within 24 h.
- **Recommendation: approve sharing.** It gives us the evaluation set (200 labelled conversations) and correct starting states on day 1.

### 1.6 Meta pricing for Indonesia (from 1 Oct 2026; IDR per delivered message, before 11% VAT)
| Category | Rate | Notes |
|---|---|---|
| Service (free-form reply in the 24 h window, sent via **API**) | **IDR 356.65** after the first **1,000 free per number per month** | Counted per message, not per conversation; the allowance does not roll over |
| Utility template | IDR 356.65 | Several sources say in-window utility is no longer free ⚠️ |
| Marketing template | IDR 586.33 | |
| Authentication | IDR 356.65 | |
| Replies from the Business app (phone, Web, Mac) | **Free** | |

Older figures still circulating online (marketing 596.33, utility 295.32) predate the October card. A payment method must be on the WABA before we exceed 1,000, or API sends may fail.

### 1.7 ❗ Correction to the brief: the 24-hour window probably doesn't limit admin replies
The 24 h customer-service window governs messages sent **through the API**. The WhatsApp Business app has never had that restriction: an admin can reply from the app days later, free. In coexistence, app messages are free and are not governed by API window rules.
**So rule 5 and the `window_expired → needs template` row are only half right.** The window matters for **our intake bot** (it can't send free-form messages after 24 h). It does not block an admin replying late from the app.
**Proposal:** keep the 18 h and 24 h steps as **SLA escalations** (a 3-day-old lead is still a lost deal), but drop the "a reply now needs an approved template" message. Templates are needed only if the system itself must send, which it never does outside intake. ⚠️ We confirm on day 1 by replying from the app to a chat older than 24 h.

### 1.8 Expected intake bot cost (the brief's tree exceeds the free 1,000)
Assumption: about **15 new unknown contacts a day** (≈450 a month). Please correct this; it is the main driver.

| Intake design | API messages per brand lead | Messages/month | Billable over 1,000 | Cost/month (incl. VAT) |
|---|---|---|---|---|
| Tree as written (greeting, category, platform, budget, timeline, PDF, SLA text) | 7 | ~3,150 | ~2,150 | **≈ IDR 850k (~US$52)** |
| **Proposed:** greeting + "Siapa Anda?" buttons → one **WhatsApp Flow** form (category, platforms, budget, timeline in one screen) → rate-card PDF with the SLA line as its caption | **3** | ~1,350 | ~350 | **≈ IDR 140k (~US$8)** |
| Proposed design at 10 new contacts a day | 3 | ~900 | 0 | **IDR 0** |

The Flow answers come back as inbound messages, which are free. A Flow sent free-form inside the window should count as one service message; several vendor docs say so, but it is unverified against Meta ⚠️. The Talent and "Lainnya" branches cost 2 messages each.

### 1.9 Telegram fallback (D4) cost
If admins refuse Telegram: utility templates to their personal numbers at ≈ IDR 396 each incl. VAT. With 10-minute batching, roughly 30 alerts per admin per day ≈ 1,800 a month ≈ **IDR 710k (~US$43) a month**. Telegram is free, so it stays the default.

### 1.10 Claude API (current prices, per million tokens)
- `claude-haiku-5-5`: $0.10 input / $0.50 output (for prompts under 100k tokens). Used for triage, state and promises.
- `claude-sonnet-5-5`: $2 input / $10 output, cache reads $0.20. Used for summaries, suggestions and matching.
- Estimate at ~800 inbound and ~600 echoes a day, with the rules pre-filter removing ~40% of LLM calls:
  Haiku ≈ $10–20 · Sonnet summaries ≈ $15 · Phase 2 suggestions (knowledge base cached) ≈ $20 · Phase 3 matching ≈ $3. **Total ≈ $30–60 a month**, metered per component per day.

### 1.11 Hosting proposal (D2)
**Recommended: DigitalOcean Droplet in Singapore, 2 GB RAM, plus weekly backups ≈ US$15 a month.**
- Boring, predictable pricing, and ~20 ms from Jakarta. One Docker Compose stack: app, Postgres (encrypted volume), and Caddy for automatic HTTPS. Domain: a subdomain of heroez's existing domain ($0).
- Cheaper alternative: Hetzner Singapore. Hetzner raised prices twice in 2026 and I couldn't confirm current Singapore pricing, so I'd only switch after seeing the checkout price. Not recommended: Oracle's free tier (idle reclamation) or local Indonesian VPS providers (fine, but weaker tooling for one-command deploys).
- **Health alerting needs an outside watcher.** A dead server can't report its own death. A free external monitor (Healthchecks.io or UptimeRobot) pings `/health` and alerts the management Telegram group after 10 minutes. This meets rule 10.
- **UU PDP note:** Claude processing happens outside Indonesia whatever the hosting choice, so cross-border transfer must be stated in the data-flow summary either way. Singapore hosting doesn't change the analysis.

---

## 2. Expected monthly cost for the whole stack

| Item | Low | Expected | Notes |
|---|---|---|---|
| Server, backups, HTTPS | $15 | $15 | DO Singapore |
| Domain | $0 | $0 | heroez subdomain |
| Uptime monitor and Telegram | $0 | $0 | Free tiers |
| Meta: intake (proposed Flow design) | $0 | $8 | Depends on new-contact volume |
| Meta: admin replies from the app | $0 | $0 | Free |
| Meta: templates | $0 | ~$0 | Only if we ever re-engage via the API |
| Claude API | $30 | $45 | Up to ~$60 with Phase 2–3 |
| **Total** | **≈ $45** | **≈ $70 (≈ IDR 1.15 M)** | |

---

## 3. Day-1 test plan (right after onboarding, before shadow mode)
1. Send a reply from the phone, WhatsApp Web, WhatsApp for Mac and WhatsApp for Windows. Confirm which produce `smb_message_echoes`.
2. Reply from the app to a chat whose last inbound message is more than 24 h old. Confirm delivery (verifies 1.7).
3. Send one intake Flow to a test number. Check how it is billed in WhatsApp Manager the next day (verifies 1.8).
4. Confirm the `history` backfill arrived, with counts and date range.
5. Stop the service. Confirm the admins notice nothing and the external monitor alerts management.

---

## 4. Decisions needed from you
See the questions in chat. Also needed, not blocking Phase 1:
- **D6** real budget tiers (IDR)
- **D9** monthly cap for Meta and Claude spend
- **D10** who owns config after handover
- Who labels the 200 evaluation conversations (I'll propose a Google Sheet with columns: chat_id, last 10 messages, state, promise text + due, sender_type, intent, priority, notes)
