import dataclasses
import io

import openpyxl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from heroez_ops.db import Store
from heroez_ops.docs_ui import ContractsUI
from heroez_ops.kb import KB
from heroez_ops.kb_ui import example_roster_xlsx


@pytest.fixture
def client(cfg, tmp_path):
    ui = ContractsUI(tmp_path, Store(":memory:"), lambda: cfg)
    app = FastAPI()
    app.include_router(ui.router(local=True))
    return ui, TestClient(app)


def test_upload_kol_list_and_schedule_from_excel(client):
    ui, c = client
    r = c.post("/pengetahuan/kol", data={"csrf": ui.csrf}, files={"file": ("kol.xlsx", example_roster_xlsx())})
    assert r.status_code == 200 and "1 KOL tersimpan" in r.text
    page = c.get("/pengetahuan").text
    assert "Dewi Lestari" in page and "Rp15.000.000" in page
    assert "0812xxxxxxx" not in page and "tidak ditampilkan ke klien" not in page        # private columns hidden
    wb = openpyxl.Workbook()
    wb.active.append(["Nama", "Mulai", "Selesai", "Brand"])
    wb.active.append(["Dewi Lestari", "10/11/2026", "12/11/2026", "RahasiaCorp"])
    buf = io.BytesIO()
    wb.save(buf)
    r = c.post("/pengetahuan/jadwal", data={"csrf": ui.csrf}, files={"file": ("j.xlsx", buf.getvalue())})
    assert "1 periode jadwal tersimpan" in r.text
    assert "RahasiaCorp" not in str([dict(x) for x in ui.store.q("SELECT * FROM talent_busy")])


def test_faq_editor_and_exact_match(client):
    ui, c = client
    c.post("/pengetahuan/faq", data={"csrf": ui.csrf, "faq_id_0": "", "faq_q_0": "Alamat kantor Heroez di mana?\nkantornya dimana",
                                      "faq_a_0": "Kantor kami di Jl. Contoh No. 1, Jakarta 🙏", "faq_auto_0": "on"})
    kb = KB(ui.store)
    assert kb.match_faq("Kantornya dimana??") ["answer_id"].startswith("Kantor kami")
    assert kb.match_faq("kantornya dimana ya kak, sama rate Dewi?") is None                 # not exact: no auto answer


def test_csv_with_semicolons(client):
    ui, c = client
    csv_data = "Nama;Niche;Rate TikTok;No HP\nRaka;gaming;20jt;0813\n".encode()
    r = c.post("/pengetahuan/kol", data={"csrf": ui.csrf}, files={"file": ("kol.csv", csv_data)})
    assert "1 KOL tersimpan" in r.text
    assert "Rp20.000.000" in c.get("/pengetahuan").text


def test_ratecard_must_be_pdf(client, tmp_path):
    ui, c = client
    assert "harus PDF" in c.post("/pengetahuan/ratecard", data={"csrf": ui.csrf}, files={"file": ("x.pdf", b"hello")}).text
    c.post("/pengetahuan/ratecard", data={"csrf": ui.csrf}, files={"file": ("x.pdf", b"%PDF-1.4 test")})
    assert (tmp_path / "data" / "kb" / "ratecard.pdf").exists()
