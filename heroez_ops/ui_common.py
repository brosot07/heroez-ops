"""Shared look for the app's web pages (settings, contracts, templates)."""
from __future__ import annotations

import html

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2330;--muted:#5d6675;--line:#dde1e7;--accent:#1f6feb;--ok:#1a7f37;--bad:#cf222e;--hl:#fff3bf}
@media (prefers-color-scheme: dark){:root{--bg:#0f1218;--card:#171b23;--ink:#e6e9ef;--muted:#9aa3b2;--line:#2a303b;--accent:#4c8dff;--ok:#3fb950;--bad:#f85149;--hl:#4d3f00}}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,Segoe UI,sans-serif}
main{max-width:960px;margin:0 auto;padding:16px 16px 64px}
nav{display:flex;gap:4px;flex-wrap:wrap;align-items:center;border-bottom:1px solid var(--line);padding:10px 16px;background:var(--card)}
nav b{margin-right:12px}nav a{color:var(--ink);text-decoration:none;padding:6px 10px;border-radius:6px}
nav a.on{background:var(--bg);font-weight:600}nav .who{margin-left:auto;color:var(--muted);font-size:13px}
h1{font-size:22px;margin:8px 0 4px}h2{font-size:17px;margin:0 0 10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:16px 0}
label{display:block;font-weight:600;margin:10px 0 4px}small,.muted{color:var(--muted);font-weight:400}
input[type=text],input[type=password],input[type=number],input[type=time],input[type=date],input[type=email],select,textarea{width:100%;box-sizing:border-box;padding:8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink);font:inherit}
table{width:100%;border-collapse:collapse}td,th{padding:6px 4px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}@media(max-width:600px){.row{grid-template-columns:1fr}}
button,.btn{display:inline-block;background:var(--accent);color:#fff;border:0;border-radius:6px;padding:10px 16px;font-weight:600;cursor:pointer;text-decoration:none;font-size:14px}
button.secondary,.btn.secondary{background:transparent;color:var(--accent);border:1px solid var(--accent)}
button.danger{background:transparent;color:var(--bad);border:1px solid var(--bad)}
.msg{padding:10px 12px;border-radius:6px;margin:12px 0}.ok{background:color-mix(in srgb,var(--ok) 15%,transparent)}.bad{background:color-mix(in srgb,var(--bad) 15%,transparent)}
code{background:var(--bg);padding:2px 5px;border-radius:4px}
mark{background:var(--hl);color:inherit;padding:0 2px;border-radius:3px}
.doc{white-space:pre-wrap;font:14px/1.6 Georgia,serif;max-height:480px;overflow:auto;border:1px solid var(--line);border-radius:6px;padding:12px;background:var(--bg)}
.scroll{overflow-x:auto}
"""


def e(v) -> str:
    return html.escape("" if v is None else str(v))


def page(title: str, body: str, nav: list[tuple[str, str]] | None = None, active: str = "",
         who: str = "", message: str = "", ok: bool = True, logout: bool = False) -> str:
    links = "".join(f'<a href="{href}" class="{"on" if href == active else ""}">{e(label)}</a>' for label, href in nav or [])
    msg = f'<div class="msg {"ok" if ok else "bad"}">{e(message)}</div>' if message else ""
    return (f'<!doctype html><html lang="id"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)}</title>'
            f'<style>{CSS}</style></head><body><nav><b>Heroez Ops</b>{links}<span class="who">{e(who)}{" · <a href=/logout>Keluar</a>" if logout else ""}</span></nav>'
            f'<main>{msg}{body}</main></body></html>')
