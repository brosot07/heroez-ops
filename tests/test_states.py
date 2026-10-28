from heroez_ops import fixtures
from heroez_ops.states import AWAITING_THEM, AWAITING_US, CLOSED, POSSIBLY_REPLIED

from .conftest import ts, wib

T0 = ts(wib(2026, 11, 2, 10, 0))  # Monday 10:00 WIB


def state(service, wa):
    c = service.engine.conversation(wa)
    return c["state"] if c else None


def test_inbound_then_reply_then_ack(service):
    service.process_payload(fixtures.inbound("628111", "Halo kak, mau tanya rate", T0))
    assert state(service, "628111") == AWAITING_US
    assert service.engine.conversation("628111")["assignee"] in ("rina", "budi")

    service.process_payload(fixtures.echo("628111", "Halo kak! Boleh info budgetnya?", T0 + 600))
    assert state(service, "628111") == AWAITING_THEM

    service.process_payload(fixtures.inbound("628111", "ok makasih kak 🙏", T0 + 900))
    assert state(service, "628111") == CLOSED
    reasons = [r["reason"] for r in service.store.q("SELECT reason FROM state_changes WHERE wa_id='628111' ORDER BY id")]
    assert len(reasons) == 3 and all(reasons)


def test_follow_up_keeps_original_waiting_time(service):
    service.process_payload(fixtures.inbound("628111", "Halo", T0))
    service.process_payload(fixtures.inbound("628111", "Mau tanya rate TikTok", T0 + 300))
    assert service.engine.conversation("628111")["waiting_since"] == T0


def test_echo_gap_marks_possibly_replied(service):
    service.process_payload(fixtures.inbound("628111", "Kak boleh minta talent list?", T0))
    # Admin replied from a device that sends no echo; client thanks us.
    service.process_payload(fixtures.inbound("628111", "makasih listnya kak", T0 + 1800))
    assert state(service, "628111") == POSSIBLY_REPLIED


def test_duplicate_webhook_is_ignored(service):
    p = fixtures.inbound("628111", "Halo", T0, msg_id="wamid.SAME")
    service.process_payload(p)
    service.process_payload(p)
    assert service.store.one("SELECT COUNT(*) n FROM messages")["n"] == 1
    assert service.store.one("SELECT COUNT(*) n FROM state_changes")["n"] == 1


def test_talent_routes_to_roster_handler(service):
    service.process_payload(fixtures.inbound("6281111111111", "Kak, brief minggu depan sudah ada?", T0))
    c = service.engine.conversation("6281111111111")
    assert (c["assignee"], c["assigned_how"]) == ("budi", "roster")


def test_round_robin_for_new_contacts(service):
    service.process_payload(fixtures.inbound("628111", "Halo", T0))
    service.process_payload(fixtures.inbound("628222", "Halo", T0))
    a = service.engine.conversation("628111")["assignee"]
    b = service.engine.conversation("628222")["assignee"]
    assert {a, b} == {"rina", "budi"}


def test_claim_is_sticky_for_next_conversation(service):
    service.process_payload(fixtures.inbound("628111", "Halo", T0))
    service.engine.claim("628111", "budi", T0 + 60)
    service.process_payload(fixtures.echo("628111", "Halo kak", T0 + 120))
    service.process_payload(fixtures.inbound("628111", "Kak, ada update?", T0 + 86400))
    assert service.engine.conversation("628111")["assignee"] == "budi"


def test_late_echo_for_older_reply_does_not_clear_newer_question(service):
    service.process_payload(fixtures.inbound("628111", "Kapan bisa kirim brief?", T0 + 600))
    service.process_payload(fixtures.echo("628111", "Halo kak", T0))  # sent earlier, delivered late
    assert state(service, "628111") == AWAITING_US


def test_first_message_sticker_needs_no_reply(service):
    service.process_payload(fixtures.inbound("628111", "", T0, msg_type="sticker"))
    assert state(service, "628111") == CLOSED


def test_manual_actions_record_feedback(service):
    service.process_payload(fixtures.inbound("628111", "Halo", T0))
    service.engine.mark_not_ours("628111", "rina", T0 + 60)
    assert state(service, "628111") == CLOSED
    service.process_payload(fixtures.inbound("628222", "Halo", T0))
    service.engine.mark_replied("628222", "budi", T0 + 60)
    assert state(service, "628222") == AWAITING_THEM
    kinds = {r["kind"] for r in service.store.q("SELECT kind FROM feedback")}
    assert kinds == {"not_ours", "already_replied"}


def test_history_backfill_sets_states(service):
    service.process_payload(fixtures.history({
        "628111": [("in", "Halo kak mau tanya rate", T0), ("out", "Halo kak!", T0 + 60)],
        "628222": [("in", "Kak invoice sudah dikirim?", T0)],
    }))
    assert state(service, "628111") == AWAITING_THEM
    assert state(service, "628222") == AWAITING_US
    assert service.store.one("SELECT kind FROM contacts WHERE wa_id='628111'")["kind"] == "existing_client"
