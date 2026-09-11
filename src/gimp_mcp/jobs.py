from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

FINAL_STATES = {"completed", "failed", "cancelled"}
VALID_STATES = {
    "queued", "initializing", "planning", "running", "rendering_preview", "reviewing",
    "paused", "stalled", "completed", "failed", "cancelled",
}


@dataclass(slots=True)
class ArtworkJob:
    job_id: str
    prompt: str
    provider: str
    model: str
    mode: str = "auto"
    session_id: str | None = None
    followups: list[str] = field(default_factory=list)
    status: str = "queued"
    current_step: int = 0
    progress: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    output_xcf: str | None = None
    output_image: str | None = None
    plan: dict[str, Any] | None = None
    batch_number: int = 0
    total_steps: int = 0
    successful_mutations: int = 0
    last_activity_at: float | None = None
    last_progress_at: float | None = None
    last_progress_signature: str | None = None
    vision_notes: list[dict[str, Any]] = field(default_factory=list)


class JobManager:
    """Persistent job state with cooperative pause/cancel controls."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, ArtworkJob] = {}
        self._lock = threading.RLock()
        self._pause_events: dict[str, threading.Event] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._recover()

    def _path(self, job_id: str) -> Path:
        return self.root / f"{job_id}.json"

    def _persist(self, job: ArtworkJob) -> None:
        path = self._path(job.job_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(job), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _recover(self) -> None:
        for path in self.root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                job = ArtworkJob(**data)
                if job.status not in VALID_STATES:
                    continue
                if job.status not in FINAL_STATES:
                    job.status = "paused"
                    job.error = "Recovered after application restart"
                self._jobs[job.job_id] = job
                self._pause_events[job.job_id] = threading.Event()
                self._cancel_events[job.job_id] = threading.Event()
                if job.status == "paused":
                    self._pause_events[job.job_id].set()
                    self._persist(job)
            except Exception:
                continue

    def create(self, prompt: str, provider: str, model: str, mode: str = "auto", session_id: str | None = None) -> ArtworkJob:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt must not be empty")
        if mode not in {"auto", "live", "batch"}:
            raise ValueError("mode must be auto, live or batch")
        job = ArtworkJob(uuid.uuid4().hex, prompt, provider.strip(), model.strip(), mode, session_id)
        with self._lock:
            self._jobs[job.job_id] = job
            self._pause_events[job.job_id] = threading.Event()
            self._cancel_events[job.job_id] = threading.Event()
            self._persist(job)
        return job

    def get(self, job_id: str) -> ArtworkJob:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def list(self) -> list[ArtworkJob]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda x: x.started_at or 0, reverse=True)

    def update(self, job: ArtworkJob, **changes: Any) -> ArtworkJob:
        with self._lock:
            for key, value in changes.items():
                if key == "status" and value not in VALID_STATES:
                    raise ValueError(f"invalid job status: {value}")
                if not hasattr(job, key):
                    raise AttributeError(key)
                setattr(job, key, value)
            self._persist(job)
        return job

    def add_followup(self, job: ArtworkJob, prompt: str) -> None:
        text = prompt.strip()
        if text:
            job.followups.append(text)
            self._persist(job)

    def start(self, job: ArtworkJob) -> None:
        self._cancel_events[job.job_id].clear()
        self._pause_events[job.job_id].clear()
        now = time.time()
        self.update(job, status="initializing", started_at=job.started_at or now, finished_at=None, error=None, last_activity_at=now, last_progress_at=job.last_progress_at or now)

    def pause(self, job_id: str) -> ArtworkJob:
        job = self.get(job_id)
        if job.status not in FINAL_STATES:
            self._pause_events[job_id].set()
            self.update(job, status="paused")
        return job

    def resume(self, job_id: str) -> ArtworkJob:
        job = self.get(job_id)
        if job.status == "paused":
            self._pause_events[job_id].clear()
            self.update(job, status="running", error=None)
        return job

    def cancel(self, job_id: str) -> ArtworkJob:
        job = self.get(job_id)
        self._cancel_events[job_id].set()
        self._pause_events[job_id].clear()
        if job.status not in FINAL_STATES:
            self.update(job, status="cancelled", finished_at=time.time())
        return job

    def checkpoint(self, job_id: str) -> None:
        if self._cancel_events[job_id].is_set():
            raise InterruptedError("job cancelled")
        while self._pause_events[job_id].is_set():
            if self._cancel_events[job_id].wait(0.1):
                raise InterruptedError("job cancelled")
            time.sleep(0.05)
