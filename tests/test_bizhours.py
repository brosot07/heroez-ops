from heroez_ops.bizhours import BusinessClock

from .conftest import wib


def test_minutes_within_one_day(cfg):
    c = BusinessClock(cfg)
    assert c.business_minutes_between(wib(2026, 11, 2, 10, 0), wib(2026, 11, 2, 10, 45)) == 45


def test_overnight_message_does_not_count_night(cfg):
    c = BusinessClock(cfg)
    # Mon 22:00 -> Tue 09:30 = 30 business minutes
    assert c.business_minutes_between(wib(2026, 11, 2, 22, 0), wib(2026, 11, 3, 9, 30)) == 30


def test_sunday_is_closed(cfg):
    c = BusinessClock(cfg)
    # Sat 20:50 -> Mon 09:20: 10 min Saturday + 20 min Monday
    assert c.business_minutes_between(wib(2026, 11, 7, 20, 50), wib(2026, 11, 9, 9, 20)) == 30
    assert not c.is_open(wib(2026, 11, 8, 12, 0))


def test_holiday_is_closed(cfg):
    c = BusinessClock(cfg)
    assert not c.is_open(wib(2026, 12, 25, 12, 0))
    # Thu 20:30 -> Sat 09:30 skips the Friday holiday
    assert c.business_minutes_between(wib(2026, 12, 24, 20, 30), wib(2026, 12, 26, 9, 30)) == 60


def test_add_business_minutes_over_weekend(cfg):
    c = BusinessClock(cfg)
    assert c.add_business_minutes(wib(2026, 11, 7, 20, 50), 30) == wib(2026, 11, 9, 9, 20)
    assert c.next_open(wib(2026, 11, 2, 23, 0)) == wib(2026, 11, 3, 9, 0)
    assert c.next_open(wib(2026, 11, 2, 21, 0)) == wib(2026, 11, 3, 9, 0)


def test_on_call_rotates_daily(cfg):
    c = BusinessClock(cfg)
    a, b = c.on_call(wib(2026, 11, 2, 23, 0)), c.on_call(wib(2026, 11, 3, 23, 0))
    assert {a, b} == {"rina", "budi"}
