"""Streamlit entry point.  All market screening remains in inside_screener."""
from __future__ import annotations

from datetime import datetime
import os

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from inside_screener.asof import AsOfInputError, ScreeningContext, historical_context
from inside_screener.configuration import validate_count_filter
from inside_screener.instruments import (
    InstrumentError,
    bod_source_metadata,
    discover_fo_stock_underlyings,
    discover_nse_equities,
    load_bod_instruments,
    load_nifty_constituents,
    parse_custom_symbols,
    resolve_custom_symbols,
)
from inside_screener.jobs import JobBusyError, ScanJobManager
from inside_screener.lookback import default_lookback_label
from inside_screener.models import CountFilter, GreenCandleFilter, RESULT_COLUMNS, SmallBodyFilter, TIMEFRAMES, default_cache_dir
from inside_screener.scan import ScanRequest


UNIVERSES = {
    "NSE F&O Stocks": "fo",
    "Nifty 50": "nifty50",
    "Nifty 500": "nifty500",
    "All NSE Equity Stocks": "all_equity",
    "Custom Symbol List": "custom",
}

# Scan context is already displayed above the table; keep the interactive view
# focused on the actionable pattern and filter statistics. The CSV remains full.
TABLE_COLUMNS = [
    "Symbol",
    "Mother Date",
    "Mother High",
    "Mother Low",
    "Mother Range %",
    "Inside Candles",
    "Last Close",
    "Small-Body Candles",
    "Small-Body %",
    "Range/Body Ratio Threshold",
    "Green Candle Count",
    "Red Candle Count",
    "Neutral Candle Count",
    "Green Candle %",
]


@st.cache_resource
def job_manager() -> ScanJobManager:
    return ScanJobManager()


def access_token() -> str | None:
    """Read a server-side token only; it is never rendered or returned to users."""
    load_dotenv()
    token = os.getenv("UPSTOX_ACCESS_TOKEN")
    if token:
        return token
    try:
        return str(st.secrets.get("UPSTOX_ACCESS_TOKEN") or "") or None
    except (FileNotFoundError, KeyError):
        return None


def historical_value(timeframe_key: str, mode: str) -> tuple[ScreeningContext | None, str | None]:
    if mode == "Current":
        return ScreeningContext.current(), None
    timeframe = TIMEFRAMES[timeframe_key]
    if timeframe.intraday or timeframe.unit in {"days", "weeks"}:
        value = st.date_input("Historical date", key="historical_date").isoformat()
    elif timeframe.calendar_period == "month":
        value = st.text_input("Historical month", placeholder="YYYY-MM", key="historical_period")
    elif timeframe.aggregate == "QE-DEC":
        value = st.text_input("Historical quarter", placeholder="YYYY-Q1", key="historical_period")
    elif timeframe.aggregate == "2QE-DEC":
        value = st.text_input("Historical half-year", placeholder="YYYY-H1", key="historical_period")
    else:
        value = st.text_input("Historical year", placeholder="YYYY", key="historical_period")
    try:
        return historical_context(timeframe, value), None
    except AsOfInputError as exc:
        return None, str(exc)


def resolve_universe(key: str, uploaded: st.runtime.uploaded_file_manager.UploadedFile | None, refresh: bool):
    """Use the existing BOD resolver for every universe and disclose mappings."""
    cache_dir = default_cache_dir()
    records = load_bod_instruments(cache_dir, refresh=refresh)
    bod_source, bod_refreshed = bod_source_metadata(cache_dir)
    metadata: dict[str, object] = {
        "bod_records": len(records), "source": bod_source,
        "refreshed_at": bod_refreshed.isoformat(timespec="minutes") if bod_refreshed else "unknown",
        "unresolved": {},
    }
    if key == "fo":
        instruments, unresolved = discover_fo_stock_underlyings(records)
        metadata["unresolved"] = unresolved
    elif key == "all_equity":
        instruments, exclusions = discover_nse_equities(records)
        metadata["exclusions"] = exclusions
    elif key in {"nifty50", "nifty500"}:
        constituents = load_nifty_constituents(cache_dir, key, refresh=refresh)
        instruments, unresolved = resolve_custom_symbols(records, constituents.symbols)
        metadata.update({
            "source": constituents.source,
            "refreshed_at": constituents.refreshed_at,
            "constituents": len(constituents.symbols),
            "unresolved": unresolved,
        })
    else:
        if uploaded is None:
            raise InstrumentError("Upload a CSV or text file with one NSE trading symbol per line.")
        try:
            content = uploaded.getvalue().decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise InstrumentError("Custom symbol file must be UTF-8 text or CSV.") from exc
        instruments, unresolved = resolve_custom_symbols(records, parse_custom_symbols(content))
        metadata["unresolved"] = unresolved
    return instruments, metadata


