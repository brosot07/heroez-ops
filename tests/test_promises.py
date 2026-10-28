import datetime as dt

from heroez_ops import fixtures
from heroez_ops.promises import detect

from .conftest import ts, wib

T0 = wib(2026, 11, 2, 10, 0)   # Monday


def test_detects_promises_with_a_time():
    now = wib(2026, 11, 2, 10, 0)
    text, due = detect("Siap kak, besok saya kirim proposalnya ya 🙏", now)
    assert text.startswith("Siap kak, besok saya kirim") and due == wib(2026, 11, 3, 10, 0)
    assert detect("Nanti sore aku kabari ya", now)[1] == wib(2026, 11, 2, 16, 0)
    assert detect("Oke, kami update hari Jumat", now)[1] == wib(2026, 11, 6, 10, 0)
    assert detect("Jam 3 sore saya konfirmasi ke talentnya", now)[1] == wib(2026, 11, 2, 15, 0)
    assert detect("Minggu depan kita follow up lagi", now)[1] == wib(2026, 11, 9, 10, 0)
    assert detect("Segera kami cek ya kak", now)[1] == wib(2026, 11, 2, 14, 0)
    assert detect("Sure, we will send the deck tomorrow", now)[1] == wib(2026, 11, 3, 10, 0)
    # Numbers are redacted, questions and requests to the client are not promises
    assert "0812" not in detect("Besok saya kirim ke 081234567890 ya", now)[0]
    assert detect("Bisa kirim briefnya besok kak?", now) is None
    assert detect("Tolong kirim logonya besok ya", now) is None
    assert detect("Sama-sama kak 🙏", now) is None
    assert detect("Besok libur ya kak", now) is None


def test_reminder_flow_admin_then_supervisor(service, channel):
    svc = service
    svc.engine.ai_enabled = False
    svc.process_payload(fixtures.inbound("628111", "Kak proposalnya bisa dikirim?", ts(T0), name="Glowskin"))
    svc.engine.claim("628111", "rina", ts(T0))
    svc.process_payload(fixtures.echo("628111", "Siap kak, besok pagi saya kirim proposalnya ya", ts(T0) + 60))
    [p] = svc.store.q("SELECT * FROM promises")
    assert p["admin"] == "rina" and p["status"] == "open" and p["due_at"] == ts(wib(2026, 11, 3, 10, 0))
    # Not yet due, and nothing is sent after notification hours.
    svc.promises.remind(wib(2026, 11, 3, 9, 59))
    svc.promises.remind(wib(2026, 11, 3, 22, 0))
    assert not [m for m in channel.sent if m["op"] == "send"]
    assert svc.promises.remind(wib(2026, 11, 3, 10, 5)) == 1
    [m] = [m for m in channel.sent if m["op"] == "send"]
    assert m["chat_id"] == 1001 and "Janji ke <b>Glowskin</b>" in m["text"] and "besok pagi saya kirim" in m["text"]
    assert [b for row in m["buttons"] for b in row] == ["✅ Sudah", "⏰ 2 jam lagi"]
    # Not repeated to the admin; two business hours later the supervisor hears once.
    svc.promises.remind(wib(2026, 11, 3, 11, 0))
    assert len([m for m in channel.sent if m["op"] == "send"]) == 1
    assert svc.promises.remind(wib(2026, 11, 3, 12, 10)) == 1
    late = [m for m in channel.sent if m["op"] == "send"][-1]
    assert late["chat_id"] == 2001 and "Janji Rina ke <b>Glowskin</b> belum ditandai selesai" in late["text"]
    svc.promises.remind(wib(2026, 11, 3, 13, 0))
    assert len([m for m in channel.sent if m["op"] == "send"]) == 2
    # The admin marks it done from the button.
    svc.bot.handle_update({"callback_query": {"id": "c1", "from": {"id": 1001}, "data": f"pdone:628111:{p['id']}"}}, wib(2026, 11, 3, 13, 5))
    p = svc.store.one("SELECT * FROM promises")
    assert p["status"] == "done" and p["done_by"] == "rina"


def test_reply_closes_promise_and_snooze_and_listing(service, channel):
    svc = service
    svc.engine.ai_enabled = False
    svc.process_payload(fixtures.inbound("628111", "Kak rate Dewi?", ts(T0), name="Glowskin"))
    svc.engine.claim("628111", "budi", ts(T0))
    svc.process_payload(fixtures.echo("628111", "Nanti sore aku kabari ya kak", ts(T0) + 60))
    [p] = svc.store.q("SELECT * FROM promises")
    assert svc.promises.snooze(p["id"], ts(wib(2026, 11, 2, 16, 0)))
    assert svc.store.one("SELECT due_at FROM promises")["due_at"] == ts(wib(2026, 11, 2, 18, 0))
    listing = svc.promises.open_text("budi", wib(2026, 11, 2, 17, 0))
    assert "🕒 Mon 02/11 18:00 · <b>Glowskin</b>: “Nanti sore aku kabari ya kak”" in listing
    assert "Tidak ada janji" in svc.promises.open_text("rina", wib(2026, 11, 2, 17, 0))
    assert "(Budi)" in svc.promises.open_text(None, wib(2026, 11, 2, 19, 0)) and "🔴" in svc.promises.open_text(None, wib(2026, 11, 2, 19, 0))
    # Budi actually followed up: the promise closes by itself.
    svc.process_payload(fixtures.echo("628111", "Kak, rate Dewi Rp15jt per konten ya", ts(T0) + 5 * 3600))
    assert svc.store.one("SELECT status, done_by FROM promises")["done_by"] == "reply"
    # /janji command for an admin
    svc.bot.handle_update({"message": {"from": {"id": 1002}, "chat": {"id": 1002}, "text": "/janji"}}, wib(2026, 11, 2, 19, 0))
    assert "Tidak ada janji" in [m for m in channel.sent if m["op"] == "send"][-1]["text"]
    text = svc.digest.build(T0 - dt.timedelta(hours=1), T0 + dt.timedelta(hours=8))
    assert "🤝 Janji ke klien: ditepati 1 · terlambat 0 · masih terbuka lewat tempo 0" in text
