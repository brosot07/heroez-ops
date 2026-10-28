"""Keep KOL availability in step with Google Calendar (private iCal addresses).

Two set-ups, usable together:
* shared calendars (Pengetahuan page): an event belongs to every KOL whose name or
  @handle appears in its title
* a calendar per KOL: the KOL list's "Kalender" column holds that KOL's iCal address

Only the dates are kept. Titles, descriptions, locations and guests are read in memory
to find the KOL and then dropped, so nothing about the work itself is stored. Events
marked "Free" (transparent) and cancelled events are ignored.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import time

import httpx

from .db import Store
from .kb import KB

log = logging.getLogger(__name__)

WINDOW_DAYS = 180
SYNC_EVERY_SECONDS = 15 * 60


def _norm(s: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", (s or "").lower().replace("@", " ")).strip() + " "


def _dates(start, end) -> tuple[dt.date, dt.date]:
    """Calendar dates an event covers (all-day DTEND and midnight ends are exclusive)."""
    if isinstance(start, dt.datetime) and isinstance(end, dt.datetime):
        s, e = start.date(), end.date()
        if end.time() == dt.time(0) and e > s:
            e -= dt.timedelta(days=1)
        return s, max(s, e)
    s = start if isinstance(start, dt.date) else start.date()
    e = (end if isinstance(end, dt.date) else end.date()) if end else s
    if not isinstance(end, dt.datetime) and e > s:
        e -= dt.timedelta(days=1)                  # all-day: DTEND is the day after
    return s, max(s, e)


def events(ics: bytes, tz, today: dt.date) -> list[tuple[str, dt.date, dt.date]]:
    """(title, first day, last day) for busy events in the sync window, recurring ones expanded."""
    import icalendar
    import recurring_ical_events
    cal = icalendar.Calendar.from_ical(ics)
    start = dt.datetime.combine(today - dt.timedelta(days=1), dt.time(0), tz)
    end = dt.datetime.combine(today + dt.timedelta(days=WINDOW_DAYS), dt.time(0), tz)
    out = []
    for ev in recurring_ical_events.of(cal).between(start, end):
        if str(ev.get("TRANSP", "")).upper() == "TRANSPARENT" or str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        s = ev.get("DTSTART").dt
        e = ev.get("DTEND").dt if ev.get("DTEND") else s
        if isinstance(s, dt.datetime):
            s = s.astimezone(tz) if s.tzinfo else s.replace(tzinfo=tz)
            e = e.astimezone(tz) if e.tzinfo else e.replace(tzinfo=tz)
        a, b = _dates(s, e)
        out.append((str(ev.get("SUMMARY", "")), a, b))
    return out


def _talent_keys(store: Store) -> list[tuple[int, list[str]]]:
    from .talents import _handles
    keys = []
    for r in store.q("SELECT id, name, data FROM talents"):
        names = [_norm(r["name"])] + [_norm(h) for h in _handles(json.loads(r["data"])["info"])]
        keys.append((r["id"], [n for n in names if len(n.strip()) >= 3]))
    return keys


def sync(store: Store, tz, http: httpx.Client | None = None, now: float | None = None) -> dict:
    """Fetch every calendar and replace the calendar-sourced busy dates. Returns a status dict."""
    http = http or httpx.Client(timeout=20, follow_redirects=True)
    today = dt.datetime.fromtimestamp(now or time.time(), tz).date()
    kb = KB(store)
    shared = [u for u in (kb.get("calendar_urls", []) or []) if u.strip()]
    own = [(r["id"], r["calendar_url"]) for r in store.q("SELECT id, calendar_url FROM talents WHERE calendar_url LIKE 'http%'")]
    keys = _talent_keys(store)
    periods: set[tuple[int, str, str]] = set()
    status = {"at": int(now or time.time()), "calendars": 0, "events": 0, "matched": 0, "unmatched": 0, "errors": []}

    def fetch(url: str) -> bytes | None:
        if not url.startswith("https://"):
            status["errors"].append("alamat kalender harus diawali https://")
            return None
        try:
            r = http.get(url)
            r.raise_for_status()
            status["calendars"] += 1
            return r.content
        except httpx.HTTPError as e:
            status["errors"].append(f"kalender …{url[-12:]} tidak bisa dibuka ({type(e).__name__})")
            return None

    for url in shared:
        data = fetch(url)
        if data is None:
            continue
        try:
            evs = events(data, tz, today)
        except Exception:
            status["errors"].append(f"kalender …{url[-12:]} tidak bisa dibaca")
            continue
        for title, a, b in evs:
            status["events"] += 1
            t = _norm(title)
            hits = [tid for tid, names in keys if any(n in t for n in names)]
            status["matched" if hits else "unmatched"] += 1
            periods.update((tid, a.isoformat(), b.isoformat()) for tid in hits)
    for tid, url in own:
        data = fetch(url)
        if data is None:
            continue
        try:
            for _, a, b in events(data, tz, today):
                status["events"] += 1
                status["matched"] += 1
                periods.add((tid, a.isoformat(), b.isoformat()))
        except Exception:
            status["errors"].append("kalender talent tidak bisa dibaca")
    # Keep the previous dates if every calendar failed (better stale than wrongly "available").
    if status["calendars"] or not (shared or own):
        with store.tx():
            store.x("DELETE FROM talent_busy WHERE source='calendar'")
            for p in periods:
                store.x("INSERT INTO talent_busy(talent_id,start,end,source) VALUES(?,?,?,'calendar')", p)
    store.set_kv("calendar_status", json.dumps(status, ensure_ascii=False))
    store.set_kv("calendar_last_try", str(int(now or time.time())))
    return status


def maybe_sync(store: Store, tz, now: float | None = None) -> None:
    now = now or time.time()
    last = int(store.get_kv("calendar_last_try", "0") or 0)
    has_any = (KB(store).get("calendar_urls") or
               store.one("SELECT 1 FROM talents WHERE calendar_url LIKE 'http%'"))
    if has_any and now - last >= SYNC_EVERY_SECONDS:
        try:
            sync(store, tz, now=now)
        except Exception:
            log.exception("calendar sync failed")
            store.set_kv("calendar_last_try", str(int(now)))
