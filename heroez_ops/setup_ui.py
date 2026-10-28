"""Local settings page (Bahasa Indonesia) at http://127.0.0.1:8788.

Bound to 127.0.0.1 only and never exposed through the tunnel. Lets a
non-technical owner enter Telegram IDs, tokens and business hours without
touching files. Saving validates the config, writes it, then restarts the service.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import secrets as pysecrets
import shutil
import tempfile
import time
from pathlib import Path
from typing import Callable

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import secrets as secretstore
from .ai import month_cost
from .alerts.telegram import TelegramAlertChannel
from .config import ConfigError, load_config
from .db import Store

MAX_ADMINS, MAX_SPV = 6, 4
DAYS = ["Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"]

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2330;--muted:#5d6675;--line:#dde1e7;--accent:#1f6feb;--ok:#1a7f37;--bad:#cf222e}
@media (prefers-color-scheme: dark){:root{--bg:#0f1218;--card:#171b23;--ink:#e6e9ef;--muted:#9aa3b2;--line:#2a303b;--accent:#4c8dff;--ok:#3fb950;--bad:#f85149}}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,Segoe UI,sans-serif}
main{max-width:860px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:0 0 10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:16px 0}
label{display:block;font-weight:600;margin:10px 0 4px}small,.muted{color:var(--muted);font-weight:400}
input[type=text],input[type=password],input[type=number],input[type=time]{width:100%;box-sizing:border-box;padding:8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)}
table{width:100%;border-collapse:collapse}td,th{padding:6px 4px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}@media(max-width:600px){.row{grid-template-columns:1fr}}
button{background:var(--accent);color:#fff;border:0;border-radius:6px;padding:10px 16px;font-weight:600;cursor:pointer}
button.secondary{background:transparent;color:var(--accent);border:1px solid var(--accent)}
.msg{padding:10px 12px;border-radius:6px;margin:12px 0}.ok{background:color-mix(in srgb,var(--ok) 15%,transparent)}.bad{background:color-mix(in srgb,var(--bad) 15%,transparent)}
code{background:var(--bg);padding:2px 5px;border-radius:4px}
"""


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


