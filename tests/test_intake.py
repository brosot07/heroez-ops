import dataclasses
import datetime as dt
import json

import httpx
import pytest

from heroez_ops import fixtures, talents
from heroez_ops.alerts.telegram import ShadowAlertChannel
from heroez_ops.concierge_ai import Understanding
from heroez_ops.db import Store
from heroez_ops.service import Service
from heroez_ops.sources.meta_send import MetaSender
from heroez_ops.states import AWAITING_THEM, AWAITING_US, CLOSED

from .conftest import ts, wib

T0 = wib(2026, 11, 2, 10, 0)

ROSTER = [
    {"Nama": "Dewi Lestari", "Handle IG": "@dewi.lestari", "Followers IG": "120rb", "Niche": "beauty, skincare",
     "Rate IG Reels": "15.000.000", "Rate TikTok": "12jt", "No HP": "0812-1111-2222", "Handler": "budi",
     "Catatan internal": "minta DP 50%"},
    {"Nama": "Raka Pratama", "Handle TikTok": "@rakagaming", "Followers TikTok": "1,2M", "Niche": "gaming",
     "Rate TikTok": "20jt", "No HP": "0813-3333-4444", "Handler": "rina"},
    {"Nama": "Sinta", "Handle IG": "@sinta.glow", "Followers IG": "300K", "Niche": "beauty", "Rate IG Reels": "25jt"},
]
BUSY = [{"Nama talent": "Dewi Lestari", "Mulai": "2026-11-10", "Selesai": "2026-11-12", "Brand": "RahasiaCorp"}]


class FakeAI:
    """Stands in for Claude: maps a joined transcript to an Understanding."""
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def understand(self, texts):
        self.calls.append(list(texts))
        return self.answers.get(texts[-1], Understanding(kind="unclear"))


class Wire:
    """Fake Meta Cloud API: records every message the bot sends."""
    def __init__(self):
        self.sent, self.n = [], 0

    def handler(self, request: httpx.Request):
        if request.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "MEDIA1"})
        self.n += 1
        self.sent.append(json.loads(request.content))
        return httpx.Response(200, json={"messages": [{"id": f"wamid.BOT{self.n}"}]})

    def texts(self):
        return [m.get("text", {}).get("body") or m.get("interactive", {}).get("body", {}).get("text")
                or m.get("document", {}).get("caption") for m in self.sent]


@pytest.fixture
def bot(cfg, tmp_path):
    def make(answers, **cfg_over):
        c = dataclasses.replace(cfg, **{"mode": "live", "intake_enabled": True, "faq_auto_send": True,
                                        "monthly_send_budget": 1000, **cfg_over})
        wire = Wire()
        store = Store(":memory:")
        sender = MetaSender(store, "TOKEN", "PNID", live=True, client=httpx.Client(transport=httpx.MockTransport(wire.handler)))
        ch = ShadowAlertChannel(tmp_path / "s.jsonl")
        svc = Service(c, store, alert_channel=ch, reply_channel=ch, sender=sender, data_dir=tmp_path)
        svc.intake.ai = FakeAI(answers)
        talents.import_roster(store, ROSTER, "test")
        talents.import_availability(store, BUSY, "test")
        svc.engine.load_roster()
        return svc, wire, ch
    return make


def conv(svc, wa):
    return svc.engine.conversation(wa)


def test_brand_naming_kols_with_dates_gets_rates_and_availability(bot):
    first = "Halo, kami dari Glowskin mau campaign TikTok & IG tgl 10-12 Nov, minat Dewi Lestari dan Sinta"
    svc, wire, _ = bot({first: Understanding(kind="brand", brand_name="Glowskin", campaign="launch serum",
                                             talents=["Dewi Lestari", "@sinta.glow"], category="beauty",
                                             platforms=["instagram"], date_start="2026-11-10", date_end="2026-11-12")})
    svc.process_payload(fixtures.inbound("628111", first, ts(T0), name="Glowskin"))
    [reply] = wire.texts()
    assert "*Dewi Lestari* (@dewi.lestari): IG Reels Rp15.000.000 — ❌ tidak tersedia di tanggal tersebut" in reply
    assert "*Sinta* (@sinta.glow): IG Reels Rp25.000.000 — ✅ tersedia 10–12 November 2026" in reply
    assert "RahasiaCorp" not in reply and "0812" not in reply and "DP 50%" not in reply
    assert "Admin kami akan melanjutkan diskusinya dalam 1 jam kerja 🙏" in reply
    c = conv(svc, "628111")
    assert c["state"] == AWAITING_US and c["priority"] == "P1"           # campaign within 14 days
    assert "Glowskin" in c["ai_summary"] and "Dewi Lestari" in c["ai_summary"]


