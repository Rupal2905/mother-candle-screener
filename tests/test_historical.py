from __future__ import annotations

from datetime import datetime
from pathlib import Path
from io import StringIO

import pandas as pd
import pytest
from rich.console import Console

from inside_screener.asof import AsOfInputError, historical_context
from inside_screener import cli
from inside_screener.cli import _display, _write_csv
from inside_screener.data import UpstoxCandleClient, prepare_timeframe
from inside_screener.logic import find_active_patterns
from inside_screener.models import CountFilter, Instrument, SmallBodyFilter, TIMEFRAMES


IST = "Asia/Kolkata"


def candles(dates: list[str], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {"Open": closes, "High": [105] * len(closes), "Low": [95] * len(closes), "Close": closes},
        index=pd.DatetimeIndex(dates, tz=IST),
    )


def test_historical_daily_has_no_lookahead_after_a_future_breakout():
    source = candles(["2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04"], [100, 101, 99, 106])
    context = historical_context(TIMEFRAMES["daily"], "2026-08-03", datetime(2026, 8, 5, tzinfo=pd.Timestamp.now(tz=IST).tz))
    prepared, reason = prepare_timeframe(source, TIMEFRAMES["daily"], as_of=context.completion_as_of, data_cutoff=context.data_cutoff)
    assert reason is None and list(prepared.index.date) == [pd.Timestamp("2026-08-01").date(), pd.Timestamp("2026-08-02").date(), pd.Timestamp("2026-08-03").date()]
    before = find_active_patterns(prepared, "TEST", "Daily", CountFilter(2))
    assert [item.inside_candles for item in before] == [2]

    with_future, _ = prepare_timeframe(source, TIMEFRAMES["daily"], as_of=context.completion_as_of, data_cutoff=context.data_cutoff)
    after = find_active_patterns(with_future, "TEST", "Daily", CountFilter(2))
    assert [(item.mother_date, item.inside_candles) for item in after] == [(item.mother_date, item.inside_candles) for item in before]
    assert find_active_patterns(source, "TEST", "Daily", CountFilter(2)) == []


def test_historical_intraday_includes_only_selected_completed_session():
    source = candles(["2026-01-07 09:15", "2026-01-07 09:30", "2026-01-08 09:15"], [100, 101, 106])
    context = historical_context(TIMEFRAMES["15m"], "2026-01-07", datetime(2026, 1, 8, tzinfo=pd.Timestamp.now(tz=IST).tz))
    prepared, reason = prepare_timeframe(source, TIMEFRAMES["15m"], as_of=context.completion_as_of, data_cutoff=context.data_cutoff)
    assert reason is None
    assert list(prepared.index.date) == [pd.Timestamp("2026-01-07").date()] * 2
    assert len(find_active_patterns(prepared, "TEST", "15 minutes", CountFilter(1))) == 1


def test_historical_weekend_and_incomplete_week_use_actual_last_completed_candle():
    source = candles(["2026-01-02", "2026-01-05"], [100, 101])
    weekend = historical_context(TIMEFRAMES["weekly"], "2026-01-04", datetime(2026, 1, 5, tzinfo=pd.Timestamp.now(tz=IST).tz))
    weekend_prepared, _ = prepare_timeframe(source.iloc[:1], TIMEFRAMES["daily"], as_of=weekend.completion_as_of, data_cutoff=weekend.data_cutoff)
    assert weekend_prepared.index[-1].date() == pd.Timestamp("2026-01-02").date()

    midweek = historical_context(TIMEFRAMES["weekly"], "2026-01-07", datetime(2026, 1, 8, tzinfo=pd.Timestamp.now(tz=IST).tz))
    weekly, _ = prepare_timeframe(source, TIMEFRAMES["weekly"], as_of=midweek.completion_as_of, data_cutoff=midweek.data_cutoff)
    assert list(weekly.index.date) == [pd.Timestamp("2026-01-02").date()]


def test_historical_month_and_higher_periods_do_not_use_future_months():
    dates = [f"2026-{month:02d}-01" for month in range(1, 9)]
    source = candles(dates, [100] * len(dates))

    month = historical_context(TIMEFRAMES["monthly"], "2026-06", datetime(2026, 9, 1, tzinfo=pd.Timestamp.now(tz=IST).tz))
    monthly, _ = prepare_timeframe(source, TIMEFRAMES["monthly"], as_of=month.completion_as_of, data_cutoff=month.data_cutoff)
    assert list(monthly.index.month) == [1, 2, 3, 4, 5, 6]

    quarter = historical_context(TIMEFRAMES["quarterly"], "2026-Q2", datetime(2026, 9, 1, tzinfo=pd.Timestamp.now(tz=IST).tz))
    quarterly, _ = prepare_timeframe(source, TIMEFRAMES["quarterly"], as_of=quarter.completion_as_of, data_cutoff=quarter.data_cutoff)
    assert list(quarterly.index.month) == [3, 6]


