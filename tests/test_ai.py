import datetime as dt
import json
from types import SimpleNamespace

import pytest

from heroez_ops import fixtures
from heroez_ops.ai import ClaudeClassifier, Verdict, month_cost
from heroez_ops.alerts.telegram import ShadowAlertChannel
from heroez_ops.db import Store
from heroez_ops.service import Service
from heroez_ops.states import AWAITING_THEM, AWAITING_US, CLOSED

from .conftest import ts, wib


class FakeClassifier:
    """Answers from a dict keyed by the client's last line; records what it was shown."""

    def __init__(self, answers):
        self.answers, self.seen = answers, []

    def classify(self, transcript, contact_kind):
        self.seen.append(transcript)
        last = [line for line in transcript.splitlines() if " KLIEN: " in line][-1].split(" KLIEN: ", 1)[1]
        return self.answers.get(last)


def V(needs, kind="question", conf=0.9, prio="P2", summary="ringkasan"):
    return Verdict(kind=kind, needs_reply=needs, priority=prio, summary=summary, confidence=conf)


@pytest.fixture
def ai_service(cfg, tmp_path):
    def make(answers):
        ch = ShadowAlertChannel(tmp_path / "s.jsonl")
        svc = Service(cfg, Store(":memory:"), alert_channel=ch, reply_channel=ch, classifier=FakeClassifier(answers))
        return svc, ch
    return make


T0 = wib(2026, 11, 2, 10, 0)


def sends(ch):
    return [r for r in ch.sent if r["op"] == "send"]


def run(svc, start, minutes):
    for m in range(minutes + 1):
        t = start + dt.timedelta(minutes=m)
        svc.classify_pending(t)
        svc.notifier.tick(t)


def test_ai_closes_a_closing_message_the_rules_missed(ai_service):
    svc, ch = ai_service({"Baik kak, kami review internal dulu minggu ini 👍": V(False, "will_get_back", 0.92)})
    svc.process_payload(fixtures.inbound("628111", "Kak minta ratecard", ts(T0)))
    svc.process_payload(fixtures.echo("628111", "Ini kak ratecardnya", ts(T0) + 60))
    svc.process_payload(fixtures.inbound("628111", "Baik kak, kami review internal dulu minggu ini 👍", ts(T0) + 120))
    # Rules alone think this needs a reply ("review" is not an ack word)...
    assert svc.engine.conversation("628111")["state"] == AWAITING_US
    run(svc, T0, 60)
    # ...the AI closes it, and no reminder is sent.
    assert svc.engine.conversation("628111")["state"] == CLOSED
    assert sends(ch) == []


def test_ai_reopens_ok_ditunggu(ai_service):
    svc, ch = ai_service({"ok ditunggu ya kak": V(True, "waiting_on_us", 0.85, summary="Klien menunggu proposal dari kita")})
    svc.process_payload(fixtures.inbound("628111", "Kak boleh minta proposal?", ts(T0)))
    svc.process_payload(fixtures.echo("628111", "Siap kak, besok saya kirim ya", ts(T0) + 60))
    svc.process_payload(fixtures.inbound("628111", "ok ditunggu ya kak", ts(T0) + 120))
    assert svc.engine.conversation("628111")["state"] == CLOSED          # rules: pure ack
    run(svc, T0, 40)
    assert svc.engine.conversation("628111")["state"] == AWAITING_US     # AI: they're waiting on us
    [msg] = sends(ch)
    assert "🤖 Klien menunggu proposal dari kita" in msg["text"]


def test_low_confidence_closure_keeps_alerting(ai_service):
    svc, ch = ai_service({"hmm oke": V(False, "acknowledgement", 0.5)})
    svc.process_payload(fixtures.inbound("628111", "Kak rate TikTok berapa?", ts(T0)))
    svc.process_payload(fixtures.inbound("628111", "hmm oke", ts(T0) + 30))
    run(svc, T0, 31)
    assert svc.engine.conversation("628111")["state"] == AWAITING_US
    assert len(sends(ch)) == 1


def test_ai_failure_falls_back_to_rules_after_wait(ai_service):
    svc, ch = ai_service({})  # classifier returns None for everything
    svc.process_payload(fixtures.inbound("628111", "Halo kak mau tanya", ts(T0)))
    run(svc, T0, 30)
    assert svc.engine.conversation("628111")["ai_pending"] == 0
    assert len(sends(ch)) == 1


def test_stale_verdict_is_ignored_after_reply(ai_service):
    svc, _ = ai_service({})
    svc.process_payload(fixtures.inbound("628111", "Halo kak", ts(T0), msg_id="wamid.Q"))
    svc.process_payload(fixtures.echo("628111", "Halo!", ts(T0) + 60))
    svc.engine.apply_verdict("628111", "wamid.Q", V(True), 0.75, ts(T0) + 90)
    assert svc.engine.conversation("628111")["state"] == AWAITING_THEM


def test_ai_sets_priority_and_sees_masked_numbers(ai_service):
    text = "Brand X, budget 300jt, deadline 10 Nov. Rek 1234567890"
    svc, _ = ai_service({"Brand X, budget 300jt, deadline 10 Nov. Rek •••": V(True, "inquiry", 0.95, "P1")})
    svc.process_payload(fixtures.inbound("628111", text, ts(T0)))
    svc.classify_pending(T0)
    assert svc.engine.conversation("628111")["priority"] == "P1"
    assert "1234567890" not in svc.classifier.seen[0]


def test_stickers_are_not_sent_to_ai(ai_service):
    svc, _ = ai_service({})
    svc.process_payload(fixtures.inbound("628111", "", ts(T0), msg_type="sticker"))
    assert svc.engine.conversation("628111")["ai_pending"] == 0


# -- the real Claude adapter, with a stubbed SDK client ---------------------

class StubMessages:
    def __init__(self, payload, stop="end_turn"):
        self.payload, self.stop, self.kwargs = payload, stop, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            stop_reason=self.stop,
            content=[SimpleNamespace(type="text", text=json.dumps(self.payload))],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=60, cache_read_input_tokens=800,
                                  cache_creation_input_tokens=0))


def test_claude_adapter_request_and_metering():
    store = Store(":memory:")
    msgs = StubMessages({"kind": "question", "needs_reply": True, "priority": "P2",
                         "summary_id": "Tanya rate TikTok", "confidence": 0.93})
    clf = ClaudeClassifier("key", store, client=SimpleNamespace(messages=msgs))
    v = clf.classify("Mon 02 Nov 10:00 KLIEN: berapa rate tiktok?", "kontak baru")
    assert v == Verdict("question", True, "P2", "Tanya rate TikTok", 0.93)
    kw = msgs.kwargs
    assert kw["model"] == "claude-haiku-5-5"
    assert kw["thinking"] == {"type": "disabled"}
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in kw
    calls, usd = month_cost(store)
    assert calls == 1 and 0 < usd < 0.001


def test_claude_adapter_refusal_returns_none():
    store = Store(":memory:")
    clf = ClaudeClassifier("key", store, client=SimpleNamespace(messages=StubMessages({}, stop="refusal")))
    assert clf.classify("x KLIEN: y", "kontak baru") is None
