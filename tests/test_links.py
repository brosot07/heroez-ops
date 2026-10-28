import datetime as dt

import httpx
import pytest

from heroez_ops import fixtures
from heroez_ops.concierge_ai import Understanding
from heroez_ops.db import Store
from heroez_ops.links import LinkChecker, analyze, defang, extract

from .test_intake import T0, bot  # noqa: F401  (fixture)
from .conftest import ts


@pytest.mark.parametrize("url", ["https://wa-business-verify.xyz/login", "http://192.168.1.5/a", "https://bit.ly/3x",
                                 "https://faceb00k-security.com", "https://drive.google.com/f/x.apk",
                                 "https://xn--whtsapp-9za.com", "https://bca-hadiah.com/klaim", "https://user@evil.com",
                                 "https://instagram.com.login-check.top/a"])
def test_phishing_patterns_flagged(url):
    assert analyze(url).level == "suspicious"


@pytest.mark.parametrize("url", ["https://instagram.com/glowskin", "https://www.tiktok.com/@dewi", "https://wa.me/628123",
                                 "https://docs.google.com/document/d/1"])
def test_known_sites_ok(url):
    assert analyze(url).level == "ok"


@pytest.mark.parametrize("url", ["https://glowskin.co.id", "https://bridal-studio.com", "https://metallica.com",
                                 "https://kopikenangan.com/menu"])
def test_ordinary_sites_unknown_not_flagged(url):
    v = analyze(url)
    assert v.level == "unknown", v.reasons


def test_defang_and_extract():
    text = "Cek https://bit.ly/abc.d dan web glowskin.co.id ya"
    assert extract(text) == ["https://bit.ly/abc.d", "glowskin.co.id"]
    assert defang(text) == "Cek hxxps://bit[.]ly/abc.d dan web glowskin[.]co[.]id ya"


def test_urlhaus_lookup_never_visits_the_link():
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, json={"query_status": "ok", "threat": "malware_download"})
    store = Store(":memory:")
    c = LinkChecker(store, "KEY", httpx.Client(transport=httpx.MockTransport(handler)))
    v = c.check("https://glowskin-promo.com/a")
    assert v.level == "malicious"
    assert seen == ["https://urlhaus-api.abuse.ch/v1/url/"]          # only the database was contacted
    c.check("https://glowskin-promo.com/a")
    assert len(seen) == 1                                             # cached


def test_bot_never_engages_with_phishing_first_message(bot):          # noqa: F811
    msg = "Selamat! Akun WhatsApp Business anda akan diblokir, verifikasi di https://wa-business-verify.xyz/login"
    svc, wire, ch = bot({msg: Understanding(kind="brand", campaign="x")})
    svc.process_payload(fixtures.inbound("628131", msg, ts(T0)))
    assert wire.sent == []
    assert svc.intake.row("628131")["status"] == "spam"
    assert "wa-business-verify[.]xyz" in svc.intake.row("628131")["outcome"]


def test_alert_shows_defanged_link_and_warning(bot):                  # noqa: F811
    svc, _, _ = bot({})
    svc.engine.ai_enabled = False
    svc.process_payload(fixtures.history({"628132": [("out", "Halo kak", ts(T0) - 86400 * 40)]}))
    svc.process_payload(fixtures.inbound("628132", "Kak ini brief kami https://bit.ly/brief-glow", ts(T0)))
    text = svc.notifier.item_lines("628132", T0 + dt.timedelta(minutes=31), "nudge")
    assert "https://bit.ly" not in text and "hxxps://bit[.]ly/brief-glow" in text
    assert "⚠️ Link mencurigakan" in text and "Jangan dibuka" in text
