"""Builders for Meta webhook payloads (tests and the replay tool).

Shapes mirror Meta's documented webhooks. After onboarding, real recorded payloads
go in tests/payloads/ and the integration tests run against them too.
"""
from __future__ import annotations

import itertools

_ids = itertools.count(1)


def _id() -> str:
    return f"wamid.TEST{next(_ids):08d}"


def _wrap(field: str, value: dict, business_number: str = "6280000000000") -> dict:
    value = {"messaging_product": "whatsapp",
             "metadata": {"display_phone_number": business_number, "phone_number_id": "PNID"}, **value}
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "WABA_ID", "changes": [{"field": field, "value": value}]}]}


def _body(msg_type: str, text: str) -> dict:
    if msg_type == "text":
        return {"text": {"body": text}}
    if msg_type == "reaction":
        return {"reaction": {"emoji": text or "👍", "message_id": "wamid.x"}}
    if msg_type in ("image", "document", "video"):
        return {msg_type: {"id": "MEDIA", "caption": text} if text else {"id": "MEDIA"}}
    return {msg_type: {"id": "MEDIA"}}


def inbound(wa_id: str, text: str, ts: int, name: str = "Klien", msg_type: str = "text", msg_id: str | None = None) -> dict:
    m = {"from": wa_id, "id": msg_id or _id(), "timestamp": str(ts), "type": msg_type, **_body(msg_type, text)}
    return _wrap("messages", {"contacts": [{"profile": {"name": name}, "wa_id": wa_id}], "messages": [m]})


def echo(wa_id: str, text: str, ts: int, business_number: str = "6280000000000", msg_id: str | None = None) -> dict:
    m = {"from": business_number, "to": wa_id, "id": msg_id or _id(), "timestamp": str(ts), "type": "text",
         "text": {"body": text}}
    return _wrap("smb_message_echoes", {"message_echoes": [m]}, business_number)


def history(threads: dict[str, list[tuple[str, str, int]]], business_number: str = "6280000000000") -> dict:
    """threads: {wa_id: [(direction 'in'|'out', text, ts), ...]}"""
    out = []
    for wa_id, msgs in threads.items():
        out.append({"id": wa_id, "messages": [
            {"from": business_number if d == "out" else wa_id, "id": _id(), "timestamp": str(ts), "type": "text",
             "text": {"body": t}, "history_context": {"status": "READ"}} for d, t, ts in msgs]})
    return _wrap("history", {"history": [{"metadata": {"phase": 0, "chunk_order": 1, "progress": 100}, "threads": out}]},
                 business_number)


def contact_sync(wa_id: str, name: str, action: str = "add") -> dict:
    return _wrap("smb_app_state_sync", {"state_sync": [
        {"type": "contact", "contact": {"full_name": name, "first_name": name.split()[0], "phone_number": wa_id},
         "action": action, "metadata": {"timestamp": "0"}}]})
