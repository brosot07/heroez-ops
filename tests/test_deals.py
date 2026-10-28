import dataclasses
import datetime as dt
import json
import re
import threading

import httpx
import pytest
from fastapi import HTTPException

from heroez_ops import fixtures
from heroez_ops.ai import Verdict
from heroez_ops.alerts.telegram import ShadowAlertChannel
from heroez_ops.concierge_ai import Understanding
from heroez_ops.db import Store
from heroez_ops.deals import LOST, Deals
from heroez_ops.deals_ui import deal_action
from heroez_ops.service import Service
from heroez_ops.webauth import User

from .conftest import ts, wib
from .test_docs_ui import setup, upload_and_wait  # noqa: F401  (fixture)
from .test_intake import bot  # noqa: F401  (fixture)

T0 = wib(2026, 11, 2, 10, 0)
OK = lambda url: (True, "")  # noqa: E731


class FakeClassifier:
    def __init__(self, verdict):
        self.verdict = verdict

    def classify(self, transcript, contact_kind):
        return self.verdict


def wait_pushes():
    for t in threading.enumerate():
        if t.name == "deals-push":
            t.join(10)


# ---------------------------------------------------------------- handshake → one tap → Deal
def test_agreement_is_confirmed_by_admin_not_by_ai(cfg, channel):
    v = Verdict("acknowledgement", False, "P2", "Glowskin setuju ambil Dewi 15jt", 0.9, deal_signal="agreement", deal_value=15_000_000)
    svc = Service(cfg, Store(":memory:"), alert_channel=channel, reply_channel=channel, classifier=FakeClassifier(v))
    svc.process_payload(fixtures.inbound("628111", "Oke deal kak, kami ambil Dewi 15jt ya", ts(T0), name="Glowskin"))
    svc.engine.claim("628111", "rina", ts(T0))
    svc.classify_pending(T0 + dt.timedelta(minutes=1))
    [d] = svc.store.q("SELECT * FROM deals")
    assert d["stage"] == "Lead" and d["value"] == 15_000_000 and d["deal_asked_at"] and d["pic"] == "rina"
    asks = [m for m in channel.sent if m["op"] == "send" and "Catat sebagai" in m["text"]]
    assert len(asks) == 1 and asks[0]["chat_id"] == 1001                      # the assignee, not the whole team
    assert "Glowskin" in asks[0]["text"] and "Rp15.000.000" in asks[0]["text"]
    assert [b for row in asks[0]["buttons"] for b in row] == ["✅ Ya, deal", "Belum"]
    # A stranger can't confirm; the admin can.
    svc.bot.handle_update({"callback_query": {"id": "c0", "from": {"id": 5555}, "data": f"dealyes:628111:{d['id']}"}}, T0)
    assert svc.store.one("SELECT stage FROM deals")["stage"] == "Lead"
    svc.bot.handle_update({"callback_query": {"id": "c1", "from": {"id": 1001}, "data": f"dealyes:628111:{d['id']}"}}, T0)
    d = svc.store.one("SELECT * FROM deals")
    assert d["stage"] == "Deal" and json.loads(d["stages"])["Deal"]
    # Another agreement message doesn't ask again once the deal is past Deal.
    svc.process_payload(fixtures.inbound("628111", "Fix ya kak, lanjut", ts(T0) + 600))
    svc.classify_pending(T0 + dt.timedelta(minutes=11))
    assert len([m for m in channel.sent if m["op"] == "send" and "Catat sebagai" in m["text"]]) == 1
    assert svc.store.one("SELECT COUNT(*) n FROM deals")["n"] == 1


