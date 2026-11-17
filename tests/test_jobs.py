from __future__ import annotations

from datetime import datetime
import threading
import time

import pandas as pd

from inside_screener.asof import ScreeningContext
from inside_screener.jobs import ScanJobManager
from inside_screener.lookback import resolve_lookback
from inside_screener.models import CountFilter, Instrument, TIMEFRAMES
from inside_screener.scan import ScanProgress, ScanReport, ScanRequest


def request(tmp_path) -> ScanRequest:
    return ScanRequest(
        access_token="not-a-real-token",
        timeframe=TIMEFRAMES["daily"],
        screening_context=ScreeningContext.current(),
        universe_label="Test",
        instruments=[Instrument("TEST", "NSE_EQ|TEST")],
        count_filter=CountFilter(1),
        cache_dir=tmp_path,
    )


def wait_for(manager: ScanJobManager, job_id: str, state: str) -> None:
    for _ in range(100):
        if manager.snapshot(job_id).state == state:  # type: ignore[union-attr]
            return
        time.sleep(0.01)
    raise AssertionError(f"job did not reach {state}")


def test_repeated_identical_submission_reuses_the_existing_job(tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def fake_scan(req, progress=None, cancelled=None):
        started.set()
        if progress:
            progress(ScanProgress(0, 1, "Downloading OHLC"))
        release.wait(1)
        report = ScanReport(req, resolve_lookback(req.timeframe, datetime(2026, 1, 1, tzinfo=pd.Timestamp.now(tz="Asia/Kolkata").tz)))
        report.cancelled = bool(cancelled and cancelled())
        return report

    monkeypatch.setattr("inside_screener.jobs.scan", fake_scan)
    manager = ScanJobManager()
    first = manager.start(request(tmp_path))
    assert started.wait(1)
    assert manager.start(request(tmp_path)).id == first.id
    release.set()
    wait_for(manager, first.id, "completed")


def test_cancellation_marks_a_partial_job_cancelled(tmp_path, monkeypatch):
    release = threading.Event()

    def fake_scan(req, progress=None, cancelled=None):
        release.wait(1)
        report = ScanReport(req, resolve_lookback(req.timeframe, datetime(2026, 1, 1, tzinfo=pd.Timestamp.now(tz="Asia/Kolkata").tz)))
        report.cancelled = bool(cancelled and cancelled())
        return report

    monkeypatch.setattr("inside_screener.jobs.scan", fake_scan)
    manager = ScanJobManager()
    job = manager.start(request(tmp_path))
    assert manager.cancel(job.id)
    release.set()
    wait_for(manager, job.id, "cancelled")
    assert manager.get(job.id).report is not None  # type: ignore[union-attr]
    resumed = manager.start(request(tmp_path))
    assert resumed.id != job.id
    release.set()
