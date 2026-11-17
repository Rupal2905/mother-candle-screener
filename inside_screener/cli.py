from __future__ import annotations

import argparse
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from .asof import AsOfInputError, ScreeningContext, historical_context
from .auth import AuthenticationError, obtain_token_interactively
from .calendar import now_ist
from .data import UpstoxAuthenticationError
from .instruments import (
    InstrumentError, bod_source_metadata, discover_fo_stock_underlyings, discover_nse_equities,
    load_bod_instruments, load_custom_symbols, load_nifty_constituents, resolve_custom_symbols,
)
from .lookback import default_lookback_label, resolve_lookback
from .models import CountFilter, GreenCandleFilter, OutputChoice, Pattern, RESULT_COLUMNS, SmallBodyFilter, TIMEFRAMES, default_cache_dir
from .scan import ScanRequest, scan


console = Console()


@dataclass(frozen=True)
class UniverseSelection:
    key: str
    label: str
    custom_file: Path | None = None


def _ask_choice(prompt: str, allowed: set[str]) -> str:
    while True:
        answer = console.input(prompt).strip().lower()
        if answer in allowed:
            return answer
        console.print(f"[yellow]Choose one of: {', '.join(sorted(allowed))}[/yellow]")


def _positive_int(prompt: str) -> int:
    while True:
        try:
            value = int(console.input(prompt).strip())
            if value > 0:
                return value
        except ValueError:
            pass
        console.print("[yellow]Enter a positive whole number.[/yellow]")


def _non_negative_int(prompt: str) -> int:
    while True:
        try:
            value = int(console.input(prompt).strip())
            if value >= 0:
                return value
        except ValueError:
            pass
        console.print("[yellow]Enter a non-negative whole number.[/yellow]")


def _percentage(prompt: str, default: float | None = None) -> float:
    while True:
        try:
            answer = console.input(prompt).strip()
            if not answer and default is not None:
                return default
            value = float(answer)
            if math.isfinite(value) and 0 <= value <= 100:
                return value
        except ValueError:
            pass
        console.print("[yellow]Enter a number from 0 to 100.[/yellow]")


def _ratio_threshold(prompt: str, default: float = 2.0) -> float:
    while True:
        answer = console.input(prompt).strip()
        if not answer:
            return default
        try:
            value = float(answer)
            if math.isfinite(value) and value >= 0:
                return value
        except ValueError:
            pass
        console.print(f"[yellow]Enter a non-negative number, or press Enter for {default:g}.[/yellow]")


def _ask_screening_context(timeframe_key: str) -> ScreeningContext:
    mode = _ask_choice("Screening Mode:\n[1] Current (latest completed candle)\n[2] Historical (select date or period)\nSelect [1]: ", {"", "1", "2"})
    if mode in {"", "1"}:
        return ScreeningContext.current()
    timeframe = TIMEFRAMES[timeframe_key]
    if timeframe.intraday or timeframe.unit in {"days", "weeks"}:
        prompt = "Enter historical date (YYYY-MM-DD): "
    elif timeframe.calendar_period == "month":
        prompt = "Enter historical month (YYYY-MM): "
    elif timeframe.aggregate == "QE-DEC":
        prompt = "Enter historical quarter (YYYY-Q1/Q2/Q3/Q4): "
    elif timeframe.aggregate == "2QE-DEC":
        prompt = "Enter historical half-year (YYYY-H1/H2): "
    else:
        prompt = "Enter historical year (YYYY): "
    while True:
        try:
            return historical_context(timeframe, console.input(prompt))
        except AsOfInputError as exc:
            console.print(f"[yellow]{exc}[/yellow]")


def _ask_universe() -> UniverseSelection:
    selection = _ask_choice("Select Stock Universe:\n[1] NSE F&O Stocks\n[2] Nifty 50\n[3] Nifty 500\n[4] All NSE Equity Stocks\n[5] Custom Symbol List\nSelection [1]: ", {"", "1", "2", "3", "4", "5"})
    if selection in {"", "1"}:
        return UniverseSelection("fo", "NSE F&O Stocks")
    if selection == "2":
        return UniverseSelection("nifty50", "Nifty 50")
    if selection == "3":
        return UniverseSelection("nifty500", "Nifty 500")
    if selection == "4":
        return UniverseSelection("all_equity", "All NSE Equity Stocks")
    while True:
        path = Path(console.input("Custom symbol CSV or text file path: ").strip().strip('"'))
        if path.is_file():
            return UniverseSelection("custom", "Custom Symbol List", path)
        console.print("[yellow]Enter an existing CSV or text file path.[/yellow]")


