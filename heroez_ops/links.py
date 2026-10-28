"""Links inside client messages: never opened, always checked, shown defanged.

The app never visits a link from a message. Checks look only at the address itself
(look-alike domains, IP addresses, shorteners, risky endings and downloads, login/OTP
bait) and, if an URLhaus key is set, ask the URLhaus database about it without
visiting it. Links are shown as hxxps://example[.]com so nobody opens one by accident.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from .db import Store

log = logging.getLogger(__name__)

URL_RE = re.compile(
    r"(?i)\b((?:https?://|www\.)[^\s<>\"'`]+|(?:[a-z0-9-]+\.)+(?:com|net|org|id|co|io|me|ly|gl|xyz|top|click|link|info|"
    r"site|online|shop|store|app|live|biz|vip|cc|tk|ml|ga|cf|gq|ru|cn|ws|pw|buzz|rest|icu|cam|sbs|cfd|lol|zip|mov|"
    r"my\.id|web\.id|co\.id|or\.id|ac\.id|go\.id)(?:/[^\s<>\"'`]*)?)")
SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "s.id", "cutt.ly", "rb.gy", "is.gd", "ow.ly", "shorturl.at", "lnk.bio",
              "linktr.ee", "tiny.cc", "rebrand.ly", "goo.gl", "t.ly", "v.gd", "bitly.com", "shorturl.asia"}
RISKY_TLDS = {"xyz", "top", "click", "link", "info", "site", "online", "live", "biz", "vip", "cc", "tk", "ml", "ga", "cf",
              "gq", "ru", "cn", "ws", "pw", "buzz", "rest", "icu", "cam", "sbs", "cfd", "lol", "zip", "mov", "shop", "store"}
# Brands people impersonate to steal WhatsApp/bank/social accounts, with their real domains.
BRANDS = {"whatsapp": ["whatsapp.com", "whatsapp.net", "wa.me"], "facebook": ["facebook.com", "fb.com", "fb.me"],
          "meta": ["meta.com", "facebook.com"], "instagram": ["instagram.com"], "tiktok": ["tiktok.com"],
          "google": ["google.com", "google.co.id", "youtube.com", "youtu.be"], "youtube": ["youtube.com", "youtu.be"],
          "telegram": ["telegram.org", "t.me"], "bca": ["bca.co.id", "klikbca.com"], "bri": ["bri.co.id"],
          "mandiri": ["bankmandiri.co.id", "livin.id"], "bni": ["bni.co.id"], "dana": ["dana.id"], "ovo": ["ovo.id"],
          "gopay": ["gojek.com", "gopay.co.id"], "shopee": ["shopee.co.id", "shopee.com"], "tokopedia": ["tokopedia.com"],
          "heroez": []}
BAIT = re.compile(r"(login|log-in|signin|sign-in|verify|verifikasi|verification|confirm|konfirmasi|otp|password|"
                  r"akun|account|secure|security|update|unlock|blokir|suspend|hadiah|prize|reward|claim|klaim|gratis|bonus)", re.I)
DOWNLOADS = re.compile(r"\.(apk|exe|msi|scr|bat|cmd|js|vbs|jar|dmg|pkg|iso|zip|rar|7z)(?:$|[?#])", re.I)
SAFE = {"instagram.com", "tiktok.com", "youtube.com", "youtu.be", "facebook.com", "wa.me", "whatsapp.com",
        "google.com", "docs.google.com", "drive.google.com", "linkedin.com", "x.com", "twitter.com", "shopee.co.id",
        "tokopedia.com", "canva.com", "notion.so", "dropbox.com"}
LEET = str.maketrans({"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})

LEVEL_RANK = {"ok": 0, "unknown": 1, "suspicious": 2, "malicious": 3}


@dataclass
class LinkVerdict:
    url: str
    host: str
    level: str                       # ok | unknown | suspicious | malicious
    reasons: list[str] = field(default_factory=list)

    @property
    def shown(self) -> str:
        return defang(self.url)


def extract(text: str) -> list[str]:
    seen = []
    for m in URL_RE.finditer(text or ""):
        u = m.group(1).rstrip(".,;:!?)]}")
        if u not in seen:
            seen.append(u)
    return seen[:10]


def defang(text: str) -> str:
    """hxxps://bit[.]ly/abc: readable, not clickable (dots in the host only)."""
    def one(m):
        u = m.group(1)
        scheme, sep, rest = u.partition("://")
        if not sep:
            scheme, rest = "", u
        host, slash, path = rest.partition("/")
        host = host.replace(".", "[.]")
        scheme = re.sub(r"(?i)^http", "hxxp", scheme)
        return (scheme + "://" if sep else "") + host + slash + path
    return URL_RE.sub(one, text or "")