class SetupUI:
    def __init__(self, home: Path, store: Store, request_restart: Callable[[], None],
                 status: Callable[[], dict] | None = None, port: int = 8788):
        self.home, self.store, self.request_restart = home, store, request_restart
        self.config_path = home / "config" / "config.yaml"
        self.secrets_path = home / "secrets.bin"
        self.status = status or (lambda: {})
        self.port = port
        self.csrf = pysecrets.token_urlsafe(24)

    # -- data -----------------------------------------------------------
    def raw(self) -> dict:
        return yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}

    def holidays(self) -> list[str]:
        p = self.home / "config" / "holidays.yaml"
        raw = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}
        return sorted(str(d) for d in raw.get("holidays") or [])

    @staticmethod
    def parse_holidays(text: str) -> list[str]:
        out = []
        for line in text.replace(",", "\n").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(dt.date.fromisoformat(line).isoformat())
            except ValueError as e:
                raise ConfigError(f"Tanggal merah tidak valid: {line!r} (pakai format 2027-03-10)") from e
        return sorted(set(out))

    def secrets(self) -> dict:
        return secretstore.load(self.secrets_path)

    def apply_form(self, raw: dict, form: dict, old_secrets: dict) -> tuple[dict, dict]:
        """Merge the submitted form into the config dict and secrets dict."""
        raw = json.loads(json.dumps(raw, default=str))  # deep copy, dates as strings
        g = lambda k, d="": (form.get(k) or d).strip()  # noqa: E731
        num = lambda k: int(g(k, "0") or 0) if g(k, "0").lstrip("-").isdigit() else 0  # noqa: E731

        raw["mode"] = "live" if g("mode") == "live" else "shadow"
        raw.setdefault("whatsapp", {})["business_number"] = "".join(ch for ch in g("business_number") if ch.isdigit())
        raw.setdefault("server", {})["public_hostname"] = g("public_hostname").removeprefix("https://").strip("/")

        spvs = []
        for j in range(1, MAX_SPV + 1):
            if g(f"s{j}_name"):
                spvs.append({"key": f"spv{j}", "name": g(f"s{j}_name"), "telegram_id": num(f"s{j}_tg")})
        spv_keys = {s["key"] for s in spvs}
        admins = []
        for i in range(1, MAX_ADMINS + 1):
            if g(f"a{i}_name"):
                own = [f"spv{j}" for j in range(1, MAX_SPV + 1) if form.get(f"a{i}_spv{j}") and f"spv{j}" in spv_keys]
                admins.append({"key": f"admin{i}", "name": g(f"a{i}_name"), "telegram_id": num(f"a{i}_tg"), "supervisors": own})
        raw["admins"], raw["supervisors"] = admins, spvs
        raw["on_call_rotation"] = [a["key"] for a in admins]

        tg = raw.setdefault("telegram", {})
        tg["mode"] = "polling"
        tg["admin_group_id"], tg["management_group_id"] = num("admin_group_id"), num("management_group_id")

        bh = raw.setdefault("business_hours", {})
        bh["days"] = [d for d in range(7) if form.get(f"day{d}")]
        bh["start"], bh["end"] = g("start", "09:30"), g("end", "21:00")
        bh.setdefault("holidays_file", "config/holidays.yaml")
        wh = raw.setdefault("work_hours", {})
        wh.setdefault("days", [0, 1, 2, 3, 4])
        wh["start"], wh["end"] = bh["start"], g("work_end", "18:30")

        raw.setdefault("ai", {})["enabled"] = bool(form.get("ai_enabled"))

        esc = raw.setdefault("escalation", {})
        for k, field in (("nudge", "nudge_assignee_after_business_min"), ("group", "alert_group_after_business_min"),
                         ("urgent", "urgent_after_real_hours"), ("repeat", "repeat_every_business_min")):
            if g(k).isdigit():
                esc[field] = int(g(k))
        wk = raw.setdefault("weekend", {})
        wk["days"] = [d for d in range(7) if form.get(f"wkd{d}")]
        if g("weekend_hours").isdigit():
            wk["min_minutes_between_messages_per_recipient"] = max(1, int(g("weekend_hours"))) * 60
        if g("max_per").isdigit():
            raw.setdefault("alerts", {})["min_minutes_between_messages_per_recipient"] = max(1, int(g("max_per")))

        new_secrets = dict(old_secrets)
        for field, key in (("telegram_bot_token", "TELEGRAM_BOT_TOKEN"), ("meta_app_secret", "META_APP_SECRET"),
                           ("tunnel_token", "CLOUDFLARE_TUNNEL_TOKEN"), ("anthropic_api_key", "ANTHROPIC_API_KEY")):
            if g(field):                      # blank = keep the existing value
                new_secrets[key] = g(field)
            if form.get(f"clear_{field}"):
                new_secrets.pop(key, None)
        new_secrets.setdefault("META_VERIFY_TOKEN", pysecrets.token_urlsafe(24))
        return raw, new_secrets

    def validate(self, raw: dict) -> None:
        """Raise ConfigError if this config would not load."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "config").mkdir()
            src_h = self.home / "config" / "holidays.yaml"
            if src_h.exists():
                shutil.copy(src_h, Path(tmp) / "config" / "holidays.yaml")
            p = Path(tmp) / "config" / "config.yaml"
            p.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
            load_config(p)

    def save(self, raw: dict, new_secrets: dict) -> None:
        self.validate(raw)
        backup = self.config_path.with_suffix(f".{int(time.time())}.bak")
        shutil.copy(self.config_path, backup)
        self.config_path.write_text(
            "# Edited from the Heroez Ops settings page.\n" + yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8")
        secretstore.save(self.secrets_path, new_secrets)

    # -- page -------------------------------------------------------------
    def page(self, message: str = "", ok: bool = True) -> str:
        raw, sec, st = self.raw(), self.secrets(), self.status()
        admins = {a["key"]: a for a in raw.get("admins") or []}
        spvs = {s["key"]: s for s in raw.get("supervisors") or []}
        tg = raw.get("telegram") or {}
        bh = raw.get("business_hours") or {}
        esc = raw.get("escalation") or {}
        host = (raw.get("server") or {}).get("public_hostname", "")
        newcomers = json.loads(self.store.get_kv("tg_newcomers", "[]"))

        def ago(ts: int) -> str:
            if not ts:
                return "belum pernah"
            s = int(time.time()) - ts
            return f"{s} detik lalu" if s < 120 else f"{s // 60} menit lalu" if s < 7200 else f"{s // 3600} jam lalu"

        spv_head = "".join(f"<th>{_e((spvs.get(f'spv{j}') or {}).get('name') or f'Supervisor {j}')}</th>" for j in range(1, MAX_SPV + 1)
                           if f"spv{j}" in spvs)
        admin_rows = ""
        for i in range(1, MAX_ADMINS + 1):
            a = admins.get(f"admin{i}") or {}
            checks = "".join(
                f'<td><input type="checkbox" name="a{i}_spv{j}" {"checked" if f"spv{j}" in (a.get("supervisors") or []) else ""}></td>'
                for j in range(1, MAX_SPV + 1) if f"spv{j}" in spvs)
            admin_rows += (f'<tr><td><input type="text" name="a{i}_name" value="{_e(a.get("name"))}" placeholder="Nama admin {i}"></td>'
                           f'<td><input type="text" name="a{i}_tg" value="{_e(a.get("telegram_id") or "")}" placeholder="ID Telegram"></td>{checks}</tr>')
        spv_rows = "".join(
            f'<tr><td><input type="text" name="s{j}_name" value="{_e((spvs.get(f"spv{j}") or {}).get("name"))}" placeholder="Nama supervisor {j}"></td>'
            f'<td><input type="text" name="s{j}_tg" value="{_e((spvs.get(f"spv{j}") or {}).get("telegram_id") or "")}" placeholder="ID Telegram"></td></tr>'
            for j in range(1, MAX_SPV + 1))
        days = "".join(f'<label style="display:inline;font-weight:400;margin-right:10px"><input type="checkbox" name="day{d}" '
                       f'{"checked" if d in (bh.get("days") or []) else ""}> {DAYS[d]}</label>' for d in range(7))
        wk_days = (raw.get("weekend") or {}).get("days") or []
        holidays_text = "\n".join(self.holidays())
        weekend_days = "".join(f'<label style="display:inline;font-weight:400;margin-right:10px"><input type="checkbox" name="wkd{d}" '
                               f'{"checked" if d in wk_days else ""}> {DAYS[d]}</label>' for d in range(7))
        newcomer_html = "".join(f"<li>{_e(u['name'])} {('@' + _e(u['username'])) if u.get('username') else ''}: <code>{_e(u['id'])}</code></li>"
                                for u in newcomers) or "<li class='muted'>Belum ada. Minta orangnya kirim /start ke bot.</li>"
        msg = f'<div class="msg {"ok" if ok else "bad"}">{_e(message)}</div>' if message else ""
        has = lambda k: "✅ tersimpan" if sec.get(k) else "❌ belum diisi"  # noqa: E731
        webhook_url = f"https://{_e(host)}/webhook/meta" if host else "(isi alamat publik dulu)"
        ai_cfg = raw.get("ai") or {}
        calls, usd = month_cost(self.store)
        if not sec.get("ANTHROPIC_API_KEY"):
            ai_status = "mati (API key belum diisi), hanya aturan kata"
        elif not ai_cfg.get("enabled", True):
            ai_status = "dimatikan"
        else:
            ai_status = f"aktif · bulan ini {calls} pesan dicek · ±US${usd:.2f}"
        if self.store.get_kv("ai_last_error") and sec.get("ANTHROPIC_API_KEY"):
            err = json.loads(self.store.get_kv("ai_last_error"))
            if err.get("kind") == "auth" and time.time() - err.get("at", 0) < 86400:
                ai_status += " · ⚠️ API key ditolak, cek lagi"

        return f"""<!doctype html><html lang="id"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Heroez Ops Settings</title><style>{CSS}</style></head><body><main>
