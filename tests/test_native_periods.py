from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from inside_screener.calendar import completed_calendar_period_bars
from inside_screener.data import prepare_timeframe
from inside_screener.logic import find_active_patterns
from inside_screener.models import CountFilter, TIMEFRAMES

IST = ZoneInfo("Asia/Kolkata")


def bars(dates):
    return pd.DataFrame(
        {"Open": [1] * len(dates), "High": [2] * len(dates), "Low": [0] * len(dates), "Close": [1] * len(dates)},
        index=pd.DatetimeIndex(dates, tz=IST),
    )


def test_current_week_is_admitted_only_after_friday_close():
    source = bars(["2026-01-02", "2026-01-05"])
    before = completed_calendar_period_bars(source, "week", datetime(2026, 1, 7, 12, 0, tzinfo=IST))
    after = completed_calendar_period_bars(source, "week", datetime(2026, 1, 9, 15, 31, tzinfo=IST))
    assert len(before) == 1
    assert len(after) == 2


def test_current_month_is_not_used_mid_month():
    source = bars(["2026-01-30", "2026-02-02"])
    result = completed_calendar_period_bars(source, "month", datetime(2026, 2, 15, 12, 0, tzinfo=IST))
    assert len(result) == 1


def test_special_friday_close_can_complete_native_week_early():
    source = bars(["2026-01-02", "2026-01-05"])
    result = completed_calendar_period_bars(
        source,
        "week",
        datetime(2026, 1, 9, 14, 0, tzinfo=IST),
        {"2026-01-09": {"close": "13:00"}},
    )
    assert len(result) == 2


def test_current_week_and_month_are_screened_while_forming():
    weekly_source = bars(["2026-01-02", "2026-01-05"])
    weekly, reason = prepare_timeframe(weekly_source, TIMEFRAMES["weekly"])
    assert reason is None
    assert list(weekly.index.date) == [pd.Timestamp("2026-01-02").date(), pd.Timestamp("2026-01-05").date()]

    monthly_source = bars(["2026-01-30", "2026-02-02"])
    monthly, reason = prepare_timeframe(monthly_source, TIMEFRAMES["monthly"])
    assert reason is None
    assert list(monthly.index.date) == [pd.Timestamp("2026-01-30").date(), pd.Timestamp("2026-02-02").date()]


def test_forming_weekly_breakout_invalidates_an_otherwise_active_mother():
    source = pd.DataFrame(
        {
            "Open": [100, 100, 100],
            "High": [105, 110, 110],
            "Low": [95, 90, 90],
            "Close": [100, 101, 106],
        },
        index=pd.DatetimeIndex(["2025-12-26", "2026-01-02", "2026-01-05"], tz=IST),
    )
    prepared, reason = prepare_timeframe(source, TIMEFRAMES["weekly"])
    assert reason is None
    matches = find_active_patterns(prepared, "TEST", "Weekly", CountFilter(2))
    assert not any(item.mother_date == source.index[0] for item in matches)
