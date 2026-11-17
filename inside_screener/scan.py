"""Shared batch scanner used by both the terminal CLI and Streamlit UI."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
import time
from pathlib import Path
from typing import Callable

import pandas as pd

from .asof import ScreeningContext
from .calendar import now_ist
from .data import RequestMetrics, UpstoxCandleClient, fetch_many, prepare_timeframe
from .logic import find_active_patterns, keep_oldest_mother_patterns, validate_ohlc
from .lookback import LookbackPlan, minimum_lookback_days_for_candles, resolve_lookback
from .models import CountFilter, GreenCandleFilter, Instrument, Pattern, SmallBodyFilter, Timeframe
from .nse_calendar import load_nse_trading_calendar


@dataclass(frozen=True)
class ScanRequest:
    access_token: str
    timeframe: Timeframe
    screening_context: ScreeningContext
    universe_label: str
    instruments: list[Instrument]
    count_filter: CountFilter
    cache_dir: Path
    lookback_override: int | None = None
    small_body_filter: SmallBodyFilter | None = None
    green_candle_filter: GreenCandleFilter | None = None
    force_refresh_data: bool = False
    refresh_calendar: bool = False
    batch_size: int = 100


@dataclass(frozen=True)
class ScanProgress:
    completed: int
    total: int
    phase: str


@dataclass
class ScanReport:
    request: ScanRequest
    lookback_plan: LookbackPlan
    patterns: list[Pattern] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    warnings: dict[str, str] = field(default_factory=dict)
    coverage_limitations: Counter[str] = field(default_factory=Counter)
    available_starts: list[pd.Timestamp] = field(default_factory=list)
    available_candle_counts: list[int] = field(default_factory=list)
    effective_last_candles: set[pd.Timestamp] = field(default_factory=set)
    scanned: int = 0
    insufficient_history: int = 0
    cancelled: bool = False
    calendar_seconds: float = 0.0
    download_seconds: float = 0.0
    screening_seconds: float = 0.0
    execution_seconds: float = 0.0
    metrics: RequestMetrics | None = None

    @property
    def complete(self) -> bool:
        return not self.cancelled and self.scanned + len(self.failures) + len(self.skipped) == len(self.request.instruments)


def scan(
    request: ScanRequest,
    progress: Callable[[ScanProgress], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> ScanReport:
    """Screen bounded batches while retaining only one batch of OHLC frames.

    Cancellation is intentionally observed between batches: a batch already in
    flight is allowed to finish so its validated per-symbol cache entries can be
    reused by a later run.
    """
    started = time.monotonic()
    anchor = request.screening_context.data_cutoff or now_ist()
    plan = resolve_lookback(request.timeframe, anchor, request.lookback_override)
    report = ScanReport(request=request, lookback_plan=plan)

    calendar_started = time.monotonic()
    trading_calendar = load_nse_trading_calendar(
        request.cache_dir, plan.start_date, anchor.date(), refresh=request.refresh_calendar
    )
    report.calendar_seconds = time.monotonic() - calendar_started
    client = UpstoxCandleClient(request.access_token, request.cache_dir, force_refresh=request.force_refresh_data)
    batch_size = max(1, request.batch_size)
    total = len(request.instruments)

    for offset in range(0, total, batch_size):
        if cancelled and cancelled():
            report.cancelled = True
            break
        batch = request.instruments[offset : offset + batch_size]
        if progress:
            progress(ScanProgress(offset, total, "Downloading OHLC"))
        download_started = time.monotonic()
        raw, batch_failures = fetch_many(
            client,
            batch,
            request.timeframe,
            plan.days,
            lambda done, _: progress(ScanProgress(offset + done, total, "Downloading OHLC")) if progress else None,
            as_of=request.screening_context.data_cutoff,
            start_date=plan.start_date,
            cancelled=cancelled,
        )
        report.download_seconds += time.monotonic() - download_started
        report.failures.update(batch_failures)
        if cancelled and cancelled():
            # Deliberately do not classify a partially processed batch as done.
            report.cancelled = True
            del raw
            break

        screening_started = time.monotonic()
        for instrument in batch:
            if instrument.symbol in batch_failures:
                continue
            source_frame = raw[instrument.symbol]
            if source_frame.empty:
                report.skipped[instrument.symbol] = "missing historical data from Upstox for the requested coverage"
                report.coverage_limitations["missing historical data from Upstox"] += 1
                continue
            prepared, reason = prepare_timeframe(
                source_frame,
                request.timeframe,
                trading_calendar,
                as_of=request.screening_context.completion_as_of,
                data_cutoff=request.screening_context.data_cutoff,
            )
            if reason:
                report.skipped[instrument.symbol] = reason
                report.coverage_limitations[reason] += 1
                continue
            if prepared.attrs.get("continuity_warning"):
                report.warnings[instrument.symbol] = str(prepared.attrs["continuity_warning"])
            validity = validate_ohlc(prepared)
            if validity:
                report.skipped[instrument.symbol] = validity
                report.coverage_limitations[validity] += 1
                continue
            report.available_starts.append(pd.Timestamp(prepared.index[0]))
            report.available_candle_counts.append(len(prepared))
            if len(prepared) < request.count_filter.minimum + 1:
                report.insufficient_history += 1
                required_candles = request.count_filter.minimum + 1
                required_days = minimum_lookback_days_for_candles(request.timeframe, required_candles)
                late_start_days = (prepared.index[0].date() - plan.start_date).days
                late_start_tolerance = 35 if request.timeframe.unit in {"weeks", "months"} else 7
                if plan.overridden and plan.days < required_days:
                    reason = f"configured lookback too short: {plan.days} days cannot reasonably contain {required_candles} {request.timeframe.label} candles"
                    report.coverage_limitations["configured lookback too short"] += 1
                elif prepared.attrs.get("continuity_warning"):
                    reason = f"insufficient history after genuine missing trading-session protection: {prepared.attrs['continuity_warning']}"
                    report.coverage_limitations["genuine missing trading sessions"] += 1
                elif late_start_days > late_start_tolerance:
                    reason = f"insufficient history: available candles begin {prepared.index[0].date()} (recent listing or unavailable Upstox history)"
                    report.coverage_limitations["recent listing or unavailable Upstox history"] += 1
                else:
                    reason = f"insufficient completed history in requested coverage: {len(prepared)} candle(s); need {required_candles}"
                    report.coverage_limitations["insufficient completed candles"] += 1
                report.skipped[instrument.symbol] = reason
                continue
            report.scanned += 1
            report.effective_last_candles.add(pd.Timestamp(prepared.index[-1]))
            report.patterns.extend(
                find_active_patterns(
                    prepared,
                    instrument.symbol,
                    request.timeframe.label,
                    request.count_filter,
                    request.small_body_filter,
                    request.green_candle_filter,
                    request.screening_context.mode,
                    request.screening_context.requested,
                    pd.Timestamp(prepared.index[-1]),
                    request.universe_label,
                )
            )
        report.screening_seconds += time.monotonic() - screening_started
        del raw
        if progress:
            progress(ScanProgress(min(offset + len(batch), total), total, "Screening"))

    report.patterns = keep_oldest_mother_patterns(report.patterns)
    report.patterns.sort(key=lambda item: (-item.inside_candles, item.symbol, item.mother_date))
    report.metrics = client.metrics
    report.execution_seconds = time.monotonic() - started
    return report
