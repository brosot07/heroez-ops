import hashlib
import hmac
import json

from heroez_ops import fixtures
from heroez_ops.sources.base import ContactEvent
from heroez_ops.sources.meta import MetaCoexistenceSource

BIZ = "6280000000000"


def test_signature():
    src = MetaCoexistenceSource("s3cret", BIZ)
    body = b'{"a":1}'
    good = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert src.verify_signature(body, {"x-hub-signature-256": good})
    assert not src.verify_signature(body, {"x-hub-signature-256": "sha256=deadbeef"})
    assert not src.verify_signature(body, {})
    assert not MetaCoexistenceSource("", BIZ).verify_signature(body, {"x-hub-signature-256": good})


def test_parse_inbound_echo_history_contacts():
    src = MetaCoexistenceSource("x", BIZ)
    [ev] = src.parse(fixtures.inbound("628111", "Halo kak", 1000, name="Brand A"))
    assert (ev.direction, ev.source, ev.wa_id, ev.text, ev.contact_name) == ("in", "messages", "628111", "Halo kak", "Brand A")

    [ev] = src.parse(fixtures.echo("628111", "Halo, boleh info budget?", 1100))
    assert (ev.direction, ev.source, ev.wa_id) == ("out", "echo", "628111")

    evs = src.parse(fixtures.history({"628222": [("in", "Hi", 10), ("out", "Hello", 20)]}))
    assert [(e.direction, e.wa_id) for e in evs] == [("in", "628222"), ("out", "628222")]

    [ev] = src.parse(fixtures.contact_sync("628333", "Rina Brand"))
    assert isinstance(ev, ContactEvent) and ev.name == "Rina Brand"


def test_ignores_status_updates_and_other_objects():
    src = MetaCoexistenceSource("x", BIZ)
    status = {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "statuses": [{"id": "wamid.1", "status": "delivered", "timestamp": "1"}]}}]}]}
    assert src.parse(status) == []
    assert src.parse({"object": "page"}) == []
    assert json.dumps(fixtures.inbound("1", "x", 1))  # serialisable
