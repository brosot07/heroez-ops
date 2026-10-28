"""Load and validate config/config.yaml into typed objects."""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml


class ConfigError(ValueError):
    """Raised with a plain-language message when config is invalid."""


@dataclass(frozen=True)
class Admin:
    key: str
    name: str
    telegram_id: int
    supervisors: tuple[str, ...] = ()   # supervisor keys; empty = all supervisors


@dataclass(frozen=True)
class Supervisor:
    key: str
    name: str
    telegram_id: int


@dataclass(frozen=True)
class Escalation:
    nudge_assignee_after_business_min: int = 30
    alert_group_after_business_min: int = 120
    alert_management_p1_after_business_min: int = 240
    urgent_after_real_hours: int = 18
    overdue_after_real_hours: int = 24
    snooze_minutes: int = 60


@dataclass(frozen=True)
class Config:
    mode: str
    tz: ZoneInfo
    business_days: frozenset[int]
    business_start: dt.time
    business_end: dt.time
    holidays: frozenset[dt.date]
    business_number: str
    window_hours: int
    admins: tuple[Admin, ...]
    supervisors: tuple[Supervisor, ...]
    on_call_rotation: tuple[str, ...]
    telegram_mode: str
    admin_group_id: int
    management_group_id: int
    management_user_ids: tuple[int, ...]
    escalation: Escalation
    min_minutes_between_messages: int
    max_items_per_message: int
    admin_morning_time: dt.time
    management_digest_time: dt.time
    talent_roster_file: Path | None
    possibly_replied_recheck_hours: int
    message_text_days: int = 90
    raw_webhook_days: int = 7
    backup_keep: int = 14
    # Secrets come from the environment, never from the YAML file.
    meta_app_secret: str = ""
    meta_verify_token: str = ""
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def live(self) -> bool:
        return self.mode == "live"

    def admin(self, key: str) -> Admin | None:
        return next((a for a in self.admins if a.key == key), None)

    def admin_by_telegram(self, telegram_id: int) -> Admin | None:
        return next((a for a in self.admins if a.telegram_id == telegram_id), None)

    def supervisor(self, key: str) -> Supervisor | None:
        return next((s for s in self.supervisors if s.key == key), None)

    def supervisor_by_telegram(self, telegram_id: int) -> Supervisor | None:
        return next((s for s in self.supervisors if s.telegram_id and s.telegram_id == telegram_id), None)

    def supervisors_of(self, admin_key: str | None) -> tuple[Supervisor, ...]:
        """The admin's own supervisors, or everyone if none are set (or no admin)."""
        a = self.admin(admin_key) if admin_key else None
        if a and a.supervisors:
            return tuple(s for s in self.supervisors if s.key in a.supervisors)
        return self.supervisors

    def allowed_telegram_ids(self) -> set[int]:
        return ({a.telegram_id for a in self.admins} | {s.telegram_id for s in self.supervisors}
                | set(self.management_user_ids)) - {0}


def _time(value: str, name: str) -> dt.time:
    try:
        return dt.time.fromisoformat(str(value))
    except ValueError as e:
        raise ConfigError(f"{name} must look like 09:00, got {value!r}") from e


