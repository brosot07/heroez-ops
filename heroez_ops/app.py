"""HTTP entry point: Meta + Telegram webhooks, health check, background ticker.

Run: uvicorn heroez_ops.app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import datetime as dt
import hmac
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .config import load_config
from .db import Store
from .service import Service

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("heroez_ops")

cfg = load_config(os.environ.get("HEROEZ_CONFIG", "config/config.yaml"))
store = Store(os.environ.get("HEROEZ_DB", "data/heroez.db"))
service = Service(cfg, store)
_stop = threading.Event()


def _ticker() -> None:
    while not _stop.is_set():
        try:
            service.tick()
        except Exception:
            log.exception("tick failed")
        store.set_kv("last_tick_at", str(int(time.time())))
        _stop.wait(30)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    t = threading.Thread(target=_ticker, name="ticker", daemon=True)
    t.start()
    log.info("Heroez Ops started in %s mode", cfg.mode)
    yield
    _stop.set()


app = FastAPI(title="Heroez Ops", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.get("/webhook/meta")
def meta_verify(request: Request):
    p = request.query_params
    if p.get("hub.mode") == "subscribe" and cfg.meta_verify_token and hmac.compare_digest(
        p.get("hub.verify_token", ""), cfg.meta_verify_token
    ):
        return PlainTextResponse(p.get("hub.challenge", ""))
    raise HTTPException(403)


@app.post("/webhook/meta")
async def meta_webhook(request: Request):
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    if not service.source.verify_signature(body, headers):
        log.warning("rejected Meta webhook with bad signature")
        raise HTTPException(403)
    service.accept_webhook("meta", body)   # durable store, then ack fast
    return {"ok": True}


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    secret = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not cfg.telegram_webhook_secret or not hmac.compare_digest(secret, cfg.telegram_webhook_secret):
        raise HTTPException(403)
    update = json.loads(await request.body())
    try:
        service.bot.handle_update(update, dt.datetime.now(dt.timezone.utc))
    except Exception:
        log.exception("telegram update failed")
    return {"ok": True}


@app.get("/health")
def health():
    """For the external uptime monitor. 503 if the background ticker is stuck."""
    now = int(time.time())
    last_tick = int(store.get_kv("last_tick_at", "0") or 0)
    pending = store.one("SELECT COUNT(*) AS n FROM inbox WHERE processed_at IS NULL")["n"]
    ok = now - last_tick < 180
    body = {"ok": ok, "mode": cfg.mode, "seconds_since_tick": now - last_tick, "pending_webhooks": pending,
            "seconds_since_last_webhook": now - int(store.get_kv("last_webhook_at", "0") or 0)}
    return JSONResponse(body, status_code=200 if ok else 503)