def _registered(host: str) -> str:
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "or", "ac", "go", "my", "web", "sch", "net", "com") and parts[-1] in ("id", "uk", "au", "sg", "my"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def analyze(url: str) -> LinkVerdict:
    """Judge a link by its address only. Never fetches it."""
    raw = url if re.match(r"(?i)^https?://", url) else "http://" + url
    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return LinkVerdict(url, "", "suspicious", ["alamat link tidak normal"])
    reasons: list[str] = []
    level = "unknown"
    reg = _registered(host)
    if not host:
        return LinkVerdict(url, host, "suspicious", ["alamat link tidak normal"])
    if parts.username or "@" in parts.netloc:
        reasons.append("ada “@” di alamat (trik menyamarkan tujuan)")
    try:
        ipaddress.ip_address(host)
        reasons.append("pakai alamat IP, bukan nama situs")
    except ValueError:
        pass
    if host.startswith("xn--") or ".xn--" in host or any(ord(ch) > 127 for ch in host):
        reasons.append("huruf asing yang mirip huruf biasa (punycode)")
    if reg in SHORTENERS or host in SHORTENERS:
        reasons.append("link pemendek: tujuan aslinya tersembunyi")
    tld = host.rsplit(".", 1)[-1]
    if tld in RISKY_TLDS:
        reasons.append(f"akhiran .{tld} sering dipakai penipuan")
    if DOWNLOADS.search(parts.path or ""):
        reasons.append("mengunduh file program/arsip")
    plain = unicodedata.normalize("NFKD", host).translate(LEET)
    tokens = set(re.split(r"[.-]", plain))
    for brand, real in BRANDS.items():
        # Short names (bca, bri, ovo...) must be a whole word of the host, longer ones may be embedded.
        hit = brand in tokens if len(brand) <= 4 else brand in plain.replace("-", "").replace(".", "")
        if hit and reg not in real and host not in real:
            reasons.append(f"meniru nama {brand} tapi bukan situs resminya")
            break
    if BAIT.search(host) or (BAIT.search(parts.path or "") and reasons):
        reasons.append("berisi kata pancingan (login/verifikasi/OTP/hadiah)")
    if host.count("-") >= 3 or len(host) > 50:
        reasons.append("nama situs panjang/aneh")
    if reasons:
        level = "suspicious"
    elif reg in SAFE or host in SAFE:
        level = "ok"
    return LinkVerdict(url, host, level, reasons)


class LinkChecker:
    """Heuristics always; URLhaus lookup (no visit) when an Auth-Key is configured. Results cached a day."""

    def __init__(self, store: Store, urlhaus_key: str = "", http: httpx.Client | None = None):
        self.store, self.key = store, urlhaus_key
        self.http = http or httpx.Client(timeout=8)

    def check(self, url: str) -> LinkVerdict:
        v = analyze(url)
        if not self.key or v.level == "ok":
            return v
        h = hashlib.sha256(url.encode()).hexdigest()
        cached = self.store.one("SELECT verdict, checked_at FROM link_checks WHERE url_hash=?", (h,))
        if cached and cached["checked_at"] > time.time() - 86400:
            status = cached["verdict"]
        else:
            status = self._urlhaus(url)
            self.store.x("INSERT INTO link_checks(url_hash,verdict,checked_at) VALUES(?,?,?) ON CONFLICT(url_hash) "
                         "DO UPDATE SET verdict=excluded.verdict, checked_at=excluded.checked_at", (h, status, int(time.time())))
        if status == "listed":
            v.level, v.reasons = "malicious", ["terdaftar di database link berbahaya (URLhaus)"] + v.reasons
        return v

    def _urlhaus(self, url: str) -> str:
        try:
            r = self.http.post("https://urlhaus-api.abuse.ch/v1/url/", data={"url": url}, headers={"Auth-Key": self.key})
            status = r.json().get("query_status")
        except (httpx.HTTPError, ValueError):
            return "error"
        return "listed" if status == "ok" else "clean" if status == "no_results" else "error"

    def check_message(self, msg_id: str, wa_id: str, text: str) -> list[LinkVerdict]:
        verdicts = [self.check(u) for u in extract(text)]
        for v in verdicts:
            self.store.x("INSERT OR IGNORE INTO message_links(msg_id,wa_id,shown,host,level,reasons) VALUES(?,?,?,?,?,?)",
                         (msg_id, wa_id, v.shown, v.host, v.level, json.dumps(v.reasons, ensure_ascii=False)))
        if any(v.level in ("suspicious", "malicious") for v in verdicts):
            self.store.audit("system", "link_warning", wa_id, {"links": [v.shown for v in verdicts if v.level != "ok"]})
        return verdicts


def worst(verdicts: list[LinkVerdict]) -> str:
    return max((v.level for v in verdicts), key=lambda lv: LEVEL_RANK[lv], default="ok")


def alert_line(store: Store, msg_id: str) -> str:
    """One Telegram line about the links in a message ('' if none)."""
    rows = store.q("SELECT shown, level, reasons FROM message_links WHERE msg_id=?", (msg_id,))
    if not rows:
        return ""
    bad = [r for r in rows if r["level"] in ("suspicious", "malicious")]
    if bad:
        r = max(bad, key=lambda x: LEVEL_RANK[x["level"]])
        why = "; ".join(json.loads(r["reasons"])[:2])
        tag = "⛔ Link berbahaya" if r["level"] == "malicious" else "⚠️ Link mencurigakan"
        return f"{tag}: {r['shown']} ({why}). Jangan dibuka, jangan isi data apa pun."
    unknown = [r["shown"] for r in rows if r["level"] == "unknown"]
    if unknown:
        return f"🔗 Link belum dikenal: {', '.join(unknown[:2])}. Buka hanya jika yakin."
    return ""
