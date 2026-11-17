"""Rate-limited Upstox V3 candle retrieval and local OHLC caching."""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
import threading
import time
from typing import Callable
from urllib.parse import quote

import pandas as pd
import requests

from .calendar import (
    aggregate_completed_monthly,
    completed_calendar_period_bars,
    completed_native_bars,
    intraday_gap_reason,
    now_ist,
    session_overrides_from_env,
    trim_after_unverified_weekday_gap,
)
from .models import Instrument, Timeframe
from .nse_calendar import NSETradingCalendar

BASE_URL = "https://api.upstox.com/v3/historical-candle"


class UpstoxAPIError(RuntimeError):
    pass


class UpstoxAuthenticationError(UpstoxAPIError):
    pass


@dataclass
class RequestMetrics:
    request_count: int = 0
    retry_count: int = 0
    request_seconds: float = 0.0
    pacer_wait_seconds: float = 0.0
    cache_hits: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_request(self, elapsed: float, retry: bool) -> None:
        with self._lock:
            self.request_count += 1
            self.request_seconds += elapsed
            self.retry_count += int(retry)

    def record_cache_hit(self) -> None:
        with self._lock:
            self.cache_hits += 1

    def record_pacer_wait(self, elapsed: float) -> None:
        with self._lock:
            self.pacer_wait_seconds += elapsed


class RequestPacer:
    """Stay materially below Upstox's 50 requests/second standard API ceiling."""

    def __init__(self, per_second: float = 12.0) -> None:
        self._interval = 1 / per_second
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> float:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self._interval
        if delay:
            time.sleep(delay)
        return delay


