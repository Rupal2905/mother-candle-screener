import pandas as pd
from datetime import date, time

from inside_screener.calendar import IST, trim_after_unverified_weekday_gap
from inside_screener.nse_calendar import NSETradingCalendar


def test_weekday_gap_is_reported_and_older_segment_is_not_screened():
    frame = pd.DataFrame(
        {"Open": [1, 1, 1], "High": [2, 2, 2], "Low": [0, 0, 0], "Close": [1, 1, 1]},
        index=pd.DatetimeIndex(["2026-01-01", "2026-01-02", "2026-01-06"], tz=IST),
    )
    trimmed, warning = trim_after_unverified_weekday_gap(frame)
    assert list(trimmed.index.date) == [pd.Timestamp("2026-01-06").date()]
    assert warning and "missing expected NSE trading day" in warning


def test_official_dussehra_holiday_does_not_truncate_daily_history():
    calendar = NSETradingCalendar(frozenset({date(2026, 1, 15), date(2026, 10, 20)}), frozenset({2026}))
    frame = pd.DataFrame(
        {"Open": [1, 1], "High": [2, 2], "Low": [0, 0], "Close": [1, 1]},
        index=pd.DatetimeIndex(["2026-10-19", "2026-10-21"], tz=IST),
    )
    retained, warning = trim_after_unverified_weekday_gap(frame, calendar)
    assert len(retained) == 2
    assert warning is None


def test_ad_hoc_january_15_capital_market_holiday_does_not_truncate_history():
    calendar = NSETradingCalendar(frozenset({date(2026, 1, 15)}), frozenset({2026}))
    frame = pd.DataFrame(
        {"Open": [1, 1], "High": [2, 2], "Low": [0, 0], "Close": [1, 1]},
        index=pd.DatetimeIndex(["2026-01-14", "2026-01-16"], tz=IST),
    )
    retained, warning = trim_after_unverified_weekday_gap(frame, calendar)
    assert len(retained) == 2 and warning is None


def test_genuine_expected_day_gap_is_still_rejected():
    calendar = NSETradingCalendar(frozenset({date(2026, 10, 20)}), frozenset({2026}))
    frame = pd.DataFrame(
        {"Open": [1, 1], "High": [2, 2], "Low": [0, 0], "Close": [1, 1]},
        index=pd.DatetimeIndex(["2026-10-19", "2026-10-22"], tz=IST),
    )
    retained, warning = trim_after_unverified_weekday_gap(frame, calendar)
    assert list(retained.index.date) == [date(2026, 10, 22)]
    assert warning and "missing expected NSE trading day" in warning


def test_muhurat_sunday_is_an_expected_session_not_a_weekend_gap():
    calendar = NSETradingCalendar(
        frozenset({date(2026, 11, 8)}),
        frozenset({2026}),
        {date(2026, 11, 8): (time(14, 20), time(15, 30))},
    )
    with_muhurat = pd.DataFrame(
        {"Open": [1, 1, 1], "High": [2, 2, 2], "Low": [0, 0, 0], "Close": [1, 1, 1]},
        index=pd.DatetimeIndex(["2026-11-06", "2026-11-08", "2026-11-09"], tz=IST),
    )
    retained, warning = trim_after_unverified_weekday_gap(with_muhurat, calendar)
    assert len(retained) == 3 and warning is None
    missing_muhurat = with_muhurat.drop(with_muhurat.index[1])
    retained, warning = trim_after_unverified_weekday_gap(missing_muhurat, calendar)
    assert len(retained) == 1
    assert warning and "missing expected NSE trading day" in warning
