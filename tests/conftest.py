import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from heroez_ops.alerts.telegram import ShadowAlertChannel
from heroez_ops.config import load_config
from heroez_ops.db import Store
from heroez_ops.service import Service

WIB = ZoneInfo("Asia/Jakarta")
BIZ = "6280000000000"

CONFIG = """
mode: shadow
business_hours:
  days: [0, 1, 2, 3, 4, 5]
  start: "09:00"
  end: "21:00"
  holidays_file: config/holidays.yaml
whatsapp:
  business_number: "6280000000000"
admins:
  - {key: rina, name: Rina, telegram_id: 1001, supervisors: [sari]}
  - {key: budi, name: Budi, telegram_id: 1002, supervisors: [sari, tono]}
supervisors:
  - {key: sari, name: Sari, telegram_id: 2001}
  - {key: tono, name: Tono, telegram_id: 2002}
on_call_rotation: [rina, budi]
telegram:
  admin_group_id: -100
  management_group_id: -200
  management_user_ids: [9001]
talent_roster_file: data/talent_roster.csv
digest:
  admin_morning_time: "08:30"
  management_time: "09:00"
"""


def wib(*args) -> dt.datetime:
    return dt.datetime(*args, tzinfo=WIB)


def ts(t: dt.datetime) -> int:
    return int(t.timestamp())


@pytest.fixture
def cfg(tmp_path: Path):
    (tmp_path / "config").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "config" / "config.yaml").write_text(CONFIG)
    # 2026-12-25 (Friday) is a holiday in these tests
    (tmp_path / "config" / "holidays.yaml").write_text("holidays:\n  - 2026-12-25\n")
    (tmp_path / "data" / "talent_roster.csv").write_text("name,phone,handler\nTalent Satu,0811-1111-1111,budi\n")
    return load_config(tmp_path / "config" / "config.yaml")


@pytest.fixture
def channel(tmp_path: Path):
    return ShadowAlertChannel(tmp_path / "shadow.jsonl")


@pytest.fixture
def service(cfg, channel):
    return Service(cfg, Store(":memory:"), alert_channel=channel, reply_channel=channel)
