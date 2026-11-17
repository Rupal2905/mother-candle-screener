"""Small in-process scan job manager for local and single-worker deployments."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import threading
from typing import Literal
from uuid import uuid4

from .scan import ScanProgress, ScanReport, ScanRequest, scan


JobState = Literal["queued", "running", "completed", "cancelled", "failed"]


class JobBusyError(RuntimeError):
    pass


def request_signature(request: ScanRequest) -> str:
    """A token-free identity used to prevent accidental duplicate web jobs."""
    payload = {
        "timeframe": request.timeframe.key,
        "as_of": request.screening_context.requested,
        "universe": request.universe_label,
        "instruments": [(item.symbol, item.instrument_key) for item in request.instruments],
        "inside": (request.count_filter.minimum, request.count_filter.maximum),
        "lookback": request.lookback_override,
        "small": repr(request.small_body_filter),
        "green": repr(request.green_candle_filter),
        "force_refresh": request.force_refresh_data,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


@dataclass
class ScanJob:
    id: str
    signature: str
    request: ScanRequest
    state: JobState = "queued"
    progress: ScanProgress = field(default_factory=lambda: ScanProgress(0, 0, "Queued"))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    report: ScanReport | None = None
    error: str | None = None
    cancel_requested: threading.Event = field(default_factory=threading.Event, repr=False)


@dataclass(frozen=True)
class JobSnapshot:
    id: str
    state: JobState
    completed: int
    total: int
    phase: str
    error: str | None
    cancelled: bool


class ScanJobManager:
    """One bounded scan at a time protects a shared Upstox analytics token.

    This is intentionally in-process. It is suitable for local use and a
    single Streamlit server only; multiple cloud replicas require durable job
    storage plus one separately rate-limited worker before they can share jobs.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, ScanJob] = {}
        self._active_id: str | None = None
        self._by_signature: dict[str, str] = {}

    def start(self, request: ScanRequest) -> ScanJob:
        signature = request_signature(request)
        with self._lock:
            previous_id = self._by_signature.get(signature)
            if previous_id and previous_id in self._jobs:
                previous = self._jobs[previous_id]
                # A completed result is safe to show again without spending the
                # shared quota. A failed/cancelled job is intentionally allowed
                # to start afresh: covered OHLC files will be reused as resume
                # points by the scanner.
                if previous.state in {"queued", "running", "completed"}:
                    return previous
                del self._by_signature[signature]
            if self._active_id:
                active = self._jobs[self._active_id]
                if active.state in {"queued", "running"}:
                    raise JobBusyError("A scan is already running. Cancel it or wait for it to finish before starting another.")
            job = ScanJob(
                id=uuid4().hex,
                signature=signature,
                request=request,
                progress=ScanProgress(0, len(request.instruments), "Queued"),
            )
            self._jobs[job.id] = job
            self._by_signature[signature] = job.id
            self._active_id = job.id
            thread = threading.Thread(target=self._run, args=(job.id,), daemon=True, name=f"mother-screener-{job.id[:8]}")
            thread.start()
            return job

    def _run(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.state = "running"

        def update(value: ScanProgress) -> None:
            with self._lock:
                self._jobs[job_id].progress = value

        try:
            report = scan(job.request, progress=update, cancelled=job.cancel_requested.is_set)
            with self._lock:
                job.report = report
                job.state = "cancelled" if report.cancelled else "completed"
                job.completed_at = datetime.now(timezone.utc)
        except Exception as exc:  # surfaced in the UI; no token details are logged here
            with self._lock:
                job.error = str(exc)
                job.state = "failed"
                job.completed_at = datetime.now(timezone.utc)
        finally:
            with self._lock:
                if self._active_id == job_id:
                    self._active_id = None

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.state not in {"queued", "running"}:
                return False
            job.cancel_requested.set()
            return True

    def get(self, job_id: str | None) -> ScanJob | None:
        if not job_id:
            return None
        with self._lock:
            return self._jobs.get(job_id)

    def snapshot(self, job_id: str | None) -> JobSnapshot | None:
        job = self.get(job_id)
        if not job:
            return None
        return JobSnapshot(
            id=job.id,
            state=job.state,
            completed=job.progress.completed,
            total=job.progress.total,
            phase=job.progress.phase,
            error=job.error,
            cancelled=job.cancel_requested.is_set(),
        )
