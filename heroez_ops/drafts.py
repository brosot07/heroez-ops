"""AI reply suggestions ("💡 Saran balasan") for Telegram alerts.

Grounded only in the knowledge base. A draft reaches the client only if an admin taps
Kirim and confirms; otherwise admins copy it into WhatsApp themselves (free).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from .ai import build_transcript
from .db import Store
from .kb import KB
from .textrules import redact

log = logging.getLogger(__name__)

PROMPT = (Path(__file__).resolve().parent / "prompts" / "draft_reply_v1.md").read_text(encoding="utf-8")
SCHEMA = {
    "type": "object",
    "properties": {"can_answer": {"type": "boolean"}, "reply": {"type": "string"}, "missing": {"type": "string"}},
    "required": ["can_answer", "reply", "missing"],
    "additionalProperties": False,
}
PRICES = {"claude-sonnet-5-5": (2.0, 10.0, 0.20)}   # USD per million tokens: input, output, cache read


class Drafter:
    def __init__(self, api_key: str, store: Store, kb: KB, tz, model: str = "claude-sonnet-5-5",
                 max_per_day: int = 150, client=None):
        if client is None:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key, timeout=60.0, max_retries=2)
        self.client, self.store, self.kb, self.tz, self.model, self.max_per_day = client, store, kb, tz, model, max_per_day

    def ensure(self, wa_id: str) -> None:
        """Make sure the conversation has a draft for its latest client message."""
        conv = self.store.one("SELECT * FROM conversations WHERE wa_id=?", (wa_id,))
        last_in = self.store.one("SELECT id FROM messages WHERE wa_id=? AND direction='in' ORDER BY ts DESC LIMIT 1", (wa_id,))
        if not conv or not last_in or conv["draft_msg_id"] == last_in["id"]:
            return
        today = dt.date.today().isoformat()
        used = int(self.store.get_kv(f"drafts:{today}", "0") or 0)
        if used >= self.max_per_day:
            return
        msgs = self.store.q("SELECT direction, ts, type, text FROM messages WHERE wa_id=? ORDER BY ts DESC LIMIT 12", (wa_id,))
        transcript = build_transcript(list(reversed(msgs)), self.tz)
        result = self._call(transcript)
        self.store.set_kv(f"drafts:{today}", str(used + 1))
        if result is None:
            return
        self.store.x("UPDATE conversations SET draft_text=?, draft_msg_id=?, draft_can_answer=? WHERE wa_id=?",
                     (result["reply"] if result["can_answer"] or result["reply"] else result["missing"],
                      last_in["id"], int(bool(result["can_answer"])), wa_id))
        if not result["can_answer"]:
            self.store.x("UPDATE conversations SET draft_text=? WHERE wa_id=?",
                         (json.dumps({"reply": result["reply"], "missing": result["missing"]}, ensure_ascii=False), wa_id))

    def _call(self, transcript: str) -> dict | None:
        import anthropic
        from .talents import context_for_ai as talent_context
        kb_text = "\n\n".join(x for x in (self.kb.context_for_ai(), talent_context(self.store, dt.datetime.now(self.tz).date()))
                              if x) or "(Knowledge base masih kosong.)"
        try:
            resp = self.client.beta.messages.create(
                model=self.model,
                max_tokens=2000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                system=[{"type": "text", "text": PROMPT},
                        {"type": "text", "text": f"<knowledge_base>\n{kb_text}\n</knowledge_base>",
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": f"<chat>\n{transcript}\n</chat>\n\nTulis saran balasan."}],
            )
        except (anthropic.APIConnectionError, anthropic.APIStatusError) as e:
            log.warning("draft failed: %s", type(e).__name__)
            return None
        self._meter(resp.usage)
        if resp.stop_reason != "end_turn":
            return None
        try:
            data = json.loads(next(b.text for b in resp.content if b.type == "text"))
        except (StopIteration, ValueError):
            return None
        data["reply"] = redact(data.get("reply", "").strip())[:1000]
        data["missing"] = data.get("missing", "").strip()[:300]
        return data

    def _meter(self, usage) -> None:
        from .ai import PRICES as HAIKU
        pin, pout, pcache = PRICES.get(self.model, HAIKU.get(self.model, (0, 0, 0)))
        inp, out = usage.input_tokens or 0, usage.output_tokens or 0
        cread = getattr(usage, "cache_read_input_tokens", 0) or 0
        cwrite = getattr(usage, "cache_creation_input_tokens", 0) or 0
        usd = (inp * pin + cwrite * pin * 1.25 + cread * pcache + out * pout) / 1_000_000
        self.store.x(
            "INSERT INTO ai_usage(day,model,calls,input_tokens,output_tokens,cache_read_tokens,usd) VALUES(?,?,1,?,?,?,?) "
            "ON CONFLICT(day,model) DO UPDATE SET calls=calls+1, input_tokens=input_tokens+excluded.input_tokens, "
            "output_tokens=output_tokens+excluded.output_tokens, cache_read_tokens=cache_read_tokens+excluded.cache_read_tokens, "
            "usd=usd+excluded.usd", (dt.date.today().isoformat(), self.model, inp + cwrite, out, cread, usd))


def parse_draft(conv) -> tuple[str, str, bool]:
    """(reply, missing, can_answer) from a conversation row."""
    if not conv or not conv["draft_text"]:
        return "", "", False
    if conv["draft_can_answer"]:
        return conv["draft_text"], "", True
    try:
        d = json.loads(conv["draft_text"])
        return d.get("reply", ""), d.get("missing", ""), False
    except ValueError:
        return "", conv["draft_text"], False