def test_vague_brand_is_asked_then_asked_for_dates_then_answered(bot):
    svc, wire, _ = bot({
        "Halo kak": Understanding(kind="unclear"),
        "Halo kak\nKami brand kopi, mau tanya Raka bisa?": Understanding(kind="brand", brand_name="Kopi X", talents=["Raka"]),
        "Halo kak\nKami brand kopi, mau tanya Raka bisa?\nAwal Desember, 1-5 Des": Understanding(
            kind="brand", date_start="2026-12-01", date_end="2026-12-05", platforms=["tiktok"]),
    })
    svc.intake.ai.understand = lambda texts, f=svc.intake.ai: f.answers.get("\n".join(texts), Understanding(kind="unclear"))
    svc.process_payload(fixtures.inbound("628222", "Halo kak", ts(T0)))
    assert "sebagai apa" in wire.texts()[-1]                              # unclear -> who-are-you buttons
    svc.process_payload(fixtures.inbound("628222", "Kami brand kopi, mau tanya Raka bisa?", ts(T0) + 60))
    assert "tanggal berapa" in wire.texts()[-1]                           # named a KOL, no dates
    assert conv(svc, "628222")["state"] != AWAITING_US or svc.intake.row("628222")["status"] == "active"
    svc.process_payload(fixtures.inbound("628222", "Awal Desember, 1-5 Des", ts(T0) + 120))
    assert "*Raka Pratama* (@rakagaming): TikTok Rp20.000.000 — ✅ tersedia 1–5 Desember 2026" in wire.texts()[-1]
    assert svc.intake.row("628222")["status"] == "done"
    assert conv(svc, "628222")["state"] == AWAITING_US


def test_category_only_gets_recommendations_by_followers(bot):
    msg = "Mau cari KOL beauty buat IG"
    svc, wire, _ = bot({msg: Understanding(kind="brand", category="beauty", platforms=["instagram"])})
    svc.process_payload(fixtures.inbound("628333", msg, ts(T0)))
    reply = wire.texts()[-1]
    assert reply.index("Sinta") < reply.index("Dewi")                      # 300K before 120rb
    assert "Raka" not in reply


def test_spam_gets_no_reply_no_alert_then_not_spam_button(bot):
    msg = "PINJAMAN ONLINE CAIR 5 MENIT klik link"
    svc, wire, ch = bot({msg: Understanding(kind="spam", summary_id="promosi pinjol")})
    svc.process_payload(fixtures.inbound("628444", msg, ts(T0)))
    assert wire.sent == [] and conv(svc, "628444")["state"] == CLOSED
    t = T0
    while t < T0 + dt.timedelta(hours=3):
        svc.notifier.tick(t)
        t += dt.timedelta(minutes=10)
    assert not [m for m in ch.sent if m["op"] == "send"]
    svc.bot.handle_update({"message": {"from": {"id": 2001}, "chat": {"id": 2001}, "text": "/spam"}}, T0)
    assert any("Bukan spam" in str(m.get("buttons")) for m in ch.sent)
    svc.bot.handle_update({"callback_query": {"id": "c", "from": {"id": 2001}, "data": "notspam:628444"}}, T0)
    assert conv(svc, "628444")["state"] == AWAITING_US


