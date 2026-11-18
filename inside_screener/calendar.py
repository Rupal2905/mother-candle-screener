"""NSE/IST candle completion checks and monthly aggregation."""
from __future__ import annotations

import json
import os
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from .nse_calendar import NSETradingCalendar

IST = ZoneInfo("Asia/Kolkata")
REGULAR_CLOSE = time(15, 30)


def now_ist() -> datetime:
    return datetime.now(IST)


def to_ist_index(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    index = pd.DatetimeIndex(result.index)
    result.index = index.tz_localize(IST) if index.tz is None else index.tz_convert(IST)
    return result.sort_index()


def _session_close(day, overrides: dict[str, object] | None = None) -> time:
    raw = (overrides or {}).get(day.isoformat()) if overrides else None
    value = raw.get("close") if isinstance(raw, dict) else raw
    if isinstance(value, str):
        try:
            return time.fromisoformat(value)
        except ValueError:
            pass
    return REGULAR_CLOSE


def session_overrides_from_env() -> dict[str, object]:
    raw = os.getenv("UPSTOX_SESSION_OVERRIDES_JSON", "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("UPSTOX_SESSION_OVERRIDES_JSON must be a JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError("UPSTOX_SESSION_OVERRIDES_JSON must be a JSON object")
    return value


def completed_native_bars(
    frame: pd.DataFrame,
    interval_minutes: int | None,
    as_of: datetime | None = None,
    session_overrides: dict[str, object] | None = None,
) -> pd.DataFrame:
    """Remove today’s still-forming Upstox candle in Asia/Kolkata.

    A final shortened 2–5 hour bar is complete at the NSE session close, not at
    its nominal interval end. Historical dates are already closed; only today
    needs a wall-clock test. Optional overrides handle announced special closes.
    """
    if frame.empty:
        return frame
    as_of = as_of or now_ist()
    result = to_ist_index(frame)
    today = as_of.date()
    if interval_minutes is None:
        if as_of.timetz().replace(tzinfo=None) < _session_close(today, session_overrides):
            return result[result.index.date < today]
        return result

    mask: list[bool] = []
    for stamp in result.index:
        if stamp.date() < today:
            mask.append(True)
            continue
        close_dt = pd.Timestamp.combine(stamp.date(), _session_close(stamp.date(), session_overrides)).tz_localize(IST)
        nominal_end = stamp + pd.Timedelta(minutes=interval_minutes)
        completed_at = min(nominal_end, close_dt)
        mask.append(stamp.date() < today or completed_at <= pd.Timestamp(as_of))
    return result.loc[mask]


def completed_calendar_period_bars(
    frame: pd.DataFrame,
    period: str,
    as_of: datetime | None = None,
    session_overrides: dict[str, object] | None = None,
) -> pd.DataFrame:
    """Remove the current native Upstox weekly or monthly bar when incomplete."""
    as_of = as_of or now_ist()
    result = to_ist_index(frame)
    if result.empty:
        return result
    as_of_local = pd.Timestamp(as_of).tz_localize(None)
    last_local = result.index[-1].tz_localize(None)
    if period == "week":
        current = as_of_local.to_period("W-FRI")
        candle_period = last_local.to_period("W-FRI")
        friday_close = pd.Timestamp.combine(current.end_time.date(), _session_close(current.end_time.date(), session_overrides)).tz_localize(IST)
        complete = pd.Timestamp(as_of) >= friday_close
    elif period == "month":
        current = as_of_local.to_period("M")
        candle_period = last_local.to_period("M")
        complete = as_of_local.to_period("M") != (as_of_local - pd.Timedelta(days=1)).to_period("M")
    else:
        raise ValueError(f"Unsupported native period: {period}")
    return result.iloc[:-1] if candle_period == current and not complete else result


def aggregate_completed_monthly(
    frame: pd.DataFrame,
    rule: str,
    as_of: datetime | None = None,
    session_overrides: dict[str, object] | None = None,
    require_complete_period: bool = False,
    include_incomplete_month: bool = False,
) -> pd.DataFrame:
    monthly = to_ist_index(frame) if include_incomplete_month else completed_calendar_period_bars(frame, "month", as_of, session_overrides)
    if monthly.empty:
        return monthly
    if rule == "2QE-DEC":
        # Pandas' 2-quarter frequency can anchor on a partial rolling pair.
        # Build true calendar half-years explicitly: Jan-Jun and Jul-Dec.
        rows: list[dict[str, float]] = []
        labels: list[pd.Timestamp] = []
        groups = monthly.groupby([monthly.index.year, (monthly.index.month > 6).astype(int)])
        for (year, half), group in groups:
            if require_complete_period and len(group) != 6:
                continue
            month = 12 if half else 6
            labels.append(pd.Timestamp(year=int(year), month=month, day=1, tz=IST) + pd.offsets.MonthEnd(0))
            rows.append({"Open": group["Open"].iloc[0], "High": group["High"].max(), "Low": group["Low"].min(), "Close": group["Close"].iloc[-1]})
        result = pd.DataFrame(rows, index=pd.DatetimeIndex(labels)).sort_index()
        return _label_current_aggregate_with_source_timestamp(result, monthly, as_of) if include_incomplete_month else result
    result = monthly[["Open", "High", "Low", "Close"]].resample(rule, label="right", closed="right").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
    ).dropna()
    if require_complete_period:
        expected_months = 3 if rule == "QE-DEC" else 12
        counts = monthly.resample(rule, label="right", closed="right").size()
        result = result.loc[result.index.isin(counts[counts == expected_months].index)]
    return _label_current_aggregate_with_source_timestamp(result, monthly, as_of) if include_incomplete_month else result


def _label_current_aggregate_with_source_timestamp(
    aggregate: pd.DataFrame,
    monthly: pd.DataFrame,
    as_of: datetime | None,
) -> pd.DataFrame:
    """Avoid presenting a forming higher-period candle as a future timestamp."""
    if aggregate.empty or monthly.empty:
        return aggregate
    cutoff = pd.Timestamp(as_of or now_ist())
    cutoff = cutoff.tz_localize(IST) if cutoff.tzinfo is None else cutoff.tz_convert(IST)
    if aggregate.index[-1] <= cutoff:
        return aggregate
    result = aggregate.copy()
    labels = result.index.to_list()
    labels[-1] = monthly.index[-1]
    result.index = pd.DatetimeIndex(labels)
    return result.sort_index()


def intraday_gap_reason(frame: pd.DataFrame, interval_minutes: int) -> str | None:
    """Conservatively reject same-session gaps; missing bars cannot be assumed valid."""
    if frame.empty:
        return None
    index = pd.DatetimeIndex(frame.index)
    dates = pd.Series(index.date, index=index)
    threshold = pd.Timedelta(minutes=interval_minutes * 1.5)
    for _, session in dates.groupby(dates):
        timestamps = session.index.sort_values()
        if len(timestamps) > 1 and (timestamps[1:] - timestamps[:-1] > threshold).any():
            return "missing intraday candle(s) inside an NSE session"
    return None


def trim_after_unverified_weekday_gap(
    frame: pd.DataFrame,
    trading_calendar: NSETradingCalendar | None = None,
) -> tuple[pd.DataFrame, str | None]:
    """Keep only the latest daily sequence without an unexplained expected day."""
    if len(frame) < 2:
        return frame, None
    result = to_ist_index(frame)
    cut = 0
    cause = ""
    for position in range(1, len(result)):
        previous = result.index[position - 1].date()
        current = result.index[position].date()
        probe = previous + timedelta(days=1)
        while probe < current:
            expected = trading_calendar.expected_trading_day(probe) if trading_calendar else (probe.weekday() < 5)
            if expected is True:
                cut = position
                cause = "missing expected NSE trading day"
                break
            if expected is None:
                cut = position
                cause = "NSE calendar unavailable for a missing date"
                break
            probe += timedelta(days=1)
    if cut:
        return result.iloc[cut:], f"history before {result.index[cut].date()} excluded after {cause}"
    return result, None