def load_config(path: str | Path = "config/config.yaml", base_dir: Path | None = None) -> Config:
    path = Path(path)
    base_dir = base_dir or path.resolve().parent.parent
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as e:
        raise ConfigError(f"Config file not found: {path}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"Config file {path} is not valid YAML: {e}") from e

    mode = raw.get("mode", "shadow")
    if mode not in ("shadow", "live"):
        raise ConfigError(f"mode must be 'shadow' or 'live', got {mode!r}")

    bh = raw.get("business_hours", {})
    days = frozenset(int(d) for d in bh.get("days", [0, 1, 2, 3, 4, 5]))
    if not days or not days <= set(range(7)):
        raise ConfigError("business_hours.days must list numbers 0 (Monday) to 6 (Sunday)")
    start = _time(bh.get("start", "09:00"), "business_hours.start")
    end = _time(bh.get("end", "21:00"), "business_hours.end")
    if end <= start:
        raise ConfigError("business_hours.end must be later than business_hours.start")

    holidays: set[dt.date] = set()
    hfile = bh.get("holidays_file")
    if hfile:
        hpath = base_dir / hfile
        if hpath.exists():
            for d in (yaml.safe_load(hpath.read_text(encoding="utf-8")) or {}).get("holidays", []) or []:
                holidays.add(d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d)))

    supervisors = []
    for s in raw.get("supervisors") or []:
        if not s.get("key"):
            raise ConfigError("every supervisor needs a 'key'")
        supervisors.append(Supervisor(key=s["key"], name=s.get("name", s["key"]), telegram_id=int(s.get("telegram_id") or 0)))
    spv_keys = {s.key for s in supervisors}

    admins = []
    for a in raw.get("admins", []):
        if not a.get("key"):
            raise ConfigError("every admin needs a 'key'")
        own = tuple(a.get("supervisors") or [])
        for k in own:
            if k not in spv_keys:
                raise ConfigError(f"admin {a['key']!r} lists unknown supervisor {k!r}")
        admins.append(Admin(key=a["key"], name=a.get("name", a["key"]), telegram_id=int(a.get("telegram_id") or 0),
                            supervisors=own))
    if not admins:
        raise ConfigError("at least one admin must be listed under 'admins'")
    keys = {a.key for a in admins}
    if keys & spv_keys:
        raise ConfigError(f"admins and supervisors need different keys: {sorted(keys & spv_keys)}")

    rotation = tuple(raw.get("on_call_rotation") or [a.key for a in admins])
    for k in rotation:
        if k not in keys:
            raise ConfigError(f"on_call_rotation contains unknown admin {k!r}")

    wa = raw.get("whatsapp", {})
    tg = raw.get("telegram", {})
    esc = Escalation(**(raw.get("escalation") or {}))
    al = raw.get("alerts", {})
    roster = raw.get("talent_roster_file")

    return Config(
        mode=mode,
        tz=ZoneInfo(raw.get("timezone", "Asia/Jakarta")),
        business_days=days,
        business_start=start,
        business_end=end,
        holidays=frozenset(holidays),
        business_number="".join(ch for ch in str(wa.get("business_number", "")) if ch.isdigit()),
        window_hours=int(wa.get("window_hours", 24)),
        admins=tuple(admins),
        supervisors=tuple(supervisors),
        on_call_rotation=rotation,
        telegram_mode=tg.get("mode", "polling"),
        admin_group_id=int(tg.get("admin_group_id") or 0),
        management_group_id=int(tg.get("management_group_id") or 0),
        management_user_ids=tuple(int(x) for x in tg.get("management_user_ids") or []),
        escalation=esc,
        min_minutes_between_messages=int(al.get("min_minutes_between_messages_per_recipient", 10)),
        max_items_per_message=int(al.get("max_items_per_message", 5)),
        admin_morning_time=_time((raw.get("digest") or {}).get("admin_morning_time", "08:30"), "digest.admin_morning_time"),
        management_digest_time=_time((raw.get("digest") or {}).get("management_time", "09:00"), "digest.management_time"),
        talent_roster_file=(base_dir / roster) if roster else None,
        possibly_replied_recheck_hours=int((raw.get("possibly_replied") or {}).get("recheck_hours", 24)),
        message_text_days=int((raw.get("retention") or {}).get("message_text_days", 90)),
        raw_webhook_days=int((raw.get("retention") or {}).get("raw_webhook_days", 7)),
        backup_keep=int((raw.get("retention") or {}).get("backup_keep", 14)),
        meta_app_secret=os.environ.get("META_APP_SECRET", ""),
        meta_verify_token=os.environ.get("META_VERIFY_TOKEN", ""),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_webhook_secret=os.environ.get("TELEGRAM_WEBHOOK_SECRET", ""),
    )