def test_talents_and_existing_clients_never_get_the_bot(bot):
    svc, wire, _ = bot({})
    svc.process_payload(fixtures.inbound("6281211112222", "Kak, brief Glowskin sudah ada?", ts(T0)))   # Dewi (roster)
    svc.process_payload(fixtures.history({"628555": [("in", "Halo", ts(T0) - 86400 * 40), ("out", "Halo kak", ts(T0) - 86400 * 40)]}))
    svc.process_payload(fixtures.inbound("628555", "Kak mau repeat order campaign", ts(T0)))         # existing client
    assert wire.sent == []
    assert conv(svc, "6281211112222")["assignee"] == "budi"                # routed to the KOL's handler


def test_admin_reply_stops_bot_for_good(bot):
    svc, wire, _ = bot({"Halo": Understanding(kind="unclear")})
    svc.process_payload(fixtures.inbound("628666", "Halo", ts(T0)))
    assert len(wire.sent) == 1
    svc.process_payload(fixtures.echo("628666", "Halo kak, saya Rina dari Heroez", ts(T0) + 30))
    svc.process_payload(fixtures.inbound("628666", "Mau tanya rate Dewi", ts(T0) + 60))
    assert len(wire.sent) == 1
    assert svc.intake.row("628666")["status"] == "stopped"


def test_silent_client_is_handed_to_admin(bot):
    svc, wire, _ = bot({"Halo": Understanding(kind="unclear")})
    svc.process_payload(fixtures.inbound("628777", "Halo", ts(T0)))
    svc.intake.expire(ts(T0) + 16 * 60 + 10**9)
    assert svc.intake.row("628777")["status"] == "done"
    assert conv(svc, "628777")["state"] == AWAITING_US


def test_monthly_budget_stops_bot_messages(bot):
    msg = "Mau cari KOL beauty buat IG"
    svc, wire, _ = bot({msg: Understanding(kind="brand", category="beauty")}, monthly_send_budget=0)
    svc.process_payload(fixtures.inbound("628888", msg, ts(T0)))
    assert wire.sent == []


def test_private_columns_and_booking_details_never_reach_ai_context(bot):
    svc, _, _ = bot({})
    ctx = talents.context_for_ai(svc.store, dt.date(2026, 11, 2))
    assert "Dewi Lestari" in ctx and "Rp15.000.000" in ctx and "10–12 November 2026" in ctx
    for secret in ("0812", "minta DP", "RahasiaCorp", "budi"):
        assert secret not in ctx
    assert set(svc.store.q("SELECT * FROM talent_busy")[0].keys()) == {"talent_id", "start", "end"}


def test_draft_send_needs_confirmation_and_sends_via_api(bot):
    svc, wire, ch = bot({})
    svc.engine.ai_enabled = False
    svc.process_payload(fixtures.history({"628999": [("out", "Halo kak", ts(T0) - 86400 * 40)]}))  # existing client
    svc.process_payload(fixtures.inbound("628999", "Kak Dewi available 20 Nov?", ts(T0), msg_id="wamid.Q1"))
    svc.store.x("UPDATE conversations SET draft_text=?, draft_msg_id=?, draft_can_answer=1 WHERE wa_id=?",
                ("Halo kak, Dewi tersedia 20 November 🙏", "wamid.Q1", "628999"))
    cb = lambda data: svc.bot.handle_update({"callback_query": {"id": "c", "from": {"id": 1001}, "data": data,
                                             "message": {"message_id": 5, "chat": {"id": 1001}, "text": "x"}}}, T0)
    cb("send:628999")
    confirm = [m for m in ch.sent if m["op"] == "send"][-1]
    assert "Kirim pesan ini" in confirm["text"] and wire.sent == []
    ok_data = next(b for row in confirm["buttons"] for b in row if b.startswith("✅"))
    # buttons in the shadow log only keep labels; rebuild the callback the bot generated
    import hashlib
    h = hashlib.sha256("Halo kak, Dewi tersedia 20 November 🙏".encode()).hexdigest()[:8]
    cb(f"sendok:628999:{h}")
    assert wire.texts() == ["Halo kak, Dewi tersedia 20 November 🙏"]
    assert conv(svc, "628999")["state"] == AWAITING_THEM
    assert ok_data
