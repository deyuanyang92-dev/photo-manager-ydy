"""test_supp_compression_worker.py — SuppCompressionWorker signal delivery.

Uses real cjxl when available; otherwise the archive still succeeds via the
JPG-passthrough fallback in archive_group (no JXL, but a valid ZIP). Never
mocks the safety gates.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QEventLoop, QTimer  # noqa: E402

from app.services.archive_service import ZipResult  # noqa: E402
from app.workers.supp_compression_worker import SuppCompressionWorker  # noqa: E402


def _real_jpg(path: str) -> str:
    """Write a small but valid JPEG (cjxl rejects malformed input)."""
    from PIL import Image

    Image.new("RGB", (16, 16), (123, 45, 67)).save(path, "JPEG")
    return path


def _touch(path: str, content: bytes = b"x") -> str:
    with open(path, "wb") as fh:
        fh.write(content)
    return path


def _run_worker(worker, timeout_ms: int = 30000) -> dict:
    """Run *worker* to completion, capturing whichever terminal signal fires."""
    loop = QEventLoop()
    captured: dict = {}

    def _done(key):
        def handler(payload=None):
            captured["key"] = key
            captured["payload"] = payload
            loop.quit()
        return handler

    worker.finished.connect(_done("finished"))
    worker.failed.connect(_done("failed"))
    worker.started_archiving.connect(
        lambda n, stem: captured.setdefault("started", (n, stem))
    )
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(timeout_ms)
    worker.start()
    loop.exec()
    worker.wait(5000)
    return captured


def test_worker_emits_finished_with_zipresult(qtbot, tmp_path):
    tiff = _touch(str(tmp_path / "FJ-XM-B2-DLC001-1-T95E-20260601.tif"))
    jpgs = [_real_jpg(str(tmp_path / f"o{i}.jpg")) for i in range(2)]
    worker = SuppCompressionWorker(jpgs, tiff, str(tmp_path), delete_jpg=False)

    captured = _run_worker(worker)

    assert captured.get("key") == "finished", f"got {captured}"
    result = captured["payload"]
    assert isinstance(result, ZipResult)
    assert result.ok
    # Artifact is {tiff_stem}.zip next to the TIFF.
    assert os.path.basename(result.zip_path) == "FJ-XM-B2-DLC001-1-T95E-20260601.zip"
    assert os.path.isfile(result.zip_path)
    # started_archiving carried the jpg count + tiff stem.
    assert captured["started"] == (2, "FJ-XM-B2-DLC001-1-T95E-20260601")
    # TIFF never touched (red-line #1).
    assert os.path.isfile(tiff)


def test_worker_failed_signal_on_empty_jpgs(qtbot, tmp_path):
    tiff = _touch(str(tmp_path / "FJ-XM-B2-DLC001-1-T95E-20260601.tif"))
    worker = SuppCompressionWorker([], tiff, str(tmp_path), delete_jpg=False)

    captured = _run_worker(worker)

    assert captured.get("key") == "failed", f"got {captured}"
    assert captured["payload"]  # non-empty error message
