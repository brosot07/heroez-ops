"""Connect the existing WhatsApp Business app number to our own Meta app, keeping the phone app
("coexistence"), with Meta's Embedded Signup. No partner in between: Meta → this server only.

Flow (run once by the owner on the settings site):
1. The page opens Meta's signup window (FB JS SDK, featureType=whatsapp_business_app_onboarding).
   The owner picks the Heroez business and scans a QR code with the WhatsApp Business app.
2. The window returns a one-time code plus the WhatsApp Business Account ID and phone number ID.
3. Here: exchange the code for a business token, subscribe our app to the account's webhooks,
   read the display number, then start the contacts sync and the history sync (must happen within
   24 hours of onboarding, contacts first). The number is NOT registered again (it already is).
"""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)
GRAPH = "https://graph.facebook.com"


class ConnectError(Exception):
    pass


def _err(r: httpx.Response) -> str:
    try:
        e = r.json().get("error") or {}
        return f"{e.get('message') or r.status_code} (code {e.get('code')})"
    except ValueError:
        return f"HTTP {r.status_code}"


def connect(code: str, app_id: str, app_secret: str, waba_id: str, phone_number_id: str,
            version: str = "v23.0", http: httpx.Client | None = None) -> dict:
    """Run every step after the signup window. Returns the result per step; raises only when the
    essential steps (token, webhook subscription, phone number) fail."""
    http = http or httpx.Client(timeout=30)
    base = f"{GRAPH}/{version}"
    steps: dict = {}

    r = http.get(f"{base}/oauth/access_token", params={"client_id": app_id, "client_secret": app_secret, "code": code})
    if r.status_code >= 300 or "access_token" not in r.text:
        raise ConnectError(f"Meta menolak kode masuk: {_err(r)}")
    token = r.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    steps["token"] = "ok"

    if not phone_number_id:
        r = http.get(f"{base}/{waba_id}/phone_numbers", headers=auth)
        nums = (r.json().get("data") or []) if r.status_code < 300 else []
        if not nums:
            raise ConnectError(f"Nomor WhatsApp tidak ditemukan di akun {waba_id}: {_err(r)}")
        phone_number_id = nums[0]["id"]

    r = http.post(f"{base}/{waba_id}/subscribed_apps", headers=auth)
    if r.status_code >= 300:
        raise ConnectError(f"Gagal berlangganan webhook untuk akun WhatsApp: {_err(r)}")
    steps["webhooks"] = "ok"

    r = http.get(f"{base}/{phone_number_id}", headers=auth, params={"fields": "display_phone_number,verified_name"})
    if r.status_code >= 300:
        raise ConnectError(f"Gagal membaca nomor WhatsApp: {_err(r)}")
    info = r.json()
    number = "".join(ch for ch in str(info.get("display_phone_number", "")) if ch.isdigit())
    steps["number"] = number

    # Contacts first, then the up-to-6-months history. Not fatal: the scanner works without history.
    for sync in ("smb_app_state_sync", "history"):
        r = http.post(f"{base}/{phone_number_id}/smb_app_data", headers=auth,
                      json={"messaging_product": "whatsapp", "sync_type": sync})
        if r.status_code < 300:
            steps[sync] = "ok"
        else:
            msg = _err(r)
            if "2593109" in msg:
                msg = "berbagi riwayat dimatikan di aplikasi WhatsApp Business (riwayat lama tidak diambil)"
            steps[sync] = f"gagal: {msg}"
            log.warning("coexistence %s sync failed: %s", sync, msg)

    return {"token": token, "waba_id": waba_id, "phone_number_id": phone_number_id, "business_number": number,
            "verified_name": info.get("verified_name", ""), "steps": steps}
