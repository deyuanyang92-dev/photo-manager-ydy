"""WoRMS background workers for taxonomy workflows."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QWidget

if False:  # TYPE_CHECKING
    from app.services.worms_service import WormsService

# ── WoRMS background worker ───────────────────────────────────────────────────

class _WormsSearchWorker(QThread):
    """Background thread for WoRMS name search + classification lookup."""

    results_ready = pyqtSignal(list)
    chain_ready = pyqtSignal(list)
    error_occurred = pyqtSignal(str)

    def __init__(self, worms_svc: "WormsService", query: str, like: bool = True, aphia_id: Optional[int] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._svc = worms_svc; self._query = query; self._like = like; self._aphia_id = aphia_id

    def run(self) -> None:
        try:
            if self._aphia_id is not None:
                chain = self._svc.flatten_classification(self._svc.classification(self._aphia_id))
                if self.isInterruptionRequested():
                    return  # superseded by a newer request — drop the result
                self.chain_ready.emit(chain)
            else:
                hits = self._svc.search(self._query, like=self._like)
                if self.isInterruptionRequested():
                    return
                self.results_ready.emit(hits)
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.error_occurred.emit(str(exc))


class _WormsJobWorker(QThread):
    """Drive a WoRMS batch-validation job to completion off the UI thread.

    Mirrors oracle ``runWormsJob``: for each record at the cursor, resolve it,
    match it against WoRMS, record the result, repeat until the job is no
    longer ``running`` (completed / paused / cancelled).  All progress is
    delivered via Qt signals — the worker never touches widgets.  Pausing is
    cooperative: the main thread flips the job status to ``paused`` and the
    worker exits at the next loop boundary.
    """

    progress = pyqtSignal(int, int, dict)   # cursor, total, counts
    finished_job = pyqtSignal(dict)         # final/last-seen job dict
    failed = pyqtSignal(str)

    def __init__(self, worms_svc: "WormsService", job_id: str, resolver, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._svc = worms_svc
        self._job_id = job_id
        self._resolve = resolver   # record_id -> Optional[dict]

    def run(self) -> None:
        try:
            while not self.isInterruptionRequested():
                job = self._svc.get_job(self._job_id)
                if not job or job.get("status") != "running":
                    if job:
                        self.finished_job.emit(job)
                    return
                record_ids = job.get("record_ids") or []
                cursor = job.get("cursor", 0)
                if cursor >= len(record_ids):
                    done = self._svc.update_job_status(self._job_id, "completed") or job
                    self.finished_job.emit(done)
                    return
                record = self._resolve(record_ids[cursor])
                # Unresolvable recordId → "stale" (oracle runWormsJob fallback).
                status = self._svc.match_one(record) if record else "stale"
                updated = self._svc.record_job_result(self._job_id, status) or job
                self.progress.emit(
                    updated.get("cursor", cursor + 1),
                    len(record_ids),
                    updated.get("counts", {}),
                )
                if updated.get("status") == "completed":
                    self.finished_job.emit(updated)
                    return
        except Exception as exc:
            self.failed.emit(str(exc))