def test_historical_half_year_and_year_require_full_source_periods():
    dates = [f"2025-{month:02d}-01" for month in range(1, 13)] + ["2026-01-01", "2026-02-01"]
    source = candles(dates, [100] * len(dates))
    half = historical_context(TIMEFRAMES["half_yearly"], "2025-H2", datetime(2026, 3, 1, tzinfo=pd.Timestamp.now(tz=IST).tz))
    half_frame, _ = prepare_timeframe(source, TIMEFRAMES["half_yearly"], as_of=half.completion_as_of, data_cutoff=half.data_cutoff)
    assert list(half_frame.index.month) == [6, 12]
    year = historical_context(TIMEFRAMES["yearly"], "2025", datetime(2026, 3, 1, tzinfo=pd.Timestamp.now(tz=IST).tz))
    year_frame, _ = prepare_timeframe(source, TIMEFRAMES["yearly"], as_of=year.completion_as_of, data_cutoff=year.data_cutoff)
    assert list(year_frame.index.year) == [2025]


def test_historical_small_body_counts_only_candles_before_cutoff():
    source = pd.DataFrame(
        {"Open": [100, 100, 100, 100], "High": [110, 110, 110, 110], "Low": [90, 90, 90, 90], "Close": [100, 102, 110, 98]},
        index=pd.date_range("2026-08-01", periods=4, freq="D", tz=IST),
    )
    context = historical_context(TIMEFRAMES["daily"], "2026-08-03", datetime(2026, 8, 5, tzinfo=pd.Timestamp.now(tz=IST).tz))
    prepared, _ = prepare_timeframe(source, TIMEFRAMES["daily"], as_of=context.completion_as_of, data_cutoff=context.data_cutoff)
    pattern = find_active_patterns(prepared, "TEST", "Daily", CountFilter(2), SmallBodyFilter(2, "count", 1))[0]
    assert pattern.inside_candles == 2 and pattern.small_body_candles == 1


def test_invalid_future_historical_input_is_rejected():
    now = datetime(2026, 8, 5, tzinfo=pd.Timestamp.now(tz=IST).tz)
    with pytest.raises(AsOfInputError):
        historical_context(TIMEFRAMES["daily"], "2026-08-05", now)
    with pytest.raises(AsOfInputError):
        historical_context(TIMEFRAMES["quarterly"], "not-a-period", now)


def test_historical_fetch_is_anchored_to_cutoff_and_kept_in_its_own_cache(tmp_path, monkeypatch):
    client = UpstoxCandleClient("token", tmp_path)
    calls: list[tuple] = []
    monkeypatch.setattr(client, "historical", lambda key, unit, interval, start, end: calls.append((start, end)) or candles(["2026-08-03"], [100]))
    cutoff = datetime(2026, 8, 3, 23, 59, tzinfo=pd.Timestamp.now(tz=IST).tz)
    client.fetch(Instrument("TEST", "NSE_EQ|TEST"), TIMEFRAMES["daily"], 365, cutoff)
    assert calls == [(pd.Timestamp("2025-08-03").date(), pd.Timestamp("2026-08-03").date())]
    assert "asof_20260803" in client._cache_path(Instrument("TEST", "NSE_EQ|TEST"), TIMEFRAMES["daily"], 365, cutoff).name


def test_historical_terminal_and_csv_metadata(tmp_path, monkeypatch):
    source = candles(["2026-08-01", "2026-08-02", "2026-08-03"], [100, 101, 99])
    context = historical_context(TIMEFRAMES["daily"], "2026-08-03", datetime(2026, 8, 5, tzinfo=pd.Timestamp.now(tz=IST).tz))
    prepared, _ = prepare_timeframe(source, TIMEFRAMES["daily"], as_of=context.completion_as_of, data_cutoff=context.data_cutoff)
    pattern = find_active_patterns(
        prepared,
        "TEST",
        "Daily",
        CountFilter(2),
        screening_mode=context.mode,
        requested_as_of=context.requested,
        effective_last_candle=prepared.index[-1],
    )[0]
    stream = StringIO()
    monkeypatch.setattr(cli, "console", Console(file=stream, width=500, force_terminal=False, color_system=None))
    _display([pattern])
    assert "Historical" in stream.getvalue() and "2026-08-03" in stream.getvalue()

    monkeypatch.chdir(tmp_path)
    output = _write_csv([pattern], "daily", context)
    written = pd.read_csv(output)
    assert "historical_20260803" in output.name
    assert written.loc[0, "Screening Mode"] == "Historical"
    assert written.loc[0, "Requested As-Of"] == "2026-08-03"
    assert written.loc[0, "Universe"] == "Current NSE F&O stocks"
