"""Meta WhatsApp Cloud API (coexistence) adapter.

Handles the webhook fields: messages, smb_message_echoes, history, smb_app_state_sync.
Payload shapes follow Meta's coexistence docs; recorded real payloads must be added
to tests/payloads/ after onboarding to confirm them (see SETUP_RUNBOOK.md, day-1 test).
"""
from __future__ import annotations

import hashlib
import hmac
import logging

from ..phone import normalize
from .base import ContactEvent, Event, InboxSource, MessageEvent

log = logging.getLogger(__name__)


def _text_of(m: dict) -> str:
    t = m.get("type", "")
    if t == "text":
        return (m.get("text") or {}).get("body", "")
    if t in ("image", "video", "document"):
        return (m.get(t) or {}).get("caption", "") or ""
    if t == "interactive":
        i = m.get("interactive") or {}
        return ((i.get("button_reply") or i.get("list_reply") or {}).get("title")) or ""
    if t == "button":
        return (m.get("button") or {}).get("text", "")
    if t == "reaction":
        return (m.get("reaction") or {}).get("emoji", "") or ""
    return ""


class MetaCoexistenceSource(InboxSource):
    name = "meta"

    def __init__(self, app_secret: str, business_number: str):
        self.app_secret = app_secret.encode()
        self.business_number = normalize(business_number)

    def verify_signature(self, body: bytes, headers: dict[str, str]) -> bool:
        sig = headers.get("x-hub-signature-256", "")
        if not self.app_secret or not sig.startswith("sha256="):
            return False
        expected = hmac.new(self.app_secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig[7:], expected)

    def parse(self, payload: dict) -> list[Event]:
        events: list[Event] = []
        if payload.get("object") != "whatsapp_business_account":
            return events
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                field = change.get("field")
                value = change.get("value") or {}
                try:
                    if field == "messages":
                        events += self._messages(value)
                    elif field == "smb_message_echoes":
                        events += self._echoes(value)
                    elif field == "history":
                        events += self._history(value)
                    elif field == "smb_app_state_sync":
                        events += self._state_sync(value)
                except Exception:  # one bad change must not drop the rest
                    log.exception("failed to parse %s change", field)
        return events

    def _messages(self, value: dict) -> list[Event]:
        names = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in value.get("contacts", [])}
        out = []
        for m in value.get("messages", []):
            wa = normalize(m.get("from", ""))
            if not wa or m.get("type") in ("system", "unsupported") and not _text_of(m):
                continue
            out.append(MessageEvent(
                direction="in", source="messages", wa_id=wa, msg_id=m["id"], ts=int(m["timestamp"]),
                type=m.get("type", "unknown"), text=_text_of(m), contact_name=names.get(m.get("from")), raw=m,
            ))
        return out

    def _echoes(self, value: dict) -> list[Event]:
        out = []
        for m in value.get("message_echoes", []):
            wa = normalize(m.get("to", ""))
            if not wa:
                continue
            out.append(MessageEvent(
                direction="out", source="echo", wa_id=wa, msg_id=m["id"], ts=int(m["timestamp"]),
                type=m.get("type", "unknown"), text=_text_of(m), raw=m,
            ))
        return out

    def _history(self, value: dict) -> list[Event]:
        out = []
        for chunk in value.get("history", []):
            if chunk.get("errors"):
                log.warning("history sync error: %s", chunk["errors"])
                continue
            for thread in chunk.get("threads", []):
                wa = normalize(thread.get("id", ""))
                for m in thread.get("messages", []):
                    sender = normalize(m.get("from", ""))
                    direction = "out" if sender == self.business_number else "in"
                    out.append(MessageEvent(
                        direction=direction, source="history", wa_id=wa, msg_id=m["id"], ts=int(m["timestamp"]),
                        type=m.get("type", "unknown"), text=_text_of(m), raw=m,
                    ))
        return out

    def _state_sync(self, value: dict) -> list[Event]:
        out = []
        for item in value.get("state_sync", []):
            if item.get("type") != "contact":
                continue
            c = item.get("contact") or {}
            wa = normalize(c.get("phone_number", ""))
            if wa:
                out.append(ContactEvent(
                    wa_id=wa, name=c.get("full_name") or c.get("first_name"), removed=item.get("action") == "remove",
                ))
        return out
