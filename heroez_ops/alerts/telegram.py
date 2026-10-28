"""Telegram Bot API channel (free). Uses plain HTTPS calls; no extra library."""
from __future__ import annotations

import json
import time
import logging
from pathlib import Path

import httpx

from .base import AlertChannel, Button

log = logging.getLogger(__name__)


def _markup(buttons: list[list[Button]] | None) -> dict | None:
    if not buttons:
        return None
    return {"inline_keyboard": [[{"text": b.text, "callback_data": b.data} for b in row] for row in buttons]}


class TelegramAlertChannel(AlertChannel):
    def __init__(self, token: str, client: httpx.Client | None = None):
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN is not set")
        self.base = f"https://api.telegram.org/bot{token}"
        self.http = client or httpx.Client(timeout=15)

    def _call(self, method: str, payload: dict, timeout: float | None = None) -> dict | list | None:
        payload = {k: v for k, v in payload.items() if v is not None}
        try:
            r = self.http.post(f"{self.base}/{method}", json=payload, timeout=timeout or 15)
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            # Don't log the exception text: it contains the URL, which contains the bot token.
            log.error("telegram %s failed: %s", method, type(e).__name__)
            return None
        if not data.get("ok"):
            log.error("telegram %s error: %s", method, data.get("description"))
            return None
        return data.get("result")

    def send(self, chat_id, text, buttons=None):
        res = self._call("sendMessage", {
            "chat_id": chat_id, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True, "link_preview_options": {"is_disabled": True}, "reply_markup": _markup(buttons),
        })
        return res.get("message_id") if isinstance(res, dict) else None

    def edit(self, chat_id, message_id, text, buttons=None):
        self._call("editMessageText", {
            "chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True, "link_preview_options": {"is_disabled": True}, "reply_markup": _markup(buttons),
        })

    def edit_buttons(self, chat_id, message_id, buttons):
        self._call("editMessageReplyMarkup", {
            "chat_id": chat_id, "message_id": message_id, "reply_markup": _markup(buttons) or {"inline_keyboard": []},
        })

    def answer_callback(self, callback_id, text):
        self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})

    def get_me(self) -> dict | None:
        return self._call("getMe", {})

    def get_updates(self, offset: int, wait_seconds: int = 25) -> list[dict]:
        """Long polling: no public address needed. Returns [] on errors."""
        res = self._call("getUpdates", {"offset": offset, "timeout": wait_seconds,
                                        "allowed_updates": ["message", "callback_query"]}, timeout=wait_seconds + 10)
        return res if isinstance(res, list) else []

    def delete_webhook(self) -> None:
        self._call("deleteWebhook", {})

    def set_webhook(self, url: str, secret: str) -> dict | None:
        return self._call("setWebhook", {"url": url, "secret_token": secret, "allowed_updates": ["message", "callback_query"]})


class ShadowAlertChannel(AlertChannel):
    """Shadow mode: record what would have been sent, send nothing."""

    def __init__(self, log_path: str | Path = "data/shadow_alerts.jsonl"):
        self.path = Path(log_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.sent: list[dict] = []
        self._next_id = 1

    def _write(self, rec: dict) -> None:
        rec.setdefault("at", int(time.time()))
        self.sent.append(rec)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def send(self, chat_id, text, buttons=None):
        mid = self._next_id
        self._next_id += 1
        self._write({"op": "send", "chat_id": chat_id, "message_id": mid, "text": text,
                     "buttons": [[b.text for b in row] for row in buttons or []]})
        return mid

    def edit(self, chat_id, message_id, text, buttons=None):
        self._write({"op": "edit", "chat_id": chat_id, "message_id": message_id, "text": text})

    def edit_buttons(self, chat_id, message_id, buttons):
        self._write({"op": "edit_buttons", "chat_id": chat_id, "message_id": message_id,
                     "buttons": [[b.text for b in row] for row in buttons or []]})

    def answer_callback(self, callback_id, text):
        self._write({"op": "answer", "callback_id": callback_id, "text": text})