def _ask_lookback_override(timeframe_key: str) -> int | None:
    load_dotenv()
    configured = os.getenv("UPSTOX_LOOKBACK_DAYS", "").strip()
    fallback = f"UPSTOX_LOOKBACK_DAYS={configured}" if configured else default_lookback_label(TIMEFRAMES[timeframe_key])
    while True:
        answer = console.input(f"Lookback (Enter for {fallback}; positive calendar days override): ").strip()
        if not answer:
            return None
        try:
            value = int(answer)
            if value > 0:
                return value
        except ValueError:
            pass
        console.print("[yellow]Enter a positive whole number of calendar days, or press Enter for the timeframe default.[/yellow]")


def ask_options() -> tuple[str, ScreeningContext, UniverseSelection, int | None, CountFilter, SmallBodyFilter | None, GreenCandleFilter | None, OutputChoice, bool]:
    console.print("\n[bold]Mother Candle Screener - Upstox / NSE[/bold]")
    console.print("Timeframes: " + ", ".join(TIMEFRAMES))
    timeframe = _ask_choice("Select timeframe: ", set(TIMEFRAMES))
    screening_context = _ask_screening_context(timeframe)
    universe = _ask_universe()
    lookback_override = _ask_lookback_override(timeframe)
    mode = _ask_choice("Inside count - [1] minimum only, [2] range: ", {"1", "2"})
    minimum = _positive_int("Minimum completed inside candles: ")
    maximum = None
    if mode == "2":
        while maximum is None or maximum < minimum:
            maximum = _positive_int("Maximum completed inside candles: ")
            if maximum < minimum:
                console.print("[yellow]Maximum must be at least the minimum.[/yellow]")
    small_body_filter = None
    if _ask_choice("Enable small-body candle filter? [y/n]: ", {"y", "n"}) == "y":
        ratio = _ratio_threshold("Range/Body ratio threshold (default 2): ")
        filter_mode = _ask_choice("Filter small-body candles by:\n[1] Minimum number\n[2] Minimum percentage\nSelection: ", {"1", "2"})
        if filter_mode == "1":
            small_body_filter = SmallBodyFilter(ratio, "count", _non_negative_int("Minimum number of small-body candles: "))
        else:
            small_body_filter = SmallBodyFilter(ratio, "percentage", _percentage("Minimum percentage of inside candles that must be small-body (0-100): "))
    green_candle_filter = None
    if _ask_choice("Enable green candle percentage filter? [y/n]: ", {"y", "n"}) == "y":
        green_candle_filter = GreenCandleFilter(_percentage("Minimum green candle percentage of inside candles (0-100, default 50): ", default=50))
    output_raw = _ask_choice("Output - [1] terminal, [2] CSV, [3] both: ", {"1", "2", "3"})
    output: OutputChoice = {"1": "terminal", "2": "csv", "3": "both"}[output_raw]  # type: ignore[assignment]
    refresh = _ask_choice("Refresh cached Upstox BOD instruments? [y/N]: ", {"y", "n", ""}) == "y"
    return timeframe, screening_context, universe, lookback_override, CountFilter(minimum, maximum), small_body_filter, green_candle_filter, output, refresh


def _display(patterns: list[Pattern]) -> None:
    table = Table(title="Active Mother Candle Patterns")
    for name in RESULT_COLUMNS:
        table.add_column(name, justify="right" if "%" in name or name in {"Mother High", "Mother Low", "Last Close", "Inside Candles"} else "left")
    for pattern in patterns:
        row = pattern.as_row()
        table.add_row(*(str(row[column]) for column in RESULT_COLUMNS))
    console.print(table)


