"""HTTP entry point: Meta + Telegram webhooks and the health check.

Server deploy: uvicorn heroez_ops.app:create_app --factory --host 0.0.0.0 --port 8000
Windows install: started by heroez_ops.winmain (no uvicorn command needed).
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
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .config import Config, load_config
from .db import Store
from .service import Service

log = logging.getLogger("heroez_ops")


def start_background(service: Service, stop: threading.Event) -> list[threading.Thread]:
    """Ticker (every 30 s) and, in polling mode, the Telegram long-poll loop."""
    def ticker():
        while not stop.is_set():
            try:
                service.tick()
            except Exception:
                log.exception("tick failed")
            service.store.set_kv("last_tick_at", str(int(time.time())))
            stop.wait(30)

    def poller():
        if service.telegram:
            service.telegram.delete_webhook()  # polling and webhook can't both be active
        while not stop.is_set():
            try:
                service.poll_telegram_once(wait_seconds=20)
            except Exception:
                log.exception("telegram polling failed")
                stop.wait(10)

    threads = [threading.Thread(target=ticker, name="ticker", daemon=True)]
    if service.cfg.telegram_mode == "polling" and service.telegram:
        threads.append(threading.Thread(target=poller, name="telegram-poller", daemon=True))
    for t in threads:
        t.start()
    return threads


def create_app(cfg: Config | None = None, store: Store | None = None, service: Service | None = None,
               run_background: bool = True) -> FastAPI:
    cfg = cfg or load_config(os.environ.get("HEROEZ_CONFIG", "config/config.yaml"))
    store = store or Store(os.environ.get("HEROEZ_DB", "data/heroez.db"))
    service = service or Service(cfg, store, data_dir=Path(os.environ.get("HEROEZ_DB", "data/heroez.db")).parent)
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if run_background:
            start_background(service, stop)
        log.info("Heroez Ops started in %s mode", cfg.mode)
        yield
        stop.set()

    app = FastAPI(title="Heroez Ops", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service, app.state.store, app.state.cfg = service, store, cfg

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
                "seconds_since_last_webhook": (now - last_wh) if (last_wh := int(store.get_kv("last_webhook_at", "0") or 0)) else None}
        return JSONResponse(body, status_code=200 if ok else 503)

    return app
