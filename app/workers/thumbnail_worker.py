"""thumbnail_worker.py — two thumbnail decode workers (spec §5 performance).

Red line (§6): workers run OFF the GUI thread, so they MUST NOT construct the
GUI-side pixmap class (resource-bound, undefined behaviour off the main thread).
They decode to ``QImage`` (thread-safe, reference-counted pixel buffer) and
emit it; the main thread converts via :func:`image_thumbnail.make_pixmap`.

Two shapes:
  * ``CoverThumbnailWorker`` — one-shot ``QThread`` subclass with ``run()``.
    Decode a single cover image. Carries a caller-supplied ``request_id`` so
    the main thread can discard stale replies (e.g. card scrolled off-screen).
  * ``GridThumbnailWorker`` — long-lived ``QObject`` moved to a ``QThread``.
    The main thread posts ``decode`` via ``QMetaObject.invokeMethod``; the
    worker's event loop dispatches it. Callers MUST ``quit()+wait()`` the
    thread on ``on_deactivate`` / ``closeEvent`` (see test_thumbnail_worker
    finally blocks) or pytest hangs (memory: workbench-timer-leak-hang).
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Q_ARG, QThread, QObject, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QImage

from app.utils.image_thumbnail import decode_image_data


class CoverThumbnailWorker(QThread):
    """One-shot: decode a single cover image on a fresh thread.

    ``decoded`` carries ``(request_id, QImage | None)``. ``request_id`` is
    opaque to the worker — the caller uses it to drop stale requests.
    """

    decoded = pyqtSignal(object, object)  # (request_id, QImage | None)

    def __init__(self, request_id: object, path: str, max_size: int = 280, parent=None):
        super().__init__(parent)
        self._request_id = request_id
        self._path = path
        self._max_size = max_size

    def run(self) -> None:  # noqa: D401 — QThread entry point
        image = decode_image_data(self._path, self._max_size)
        self.decoded.emit(self._request_id, image)


class GridThumbnailWorker(QObject):
    """Long-lived: lives on a worker ``QThread`` (caller creates + moveToThread).

    Main thread posts ``decode(request_id, path, max_size)`` via
    ``QMetaObject.invokeMethod(..., QueuedConnection)``. Replies come back on
    ``decoded`` as ``(request_id, QImage | None)``. Never construct the GUI
    pixmap here — emit QImage and let the main thread convert.
    """

    decoded = pyqtSignal(object, object)  # (request_id, QImage | None)

    @pyqtSlot(int, str, int)
    def decode(self, request_id: int, path: str, max_size: int) -> None:
        image = decode_image_data(path, max_size)
        self.decoded.emit(request_id, image)
