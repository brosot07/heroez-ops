"""Direct mode (DNS outside Cloudflare): Caddy in front, Telegram-code login instead of Cloudflare Access."""
import dataclasses
import re
import shutil
from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from heroez_ops.codelogin import CodeLogin
from heroez_ops.db import Store
from heroez_ops.docs_ui import ContractsUI
from heroez_ops.setup_ui import SetupUI
from heroez_ops.winmain import host_dispatch

DEFAULTS = Path(__file__).resolve().parent.parent / "heroez_ops" / "defaults"


# ---------------------------------------------------------------- routing and certificates
def test_one_port_serves_both_addresses_and_caddy_only_gets_certificates_for_them():
    settings, public = FastAPI(), FastAPI()
    settings.get("/")(lambda: {"app": "settings"})
    public.get("/")(lambda: {"app": "public"})
    app = host_dispatch(settings, public, "heroezadmin.nitsujlabs.app", {"heroez.nitsujlabs.app", "heroezadmin.nitsujlabs.app"})
    c = TestClient(app)
    assert c.get("/", headers={"host": "heroezadmin.nitsujlabs.app"}).json() == {"app": "settings"}
    assert c.get("/", headers={"host": "heroez.nitsujlabs.app"}).json() == {"app": "public"}
    assert c.get("/", headers={"host": "heroezadmin.nitsujlabs.app:443"}).json() == {"app": "settings"}
    assert c.get("/_caddy/ask?domain=heroez.nitsujlabs.app").status_code == 200
    assert c.get("/_caddy/ask?domain=heroezadmin.nitsujlabs.app").status_code == 200
    assert c.get("/_caddy/ask?domain=evil.example.com").status_code == 404
    assert c.get("/_caddy/ask").status_code == 404


def test_caddyfile_blocks_the_ask_path_and_forces_https():
    caddy = (Path(__file__).resolve().parent.parent / "deploy" / "Caddyfile").read_text()
    assert "ask http://app:8787/_caddy/ask" in caddy and "respond @internal 404" in caddy
    assert "redir https://{host}{uri} permanent" in caddy and "Strict-Transport-Security" in caddy


# ---------------------------------------------------------------- team login by Telegram code
def team_app(cfg, tmp_path):
    c = dataclasses.replace(cfg, telegram_bot_token="123:abc", access_team_domain="", access_audience="")
    c = dataclasses.replace(c, admins=tuple(dataclasses.replace(a, email=f"{a.key}@heroez.id") for a in c.admins))
    ui = ContractsUI(tmp_path, Store(":memory:"), lambda: c)
    sent = []
    ui.send_telegram = lambda chat, text: sent.append((chat, text)) or True
    app = FastAPI()
    app.include_router(ui.router(local=False))
    return ui, TestClient(app, base_url="https://heroez.nitsujlabs.app"), sent


