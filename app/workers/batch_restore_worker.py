"""Parallel file restoration worker for selected result ZIP archives."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from threading import Event

from PyQt6.QtCore import QThread, pyqtSignal

from app.services.archive_service import (
    restore_archive,
    restore_archive_to_original_paths,
)


@dataclass(frozen=True)
class BatchRestoreTask:
    zip_path: str
    mode: str
    original_paths: tuple[str, ...] = ()
    output_dir: str = ""
    owner_uid: str = ""
    group_index: int = -1
    parallel_safe: bool = True


@dataclass
class BatchRestoreOutcome:
    task: BatchRestoreTask
    result: object | None = None
    error: str = ""


@dataclass
class BatchRestoreSummary:
    outcomes: list[BatchRestoreOutcome] = field(default_factory=list)
    concurrency: int = 1
    cancelled: bool = False


class BatchRestoreWorker(QThread):
    started_batch = pyqtSignal(int, int)
    progress = pyqtSignal(int, int, str)
    finished_batch = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, tasks: list[BatchRestoreTask], concurrency: int = 3, parent=None) -> None:
        super().__init__(parent)
        self._tasks = list(tasks)
        self._concurrency = max(1, min(4, int(concurrency)))
        self._cancel_requested = Event()

    def cancel(self) -> None:
        self._cancel_requested.set()

    def _execute(self, task: BatchRestoreTask) -> BatchRestoreOutcome:
        if self._cancel_requested.is_set():
            return BatchRestoreOutcome(task=task, error="已取消")
        try:
            if task.mode == "original":
                result = restore_archive_to_original_paths(
                    task.zip_path,
                    list(task.original_paths),
                    overwrite=True,
                )
            elif task.mode == "copy":
                result = restore_archive(
                    task.zip_path,
                    task.output_dir,
                    overwrite=False,
                )
            else:
                raise ValueError(f"未知还原模式：{task.mode}")
            return BatchRestoreOutcome(task=task, result=result)
        except Exception as exc:  # noqa: BLE001
            return BatchRestoreOutcome(task=task, error=str(exc))

    def run(self) -> None:
        try:
            total = len(self._tasks)
            parallel_tasks = [task for task in self._tasks if task.parallel_safe]
            serial_tasks = [task for task in self._tasks if not task.parallel_safe]
            active_concurrency = min(self._concurrency, max(1, len(parallel_tasks)))
            self.started_batch.emit(total, active_concurrency)

            outcomes: dict[BatchRestoreTask, BatchRestoreOutcome] = {}
            completed = 0
            if parallel_tasks:
                with ThreadPoolExecutor(
                    max_workers=active_concurrency,
                    thread_name_prefix="zip-restore",
                ) as executor:
                    futures = {
                        executor.submit(self._execute, task): task
                        for task in parallel_tasks
                    }
                    for future in as_completed(futures):
                        task = futures[future]
                        outcomes[task] = (
                            BatchRestoreOutcome(task=task, error="已取消")
                            if future.cancelled()
                            else future.result()
                        )
                        completed += 1
                        self.progress.emit(completed, total, task.zip_path)
                        if self._cancel_requested.is_set():
                            for pending in futures:
                                pending.cancel()

            for task in serial_tasks:
                outcome = self._execute(task)
                outcomes[task] = outcome
                completed += 1
                self.progress.emit(completed, total, task.zip_path)

            ordered = [
                outcomes.get(task, BatchRestoreOutcome(task=task, error="已取消"))
                for task in self._tasks
            ]
            self.finished_batch.emit(
                BatchRestoreSummary(
                    outcomes=ordered,
                    concurrency=active_concurrency,
                    cancelled=self._cancel_requested.is_set(),
                )
            )
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
