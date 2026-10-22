import math
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from dealfinder import db
from dealfinder.budget import SCAN_LOCK, Budget, Caps, GateDecision
from dealfinder.config import Config

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=LA)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


def make_budget(conn, now=NOW, cfg=None):
    return Budget(conn, cfg or Config(), lambda: now)


def spend(conn, cost, at):
    db.record_spend(conn, 1, 1000, 100, cost, at)


def test_off_by_default(conn):
    b = make_budget(conn)
    assert b.scan_enabled() is False
    assert b.can_auto_scan(10) == GateDecision(False, "scanning is off")


def test_scan_enabled_roundtrip(conn):
    b = make_budget(conn)
    b.set_scan_enabled(True)
    assert b.scan_enabled() is True
    assert db.get_setting(conn, "scan_enabled") == "true"
    b.set_scan_enabled(False)
    assert b.scan_enabled() is False
    assert db.get_setting(conn, "scan_enabled") == "false"


def test_paused_roundtrip(conn):
    b = make_budget(conn)
    assert b.paused() is False
    b.set_paused(True)
    assert b.paused() is True
    assert db.get_setting(conn, "scheduler_paused") == "true"
    b.set_paused(False)
    assert b.paused() is False


def test_allowed_when_on_and_under_everything(conn):
    b = make_budget(conn)
    b.set_scan_enabled(True)
    assert b.can_auto_scan(10) == GateDecision(True, "ok")
    assert b.can_auto_scan(300).allowed  # at the limit is fine


def test_price_filter(conn):
    b = make_budget(conn)
    b.set_scan_enabled(True)
    d = b.can_auto_scan(301)
    assert d == GateDecision(False, "over max scan price")


def test_daily_cap_with_ceiling(conn):
    b = make_budget(conn)
    b.set_scan_enabled(True)
    spend(conn, 4.80, NOW)
    assert b.can_auto_scan(10) == GateDecision(False, "daily cap reached")
    conn.execute("DELETE FROM vision_spend")
    spend(conn, 4.70, NOW)
    assert b.can_auto_scan(10) == GateDecision(True, "ok")


def test_monthly_cap(conn):
    b = make_budget(conn)
    b.set_scan_enabled(True)
    spend(conn, 49.80, datetime(2026, 10, 3, 9, 0, tzinfo=LA))
    assert b.spent_today() == 0
    assert b.can_auto_scan(10) == GateDecision(False, "monthly cap reached")


def test_check_order(conn):
    b = make_budget(conn)
    spend(conn, 49.80, NOW)  # over both caps
    assert b.can_auto_scan(301).reason == "scanning is off"
    b.set_scan_enabled(True)
    assert b.can_auto_scan(301).reason == "over max scan price"
    assert b.can_auto_scan(10).reason == "daily cap reached"
    b.set_caps(daily=100)
    assert b.can_auto_scan(10).reason == "monthly cap reached"


def test_manual_ignores_switch_and_price_but_not_caps(conn):
    b = make_budget(conn)
    assert b.scan_enabled() is False
    assert b.can_manual_scan() == GateDecision(True, "ok")
    spend(conn, 4.80, NOW)
    assert b.can_manual_scan() == GateDecision(False, "daily cap reached")
    b.set_caps(daily=50)
    spend(conn, 45.0, datetime(2026, 10, 1, 8, 0, tzinfo=LA))
    assert b.can_manual_scan() == GateDecision(False, "monthly cap reached")


def test_day_and_month_reset(conn):
    # Yesterday 23:59 local does not count today.
    spend(conn, 3.0, datetime(2026, 10, 21, 23, 59, tzinfo=LA))
    spend(conn, 1.0, datetime(2026, 10, 22, 0, 0, tzinfo=LA))
    b = make_budget(conn)
    assert b.spent_today() == pytest.approx(1.0)
    assert b.spent_this_month() == pytest.approx(4.0)

    # Spend on the 31st does not count on the 1st.
    conn.execute("DELETE FROM vision_spend")
    spend(conn, 7.0, datetime(2026, 10, 31, 23, 59, tzinfo=LA))
    spend(conn, 2.0, datetime(2026, 11, 1, 0, 1, tzinfo=LA))
    nov1 = make_budget(conn, datetime(2026, 11, 1, 10, 0, tzinfo=LA))
    assert nov1.spent_today() == pytest.approx(2.0)
    assert nov1.spent_this_month() == pytest.approx(2.0)
    oct31 = make_budget(conn, datetime(2026, 10, 31, 23, 59, 30, tzinfo=LA))
    assert oct31.spent_this_month() == pytest.approx(7.0)


