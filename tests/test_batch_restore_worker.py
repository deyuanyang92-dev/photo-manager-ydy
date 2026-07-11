from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from app.workers.batch_restore_worker import (
    BatchRestoreTask,
    BatchRestoreWorker,
)


def test_batch_restore_worker_runs_safe_tasks_in_parallel(monkeypatch):
    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_restore(zip_path, original_paths, overwrite):
        nonlocal active, max_active
        assert overwrite is True
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.04)
        with lock:
            active -= 1
        return SimpleNamespace(ok=True, count=1, skipped=[], failures=[])

    monkeypatch.setattr(
        "app.workers.batch_restore_worker.restore_archive_to_original_paths",
        fake_restore,
    )
    tasks = [
        BatchRestoreTask(
            zip_path=f"{index}.zip",
            mode="original",
            original_paths=(f"{index}.jpg",),
        )
        for index in range(4)
    ]
    worker = BatchRestoreWorker(tasks, concurrency=3)
    summaries = []
    worker.finished_batch.connect(summaries.append)

    worker.run()

    assert max_active >= 2
    assert summaries and summaries[0].concurrency == 3
    assert all(not outcome.error for outcome in summaries[0].outcomes)


def test_batch_restore_worker_isolates_item_failure(monkeypatch):
    def fake_restore(zip_path, original_paths, overwrite):
        if zip_path == "bad.zip":
            raise OSError("broken archive")
        return SimpleNamespace(ok=True, count=1, skipped=[], failures=[])

    monkeypatch.setattr(
        "app.workers.batch_restore_worker.restore_archive_to_original_paths",
        fake_restore,
    )
    worker = BatchRestoreWorker(
        [
            BatchRestoreTask("good.zip", "original", ("good.jpg",)),
            BatchRestoreTask("bad.zip", "original", ("bad.jpg",)),
        ],
        concurrency=2,
    )
    summaries = []
    worker.finished_batch.connect(summaries.append)

    worker.run()

    outcomes = summaries[0].outcomes
    assert outcomes[0].result is not None and not outcomes[0].error
    assert outcomes[1].result is None and "broken archive" in outcomes[1].error


def test_copy_mode_tasks_run_serially(monkeypatch):
    active = 0
    max_active = 0

    def fake_copy(zip_path, output_dir, overwrite):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        time.sleep(0.02)
        active -= 1
        return SimpleNamespace(ok=True, count=1, skipped=[], failures=[])

    monkeypatch.setattr(
        "app.workers.batch_restore_worker.restore_archive",
        fake_copy,
    )
    worker = BatchRestoreWorker(
        [
            BatchRestoreTask(
                f"{index}.zip",
                "copy",
                output_dir="incoming-jpg",
                parallel_safe=False,
            )
            for index in range(3)
        ],
        concurrency=3,
    )
    summaries = []
    worker.finished_batch.connect(summaries.append)

    worker.run()

    assert max_active == 1
    assert len(summaries[0].outcomes) == 3
