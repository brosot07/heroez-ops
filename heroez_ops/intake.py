"""Automated first conversation with NEW contacts, before an admin takes over.

* spam: no reply, no alert; listed daily with a "Bukan spam" button
* brands: ask what they need and which KOLs; answer each named KOL's rates and
  availability (from the KOL list and calendar, never written by the AI), or suggest
  matching KOLs / send the rate card; then hand over to an admin
* talents and others: collect a short description, then hand over
* typed FAQ questions: the approved answer, word for word

Starts only for a new unknown contact (not a talent, not an existing client), on the first
message or the first after 30+ days of silence, and only if no human replied in 24 h.
Stops for good the moment an admin reply is seen. At most two questions before handover.
"""
from __future__ import annotations

import datetime as dt
import re
import json
import logging
import time
from pathlib import Path

from . import talents as tl
from .concierge_ai import ConciergeAI, Understanding
from .config import Config
from .db import Store
from .kb import KB
from .sources.base import MessageEvent
from .sources.meta_send import MetaSender, SendError, month_sends
from .states import Engine

log = logging.getLogger(__name__)

WHO = [("brand", "Brand / Agency"), ("talent", "Talent / KOL"), ("other", "Lainnya")]
WHO_EN = [("brand", "Brand / Agency"), ("talent", "Talent / KOL"), ("other", "Other")]
SILENCE_RESTART = 30 * 86400
MAX_QUESTIONS = 2
# "Sure" spam needs the AI verdict plus one of these (or a suspicious link); otherwise the sender gets the menu.
SURE_SPAM = re.compile(r"(pinjol|pinjaman|kredit|cicilan|crypto|kripto|bitcoin|trading|judi|slot|togel|casino|kasino|"
                       r"undian|menang(kan)?|hadiah|lucky|jackpot|deposit|withdraw|saldo|voucher|promo|diskon|cashback|"
                       r"gratis|free|klik|click|daftar sekarang|register now|limited|terbatas|unsubscribe|bc\b|broadcast)", re.I)
EN_TEXTS = {
    "greeting": "Hi, thank you for contacting Heroez 🙌\nMay we know who you are?",
    "brand_ask": ("Hi, thank you for contacting Heroez 🙌\nCould you tell us your brand/agency, the campaign you have in "
                  "mind, and any KOLs you're interested in? Please include the platform and campaign dates."),
    "brand_ask_dates": "Great! What are the campaign dates? Then we can check the talents' availability 🙏",
    "talent_list_intro": "Here are the talents you asked about:",
    "recommend_intro": "Here are some of our talents that fit:",
    "not_in_list": "For {names}, our admin will check and get back to you.",
    "alternatives_intro": "As alternatives available on those dates:",
    "price_note": "Prices are per content, excluding tax.",
    "ratecard_caption": "Here is our package rate card 🙏",
    "handover": "Our admin will continue the discussion within {sla} 🙏",
    "handover_offhours": "Our admin will continue the discussion during the next working hours 🙏",
    "nano_handover": "For nano/micro KOL needs we have dedicated packages; our supervisor will propose one 🙏",
    "ask_company": "Could you tell us your brand/agency name? We'll send detailed rates after that 🙏",
    "off_topic": "Sorry, this number is only for KOL and campaign collaborations with Heroez. Anything we can help with on that? 🙏",
    "talent_prompt": "Hi! Please send your handle (IG/TikTok/YouTube), main platform and follower count 🙏",
    "talent_done": "Thank you! Our talent team will contact you 🙏",
    "other_prompt": "Sure, please tell us briefly what you need. Our team will reply as soon as possible 🙏",
    "other_done": "Thank you, we have received your message. Our team will reply within {sla} 🙏",
}


def reply_id(ev: MessageEvent) -> str | None:
    if ev.type != "interactive":
        return None
    i = ev.raw.get("interactive") or {}
    return ((i.get("button_reply") or i.get("list_reply") or {}).get("id")) or None


