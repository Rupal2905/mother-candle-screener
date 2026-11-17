"""Historical screening cutoffs, independent of pattern detection."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import re

from .calendar import IST, now_ist
from .models import Timeframe


class AsOfInputError(ValueError):
    pass


@dataclass(frozen=True)
class ScreeningContext:
    mode: str
    requested: str
    data_cutoff: datetime | None = None
    completion_as_of: datetime | None = None

    @classmethod
    def current(cls) -> "ScreeningContext":
        return cls("Current", "Latest completed candle")

    @property
    def historical(self) -> bool:
        return self.mode == "Historical"


def _end_of_day(day: date) -> datetime:
    return datetime.combine(day, time.max, tzinfo=IST)


def _last_day(year: int, month: int) -> date:
    first_next = date(year + (month == 12), month % 12 + 1, 1)
    return first_next - timedelta(days=1)


def _validate_past(day: date, now: datetime) -> None:
    if day >= now.date():
        raise AsOfInputError("Historical cutoffs must be before today; use Current mode for the latest completed candle.")


def historical_context(timeframe: Timeframe, raw: str, now: datetime | None = None) -> ScreeningContext:
    """Parse a timeframe-specific historical selection into safe data cutoffs."""
    now = now or now_ist()
    value = raw.strip().upper()
    try:
        if timeframe.intraday or timeframe.unit in {"days", "weeks"}:
            selected = date.fromisoformat(value)
            _validate_past(selected, now)
            cutoff = _end_of_day(selected)
            return ScreeningContext("Historical", selected.isoformat(), cutoff, cutoff)

        if timeframe.calendar_period == "month":
            year, month = map(int, value.split("-"))
            selected = _last_day(year, month)
            _validate_past(selected, now)
            return ScreeningContext("Historical", f"{year:04d}-{month:02d}", _end_of_day(selected), _end_of_day(selected + timedelta(days=1)))

        quarter = re.fullmatch(r"(\d{4})-Q([1-4])", value)
        if timeframe.aggregate == "QE-DEC" and quarter:
            year, number = int(quarter.group(1)), int(quarter.group(2))
            selected = _last_day(year, number * 3)
            _validate_past(selected, now)
            return ScreeningContext("Historical", value, _end_of_day(selected), _end_of_day(selected + timedelta(days=1)))

        half = re.fullmatch(r"(\d{4})-H([12])", value)
        if timeframe.aggregate == "2QE-DEC" and half:
            year, number = int(half.group(1)), int(half.group(2))
            selected = _last_day(year, 6 if number == 1 else 12)
            _validate_past(selected, now)
            return ScreeningContext("Historical", value, _end_of_day(selected), _end_of_day(selected + timedelta(days=1)))

        if timeframe.aggregate == "YE-DEC" and re.fullmatch(r"\d{4}", value):
            selected = date(int(value), 12, 31)
            _validate_past(selected, now)
            return ScreeningContext("Historical", value, _end_of_day(selected), _end_of_day(selected + timedelta(days=1)))
    except (TypeError, ValueError) as exc:
        raise AsOfInputError("Invalid historical selection for the chosen timeframe.") from exc

    if timeframe.calendar_period == "month":
        expected = "YYYY-MM"
    elif timeframe.aggregate == "QE-DEC":
        expected = "YYYY-Q1, YYYY-Q2, YYYY-Q3, or YYYY-Q4"
    elif timeframe.aggregate == "2QE-DEC":
        expected = "YYYY-H1 or YYYY-H2"
    elif timeframe.aggregate == "YE-DEC":
        expected = "YYYY"
    else:
        expected = "YYYY-MM-DD"
    raise AsOfInputError(f"Enter {expected}.")
