"""Web app on a server: settings page at its own address, keys encrypted at rest, first-start command."""
import os
import shutil
import sys
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from heroez_ops import secrets as secretstore
from heroez_ops.db import Store
from heroez_ops.setup_ui import SetupUI
from heroez_ops.winmain import cmd_init, cmd_reset_password, ensure_home

DEFAULTS = Path(__file__).resolve().parent.parent / "heroez_ops" / "defaults"


def make_ui(tmp_path, server=None, access=None):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    raw = yaml.safe_load((DEFAULTS / "config.yaml").read_text(encoding="utf-8"))
    if server:
        raw["server"] = server
    if access:
        raw["access"] = access
    (tmp_path / "config" / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    shutil.copy(DEFAULTS / "holidays.yaml", tmp_path / "config" / "holidays.yaml")
    (tmp_path / "initial_password.txt").write_text("rahasia-123\n")
    return SetupUI(tmp_path, Store(":memory:"), lambda: None)


def login(client, host):
    r = client.post("/login", data={"csrf": client.ui.csrf, "password": "rahasia-123", "next": "/"},
                    headers={"host": host}, follow_redirects=False)
    return r


def client_for(ui, base="http://127.0.0.1:8788"):
    c = TestClient(ui.create_app(), base_url=base)
    c.ui = ui
    return c


def test_settings_address_is_refused_until_configured_then_needs_password(tmp_path):
    ui = make_ui(tmp_path)
    c = client_for(ui, "https://opsadmin.heroez.id")
    assert c.get("/login").status_code == 403                       # no settings address set: local only
    ui2 = make_ui(tmp_path / "b", server={"settings_hostname": "opsadmin.heroez.id"})
    c2 = client_for(ui2, "https://opsadmin.heroez.id")
    assert c2.get("/", follow_redirects=False).headers["location"].startswith("/login")
    r = c2.post("/login", data={"csrf": ui2.csrf, "password": "rahasia-123", "next": "/"},
                headers={"origin": "https://opsadmin.heroez.id"}, follow_redirects=False)
    assert r.status_code == 303 and "secure" in r.headers["set-cookie"].lower()
    page = c2.get("/").text
    assert "hanya dilindungi password" in page                      # warns until Cloudflare Access is set
    # Another site can't post to it
    assert c2.post("/login", data={"csrf": ui2.csrf, "password": "x"}, headers={"origin": "https://evil.example"}).status_code == 403
    # Any other hostname is still refused
    assert client_for(ui2, "https://ops.heroez.id").get("/login").status_code == 403


def test_with_access_set_only_owner_emails_get_in(tmp_path, monkeypatch):
    ui = make_ui(tmp_path, server={"settings_hostname": "opsadmin.heroez.id"},
                 access={"team_domain": "heroez.cloudflareaccess.com", "audience": "aud1", "owner_emails": ["justin@heroez.id"]})

    class FakeVerifier:
        def __init__(self, team, aud):
            pass

        def email(self, token):
            return {"good": "justin@heroez.id", "admin": "rina@heroez.id"}.get(token)

    monkeypatch.setattr("heroez_ops.webauth.AccessVerifier", FakeVerifier)
    c = client_for(ui, "https://opsadmin.heroez.id")
    assert c.get("/login").status_code == 403                                           # no Access login
    assert c.get("/login", headers={"cf-access-jwt-assertion": "admin"}).status_code == 403   # team member, not owner
    assert c.get("/login", headers={"cf-access-jwt-assertion": "good"}).status_code == 200
    # From the server itself (127.0.0.1) the Access login is not needed, the password still is.
    assert client_for(ui).get("/login").status_code == 200
    assert ui.store.one("SELECT COUNT(*) n FROM audit_log WHERE action='settings_denied'")["n"] == 2


@pytest.mark.skipif(sys.platform == "win32", reason="Windows uses DPAPI")
def test_secrets_are_encrypted_on_linux(tmp_path):
    p = tmp_path / "secrets.bin"
    secretstore.save(p, {"TELEGRAM_BOT_TOKEN": "123:abc", "ANTHROPIC_API_KEY": "sk-ant-xyz"})
    data = p.read_bytes()
    assert data.startswith(b"HZAESGCM1") and b"sk-ant-xyz" not in data and b"123:abc" not in data
    assert oct(os.stat(p).st_mode & 0o777) == "0o600" and oct(os.stat(tmp_path / "master.key").st_mode & 0o777) == "0o600"
    assert secretstore.load(p) == {"TELEGRAM_BOT_TOKEN": "123:abc", "ANTHROPIC_API_KEY": "sk-ant-xyz"}
    (tmp_path / "master.key").write_text(os.urandom(32).hex())         # wrong key: unreadable
    with pytest.raises(Exception):
        secretstore.load(p)


def test_init_and_reset_password(tmp_path, capsys):
    ensure_home(tmp_path, quiet=True)
    cmd_init(tmp_path, {"token": "eyJtoken", "public": "https://ops.heroez.id/", "settings": "OpsAdmin.heroez.id",
                        "owner": "Justin@heroez.id, x"})
    out = capsys.readouterr().out
    pw = out.split("First owner password: ")[1].split()[0]
    assert (tmp_path / "initial_password.txt").read_text().strip() == pw
    raw = yaml.safe_load((tmp_path / "config" / "config.yaml").read_text())
    assert raw["server"] == {"public_hostname": "ops.heroez.id", "settings_hostname": "opsadmin.heroez.id"} or \
        (raw["server"]["public_hostname"], raw["server"]["settings_hostname"]) == ("ops.heroez.id", "opsadmin.heroez.id")
    assert raw["access"]["owner_emails"] == ["justin@heroez.id"]
    assert secretstore.load(tmp_path / "secrets.bin")["CLOUDFLARE_TUNNEL_TOKEN"] == "eyJtoken"
    # The settings page accepts it once; a reset prints a new one and the old password stops working.
    ui = SetupUI(tmp_path, Store(":memory:"), lambda: None)
    assert ui.owner.check_password(pw)
    cmd_reset_password(tmp_path)
    new = capsys.readouterr().out.split("password: ")[1].split()[0]
    ui.owner.apply_reset_flag()
    ui.owner.apply_initial_password()
    assert ui.owner.check_password(new) and not ui.owner.check_password(pw)


def test_owner_can_download_a_backup_without_keys(tmp_path):
    import io
    import zipfile
    ui = make_ui(tmp_path)
    secretstore.save(tmp_path / "secrets.bin", {"TELEGRAM_BOT_TOKEN": "123:abc"})
    (tmp_path / "data" / "contracts").mkdir(parents=True)
    (tmp_path / "data" / "contracts" / "1.docx").write_bytes(b"doc")
    c = client_for(ui)
    assert c.get("/cadangan", follow_redirects=False).status_code == 303          # login first
    login(c, "127.0.0.1:8788")
    r = c.get("/cadangan")
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "data/heroez.db" in names and "data/contracts/1.docx" in names and "config/config.yaml" in names
    assert not any(n.endswith(("secrets.bin", "master.key", "owner.json")) for n in names)
