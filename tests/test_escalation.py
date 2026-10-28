import datetime as dt

from heroez_ops import fixtures

from .conftest import ts, wib


def sends(channel):
    return [r for r in channel.sent if r["op"] == "send"]


def run(service, start, end, step_min=1):
    t = start
    while t <= end:
        service.notifier.tick(t)
        t += dt.timedelta(minutes=step_min)


def assignee(service, wa):
    return service.engine.conversation(wa)["assignee"]


def tg(service, key):
    return service.cfg.admin(key).telegram_id


def test_nudge_after_30_business_minutes_to_assignee_only(service, channel):
    t0 = wib(2026, 11, 2, 10, 0)
    service.process_payload(fixtures.inbound("628111", "Halo kak mau tanya rate", ts(t0), name="Brand A"))
    run(service, t0, t0 + dt.timedelta(minutes=29))
    assert sends(channel) == []
    run(service, t0 + dt.timedelta(minutes=30), t0 + dt.timedelta(minutes=30))
    [msg] = sends(channel)
    assert msg["chat_id"] == tg(service, assignee(service, "628111"))
    assert "Brand A" in msg["text"] and "wa.me/628111" in msg["text"]
    assert msg["buttons"][0] == ["✋ Saya ambil", "✅ Sudah dibalas"]


def test_reply_stops_escalation(service, channel):
    t0 = wib(2026, 11, 2, 10, 0)
    service.process_payload(fixtures.inbound("628111", "Halo kak", ts(t0)))
    service.process_payload(fixtures.echo("628111", "Halo kak!", ts(t0 + dt.timedelta(minutes=20))))
    run(service, t0, t0 + dt.timedelta(hours=5), step_min=5)
    assert sends(channel) == []


def test_ack_messages_never_alert(service, channel):
    t0 = wib(2026, 11, 2, 10, 0)
    service.process_payload(fixtures.inbound("628111", "Kak rate card dong", ts(t0)))
    service.process_payload(fixtures.echo("628111", "Ini kak ratecardnya", ts(t0 + dt.timedelta(minutes=5))))
    service.process_payload(fixtures.inbound("628111", "ok makasih kak 🙏", ts(t0 + dt.timedelta(minutes=6))))
    service.process_payload(fixtures.inbound("628222", "", ts(t0), msg_type="sticker"))
    run(service, t0, t0 + dt.timedelta(hours=3), step_min=5)
    assert sends(channel) == []


def test_group_alert_at_two_hours(service, channel):
    t0 = wib(2026, 11, 2, 10, 0)
    service.process_payload(fixtures.inbound("628111", "Halo kak", ts(t0)))
    run(service, t0, t0 + dt.timedelta(hours=2), step_min=1)
    chats = [m["chat_id"] for m in sends(channel)]
    assert -100 in chats                          # admin group
    assert tg(service, "rina") in chats           # supervisor


def test_overnight_message_waits_for_morning(service, channel):
    t0 = wib(2026, 11, 2, 22, 0)  # Monday night
    service.process_payload(fixtures.inbound("628111", "Kak mau tanya rate", ts(t0)))
    run(service, t0, wib(2026, 11, 3, 8, 29), step_min=10)
    assert sends(channel) == []
    # 08:30 morning list mentions it; 09:30 = 30 business minutes -> nudge
    run(service, wib(2026, 11, 3, 8, 30), wib(2026, 11, 3, 9, 30), step_min=1)
    texts = [m["text"] for m in sends(channel)]
    assert any("Selamat pagi" in t and "628111" in t for t in texts)
    assert any("Belum dibalas" in t for t in texts)


def test_urgent_at_18_hours_goes_to_on_call_outside_hours(service, channel):
    t0 = wib(2026, 11, 7, 20, 0)   # Saturday 20:00; Sunday is closed
    service.process_payload(fixtures.inbound("628111", "Kak urgent, brief belum masuk", ts(t0)))
    service.engine.claim("628111", "rina", ts(t0))
    service.notifier.escalator.mark_fired("628111", "group")  # isolate the 18h rule
    run(service, wib(2026, 11, 8, 13, 59), wib(2026, 11, 8, 14, 0))
    urgent = [m for m in sends(channel) if "URGENT" in m["text"]]
    on_call = service.notifier.clock.on_call(wib(2026, 11, 8, 14, 0))
    assert {m["chat_id"] for m in urgent} == {tg(service, on_call), -100}


def test_snooze_delays_nudge(service, channel):
    t0 = wib(2026, 11, 2, 10, 0)
    service.process_payload(fixtures.inbound("628111", "Halo kak", ts(t0)))
    service.engine.snooze("628111", "rina", ts(t0 + dt.timedelta(minutes=10)))
    run(service, t0, t0 + dt.timedelta(minutes=69))
    assert sends(channel) == []
    run(service, t0 + dt.timedelta(minutes=70), t0 + dt.timedelta(minutes=70))
    assert len(sends(channel)) == 1


def test_p1_lead_alerts_management_at_4h(service, channel):
    t0 = wib(2026, 11, 2, 10, 0)
    service.process_payload(fixtures.inbound("628111", "Brand X, budget 500jt, deadline 2 minggu", ts(t0)))
    service.engine.set_priority("628111", "P1", ts(t0))
    run(service, t0, t0 + dt.timedelta(hours=4), step_min=5)
    assert any(m["chat_id"] == -200 for m in sends(channel))


def test_batching_one_message_per_10_minutes(service, channel):
    t0 = wib(2026, 11, 2, 10, 0)
    service.engine.claim  # noqa: B018
    for i, wa in enumerate(["628101", "628102", "628103"]):
        service.process_payload(fixtures.inbound(wa, "Halo kak", ts(t0 + dt.timedelta(minutes=i * 3))))
        service.engine.claim(wa, "rina", ts(t0))
    run(service, t0, t0 + dt.timedelta(minutes=45))
    to_rina = [m for m in sends(channel) if m["chat_id"] == tg(service, "rina")]
    assert len(to_rina) == 2                           # 10:30 (first) and 10:40 (the other two)
    assert "2 chat perlu dibalas" in to_rina[1]["text"]
    assert len(to_rina[1]["buttons"]) == 2             # one button row per chat


def test_alerts_redact_long_numbers(service, channel):
    t0 = wib(2026, 11, 2, 10, 0)
    service.process_payload(fixtures.inbound("628111", "Kak transfer ke rek 1234567890 ya, kapan cair?", ts(t0)))
    run(service, t0 + dt.timedelta(minutes=30), t0 + dt.timedelta(minutes=30))
    [msg] = sends(channel)
    assert "1234567890" not in msg["text"]


def test_history_backfill_does_not_flood(service, channel):
    t0 = wib(2026, 11, 2, 10, 0)
    old = ts(t0 - dt.timedelta(days=3))
    service.process_payload(fixtures.history({f"62811{i}": [("in", "Halo kak", old)] for i in range(10)}))
    run(service, t0, t0 + dt.timedelta(hours=1), step_min=5)
    assert sends(channel) == []
    assert "10 chat" in service.notifier.queue_text(None, t0)
