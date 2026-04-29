"""Tiny in-memory job tracker.

Good enough for a single-user side project. Swap for Redis / a DB
when you want multi-worker concurrency.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class JobStatus(str, Enum):
    QUEUED = "queued"
    GENERATING_3D = "generating_3d"
    DECIMATING = "decimating"
    UNFOLDING = "unfolding"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Job:
    id: str
    status: JobStatus = JobStatus.QUEUED
    message: str = ""
    progress: int = 0  # 0..100
    image_path: Optional[Path] = None
    glb_path: Optional[Path] = None
    obj_path: Optional[Path] = None
    pdf_path: Optional[Path] = None
    svg_path: Optional[Path] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status.value,
            "message": self.message,
            "progress": self.progress,
            "error": self.error,
            "has_pdf": self.pdf_path is not None and self.pdf_path.exists(),
            "has_svg": self.svg_path is not None and self.svg_path.exists(),
            "has_glb": self.glb_path is not None and self.glb_path.exists(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, image_path: Path) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], image_path=image_path)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def update(
        self,
        job_id: str,
        *,
        status: Optional[JobStatus] = None,
        message: Optional[str] = None,
        progress: Optional[int] = None,
        error: Optional[str] = None,
        **paths,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            if status is not None:
                job.status = status
            if message is not None:
                job.message = message
            if progress is not None:
                job.progress = progress
            if error is not None:
                job.error = error
            for k, v in paths.items():
                if hasattr(job, k):
                    setattr(job, k, v)
            job.updated_at = time.time()


store = JobStore()
