"""Validation shared by interactive front ends before a scan job is created."""
from __future__ import annotations

from .models import CountFilter


def validate_count_filter(minimum: int, maximum: int | None) -> CountFilter:
    if minimum < 1:
        raise ValueError("Minimum inside candles must be at least 1.")
    if maximum is not None and maximum < minimum:
        raise ValueError("Maximum inside candles must be at least the minimum.")
    return CountFilter(minimum, maximum)