class UpstoxCandleClient:
    def __init__(self, access_token: str, cache_dir: Path, force_refresh: bool = False) -> None:
        self._headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}
        self.cache_dir = cache_dir
        self.pacer = RequestPacer()
        self._session = requests.Session()
        self.force_refresh = force_refresh
        self.metrics = RequestMetrics()

    def _request(self, url: str) -> dict[str, object]:
        last: Exception | None = None
        for attempt in range(3):
            self.metrics.record_pacer_wait(self.pacer.wait())
            started = time.monotonic()
            request_recorded = False
            try:
                response = self._session.get(url, headers=self._headers, timeout=30)
                self.metrics.record_request(time.monotonic() - started, retry=attempt > 0)
                request_recorded = True
                if response.status_code in (401, 403):
                    raise UpstoxAuthenticationError("access token rejected or expired; obtain a fresh Upstox OAuth token")
                if response.status_code == 429 or response.status_code >= 500:
                    raise UpstoxAPIError(f"temporary Upstox response {response.status_code}: {response.text[:200]}")
                if response.status_code >= 400:
                    raise UpstoxAPIError(f"Upstox response {response.status_code}: {response.text[:300]}")
                payload = response.json()
                if payload.get("status") != "success":
                    raise UpstoxAPIError(f"unexpected Upstox response: {payload}")
                return payload
            except UpstoxAuthenticationError:
                raise
            except (requests.RequestException, ValueError, UpstoxAPIError) as exc:
                # Authentication failures are handled above; every other failed
                # attempt is visible in run metrics, including transport stalls.
                if not request_recorded:
                    self.metrics.record_request(time.monotonic() - started, retry=attempt > 0)
                last = exc
                if attempt < 2:
                    time.sleep(1.0 * (2**attempt))
        raise UpstoxAPIError(str(last))

    @staticmethod
    def _to_frame(payload: dict[str, object]) -> pd.DataFrame:
        candles = payload.get("data", {}).get("candles", [])  # type: ignore[union-attr]
        if not candles:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close"])
        frame = pd.DataFrame(candles, columns=["Timestamp", "Open", "High", "Low", "Close", "Volume", "Open Interest"])
        frame["Timestamp"] = pd.to_datetime(frame["Timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
        frame = frame.set_index("Timestamp")
        return frame[["Open", "High", "Low", "Close"]].apply(pd.to_numeric, errors="coerce").sort_index()

    def historical(self, key: str, unit: str, interval: int, from_day: date, to_day: date) -> pd.DataFrame:
        encoded = quote(key, safe="")
        url = f"{BASE_URL}/{encoded}/{unit}/{interval}/{to_day.isoformat()}/{from_day.isoformat()}"
        return self._to_frame(self._request(url))

    def intraday(self, key: str, unit: str, interval: int) -> pd.DataFrame:
        encoded = quote(key, safe="")
        url = f"{BASE_URL}/intraday/{encoded}/{unit}/{interval}"
        return self._to_frame(self._request(url))

    @staticmethod
    def _window_days(timeframe: Timeframe) -> int:
        if timeframe.unit == "minutes" and timeframe.interval <= 15:
            return 30
        if timeframe.unit in {"minutes", "hours"}:
            return 90
        if timeframe.unit == "days":
            return 3650
        return 100_000

    def _cache_path(self, instrument: Instrument, timeframe: Timeframe, lookback_days: int, as_of: datetime | None = None) -> Path:
        safe = instrument.instrument_key.replace("|", "_")
        suffix = f"_asof_{as_of.date():%Y%m%d}" if as_of else ""
        return self.cache_dir / "ohlc" / f"{safe}_{timeframe.key}_{lookback_days}{suffix}.pkl"

    def _cache_ttl_minutes(self, timeframe: Timeframe) -> int:
        return 15 if timeframe.intraday or timeframe.unit == "days" else 6 * 60

    def _read_fresh_cache(self, path: Path, timeframe: Timeframe) -> pd.DataFrame | None:
        if self.force_refresh or not path.exists():
            return None
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=now_ist().tzinfo)
        now = now_ist()
        age = now - modified
        if age > timedelta(minutes=self._cache_ttl_minutes(timeframe)):
            # A daily cache written after today's NSE close contains a final,
            # not forming, daily candle. Reuse it for the rest of the evening.
            if not (timeframe.unit == "days" and modified.date() == now.date() and modified.timetz().replace(tzinfo=None) >= datetime.strptime("15:30", "%H:%M").time() and age <= timedelta(hours=6)):
                return None
        try:
            return pd.read_pickle(path)
        except (OSError, ValueError, EOFError):
            return None

    @staticmethod
    def _covers_period(frame: pd.DataFrame, start: date, end: date, timeframe: Timeframe) -> bool:
        if frame.empty:
            return False
        index = pd.DatetimeIndex(frame.index)
        first = index.min().date()
        last = index.max().date()
        tolerance_days = 35 if timeframe.unit in {"weeks", "months"} else 7
        # Requested bounds can fall on holidays/weekends or before a native
        # weekly/monthly candle timestamp.  Permit only that small calendar
        # tolerance; later coverage is refetched rather than trusted.
        return first <= start + timedelta(days=tolerance_days) and last >= end - timedelta(days=tolerance_days)

    def _read_cached_period(self, path: Path, timeframe: Timeframe, immutable: bool) -> pd.DataFrame | None:
        if self.force_refresh or not path.exists():
            return None
        if immutable:
            try:
                return pd.read_pickle(path)
            except (OSError, ValueError, EOFError):
                return None
        return self._read_fresh_cache(path, timeframe)

    def _compatible_cache_paths(self, instrument: Instrument, timeframe: Timeframe, as_of: datetime | None) -> list[Path]:
        safe = instrument.instrument_key.replace("|", "_")
        suffix = f"_asof_{as_of.date():%Y%m%d}" if as_of else ""
        candidates = list((self.cache_dir / "ohlc").glob(f"{safe}_{timeframe.key}_*{suffix}.pkl"))
        if as_of is None:
            candidates = [path for path in candidates if "_asof_" not in path.name]
        return candidates

    def fetch(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        lookback_days: int,
        as_of: datetime | None = None,
        start_date: date | None = None,
    ) -> pd.DataFrame:
        """Fetch a current frame or an immutable historical snapshot ending at as_of."""
        now = now_ist()
        anchor = as_of or now
        start = start_date or (anchor.date() - timedelta(days=lookback_days))
        historical_end = anchor.date() if as_of else (now.date() if timeframe.unit in {"weeks", "months"} else now.date() - timedelta(days=1))
        path = self._cache_path(instrument, timeframe, lookback_days, as_of)
        cached = self._read_cached_period(path, timeframe, immutable=as_of is not None)
        if cached is not None and self._covers_period(cached, start, historical_end, timeframe):
            self.metrics.record_cache_hit()
            return cached
        for candidate in self._compatible_cache_paths(instrument, timeframe, as_of):
            if candidate == path:
                continue
            cached = self._read_cached_period(candidate, timeframe, immutable=as_of is not None)
            if cached is not None and self._covers_period(cached, start, historical_end, timeframe):
                self.metrics.record_cache_hit()
                return cached
        chunks: list[pd.DataFrame] = []
        if start <= historical_end:
            cursor = start
            span = self._window_days(timeframe)
            while cursor <= historical_end:
                end = min(cursor + timedelta(days=span - 1), historical_end)
                chunks.append(self.historical(instrument.instrument_key, timeframe.unit, timeframe.interval, cursor, end))
                cursor = end + timedelta(days=1)
        # Intraday V3 is only needed for the present session. Historical scans
        # deliberately stop at their selected date and never request current data.
        if as_of is None and timeframe.unit in {"minutes", "hours", "days"} and now.weekday() < 5:
            chunks.append(self.intraday(instrument.instrument_key, timeframe.unit, timeframe.interval))
        frame = pd.concat(chunks).loc[:, ["Open", "High", "Low", "Close"]] if chunks else pd.DataFrame(columns=["Open", "High", "Low", "Close"])
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            frame.to_pickle(path)
        except OSError:
            pass
        return frame


def fetch_many(
    client: UpstoxCandleClient,
    instruments: list[Instrument],
    timeframe: Timeframe,
    lookback_days: int,
    progress: Callable[[int, int], None] | None = None,
    max_workers: int = 4,
    as_of: datetime | None = None,
    start_date: date | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    data: dict[str, pd.DataFrame] = {}
    failures: dict[str, str] = {}
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        remaining = iter(instruments)
        futures = {}
        for _ in range(min(max_workers, len(instruments))):
            instrument = next(remaining, None)
            if instrument is not None:
                futures[executor.submit(client.fetch, instrument, timeframe, lookback_days, as_of, start_date)] = instrument
        while futures:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                instrument = futures.pop(future)
                try:
                    data[instrument.symbol] = future.result()
                except UpstoxAuthenticationError:
                    raise
                except Exception as exc:
                    failures[instrument.symbol] = str(exc)
                completed += 1
                if progress:
                    progress(completed, len(instruments))
            if cancelled and cancelled():
                # Do not queue more symbols after a user cancellation. Running
                # requests finish under their normal timeout/retry policy so no
                # sockets or cache writes are abandoned mid-flight.
                for future in futures:
                    future.cancel()
                break
            while len(futures) < max_workers:
                instrument = next(remaining, None)
                if instrument is None:
                    break
                futures[executor.submit(client.fetch, instrument, timeframe, lookback_days, as_of, start_date)] = instrument
    return data, failures


def prepare_timeframe(
    frame: pd.DataFrame,
    timeframe: Timeframe,
    trading_calendar: NSETradingCalendar | None = None,
    as_of: datetime | None = None,
    data_cutoff: datetime | None = None,
) -> tuple[pd.DataFrame, str | None]:
    if frame.empty:
        return frame, "no OHLC candles returned"
    if data_cutoff is not None:
        cutoff = pd.Timestamp(data_cutoff)
        cutoff = cutoff.tz_localize("Asia/Kolkata") if cutoff.tzinfo is None else cutoff.tz_convert("Asia/Kolkata")
        frame = frame.loc[pd.DatetimeIndex(frame.index) <= cutoff]
        if frame.empty:
            return frame, "no OHLC candles available through the selected cutoff"
    overrides = trading_calendar.session_overrides() if trading_calendar else {}
    overrides.update(session_overrides_from_env())
    if timeframe.intraday:
        minutes = timeframe.interval if timeframe.unit == "minutes" else timeframe.interval * 60
        completed = completed_native_bars(frame, minutes, as_of=as_of, session_overrides=overrides)
        return completed, intraday_gap_reason(completed, minutes)
    if timeframe.unit == "days":
        completed = completed_native_bars(frame, None, as_of=as_of, session_overrides=overrides)
        prepared, warning = trim_after_unverified_weekday_gap(completed, trading_calendar)
        if warning:
            prepared.attrs["continuity_warning"] = warning
        return prepared, None
    if timeframe.calendar_period:
        return completed_calendar_period_bars(frame, timeframe.calendar_period, as_of=as_of, session_overrides=overrides), None
    if timeframe.aggregate:
        return aggregate_completed_monthly(
            frame,
            timeframe.aggregate,
            as_of=as_of,
            session_overrides=overrides,
            require_complete_period=data_cutoff is not None,
        ), None
    return frame, None
