import datetime as dt

from heroez_ops import fixtures
from heroez_ops.learning import Learning

from .conftest import ts, wib

T0 = wib(2026, 11, 2, 10, 0)


def test_admin_replies_become_candidates_and_drafts_are_scored(service):
    svc = service
    svc.engine.ai_enabled = False
    svc.process_payload(fixtures.inbound("628111", "Kak rate Dewi untuk IG Reels berapa? rek 1234567890", ts(T0), msg_id="wamid.Q"))
    svc.store.x("UPDATE conversations SET draft_text=?, draft_msg_id=?, draft_can_answer=1 WHERE wa_id=?",
                ("Halo kak! Rate Dewi untuk IG Reels Rp15.000.000 per konten ya 🙏", "wamid.Q", "628111"))
    svc.process_payload(fixtures.echo("628111", "Halo kak! Rate Dewi untuk IG Reels Rp15.000.000 per konten ya 🙏", ts(T0) + 300))
    learn = Learning(svc.store)
    [c] = learn.candidates()
    assert c["admin_reply"].startswith("Halo kak! Rate Dewi") and "1234567890" not in c["client_text"]
    [o] = svc.store.q("SELECT * FROM draft_outcomes")
    assert o["outcome"] == "used" and o["similarity"] >= 0.9
    # An edited reply
    svc.process_payload(fixtures.inbound("628111", "Kalau TikTok?", ts(T0) + 600, msg_id="wamid.Q2"))
    svc.store.x("UPDATE conversations SET draft_text=?, draft_msg_id=?, draft_can_answer=1 WHERE wa_id=?",
                ("Untuk TikTok Rp12.000.000 per video kak 🙏", "wamid.Q2", "628111"))
    svc.process_payload(fixtures.echo("628111", "Untuk TikTok Rp12.000.000 per video kak, kalau ambil 3 video bisa kami diskusikan 🙏", ts(T0) + 900))
    stats = learn.draft_stats(ts(T0), ts(T0) + 3600)
    assert stats["n"] == 2 and stats["used"] == 1 and stats["edited"] == 1 and stats["used_pct"] == 50


def test_acks_teach_nothing_and_approval_feeds_the_prompt(service):
    svc = service
    svc.engine.ai_enabled = False
    svc.process_payload(fixtures.inbound("628222", "ok makasih kak 🙏", ts(T0)))
    svc.process_payload(fixtures.echo("628222", "Sama-sama kak!", ts(T0) + 60))
    learn = Learning(svc.store)
    assert learn.candidates() == []
    svc.process_payload(fixtures.inbound("628222", "Kak minta proposal dong", ts(T0) + 120))
    svc.process_payload(fixtures.echo("628222", "Siap kak, proposal kami kirim sore ini ya 🙏", ts(T0) + 180))
    [c] = learn.candidates()
    assert learn.examples_for_prompt() == ""                                  # candidates are not used until approved
    learn.decide(c["id"], True, "spv:sari", admin_reply="Siap kak, proposalnya kami kirim sore ini ya 🙏")
    prompt = learn.examples_for_prompt()
    assert "KLIEN: Kak minta proposal dong" in prompt and "HEROEZ: Siap kak, proposalnya" in prompt
    learn.decide(c["id"], False, "spv:sari")
    assert learn.examples_for_prompt() == "" and learn.approved() == []


def test_digest_reports_draft_acceptance(service, channel):
    svc = service
    svc.engine.ai_enabled = False
    svc.process_payload(fixtures.inbound("628333", "Rate Sinta?", ts(T0), msg_id="wamid.S"))
    svc.store.x("UPDATE conversations SET draft_text=?, draft_msg_id=?, draft_can_answer=1 WHERE wa_id=?", ("Rp25.000.000 kak", "wamid.S", "628333"))
    svc.process_payload(fixtures.echo("628333", "Rp25.000.000 kak", ts(T0) + 60))
    text = svc.digest.build(T0 - dt.timedelta(days=1), T0 + dt.timedelta(hours=2))
    assert "Saran balasan 7 hari: dipakai apa adanya 100%" in text
