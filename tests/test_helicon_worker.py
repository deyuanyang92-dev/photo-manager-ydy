"""test_helicon_worker.py — Unit tests for HeliconWorker QThread.

TDD: tests written before implementation.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from PyQt6.QtCore import QCoreApplication


@pytest.fixture(scope="module")
def qapp():
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)
    return app


class _FakeProc:
    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    def poll(self):
        return self.returncode

    def communicate(self, timeout=None):
        return self._stdout, self._stderr

    def kill(self):
        self.returncode = -9


def _run_worker(worker):
    """Start worker, wait for completion, pump event loop so signals deliver."""
    worker.start()
    worker.wait(5000)
    QCoreApplication.processEvents()


def test_worker_emits_finished_on_success(tmp_path, qapp):
    """finished signal carries output Path when subprocess returns 0 and file exists."""
    from app.workers.helicon_worker import HeliconWorker

    output = tmp_path / "out.tif"
    output.write_bytes(b"\x00" * 16)

    finished_results = []
    failed_results = []

    fake_proc = _FakeProc(returncode=0)

    with patch("app.workers.helicon_worker.subprocess.Popen", return_value=fake_proc):
        worker = HeliconWorker(cmd=["helicon", "-silent"], output_path=output)
        worker.finished.connect(lambda p: finished_results.append(p))
        worker.failed.connect(lambda m: failed_results.append(m))
        _run_worker(worker)

    assert not failed_results, f"Unexpected failure: {failed_results}"
    assert len(finished_results) == 1
    assert finished_results[0] == output


def test_worker_emits_failed_on_nonzero(tmp_path, qapp):
    """failed signal emitted when subprocess exits with nonzero returncode."""
    from app.workers.helicon_worker import HeliconWorker

    output = tmp_path / "out.tif"
    fake_proc = _FakeProc(returncode=1, stderr=b"Helicon error detail")

    finished_results = []
    failed_results = []

    with patch("app.workers.helicon_worker.subprocess.Popen", return_value=fake_proc):
        worker = HeliconWorker(cmd=["helicon", "-silent"], output_path=output)
        worker.finished.connect(lambda p: finished_results.append(p))
        worker.failed.connect(lambda m: failed_results.append(m))
        _run_worker(worker)

    assert not finished_results, "Should not emit finished on failure"
    assert len(failed_results) == 1
    assert "Helicon error detail" in failed_results[0]


def test_worker_emits_failed_when_output_missing(tmp_path, qapp):
    """failed signal when subprocess succeeds but output file was not created."""
    from app.workers.helicon_worker import HeliconWorker

    output = tmp_path / "out.tif"  # not created

    finished_results = []
    failed_results = []

    fake_proc = _FakeProc(returncode=0)

    with patch("app.workers.helicon_worker.subprocess.Popen", return_value=fake_proc):
        worker = HeliconWorker(cmd=["helicon", "-silent"], output_path=output)
        worker.finished.connect(lambda p: finished_results.append(p))
        worker.failed.connect(lambda m: failed_results.append(m))
        _run_worker(worker)

    assert not finished_results
    assert len(failed_results) == 1
    assert "未生成" in failed_results[0]


def test_worker_cancel_kills_process(tmp_path, qapp):
    """cancel() kills the running process."""
    from app.workers.helicon_worker import HeliconWorker

    output = tmp_path / "out.tif"
    fake_proc = _FakeProc(returncode=None)  # simulate still running
    fake_proc.kill_called = False

    original_kill = fake_proc.kill

    def tracking_kill():
        fake_proc.kill_called = True
        fake_proc.returncode = -9
        original_kill()

    fake_proc.kill = tracking_kill
    fake_proc.poll = lambda: fake_proc.returncode  # None while running

    worker = HeliconWorker(cmd=["helicon", "-silent"], output_path=output)
    worker._proc = fake_proc  # inject before run so cancel can reach it

    worker.cancel()

    assert fake_proc.kill_called, "cancel() must call proc.kill()"