<h1>Heroez Ops: Pengaturan</h1><p class="muted">Halaman ini hanya bisa dibuka dari komputer ini.</p>{msg}
<div class="card"><h2>Status</h2>
<p>Mode: <b>{"LIVE (notifikasi dikirim)" if raw.get("mode") == "live" else "SHADOW (hanya dicatat, belum dikirim)"}</b><br>
Pesan WhatsApp terakhir masuk: <b>{ago(st.get("last_webhook_at", 0))}</b><br>
Sistem terakhir aktif: <b>{ago(st.get("last_tick_at", 0))}</b><br>
Tunnel (alamat publik): <b>{_e(st.get("tunnel", "tidak dipakai"))}</b><br>
AI (Claude): <b>{ai_status}</b></p>
<p>Isi ini di Meta (Webhook):<br>Callback URL: <code>{webhook_url}</code><br>Verify token: <code>{_e(sec.get("META_VERIFY_TOKEN", "(muncul setelah simpan pertama)"))}</code></p></div>

<form method="post" action="/save"><input type="hidden" name="csrf" value="{self.csrf}">
<div class="card"><h2>1. Mode</h2>
<label style="font-weight:400"><input type="radio" name="mode" value="shadow" {"checked" if raw.get("mode") != "live" else ""}> Shadow: hitung dan catat saja (minggu pertama)</label>
<label style="font-weight:400"><input type="radio" name="mode" value="live" {"checked" if raw.get("mode") == "live" else ""}> Live: kirim notifikasi Telegram</label></div>

