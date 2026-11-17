"""Pure pattern detection; no downloads or terminal code live here."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .models import CountFilter, GreenCandleFilter, Pattern, SmallBodyFilter

REQUIRED_COLUMNS = ("Open", "High", "Low", "Close")


def validate_ohlc(frame: pd.DataFrame) -> str | None:
    """Return a reason when OHLC cannot safely be screened, otherwise None."""
    if frame is None or frame.empty:
        return "no OHLC data returned"
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        return f"missing required columns: {', '.join(missing)}"
    if len(frame) < 2:
        return "insufficient history: fewer than two completed candles"
    values = frame.loc[:, REQUIRED_COLUMNS]
    if values.isna().any().any():
        return "missing OHLC values"
    numeric = values.apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any():
        return "invalid OHLC: non-numeric values"
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        return "invalid OHLC: non-finite values"
    if (numeric["High"] < numeric["Low"]).any():
        return "invalid OHLC: High below Low"
    if (numeric["High"] < numeric[["Open", "Close"]].max(axis=1)).any():
        return "invalid OHLC: High below Open or Close"
    if (numeric["Low"] > numeric[["Open", "Close"]].min(axis=1)).any():
        return "invalid OHLC: Low above Open or Close"
    return None


def find_active_patterns(
    frame: pd.DataFrame,
    symbol: str,
    timeframe: str,
    count_filter: CountFilter,
    small_body_filter: SmallBodyFilter | None = None,
    green_candle_filter: GreenCandleFilter | None = None,
    screening_mode: str = "Current",
    requested_as_of: str = "Latest completed candle",
    effective_last_candle: pd.Timestamp | None = None,
    universe: str = "Current NSE F&O stocks",
) -> list[Pattern]:
    """Find every active mother candle in completed, gap-validated OHLC data.

    A candidate is active only when *every* following available completed candle
    through the final row closes in its inclusive [Low, High] range.  This makes
    the inside count exactly the number of rows following the mother candle.
    Overlapping candidates are deliberately preserved; a unique result is
    identified by (symbol, timeframe, mother candle timestamp).
    """
    reason = validate_ohlc(frame)
    if reason:
        return []

    clean = frame.sort_index().copy()
    highs = clean["High"].to_numpy(dtype=float)
    lows = clean["Low"].to_numpy(dtype=float)
    opens = clean["Open"].to_numpy(dtype=float)
    closes = clean["Close"].to_numpy(dtype=float)
    n = len(clean)
    patterns: list[Pattern] = []

    # A mother needs at least min qualifying candles after it.  Checking every
    # subsequent close (not its wick) directly implements the requested rule.
    for mother_index in range(0, n - count_filter.minimum):
        inside_count = n - mother_index - 1
        if not count_filter.accepts(inside_count):
            continue
        following_closes = closes[mother_index + 1 :]
        if np.all((following_closes >= lows[mother_index]) & (following_closes <= highs[mother_index])):
            small_body_candles: int | None = None
            ratio_threshold: float | None = None
            if small_body_filter is not None:
                following_opens = opens[mother_index + 1 :]
                following_highs = highs[mother_index + 1 :]
                following_lows = lows[mother_index + 1 :]
                ranges = following_highs - following_lows
                bodies = np.abs(following_closes - following_opens)
                # Avoid dividing by zero: a non-flat zero-body candle qualifies,
                # while a zero-range candle never does.  The strict comparison
                # implements Range / Body > ratio_threshold for non-zero bodies.
                is_small_body = ((bodies == 0) & (ranges > 0)) | (
                    (bodies > 0) & (ranges > bodies * small_body_filter.ratio_threshold)
                )
                small_body_candles = int(np.count_nonzero(is_small_body))
                ratio_threshold = small_body_filter.ratio_threshold
                if not small_body_filter.accepts(small_body_candles, inside_count):
                    continue
            following_opens = opens[mother_index + 1 :]
            green_candles = int(np.count_nonzero(following_closes > following_opens))
            red_candles = int(np.count_nonzero(following_closes < following_opens))
            neutral_candles = inside_count - green_candles - red_candles
            if green_candle_filter is not None and not green_candle_filter.accepts(green_candles, inside_count):
                continue
            patterns.append(
                Pattern(
                    symbol=symbol,
                    timeframe=timeframe,
                    mother_date=pd.Timestamp(clean.index[mother_index]),
                    mother_high=float(highs[mother_index]),
                    mother_low=float(lows[mother_index]),
                    inside_candles=inside_count,
                    last_close=float(closes[-1]),
                    small_body_candles=small_body_candles,
                    ratio_threshold=ratio_threshold,
                    green_candles=green_candles,
                    red_candles=red_candles,
                    neutral_candles=neutral_candles,
                    screening_mode=screening_mode,
                    requested_as_of=requested_as_of,
                    effective_last_candle=effective_last_candle,
                    universe=universe,
                )
            )
    return patterns


def keep_oldest_mother_patterns(patterns: list[Pattern]) -> list[Pattern]:
    """Keep the earliest active mother for each symbol/timeframe result set.

    Detection deliberately still identifies overlapping valid mothers. The scan
    presentation uses this final selection step so one stock is not repeated
    when several active mothers share the same current candle sequence.
    """
    oldest: dict[tuple[str, str], Pattern] = {}
    for pattern in patterns:
        key = (pattern.symbol, pattern.timeframe)
        current = oldest.get(key)
        if current is None or pattern.mother_date < current.mother_date:
            oldest[key] = pattern
    return list(oldest.values())
