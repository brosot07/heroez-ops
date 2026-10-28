import datetime as dt

from heroez_ops import fixtures

from .conftest import ts, wib


def test_management_digest(service, channel):
    d0 = wib(2026, 11, 2, 10, 0)
    # Rina-or-Budi lead answered after 45 min; a second chat still waiting; a third went quiet 6 days ago.
    service.process_payload(fixtures.inbound("628111", "Halo kak mau tanya rate", ts(d0), name="Glowskin"))
    service.process_payload(fixtures.echo("628111", "Halo kak!", ts(d0 + dt.timedelta(minutes=45))))
    service.process_payload(fixtures.inbound("628222", "Kak invoice kapan?", ts(d0 + dt.timedelta(hours=2)), name="Kopi"))
    service.process_payload(fixtures.inbound("628333", "Halo", ts(d0 - dt.timedelta(days=7)), name="Lama"))
    service.process_payload(fixtures.echo("628333", "Halo kak, ini proposalnya", ts(d0 - dt.timedelta(days=6))))
    service.notifier.escalator.mark_fired  # noqa: B018

    assert service.digest.maybe_send(wib(2026, 11, 3, 9, 0), channel)
    assert not service.digest.maybe_send(wib(2026, 11, 3, 9, 5), channel)   # once a day
    sent = [r for r in channel.sent if r["op"] == "send" and "Ringkasan harian" in r["text"]]
    assert {r["chat_id"] for r in sent} == {2001, 2002, -200}              # supervisors + management group
    text = sent[0]["text"]
    assert "Pesan masuk: <b>2</b>" in text
    assert "median 45 menit" in text
    assert "Masih menunggu balasan sekarang: <b>1</b>" in text
    assert "Lama" in text and "Diam lebih dari 5 hari" in text
