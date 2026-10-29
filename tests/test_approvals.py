import dataclasses

from heroez_ops import fixtures
from heroez_ops.concierge_ai import Understanding
from heroez_ops.db import Store
from heroez_ops.service import Service
from heroez_ops.alerts.telegram import ShadowAlertChannel

from .conftest import ts, wib
from .test_intake import FakeAI, bot  # noqa: F401  (fixture)

T0 = wib(2026, 11, 2, 10, 0)
BRAND = "Halo, kami dari Glowskin mau campaign IG tgl 20-22 Nov, minat Sinta"
BRAND_U = Understanding(kind="brand", brand_name="Glowskin", talents=["Sinta"], category="beauty",
                        platforms=["instagram"], date_start="2026-11-20", date_end="2026-11-22")


def cards(ch):
    return [m for m in ch.sent if m["op"] == "send" and "Balasan bot" in m["text"] or m["op"] == "send" and "Contoh balasan bot" in m["text"]]


def press(svc, user, data):
    svc.bot.handle_update({"callback_query": {"id": "c", "from": {"id": user}, "data": data}}, T0)


def outbox(svc):
    return svc.store.q("SELECT * FROM bot_outbox ORDER BY id")


def test_nothing_reaches_the_client_until_a_supervisor_taps_kirim(bot):  # noqa: F811
    svc, wire, ch = bot({BRAND: BRAND_U}, bot_reply_mode="approve")
    svc.process_payload(fixtures.inbound("628111", BRAND, ts(T0), name="Glowskin"))
    assert wire.sent == []                                            # nothing sent yet
    [p] = outbox(svc)
    assert p["status"] == "pending" and "Sinta" in p["body"] and "Rp25.000.000" in p["body"]
    cs = cards(svc.intake.approvals.channel)
    assert {c["chat_id"] for c in cs} == {2001, 2002}                 # every supervisor sees it
    assert "Balasan bot menunggu persetujuan" in cs[0]["text"] and "Klien: “Halo, kami dari Glowskin" in cs[0]["text"]
    assert cs[0]["buttons"] == [["✅ Kirim", "❌ Jangan, serahkan ke admin"]]
    # An admin can't approve; a supervisor can.
    press(svc, 1001, f"bok:628111:{p['id']}")
    assert wire.sent == []
    press(svc, 2001, f"bok:628111:{p['id']}")
    assert len(wire.sent) == 1 and "Sinta" in wire.texts()[0]
    assert outbox(svc)[0]["status"] == "sent" and outbox(svc)[0]["decided_by"] == "Sari"
    edits = [m for m in ch.sent if m["op"] == "edit"]
    assert len(edits) == 2 and all("✅ Dikirim (Sari)" in m["text"] for m in edits)   # both supervisors' cards updated
    press(svc, 2002, f"bok:628111:{p['id']}")                          # second supervisor is too late
    assert len(wire.sent) == 1
    assert [m for m in ch.sent if m["op"] == "answer"][-1]["text"] == "Sudah ditangani sebelumnya."


def test_rejected_reply_is_never_sent_and_an_admin_takes_over(bot):  # noqa: F811
    svc, wire, ch = bot({"Halo kak": Understanding(kind="unclear")}, bot_reply_mode="approve")
    svc.process_payload(fixtures.inbound("628222", "Halo kak", ts(T0)))
    [p] = outbox(svc)
    assert p["kind"] == "buttons" and svc.intake.row("628222")["status"] == "active"
    assert "Tombol: [Brand" in cards(ch)[0]["text"]
    press(svc, 2002, f"bno:628222:{p['id']}")
    assert wire.sent == [] and outbox(svc)[0]["status"] == "rejected"
    assert svc.intake.row("628222")["status"] == "done"                # handed to a person
    assert svc.engine.conversation("628222")["state"] == "AWAITING_US"
    assert svc.store.one("SELECT kind FROM feedback WHERE wa_id='628222'")["kind"] == "bot_reply_wrong"


def test_admin_reply_cancels_waiting_bot_message(bot):  # noqa: F811
    svc, wire, ch = bot({"Halo kak": Understanding(kind="unclear")}, bot_reply_mode="approve")
    svc.process_payload(fixtures.inbound("628333", "Halo kak", ts(T0)))
    [p] = outbox(svc)
    svc.process_payload(fixtures.echo("628333", "Halo kak, ada yang bisa kami bantu?", ts(T0) + 120))
    assert outbox(svc)[0]["status"] == "cancelled"
    assert any("Admin sudah membalas sendiri" in m["text"] for m in ch.sent if m["op"] == "edit")
    press(svc, 2001, f"bok:628333:{p['id']}")
    assert wire.sent == []


