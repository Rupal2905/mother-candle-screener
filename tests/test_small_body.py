from __future__ import annotations

from io import StringIO

import pandas as pd
from rich.console import Console

from inside_screener import cli
from inside_screener.cli import _display, _write_csv
from inside_screener.logic import find_active_patterns, validate_ohlc
from inside_screener.models import CountFilter, SmallBodyFilter


def ohlc_frame(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["Open", "High", "Low", "Close"],
        index=pd.date_range("2026-01-01", periods=len(rows), freq="D", tz="Asia/Kolkata"),
    )


MOTHER = (100, 110, 90, 100)


def first_pattern(rows: list[tuple[float, float, float, float]], body_filter: SmallBodyFilter | None):
    matches = find_active_patterns(ohlc_frame([MOTHER, *rows]), "TEST", "Daily", CountFilter(len(rows)), body_filter)
    assert len(matches) == 1
    return matches[0]


def test_bullish_bearish_and_user_example_ratio_are_counted_equally():
    # The user example has Range=7, Body=2, and Ratio=3.5, so it qualifies.
    example = first_pattern([(100, 105, 98, 102)], SmallBodyFilter(2, "count", 1))
    assert example.small_body_candles == 1

    # The bullish and bearish candles both have Range/Body=4.
    pattern = first_pattern(
        [(100, 110, 90, 105), (105, 110, 90, 100)],
        SmallBodyFilter(2, "count", 2),
    )
    assert pattern.small_body_candles == 2
    assert pattern.small_body_pct == 100


def test_ratio_equal_to_threshold_and_zero_range_do_not_qualify():
    pattern = first_pattern(
        [(100, 110, 90, 110), (100, 100, 100, 100)],
        SmallBodyFilter(2, "count", 0),
    )
    assert pattern.small_body_candles == 0


def test_zero_body_with_nonzero_range_qualifies_without_division():
    pattern = first_pattern([(100, 110, 90, 100)], SmallBodyFilter(2, "count", 1))
    assert pattern.small_body_candles == 1


def test_invalid_ohlc_is_rejected_before_small_body_classification():
    bad_high = ohlc_frame([MOTHER, (100, 99, 90, 95)])
    bad_low = ohlc_frame([MOTHER, (100, 110, 101, 105)])
    assert validate_ohlc(bad_high) == "invalid OHLC: High below Open or Close"
    assert validate_ohlc(bad_low) == "invalid OHLC: Low above Open or Close"
    assert find_active_patterns(bad_high, "TEST", "Daily", CountFilter(1), SmallBodyFilter(2, "count", 1)) == []


def test_minimum_small_body_count_filters_without_changing_mother_eligibility():
    rows = [(100, 110, 90, 102), (100, 110, 90, 110), (100, 110, 90, 98)]
    passing = first_pattern(rows, SmallBodyFilter(2, "count", 2))
    assert passing.small_body_candles == 2
    assert find_active_patterns(ohlc_frame([MOTHER, *rows]), "TEST", "Daily", CountFilter(3), SmallBodyFilter(2, "count", 3)) == []
    assert len(find_active_patterns(ohlc_frame([MOTHER, *rows]), "TEST", "Daily", CountFilter(3))) == 1


def test_minimum_percentage_uses_each_pattern_inside_count():
    three_inside = [(100, 110, 90, 102), (100, 110, 90, 110), (100, 110, 90, 98)]
    pattern = first_pattern(three_inside, SmallBodyFilter(2, "percentage", 60))
    assert pattern.small_body_candles == 2
    assert round(pattern.small_body_pct or 0, 2) == 66.67
    assert find_active_patterns(ohlc_frame([MOTHER, *three_inside]), "TEST", "Daily", CountFilter(3), SmallBodyFilter(2, "percentage", 67)) == []

    four_inside = [*three_inside, (100, 110, 90, 110)]
    pattern = first_pattern(four_inside, SmallBodyFilter(2, "percentage", 50))
    assert pattern.small_body_candles == 2
    assert pattern.small_body_pct == 50


def test_overlapping_patterns_calculate_their_own_small_body_totals():
    data = ohlc_frame([MOTHER, (100, 110, 90, 102), (100, 110, 90, 110), (100, 110, 90, 98)])
    patterns = find_active_patterns(data, "TEST", "Daily", CountFilter(1), SmallBodyFilter(2, "percentage", 0))
    assert [(pattern.inside_candles, pattern.small_body_candles) for pattern in patterns] == [(3, 2), (2, 1), (1, 1)]


def test_disabled_filter_preserves_existing_results_and_marks_new_columns_na():
    data = ohlc_frame([MOTHER, (100, 110, 90, 110), (100, 110, 90, 98)])
    baseline = find_active_patterns(data, "TEST", "Daily", CountFilter(1))
    disabled = find_active_patterns(data, "TEST", "Daily", CountFilter(1), None)
    assert [(item.mother_date, item.inside_candles) for item in disabled] == [(item.mother_date, item.inside_candles) for item in baseline]
    assert disabled[0].as_row()["Small-Body Candles"] == "N/A"
    assert disabled[0].as_row()["Small-Body %"] == "N/A"
    assert disabled[0].as_row()["Range/Body Ratio Threshold"] == "N/A"


def test_terminal_and_csv_include_small_body_columns(tmp_path, monkeypatch):
    pattern = first_pattern([(100, 110, 90, 102)], SmallBodyFilter(2, "count", 1))
    stream = StringIO()
    monkeypatch.setattr(cli, "console", Console(file=stream, width=500, force_terminal=False, color_system=None))
    _display([pattern])
    output = stream.getvalue()
    assert "Small-Body Candles" in output
    assert "Range/Body Ratio Threshold" in output

    monkeypatch.chdir(tmp_path)
    destination = _write_csv([pattern], "daily")
    written = pd.read_csv(destination)
    assert ["Small-Body Candles", "Small-Body %", "Range/Body Ratio Threshold"] == list(written.columns[14:17])
    assert written.loc[0, "Small-Body Candles"] == 1
    assert written.loc[0, "Range/Body Ratio Threshold"] == 2


def test_terminal_input_validation_and_small_body_options(monkeypatch):
    answers = iter(["bad", "daily", "1", "1", "", "1", "0", "2", "maybe", "y", "-1", "", "3", "1", "-1", "2", "n", "4", "1", "n"])
    messages: list[str] = []
    monkeypatch.setattr(cli.console, "input", lambda prompt: next(answers))
    monkeypatch.setattr(cli.console, "print", lambda message: messages.append(str(message)))

    timeframe, screening_context, universe, lookback_override, count_filter, body_filter, green_filter, output, refresh = cli.ask_options()
    assert timeframe == "daily"
    assert screening_context.mode == "Current"
    assert universe.key == "fo"
    assert lookback_override is None
    assert count_filter == CountFilter(2)
    assert body_filter == SmallBodyFilter(2, "count", 2)
    assert green_filter is None
    assert output == "terminal"
    assert refresh is False
    assert any("Choose one" in message for message in messages)
    assert any("positive whole" in message for message in messages)
    assert any("non-negative number" in message for message in messages)
    assert any("non-negative whole" in message for message in messages)