def test_belum_keeps_the_lead_and_does_not_nag(cfg, channel):
    v = Verdict("acknowledgement", False, "P2", "setuju", 0.9, deal_signal="agreement")
    svc = Service(cfg, Store(":memory:"), alert_channel=channel, reply_channel=channel, classifier=FakeClassifier(v))
    svc.process_payload(fixtures.inbound("628111", "oke sip", ts(T0)))
    svc.classify_pending(T0)
    d = svc.store.one("SELECT * FROM deals")
    asks = lambda: len([m for m in channel.sent if m["op"] == "send" and "Catat sebagai" in m["text"]])  # noqa: E731
    assert asks() == 2 and {m["chat_id"] for m in channel.sent if m["op"] == "send"} == {2001, 2002}  # unassigned → supervisors
    svc.bot.handle_update({"callback_query": {"id": "c1", "from": {"id": 2001}, "data": f"dealno:628111:{d['id']}"}}, T0)
    assert svc.store.one("SELECT stage FROM deals")["stage"] == "Lead"
    svc.process_payload(fixtures.inbound("628111", "oke sip lanjut", ts(T0) + 3600))
    svc.classify_pending(T0 + dt.timedelta(hours=1))
    assert asks() == 2                                                        # not asked again within 3 days


def test_proposal_request_moves_lead_quietly_and_talents_are_ignored(cfg, channel):
    v = Verdict("request", True, "P2", "minta proposal", 0.9, deal_signal="proposal_requested")
    svc = Service(cfg, Store(":memory:"), alert_channel=channel, reply_channel=channel, classifier=FakeClassifier(v))
    svc.process_payload(fixtures.inbound("628111", "Kak boleh kirim proposal?", ts(T0), name="Brand A"))
    svc.process_payload(fixtures.inbound("6281111111111", "Kak proposal buat aku?", ts(T0)))       # the talent
    svc.classify_pending(T0)
    rows = svc.store.q("SELECT * FROM deals")
    assert len(rows) == 1 and rows[0]["stage"] == "Proposal" and rows[0]["brand"] == "Brand A" and rows[0]["source"] == "chat"
    assert not [m for m in channel.sent if m["op"] == "send" and "Catat sebagai" in m["text"]]


# ---------------------------------------------------------------- bot handoff → Lead
def test_bot_handoff_of_a_brand_creates_a_lead(bot):  # noqa: F811
    first = "Halo, kami dari Glowskin mau campaign TikTok & IG tgl 10-12 Nov, minat Dewi Lestari dan Sinta"
    svc, wire, _ = bot({first: Understanding(kind="brand", brand_name="Glowskin", campaign="launch serum",
                                             talents=["Dewi Lestari", "@sinta.glow"], category="beauty",
                                             platforms=["instagram"], date_start="2026-11-10", date_end="2026-11-12")})
    svc.process_payload(fixtures.inbound("628111", first, ts(T0), name="Glowskin"))
    [d] = svc.store.q("SELECT * FROM deals")
    assert d["brand"] == "Glowskin" and d["stage"] == "Lead" and d["source"] == "bot"
    assert d["talents"] == "Dewi Lestari, Sinta" and d["period_start"] == "2026-11-10" and d["period_end"] == "2026-11-12"
    assert d["pic"] == svc.engine.conversation("628111")["assignee"]
    # More messages from the same number don't create a second lead.
    svc.process_payload(fixtures.inbound("628111", "Kak masih ada?", ts(T0) + 7200))
    assert svc.store.one("SELECT COUNT(*) n FROM deals")["n"] == 1
    assert svc.store.one("SELECT last_activity FROM deals")["last_activity"] == ts(T0) + 7200


