"""Sending through the WhatsApp Cloud API (bot menu, approved FAQ answers, approved drafts).

Every message sent here is counted: after 1,000 service messages per month Meta bills
each one. In shadow mode nothing is sent; the message is logged instead.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
from pathlib import Path

import httpx

from ..db import Store

log = logging.getLogger(__name__)

FREE_SERVICE_PER_MONTH = 1000
BUTTON_TITLE_MAX, LIST_TITLE_MAX, LIST_DESC_MAX, BODY_MAX = 20, 24, 72, 1024


class SendError(Exception):
    pass


class MetaSender:
    def __init__(self, store: Store, access_token: str, phone_number_id: str, api_version: str = "v23.0",
                 live: bool = False, shadow_log: Path | None = None, client: httpx.Client | None = None):
        self.store, self.token, self.phone_id, self.version = store, access_token, phone_number_id, api_version
        self.live = live and bool(access_token and phone_number_id)
        self.shadow_log = shadow_log
        self.http = client or httpx.Client(timeout=20)

    @property
    def configured(self) -> bool:
        return bool(self.token and self.phone_id)

    # -- message builders -----------------------------------------------------
    def text(self, to: str, body: str, purpose: str) -> str | None:
        return self._send(to, {"type": "text", "text": {"body": body[:4096], "preview_url": False}}, purpose, body)

    def buttons(self, to: str, body: str, options: list[tuple[str, str]], purpose: str) -> str | None:
        btns = [{"type": "reply", "reply": {"id": i, "title": t[:BUTTON_TITLE_MAX]}} for i, t in options[:3]]
        return self._send(to, {"type": "interactive", "interactive": {
            "type": "button", "body": {"text": body[:BODY_MAX]}, "action": {"buttons": btns}}}, purpose, body)

    def list(self, to: str, body: str, button: str, rows: list[tuple[str, str]], purpose: str) -> str | None:
        items = [{"id": i, "title": t[:LIST_TITLE_MAX]} for i, t in rows[:10]]
        return self._send(to, {"type": "interactive", "interactive": {
            "type": "list", "body": {"text": body[:BODY_MAX]},
            "action": {"button": button[:BUTTON_TITLE_MAX], "sections": [{"title": button[:24], "rows": items}]}}},
            purpose, body)

    def document(self, to: str, media_id: str, filename: str, caption: str, purpose: str) -> str | None:
        return self._send(to, {"type": "document", "document": {"id": media_id, "filename": filename,
                                                                "caption": caption[:BODY_MAX]}}, purpose, caption)

    def upload_media(self, data: bytes, filename: str, mime: str = "application/pdf") -> str:
        if not self.configured:
            raise SendError("Meta belum dihubungkan (token / phone number ID kosong).")
        r = self.http.post(f"https://graph.facebook.com/{self.version}/{self.phone_id}/media",
                           headers={"Authorization": f"Bearer {self.token}"},
                           data={"messaging_product": "whatsapp", "type": mime},
                           files={"file": (filename, data, mime)})
        if r.status_code >= 300:
            raise SendError(f"Upload ke Meta gagal ({r.status_code}).")
        return r.json()["id"]

    # -- core -------------------------------------------------------------------
    def _send(self, to: str, message: dict, purpose: str, summary: str) -> str | None:
        """Send one message. Returns the message id ('shadow-…' when not live)."""
        now = int(time.time())
        if not self.live:
            mid = f"shadow-{now}-{abs(hash((to, summary))) % 10**8}"
            if self.shadow_log:
                with self.shadow_log.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"op": "whatsapp_send", "to": to, "purpose": purpose, "message": message,
                                        "at": now}, ensure_ascii=False) + "\n")
            self.store.audit("bot", f"wa_send_shadow:{purpose}", to, {"summary": summary[:200]}, now)
            return mid
        payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, **message}
        try:
            r = self.http.post(f"https://graph.facebook.com/{self.version}/{self.phone_id}/messages",
                               headers={"Authorization": f"Bearer {self.token}"}, json=payload)
        except httpx.HTTPError as e:
            log.error("WhatsApp send failed: %s", type(e).__name__)
            raise SendError("Tidak bisa menghubungi Meta.") from e
        if r.status_code >= 300:
            err = (r.json().get("error") or {}) if r.headers.get("content-type", "").startswith("application/json") else {}
            log.error("WhatsApp send rejected: %s %s", r.status_code, err.get("message"))
            raise SendError(f"Meta menolak pesan: {err.get('message') or r.status_code}")
        mid = r.json()["messages"][0]["id"]
        month = dt.date.today().strftime("%Y-%m")
        self.store.x("INSERT INTO api_sends(month,purpose,count) VALUES(?,?,1) ON CONFLICT(month,purpose) "
                     "DO UPDATE SET count=count+1", (month, purpose))
        self.store.audit("bot", f"wa_send:{purpose}", to, {"id": mid, "summary": summary[:200]}, now)
        return mid


def month_sends(store: Store, month: str | None = None) -> int:
    month = month or dt.date.today().strftime("%Y-%m")
    return int(store.one("SELECT COALESCE(SUM(count),0) n FROM api_sends WHERE month=?", (month,))["n"])
