from heroez_ops import fixtures
from heroez_ops.states import AWAITING_THEM, CLOSED

from .conftest import ts, wib

NOW = wib(2026, 11, 2, 11, 0)


def press(service, user_id, data, message=None):
    cq = {"id": "cb1", "from": {"id": user_id}, "data": data}
    if message:
        cq["message"] = message
    service.bot.handle_update({"callback_query": cq}, NOW)


def command(service, user_id, text):
    service.bot.handle_update({"message": {"from": {"id": user_id}, "chat": {"id": user_id}, "text": text}}, NOW)


def setup_chat(service):
    service.process_payload(fixtures.inbound("628111", "Halo kak mau tanya", ts(wib(2026, 11, 2, 10, 0)), name="Brand A"))


def test_buttons(service, channel):
    setup_chat(service)
    press(service, 1002, "claim:628111")
    assert service.engine.conversation("628111")["assignee"] == "budi"
    press(service, 1002, "done:628111")
    assert service.engine.conversation("628111")["state"] == AWAITING_THEM
    answers = [r["text"] for r in channel.sent if r["op"] == "answer"]
    assert answers[0].startswith("✋") and answers[1].startswith("✅")


def test_not_ours_closes(service):
    setup_chat(service)
    press(service, 1001, "nope:628111")
    assert service.engine.conversation("628111")["state"] == CLOSED


def test_strangers_are_refused(service, channel):
    setup_chat(service)
    press(service, 5555, "nope:628111")
    assert service.engine.conversation("628111")["state"] != CLOSED
    command(service, 5555, "/semua")
    assert not any(r["op"] == "send" and "Semua chat" in r.get("text", "") for r in channel.sent)
    command(service, 5555, "/start")
    assert "5555" in channel.sent[-1]["text"]   # shows ID so it can be added to config


def test_queue_and_search(service, channel):
    setup_chat(service)
    assignee = service.engine.conversation("628111")["assignee"]
    command(service, service.cfg.admin(assignee).telegram_id, "/antrian")
    assert "Brand A" in channel.sent[-1]["text"]
    command(service, 1001, "/cari Brand")
    assert "628111" in channel.sent[-1]["text"]
    command(service, 1001, "/cari 0899")       # no match
    assert "Tidak ada" in channel.sent[-1]["text"]


def test_single_alert_is_updated_after_resolve(service, channel):
    setup_chat(service)
    msg = {"message_id": 7, "chat": {"id": 1001}, "text": "x",
           "reply_markup": {"inline_keyboard": [[{"text": "✅", "callback_data": "done:628111"}]]}}
    press(service, 1001, "done:628111", message=msg)
    edit = [r for r in channel.sent if r["op"] == "edit"][-1]
    assert "Sudah dibalas (Rina)" in edit["text"]


def test_supervisor_can_take_a_chat_personally(service, channel):
    setup_chat(service)
    press(service, 2001, "claim:628111")
    assert service.engine.conversation("628111")["assignee"] == "spv:sari"
    press(service, 2001, "done:628111")
    assert service.engine.conversation("628111")["state"] == AWAITING_THEM


def test_newcomer_start_is_remembered(service, channel):
    service.bot.handle_update({"message": {"from": {"id": 777, "first_name": "Dina"}, "chat": {"id": 777}, "text": "/start"}}, NOW)
    assert '"id": 777' in service.store.get_kv("tg_newcomers")