# ---------------------------------------------------------------- contract → Kontrak with value
def test_contract_moves_deal_to_kontrak_with_its_value(setup):  # noqa: F811
    ui, client = setup
    ui._deals = lambda: Deals(ui.cfg(), ui.store, http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok"))), check_host=OK)
    tid = upload_and_wait(ui, client)
    client.post(f"/template/{tid}/setujui", data={"csrf": ui.csrf})
    did = ui._deals().ensure_lead("628111", "PT Glowskin Indonesia", "rina", ts(T0), talents="Dewi")
    form = client.get(f"/kontrak/baru?t={tid}").text
    assert f'<option value="{did}">#{did} PT Glowskin Indonesia · Lead</option>' in form
    number = re.search(r'name="f_contract_number" value="([^"]+)"', form).group(1)
    r = client.post("/kontrak/simpan", data={
        "csrf": ui.csrf, "tid": tid, "cid": 0, "deal_id": did, "f_client_name": "PT Glowskin Indonesia",
        "f_client_address": "Jl. Kemang Raya 5", "f_fee_total": "", "f_sign_date": "2026-11-10", "f_contract_number": number,
        "r_talent_0": "Dewi", "r_platform_0": "TikTok", "r_fee_0": "100jt",
        "r_talent_1": "Raka", "r_platform_1": "IG", "r_fee_1": "50.000.000"}, follow_redirects=False)
    assert r.status_code == 303
    cid = int(r.headers["location"].rsplit("/", 1)[1])
    d = ui.store.one("SELECT * FROM deals WHERE id=?", (did,))
    assert d["stage"] == "Kontrak" and d["contract_id"] == cid and d["contract_number"] == number
    assert d["value"] == 150_000_000 * 111 // 100 or d["value"] in (150_000_000, 166_500_000)   # grand total incl. PPN when PKP
    # Editing the contract keeps the link; a contract without a chosen deal creates one at Kontrak.
    form2 = client.get(f"/kontrak/{cid}/ubah").text
    assert f'<option value="{did}" selected>' in form2
    r = client.post("/kontrak/simpan", data={
        "csrf": ui.csrf, "tid": tid, "cid": 0, "deal_id": 0, "f_client_name": "PT Lain",
        "f_client_address": "Jl. X", "f_fee_total": "20jt", "f_sign_date": "2026-11-10", "f_contract_number": "",
        "r_talent_0": "Sinta", "r_platform_0": "IG", "r_fee_0": "20jt"}, follow_redirects=False)
    assert r.status_code == 303
    rows = ui.store.q("SELECT brand, stage, source FROM deals ORDER BY id")
    assert len(rows) == 2 and rows[1]["brand"] == "PT Lain" and rows[1]["stage"] == "Kontrak" and rows[1]["source"] == "contract"
    page = client.get("/deal").text
    assert "PT Glowskin Indonesia" in page and "PT Lain" in page and "Kontrak" in page


# ---------------------------------------------------------------- stage rules
def test_lunas_and_batal_are_for_supervisors_only(cfg):
    store = Store(":memory:")
    deals = Deals(cfg, store, check_host=OK)
    did = deals.ensure_lead("628111", "Brand A", "rina", ts(T0))
    admin = User("rina@heroez.id", "Rina", "admin", "rina")
    spv = User("sari@heroez.id", "Sari", "supervisor", "sari")
    deal_action(deals, admin, did, "Ditandatangani", value="25jt", note="tanda tangan di kantor", at=ts(T0) + 60)
    d = deals.get(did)
    assert d["stage"] == "Ditandatangani" and d["value"] == 25_000_000 and "tanda tangan di kantor" in d["notes"]
    with pytest.raises(HTTPException) as ex:
        deal_action(deals, admin, did, "Lunas", at=ts(T0) + 120)
    assert ex.value.status_code == 403
    with pytest.raises(HTTPException):
        deal_action(deals, admin, did, LOST, at=ts(T0) + 120)
    deal_action(deals, spv, did, "Lunas", at=ts(T0) + 180)
    assert deals.get(did)["stage"] == "Lunas" and deals.open_for("628111") is None
    with pytest.raises(HTTPException):
        deal_action(deals, spv, did, "Menang", at=ts(T0) + 200)
    # Custom stage names from config
    c2 = dataclasses.replace(cfg, deal_stages=("Prospek", "Nego", "Closing"))
    d2 = Deals(c2, store, check_host=OK)
    assert d2.stages == ["Prospek", "Nego", "Closing"]
    did2 = d2.ensure_lead("628222", "Brand B", None, ts(T0))
    assert d2.get(did2)["stage"] == "Prospek"


def test_stage_config_parsing(cfg, tmp_path):
    from heroez_ops.config import _stages
    assert _stages("Lead, Nego, Deal, Batal") == ("Lead", "Nego", "Deal")
    assert _stages(["Lead"]) == cfg.deal_stages and _stages(None) == cfg.deal_stages


# ---------------------------------------------------------------- Google Sheet mirror
def test_rows_are_pushed_to_the_apps_script_and_debounced(cfg):
    posts = []

    def handler(request: httpx.Request):
        posts.append(json.loads(request.content))
        return httpx.Response(200, text="ok 1 rows")

    c = dataclasses.replace(cfg, deals_sheet_url="https://script.google.com/macros/s/abc/exec", deals_sheet_token="tok")
    store = Store(":memory:")
    deals = Deals(c, store, http=httpx.Client(transport=httpx.MockTransport(handler)), check_host=OK)
    did = deals.ensure_lead("628111", "Brand A", "rina", ts(T0), talents="Dewi", period=("2026-11-10", "2026-11-12"))
    wait_pushes()
    assert len(posts) == 1 and posts[0]["token"] == "tok" and posts[0]["reason"] == "lead"
    head, row = posts[0]["rows"][0], posts[0]["rows"][1]
    assert head[:9] == ["ID", "Brand", "Kontak", "PIC", "Talent", "Mulai", "Selesai", "Nilai (Rp)", "Tahap"]
    assert row[:9] == [did, "Brand A", "", "Rina", "Dewi", "2026-11-10", "2026-11-12", 0, "Lead"]
    assert head[9:16] == ["Lead", "Proposal", "Deal", "Kontrak", "Ditandatangani", "Lunas", "Batal"]
    assert row[9] == "2026-11-02" and row[-1] == "https://wa.me/628111"
    assert json.loads(store.get_kv("deals_sheet_status"))["ok"] is True
    # A second lead right after is debounced; a key event (Deal) is pushed at once.
    deals.ensure_lead("628222", "Brand B", None, ts(T0) + 10)
    wait_pushes()
    assert len(posts) == 1 and store.get_kv("deals_push_pending") == "1"
    deals.set_stage(did, "Deal", "rina", ts(T0) + 20)
    wait_pushes()
    assert len(posts) == 2 and posts[1]["reason"] == "Deal" and len(posts[1]["rows"]) == 3
    # The morning refresh pushes once per day.
    store.set_kv("deals_push_at", "0")
    deals.daily_refresh(wib(2026, 11, 3, 9, 0))
    deals.daily_refresh(wib(2026, 11, 3, 9, 30))
    wait_pushes()
    assert len(posts) == 3 and posts[2]["reason"] == "daily"


def test_non_apps_script_urls_are_refused(cfg):
    c = dataclasses.replace(cfg, deals_sheet_url="https://evil.example.com/exec", deals_sheet_token="tok")
    store = Store(":memory:")
    deals = Deals(c, store, http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))), check_host=OK)
    assert deals.push(force=True) is False
    assert json.loads(store.get_kv("deals_sheet_status"))["ok"] is False


# ---------------------------------------------------------------- digest
def test_digest_reports_the_pipeline(service):
    svc = service
    deals = Deals(svc.cfg, svc.store, check_host=OK)
    did = deals.ensure_lead("628111", "Brand A", "rina", ts(T0) - 3600)
    deals.set_stage(did, "Deal", "rina", ts(T0), value=30_000_000)
    deals.ensure_lead("628222", "Brand B", None, ts(T0))
    text = svc.digest.build(T0 - dt.timedelta(days=1), T0 + dt.timedelta(hours=2))
    assert "🤝 Deal: 2 lead baru kemarin · 1 deal 30 hari terakhir (Rp30.000.000)" in text
    assert "Pipeline: Lead 1 · Deal 1 (Rp30.000.000)" in text