def report_dataframe(report) -> pd.DataFrame:
    return pd.DataFrame([item.as_row() for item in report.patterns], columns=RESULT_COLUMNS)


def render_results(report) -> None:
    if report.cancelled:
        st.warning("This scan was cancelled. The rows below are partial results and are not a completed screen.")
    elif not report.complete:
        st.warning("This scan ended without complete symbol accounting; review failures and skipped symbols.")
    else:
        st.success("Scan completed.")

    a, b, c, d = st.columns(4)
    a.metric("Symbols scanned", report.scanned)
    b.metric("Matches", len(report.patterns))
    c.metric("Skipped", len(report.skipped))
    d.metric("API failures", len(report.failures))
    st.caption(
        f"Lookback: {report.lookback_plan.requested_label}; requested {report.lookback_plan.start_date} to "
        f"{report.lookback_plan.anchor_date}. Execution: {report.execution_seconds:.1f}s."
    )
    if report.request.screening_context.historical:
        st.info("Historical scan uses the current selected universe; historical listing and constituent membership are not reconstructed.")
    if report.available_starts:
        st.caption(
            f"Effective available starts: {min(report.available_starts):%Y-%m-%d} to {max(report.available_starts):%Y-%m-%d}; "
            f"completed candles per usable symbol: {min(report.available_candle_counts)}–{max(report.available_candle_counts)}."
        )
    if report.coverage_limitations:
        st.warning("Coverage limitations: " + "; ".join(f"{key}: {value}" for key, value in sorted(report.coverage_limitations.items())))

    frame = report_dataframe(report)
    symbol_filter = st.text_input("Filter result symbols", key="result_symbol_filter").strip().upper()
    start, end = st.columns(2)
    mother_start = start.date_input("Mother date from", value=None, key="mother_start")
    mother_end = end.date_input("Mother date to", value=None, key="mother_end")
    minimum_range = st.number_input("Minimum mother range %", min_value=0.0, value=0.0, step=0.1)
    view = frame.copy()
    if symbol_filter:
        view = view[view["Symbol"].astype(str).str.contains(symbol_filter, case=False, na=False)]
    if mother_start is not None:
        view = view[pd.to_datetime(view["Mother Date"]).dt.date >= mother_start]
    if mother_end is not None:
        view = view[pd.to_datetime(view["Mother Date"]).dt.date <= mother_end]
    view = view[pd.to_numeric(view["Mother Range %"], errors="coerce") >= minimum_range]
    st.dataframe(view.loc[:, TABLE_COLUMNS], use_container_width=True, hide_index=True)
    st.download_button(
        "Download full results as CSV",
        data=frame.to_csv(index=False).encode("utf-8"),
        file_name=f"mother_candle_{report.request.timeframe.key}_{datetime.now():%Y%m%d_%H%M%S}.csv",
        mime="text/csv",
        disabled=frame.empty,
    )
    with st.expander("Failures, skipped symbols, and unresolved mappings"):
        st.json({"api_failures": report.failures, "skipped": report.skipped, "continuity_warnings": report.warnings})


def render_live_job_status(manager: ScanJobManager, job_id: str) -> None:
    """Refresh a running job without submitting the configuration again."""
    initial = manager.snapshot(job_id)
    active = initial is not None and initial.state in {"queued", "running"}

    @st.fragment(run_every="2s" if active else None)
    def status_fragment() -> None:
        job = manager.get(job_id)
        snapshot = manager.snapshot(job_id)
        if not job or not snapshot:
            return
        st.subheader("Scan status")
        st.progress(snapshot.completed / snapshot.total if snapshot.total else 0.0, text=f"{snapshot.phase}: {snapshot.completed}/{snapshot.total}")
        st.write(f"Job `{snapshot.id[:8]}` - **{snapshot.state}**")
        if snapshot.state in {"queued", "running"}:
            st.caption("Status refreshes every two seconds. A slow Upstox request can retry before its symbol advances; cancellation stops new work after the current four in-flight requests finish.")
        if snapshot.error:
            st.error(snapshot.error)
        if job.report:
            render_results(job.report)
        if snapshot.state not in {"queued", "running"} and st.session_state.get("last_finished_job") != snapshot.id:
            st.session_state.last_finished_job = snapshot.id
            st.rerun(scope="app")

    status_fragment()


