"""Tests for thumbnail workers (spec §5 performance, red-line §6).

Red line enforced here: worker threads MUST NOT construct QPixmap. QPixmap is a
GUI-resource-bound class; constructing it off the main thread is undefined
behaviour in Qt. Workers emit QImage (thread-safe, reference-counted buffer)
and the main thread converts via ``image_thumbnail.make_pixmap``.
"""
from __future__ import annotations

import inspect
import os
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Q_ARG, QMetaObject, QThread, Qt
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def _write_image(path, color="#4477aa") -> None:
    img = QImage(40, 30, QImage.Format.Format_RGB32)
    img.fill(QColor(color))
    assert img.save(str(path))


# ---------------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------------


def test_cover_worker_emits_qimage(tmp_path) -> None:
    from app.workers.thumbnail_worker import CoverThumbnailWorker

    path = tmp_path / "cover.jpg"
    _write_image(path)

    worker = CoverThumbnailWorker(7, str(path), max_size=32)
    spy = QSignalSpy(worker.decoded)
    worker.start()
    assert spy.wait(5000), "cover worker did not emit decoded"
    worker.wait(2000)

    assert len(spy) == 1
    request_id, image = spy[0]
    assert request_id == 7
    assert isinstance(image, QImage), "worker must emit QImage, not QPixmap"
    assert not image.isNull()
    assert image.width() <= 32 and image.height() <= 32


def test_cover_worker_missing_file_emits_none(tmp_path) -> None:
    from app.workers.thumbnail_worker import CoverThumbnailWorker

    worker = CoverThumbnailWorker(3, str(tmp_path / "nope.jpg"), max_size=32)
    spy = QSignalSpy(worker.decoded)
    worker.start()
    assert spy.wait(5000)
    worker.wait(2000)

    assert len(spy) == 1
    request_id, image = spy[0]
    assert request_id == 3
    assert image is None


def test_grid_worker_decode_emits_qimage(tmp_path) -> None:
    from app.workers.thumbnail_worker import GridThumbnailWorker

    path = tmp_path / "grid.jpg"
    _write_image(path)

    thread = QThread()
    worker = GridThumbnailWorker()
    worker.moveToThread(thread)
    spy = QSignalSpy(worker.decoded)
    thread.start()
    try:
        QMetaObject.invokeMethod(
            worker,
            "decode",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(int, 11),
            Q_ARG(str, str(path)),
            Q_ARG(int, 24),
        )
        assert spy.wait(5000), "grid worker did not emit decoded"
        assert len(spy) == 1
        request_id, image = spy[0]
        assert request_id == 11
        assert isinstance(image, QImage)
        assert not image.isNull()
    finally:
        thread.quit()
        thread.wait(2000)


def test_grid_worker_preserves_request_ids(tmp_path) -> None:
    """A long-lived grid worker must route each reply back with the caller's id."""
    from app.workers.thumbnail_worker import GridThumbnailWorker

    p1 = tmp_path / "a.jpg"
    p2 = tmp_path / "b.jpg"
    _write_image(p1, "#cc1111")
    _write_image(p2, "#11cc11")

    thread = QThread()
    worker = GridThumbnailWorker()
    worker.moveToThread(thread)
    spy = QSignalSpy(worker.decoded)
    thread.start()
    try:
        QMetaObject.invokeMethod(
            worker,
            "decode",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(int, 100),
            Q_ARG(str, str(p1)),
            Q_ARG(int, 16),
        )
        QMetaObject.invokeMethod(
            worker,
            "decode",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(int, 200),
            Q_ARG(str, str(p2)),
            Q_ARG(int, 16),
        )
        for _ in range(60):
            if len(spy) >= 2:
                break
            assert spy.wait(500)
        assert len(spy) >= 2
        ids = [spy[i][0] for i in range(len(spy))]
        assert 100 in ids and 200 in ids
        for i in range(len(spy)):
            assert isinstance(spy[i][1], QImage), "grid worker must emit QImage"
    finally:
        thread.quit()
        thread.wait(2000)


# ---------------------------------------------------------------------------
# Red line (spec §5): worker thread must never construct QPixmap
# ---------------------------------------------------------------------------


def test_thumbnail_worker_module_does_not_reference_qpixmap() -> None:
    """Static red-line: the worker module source must not mention QPixmap.

    Workers emit QImage; the main thread converts via make_pixmap. Referencing
    QPixmap inside the worker module is a footgun the red line forbids.
    """
    from app.workers import thumbnail_worker

    source = inspect.getsource(thumbnail_worker)
    assert "QPixmap" not in source, (
        "thumbnail_worker must not reference QPixmap — emit QImage and let the "
        "main thread convert via image_thumbnail.make_pixmap (spec §5 red line)"
    )


def _install_qpixmap_init_spy(monkeypatch):
    """Patch QPixmap.__init__ to flag off-main-thread constructions.

    Returns a list that any worker-thread QPixmap construction appends to, or
    None if this PyQt6/sip build refuses the patch.
    """
    main_thread = threading.get_ident()
    violations: list[int] = []
    orig_init = QPixmap.__init__

    def spy(self, *args, **kwargs):
        if threading.get_ident() != main_thread:
            violations.append(threading.get_ident())
        return orig_init(self, *args, **kwargs)

    try:
        monkeypatch.setattr(QPixmap, "__init__", spy)
    except (TypeError, AttributeError):
        return None
    return violations


def test_cover_worker_never_constructs_qpixmap(tmp_path, monkeypatch) -> None:
    """RED LINE (spec §5): no QPixmap may be constructed on the worker thread."""
    from app.workers.thumbnail_worker import CoverThumbnailWorker

    violations = _install_qpixmap_init_spy(monkeypatch)
    if violations is None:
        pytest.skip("PyQt6/sip refuses QPixmap.__init__ monkeypatch on this build")

    path = tmp_path / "rl_cover.jpg"
    _write_image(path)

    worker = CoverThumbnailWorker(1, str(path), max_size=32)
    spy = QSignalSpy(worker.decoded)
    worker.start()
    assert spy.wait(5000)
    worker.wait(2000)

    assert not violations, (
        f"QPixmap was constructed off the main thread: {violations}"
    )


def test_grid_worker_never_constructs_qpixmap(tmp_path, monkeypatch) -> None:
    """RED LINE (spec §5): no QPixmap may be constructed on the worker thread."""
    from app.workers.thumbnail_worker import GridThumbnailWorker

    violations = _install_qpixmap_init_spy(monkeypatch)
    if violations is None:
        pytest.skip("PyQt6/sip refuses QPixmap.__init__ monkeypatch on this build")

    path = tmp_path / "rl_grid.jpg"
    _write_image(path)

    thread = QThread()
    worker = GridThumbnailWorker()
    worker.moveToThread(thread)
    spy = QSignalSpy(worker.decoded)
    thread.start()
    try:
        QMetaObject.invokeMethod(
            worker,
            "decode",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(int, 1),
            Q_ARG(str, str(path)),
            Q_ARG(int, 32),
        )
        assert spy.wait(5000)
    finally:
        thread.quit()
        thread.wait(2000)

    assert not violations, (
        f"QPixmap was constructed off the main thread: {violations}"
    )
