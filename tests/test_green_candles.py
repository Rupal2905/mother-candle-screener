from __future__ import annotations

import pandas as pd
from io import StringIO
from rich.console import Console

from inside_screener import cli
from inside_screener.logic import find_active_patterns
from inside_screener.models import CountFilter, GreenCandleFilter, SmallBodyFilter


def ohlc(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["Open", "High", "Low", "Close"],
        index=pd.date_range("2026-01-01", periods=len(rows), freq="D", tz="Asia/Kolkata"),
    )


MOTHER = (100, 110, 90, 100)


def pattern(rows: list[tuple[float, float, float, float]], green: GreenCandleFilter | None = None, small: SmallBodyFilter | None = None):
    return find_active_patterns(ohlc([MOTHER, *rows]), "TEST", "Daily", CountFilter(len(rows)), small, green)


def test_green_red_and_neutral_inside_candles_are_counted_strictly():
    matches = pattern([(100, 108, 99, 102), (104, 109, 98, 100), (101, 106, 99, 101)])
    assert len(matches) == 1
    item = matches[0]
    assert (item.green_candles, item.red_candles, item.neutral_candles) == (1, 1, 1)
    assert round(item.green_candle_pct or 0, 2) == 33.33


def test_green_percentage_is_inclusive_and_accepts_decimals():
    rows = [(100, 108, 99, 102), (100, 109, 98, 103), (104, 109, 98, 100)]
    assert len(pattern(rows, GreenCandleFilter(66.66666666666667))) == 1
    assert pattern(rows, GreenCandleFilter(67)) == []


def test_disabled_green_filter_does_not_change_mother_or_small_body_results():
    rows = [(100, 110, 90, 102), (100, 110, 90, 110), (100, 110, 90, 98)]
    baseline = pattern(rows, None, SmallBodyFilter(2, "count", 2))
    enabled = pattern(rows, GreenCandleFilter(67), SmallBodyFilter(2, "count", 2))
    assert len(baseline) == 1
    assert enabled == []
    assert baseline[0].small_body_candles == 2


def test_overlaps_calculate_green_percentages_independently():
    rows = [(100, 110, 90, 102), (100, 110, 90, 99), (100, 110, 90, 102)]
    matches = find_active_patterns(ohlc([MOTHER, *rows]), "TEST", "Daily", CountFilter(1), green_candle_filter=GreenCandleFilter(0))
    assert [(item.inside_candles, item.green_candles) for item in matches] == [(3, 2), (2, 1), (1, 1)]


def test_green_columns_are_present_in_result_row():
    item = pattern([(100, 110, 90, 102)])[0]
    assert item.as_row()["Green Candle Count"] == 1
    assert item.as_row()["Red Candle Count"] == 0
    assert item.as_row()["Neutral Candle Count"] == 0
    assert item.as_row()["Green Candle %"] == 100.0


def test_terminal_green_option_defaults_to_fifty_and_output_includes_columns(monkeypatch):
    answers = iter(["daily", "1", "1", "", "1", "1", "n", "y", "", "1", "n"])
    messages: list[str] = []
    monkeypatch.setattr(cli.console, "input", lambda prompt: next(answers))
    monkeypatch.setattr(cli.console, "print", lambda value: messages.append(str(value)))
    _, _, _, _, _, _, green, _, _ = cli.ask_options()
    assert green == GreenCandleFilter(50)

    stream = StringIO()
    monkeypatch.setattr(cli, "console", Console(file=stream, width=500, force_terminal=False, color_system=None))
    cli._display([pattern([(100, 110, 90, 102)])[0]])
    assert "Green Candle Count" in stream.getvalue()
