"""Business-hours arithmetic in WIB.

All datetimes passed in must be timezone-aware. Two clocks use this class:
  * the notification clock (default): business_hours from config; reminder timers.
    Holidays count as open days when they are treated like weekends.
  * the work clock (BusinessClock.work): the admins' working day, for response-time metrics.
"""
from __future__ import annotations

import datetime as dt

from .config import Config


class BusinessClock:
    def __init__(self, cfg: Config, days: frozenset[int] | None = None, start: dt.time | None = None,
                 end: dt.time | None = None, holidays_closed: bool | None = None):
        self.cfg = cfg
        self.days = cfg.business_days if days is None else days
        self.start = start or cfg.business_start
        self.end = end or cfg.business_end
        self.holidays_closed = (not cfg.holidays_as_weekend) if holidays_closed is None else holidays_closed

    @classmethod
    def work(cls, cfg: Config) -> "BusinessClock":
        return cls(cfg, cfg.work_days, cfg.work_start, cfg.work_end, holidays_closed=True)

    def is_business_day(self, d: dt.date) -> bool:
        return d.weekday() in self.days and not (self.holidays_closed and d in self.cfg.holidays)

    def is_quiet_day(self, d: dt.date) -> bool:
        """Weekend-style day: fewer notifications."""
        return d.weekday() in self.cfg.weekend_days or (self.cfg.holidays_as_weekend and d in self.cfg.holidays)

    def _day_bounds(self, d: dt.date) -> tuple[dt.datetime, dt.datetime]:
        tz = self.cfg.tz
        return dt.datetime.combine(d, self.start, tz), dt.datetime.combine(d, self.end, tz)

    def is_open(self, t: dt.datetime) -> bool:
        local = t.astimezone(self.cfg.tz)
        if not self.is_business_day(local.date()):
            return False
        start, end = self._day_bounds(local.date())
        return start <= local < end

    def business_minutes_between(self, a: dt.datetime, b: dt.datetime) -> float:
        """Business minutes elapsed from a to b (0 if b <= a)."""
        if b <= a:
            return 0.0
        a, b = a.astimezone(self.cfg.tz), b.astimezone(self.cfg.tz)
        total = 0.0
        day = a.date()
        while day <= b.date():
            if self.is_business_day(day):
                start, end = self._day_bounds(day)
                lo, hi = max(start, a), min(end, b)
                if hi > lo:
                    total += (hi - lo).total_seconds() / 60
            day += dt.timedelta(days=1)
        return total

    def add_business_minutes(self, t: dt.datetime, minutes: float) -> dt.datetime:
        """The moment when `minutes` business minutes have elapsed after t."""
        local = t.astimezone(self.cfg.tz)
        remaining = minutes
        day = local.date()
        for _ in range(400):  # bounded: a year plus slack
            if self.is_business_day(day):
                start, end = self._day_bounds(day)
                lo = max(start, local)
                if end > lo:
                    avail = (end - lo).total_seconds() / 60
                    if remaining <= avail:
                        return lo + dt.timedelta(minutes=remaining)
                    remaining -= avail
            day += dt.timedelta(days=1)
            local = dt.datetime.combine(day, dt.time(0), self.cfg.tz)
        raise ValueError("no business hours found within a year; check config")

    def next_open(self, t: dt.datetime) -> dt.datetime:
        """t itself if open, otherwise the start of the next business period."""
        return self.add_business_minutes(t, 0)

    def on_call(self, t: dt.datetime) -> str:
        """Admin key on call for the local date of t (simple daily rotation)."""
        rot = self.cfg.on_call_rotation
        return rot[t.astimezone(self.cfg.tz).date().toordinal() % len(rot)]
