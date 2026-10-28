"""Escalation ladder: decides which alert steps are due for each waiting chat.

Steps fire at most once per wait (reset when we reply). Thresholds come from config.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

from .bizhours import BusinessClock
from .config import Config
from .db import Store
from .states import AWAITING_US


@dataclass(frozen=True)
class Due:
    wa_id: str
    step: str                 # nudge | group | management | urgent | overdue
    recipients: tuple[str, ...]  # "admin:<key>" | "spv:<key>" | "group:admin" | "group:management"


def _ts(t: dt.datetime) -> int:
    return int(t.timestamp())


class Escalator:
    def __init__(self, cfg: Config, store: Store, clock: BusinessClock):
        self.cfg, self.store, self.clock = cfg, store, clock

    def due(self, now: dt.datetime) -> list[Due]:
        e = self.cfg.escalation
        out: list[Due] = []
        rows = self.store.q("SELECT * FROM conversations WHERE state=? AND waiting_since IS NOT NULL", (AWAITING_US,))
        now_ts = _ts(now)
        for c in rows:
            fired = set(json.loads(c["fired_steps"] or "[]"))
            waiting = dt.datetime.fromtimestamp(c["waiting_since"], dt.timezone.utc)
            last_in = dt.datetime.fromtimestamp(c["last_inbound_at"] or c["waiting_since"], dt.timezone.utc)
            bmin = self.clock.business_minutes_between(waiting, now)
            real_h = (now - last_in).total_seconds() / 3600
            snoozed = c["snoozed_until"] and c["snoozed_until"] > now_ts
            assignee = c["assignee"]
            all_admins = tuple(f"admin:{a.key}" for a in self.cfg.admins)
            own_spv = tuple(f"spv:{s.key}" for s in self.cfg.supervisors_of(assignee))
            all_spv = tuple(f"spv:{s.key}" for s in self.cfg.supervisors)
            me = (f"admin:{assignee}",) if assignee else ()

            steps: list[tuple[str, tuple[str, ...]]] = []
            if real_h >= e.overdue_after_real_hours:
                steps.append(("overdue", all_admins + own_spv + ("group:admin",)))
            elif real_h >= e.urgent_after_real_hours:
                if self.clock.is_open(now):
                    steps.append(("urgent", all_admins + own_spv + ("group:admin",)))
                else:
                    steps.append(("urgent", (f"admin:{self.clock.on_call(now)}",) + own_spv + ("group:admin",)))
            if not snoozed:
                if c["priority"] == "P1" and bmin >= e.alert_management_p1_after_business_min:
                    steps.append(("management", ("group:management",) + all_spv))
                if bmin >= e.alert_group_after_business_min:
                    steps.append(("group", me + own_spv + ("group:admin",)))
                if bmin >= e.nudge_assignee_after_business_min and assignee:
                    steps.append(("nudge", me))

            for step, recipients in steps:
                if step not in fired:
                    out.append(Due(c["wa_id"], step, recipients))
        return out

    def mark_fired(self, wa_id: str, step: str) -> None:
        row = self.store.one("SELECT fired_steps FROM conversations WHERE wa_id=?", (wa_id,))
        fired = set(json.loads(row["fired_steps"] or "[]")) if row else set()
        fired.add(step)
        # A later step implies the earlier ones are no longer worth sending separately.
        order = ["nudge", "group", "urgent", "overdue"]
        if step in order:
            fired.update(order[: order.index(step)])
        self.store.x("UPDATE conversations SET fired_steps=? WHERE wa_id=?", (json.dumps(sorted(fired)), wa_id))