def test_local_boundaries_use_local_zone_not_utc(conn):
    # 2026-10-22 18:00 PDT is 2026-10-23 01:00 UTC, still "today" locally.
    spend(conn, 2.0, datetime(2026, 10, 22, 18, 0, tzinfo=LA))
    b = make_budget(conn, datetime(2026, 10, 22, 19, 0, tzinfo=LA))
    assert b.spent_today() == pytest.approx(2.0)


def test_day_boundary_across_dst_change(conn):
    # US fall-back 2026-11-01: that local day is 25 hours long.
    spend(conn, 1.0, datetime(2026, 11, 1, 0, 30, tzinfo=LA))
    spend(conn, 1.0, datetime(2026, 11, 1, 23, 30, tzinfo=LA))
    spend(conn, 5.0, datetime(2026, 11, 2, 0, 30, tzinfo=LA))
    b = make_budget(conn, datetime(2026, 11, 1, 12, 0, tzinfo=LA))
    assert b.spent_today() == pytest.approx(2.0)


def test_record_stores_utc_at_clock_time(conn):
    b = make_budget(conn)
    b.record(7, 1200, 340, 0.0123)
    row = conn.execute("SELECT * FROM vision_spend").fetchone()
    assert row["listing_id"] == 7
    assert row["input_tokens"] == 1200
    assert row["output_tokens"] == 340
    assert row["cost_usd"] == pytest.approx(0.0123)
    assert row["scanned_at"] == "2026-10-22T19:00:00+00:00"
    assert b.spent_today() == pytest.approx(0.0123)


def test_spend_between_is_half_open(conn):
    t = lambda h: datetime(2026, 10, 22, h, 0, tzinfo=LA)  # noqa: E731
    spend(conn, 1.0, t(9))
    spend(conn, 2.0, t(10))
    spend(conn, 4.0, t(11))
    assert db.spend_between(conn, t(9), t(11)) == pytest.approx(3.0)
    assert db.spend_between(conn, t(12), t(13)) == 0.0


def test_caps_default_to_config_and_can_be_set(conn):
    b = make_budget(conn)
    assert b.caps() == Caps(daily=5.0, monthly=50.0, max_scan_price=300.0)
    b.set_caps(daily=8)
    assert b.caps() == Caps(daily=8.0, monthly=50.0, max_scan_price=300.0)
    b.set_caps(monthly=20, max_scan_price=150)
    assert b.caps() == Caps(daily=8.0, monthly=20.0, max_scan_price=150.0)
    # stored settings win over config
    other_cfg = Config(initial_daily_cap=1.0, initial_monthly_cap=2.0, initial_max_scan_price=3.0)
    assert make_budget(conn, cfg=other_cfg).caps().daily == 8.0


def test_caps_fall_back_to_config_values(tmp_path):
    c = db.connect(tmp_path / "fresh.db")
    cfg = Config(initial_daily_cap=1.0, initial_monthly_cap=2.0, initial_max_scan_price=3.0)
    assert make_budget(c, cfg=cfg).caps() == Caps(1.0, 2.0, 3.0)
    c.close()


def test_zero_cap_is_allowed_and_blocks_scanning(conn):
    b = make_budget(conn)
    b.set_scan_enabled(True)
    b.set_caps(daily=0)
    assert b.caps().daily == 0.0
    assert b.can_auto_scan(10).reason == "daily cap reached"


def test_set_caps_validates(conn):
    b = make_budget(conn)
    for bad in (-1, -0.01, math.nan, math.inf, -math.inf):
        for name in ("daily", "monthly", "max_scan_price"):
            with pytest.raises(ValueError):
                b.set_caps(**{name: bad})
    assert b.caps() == Caps(5.0, 50.0, 300.0)
    # nothing is written when any value is bad
    with pytest.raises(ValueError):
        b.set_caps(daily=9, monthly=-1)
    assert b.caps().daily == 5.0
    # no-arg call is a no-op
    b.set_caps()
    assert b.caps() == Caps(5.0, 50.0, 300.0)


def test_lock_is_shared_across_instances(conn, tmp_path):
    other_conn = db.connect(tmp_path / "other.db")
    b1 = make_budget(conn)
    b2 = make_budget(other_conn)
    assert b1.lock is b2.lock
    assert b1.lock is SCAN_LOCK
    other_conn.close()
