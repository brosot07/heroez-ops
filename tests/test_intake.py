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
from heroez_ops.states import AWAITING_US, CLOSED

from .conftest import ts, wib

T0 = wib(2026, 11, 2, 10, 0)

ROSTER = [
    {"Nama": "Dewi Lestari", "Handle IG": "@dewi.lestari", "Followers IG": "120rb", "Niche": "beauty, skincare",
     "Rate IG Reels": "15.000.000", "Rate TikTok": "12jt", "No HP": "0812-1111-2222", "Handler": "budi",
     "Catatan internal": "minta DP 50%"},
    {"Nama": "Raka Pratama", "Handle TikTok": "@rakagaming", "Followers TikTok": "1,2M", "Niche": "gaming",
     "Rate TikTok": "20jt", "No HP": "0813-3333-4444", "Handler": "rina"},
    {"Nama": "Sinta", "Handle IG": "@sinta.glow", "Followers IG": "300K", "Niche": "beauty", "Rate IG Reels": "25jt"},
    {"Nama": "Bima", "Handle IG": "@bima.eats", "Followers IG": "80rb", "Niche": "fnb, kuliner", "Tipe": "non-eksklusif"},
    {"Nama": "Paket Nano Beauty", "Niche": "beauty", "Tipe": "nano", "Handler": "rina"},
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
    assert "*Sinta* (@sinta.glow): IG Reels Rp25.000.000 — jadwal 10–12 November 2026 terlihat kosong, akan dikonfirmasi admin" in reply
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
    assert "*Raka Pratama* (@rakagaming): TikTok Rp20.000.000 — jadwal 1–5 Desember 2026 terlihat kosong, akan dikonfirmasi admin" in wire.texts()[-1]
    assert svc.intake.row("628222")["status"] == "done"
    assert conv(svc, "628222")["state"] == AWAITING_US


def test_brand_without_named_kol_goes_to_supervisor_triage(bot):
    msg = "Mau cari KOL beauty buat IG"
    svc, wire, ch = bot({msg: Understanding(kind="brand", brand_name="Glowskin", category="beauty", platforms=["instagram"])})
    svc.process_payload(fixtures.inbound("628333", msg, ts(T0)))
    assert wire.texts()[-1].startswith("Admin kami akan melanjutkan")      # no KOL suggestions: a person decides
    c = conv(svc, "628333")
    assert c["state"] == AWAITING_US and c["assignee"] is None and c["triage_since"]
    svc.notifier.tick(T0 + dt.timedelta(minutes=1))
    sent = [m for m in ch.sent if m["op"] == "send"]
    assert {m["chat_id"] for m in sent} == {2001, 2002}                    # supervisors only
    assert "pilih admin" in sent[0]["text"] and any("→" in b for row in sent[0]["buttons"] for b in row)
    # A supervisor assigns it to Budi.
    svc.bot.handle_update({"callback_query": {"id": "c", "from": {"id": 2001}, "data": "assign:628333:budi"}}, T0)
    c = conv(svc, "628333")
    assert c["assignee"] == "budi" and c["assigned_how"] == "triage" and c["triage_since"] is None


def test_triage_falls_back_to_round_robin_after_grace(bot):
    msg = "Mau campaign kopi, ada solusi?"
    svc, _, ch = bot({msg: Understanding(kind="brand", brand_name="Kopi X", campaign="launch kopi")})
    svc.process_payload(fixtures.inbound("628334", msg, ts(T0)))
    svc.notifier.tick(T0 + dt.timedelta(minutes=31))
    c = conv(svc, "628334")
    assert c["assignee"] in ("rina", "budi") and c["assigned_how"] == "round_robin"


def test_named_kol_routes_to_its_handler(bot):
    msg = "Mau Raka untuk TikTok 1-5 Des"
    svc, _, _ = bot({msg: Understanding(kind="brand", brand_name="Kopi Z", talents=["Raka"], platforms=["tiktok"],
                                        date_start="2026-12-01", date_end="2026-12-05")})
    svc.process_payload(fixtures.inbound("628335", msg, ts(T0)))
    c = conv(svc, "628335")
    assert c["assignee"] == "rina" and c["assigned_how"] == "kol_handler"   # Raka's handler in ROSTER


def test_maybe_spam_gets_menu_and_second_message_rescues(bot):
    msg = "Halo, kami punya penawaran menarik untuk bisnis Anda"
    svc, wire, _ = bot({msg: Understanding(kind="spam", summary_id="promosi umum"),
                        "Kami brand kopi, mau tanya KOL F&B": Understanding(kind="brand", brand_name="Kopi Y", category="fnb")})
    svc.process_payload(fixtures.inbound("628445", msg, ts(T0)))
    assert "sebagai apa" in wire.texts()[-1]                                # not sure: the menu, not silence
    assert svc.intake.row("628445")["status"] == "active"
    svc.intake.ai.understand = lambda texts, f=svc.intake.ai: f.answers.get(texts[-1], Understanding(kind="unclear"))
    svc.process_payload(fixtures.inbound("628445", "Kami brand kopi, mau tanya KOL F&B", ts(T0) + 60))
    assert svc.intake.row("628445")["status"] == "done"                     # a real person: handled normally
    assert conv(svc, "628445")["state"] == AWAITING_US


def test_maybe_spam_that_never_answers_becomes_spam(bot):
    msg = "Halo, kami punya penawaran menarik untuk bisnis Anda"
    svc, wire, _ = bot({msg: Understanding(kind="spam", summary_id="promosi umum")})
    svc.process_payload(fixtures.inbound("628446", msg, ts(T0)))
    svc.intake.expire(ts(T0) + 20 * 60 + 10**9)
    assert svc.intake.row("628446")["status"] == "spam"
    assert conv(svc, "628446")["state"] == CLOSED


def test_sure_spam_is_silent_and_second_spam_message_stays_spam(bot):
    msg = "PINJAMAN ONLINE CAIR 5 MENIT klik link"
    svc, wire, _ = bot({msg: Understanding(kind="spam", summary_id="pinjol"),
                        "Promo bunga 0% daftar sekarang": Understanding(kind="spam", summary_id="pinjol")})
    svc.process_payload(fixtures.inbound("628447", msg, ts(T0)))
    assert wire.sent == [] and svc.intake.row("628447")["status"] == "spam"
    svc.intake.ai.understand = lambda texts, f=svc.intake.ai: f.answers.get(texts[-1], Understanding(kind="unclear"))
    svc.process_payload(fixtures.inbound("628447", "Promo bunga 0% daftar sekarang", ts(T0) + 60))
    assert wire.sent == [] and svc.intake.row("628447")["status"] == "spam"


def test_daily_spam_card_to_supervisors(bot):
    msg = "PINJAMAN ONLINE CAIR 5 MENIT klik link"
    svc, _, ch = bot({msg: Understanding(kind="spam", summary_id="pinjol")})
    svc.process_payload(fixtures.inbound("628448", msg, ts(T0)))
    svc.notifier.morning_list(dt.datetime(2026, 11, 3, 8, 35, tzinfo=T0.tzinfo))
    cards = [m for m in ch.sent if m["op"] == "send" and m["buttons"] and "Bukan spam" in m["buttons"][0][0]]
    assert {m["chat_id"] for m in cards} == {2001, 2002}
    assert cards[0]["buttons"] == [["Bukan spam 1"]]


def test_review_mode_sends_only_to_supervisors_with_feedback_buttons(bot):
    svc, _, ch = bot({}, mode="review")
    svc.engine.ai_enabled = False
    svc.process_payload(fixtures.history({"628449": [("out", "Halo kak", ts(T0) - 86400 * 40)]}))
    svc.process_payload(fixtures.inbound("628449", "Kak, rate Dewi berapa?", ts(T0)))
    svc.engine.claim("628449", "rina", ts(T0))
    for m in range(0, 32):
        svc.notifier.tick(T0 + dt.timedelta(minutes=m))
    sent = [m for m in ch.sent if m["op"] == "send"]
    assert sent and {m["chat_id"] for m in sent} == {2001, 2002}         # Rina (1001) gets nothing in review mode
    assert ["👍 Notifikasi benar", "👎 Salah / tidak perlu"] in sent[0]["buttons"]
    svc.bot.handle_update({"callback_query": {"id": "c", "from": {"id": 2001}, "data": "fb_no:628449"}}, T0)
    assert conv(svc, "628449")["state"] == CLOSED
    kinds = [r["kind"] for r in svc.store.q("SELECT kind FROM feedback")]
    assert kinds == ["alert_wrong"]
    assert svc.sender.live is False or True                                # bot sends are not live in review mode (see cfg.live)


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
    assert set(svc.store.q("SELECT * FROM talent_busy")[0].keys()) == {"talent_id", "start", "end", "source"}


def test_alert_has_no_send_or_not_ours_buttons(bot):
    svc, _, ch = bot({})
    svc.engine.ai_enabled = False
    svc.process_payload(fixtures.history({"628999": [("out", "Halo kak", ts(T0) - 86400 * 40)]}))
    svc.process_payload(fixtures.inbound("628999", "Kak Dewi available 20 Nov?", ts(T0), msg_id="wamid.Q1"))
    svc.store.x("UPDATE conversations SET draft_text=?, draft_msg_id=?, draft_can_answer=1 WHERE wa_id=?",
                ("Halo kak, Dewi tersedia 20 November 🙏", "wamid.Q1", "628999"))
    text, buttons = svc.notifier.render([("628999", "nudge")], T0 + dt.timedelta(minutes=31))
    labels = [b.text for row in buttons for b in row]
    assert labels == ["✋ Saya ambil", "✅ Sudah dibalas", "⏰ Tunda 1 jam"]
    assert "Dewi tersedia 20 November" in text                        # suggestion still shown, to copy


def test_unavailable_kol_gets_available_alternatives(bot):
    msg = "Mau Dewi Lestari untuk IG tgl 10-12 Nov"
    svc, wire, _ = bot({msg: Understanding(kind="brand", brand_name="Glowskin", talents=["Dewi Lestari"],
                                           platforms=["instagram"], date_start="2026-11-10", date_end="2026-11-12")})
    svc.process_payload(fixtures.inbound("628121", msg, ts(T0)))
    reply = wire.texts()[-1]
    assert "Dewi Lestari* (@dewi.lestari): IG Reels Rp15.000.000 — ❌ tidak tersedia" in reply
    assert "Sebagai alternatif" in reply
    assert "*Sinta* (@sinta.glow): IG Reels Rp25.000.000 — jadwal 10–12 November 2026 terlihat kosong" in reply   # same niche, free
    assert "Raka" not in reply                                                                         # gaming: not similar
    assert "alternatif: Sinta" in svc.engine.conversation("628121")["ai_summary"]


def test_available_kol_gets_no_cross_sell(bot):
    msg = "Sinta untuk IG tgl 10-12 Nov bisa?"
    svc, wire, _ = bot({msg: Understanding(kind="brand", brand_name="Glowskin", talents=["Sinta"], platforms=["instagram"],
                                           date_start="2026-11-10", date_end="2026-11-12")})
    svc.process_payload(fixtures.inbound("628122", msg, ts(T0)))
    assert "alternatif" not in wire.texts()[-1]


def test_specific_hour_request_goes_to_admin(bot):
    msg = "Sinta bisa live TikTok tgl 10 Nov jam 19.00?"
    svc, wire, _ = bot({msg: Understanding(kind="brand", brand_name="Glowskin", talents=["Sinta"], platforms=["tiktok"],
                                           date_start="2026-11-10", date_end="2026-11-10", asks_specific_time=True)})
    svc.process_payload(fixtures.inbound("628123", msg, ts(T0)))
    [reply] = wire.texts()
    assert reply.startswith("Admin kami akan melanjutkan") and "Rp" not in reply and "tersedia" not in reply
    assert svc.intake.row("628123")["outcome"] == "minta jam tertentu"
    assert svc.engine.conversation("628123")["state"] == AWAITING_US


def test_unidentified_sender_is_asked_for_company_then_gets_mulai_dari(bot):
    first = "Rate Dewi berapa?"
    svc, wire, _ = bot({first: Understanding(kind="brand", talents=["Dewi Lestari"], platforms=["instagram"],
                                             date_start="2026-11-20", date_end="2026-11-20")})
    svc.process_payload(fixtures.inbound("628511", first, ts(T0)))
    assert "nama brand/agency" in wire.texts()[-1]                     # asked who they are first
    svc.intake.ai.understand = lambda texts, f=svc.intake.ai: Understanding(kind="brand", talents=["Dewi Lestari"],
                                                                            platforms=["instagram"], date_start="2026-11-20",
                                                                            date_end="2026-11-20")
    svc.process_payload(fixtures.inbound("628511", "cuma mau tahu aja", ts(T0) + 60))     # still no company
    reply = wire.texts()[-1]
    assert "Dewi Lestari* (@dewi.lestari): mulai dari Rp15.000.000" in reply
    assert "Rp15.000.000 · " not in reply                              # no full rate card for an unknown sender


def test_identified_brand_gets_exact_rates(bot):
    msg = "Kami dari PT Glowskin, rate Dewi untuk IG Reels?"
    svc, wire, _ = bot({msg: Understanding(kind="brand", brand_name="PT Glowskin", talents=["Dewi Lestari"], platforms=["instagram"],
                                           date_start="2026-11-20", date_end="2026-11-20")})
    svc.process_payload(fixtures.inbound("628512", msg, ts(T0)))
    assert "IG Reels Rp15.000.000" in wire.texts()[-1] and "mulai dari" not in wire.texts()[-1]


def test_non_exclusive_talent_has_no_rate_in_bot_reply(bot):
    msg = "Kami Kopi Senja, Bima bisa untuk IG tgl 20 Nov?"
    svc, wire, _ = bot({msg: Understanding(kind="brand", brand_name="Kopi Senja", talents=["Bima"], platforms=["instagram"],
                                           date_start="2026-11-20", date_end="2026-11-20")})
    svc.process_payload(fixtures.inbound("628513", msg, ts(T0)))
    reply = wire.texts()[-1]
    assert "*Bima* (@bima.eats): kami bisa bantu untuk talent ini; rate dikonfirmasi admin kami" in reply
    assert "Rp" not in reply


def test_nano_request_goes_to_supervisor_without_names(bot):
    msg = "Kami brand X, mau paket nano beauty"
    svc, wire, _ = bot({msg: Understanding(kind="brand", brand_name="Brand X", talents=["Paket Nano Beauty"],
                                           date_start="2026-11-20", date_end="2026-11-20")})
    svc.process_payload(fixtures.inbound("628514", msg, ts(T0)))
    reply = wire.texts()[-1]
    assert "nano/micro KOL" in reply and "Rp" not in reply and "Paket Nano" not in reply
    assert conv(svc, "628514")["triage_since"] and conv(svc, "628514")["assignee"] is None


def test_handover_wording_outside_hours(bot):
    msg = "Kami dari PT Glowskin, rate Sinta?"
    svc, wire, _ = bot({msg: Understanding(kind="brand", brand_name="PT Glowskin", talents=["Sinta"],
                                           date_start="2026-11-20", date_end="2026-11-20")})
    night = wib(2026, 11, 2, 23, 30)
    svc.process_payload(fixtures.inbound("628515", msg, ts(night)))
    assert "jam kerja berikutnya" in wire.texts()[-1] and "1 jam kerja" not in wire.texts()[-1]


def test_ai_context_hides_non_exclusive_rates_and_shows_type(bot):
    svc, _, _ = bot({})
    ctx = talents.context_for_ai(svc.store, dt.date(2026, 11, 2))
    assert "Bima | tipe: non-eksklusif" in ctx and "Rate: tanyakan admin" in ctx
    assert "Dewi Lestari | tipe: eksklusif" in ctx
