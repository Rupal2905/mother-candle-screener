"""Upstox BOD JSON cache and NSE F&O underlying-equity discovery."""
from __future__ import annotations

import gzip
import csv
from io import StringIO
import json
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import requests

from .models import Instrument

NSE_BOD_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
NIFTY_CONSTITUENT_URLS = {
    "nifty50": "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv",
    "nifty500": "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
}
NIFTY_DOWNLOAD_HEADERS = {
    "Accept": "text/csv,application/octet-stream;q=0.9,*/*;q=0.8",
    "User-Agent": "Mozilla/5.0 (compatible; MotherCandleScreener/1.0)",
    "Referer": "https://www.niftyindices.com/",
}


class InstrumentError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConstituentList:
    key: str
    symbols: list[str]
    source: str
    refreshed_at: datetime
    cache_path: Path


def bod_source_metadata(cache_dir: Path) -> tuple[str, datetime | None]:
    """Return the BOD source and its local-cache refresh time for disclosure."""
    path = cache_dir / "upstox_nse_bod.json.gz"
    refreshed = datetime.fromtimestamp(path.stat().st_mtime) if path.exists() else None
    return NSE_BOD_URL, refreshed


def _fresh(path: Path, hours: int = 24) -> bool:
    return path.exists() and datetime.fromtimestamp(path.stat().st_mtime) > datetime.now() - timedelta(hours=hours)


