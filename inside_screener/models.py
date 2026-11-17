from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Literal

import pandas as pd


@dataclass(frozen=True)
class Timeframe:
    key: str
    label: str
    unit: str
    interval: int
    intraday: bool = False
    calendar_period: str | None = None
    aggregate: str | None = None


TIMEFRAMES: dict[str, Timeframe] = {
    "5m": Timeframe("5m", "5 minutes", "minutes", 5, intraday=True),
    "15m": Timeframe("15m", "15 minutes", "minutes", 15, intraday=True),
    "30m": Timeframe("30m", "30 minutes", "minutes", 30, intraday=True),
    "60m": Timeframe("60m", "60 minutes", "minutes", 60, intraday=True),
    "90m": Timeframe("90m", "90 minutes", "minutes", 90, intraday=True),
    "2h": Timeframe("2h", "2 hours", "hours", 2, intraday=True),
    "3h": Timeframe("3h", "3 hours", "hours", 3, intraday=True),
    "4h": Timeframe("4h", "4 hours", "hours", 4, intraday=True),
    "5h": Timeframe("5h", "5 hours", "hours", 5, intraday=True),
    "daily": Timeframe("daily", "Daily", "days", 1),
    "weekly": Timeframe("weekly", "Weekly", "weeks", 1, calendar_period="week"),
    "monthly": Timeframe("monthly", "Monthly", "months", 1, calendar_period="month"),
    "quarterly": Timeframe("quarterly", "Quarterly", "months", 1, aggregate="QE-DEC"),
    "half_yearly": Timeframe("half_yearly", "Half-yearly", "months", 1, aggregate="2QE-DEC"),
    "yearly": Timeframe("yearly", "Yearly", "months", 1, aggregate="YE-DEC"),
}


@dataclass(frozen=True)
class CountFilter:
    minimum: int
    maximum: int | None = None

    def accepts(self, count: int) -> bool:
        return count >= self.minimum and (self.maximum is None or count <= self.maximum)


SmallBodyFilterMode = Literal["count", "percentage"]


@dataclass(frozen=True)
class SmallBodyFilter:
    """Optional criteria for small bodies among a pattern's inside candles."""

    ratio_threshold: float
    mode: SmallBodyFilterMode
    minimum: int | float

    def __post_init__(self) -> None:
        if not math.isfinite(self.ratio_threshold) or self.ratio_threshold < 0:
            raise ValueError("Range/body ratio threshold must be non-negative.")
        if self.mode == "count":
            if not float(self.minimum).is_integer() or self.minimum < 0:
                raise ValueError("Minimum small-body candle count must be a non-negative whole number.")
        elif self.mode == "percentage":
            if not 0 <= self.minimum <= 100:
                raise ValueError("Minimum small-body percentage must be between 0 and 100.")
        else:
            raise ValueError("Small-body filter mode must be 'count' or 'percentage'.")

    def accepts(self, small_body_candles: int, inside_candles: int) -> bool:
        if self.mode == "count":
            return small_body_candles >= self.minimum
        return small_body_candles * 100 >= inside_candles * self.minimum


@dataclass(frozen=True)
class GreenCandleFilter:
    """Optional minimum proportion of green inside candles for a pattern."""

    minimum_percentage: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.minimum_percentage) or not 0 <= self.minimum_percentage <= 100:
            raise ValueError("Minimum green candle percentage must be between 0 and 100.")

    def accepts(self, green_candles: int, inside_candles: int) -> bool:
        # Compare the unrounded values. Equality deliberately passes.
        return green_candles * 100 >= inside_candles * self.minimum_percentage


@dataclass(frozen=True)
class Instrument:
    symbol: str
    instrument_key: str


@dataclass(frozen=True)
class Pattern:
    symbol: str
    timeframe: str
    mother_date: pd.Timestamp
    mother_high: float
    mother_low: float
    inside_candles: int
    last_close: float
    small_body_candles: int | None = None
    ratio_threshold: float | None = None
    green_candles: int | None = None
    red_candles: int | None = None
    neutral_candles: int | None = None
    screening_mode: str = "Current"
    requested_as_of: str = "Latest completed candle"
    effective_last_candle: pd.Timestamp | None = None
    universe: str = "Current NSE F&O stocks"

    @property
    def distance_to_high_pct(self) -> float:
        return (self.mother_high - self.last_close) / self.mother_high * 100

    @property
    def distance_to_low_pct(self) -> float:
        return (self.last_close - self.mother_low) / self.mother_low * 100

    @property
    def range_pct(self) -> float:
        return (self.mother_high - self.mother_low) / self.mother_low * 100

    @property
    def small_body_pct(self) -> float | None:
        if self.small_body_candles is None:
            return None
        return self.small_body_candles / self.inside_candles * 100

    @property
    def green_candle_pct(self) -> float | None:
        if self.green_candles is None:
            return None
        return self.green_candles / self.inside_candles * 100

    def as_row(self) -> dict[str, object]:
        return {
            "Screening Mode": self.screening_mode,
            "Requested As-Of": self.requested_as_of,
            "Effective Last Completed Candle": (
                self.effective_last_candle.strftime("%Y-%m-%d %H:%M %Z").strip()
                if self.effective_last_candle is not None else "N/A"
            ),
            "Universe": self.universe,
            "Symbol": self.symbol,
            "Timeframe": self.timeframe,
            "Mother Date": self.mother_date.strftime("%Y-%m-%d %H:%M %Z").strip(),
            "Mother High": round(self.mother_high, 4),
            "Mother Low": round(self.mother_low, 4),
            "Inside Candles": self.inside_candles,
            "Last Close": round(self.last_close, 4),
            "Distance to High %": round(self.distance_to_high_pct, 4),
            "Distance to Low %": round(self.distance_to_low_pct, 4),
            "Mother Range %": round(self.range_pct, 4),
            "Small-Body Candles": self.small_body_candles if self.small_body_candles is not None else "N/A",
            "Small-Body %": round(self.small_body_pct, 4) if self.small_body_pct is not None else "N/A",
            "Range/Body Ratio Threshold": round(self.ratio_threshold, 4) if self.ratio_threshold is not None else "N/A",
            "Green Candle Count": self.green_candles if self.green_candles is not None else "N/A",
            "Red Candle Count": self.red_candles if self.red_candles is not None else "N/A",
            "Neutral Candle Count": self.neutral_candles if self.neutral_candles is not None else "N/A",
            "Green Candle %": round(self.green_candle_pct, 4) if self.green_candle_pct is not None else "N/A",
        }


OutputChoice = Literal["terminal", "csv", "both"]


RESULT_COLUMNS = [
    "Screening Mode", "Requested As-Of", "Effective Last Completed Candle", "Universe",
    "Symbol", "Timeframe", "Mother Date", "Mother High", "Mother Low", "Inside Candles", "Last Close",
    "Distance to High %", "Distance to Low %", "Mother Range %", "Small-Body Candles", "Small-Body %",
    "Range/Body Ratio Threshold", "Green Candle Count", "Red Candle Count", "Neutral Candle Count", "Green Candle %",
]


def default_cache_dir() -> Path:
    return Path(__file__).resolve().parent.parent / ".cache"
