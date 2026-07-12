"""MediaThumbnailLoader contract: GUI-safe cache path + stale-reply handling.

Guards the 2026-07-12 卡顿修复 in app/widgets/grouping_media_helpers.py:
  * cache lookups must never run a full decode on the GUI thread;
  * replies for a rebuilt table (generation bump) / destroyed label must be
    dropped, not painted onto a dead QLabel;
  * shutdown() must quit()+wait() the decode thread (no dangling thread).
"""
from __future__ import annotations

import threading

import pytest
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import QLabel

from app.widgets import grouping_media_helpers as gmh


@pytest.fixture(autouse=True)
def _clear_thumb_cache():
    gmh._RELATED_THUMB_CACHE.clear()
    yield
    gmh._RELATED_THUMB_CACHE.clear()


@pytest.fixture
def loader(qtbot):
    obj = gmh.MediaThumbnailLoader(size=64)
    yield obj
    obj.shutdown()


def _image(color: str = "#ff0000", w: int = 40, h: int = 20) -> QImage:
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(color))
    return img


def _jpg(tmp_path, name: str = "a.jpg"):
    path = tmp_path / name
    _image().save(str(path), "JPEG")
    return path


def test_cached_lookup_never_decodes_on_gui_thread(tmp_path, qtbot, monkeypatch):
    """_cached_media_thumbnail_pixmap must not fall back to a full decode."""
    path = _jpg(tmp_path)

    def _boom(*_a, **_kw):  # pragma: no cover - only fires on regression
        raise AssertionError("full decode ran on the GUI thread")

    monkeypatch.setattr("app.utils.image_thumbnail.decode_image_data", _boom)
    monkeypatch.setattr("app.utils.image_thumbnail.decode_image_thumbnail", _boom)
    assert gmh._cached_media_thumbnail_pixmap(str(path), 64) is None


def test_request_queues_on_cache_miss_then_paints_label(tmp_path, qtbot, loader):
    path = _jpg(tmp_path)
    label = QLabel()
    qtbot.addWidget(label)

    assert loader.request(label, str(path), fallback_text="JPG") is False
    assert loader.pending_count() == 1
    assert label.pixmap().isNull()  # placeholder until the worker replies

    qtbot.waitUntil(lambda: loader.pending_count() == 0, timeout=5000)
    assert not label.pixmap().isNull()
    assert label.property("hasThumbnail") is True


def test_second_request_is_served_from_cache_same_frame(tmp_path, qtbot, loader):
    path = _jpg(tmp_path)
    first, second = QLabel(), QLabel()
    qtbot.addWidget(first)
    qtbot.addWidget(second)

    loader.request(first, str(path), fallback_text="JPG")
    qtbot.waitUntil(lambda: loader.pending_count() == 0, timeout=5000)

    assert loader.request(second, str(path), fallback_text="JPG") is True
    assert loader.pending_count() == 0
    assert not second.pixmap().isNull()


def test_reset_bumps_generation_and_drops_stale_replies(tmp_path, qtbot, loader):
    path = _jpg(tmp_path)
    label = QLabel()
    qtbot.addWidget(label)

    loader.request(label, str(path), fallback_text="JPG")
    rid = max(loader._pending)
    gen_before = loader.generation

    loader.reset()  # simulates _populate() rebuilding the table
    assert loader.generation == gen_before + 1
    assert loader.pending_count() == 0

    loader._on_decoded(rid, _image())  # stale reply must be ignored
    assert label.pixmap().isNull()
    assert label.property("hasThumbnail") in (None, False)


def test_destroyed_label_unregisters_its_request(tmp_path, qtbot, loader):
    path = _jpg(tmp_path)
    label = QLabel()
    loader.request(label, str(path), fallback_text="JPG")
    rid = max(loader._pending)

    label.deleteLater()
    label.destroyed.emit()  # deterministic stand-in for the C++ delete
    assert rid not in loader._pending

    loader._on_decoded(rid, _image())  # must not touch the dead label


def test_stale_reply_for_unknown_id_is_ignored(loader):
    loader._on_decoded(9999, _image())  # no entry -> no crash
    assert loader.pending_count() == 0


def test_shutdown_stops_the_decode_thread(qtbot):
    obj = gmh.MediaThumbnailLoader(size=64)
    thread = obj._thread
    assert thread.isRunning()
    obj.shutdown()
    assert not thread.isRunning()
    assert obj._worker is None


def test_request_after_shutdown_falls_back_to_placeholder(tmp_path, qtbot):
    obj = gmh.MediaThumbnailLoader(size=64)
    obj.shutdown()
    label = QLabel()
    qtbot.addWidget(label)
    assert obj.request(label, str(_jpg(tmp_path)), fallback_text="JPG") is False
    assert label.text() == "JPG"


def test_media_thumbnail_pixmap_still_returns_a_pixmap(tmp_path, qtbot):
    """Legacy synchronous caller contract is unchanged."""
    pixmap = gmh._media_thumbnail_pixmap(str(_jpg(tmp_path)), 64)
    assert isinstance(pixmap, QPixmap) and not pixmap.isNull()
    assert pixmap.width() <= 64 and pixmap.height() <= 64
    assert gmh._media_thumbnail_pixmap(str(tmp_path / "missing.jpg"), 64) is None


# ─────────────────────────────────────────────────────────────────────────────
# Real-caller adoption: the grouping picker dialogs must go through the loader,
# i.e. NO image decode may ever run on the GUI thread (2026-07-12 卡顿修复).
# ─────────────────────────────────────────────────────────────────────────────