def load_bod_instruments(cache_dir: Path, refresh: bool = False) -> list[dict[str, object]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "upstox_nse_bod.json.gz"
    if not refresh and _fresh(path):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    try:
        response = requests.get(NSE_BOD_URL, timeout=45)
        response.raise_for_status()
        content = response.content
        decoded = gzip.decompress(content) if content[:2] == b"\x1f\x8b" else content
        instruments = json.loads(decoded)
        if not isinstance(instruments, list):
            raise InstrumentError("Upstox BOD JSON was not an array")
        with gzip.open(path, "wb") as handle:
            handle.write(json.dumps(instruments).encode("utf-8"))
        return instruments
    except (OSError, requests.RequestException, json.JSONDecodeError, InstrumentError) as exc:
        if path.exists():
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                return json.load(handle)
        raise InstrumentError(f"could not obtain Upstox NSE BOD instruments: {exc}") from exc


def load_nifty_constituents(cache_dir: Path, index_key: str, refresh: bool = False) -> ConstituentList:
    """Load a current official NSE Indices constituent CSV with a daily cache."""
    if index_key not in NIFTY_CONSTITUENT_URLS:
        raise InstrumentError(f"unsupported Nifty constituent list: {index_key}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{index_key}_constituents.csv"
    content: str | None = None
    if not refresh and _fresh(path):
        try:
            content = path.read_text(encoding="utf-8-sig")
        except OSError:
            content = None
    if content is None:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = requests.get(
                    NIFTY_CONSTITUENT_URLS[index_key],
                    headers=NIFTY_DOWNLOAD_HEADERS,
                    timeout=(10, 45),
                )
                response.raise_for_status()
                content = response.content.decode("utf-8-sig")
                path.write_text(content, encoding="utf-8")
                break
            except (OSError, UnicodeDecodeError, requests.RequestException) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1.0 * (2**attempt))
        if content is None:
            if path.exists():
                try:
                    content = path.read_text(encoding="utf-8-sig")
                except OSError as cached_exc:
                    raise InstrumentError(f"could not read cached {index_key} constituents: {cached_exc}") from cached_exc
            else:
                raise InstrumentError(f"could not obtain official {index_key} constituents after 3 attempts: {last_error}") from last_error
    try:
        rows = csv.DictReader(StringIO(content))
        symbols = [str(row.get("Symbol") or row.get("SYMBOL") or "").strip().upper() for row in rows]
    except csv.Error as exc:
        raise InstrumentError(f"official {index_key} constituent CSV is malformed: {exc}") from exc
    symbols = [symbol for symbol in symbols if symbol]
    if not symbols:
        raise InstrumentError(f"official {index_key} constituent CSV contains no symbols")
    return ConstituentList(
        key=index_key,
        symbols=sorted(set(symbols)),
        source=NIFTY_CONSTITUENT_URLS[index_key],
        refreshed_at=datetime.fromtimestamp(path.stat().st_mtime),
        cache_path=path,
    )


def discover_fo_stock_underlyings(records: list[dict[str, object]]) -> tuple[list[Instrument], dict[str, str]]:
    """Resolve NSE_FO EQUITY underlyings to active NSE_EQ instrument keys."""
    equities_by_key: dict[str, dict[str, object]] = {}
    equities_by_symbol: dict[str, dict[str, object]] = {}
    for record in records:
        if record.get("segment") == "NSE_EQ" and record.get("instrument_type") == "EQ":
            key = str(record.get("instrument_key", ""))
            symbol = str(record.get("trading_symbol", ""))
            if key and symbol:
                equities_by_key[key] = record
                equities_by_symbol[symbol] = record

    resolved: dict[str, Instrument] = {}
    unresolved: dict[str, str] = {}
    for record in records:
        if record.get("segment") != "NSE_FO" or record.get("underlying_type") != "EQUITY":
            continue
        candidate_key = str(record.get("underlying_key", ""))
        candidate_symbol = str(record.get("underlying_symbol", ""))
        equity = equities_by_key.get(candidate_key) or equities_by_symbol.get(candidate_symbol)
        label = candidate_symbol or candidate_key or str(record.get("trading_symbol", "unknown"))
        if not equity:
            unresolved[label] = "no NSE_EQ equity mapping in the Upstox BOD file"
            continue
        symbol = str(equity["trading_symbol"])
        key = str(equity["instrument_key"])
        resolved[key] = Instrument(symbol=symbol, instrument_key=key)
    return sorted(resolved.values(), key=lambda value: value.symbol), unresolved


def discover_nse_equities(records: list[dict[str, object]]) -> tuple[list[Instrument], dict[str, int]]:
    """Return ordinary NSE equities and a transparent count of exclusions.

    The BOD file does not provide a dedicated ETF flag.  In addition to the
    documented NSE_EQ/EQ selection, INF ISINs and common ETF labels are
    excluded so fund units are not silently treated as company shares.
    """
    excluded: Counter[str] = Counter()
    by_key: dict[str, Instrument] = {}
    for record in records:
        if record.get("segment") != "NSE_EQ":
            continue
        instrument_type = str(record.get("instrument_type", "<missing>"))
        if instrument_type != "EQ":
            excluded[f"instrument type: {instrument_type}"] += 1
            continue
        key = str(record.get("instrument_key", "")).strip()
        symbol = str(record.get("trading_symbol", "")).strip()
        isin = str(record.get("isin", "")).upper()
        name = str(record.get("name", "")).upper()
        if not key:
            excluded["missing instrument key"] += 1
            continue
        if not symbol:
            excluded["missing trading symbol"] += 1
            continue
        if isin.startswith("INF") or "ETF" in name or symbol.endswith("BEES"):
            excluded["ETF or fund unit"] += 1
            continue
        if key in by_key:
            excluded["duplicate instrument key"] += 1
            continue
        by_key[key] = Instrument(symbol=symbol, instrument_key=key)

    selected: list[Instrument] = []
    symbols: set[str] = set()
    for instrument in sorted(by_key.values(), key=lambda item: (item.symbol, item.instrument_key)):
        normalized = instrument.symbol.upper()
        if normalized in symbols:
            excluded["duplicate trading symbol"] += 1
            continue
        symbols.add(normalized)
        selected.append(instrument)
    return selected, dict(sorted(excluded.items()))


def load_custom_symbols(path: Path) -> list[str]:
    """Read one NSE trading symbol per text line or first CSV column."""
    try:
        return parse_custom_symbols(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise InstrumentError(f"could not read custom symbol file: {exc}") from exc


def parse_custom_symbols(content: str) -> list[str]:
    """Parse uploaded CSV/text contents without requiring a persistent file."""
    lines = content.splitlines()
    symbols: list[str] = []
    for line in lines:
        value = line.split(",", 1)[0].strip()
        if not value or value.lower() in {"symbol", "trading_symbol", "ticker"}:
            continue
        symbols.append(value.upper())
    if not symbols:
        raise InstrumentError("custom symbol file contains no symbols")
    return symbols


def resolve_custom_symbols(records: list[dict[str, object]], requested: list[str]) -> tuple[list[Instrument], dict[str, str]]:
    """Validate requested NSE stock symbols against the eligible equity set."""
    equities, _ = discover_nse_equities(records)
    by_symbol = {instrument.symbol.upper(): instrument for instrument in equities}
    resolved: list[Instrument] = []
    rejected: dict[str, str] = {}
    seen: set[str] = set()
    for symbol in requested:
        normalized = symbol.strip().upper()
        if not normalized:
            continue
        if normalized in seen:
            rejected[symbol] = "duplicate symbol in custom file"
            continue
        seen.add(normalized)
        instrument = by_symbol.get(normalized)
        if instrument is None:
            rejected[symbol] = "not an eligible NSE_EQ ordinary equity in the current Upstox BOD file"
            continue
        resolved.append(instrument)
    return sorted(resolved, key=lambda item: item.symbol), rejected