<div class="card"><h2>2. Telegram</h2>
<label>Token bot <small>(dari @BotFather) · {has("TELEGRAM_BOT_TOKEN")} · kosongkan untuk tidak mengubah</small></label>
<input type="password" name="telegram_bot_token" autocomplete="off">
<p class="muted">Orang yang baru kirim /start ke bot (salin ID-nya ke tabel di bawah):</p><ul>{newcomer_html}</ul></div>

<div class="card"><h2>3. Supervisor</h2><table><tr><th>Nama</th><th>ID Telegram</th></tr>{spv_rows}</table>
<p class="muted">Simpan dulu setelah menambah supervisor baru, lalu centang supervisor untuk tiap admin.</p></div>

<div class="card"><h2>4. Admin</h2><p class="muted">Centang supervisor yang ikut menerima eskalasi admin itu. Tidak dicentang = semua supervisor.</p>
<table><tr><th>Nama</th><th>ID Telegram</th>{spv_head}</tr>{admin_rows}</table></div>

<div class="card"><h2>5. Jam kerja & pengingat</h2>{days}
<div class="row"><div><label>Mulai kerja & notifikasi</label><input type="time" name="start" value="{_e(bh.get("start", "09:30"))}"></div>
<div><label>Notifikasi berhenti</label><input type="time" name="end" value="{_e(bh.get("end", "21:00"))}"></div></div>
<label>Jam kerja selesai <small>(untuk mengukur waktu balas, Senin–Jumat)</small></label><input type="time" name="work_end" value="{_e((raw.get("work_hours") or {}).get("end", "18:30"))}">
<label>Tanggal merah <small>(satu per baris, format 2027-03-10; dihitung seperti akhir pekan)</small></label>
<textarea name="holidays" rows="6" style="width:100%;box-sizing:border-box;padding:8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)">{_e(holidays_text)}</textarea>
<div class="row"><div><label>Ingatkan admin setelah <small>(menit jam kerja)</small></label><input type="number" name="nudge" value="{_e(esc.get("nudge_assignee_after_business_min", 30))}"></div>
<div><label>Libatkan supervisor setelah <small>(menit jam kerja)</small></label><input type="number" name="group" value="{_e(esc.get("alert_group_after_business_min", 120))}"></div></div>
<div class="row"><div><label>Ulangi pengingat tiap <small>(menit jam kerja, 0 = tidak)</small></label><input type="number" name="repeat" value="{_e(esc.get("repeat_every_business_min", 60))}"></div>
<div><label>Maksimal 1 notifikasi per orang tiap <small>(menit)</small></label><input type="number" name="max_per" value="{_e((raw.get("alerts") or {}).get("min_minutes_between_messages_per_recipient", 60))}"></div></div>
<label>Akhir pekan <small>(hari dengan notifikasi lebih jarang)</small></label>{weekend_days}
<label>Akhir pekan: maksimal 1 notifikasi per orang tiap <small>(jam)</small></label><input type="number" name="weekend_hours" value="{_e(int((raw.get("weekend") or {}).get("min_minutes_between_messages_per_recipient", 360)) // 60)}">
<label>Peringatan urgent setelah <small>(jam, sejak pesan klien)</small></label><input type="number" name="urgent" value="{_e(esc.get("urgent_after_real_hours", 18))}">
<div class="row"><div><label>ID grup admin <small>(opsional)</small></label><input type="text" name="admin_group_id" value="{_e(tg.get("admin_group_id") or "")}"></div>
<div><label>ID grup manajemen <small>(opsional)</small></label><input type="text" name="management_group_id" value="{_e(tg.get("management_group_id") or "")}"></div></div></div>

