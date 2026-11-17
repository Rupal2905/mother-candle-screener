from datetime import date, datetime
from pathlib import Path

import pandas as pd

from inside_screener.data import UpstoxCandleClient
from inside_screener.lookback import configured_lookback_days, resolve_lookback
from inside_screener.logic import find_active_patterns
from inside_screener.models import CountFilter, Instrument, TIMEFRAMES


ANCHOR = datetime(2026, 11, 13, 12, 0, tzinfo=pd.Timestamp.now(tz="Asia/Kolkata").tz)


def test_timeframe_defaults_are_anchored_and_clamped_to_upstox_availability():
    expected = {
        "5m": date(2026, 5, 13),
        "15m": date(2026, 5, 13),
        "30m": date(2025, 11, 13),
        "5h": date(2025, 11, 13),
        "daily": date(2021, 11, 13),
        "weekly": date(2016, 11, 13),
        "monthly": date(2011, 11, 13),
        "quarterly": date(2006, 11, 13),
        "half_yearly": date(2006, 11, 13),
        "yearly": date(2000, 1, 1),
    }
    for key, start in expected.items():
        assert resolve_lookback(TIMEFRAMES[key], ANCHOR).start_date == start

    old_intraday = resolve_lookback(TIMEFRAMES["5m"], datetime(2022, 3, 1, tzinfo=ANCHOR.tzinfo))
    assert old_intraday.start_date == date(2022, 1, 1)
    assert old_intraday.clipped_to_availability


def test_terminal_and_environment_overrides_are_respected(monkeypatch):
    assert resolve_lookback(TIMEFRAMES["weekly"], ANCHOR, 400).start_date == date(2025, 10, 9)
    monkeypatch.setenv("UPSTOX_LOOKBACK_DAYS", "100")
    assert configured_lookback_days() == 100
    assert resolve_lookback(TIMEFRAMES["daily"], ANCHOR).start_date == date(2026, 8, 5)


def test_historical_lookback_is_anchored_to_cutoff_not_today():
    cutoff = datetime(2018, 6, 30, 23, 59, tzinfo=ANCHOR.tzinfo)
    plan = resolve_lookback(TIMEFRAMES["weekly"], cutoff)
    assert plan.anchor_date == date(2018, 6, 30)
    assert plan.start_date == date(2008, 6, 30)


def test_very_old_weekly_mother_candle_remains_screenable_when_source_is_provided():
    frame = pd.DataFrame(
        {"Open": [100, 101, 99], "High": [105, 110, 110], "Low": [95, 90, 90], "Close": [100, 101, 99]},
        index=pd.DatetimeIndex(["2010-01-01", "2010-01-08", "2010-01-15"], tz="Asia/Kolkata"),
    )
    matches = find_active_patterns(frame, "TEST", "Weekly", CountFilter(2))
    assert len(matches) == 1 and matches[0].mother_date.year == 2010


def test_long_intraday_ranges_are_split_into_v3_valid_windows(tmp_path, monkeypatch):
    client = UpstoxCandleClient("token", tmp_path)
    calls: list[tuple[date, date]] = []
    monkeypatch.setattr(
        client,
        "historical",
        lambda key, unit, interval, start, end: calls.append((start, end)) or pd.DataFrame(columns=["Open", "High", "Low", "Close"]),
    )
    start = date(2026, 1, 1)
    cutoff = datetime(2026, 7, 1, 23, 59, tzinfo=ANCHOR.tzinfo)
    client.fetch(Instrument("TEST", "NSE_EQ|TEST"), TIMEFRAMES["15m"], 181, cutoff, start)
    assert len(calls) == 7
    assert calls[0] == (date(2026, 1, 1), date(2026, 1, 30))
    assert calls[-1][1] == date(2026, 7, 1)


def test_short_cache_is_not_reused_as_complete_long_history(tmp_path, monkeypatch):
    client = UpstoxCandleClient("token", tmp_path)
    instrument = Instrument("TEST", "NSE_EQ|TEST")
    cutoff = datetime(2026, 11, 13, 23, 59, tzinfo=ANCHOR.tzinfo)
    short = pd.DataFrame(
        {"Open": [1], "High": [2], "Low": [0], "Close": [1]},
        index=pd.DatetimeIndex(["2026-11-12"], tz="Asia/Kolkata"),
    )
    path = client._cache_path(instrument, TIMEFRAMES["daily"], 365, cutoff)
    path.parent.mkdir(parents=True)
    short.to_pickle(path)
    calls: list[tuple[date, date]] = []
    monkeypatch.setattr(
        client,
        "historical",
        lambda key, unit, interval, start, end: calls.append((start, end)) or short,
    )
    client.fetch(instrument, TIMEFRAMES["daily"], 1826, cutoff, date(2021, 11, 13))
    assert calls == [(date(2021, 11, 13), date(2026, 11, 13))]
