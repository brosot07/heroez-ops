"""AI check of a chat's last message: does Heroez still owe a reply?

Uses Claude Haiku via the official SDK with a fixed JSON output schema. Every call
is metered (tokens and estimated USD per day). Any failure returns None and the
caller falls back to the text rules, so alerts never depend on the AI being up.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .db import Store
from .textrules import redact

log = logging.getLogger(__name__)

PROMPT_VERSION = "classify_v1"
PROMPT = (Path(__file__).resolve().parent / "prompts" / f"{PROMPT_VERSION}.md").read_text(encoding="utf-8")

KINDS = ["question", "inquiry", "request", "complaint", "waiting_on_us", "greeting", "media",
         "closing", "acknowledgement", "will_get_back", "update_no_reply", "spam", "other"]

SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": KINDS},
        "needs_reply": {"type": "boolean"},
        "priority": {"type": "string", "enum": ["P1", "P2", "P3"]},
        "summary_id": {"type": "string"},
        "confidence": {"type": "number"},
        "deal_signal": {"type": "string", "enum": ["none", "agreement", "proposal_requested"]},
        "deal_value_idr": {"type": "integer"},
    },
    "required": ["kind", "needs_reply", "priority", "summary_id", "confidence", "deal_signal", "deal_value_idr"],
    "additionalProperties": False,
}

# USD per million tokens (input, output, cache read). Check current pricing when changing models.
PRICES = {"claude-haiku-5-5": (0.10, 0.50, 0.01)}


@dataclass(frozen=True)
class Verdict:
    kind: str
    needs_reply: bool
    priority: str
    summary: str
    confidence: float
    deal_signal: str = "none"          # none | agreement | proposal_requested
    deal_value: int = 0                # Rp mentioned with the agreement, if any


class Classifier(Protocol):
    def classify(self, transcript: str, contact_kind: str) -> Verdict | None: ...


def build_transcript(rows: list, tz) -> str:
    """rows: messages oldest first (direction, ts, type, text)."""
    lines = []
    for r in rows:
        who = "KLIEN" if r["direction"] == "in" else "HEROEZ"
        when = dt.datetime.fromtimestamp(r["ts"], tz).strftime("%a %d %b %H:%M")
        body = (r["text"] or "").strip() or f"[{r['type']}]"
        if r["text"] and r["type"] not in ("text", "interactive", "button"):
            body = f"[{r['type']}] {body}"
        lines.append(f"{when} {who}: {redact(body)[:1000]}")
    return "\n".join(lines)


class ClaudeClassifier:
    def __init__(self, api_key: str, store: Store, model: str = "claude-haiku-5-5", client=None, daily_cap: int = 3000):
        if client is None:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key, timeout=30.0, max_retries=2)
        self.client, self.store, self.model, self.daily_cap = client, store, model, daily_cap

    def classify(self, transcript: str, contact_kind: str) -> Verdict | None:
        import anthropic
        if over_daily_cap(self.store, self.daily_cap):
            return None
        user = (f"Jenis kontak menurut data kami: {contact_kind}\n\n"
                f"<chat>\n{transcript}\n</chat>\n\nApakah Heroez masih perlu membalas?")
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=400,
                # A short classification: no extended thinking, low effort, cached system prompt.
                thinking={"type": "disabled"},
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                cache_control={"type": "ephemeral"},
                system=PROMPT,
                messages=[{"role": "user", "content": user}],
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            log.error("Claude API key rejected; AI check skipped")
            self._meter_error("auth")
            return None
        except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.APIStatusError) as e:
            log.warning("Claude call failed (%s); using text rules", type(e).__name__)
            self._meter_error("api")
            return None
        self._meter(resp.usage)
        if resp.stop_reason != "end_turn":
            log.warning("Claude stop_reason=%s; using text rules", resp.stop_reason)
            return None
        try:
            data = json.loads(next(b.text for b in resp.content if b.type == "text"))
            return Verdict(kind=data["kind"], needs_reply=bool(data["needs_reply"]), priority=data["priority"],
                           summary=redact(data["summary_id"].strip())[:200],
                           confidence=max(0.0, min(1.0, float(data["confidence"]))),
                           deal_signal=str(data.get("deal_signal") or "none"),
                           deal_value=max(0, int(data.get("deal_value_idr") or 0)))
        except (StopIteration, ValueError, KeyError, TypeError):
            log.warning("Claude returned unexpected output; using text rules")
            return None

    # -- metering ---------------------------------------------------------
    def _meter(self, usage) -> None:
        day = dt.date.today().isoformat()
        inp = usage.input_tokens or 0
        out = usage.output_tokens or 0
        cread = getattr(usage, "cache_read_input_tokens", 0) or 0
        cwrite = getattr(usage, "cache_creation_input_tokens", 0) or 0
        pin, pout, pcache = PRICES.get(self.model, (0, 0, 0))
        usd = (inp * pin + cwrite * pin * 1.25 + cread * pcache + out * pout) / 1_000_000
        self.store.x(
            "INSERT INTO ai_usage(day,model,calls,input_tokens,output_tokens,cache_read_tokens,usd) VALUES(?,?,1,?,?,?,?) "
            "ON CONFLICT(day,model) DO UPDATE SET calls=calls+1, input_tokens=input_tokens+excluded.input_tokens, "
            "output_tokens=output_tokens+excluded.output_tokens, cache_read_tokens=cache_read_tokens+excluded.cache_read_tokens, "
            "usd=usd+excluded.usd",
            (day, self.model, inp + cwrite, out, cread, usd))

    def _meter_error(self, kind: str) -> None:
        self.store.set_kv("ai_last_error", json.dumps({"at": int(time.time()), "kind": kind}))


def calls_today(store: Store) -> int:
    row = store.one("SELECT COALESCE(SUM(calls),0) c FROM ai_usage WHERE day=?", (dt.date.today().isoformat(),))
    return int(row["c"])


def over_daily_cap(store: Store, cap: int) -> bool:
    if cap and calls_today(store) >= cap:
        if store.get_kv("ai_cap_warned") != dt.date.today().isoformat():
            store.set_kv("ai_cap_warned", dt.date.today().isoformat())
            log.warning("AI daily call cap (%d) reached; falling back to rules for the rest of the day", cap)
        return True
    return False


def month_cost(store: Store, today: dt.date | None = None) -> tuple[int, float]:
    """(calls, usd) for the current calendar month."""
    today = today or dt.date.today()
    row = store.one("SELECT COALESCE(SUM(calls),0) c, COALESCE(SUM(usd),0) u FROM ai_usage WHERE day LIKE ?",
                    (today.strftime("%Y-%m") + "%",))
    return int(row["c"]), float(row["u"])