def _write_csv(patterns: list[Pattern], timeframe: str, screening_context: ScreeningContext | None = None) -> Path:
    context = screening_context or ScreeningContext.current()
    requested = context.requested.replace("-", "").replace(" ", "_").lower()
    destination = Path.cwd() / f"mother_inside_upstox_nse_{timeframe}_{context.mode.lower()}_{requested}_{datetime.now():%Y%m%d_%H%M%S}.csv"
    pd.DataFrame([pattern.as_row() for pattern in patterns], columns=RESULT_COLUMNS).to_csv(destination, index=False)
    return destination


def _discover(universe: UniverseSelection, cache_dir: Path, refresh: bool):
    records = load_bod_instruments(cache_dir, refresh)
    bod_source, bod_refreshed = bod_source_metadata(cache_dir)
    exclusions: dict[str, int] = {}
    source = None
    if universe.key == "fo":
        instruments, unresolved = discover_fo_stock_underlyings(records)
    elif universe.key == "all_equity":
        instruments, exclusions = discover_nse_equities(records)
        unresolved = {}
    elif universe.key in {"nifty50", "nifty500"}:
        constituents = load_nifty_constituents(cache_dir, universe.key, refresh)
        instruments, unresolved = resolve_custom_symbols(records, constituents.symbols)
        source = f"{constituents.source} | refreshed: {constituents.refreshed_at:%Y-%m-%d %H:%M} | listed symbols: {len(constituents.symbols)}"
    else:
        assert universe.custom_file is not None
        instruments, unresolved = resolve_custom_symbols(records, load_custom_symbols(universe.custom_file))
    return records, instruments, unresolved, exclusions, source, bod_source, bod_refreshed


