# Connect the Heroez WhatsApp Business number (direct, no partner)

The number **stays on the WhatsApp Business app on the phone** ("coexistence"). Messages go from Meta
straight to the Heroez server; no other company sees them.

⚠️ **Never** use *WhatsApp → API Setup → Add phone number* in the Meta app. That moves the number away
from the phone app. Only use the **Hubungkan WhatsApp Business** button on the Heroez settings page.

Meta changes its screens often: if a button has a slightly different name, look for the closest match.

---

## A. Before you start
- Admin access to Heroez's **Meta Business account** (business.facebook.com).
- The phone with the **WhatsApp Business** app for the Heroez number, updated to the latest version.
- Start **business verification** now (it can take days and Meta may require it):
  business.facebook.com → **Settings** → **Security Center** → **Start verification**
  (legal name, address, phone, website, NIB / akta).

## B. Create the Meta app (5 min)
1. Go to **developers.facebook.com** → log in → **My Apps** → **Create App**.
2. App name: `Heroez Ops`. Contact email: yours.
3. Use case: **Connect with customers through WhatsApp** (or *Other* → type **Business**, then add WhatsApp).
4. Business portfolio: **Heroez** → **Create app**.

## C. App ID and App Secret → settings page (2 min)
1. In the app: **App settings** → **Basic**.
2. Copy **App ID** → Heroez settings page, section 9, box **Meta App ID**.
3. **App secret** → **Show** → copy → section 9, box **Meta App Secret**. (Only there, never in chat.)
4. Still on *Basic*, in **App domains** type `heroezadmin.nitsujlabs.app` → **Save changes**.
5. On the settings page click **Simpan & mulai ulang**.

## D. Facebook Login for Business + configuration (5 min)
1. In the app: **Add product** → **Facebook Login for Business** → **Set up**.
2. **Settings** of Facebook Login for Business:
   - **Login with the JavaScript SDK**: **Yes**
   - **Allowed domains for the JavaScript SDK**: `https://heroezadmin.nitsujlabs.app`
   - **Valid OAuth Redirect URIs**: `https://heroezadmin.nitsujlabs.app/`
   - **Save**.
3. **Configurations** → **Create configuration**:
   - Name: `Heroez WhatsApp`
   - Login variation: **WhatsApp Embedded Signup**
   - Permissions: **whatsapp_business_management** and **whatsapp_business_messaging**
   - **Create** → copy the **Configuration ID** → settings page, section 9, box **Configuration ID** → **Simpan & mulai ulang**.

## E. Webhook (3 min)
1. In the app: **WhatsApp** → **Configuration** → **Webhook** → **Edit**.
2. **Callback URL**: `https://heroez.nitsujlabs.app/webhook/meta`
3. **Verify token**: copy it from the top of the Heroez settings page (section 9 shows it) → **Verify and save**.
4. **Webhook fields** → **Manage** → subscribe: **messages**, **smb_message_echoes**, **history**,
   **smb_app_state_sync**, **account_update**.

## F. Connect (5 min, phone in hand)
1. Settings page → section 9 → **📱 Hubungkan WhatsApp Business**.
2. Click the button → Meta's window opens → log in → choose **Heroez** →
   **Connect your existing WhatsApp Business app** → enter the Heroez number → follow the QR / phone prompts.
3. **Allow sharing chat history** when asked (the app then knows on day 1 which chats are still waiting).
4. Wait on the page until **✅ Tersambung** appears. Linked devices (WhatsApp Web) are disconnected once:
   re-link them afterwards.

## G. Check
- Send a WhatsApp message to the Heroez number from another phone. Within a minute the settings page shows
  **Pesan WhatsApp terakhir masuk: … detik lalu**.
- Reply from the phone or WhatsApp Web → in Telegram `/semua` the chat disappears from the waiting list.
- Keep **Mode: Shadow** for the first week (nothing is sent to anyone; the app only watches and logs).

## If Meta says the app needs review / Live mode
Meta sometimes requires **App Review** (advanced access to the two WhatsApp permissions, with a short screen
recording of the settings page and this flow) or business verification before the signup works. That is the
"Tech Provider" path; it is free but takes 1–4 weeks. Send a screenshot of Meta's message and we'll do it step by step.
