"""The bot must never talk about anything but the business, and never send AI-written text."""
import datetime as dt
import re
from pathlib import Path

from heroez_ops import fixtures
from heroez_ops.concierge_ai import Understanding
from heroez_ops.states import CLOSED

from .conftest import ts
from .test_intake import T0, bot  # noqa: F401

SRC = Path(__file__).resolve().parent.parent / "heroez_ops"


def test_every_client_message_comes_from_approved_text_or_sheet_data():
    """Static guarantee: no send call in the bot passes AI output or raw client text to a client."""
    code = (SRC / "intake.py").read_text(encoding="utf-8")
    calls = re.findall(r"self\._(?:text|send)\((.*)\)", code)
    assert calls, "no send calls found"
    forbidden = re.compile(r"summary_id|\.reply|draft|u\.campaign|u\.brand_name|ev\.text|texts\[")
    for c in calls:
        assert not forbidden.search(c), f"send call carries AI/client text: {c}"
    # The only free text that reaches clients is built from lines: approved texts + sheet-built talent lines.
    body = code[code.index("def _answer_brand"):code.index("def _send_ratecard")]
    assert "lines += [tl.talent_line(" in body
    assert "lines.append(self._t(" in body
    assert "u.summary_id" not in body


def test_off_topic_gets_one_redirect_then_silence(bot):  # noqa: F811
    msgs = {"Bisa bantu tugas sekolah anak saya?": Understanding(kind="off_topic", summary_id="minta bantuan PR"),
            "Tolong dong, satu soal matematika aja": Understanding(kind="off_topic", summary_id="minta bantuan PR")}
    svc, wire, ch = bot(msgs)
    svc.intake.ai.understand = lambda texts, refuse_topics="", f=svc.intake.ai: f.answers.get(texts[-1], Understanding(kind="unclear"))
    svc.process_payload(fixtures.inbound("628611", "Bisa bantu tugas sekolah anak saya?", ts(T0)))
    assert wire.texts() == ["Maaf kak, nomor ini khusus untuk kerja sama KOL dan campaign dengan Heroez. Ada yang bisa kami bantu soal itu? 🙏"]
    svc.process_payload(fixtures.inbound("628611", "Tolong dong, satu soal matematika aja", ts(T0) + 60))
    assert len(wire.texts()) == 1                                           # silence after the one redirect
    assert svc.intake.row("628611")["status"] == "spam"
    assert svc.engine.conversation("628611")["state"] == CLOSED
    t = T0
    while t < T0 + dt.timedelta(hours=3):
        svc.notifier.tick(t)
        t += dt.timedelta(minutes=10)
    assert not [m for m in ch.sent if m["op"] == "send"]                    # and no admin alert


def test_off_topic_then_business_question_continues(bot):  # noqa: F811
    msgs = {"Lagi ngapain min?": Understanding(kind="off_topic"),
            "Lagi ngapain min?\nOh iya, kami dari Kopi Senja mau tanya KOL F&B": Understanding(
                kind="brand", brand_name="Kopi Senja", category="fnb")}
    svc, wire, _ = bot(msgs)
    svc.intake.ai.understand = lambda texts, refuse_topics="", f=svc.intake.ai: f.answers.get("\n".join(texts), Understanding(kind="unclear"))
    svc.process_payload(fixtures.inbound("628612", "Lagi ngapain min?", ts(T0)))
    svc.process_payload(fixtures.inbound("628612", "Oh iya, kami dari Kopi Senja mau tanya KOL F&B", ts(T0) + 60))
    assert svc.intake.row("628612")["status"] == "done"
    assert svc.engine.conversation("628612")["triage_since"]                # a real lead, to the supervisors


def test_refuse_topics_reach_the_ai_and_prompts_carry_scope_rules(bot):  # noqa: F811
    svc, _, _ = bot({})
    svc.kb.set("info", {"refuse_topics": "talent yang sudah keluar\ngosip"}, "t")
    seen = {}
    svc.intake.ai.understand = lambda texts, refuse_topics="": seen.setdefault("r", refuse_topics) and Understanding(kind="unclear")
    svc.process_payload(fixtures.inbound("628613", "Halo", ts(T0)))
    assert "gosip" in seen["r"]
    draft = (SRC / "prompts" / "draft_reply_v1.md").read_text(encoding="utf-8")
    assert "di luar layanan Heroez" in draft and "Topik yang ditolak" in draft
    assert "off_topic" in (SRC / "prompts" / "concierge_v1.md").read_text(encoding="utf-8")