def main() -> None:
    st.set_page_config(page_title="Mother Candle Screener", page_icon="📈", layout="wide")
    st.title("Mother Candle Screener")
    st.caption("Upstox OHLC • NSE equities • completed candles only")
    st.info("Cloud pilot notice: this in-process job manager supports one active scan per app process. Full-universe, long-history scans should use a persistent worker deployment; Community Cloud caches and running jobs are not durable.")
    manager = job_manager()

    with st.sidebar:
        st.header("Screening configuration")
        st.caption("Changing a setting never starts a scan; only Run screener does.")
        universe_label = st.selectbox("Stock universe", list(UNIVERSES))
        universe_key = UNIVERSES[universe_label]
        uploaded = st.file_uploader("Custom symbols (.csv or .txt)", type=["csv", "txt"], disabled=universe_key != "custom")
        timeframe_key = st.selectbox(
            "Timeframe", list(TIMEFRAMES), index=list(TIMEFRAMES).index("daily"), format_func=lambda key: TIMEFRAMES[key].label
        )
        mode = st.radio("Screening mode", ["Current", "Historical"], horizontal=True)
        context, context_error = historical_value(timeframe_key, mode)
        st.caption(f"Default lookback: {default_lookback_label(TIMEFRAMES[timeframe_key])}")
        override_enabled = st.checkbox("Override lookback")
        lookback_override = int(st.number_input("Lookback calendar days", min_value=1, value=365, step=1)) if override_enabled else None
        minimum = int(st.number_input("Minimum inside candles", min_value=1, value=7, step=1))
        range_enabled = st.checkbox("Set maximum inside candles")
        maximum = int(st.number_input("Maximum inside candles", min_value=minimum, value=minimum, step=1)) if range_enabled else None

        small_enabled = st.checkbox("Enable small-body filter", value=True)
        small_filter = None
        if small_enabled:
            ratio = float(st.number_input("Minimum strict Range/Body ratio", min_value=0.0, value=2.0, step=0.1))
            small_mode = st.radio("Small-body filter method", ["Minimum number", "Minimum percentage"], index=1, horizontal=True)
            if small_mode == "Minimum number":
                small_filter = SmallBodyFilter(ratio, "count", int(st.number_input("Minimum small-body candles", min_value=0, value=1, step=1)))
            else:
                small_filter = SmallBodyFilter(ratio, "percentage", float(st.number_input("Minimum small-body %", min_value=0.0, max_value=100.0, value=50.0, step=0.1)))

        green_enabled = st.checkbox("Enable green candle filter", value=True)
        green_filter = GreenCandleFilter(float(st.number_input("Minimum green candle %", min_value=0.0, max_value=100.0, value=50.0, step=0.1))) if green_enabled else None
        refresh = st.checkbox("Refresh BOD / constituent lists")
        force_refresh = st.checkbox("Bypass OHLC cache (slower)")
        submitted = st.button("Run screener", type="primary", use_container_width=True)

        current = manager.get(st.session_state.get("scan_job_id"))
        if current and current.state in {"queued", "running"} and st.button("Cancel scan"):
            manager.cancel(current.id)
            st.rerun()

    if submitted:
        token = access_token()
        if not token:
            st.error("Set UPSTOX_ACCESS_TOKEN locally or in Streamlit secrets. It is read only on the server and is never requested from app users.")
        elif context_error or context is None:
            st.error(context_error or "Choose a valid historical cutoff.")
        else:
            try:
                instruments, metadata = resolve_universe(universe_key, uploaded, refresh)
                if not instruments:
                    raise InstrumentError("No eligible Upstox NSE_EQ instruments were resolved for this universe.")
                request = ScanRequest(
                    access_token=token,
                    timeframe=TIMEFRAMES[timeframe_key],
                    screening_context=context,
                    universe_label=universe_label,
                    instruments=instruments,
                    count_filter=validate_count_filter(minimum, maximum),
                    cache_dir=default_cache_dir(),
                    lookback_override=lookback_override,
                    small_body_filter=small_filter,
                    green_candle_filter=green_filter,
                    force_refresh_data=force_refresh,
                    refresh_calendar=refresh,
                    batch_size=max(1, int(os.getenv("UPSTOX_SCAN_BATCH_SIZE", "100"))),
                )
                job = manager.start(request)
                st.session_state.scan_job_id = job.id
                st.session_state.universe_metadata = metadata
                st.rerun()
            except (InstrumentError, ValueError, JobBusyError) as exc:
                st.error(str(exc))

    metadata = st.session_state.get("universe_metadata")
    if metadata:
        st.subheader("Selected universe")
        st.json(metadata)

    job = manager.get(st.session_state.get("scan_job_id"))
    if job:
        render_live_job_status(manager, job.id)


if __name__ == "__main__":
    main()
