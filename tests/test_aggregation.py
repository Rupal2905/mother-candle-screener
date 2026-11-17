from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from inside_screener.calendar import aggregate_completed_monthly, completed_calendar_period_bars

IST = ZoneInfo("Asia/Kolkata")


def monthly_frame(dates):
    return pd.DataFrame(
        {"Open": [10, 20, 30, 40, 50, 60, 70], "High": [12, 22, 32, 42, 52, 62, 72],
         "Low": [9, 19, 29, 39, 49, 59, 69], "Close": [11, 21, 31, 41, 51, 61, 71]},
        index=pd.DatetimeIndex(dates, tz=IST),
    )


def test_current_native_month_is_excluded_and_completed_months_aggregate_to_quarter():
    source = monthly_frame(["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01", "2026-05-01", "2026-06-01", "2026-07-01"])
    completed = completed_calendar_period_bars(source, "month", datetime(2026, 7, 15, 12, 0, tzinfo=IST))
    assert len(completed) == 6
    quarters = aggregate_completed_monthly(source, "QE-DEC", datetime(2026, 7, 15, 12, 0, tzinfo=IST))
    assert len(quarters) == 2
    assert tuple(quarters.iloc[0]) == (10, 32, 9, 31)
    assert tuple(quarters.iloc[1]) == (40, 62, 39, 61)


def test_half_yearly_and_yearly_use_completed_monthly_ohlc():
    source = monthly_frame(["2025-07-01", "2025-08-01", "2025-09-01", "2025-10-01", "2025-11-01", "2025-12-01", "2026-01-01"])
    as_of = datetime(2026, 1, 15, 12, 0, tzinfo=IST)
    half = aggregate_completed_monthly(source, "2QE-DEC", as_of)
    year = aggregate_completed_monthly(source, "YE-DEC", as_of)
    assert tuple(half.iloc[-1]) == (10, 62, 9, 61)
    assert tuple(year.iloc[-1]) == (10, 62, 9, 61)