class Intake:
    def __init__(self, cfg: Config, store: Store, engine: Engine, kb: KB, sender: MetaSender, files_dir: Path,
                 ai: ConciergeAI | None = None):
        self.cfg, self.store, self.engine, self.kb, self.sender = cfg, store, engine, kb, sender
        self.files_dir, self.ai = files_dir, ai
        self.now = int(time.time())
        from .bizhours import BusinessClock
        self.clock = BusinessClock(cfg)

    # -- entry points -------------------------------------------------------------
    def on_event(self, ev: MessageEvent, now: int | None = None) -> None:
        if ev.source == "history":
            return
        now = now or max(ev.ts, 0) or int(time.time())
        self.now = now
        row = self.row(ev.wa_id)
        if ev.direction == "out":
            if row and row["status"] == "active" and ev.source in ("echo", "api"):
                self._set(ev.wa_id, status="stopped", outcome="admin membalas sendiri")
            return
        if row and row["status"] == "active":
            self._advance(row, ev, now)
        elif row and row["status"] == "spam" and ev.text and self.cfg.intake_enabled:
            # Second message after a spam verdict: look again instead of staying silent forever.
            self._set(ev.wa_id, status="active", step="new", data={"texts": [ev.text[:1500]], "asked": 0, "was_spam": True})
            self._understand_and_act(self.row(ev.wa_id), ev)
        elif self.cfg.intake_enabled and self._should_start(ev):
            self._start(ev, now)
        elif self.cfg.faq_auto_send and self._may_auto_answer(ev):
            faq = self.kb.match_faq(ev.text)
            if faq:
                self._send_faq(ev.wa_id, faq, "id")

    def expire(self, now: int | None = None) -> int:
        """The client stopped answering: a human takes over."""
        now = now or int(time.time())
        self.now = now
        rows = self.store.q("SELECT * FROM intake WHERE status='active' AND updated_at<?",
                            (now - self.cfg.intake_timeout_minutes * 60,))
        for r in rows:
            data = json.loads(r["data"])
            if data.get("redirected") and len(data.get("texts", [])) <= 1:
                self._set(r["wa_id"], status="spam", outcome="di luar topik (tidak melanjutkan)")
                self.engine.mark_spam(r["wa_id"], "di luar topik", now)
                continue
            if data.get("maybe_spam") and len(data.get("texts", [])) <= 1:
                # Looked like spam, got the menu, never answered: treat as spam (daily card, rescue button).
                self._set(r["wa_id"], status="spam", outcome=data.get("spam_summary") or "diduga spam (tidak merespons menu)")
                self.engine.mark_spam(r["wa_id"], data.get("spam_summary", ""), now)
                continue
            self._handoff(r, "klien tidak melanjutkan percakapan")
        return len(rows)

    def not_spam(self, wa_id: str, who: str) -> bool:
        r = self.row(wa_id)
        if not r or r["status"] != "spam":
            return False
        self.store.x("INSERT INTO feedback(wa_id,kind,admin,state_before,at) VALUES(?,?,?,?,?)",
                     (wa_id, "not_spam", who, "CLOSED", self.now))
        self._handoff(r, f"ditandai bukan spam oleh {who}")
        return True

    def row(self, wa_id: str):
        return self.store.one("SELECT * FROM intake WHERE wa_id=?", (wa_id,))

    # -- decisions ------------------------------------------------------------------
    def _human_replied_within(self, wa_id: str, seconds: int, before: int) -> bool:
        return bool(self.store.one(
            "SELECT 1 FROM messages WHERE wa_id=? AND direction='out' AND source IN ('echo','api','history') AND ts>?",
            (wa_id, before - seconds)))

    def _should_start(self, ev: MessageEvent) -> bool:
        c = self.store.one("SELECT kind FROM contacts WHERE wa_id=?", (ev.wa_id,))
        if not c or c["kind"] != "new":
            return False                          # talents and existing clients never get the bot
        prev = self.store.one("SELECT MAX(ts) t FROM messages WHERE wa_id=? AND id!=?", (ev.wa_id, ev.msg_id))
        if prev and prev["t"] and prev["t"] > ev.ts - SILENCE_RESTART:
            return False
        return not self._human_replied_within(ev.wa_id, 86400, ev.ts)

    def _may_auto_answer(self, ev: MessageEvent) -> bool:
        c = self.store.one("SELECT kind FROM contacts WHERE wa_id=?", (ev.wa_id,))
        return bool(c and c["kind"] != "talent" and ev.type == "text"
                    and not self._human_replied_within(ev.wa_id, 86400, ev.ts) and self._budget_ok())

    def _budget_ok(self) -> bool:
        return month_sends(self.store) < self.cfg.monthly_send_budget

    # -- sending ----------------------------------------------------------------------
    def _t(self, key: str, lang: str, **kw) -> str:
        if key == "handover" and not self.clock.is_open(dt.datetime.fromtimestamp(self.now, dt.timezone.utc)):
            key = "handover_offhours"       # no "within 1 working hour" promise at 23:00 or on a holiday
        t = EN_TEXTS[key] if lang == "en" else self.kb.text(key)
        t = t.replace("{sla}", self.cfg.sla_text)
        for k, v in kw.items():
            t = t.replace("{" + k + "}", v)
        return t

    def _send(self, fn, wa: str, *args, purpose: str = "intake", body: str = "") -> bool:
        if not self._budget_ok():
            log.warning("monthly send budget reached; bot message not sent")
            return False
        try:
            mid = fn(wa, *args, purpose=purpose)
        except SendError as e:
            log.error("bot send failed: %s", e)
            return False
        if mid and self.sender.live:
            self.engine.record_bot_message(wa, mid, body, self.now)
        return True

    def _text(self, wa: str, body: str, purpose: str = "intake") -> bool:
        return self._send(self.sender.text, wa, body, purpose=purpose, body=body)

    # -- state ------------------------------------------------------------------------
    def _set(self, wa: str, **cols) -> None:
        cols["updated_at"] = self.now
        if "data" in cols and not isinstance(cols["data"], str):
            cols["data"] = json.dumps(cols["data"], ensure_ascii=False)
        sets = ", ".join(f"{k}=?" for k in cols)
        self.store.x(f"UPDATE intake SET {sets} WHERE wa_id=?", (*cols.values(), wa))

    def _start(self, ev: MessageEvent, now: int) -> None:
        bad = self.store.q("SELECT shown, level, reasons FROM message_links WHERE msg_id=? AND level IN ('suspicious','malicious')",
                           (ev.msg_id,))
        self.store.x("INSERT INTO intake(wa_id,status,step,lang,data,first_msg_ts,started_at,updated_at) VALUES(?,?,?,?,?,?,?,?) "
                     "ON CONFLICT(wa_id) DO UPDATE SET status=excluded.status, step=excluded.step, lang=excluded.lang, "
                     "data=excluded.data, first_msg_ts=excluded.first_msg_ts, started_at=excluded.started_at, "
                     "updated_at=excluded.updated_at, outcome=NULL",
                     (ev.wa_id, "active", "new", "id", json.dumps({"texts": [ev.text[:1500]], "asked": 0}), ev.ts, now, now))
        self.store.audit("bot", "intake_start", ev.wa_id, None, now)
        if bad:
            # A first message with a phishing-looking link: never engage; list it with the spam for a human to check.
            reason = f"link mencurigakan {bad[0]['shown']}"
            self._set(ev.wa_id, status="spam", outcome=reason)
            self.engine.mark_spam(ev.wa_id, reason, now)
            return
        self._understand_and_act(self.row(ev.wa_id), ev)

    def _advance(self, row, ev: MessageEvent, now: int) -> None:
        data = json.loads(row["data"])
        rid = reply_id(ev)
        if row["step"] == "who" and rid in ("brand", "talent", "other"):
            data["u"] = {**data.get("u", {}), "kind": rid}
            self._set(ev.wa_id, data=data)
            self._act(self.row(ev.wa_id), Understanding(**data["u"]))
            return
        if row["step"] in ("talent_info", "other_msg"):
            data["detail"] = ev.text[:500]
            self._set(ev.wa_id, data=data)
            self._text(ev.wa_id, self._t("talent_done" if row["step"] == "talent_info" else "other_done", row["lang"]))
            self._handoff(self.row(ev.wa_id), "data diterima")
            return
        if ev.text:
            data.setdefault("texts", []).append(ev.text[:1500])
            self._set(ev.wa_id, data=data)
        faq = self.kb.match_faq(ev.text) if self.cfg.faq_auto_send else None
        if faq:
            self._send_faq(ev.wa_id, faq, row["lang"])
            self._handoff(self.row(ev.wa_id), "pertanyaan FAQ dijawab")
            return
        self._understand_and_act(self.row(ev.wa_id), ev)

    def _understand_and_act(self, row, ev: MessageEvent) -> None:
        data = json.loads(row["data"])
        u = None
        if self.ai:
            got = self.ai.understand(data.get("texts", []) or [ev.text], refuse_topics=self.kb.info("refuse_topics"))
            if got:
                u = Understanding(**data["u"]).merge(got) if data.get("u") else got
        if u is None:                              # AI off or failed: simple button menu
            u = Understanding(**data.get("u", {})) if data.get("u") else Understanding()
            if row["step"] != "new" and u.kind == "unclear":
                self._handoff(row, "pesan diketik, AI tidak tersedia")
                return
        data["u"] = u.__dict__
        self._set(row["wa_id"], data=data, lang=u.lang)
        self._act(self.row(row["wa_id"]), u)

    def _act(self, row, u: Understanding) -> None:
        wa, lang = row["wa_id"], u.lang
        data = json.loads(row["data"])
        asked = data.get("asked", 0)

        def ask(text_key: str, step: str) -> None:
            data["asked"] = asked + 1
            self._set(wa, data=data, step=step)
            self._text(wa, self._t(text_key, lang))

        if u.kind == "spam":
            texts = " ".join(data.get("texts", []))
            links_bad = self.store.one("SELECT 1 FROM message_links WHERE wa_id=? AND level IN ('suspicious','malicious')", (wa,))
            sure = bool(links_bad or SURE_SPAM.search(texts) or data.get("was_spam"))
            if sure or asked >= MAX_QUESTIONS:
                self._set(wa, status="spam", outcome=u.summary_id or "diduga spam")
                self.engine.mark_spam(wa, u.summary_id, self.now)
                self.store.audit("bot", "spam", wa, {"summary": u.summary_id, "sure": sure})
                return
            # Maybe spam: send the menu once. A real person taps or writes again; spammers don't.
            data["maybe_spam"], data["spam_summary"] = True, u.summary_id
            data["asked"] = asked + 1
            self._set(wa, data=data, step="who")
            text = self._t("greeting", lang)
            self._send(self.sender.buttons, wa, text, WHO_EN if lang == "en" else WHO, body=text)
            return
        if u.kind == "off_topic":
            # Not about the business: one fixed redirect, then silence (listed on the daily card if it goes on).
            if data.get("redirected"):
                self._set(wa, status="spam", outcome="di luar topik (sudah diarahkan sekali)")
                self.engine.mark_spam(wa, u.summary_id or "di luar topik", self.now)
                return
            data["redirected"] = True
            self._set(wa, data=data, step="new")
            self._text(wa, self._t("off_topic", lang))
            self.store.audit("bot", "off_topic_redirect", wa, {"summary": u.summary_id})
            return
        if u.kind == "unclear":
            if asked >= MAX_QUESTIONS:
                self._handoff(row, "belum jelas")
                return
            data["asked"] = asked + 1
            self._set(wa, data=data, step="who")
            text = self._t("greeting", lang)
            self._send(self.sender.buttons, wa, text, WHO_EN if lang == "en" else WHO, body=text)
            return
        if u.kind == "talent":
            ask("talent_prompt", "talent_info")
            return
        if u.kind == "other":
            ask("other_prompt", "other_msg")
            return
        # brand
        if u.needs_human or u.asks_specific_time:
            # Hour-level schedules and anything beyond rates/availability: a person answers.
            self._text(wa, self._t("handover", lang))
            self._handoff(self.row(wa), "minta jam tertentu" if u.asks_specific_time else "perlu admin")
            return
        knows_need = bool(u.talents or u.category not in ("unknown", "other") or u.campaign)
        if not knows_need and asked < MAX_QUESTIONS:
            ask("brand_ask", "brand")
            return
        if not u.talents:
            # No KOL named / asking for a solution: supervisors decide who handles it.
            self._text(wa, self._t("handover", lang))
            self._handoff(self.row(wa), "lead tanpa talent tertentu", triage=True)
            return
        if u.talents and not u.dates() and asked < MAX_QUESTIONS and not data.get("asked_dates"):
            data["asked_dates"] = True
            ask("brand_ask_dates", "brand_dates")
            return
        self._answer_brand(self.row(wa), u)

    def _answer_brand(self, row, u: Understanding) -> None:
        wa, lang = row["wa_id"], u.lang
        dates = u.dates()
        lines: list[str] = []
        found, missing = tl.find_talents(self.store, u.talents) if u.talents else ([], [])
        alternatives = []
        qualified = bool(u.brand_name.strip())          # exact rates only for an identified brand/agency
        if any(tl.ttype(r) == "nano" for r in found):
            # Nano KOL work is packaged and priced by people; never listed individually.
            self._text(wa, self._t("nano_handover", lang))
            self._handoff(self.row(wa), "minta nano KOL", triage=True)
            return
        if found and not qualified and any(json.loads(r["data"])["rates"] for r in found) \
                and not json.loads(row["data"]).get("asked_company") and json.loads(row["data"]).get("asked", 0) < MAX_QUESTIONS:
            data = json.loads(row["data"])
            data["asked_company"], data["asked"] = True, data.get("asked", 0) + 1
            self._set(wa, data=data, step="brand_company")
            self._text(wa, self._t("ask_company", lang))
            return
        if found:
            lines.append(self._t("talent_list_intro", lang))
            lines += [tl.talent_line(self.store, r, u.platforms, dates, lang, qualified) for r in found]
            if dates:
                busy = [r for r in found if not tl.available(self.store, r["id"], *dates)]
                if busy:
                    alternatives = tl.alternatives(self.store, busy, u.category, dates, exclude={r["id"] for r in found})
            if alternatives:
                lines.append(self._t("alternatives_intro", lang))
                lines += [tl.talent_line(self.store, r, u.platforms, dates, lang, qualified) for r in alternatives]
        if missing:
            lines.append(self._t("not_in_list", lang, names=", ".join(missing)))
        if not found:
            recs = tl.recommend(self.store, u.category, u.platforms, dates)
            if recs:
                lines.append(self._t("recommend_intro", lang))
                lines += [tl.talent_line(self.store, r, u.platforms, dates, lang, qualified) for r in recs]
        if any("Rp" in line for line in lines):
            lines.append(self._t("price_note", lang))
        else:
            self._send_ratecard(wa, lang)
        lines.append(self._t("handover", lang))
        self._text(wa, "\n".join(lines))
        data = json.loads(row["data"])
        data["answered"] = {"found": [r["name"] for r in found], "missing": missing,
                            "alternatives": [r["name"] for r in alternatives]}
        self._set(wa, data=data)
        self._handoff(self.row(wa), "info talent dikirim" if found else "dijawab bot")

    def _send_ratecard(self, wa: str, lang: str) -> None:
        pdf = self.files_dir / "ratecard.pdf"
        if not pdf.exists():
            return
        media = self.kb.get("ratecard_media") or {}
        if self.sender.live and (not media.get("id") or media.get("uploaded_at", 0) < time.time() - 25 * 86400):
            try:
                media = {"id": self.sender.upload_media(pdf.read_bytes(), "Rate card Heroez.pdf"), "uploaded_at": int(time.time())}
                self.kb.set("ratecard_media", media, "bot")
            except SendError as e:
                log.error("rate card upload failed: %s", e)
                return
        caption = self._t("ratecard_caption", lang)
        self._send(self.sender.document, wa, media.get("id") or "shadow-media", "Rate card Heroez.pdf", caption, body=caption)

    def _send_faq(self, wa: str, faq: dict, lang: str) -> None:
        answer = (faq.get("answer_en") if lang == "en" and faq.get("answer_en") else faq.get("answer_id")) or ""
        if answer and self._text(wa, answer, purpose="faq"):
            self.store.audit("bot", "faq_answer", wa, {"faq": faq.get("id")})
            if self.sender.live and not self.row(wa):
                self.engine.mark_answered_by_bot(wa, f"FAQ dijawab otomatis: {faq['questions'][0][:60]}", self.now)

    def _handoff(self, row, outcome: str, triage: bool = False) -> None:
        self._set(row["wa_id"], status="done", outcome=outcome)
        self.store.audit("bot", "intake_done", row["wa_id"], {"outcome": outcome, "triage": triage})
        data = json.loads(row["data"])
        summary, priority = self.summary(data)
        preferred = None
        if not triage and data.get("u", {}).get("talents"):
            # The handler of the first KOL the brand named leads the conversation.
            found, _ = tl.find_talents(self.store, data["u"]["talents"][:1])
            if found:
                h = self.store.one("SELECT handler FROM talents WHERE id=?", (found[0]["id"],))
                preferred = (h["handler"] or None) if h else None
        self.engine.handoff(row["wa_id"], f"{summary} ({outcome})", priority, row["first_msg_ts"], self.now,
                            preferred=preferred, triage=triage)

    @staticmethod
    def summary(data: dict) -> tuple[str, str]:
        u = Understanding(**data["u"]) if data.get("u") else Understanding()
        if u.kind == "brand":
            parts = [u.brand_name or "brand", u.campaign]
            if data.get("answered", {}).get("found"):
                parts.append("talent: " + ", ".join(data["answered"]["found"]))
            if data.get("answered", {}).get("alternatives"):
                parts.append("alternatif: " + ", ".join(data["answered"]["alternatives"]))
            elif u.talents:
                parts.append("tanya: " + ", ".join(u.talents))
            d = u.dates()
            if d:
                parts.append(tl._range(*d))
            if u.budget_idr:
                parts.append(f"budget Rp{u.budget_idr:,}".replace(",", "."))
            soon = d and (d[0] - dt.date.today()).days <= 14
            prio = "P1" if soon or u.budget_idr >= 100_000_000 or u.needs_human else "P2"
            return "Lead brand: " + " · ".join(p for p in parts if p), prio
        if u.kind == "talent":
            return f"Lamaran talent: {data.get('detail', '-')[:80]}", "P3"
        if u.kind == "other":
            return f"Lainnya: {data.get('detail', u.summary_id)[:80]}", "P2"
        return f"Kontak baru: {(u.summary_id or ' / '.join(data.get('texts', [])))[:80]}", "P2"
