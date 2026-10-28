import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from heroez_ops import secrets as secretstore
from heroez_ops.config import load_config
from heroez_ops.db import Store
from heroez_ops.setup_ui import SetupUI


@pytest.fixture
def ui(tmp_path):
    (tmp_path / "config").mkdir()
    defaults = Path(__file__).resolve().parent.parent / "heroez_ops" / "defaults"
    shutil.copy(defaults / "config.yaml", tmp_path / "config" / "config.yaml")
    shutil.copy(defaults / "holidays.yaml", tmp_path / "config" / "holidays.yaml")
    restarted = []
    u = SetupUI(tmp_path, Store(":memory:"), lambda: restarted.append(1))
    u.restarted = restarted
    return u


FORM = {"mode": "live", "business_number": "+62 812-3456-7890", "s1_name": "Sari", "s1_tg": "2001",
        "s2_name": "Tono", "s2_tg": "2002", "a1_name": "Rina", "a1_tg": "1001", "a1_spv1": "on",
        "a2_name": "Budi", "a2_tg": "1002", "a2_spv1": "on", "a2_spv2": "on", "start": "08:00", "end": "20:00",
        "day0": "on", "day1": "on", "nudge": "20", "group": "90", "urgent": "16",
        "telegram_bot_token": "123:abc", "meta_app_secret": ""}


def test_form_round_trip(ui, tmp_path):
    raw, sec = ui.apply_form(ui.raw(), FORM, {"META_APP_SECRET": "keepme"})
    ui.save(raw, sec)
    cfg = load_config(tmp_path / "config" / "config.yaml")
    assert cfg.live and cfg.business_number == "6281234567890"
    assert [s.name for s in cfg.supervisors_of("admin2")] == ["Sari", "Tono"]
    assert [s.name for s in cfg.supervisors_of("admin1")] == ["Sari"]
    assert cfg.business_days == {0, 1} and cfg.escalation.nudge_assignee_after_business_min == 20
    saved = secretstore.load(tmp_path / "secrets.bin")
    assert saved["TELEGRAM_BOT_TOKEN"] == "123:abc"
    assert saved["META_APP_SECRET"] == "keepme"          # blank field keeps the old value
    assert len(saved["META_VERIFY_TOKEN"]) > 20          # generated on first save


def test_invalid_config_is_not_saved(ui, tmp_path):
    before = (tmp_path / "config" / "config.yaml").read_text()
    bad = dict(FORM, start="21:00", end="09:00")
    c = TestClient(ui.create_app())
    r = c.post("/save", data={**bad, "csrf": ui.csrf})
    assert "Belum tersimpan" in r.text
    assert (tmp_path / "config" / "config.yaml").read_text() == before
    assert ui.restarted == []


def test_page_needs_csrf_and_local_host(ui):
    c = TestClient(ui.create_app())
    assert c.get("/").status_code == 200
    assert c.get("/", headers={"host": "attacker.example"}).status_code == 403
    assert c.post("/save", data=FORM).status_code == 403
    r = c.post("/save", data={**FORM, "csrf": ui.csrf})
    assert "Tersimpan" in r.text and ui.restarted == [1]
    assert "123:abc" not in c.get("/").text               # secrets never shown back


def test_bot_switches_and_meta_token(ui, tmp_path):
    form = {**FORM, "intake_enabled": "on", "sla_text": "30 menit", "send_budget": "800", "phone_number_id": "1234 5678",
            "meta_access_token": "EAAG-secret"}
    raw, sec = ui.apply_form(ui.raw(), form, {})
    ui.save(raw, sec)
    cfg = load_config(tmp_path / "config" / "config.yaml")
    assert cfg.intake_enabled and not cfg.faq_auto_send and not cfg.drafts_enabled
    assert cfg.sla_text == "30 menit" and cfg.monthly_send_budget == 800 and cfg.phone_number_id == "12345678"
    assert secretstore.load(tmp_path / "secrets.bin")["META_ACCESS_TOKEN"] == "EAAG-secret"
