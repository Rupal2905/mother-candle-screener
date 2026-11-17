from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from inside_screener.calendar import completed_native_bars, intraday_gap_reason
from inside_screener.logic import find_active_patterns, keep_oldest_mother_patterns, validate_ohlc
from inside_screener.models import CountFilter


IST = ZoneInfo("Asia/Kolkata")


def frame(closes, highs=None, lows=None, start="2026-01-01"):
    index = pd.date_range(start, periods=len(closes), freq="D", tz=IST)
    highs = highs or [close + 1 for close in closes]
    lows = lows or [close - 1 for close in closes]
    return pd.DataFrame({"Open": closes, "High": highs, "Low": lows, "Close": closes}, index=index)


def test_wicks_can_escape_and_all_six_closes_are_inside():
    data = frame(
        [100, 101, 98, 104, 96, 100, 103],
        highs=[105, 110, 110, 110, 110, 110, 110],
        lows=[95, 90, 90, 90, 90, 90, 90],
    )
    matches = find_active_patterns(data, "TEST", "Daily", CountFilter(6))
    mother = next(match for match in matches if match.mother_date == data.index[0])
    assert mother.inside_candles == 6


def test_close_exactly_on_mother_boundaries_is_valid():
    data = frame([100, 105, 95], highs=[105, 120, 120], lows=[95, 80, 80])
    matches = find_active_patterns(data, "TEST", "Daily", CountFilter(2, 2))
    assert any(match.mother_date == data.index[0] for match in matches)


def test_outside_close_invalidates_active_mother_pattern():
    data = frame([100, 101, 106], highs=[105, 110, 110], lows=[95, 90, 90])
    matches = find_active_patterns(data, "TEST", "Daily", CountFilter(2))
    assert not any(match.mother_date == data.index[0] for match in matches)


def test_overlapping_mothers_are_retained_as_separate_rows():
    data = frame([100, 100, 100, 100], highs=[105] * 4, lows=[95] * 4)
    matches = find_active_patterns(data, "TEST", "Daily", CountFilter(1))
    assert [match.inside_candles for match in matches] == [3, 2, 1]
    assert len({match.mother_date for match in matches}) == 3


def test_final_scan_selection_keeps_only_the_oldest_overlapping_mother_per_symbol():
    data = frame([100, 100, 100, 100], highs=[105] * 4, lows=[95] * 4)
    overlaps = find_active_patterns(data, "TEST", "Daily", CountFilter(1))
    selected = keep_oldest_mother_patterns(overlaps)
    assert len(selected) == 1
    assert selected[0].mother_date == data.index[0]
    assert selected[0].inside_candles == 3


def test_range_filter_and_insufficient_history():
    data = frame([100, 100, 100, 100, 100], highs=[105] * 5, lows=[95] * 5)
    matches = find_active_patterns(data, "TEST", "Daily", CountFilter(2, 3))
    assert [match.inside_candles for match in matches] == [3, 2]
    assert find_active_patterns(data.iloc[:2], "TEST", "Daily", CountFilter(2)) == []


def test_incomplete_daily_and_intraday_candles_are_removed():
    daily_index = pd.DatetimeIndex(["2026-01-06", "2026-01-07"], tz=IST)
    daily = pd.DataFrame({"Open": [1, 1], "High": [2, 2], "Low": [0, 0], "Close": [1, 1]}, index=daily_index)
    before_close = datetime(2026, 1, 7, 15, 0, tzinfo=IST)
    assert len(completed_native_bars(daily, None, before_close)) == 1
    after_close = datetime(2026, 1, 7, 15, 31, tzinfo=IST)
    assert len(completed_native_bars(daily, None, after_close)) == 2

    intraday_index = pd.DatetimeIndex(["2026-01-07 14:55", "2026-01-07 15:00"], tz=IST)
    intraday = pd.DataFrame({"Open": [1, 1], "High": [2, 2], "Low": [0, 0], "Close": [1, 1]}, index=intraday_index)
    assert len(completed_native_bars(intraday, 15, datetime(2026, 1, 7, 15, 9, tzinfo=IST))) == 0
    assert len(completed_native_bars(intraday, 15, datetime(2026, 1, 7, 15, 14, tzinfo=IST))) == 1


def test_missing_values_and_intraday_gaps_are_reported():
    data = frame([100, None], highs=[101, None], lows=[99, None])
    assert validate_ohlc(data) == "missing OHLC values"
    index = pd.DatetimeIndex(["2026-01-07 09:15", "2026-01-07 09:25"], tz=IST)
    data = pd.DataFrame({"Open": [1, 1], "High": [2, 2], "Low": [0, 0], "Close": [1, 1]}, index=index)
    assert intraday_gap_reason(data, 5) == "missing intraday candle(s) inside an NSE session"
