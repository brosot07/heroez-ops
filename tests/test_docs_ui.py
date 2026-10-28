import dataclasses
import json
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from heroez_ops import doctemplate as dtp
from heroez_ops.db import Store
from heroez_ops.docs_ui import ContractsUI
from heroez_ops.webauth import User

from .test_doctemplate import fake_ai_answer, sample_contract


@pytest.fixture
def setup(cfg, tmp_path):
    from heroez_ops.contract_ai import clean_spec
    c = dataclasses.replace(cfg, anthropic_api_key="test-key", pkp=True, ppn_rate=0.11,
                            contract_number_format="{seq:03d}/HRZ-KTR/{roman_month}/{year}")
    ui = ContractsUI(tmp_path, Store(":memory:"), lambda: c,
                     analyzer=lambda data, key: clean_spec(fake_ai_answer(data), data))
    app = FastAPI()
    app.include_router(ui.router(local=True))
    return ui, TestClient(app)


def upload_and_wait(ui, client) -> int:
    r = client.post("/template/upload", data={"csrf": ui.csrf, "name": "Perjanjian Kampanye"},
                    files={"file": ("kontrak.docx", sample_contract())}, follow_redirects=False)
    assert r.status_code == 303
    tid = int(r.headers["location"].rsplit("/", 1)[1])
    for t in __import__("threading").enumerate():
        if t.name == f"template-{tid}":
            t.join(10)
    return tid


def test_template_flow_upload_review_approve(setup):
    ui, client = setup
    tid = upload_and_wait(ui, client)
    page = client.get(f"/template/{tid}").text
    assert "Template cocok" in page and "<mark>Nama klien</mark>" in page
    # Not usable until approved
    assert client.get(f"/kontrak/baru?t={tid}").status_code == 400
    assert client.post(f"/template/{tid}/setujui", data={"csrf": ui.csrf}, follow_redirects=False).status_code == 303
    assert "🟢 Disetujui" in client.get("/template").text


def test_contract_from_form_with_auto_totals(setup):
    ui, client = setup
    tid = upload_and_wait(ui, client)
    client.post(f"/template/{tid}/setujui", data={"csrf": ui.csrf})
    form = client.get(f"/kontrak/baru?t={tid}").text
    assert 'name="f_client_name"' in form and 'name="r_talent_0"' in form
    number = re.search(r'name="f_contract_number" value="([^"]+)"', form).group(1)
    r = client.post("/kontrak/simpan", data={
        "csrf": ui.csrf, "tid": tid, "cid": 0, "f_client_name": "PT Glowskin Indonesia",
        "f_client_address": "Jl. Kemang Raya 5", "f_fee_total": "", "f_sign_date": "2026-11-10",
        "f_contract_number": number,
        "r_talent_0": "Dewi", "r_platform_0": "TikTok", "r_fee_0": "100jt",
        "r_talent_1": "Raka", "r_platform_1": "IG", "r_fee_1": "50.000.000",
        "r_talent_2": "", "r_platform_2": "", "r_fee_2": ""}, follow_redirects=False)
    assert r.status_code == 303
    cid = int(r.headers["location"].rsplit("/", 1)[1])
    data = json.loads(ui.store.one("SELECT data FROM contracts WHERE id=?", (cid,))["data"])
    assert data["values"]["fee_total"] == 150_000_000           # summed from the SOW rows
    assert len(data["rows"]) == 2                               # empty row ignored
    doc = dtp.load(client.get(f"/kontrak/{cid}/word").content)
    body = "\n".join(p.text for p in doc.paragraphs)
    assert "PT Glowskin Indonesia" in body and "Rp150.000.000 (seratus lima puluh juta Rupiah)" in body
    assert "Pasal 1. Para Pihak sepakat untuk bekerja sama dengan itikad baik." in body
    assert doc.sections[0].header.paragraphs[0].text == f"No. {number}"
    # The number is used up: the next form proposes the following one.
    nxt = re.search(r'name="f_contract_number" value="([^"]+)"', client.get(f"/kontrak/baru?t={tid}").text).group(1)
    assert nxt != number and nxt.startswith("002/")


def test_bad_amount_shows_form_again(setup):
    ui, client = setup
    tid = upload_and_wait(ui, client)
    client.post(f"/template/{tid}/setujui", data={"csrf": ui.csrf})
    r = client.post("/kontrak/simpan", data={"csrf": ui.csrf, "tid": tid, "cid": 0, "f_client_name": "X",
                                              "f_fee_total": "seratus juta"})
    assert "tidak terbaca" in r.text
    assert ui.store.one("SELECT COUNT(*) n FROM contracts")["n"] == 0


def test_manual_field_and_csrf(setup):
    ui, client = setup
    tid = upload_and_wait(ui, client)
    assert client.post(f"/template/{tid}/simpan", data={"keep_0": "on"}).status_code == 403   # no csrf
    r = client.post(f"/template/{tid}/simpan", data={
        "csrf": ui.csrf, **{f"keep_{i}": "on" for i in range(6)}, "keep_sow": "on",
        "new_text": "Ditandatangani pada", "new_label": "Kalimat tanda tangan", "new_type": "text", "new_group": "other"})
    assert "Kalimat tanda tangan" in r.text
    # Editing sends the template back to review, so it must be approved again.
    assert ui.store.one("SELECT status FROM contract_templates WHERE id=?", (tid,))["status"] == "review"


def test_admins_cannot_manage_templates(setup, cfg):
    ui, _ = setup
    app = FastAPI()
    router = ui.router(local=False)
    app.include_router(router)
    import heroez_ops.docs_ui as mod
    admin = User("rina@heroez.id", "Rina", "admin", "rina")
    orig = mod.remote_user
    mod.remote_user = lambda request, cfg, verifier: admin
    try:
        c = TestClient(app)
        assert c.get("/kontrak").status_code == 200
        r = c.post("/template/upload", data={"csrf": ui.csrf, "name": "x"}, files={"file": ("a.docx", sample_contract())})
        assert r.status_code == 403
    finally:
        mod.remote_user = orig


def test_remote_pages_refuse_without_login(setup):
    ui, _ = setup
    app = FastAPI()
    app.include_router(ui.router(local=False))
    c = TestClient(app)
    r = c.get("/kontrak")
    assert r.status_code == 403 and "Login belum diatur" in r.text   # Access not configured: default deny


def test_queue_page_and_actions(setup):
    ui, client = setup
    from heroez_ops import fixtures
    from heroez_ops.states import Engine
    eng = Engine(ui.cfg(), ui.store)
    for ev in __import__("heroez_ops.sources.meta", fromlist=["x"]).MetaCoexistenceSource("x", "6280000000000").parse(
            fixtures.inbound("628111", "Kak cek link bit.ly/promo ya", 1_800_000_000, name="Brand A")):
        eng.handle(ev)
    page = client.get("/antrian").text
    assert "Brand A" in page and "bit[.]ly/promo" in page and "bit.ly/promo" not in page
    r = client.post("/antrian/aksi", data={"csrf": ui.csrf, "wa": "628111", "aksi": "dibalas"}, follow_redirects=False)
    assert r.status_code == 303
    assert "Tidak ada chat" in client.get("/antrian").text
    assert client.post("/antrian/aksi", data={"wa": "628111", "aksi": "dibalas"}).status_code == 403   # csrf required
