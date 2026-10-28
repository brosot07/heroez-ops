"""Integration: HTTP webhooks end to end (signature, durable inbox, processing)."""
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from heroez_ops import fixtures


@pytest.fixture
def client(cfg, tmp_path, monkeypatch):
    cfg_path = tmp_path / "config" / "config.yaml"
    monkeypatch.setenv("HEROEZ_CONFIG", str(cfg_path))
    monkeypatch.setenv("HEROEZ_DB", str(tmp_path / "data" / "app.db"))
    monkeypatch.setenv("META_APP_SECRET", "appsecret")
    monkeypatch.setenv("META_VERIFY_TOKEN", "verifyme")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "tgsecret")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    from heroez_ops.app import create_app
    app = create_app(run_background=False)
    with TestClient(app) as c:
        c.service, c.store = app.state.service, app.state.store
        yield c


def signed(body: bytes) -> dict:
    return {"X-Hub-Signature-256": "sha256=" + hmac.new(b"appsecret", body, hashlib.sha256).hexdigest(),
            "Content-Type": "application/json"}


def test_verify_handshake(client):
    r = client.get("/webhook/meta", params={"hub.mode": "subscribe", "hub.verify_token": "verifyme", "hub.challenge": "42"})
    assert r.status_code == 200 and r.text == "42"
    assert client.get("/webhook/meta", params={"hub.mode": "subscribe", "hub.verify_token": "no"}).status_code == 403


def test_bad_signature_rejected(client):
    body = json.dumps(fixtures.inbound("628111", "Halo", 1_800_000_000)).encode()
    assert client.post("/webhook/meta", content=body, headers={"X-Hub-Signature-256": "sha256=bad"}).status_code == 403


def test_signed_webhook_is_stored_then_processed(client):
    body = json.dumps(fixtures.inbound("628111", "Halo kak", 1_800_000_000)).encode()
    r = client.post("/webhook/meta", content=body, headers=signed(body))
    assert r.status_code == 200
    client.service.process_inbox()
    conv = client.service.engine.conversation("628111")
    assert conv["state"] == "AWAITING_US"


def test_telegram_requires_secret(client):
    assert client.post("/webhook/telegram", json={}).status_code == 403
    r = client.post("/webhook/telegram", json={"update_id": 1}, headers={"X-Telegram-Bot-Api-Secret-Token": "tgsecret"})
    assert r.status_code == 200


def test_health(client):
    client.store.set_kv("last_tick_at", str(__import__("time").time().__int__()))
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["mode"] == "shadow"
