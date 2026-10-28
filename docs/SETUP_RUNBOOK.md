# Meta (WhatsApp Cloud API) onboarding runbook

Goal: connect the **existing** Heroez WhatsApp Business number to the Cloud API in *coexistence* mode, so the
app receives copies of every private chat (and the admins' replies) while the team keeps using WhatsApp
Business on the phone and WhatsApp Web. Nothing unofficial is involved.

Meta's screens change often. The steps below name the things you need; when a button has moved, search the
official docs for the phrase in *italics*: <https://developers.facebook.com/docs/whatsapp/>.

Before you start, finish `SETUP_WINDOWS.md` sections 1–3 (program installed, Telegram working, public address up).
You need: the Heroez Facebook Business Portfolio (Business Manager) admin login, the phone with the Heroez
WhatsApp Business app on it, and about an hour.

## 1. Conditions (check first)
- The number is on the **WhatsApp Business app** (not personal WhatsApp), updated to the latest version.
- The number is **not already** registered with the Cloud API or another BSP. If it ever was, it must be removed there first.
- The phone stays with the business: coexistence needs the app opened at least once every 14 days.
- Accepted: broadcast lists stop working, disappearing messages / view-once / live location are off in 1:1 chats,
  groups keep working but are not seen by the app (see `ADMIN_GUIDE_ID.md` §5).
- Indonesia is a supported country for coexistence at the time of writing; if the *Connect existing number* option is
  missing in step 4, that is the first thing to check in Meta's *coexistence* docs.

## 2. Meta app
1. <https://developers.facebook.com/apps> → **Create app** → use case *Other* → type **Business** → name it `Heroez Ops`,
   attach the Heroez Business Portfolio.
2. In the app dashboard add the **WhatsApp** product.
3. *App settings → Basic*: copy the **App secret** → Heroez Ops settings page section 9 → *Meta App Secret* → Simpan.
   (Secrets are stored encrypted on the host PC; never in the config file or Git.)

## 3. Webhook
1. In the Meta app: *WhatsApp → Configuration → Webhook → Edit*.
2. **Callback URL**: `https://<your hostname>/webhook/meta` and **Verify token**: both are shown at the top of the
   Heroez Ops settings page. Click *Verify and save* — the page must say verified (the host PC must be on and the
   tunnel running: settings page status shows *Tunnel: berjalan*).
3. *Webhook fields → Manage*: subscribe to **messages**, **smb_message_echoes**, **history**, **smb_app_state_sync**
   (the last three only exist for coexistence; if they are missing, the app is not yet on a version with coexistence —
   check *Webhook fields* in the coexistence docs).

## 4. Connect the existing number (coexistence onboarding)
1. *WhatsApp → API Setup* (or *Getting started*). Do **not** use the test number for production.
2. Choose **Add phone number → Connect an existing WhatsApp Business app number** (the *Embedded Signup* flow with
   *coexistence*; the docs call it *onboarding business app users*). Follow the flow on the Meta page:
   business details → scan the **QR code** with the WhatsApp Business app on the phone (Settings → Linked devices →
   Link a device, or the prompt the flow shows) → accept the terms about broadcast lists and disappearing messages.
3. When it finishes, the number shows under *Phone numbers* with a **Phone number ID**. Copy it → Heroez Ops settings
   page section 9 → *Phone number ID* → Simpan.
4. The phone app shows the number as connected to Meta (the *Meta Business* banner under Settings → Business tools).

## 5. Permanent access token (needed only for the Phase 2 bot replies)
1. Business settings (<https://business.facebook.com/settings>) → *Users → System users* → **Add** → name `heroez-ops`,
   role *Admin* is not needed: *Employee* is enough.
2. *Assign assets*: the Heroez Ops app with **Develop app** (or *Manage app*), and the **WhatsApp account** (WABA) with
   *Manage* / *Message* permissions.
3. **Generate new token** → app `Heroez Ops` → expiry *Never* → permissions **whatsapp_business_messaging** and
   **whatsapp_business_management** → copy the token once → settings page section 9 → *Meta access token* → Simpan.
4. Without this token the app still receives everything and sends reminders; only the bot replies (Phase 2) are off.

## 6. Check that messages arrive
1. From another phone, send a WhatsApp message to the Heroez number. Within a minute the settings page status shows
   *Pesan terakhir diterima: …* and the chat appears in `/semua` on Telegram (shadow mode still sends nothing to admins).
2. Reply from WhatsApp Web or the phone. The chat disappears from `/semua` (the reply "echo" was received). A reply
   from the **WhatsApp for Windows** app does *not* produce an echo — that is why the admins must not use it.
3. *History*: after onboarding Meta sends the last ~6 months of chats in chunks over a few minutes. Those are stored
   so the morning list is correct on day one; they never trigger reminders.
4. If nothing arrives: Meta app → *Webhooks* → check the subscription is on the **WhatsApp Business Account** object;
   the callback URL opens in a browser (`/health` returns `{"ok":true}`); tunnel status; Windows service running.

## 7. Business verification and limits
- Receiving messages and the admins' own replies cost nothing and have no limit.
- API sends (the bot's replies) are free up to 1,000 service conversations per month; after that about IDR 357 per
  24-hour conversation (Meta's *service* rate for Indonesia — check the current *pricing* page). The app's monthly
  send budget (settings section 7) stops the bot before the bill grows.
- An unverified business can message a limited number of unique customers per day; **Business verification** in the
  Business Portfolio removes the limit. Start it early: it can take days.
- Display name: the name on the number must match the business; Meta may ask to review it.

## 8. Going live checklist
- [ ] Shadow week finished: `data\shadow_alerts.jsonl` reviewed, reminder timing is right, no false "unreplied" chats
- [ ] Every admin and supervisor has `/start`-ed the bot and received the test message
- [ ] Admins know: reply from the phone or WhatsApp Web only (never the Windows app)
- [ ] Clients and talents informed of the processing (data-flow note in `DATA_FLOW_ID.md`)
- [ ] Health monitor set on `/health`
- [ ] Switch settings section 1 to **Review** (alerts to supervisors only, with 👍/👎) for a few days, then **Live**
- [ ] Bot (Phase 2) stays off until the KOL list, availability and texts on the *Pengetahuan* page have been checked
