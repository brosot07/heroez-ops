"""Understanding a new contact's message for the brand concierge (Claude Haiku).

The AI only extracts facts (who they are, which KOLs, dates). Every price and every
availability answer the bot sends is built by the app from the KOL list and calendar.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from .ai import PRICES
from .db import Store
from .textrules import redact

log = logging.getLogger(__name__)

PROMPT = (Path(__file__).resolve().parent / "prompts" / "concierge_v1.md").read_text(encoding="utf-8")
SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["brand", "talent", "spam", "other", "unclear"]},
        "lang": {"type": "string", "enum": ["id", "en"]},
        "brand_name": {"type": "string"},
        "campaign": {"type": "string"},
        "talents": {"type": "array", "items": {"type": "string"}},
        "category": {"type": "string", "enum": ["beauty", "fnb", "gaming", "fashion", "tech", "other", "unknown"]},
        "platforms": {"type": "array", "items": {"type": "string", "enum": ["tiktok", "instagram", "youtube", "other"]}},
        "date_start": {"type": "string"},
        "date_end": {"type": "string"},
        "budget_idr": {"type": "integer"},
        "needs_human": {"type": "boolean"},
        "summary_id": {"type": "string"},
    },
    "required": ["kind", "lang", "brand_name", "campaign", "talents", "category", "platforms", "date_start",
                 "date_end", "budget_idr", "needs_human", "summary_id"],
    "additionalProperties": False,
}


@dataclass
class Understanding:
    kind: str = "unclear"
    lang: str = "id"
    brand_name: str = ""
    campaign: str = ""
    talents: list[str] = field(default_factory=list)
    category: str = "unknown"
    platforms: list[str] = field(default_factory=list)
    date_start: str = ""
    date_end: str = ""
    budget_idr: int = 0
    needs_human: bool = False
    summary_id: str = ""

    def merge(self, newer: "Understanding") -> "Understanding":
        """Later messages add to what we already know; non-empty new values win."""
        out = Understanding(**self.__dict__)
        for k, v in newer.__dict__.items():
            if k == "talents":
                out.talents = list(dict.fromkeys(self.talents + v))
            elif k == "platforms":
                out.platforms = list(dict.fromkeys(self.platforms + v))
            elif k == "kind":
                out.kind = v if self.kind in ("unclear", "") else self.kind
            elif k == "needs_human":
                out.needs_human = self.needs_human or v
            elif v not in ("", 0, "unknown", None):
                setattr(out, k, v)
        return out

    def dates(self) -> tuple[dt.date, dt.date] | None:
        try:
            s = dt.date.fromisoformat(self.date_start)
            e = dt.date.fromisoformat(self.date_end or self.date_start)
            return (s, e) if e >= s else (e, s)
        except ValueError:
            return None


class ConciergeAI:
    def __init__(self, api_key: str, store: Store, tz, model: str = "claude-haiku-5-5", client=None):
        if client is None:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key, timeout=30.0, max_retries=2)
        self.client, self.store, self.tz, self.model = client, store, tz, model

    def understand(self, messages: list[str]) -> Understanding | None:
        import anthropic
        today = dt.datetime.now(self.tz).date()
        chat = "\n".join(f"KLIEN: {redact(m)[:1500]}" for m in messages[-6:])
        try:
            resp = self.client.messages.create(
                model=self.model, max_tokens=600,
                thinking={"type": "disabled"},
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                cache_control={"type": "ephemeral"},
                system=PROMPT,
                messages=[{"role": "user", "content": f"Hari ini: {today.isoformat()} ({today.strftime('%A')}).\n\n<pesan>\n{chat}\n</pesan>"}],
            )
        except (anthropic.APIConnectionError, anthropic.APIStatusError) as e:
            log.warning("concierge AI failed: %s", type(e).__name__)
            return None
        self._meter(resp.usage)
        if resp.stop_reason != "end_turn":
            return None
        try:
            d = json.loads(next(b.text for b in resp.content if b.type == "text"))
            d["summary_id"] = redact(d.get("summary_id", ""))[:200]
            d["talents"] = [t.strip()[:60] for t in d.get("talents", []) if t.strip()][:10]
            return Understanding(**{k: d[k] for k in SCHEMA["required"]})
        except (StopIteration, ValueError, KeyError, TypeError):
            return None

    def _meter(self, usage) -> None:
        pin, pout, pcache = PRICES.get(self.model, (0, 0, 0))
        inp, out = usage.input_tokens or 0, usage.output_tokens or 0
        cread = getattr(usage, "cache_read_input_tokens", 0) or 0
        cwrite = getattr(usage, "cache_creation_input_tokens", 0) or 0
        usd = (inp * pin + cwrite * pin * 1.25 + cread * pcache + out * pout) / 1_000_000
        self.store.x(
            "INSERT INTO ai_usage(day,model,calls,input_tokens,output_tokens,cache_read_tokens,usd) VALUES(?,?,1,?,?,?,?) "
            "ON CONFLICT(day,model) DO UPDATE SET calls=calls+1, input_tokens=input_tokens+excluded.input_tokens, "
            "output_tokens=output_tokens+excluded.output_tokens, cache_read_tokens=cache_read_tokens+excluded.cache_read_tokens, "
            "usd=usd+excluded.usd", (dt.date.today().isoformat(), self.model, inp + cwrite, out, cread, usd))
