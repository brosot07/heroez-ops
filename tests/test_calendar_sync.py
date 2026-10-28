import datetime as dt
from zoneinfo import ZoneInfo

import httpx

from heroez_ops import calendar_sync, talents
from heroez_ops.db import Store
from heroez_ops.kb import KB

WIB = ZoneInfo("Asia/Jakarta")
NOW = dt.datetime(2026, 11, 2, 10, 0, tzinfo=WIB).timestamp()

SHARED = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Google Inc//Google Calendar 70.9054//EN
X-WR-TIMEZONE:Asia/Jakarta
BEGIN:VEVENT
UID:1
DTSTART;VALUE=DATE:20261110
DTEND;VALUE=DATE:20261113
SUMMARY:Dewi Lestari - shooting RahasiaCorp
DESCRIPTION:fee 50jt, lokasi rahasia
END:VEVENT
BEGIN:VEVENT
UID:2
DTSTART;TZID=Asia/Jakarta:20261115T190000
DTEND;TZID=Asia/Jakarta:20261115T210000
SUMMARY:Live @sinta.glow
END:VEVENT
BEGIN:VEVENT
UID:3
DTSTART;VALUE=DATE:20261120
DTEND;VALUE=DATE:20261121
SUMMARY:Raka - hold (tentative)
TRANSP:TRANSPARENT
END:VEVENT
BEGIN:VEVENT
UID:4
DTSTART;VALUE=DATE:20261105
DTEND;VALUE=DATE:20261106
SUMMARY:Meeting internal tim
END:VEVENT
BEGIN:VEVENT
UID:5
DTSTART;TZID=Asia/Jakarta:20261102T090000
DTEND;TZID=Asia/Jakarta:20261102T100000
RRULE:FREQ=WEEKLY;COUNT=3
SUMMARY:Raka streaming rutin
END:VEVENT
END:VCALENDAR
"""
OWN = b"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:a
DTSTART;VALUE=DATE:20261201
DTEND;VALUE=DATE:20261203
SUMMARY:Private trip
END:VEVENT
END:VCALENDAR
"""


OK = lambda url: (True, "")   # noqa: E731  (tests use fake transports; real DNS checks are tested separately)


def setup():
    store = Store(":memory:")
    talents.import_roster(store, [
        {"Nama": "Dewi Lestari", "Handle IG": "@dewi.lestari", "Niche": "beauty"},
        {"Nama": "Sinta", "Handle IG": "@sinta.glow", "Niche": "beauty"},
        {"Nama": "Raka", "Handle TikTok": "@rakagaming", "Niche": "gaming",
         "Kalender": "https://calendar.google.com/calendar/ical/raka/private-x/basic.ics"},
    ], "t")
    KB(store).set("calendar_urls", ["https://calendar.google.com/calendar/ical/shared/private-y/basic.ics"], "t")
    return store


def client(responses):
    def handler(req):
        for key, body in responses.items():
            if key in str(req.url):
                return httpx.Response(200, content=body) if body is not None else httpx.Response(404)
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def busy(store):
    rows = store.q("SELECT t.name, b.start, b.end, b.source FROM talent_busy b JOIN talents t ON t.id=b.talent_id ORDER BY b.start")
    return [(r["name"], r["start"], r["end"], r["source"]) for r in rows]


