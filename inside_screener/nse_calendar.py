"""Official NSE equity-session calendar used to validate Upstox OHLC gaps."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

import requests

NSE_HOLIDAYS_URL = "https://www.nseindia.com/api/holiday-master?type=trading"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Accept-Language": "en-IN,en;q=0.9"}

# Official NSE CM circular NSE/CMTR/65587 (2025) and the 2026 NSE holiday API.
# These fallbacks protect the current years when the live calendar endpoint is
# unavailable; older uncovered years remain explicitly unknown rather than
# being guessed as trading or holiday sessions.
OFFICIAL_HOLIDAYS: dict[int, set[date]] = {
    2025: {date.fromisoformat(value) for value in (
        "2025-02-26", "2025-03-14", "2025-03-31", "2025-04-10", "2025-04-14", "2025-04-18", "2025-05-01",
        "2025-08-15", "2025-08-27", "2025-10-02", "2025-10-21", "2025-10-22", "2025-11-05", "2025-12-25",
    )},
    2026: {date.fromisoformat(value) for value in (
        "2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31", "2026-04-03", "2026-04-14", "2026-05-01",
        "2026-05-28", "2026-06-26", "2026-09-14", "2026-10-02", "2026-10-20", "2026-11-08", "2026-11-10",
        "2026-11-24", "2026-12-25",
    )},
}

# NSE/CMTR/76941: Diwali Muhurat equity continuous session, 08 Nov 2026.
OFFICIAL_SPECIAL_SESSIONS: dict[date, tuple[time, time]] = {
    date(2026, 11, 8): (time(14, 20), time(15, 30)),
}


@dataclass(frozen=True)
class NSETradingCalendar:
    holidays: frozenset[date]
    covered_years: frozenset[int]
    special_sessions: dict[date, tuple[time, time]] = field(default_factory=dict)

    def expected_trading_day(self, day: date) -> bool | None:
        """True/False when calendar coverage is known; None means unknown."""
        if day in self.special_sessions:
            return True
        if day.year not in self.covered_years:
            return None
        if day.weekday() >= 5 or day in self.holidays:
            return False
        return True

    def close_time(self, day: date) -> time:
        return self.special_sessions.get(day, (time(9, 15), time(15, 30)))[1]

    def session_overrides(self) -> dict[str, object]:
        return {day.isoformat(): {"close": close.isoformat()} for day, (_, close) in self.special_sessions.items()}


def _parse_payload(payload: dict[str, object]) -> set[date]:
    # Underlying stocks trade in the Capital Market segment, so use CM—not FO.
    rows = payload.get("CM", [])
    if not isinstance(rows, list):
        raise ValueError("NSE holiday API did not contain a CM holiday list")
    holidays: set[date] = set()
    for row in rows:
        if isinstance(row, dict) and row.get("tradingDate"):
            holidays.add(datetime.strptime(str(row["tradingDate"]), "%d-%b-%Y").date())
    return holidays


def load_nse_trading_calendar(cache_dir: Path, start: date, end: date, refresh: bool = False) -> NSETradingCalendar:
    """Load current official NSE calendar, with cache and archived fallbacks."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "nse_cm_holidays.json"
    holidays = {item for year, values in OFFICIAL_HOLIDAYS.items() for item in values}
    covered = set(OFFICIAL_HOLIDAYS)
    cache_is_fresh = cache_path.exists() and datetime.fromtimestamp(cache_path.stat().st_mtime) > datetime.now() - timedelta(hours=24)
    payload: dict[str, object] | None = None
    if cache_is_fresh and not refresh:
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
    if payload is None:
        try:
            session = requests.Session()
            response = session.get(NSE_HOLIDAYS_URL, headers=HEADERS, timeout=10)
            # The API normally responds directly. Only establish NSE cookies
            # when it explicitly asks for them, avoiding an extra slow request.
            if response.status_code in (401, 403):
                session.get("https://www.nseindia.com", headers=HEADERS, timeout=5)
                response = session.get(NSE_HOLIDAYS_URL, headers=HEADERS, timeout=10)
            response.raise_for_status()
            payload = response.json()
            cache_path.write_text(json.dumps(payload), encoding="utf-8")
        except (requests.RequestException, OSError, ValueError):
            payload = None
    if payload is not None:
        try:
            parsed = _parse_payload(payload)
            holidays.update(parsed)
            covered.update(item.year for item in parsed)
        except ValueError:
            pass
    sessions = dict(OFFICIAL_SPECIAL_SESSIONS)
    return NSETradingCalendar(frozenset(holidays), frozenset(covered), sessions)
