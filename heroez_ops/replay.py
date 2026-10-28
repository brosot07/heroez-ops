"""Replay historical traffic through the pipeline and report which alerts would fire.

Input: a JSONL file, one event per line, in time order. Either a raw Meta webhook:
    {"at": "2026-11-02T10:15:00+07:00", "payload": {...}}
or the short form:
    {"at": "2026-11-02T10:15:00+07:00", "wa_id": "62812...", "dir": "in", "text": "Halo kak", "name": "Brand X"}
    {"at": "...", "wa_id": "62812...", "dir": "out", "text": "Halo, boleh info budgetnya?"}

Usage:
    python -m heroez_ops.replay traffic.jsonl [--until 2026-11-03T09:00+07:00] [--config config/config.yaml]
Nothing is sent: alerts are printed and written to the shadow log.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

from . import fixtures
from .alerts.telegram import ShadowAlertChannel
from .config import load_config
from .db import Store
from .service import Service


def _parse_time(s: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(s)
    if t.tzinfo is None:
        raise SystemExit(f"time {s!r} needs a timezone, e.g. +07:00")
    return t


def load_events(path: Path) -> list[tuple[dt.datetime, dict]]:
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        rec = json.loads(line)
        at = _parse_time(rec["at"])
        if "payload" in rec:
            payload = rec["payload"]
        elif rec.get("dir") == "out":
            payload = fixtures.echo(rec["wa_id"], rec.get("text", ""), int(at.timestamp()))
        elif rec.get("dir") == "in":
            payload = fixtures.inbound(rec["wa_id"], rec.get("text", ""), int(at.timestamp()),
                                       name=rec.get("name", "Klien"), msg_type=rec.get("type", "text"))
        else:
            raise SystemExit(f"line {n}: needs 'payload' or 'dir' in/out")
        out.append((at, payload))
    out.sort(key=lambda x: x[0])
    return out


def replay(events: list[tuple[dt.datetime, dict]], service: Service, until: dt.datetime | None = None,
           step_minutes: int = 1) -> None:
    if not events:
        return
    t = events[0][0]
    end = until or events[-1][0] + dt.timedelta(hours=1)
    i = 0
    while t <= end:
        while i < len(events) and events[i][0] <= t:
            service.process_payload(events[i][1])
            i += 1
        service.notifier.tick(t)
        t += dt.timedelta(minutes=step_minutes)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("traffic", type=Path)
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--until", help="keep the clock running until this time (ISO with +07:00)")
    ap.add_argument("--out", default="data/replay_alerts.jsonl")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    Path(args.out).unlink(missing_ok=True)
    channel = ShadowAlertChannel(args.out)
    with tempfile.TemporaryDirectory() as tmp:
        service = Service(cfg, Store(Path(tmp) / "replay.db"), alert_channel=channel, reply_channel=channel)
        clock_log: list[tuple[str, dict]] = []
        orig_tick = service.notifier.tick

        def tick(now):
            before = len(channel.sent)
            orig_tick(now)
            for rec in channel.sent[before:]:
                clock_log.append((now.astimezone(cfg.tz).strftime("%a %d %b %H:%M"), rec))
        service.notifier.tick = tick
        replay(load_events(args.traffic), service, _parse_time(args.until) if args.until else None)

        states = service.store.q("SELECT state, COUNT(*) AS n FROM conversations GROUP BY state")
    print(f"Replay finished. {len(clock_log)} Telegram message(s) would have been sent.\n")
    for when, rec in clock_log:
        print(f"[{when} WIB] → chat {rec['chat_id']}")
        print("   " + rec["text"].replace("\n", "\n   "))
        print()
    print("Final chat states: " + ", ".join(f"{r['state']}={r['n']}" for r in states))
    return 0


if __name__ == "__main__":
    sys.exit(main())
