import pandas as pd

from inside_screener.data import UpstoxCandleClient, fetch_many
from inside_screener.models import Instrument, TIMEFRAMES


def test_upstox_v3_payload_is_normalised_to_ist_ohlc():
    payload = {"data": {"candles": [["2026-01-07T09:15:00+05:30", 100, 105, 95, 101, 1000, 0]]}}
    frame = UpstoxCandleClient._to_frame(payload)
    assert list(frame.columns) == ["Open", "High", "Low", "Close"]
    assert str(frame.index.tz) == "Asia/Kolkata"
    assert frame.iloc[0].to_dict() == {"Open": 100, "High": 105, "Low": 95, "Close": 101}


def test_requested_timeframes_use_upstox_supported_native_units_or_monthly_source():
    assert TIMEFRAMES["5m"].unit == "minutes"
    assert TIMEFRAMES["5h"].unit == "hours"
    assert TIMEFRAMES["weekly"].unit == "weeks"
    assert TIMEFRAMES["monthly"].unit == "months"
    assert TIMEFRAMES["quarterly"].unit == "months"
    assert TIMEFRAMES["half_yearly"].aggregate == "2QE-DEC"


def test_cancelled_download_does_not_queue_the_rest_of_a_large_batch():
    calls: list[str] = []

    class Client:
        def fetch(self, instrument, *args):
            calls.append(instrument.symbol)
            return pd.DataFrame(columns=["Open", "High", "Low", "Close"])

    instruments = [Instrument(f"TEST{index}", f"NSE_EQ|{index}") for index in range(10)]
    completed = []

    def progress(done, total):
        completed.append(done)

    fetch_many(Client(), instruments, TIMEFRAMES["daily"], 365, progress=progress, max_workers=4, cancelled=lambda: bool(completed))
    assert 1 <= len(calls) <= 4
    assert completed == list(range(1, len(completed) + 1))