def test_sync_reads_dates_only():
    store = setup()
    st = calendar_sync.sync(store, WIB, check_host=OK, http=client({"shared": SHARED, "raka": OWN}), now=NOW)
    assert ("Dewi Lestari", "2026-11-10", "2026-11-12", "calendar") in busy(store)     # all-day: end exclusive
    assert ("Sinta", "2026-11-15", "2026-11-15", "calendar") in busy(store)            # matched by @handle
    assert ("Raka", "2026-12-01", "2026-12-02", "calendar") in busy(store)             # own calendar
    assert [b for b in busy(store) if b[0] == "Raka" and b[1].startswith("2026-11-")] == [
        ("Raka", "2026-11-02", "2026-11-02", "calendar"), ("Raka", "2026-11-09", "2026-11-09", "calendar"),
        ("Raka", "2026-11-16", "2026-11-16", "calendar")]                               # weekly repeat, "Free" hold ignored
    assert st["unmatched"] == 1                                                          # "Meeting internal tim"
    dump = str([dict(r) for r in store.q("SELECT * FROM talent_busy")]) + str(store.q("SELECT value FROM kv"))
    assert "RahasiaCorp" not in dump and "50jt" not in dump and "shooting" not in dump


def test_upload_and_calendar_sources_coexist_and_resync_replaces():
    store = setup()
    talents.import_availability(store, [{"Nama": "Sinta", "Mulai": "2026-12-20", "Selesai": "2026-12-21"}], "t")
    calendar_sync.sync(store, WIB, check_host=OK, http=client({"shared": SHARED, "raka": OWN}), now=NOW)
    assert ("Sinta", "2026-12-20", "2026-12-21", "upload") in busy(store)
    # Dewi's booking is removed from Google Calendar -> gone after the next sync; upload stays.
    calendar_sync.sync(store, WIB, check_host=OK, http=client({"shared": SHARED.replace(b"Dewi Lestari", b"Someone"), "raka": OWN}), now=NOW)
    assert not [b for b in busy(store) if b[0] == "Dewi Lestari"]
    assert ("Sinta", "2026-12-20", "2026-12-21", "upload") in busy(store)


def test_failed_fetch_keeps_previous_dates():
    store = setup()
    calendar_sync.sync(store, WIB, check_host=OK, http=client({"shared": SHARED, "raka": OWN}), now=NOW)
    before = busy(store)
    st = calendar_sync.sync(store, WIB, check_host=OK, http=client({}), now=NOW)                            # Google unreachable
    assert st["errors"] and busy(store) == before


def test_bot_uses_calendar_dates():
    store = setup()
    calendar_sync.sync(store, WIB, check_host=OK, http=client({"shared": SHARED, "raka": OWN}), now=NOW)
    dewi = store.one("SELECT id FROM talents WHERE name='Dewi Lestari'")["id"]
    assert not talents.available(store, dewi, dt.date(2026, 11, 11), dt.date(2026, 11, 11))
    assert talents.available(store, dewi, dt.date(2026, 11, 13), dt.date(2026, 11, 14))


def test_feed_limits_and_no_redirects():
    import datetime as dt
    store = setup()
    # A redirect is not followed (SSRF): the sync reports an error and keeps nothing from it.
    def redirecting(req):
        if "shared" in str(req.url):
            return httpx.Response(302, headers={"location": "http://127.0.0.1:8788/kontrak"})
        return httpx.Response(200, content=OWN)
    st = calendar_sync.sync(store, WIB, check_host=OK, http=httpx.Client(transport=httpx.MockTransport(redirecting)), now=NOW)
    assert any("tidak bisa dibuka" in e for e in st["errors"])
    # Minutely repeats are dropped; a huge feed is refused.
    bomb = SHARED.replace(b"RRULE:FREQ=WEEKLY;COUNT=3", b"RRULE:FREQ=MINUTELY")
    evs = calendar_sync.events(bomb, WIB, dt.date(2026, 11, 2))
    assert len(evs) < 20
    import pytest
    with pytest.raises(ValueError):
        calendar_sync.events(b"BEGIN:VCALENDAR\n" + b"X" * (3 * 1024 * 1024), WIB, dt.date(2026, 11, 2))


def test_private_hosts_refused():
    from heroez_ops.safety import is_public_https
    assert not is_public_https("http://calendar.google.com/x")[0]
    assert not is_public_https("https://127.0.0.1/x")[0]
    assert not is_public_https("https://user:pw@calendar.google.com/x")[0]