def _boom(*_a, **_kw):  # pragma: no cover - only fires on regression
    raise AssertionError("image decode ran on the GUI thread")


def _forbid_gui_thread_decode(monkeypatch) -> None:
    """Any decode entry point reachable from the GUI thread blows up."""
    from app.widgets import grouping_dialogs as gd

    monkeypatch.setattr(gd, "_media_thumbnail_pixmap", _boom)
    monkeypatch.setattr("app.utils.image_thumbnail.decode_image_thumbnail", _boom)
    monkeypatch.setattr(gmh, "_media_thumbnail_pixmap", _boom)


def _spy_worker_decodes(monkeypatch) -> list:
    """Record the thread each worker-side decode actually ran on."""
    from app.workers import thumbnail_worker

    real = thumbnail_worker.decode_image_data
    seen: list = []

    def _spy(path, max_size):
        seen.append(threading.current_thread())
        return real(path, max_size)

    monkeypatch.setattr(thumbnail_worker, "decode_image_data", _spy)
    return seen


def _row_for_name(table, name: str, col: int) -> int:
    for row in range(table.rowCount()):
        item = table.item(row, col)
        if item is not None and item.text() == name:
            return row
    raise AssertionError(f"row {name!r} not found")


def test_location_picker_thumbnails_arrive_via_loader_off_gui_thread(
    tmp_path, qtbot, monkeypatch
):
    from app.widgets import grouping_dialogs as gd

    decoded_on = _spy_worker_decodes(monkeypatch)
    _forbid_gui_thread_decode(monkeypatch)
    jpg = _jpg(tmp_path, "P6201980.JPG")

    dlg = gd._MediaLocationPickerDialog("选择相关 JPG/TIF", str(tmp_path))
    qtbot.addWidget(dlg)
    assert isinstance(dlg._thumbs, gmh.MediaThumbnailLoader)

    row = _row_for_name(dlg._table, jpg.name, 1)
    label = dlg._table.cellWidget(row, 0)
    assert label.property("hasThumbnail") is False  # placeholder until reply

    qtbot.waitUntil(lambda: label.property("hasThumbnail") is True, timeout=8000)
    assert not label.pixmap().isNull()
    assert decoded_on and all(t is not threading.main_thread() for t in decoded_on)
    dlg.close()


def test_related_picker_thumbnails_arrive_via_loader_off_gui_thread(
    tmp_path, qtbot, monkeypatch
):
    from app.widgets import grouping_dialogs as gd

    decoded_on = _spy_worker_decodes(monkeypatch)
    _forbid_gui_thread_decode(monkeypatch)
    jpg = _jpg(tmp_path, "P6201980.JPG")
    candidates = [{"path": str(jpg), "name": jpg.name, "kind": "JPG", "mtime": 1}]

    dlg = gd._RelatedFilesPickerDialog("UID-1", str(tmp_path), candidates)
    qtbot.addWidget(dlg)
    assert isinstance(dlg._thumbs, gmh.MediaThumbnailLoader)

    label = dlg._table.cellWidget(0, 1)
    assert label.property("hasThumbnail") is False

    qtbot.waitUntil(lambda: label.property("hasThumbnail") is True, timeout=8000)
    assert not label.pixmap().isNull()
    assert decoded_on and all(t is not threading.main_thread() for t in decoded_on)
    dlg.close()


def test_location_picker_repopulate_drops_stale_replies(tmp_path, qtbot, monkeypatch):
    """Navigating to another folder must invalidate in-flight requests."""
    from app.widgets import grouping_dialogs as gd

    _forbid_gui_thread_decode(monkeypatch)
    _jpg(tmp_path, "P6201980.JPG")
    other = tmp_path / "sub"
    other.mkdir()

    dlg = gd._MediaLocationPickerDialog("t", str(tmp_path))
    qtbot.addWidget(dlg)
    gen_before = dlg._thumbs.generation
    dlg._go_to_dir(str(other))
    assert dlg._thumbs.generation == gen_before + 1
    assert dlg._thumbs.pending_count() == 0
    assert dlg._thumb_queue == []
    dlg.close()


@pytest.mark.parametrize("closer", ["close", "reject", "accept"])
def test_location_picker_shuts_the_decode_thread_down(tmp_path, qtbot, closer):
    from app.widgets import grouping_dialogs as gd

    _jpg(tmp_path, "P6201980.JPG")
    dlg = gd._MediaLocationPickerDialog("t", str(tmp_path))
    qtbot.addWidget(dlg)
    thread = dlg._thumbs._thread
    assert thread.isRunning()

    getattr(dlg, closer)()
    assert not thread.isRunning()
    assert dlg._thumbs._worker is None


@pytest.mark.parametrize("closer", ["close", "reject", "accept"])
def test_related_picker_shuts_the_decode_thread_down(tmp_path, qtbot, closer):
    from app.widgets import grouping_dialogs as gd

    jpg = _jpg(tmp_path, "P6201980.JPG")
    dlg = gd._RelatedFilesPickerDialog(
        "UID-1", str(tmp_path),
        [{"path": str(jpg), "name": jpg.name, "kind": "JPG", "mtime": 1}],
    )
    qtbot.addWidget(dlg)
    thread = dlg._thumbs._thread
    assert thread.isRunning()

    getattr(dlg, closer)()
    assert not thread.isRunning()
    assert dlg._thumbs._worker is None