def _report_details(report) -> None:
    if report.available_starts:
        console.print(f"Coverage: requested from {report.lookback_plan.start_date}; effective available starts range {min(report.available_starts):%Y-%m-%d} to {max(report.available_starts):%Y-%m-%d}; completed candle count range {min(report.available_candle_counts)} to {max(report.available_candle_counts)}.")
    if report.coverage_limitations:
        console.print("[yellow]Coverage limitations:[/yellow] " + ", ".join(f"{reason}: {count}" for reason, count in sorted(report.coverage_limitations.items())))
    if report.request.screening_context.historical and report.effective_last_candles:
        effective = ", ".join(stamp.strftime("%Y-%m-%d %H:%M %Z").strip() for stamp in sorted(report.effective_last_candles))
        console.print(f"[cyan]Effective last completed candle(s): {effective}[/cyan]")
    for heading, values in (("API failures", report.failures), ("Skipped symbols", report.skipped), ("Continuity warnings", report.warnings)):
        if values:
            console.print(f"[yellow]{heading}:[/yellow]")
            for symbol, reason in sorted(values.items()):
                console.print(f"  {symbol}: {reason}")


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upstox NSE mother candle screener")
    parser.add_argument("--test", action="store_true", help="scan only UPSTOX_TEST_SYMBOL_LIMIT symbols after discovery")
    parser.add_argument("--refresh-data", action="store_true", help="bypass local OHLC cache for a live Upstox timing/data check")
    parser.add_argument("--benchmark", action="store_true", help="non-interactive Daily/minimum-7 five-symbol test benchmark")
    args = parser.parse_args(argv)
    wall_started = time.monotonic()
    if args.benchmark:
        args.test = True
        options = ("daily", ScreeningContext.current(), UniverseSelection("fo", "NSE F&O Stocks"), None, CountFilter(7), None, None, "terminal", False)
        console.print("[cyan]Benchmark mode: Daily, minimum 7, terminal output, test-symbol limit only.[/cyan]")
    else:
        options = ask_options()
    timeframe_key, screening_context, universe, lookback_override, count_filter, small_body_filter, green_candle_filter, output, refresh = options
    try:
        token = obtain_token_interactively()
    except AuthenticationError as exc:
        console.print(f"[red]Authentication unavailable: {exc}[/red]")
        return 2
    cache_dir = default_cache_dir()
    try:
        discovery_started = time.monotonic()
        records, instruments, unresolved, exclusions, source, bod_source, bod_refreshed = _discover(universe, cache_dir, refresh)
        discovery_seconds = time.monotonic() - discovery_started
    except InstrumentError as exc:
        console.print(f"[red]{exc}[/red]")
        return 2
    eligible_count = len(instruments)
    if args.test:
        instruments = instruments[:int(os.getenv("UPSTOX_TEST_SYMBOL_LIMIT", "5"))]
        console.print(f"[cyan]Test mode: scanning {len(instruments)} symbols.[/cyan]")
    console.print(f"Universe: [bold]{universe.label}[/bold] | BOD instruments: {len(records)} | eligible symbols: {eligible_count} | selected to scan: {len(instruments)}.")
    console.print(f"BOD source: {bod_source} | refreshed: {bod_refreshed:%Y-%m-%d %H:%M}" if bod_refreshed else f"BOD source: {bod_source} | refresh time unavailable")
    if source:
        console.print(f"Official constituent source: {source}.")
    if exclusions:
        console.print("[yellow]Excluded NSE_EQ categories:[/yellow] " + ", ".join(f"{name}: {count}" for name, count in exclusions.items()))
    if unresolved:
        heading = "Invalid custom symbols" if universe.key == "custom" else "Unresolved mappings"
        console.print(f"[yellow]{heading}:[/yellow] " + ", ".join(sorted(unresolved)))
    timeframe = TIMEFRAMES[timeframe_key]
    try:
        plan = resolve_lookback(timeframe, screening_context.data_cutoff or now_ist(), lookback_override)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        return 2
    console.print(f"Lookback: {plan.requested_label} ({plan.days} calendar days), {plan.start_date} to {plan.anchor_date}, anchored to the selected cutoff.")
    if screening_context.historical:
        console.print(f"[cyan]Historical mode: requested {screening_context.requested}; historical scan using current {universe.label} universe; historical listings and constituents are not reconstructed.[/cyan]")
    try:
        batch_size = max(1, int(os.getenv("UPSTOX_SCAN_BATCH_SIZE", "100")))
    except ValueError:
        batch_size = 100
    request = ScanRequest(token, timeframe, screening_context, universe.label, instruments, count_filter, cache_dir, lookback_override, small_body_filter, green_candle_filter, args.refresh_data, refresh, batch_size)
    console.print(f"Scanning in batches of {batch_size}; completed OHLC caches make interrupted scans resumable without refetching covered symbols.")
    try:
        with Progress(TextColumn("Scanning"), BarColumn(), TextColumn("{task.completed}/{task.total}"), TimeElapsedColumn(), console=console) as progress:
            task = progress.add_task("scan", total=len(instruments))
            report = scan(request, lambda value: progress.update(task, completed=value.completed))
    except UpstoxAuthenticationError as exc:
        console.print(f"[red]{exc}[/red]")
        return 2
    _report_details(report)
    if output in {"terminal", "both"}:
        _display(report.patterns)
    if output in {"csv", "both"}:
        location = _write_csv(report.patterns, timeframe.key, screening_context)
        console.print(f"CSV written: [green]{location}[/green]")
    metrics = report.metrics
    console.print(f"\n[bold]Summary:[/bold] {universe.label} | {len(instruments)} selected | {report.scanned} scanned | {len(report.patterns)} matches | {report.insufficient_history} insufficient history | {len(report.failures)} API failures | {len(report.skipped)} skipped | {report.execution_seconds:.1f}s execution")
    if metrics:
        console.print(f"Upstox requests: {metrics.request_count} attempts ({metrics.retry_count} retries), {metrics.request_seconds:.1f}s HTTP time, {metrics.pacer_wait_seconds:.2f}s limiter wait, {metrics.cache_hits} OHLC cache hits.")
    console.print(f"Timing - BOD: {discovery_seconds:.1f}s | calendar: {report.calendar_seconds:.1f}s | OHLC phase: {report.download_seconds:.1f}s | screening: {report.screening_seconds:.3f}s | wall clock: {time.monotonic() - wall_started:.1f}s.")
    console.print("When overlapping active mothers exist, results keep only the oldest mother candle per symbol and timeframe.")
    return 0