def test_team_login_with_a_telegram_code(cfg, tmp_path):
    ui, c, sent = team_app(cfg, tmp_path)
    r = c.get("/spk", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/masuk?next=/spk"
    # Unknown email: same answer, nothing sent
    r = c.post("/masuk", data={"email": "stranger@gmail.com", "next": "/spk"})
    assert "Jika email ini terdaftar" in r.text and sent == []
    # Rina asks for a code: it goes to her Telegram ID only
    r = c.post("/masuk", data={"email": "Rina@heroez.id", "next": "/spk"})
    assert "Jika email ini terdaftar" in r.text and len(sent) == 1 and sent[0][0] == 1001
    code = re.search(r"<b>(\d{6})</b>", sent[0][1]).group(1)
    assert code not in r.text                                                 # never shown in the browser
    # A second request within a minute is refused (no spamming her Telegram)
    c.post("/masuk", data={"email": "rina@heroez.id"})
    assert len(sent) == 1
    # Wrong code, then the right one
    assert "Kode salah" in c.post("/masuk/kode", data={"email": "rina@heroez.id", "code": "000000"}).text
    r = c.post("/masuk/kode", data={"email": "rina@heroez.id", "code": code, "next": "/spk"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/spk"
    cookie = r.headers["set-cookie"].lower()
    assert "hz_team=" in cookie and "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
    assert "domain=" not in cookie and "max-age" not in cookie                # host-only, ends with the browser
    page = c.get("/spk").text
    assert "Rina" in page and 'href=/keluar' in page
    # The code works once only; logging out ends the session
    assert "Kode salah" in c.post("/masuk/kode", data={"email": "rina@heroez.id", "code": code}).text
    c.get("/keluar")
    assert c.get("/spk", follow_redirects=False).status_code == 303


def test_codes_lock_after_five_wrong_tries_and_expire():
    login = CodeLogin(Store(":memory:"), "team")
    code = login.new_code("rina@heroez.id", now=1000)
    for _ in range(5):
        assert not login.check_code("rina@heroez.id", "999999", now=1001)
    assert not login.check_code("rina@heroez.id", code, now=1002)              # locked after 5 wrong tries
    code = login.new_code("rina@heroez.id", now=1100)
    assert not login.check_code("rina@heroez.id", code, now=1100 + 601)        # expired after 10 minutes
    for t in (1200, 1300, 1400):
        login.new_code("rina@heroez.id", now=t)
    assert login.new_code("rina@heroez.id", now=1500) is None                  # 5 per hour


def test_cross_site_login_posts_are_refused(cfg, tmp_path):
    ui, c, sent = team_app(cfg, tmp_path)
    r = c.post("/masuk", data={"email": "rina@heroez.id"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403 and sent == []


def test_open_redirect_after_login_is_blocked(cfg, tmp_path):
    ui, c, sent = team_app(cfg, tmp_path)
    c.post("/masuk", data={"email": "rina@heroez.id"})
    code = re.search(r"<b>(\d{6})</b>", sent[0][1]).group(1)
    r = c.post("/masuk/kode", data={"email": "rina@heroez.id", "code": code, "next": "//evil.example/x"}, follow_redirects=False)
    assert r.headers["location"] == "/antrian"


# ---------------------------------------------------------------- settings: password + Telegram code
def settings_ui(tmp_path, owner_ids):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    raw = yaml.safe_load((DEFAULTS / "config.yaml").read_text(encoding="utf-8"))
    raw["server"] = {"mode": "direct", "public_hostname": "heroez.nitsujlabs.app", "settings_hostname": "heroezadmin.nitsujlabs.app"}
    raw["access"] = {"owner_telegram_ids": owner_ids}
    (tmp_path / "config" / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    shutil.copy(DEFAULTS / "holidays.yaml", tmp_path / "config" / "holidays.yaml")
    (tmp_path / "initial_password.txt").write_text("rahasia-123\n")
    ui = SetupUI(tmp_path, Store(":memory:"), lambda: None)
    sent = []
    ui.send_telegram = lambda chat, text: sent.append((chat, text)) or True
    return ui, TestClient(ui.create_app(), base_url="https://heroezadmin.nitsujlabs.app"), sent


def test_settings_from_the_internet_need_password_and_telegram_code(tmp_path):
    ui, c, sent = settings_ui(tmp_path, [777])
    r = c.post("/login", data={"csrf": ui.csrf, "password": "rahasia-123", "next": "/"}, follow_redirects=False)
    assert r.status_code == 200 and "Kode Telegram" in r.text and "hz_owner=" not in r.headers.get("set-cookie", "")
    assert sent[0][0] == 777
    code = re.search(r"<b>(\d{6})</b>", sent[0][1]).group(1)
    assert c.get("/", follow_redirects=False).headers["location"].startswith("/login")   # not in yet
    assert c.post("/login/kode", data={"csrf": ui.csrf, "code": "000000", "next": "/"}).status_code == 401
    r = c.post("/login/kode", data={"csrf": ui.csrf, "code": code, "next": "/"}, follow_redirects=False)
    assert r.status_code == 303 and "hz_owner=" in r.headers["set-cookie"]
    assert "password pemilik + kode dari bot Telegram" in c.get("/").text
    # Without the password step first, a code alone gets nowhere
    ui2, c2, sent2 = settings_ui(tmp_path / "b", [777])
    assert c2.post("/login/kode", data={"csrf": ui2.csrf, "code": "123456"}, follow_redirects=False).headers["location"] == "/login"


def test_without_owner_telegram_id_the_page_warns(tmp_path):
    ui, c, sent = settings_ui(tmp_path, [])
    r = c.post("/login", data={"csrf": ui.csrf, "password": "rahasia-123", "next": "/"}, follow_redirects=False)
    assert r.status_code == 303 and sent == []                                  # password only…
    assert "Telegram ID pemilik" in c.get("/").text and "hanya dilindungi password" in c.get("/").text   # …and a warning


def test_bare_address_opens_the_queue(cfg, tmp_path):
    ui, c, sent = team_app(cfg, tmp_path)
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/antrian"