<div class="card"><h2>6. AI (Claude)</h2>
<p class="muted">AI membaca pesan terakhir klien dan menentukan: pertanyaan, permintaan, atau penutup.
Chat yang ditutup klien ("baik kak, kami review dulu") tidak dikirimi pengingat. Biaya kira-kira US$1–5 per bulan.</p>
<label style="font-weight:400"><input type="checkbox" name="ai_enabled" {"checked" if (raw.get("ai") or {}).get("enabled", True) else ""}> Pakai AI untuk mengecek pesan</label>
<label>Claude API key <small>(console.anthropic.com → API keys) · {has("ANTHROPIC_API_KEY")}</small></label>
<input type="password" name="anthropic_api_key" autocomplete="off"></div>

<div class="card"><h2>7. WhatsApp & koneksi</h2>
<label>Nomor WhatsApp Heroez <small>(dengan kode negara, mis. 62812…)</small></label><input type="text" name="business_number" value="{_e((raw.get("whatsapp") or {}).get("business_number", ""))}">
<label>Meta App Secret <small>· {has("META_APP_SECRET")}</small></label><input type="password" name="meta_app_secret" autocomplete="off">
<label>Alamat publik <small>(hostname Cloudflare Tunnel, mis. ops.heroez.id)</small></label><input type="text" name="public_hostname" value="{_e(host)}">
<label>Cloudflare Tunnel token <small>· {has("CLOUDFLARE_TUNNEL_TOKEN")}</small></label><input type="password" name="tunnel_token" autocomplete="off"></div>

<button type="submit">Simpan & mulai ulang</button></form>
<form method="post" action="/test-telegram" style="margin-top:12px"><input type="hidden" name="csrf" value="{self.csrf}">
<button class="secondary" type="submit">Kirim pesan tes ke semua admin & supervisor</button></form>
</main></body></html>"""

    def send_test(self) -> tuple[bool, str]:
        token = self.secrets().get("TELEGRAM_BOT_TOKEN")
        if not token:
            return False, "Token bot Telegram belum diisi."
        ch = TelegramAlertChannel(token)
        if not ch.get_me():
            return False, "Token bot Telegram salah atau tidak ada internet."
        raw = self.raw()
        people = [(p.get("name"), int(p.get("telegram_id") or 0)) for p in (raw.get("admins") or []) + (raw.get("supervisors") or [])]
        results = []
        for name, tid in people:
            if not tid:
                results.append(f"{name}: ID kosong")
                continue
            ok = ch.send(tid, f"✅ Tes dari Heroez Ops: notifikasi untuk {html.escape(name)} berhasil.") is not None
            results.append(f"{name}: {'terkirim' if ok else 'GAGAL (sudah kirim /start ke bot?)'}")
        return all(r.endswith("terkirim") for r in results), " · ".join(results) or "Belum ada admin/supervisor."

    # -- app ------------------------------------------------------------
    def create_app(self) -> FastAPI:
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        allowed_hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}", "127.0.0.1", "localhost", "testserver"}

        def guard(request: Request) -> None:
            # Blocks DNS-rebinding: the page must be opened as 127.0.0.1/localhost.
            if request.headers.get("host", "") not in allowed_hosts:
                raise HTTPException(403)

        async def form_of(request: Request) -> dict:
            from urllib.parse import parse_qs
            body = (await request.body()).decode("utf-8")
            form = {k: v[-1] for k, v in parse_qs(body, keep_blank_values=True).items()}
            if not pysecrets.compare_digest(form.get("csrf", ""), self.csrf):
                raise HTTPException(403, "Halaman kedaluwarsa, buka ulang.")
            return form

        @app.get("/", response_class=HTMLResponse)
        def index(request: Request):
            guard(request)
            return self.page()

        @app.post("/save", response_class=HTMLResponse)
        async def save(request: Request):
            guard(request)
            form = await form_of(request)
            raw, sec = self.apply_form(self.raw(), form, self.secrets())
            try:
                hol = self.parse_holidays(form.get("holidays", "")) if "holidays" in form else None
                self.save(raw, sec)
                if hol is not None:
                    (self.home / "config" / "holidays.yaml").write_text(
                        "# Public holidays and cuti bersama (edited from the settings page).\n"
                        + yaml.safe_dump({"holidays": hol}), encoding="utf-8")
            except ConfigError as e:
                return self.page(f"Belum tersimpan: {e}", ok=False)
            self.request_restart()
            return self.page("Tersimpan. Layanan dimulai ulang; muat ulang halaman ini dalam beberapa detik.")

        @app.post("/test-telegram", response_class=HTMLResponse)
        async def test(request: Request):
            guard(request)
            await form_of(request)
            ok, text = self.send_test()
            return self.page(text, ok=ok)

        return app