def test_unapproved_reply_expires_and_goes_to_admin(bot):  # noqa: F811
    svc, wire, ch = bot({"Halo kak": Understanding(kind="unclear")}, bot_reply_mode="approve")
    svc.process_payload(fixtures.inbound("628444", "Halo kak", ts(T0)))
    svc.intake.expire(ts(T0) + 20 * 60)                                # past the 15-min client timeout, not the 30-min approval
    assert svc.intake.row("628444")["status"] == "active" and outbox(svc)[0]["status"] == "pending"
    svc.intake.expire(ts(T0) + 31 * 60)
    assert outbox(svc)[0]["status"] == "expired" and wire.sent == []
    assert svc.intake.row("628444")["status"] == "done"
    assert any("Tidak disetujui dalam 30 menit" in m["text"] for m in ch.sent if m["op"] == "edit")


def test_approval_sends_earlier_waiting_messages_in_order(bot):  # noqa: F811
    svc, wire, ch = bot({"Halo kak": Understanding(kind="unclear")}, bot_reply_mode="approve")
    svc.process_payload(fixtures.inbound("628555", "Halo kak", ts(T0)))
    svc.intake._text("628555", "Pesan kedua")                          # a second message queued in the same chat
    first, second = outbox(svc)
    press(svc, 2001, f"bok:628555:{second['id']}")
    assert [r["status"] for r in outbox(svc)] == ["sent", "sent"] and len(wire.sent) == 2
    assert wire.sent[0]["type"] == "interactive" and wire.texts()[1] == "Pesan kedua"


def test_shadow_mode_shows_previews_and_sends_nothing(cfg, tmp_path):
    c = dataclasses.replace(cfg, mode="shadow", bot_reply_mode="approve")
    ch = ShadowAlertChannel(tmp_path / "s.jsonl")
    svc = Service(c, Store(":memory:"), alert_channel=ch, reply_channel=ch, data_dir=tmp_path)
    svc.intake.ai = FakeAI({"Halo kak": Understanding(kind="unclear")})
    svc.process_payload(fixtures.inbound("628666", "Halo kak", ts(T0)))
    [card] = [m for m in ch.sent if m["op"] == "send" and m["chat_id"] == 2001]
    assert "Contoh balasan bot" in card["text"] and card["buttons"] == [["👍 Bagus", "👎 Kurang tepat"]]
    [p] = outbox(svc)
    press(svc, 2001, f"bno:628666:{p['id']}")
    assert outbox(svc)[0]["status"] == "rated_bad"
    assert svc.intake.row("628666")["status"] == "active"              # preview only: the flow is not interrupted
    text = svc.digest.build(T0, wib(2026, 11, 2, 12, 0))
    assert "🤖 Balasan bot: contoh 👎 1" in text


def test_bot_command_switches_off_and_back_to_approval_but_not_to_auto(bot):  # noqa: F811
    svc, wire, ch = bot({BRAND: BRAND_U}, bot_reply_mode="auto")

    def cmd(user, text):
        svc.bot.handle_update({"message": {"from": {"id": user}, "chat": {"id": user}, "text": text}}, T0)
        return [m for m in ch.sent if m["op"] == "send"][-1]["text"]

    assert "Otomatis" in cmd(2001, "/bot")
    assert "Hanya supervisor" in cmd(1001, "/bot mati")
    assert "Mati" in cmd(2001, "/bot mati")
    svc.process_payload(fixtures.inbound("628777", BRAND, ts(T0), name="Glowskin"))
    assert wire.sent == [] and outbox(svc) == []                       # switched off: nothing sent or queued
    assert "Cek dulu" in cmd(2002, "/bot cek")
    assert "hanya bisa dinyalakan dari halaman Pengaturan" in cmd(2001, "/bot otomatis")
    svc.process_payload(fixtures.inbound("628888", BRAND, ts(T0) + 60, name="Glowskin"))
    assert wire.sent == [] and len(outbox(svc)) == 1
    assert "1 balasan menunggu persetujuan" in cmd(2001, "/bot")
