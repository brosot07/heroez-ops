import json
import shutil
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from heroez_ops import secrets as secretstore
from heroez_ops import wa_connect
from heroez_ops.db import Store
from heroez_ops.setup_ui import SetupUI

DEFAULTS = Path(__file__).resolve().parent.parent / "heroez_ops" / "defaults"


class FakeMeta:
    def __init__(self, history_off=False, bad_code=False):
        self.calls, self.history_off, self.bad_code = [], history_off, bad_code

    def __call__(self, req: httpx.Request):
        path = req.url.path
        self.calls.append((req.method, path, req.url.params.get("client_secret"), req.headers.get("authorization"),
                           json.loads(req.content) if req.content else None))
        if path.endswith("/oauth/access_token"):
            if self.bad_code:
                return httpx.Response(400, json={"error": {"message": "Invalid verification code", "code": 100}})
            return httpx.Response(200, json={"access_token": "BIZTOKEN", "token_type": "bearer"})
        if path.endswith("/WABA1/subscribed_apps"):
            return httpx.Response(200, json={"success": True})
        if path.endswith("/PN1") and req.method == "GET":
            return httpx.Response(200, json={"display_phone_number": "+62 812-3456-7890", "verified_name": "Heroez", "id": "PN1"})
        if path.endswith("/PN1/smb_app_data"):
            if self.history_off and json.loads(req.content)["sync_type"] == "history":
                return httpx.Response(400, json={"error": {"message": "History sharing disabled", "code": 2593109}})
            return httpx.Response(200, json={"success": True})
        if path.endswith("/WABA1/phone_numbers"):
            return httpx.Response(200, json={"data": [{"id": "PN1"}]})
        return httpx.Response(404, json={"error": {"message": "unknown", "code": 1}})


def test_full_coexistence_connection_in_the_right_order():
    meta = FakeMeta()
    res = wa_connect.connect("CODE", "APPID", "APPSECRET", "WABA1", "PN1", http=httpx.Client(transport=httpx.MockTransport(meta)))
    assert res["token"] == "BIZTOKEN" and res["business_number"] == "6281234567890" and res["verified_name"] == "Heroez"
    paths = [(m, p.split("/v23.0")[1]) for m, p, *_ in meta.calls]
    assert paths == [("GET", "/oauth/access_token"), ("POST", "/WABA1/subscribed_apps"), ("GET", "/PN1"),
                     ("POST", "/PN1/smb_app_data"), ("POST", "/PN1/smb_app_data")]
    assert meta.calls[0][2] == "APPSECRET" and all(c[3] == "Bearer BIZTOKEN" for c in meta.calls[1:])
    assert [c[4]["sync_type"] for c in meta.calls[3:]] == ["smb_app_state_sync", "history"]   # contacts first
    assert not any(p.endswith("/register") for _, p, *_ in meta.calls)                       # never re-register the number


def test_history_refused_is_not_fatal_and_missing_phone_id_is_looked_up():
    meta = FakeMeta(history_off=True)
    res = wa_connect.connect("CODE", "APPID", "S", "WABA1", "", http=httpx.Client(transport=httpx.MockTransport(meta)))
    assert res["phone_number_id"] == "PN1" and res["steps"]["smb_app_state_sync"] == "ok"
    assert "berbagi riwayat dimatikan" in res["steps"]["history"]


def test_bad_code_is_reported():
    with pytest.raises(wa_connect.ConnectError, match="Invalid verification code"):
        wa_connect.connect("X", "APPID", "S", "WABA1", "PN1", http=httpx.Client(transport=httpx.MockTransport(FakeMeta(bad_code=True))))


def test_settings_page_saves_the_connection(tmp_path):
    (tmp_path / "config").mkdir()
    raw = yaml.safe_load((DEFAULTS / "config.yaml").read_text(encoding="utf-8"))
    raw["whatsapp"] = {"business_number": "", "app_id": "123", "signup_config_id": "456"}
    (tmp_path / "config" / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    shutil.copy(DEFAULTS / "holidays.yaml", tmp_path / "config" / "holidays.yaml")
    (tmp_path / "initial_password.txt").write_text("rahasia-123\n")
    restarted = []
    ui = SetupUI(tmp_path, Store(":memory:"), lambda: restarted.append(1))
    c = TestClient(ui.create_app(), base_url="http://127.0.0.1:8788")
    c.post("/login", data={"csrf": ui.csrf, "password": "rahasia-123", "next": "/"})
    assert "Meta App Secret" in c.get("/whatsapp").text                    # secret missing → told what to fill
    secretstore.save(tmp_path / "secrets.bin", {"META_APP_SECRET": "APPSECRET"})
    page = c.get("/whatsapp").text
    assert "whatsapp_business_app_onboarding" in page and '"456"' in page and "connect.facebook.net" in page
    ui.wa_connector = lambda code, app, secret, waba, pn, ver: {
        "token": "BIZTOKEN", "waba_id": waba, "phone_number_id": pn or "PN1", "business_number": "6281234567890",
        "verified_name": "Heroez", "steps": {"token": "ok", "webhooks": "ok", "history": "ok"}}
    assert c.post("/whatsapp/selesai", data={"code": "C", "waba_id": "1001", "phone_number_id": "2002"}).status_code == 403   # CSRF
    r = c.post("/whatsapp/selesai", data={"csrf": ui.csrf, "code": "C", "waba_id": "1001", "phone_number_id": "2002"})
    assert r.json()["ok"] and restarted
    saved = yaml.safe_load((tmp_path / "config" / "config.yaml").read_text())["whatsapp"]
    assert (saved["waba_id"], saved["phone_number_id"], saved["business_number"]) == ("1001", "2002", "6281234567890")
    assert secretstore.load(tmp_path / "secrets.bin")["META_ACCESS_TOKEN"] == "BIZTOKEN"
    assert "Sudah tersambung" in c.get("/whatsapp").text
