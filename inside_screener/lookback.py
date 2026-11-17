"""Timeframe-aware, availability-clamped historical lookback policy."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import os

import pandas as pd

from .models import Timeframe


INTRADAY_AVAILABLE_FROM = date(2022, 1, 1)
EOD_AVAILABLE_FROM = date(2000, 1, 1)


@dataclass(frozen=True)
class LookbackPlan:
    requested_label: str
    start_date: date
    anchor_date: date
    availability_start: date
    overridden: bool
    clipped_to_availability: bool

    @property
    def days(self) -> int:
        return (self.anchor_date - self.start_date).days


def default_lookback_label(timeframe: Timeframe) -> str:
    if timeframe.unit == "minutes" and timeframe.interval <= 15:
        return "6 months"
    if timeframe.intraday:
        return "1 year"
    if timeframe.unit == "days":
        return "5 years"
    if timeframe.calendar_period == "week":
        return "10 years"
    if timeframe.calendar_period == "month":
        return "15 years"
    if timeframe.aggregate in {"QE-DEC", "2QE-DEC"}:
        return "20 years"
    return "maximum Upstox history"


def configured_lookback_days() -> int | None:
    raw = os.getenv("UPSTOX_LOOKBACK_DAYS", "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("UPSTOX_LOOKBACK_DAYS must be a positive whole number") from exc
    if value <= 0:
        raise ValueError("UPSTOX_LOOKBACK_DAYS must be a positive whole number")
    return value


def resolve_lookback(timeframe: Timeframe, anchor: datetime, override_days: int | None = None) -> LookbackPlan:
    """Create an availability-safe plan anchored to current or historical cutoff."""
    anchor_date = anchor.date()
    configured = configured_lookback_days()
    selected_override = override_days if override_days is not None else configured
    availability = INTRADAY_AVAILABLE_FROM if timeframe.intraday else EOD_AVAILABLE_FROM
    if selected_override is not None:
        requested_start = anchor_date - timedelta(days=selected_override)
        label = f"{selected_override} calendar days"
        overridden = True
    elif timeframe.unit == "minutes" and timeframe.interval <= 15:
        requested_start = (pd.Timestamp(anchor_date) - pd.DateOffset(months=6)).date()
        label = "6 months"
        overridden = False
    elif timeframe.intraday:
        requested_start = (pd.Timestamp(anchor_date) - pd.DateOffset(years=1)).date()
        label = "1 year"
        overridden = False
    elif timeframe.unit == "days":
        requested_start = (pd.Timestamp(anchor_date) - pd.DateOffset(years=5)).date()
        label = "5 years"
        overridden = False
    elif timeframe.calendar_period == "week":
        requested_start = (pd.Timestamp(anchor_date) - pd.DateOffset(years=10)).date()
        label = "10 years"
        overridden = False
    elif timeframe.calendar_period == "month":
        requested_start = (pd.Timestamp(anchor_date) - pd.DateOffset(years=15)).date()
        label = "15 years"
        overridden = False
    elif timeframe.aggregate in {"QE-DEC", "2QE-DEC"}:
        requested_start = (pd.Timestamp(anchor_date) - pd.DateOffset(years=20)).date()
        label = "20 years"
        overridden = False
    else:
        requested_start = availability
        label = "maximum Upstox history"
        overridden = False
    start = max(requested_start, availability)
    return LookbackPlan(label, start, anchor_date, availability, overridden, start != requested_start)


def minimum_lookback_days_for_candles(timeframe: Timeframe, candles: int) -> int:
    """Conservative calendar-day lower bound used only for diagnostics."""
    if timeframe.intraday:
        minutes = timeframe.interval if timeframe.unit == "minutes" else timeframe.interval * 60
        sessions = max(1, (candles * minutes + 374) // 375)
        return sessions * 2
    if timeframe.unit == "days":
        return candles
    if timeframe.calendar_period == "week":
        return candles * 7
    if timeframe.calendar_period == "month":
        return candles * 28
    if timeframe.aggregate == "QE-DEC":
        return candles * 90
    if timeframe.aggregate == "2QE-DEC":
        return candles * 181
    return candles * 365
