"""Regression tests for the UID-grid main-thread stall (卡顿根因).

Red lines locked here:

1. ``set_groups`` must NEVER walk every photo on the GUI thread. The old
   ``_prefetch_all_thumbnails`` looped over *all* sections × *all* rows and ran
   ``try_cached_image_data`` (Path.resolve + stat + sha256 + 读盘 + 全量 JPEG
   解码) per photo — 上千张时是秒级冻结。Prefetch must be viewport-clipped.
2. The GUI thread (delegate ``paint`` → ``_request_decode``) must do NO disk
   I/O at all: no ``try_cached_image_data``, no ``read_disk_thumbnail``. The
   memory/disk cache fast-path already exists inside the worker
   (``decode_image_data(use_cache=True)``).
3. Repeated ``set_groups`` must not stack scans (single-shot member QTimer,
   ``start()`` resets instead of queueing another full pass).
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtGui import QPixmapCache
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _clear_pixmap_cache():
    QPixmapCache.clear()
    yield
    QPixmapCache.clear()


def _groups(n_groups: int, items_per: int) -> list[dict]:
    """Groups pointing at nonexistent files — we only measure GUI-thread work,
    never the decode result."""
    return [
        {
            "uid": f"浙江-三门湾-B{gi:02d}-{gi * 100 + 1:03d}-A1-20240301",
            "items": [
                {
                    "path": f"/nonexistent/uidgrid/g{gi}_{ii}.tif",
                    "name": f"g{gi}_{ii}.tif",
                    "seq": ii + 1,
                }
                for ii in range(items_per)
            ],
        }
        for gi in range(n_groups)
    ]


def _drain(ms: int = 120) -> None:
    QApplication.processEvents()
    QTest.qWait(ms)
    QApplication.processEvents()


def test_set_groups_never_scans_every_photo_on_the_gui_thread(monkeypatch):
    """2000 张 / 视口只装得下几十格 → GUI 线程投递的解码请求必须 << 2000."""
    from app.widgets import uid_grouped_grid as mod

    n_groups, items_per = 40, 50            # 2000 photos
    total = n_groups * items_per

    calls: list[tuple[int, int, str]] = []
    real_request = mod.UidGroupedGrid._request_decode

    def _counting_request(self, section_idx, row, path):
        calls.append((section_idx, row, path))
        return real_request(self, section_idx, row, path)

    monkeypatch.setattr(mod.UidGroupedGrid, "_request_decode", _counting_request)

    grid = mod.UidGroupedGrid()
    try:
        grid.resize(800, 600)
        grid.show()
        grid.set_groups(_groups(n_groups, items_per))
        _drain()

        assert len(calls) <= mod._PREFETCH_BUDGET, (
            f"GUI thread posted {len(calls)} decode requests for {total} photos "
            "— prefetch must be viewport-clipped, never a full-table scan"
        )
        assert len(calls) < total / 4
    finally:
        grid.teardown()


def test_gui_thread_does_no_disk_io_for_thumbnails(monkeypatch, tmp_path):
    """paint / _request_decode 路径上不得出现 try_cached_image_data 或
    read_disk_thumbnail（stat + sha256 + 读盘 + JPEG 解码）。"""
    import threading

    from app.utils import image_thumbnail as it
    from app.widgets import uid_grouped_grid as mod

    main_ident = threading.get_ident()
    offenders: list[str] = []

    def _guard(name, fn):
        def _wrapped(*a, **kw):
            if threading.get_ident() == main_ident:
                offenders.append(name)
            return fn(*a, **kw)

        return _wrapped

    monkeypatch.setattr(
        it, "try_cached_image_data", _guard("try_cached_image_data", it.try_cached_image_data)
    )
    monkeypatch.setattr(
        it, "read_disk_thumbnail", _guard("read_disk_thumbnail", it.read_disk_thumbnail)
    )
    monkeypatch.setattr(
        mod,
        "try_cached_image_data",
        _guard("uid_grid.try_cached_image_data", mod.try_cached_image_data),
    )

    # Real image files so the disk-cache path is actually reachable.
    from PyQt6.QtGui import QColor, QImage

    paths = []
    for i in range(12):
        p = tmp_path / f"p{i}.png"
        img = QImage(60, 40, QImage.Format.Format_RGB32)
        img.fill(QColor("#3388cc"))
        assert img.save(str(p))
        paths.append(str(p))

    grid = mod.UidGroupedGrid()
    try:
        grid.resize(700, 500)
        grid.show()
        grid.set_groups([
            {
                "uid": "浙江-三门湾-B01-001-A1-20240301",
                "items": [
                    {"path": p, "name": os.path.basename(p), "seq": i + 1}
                    for i, p in enumerate(paths)
                ],
            }
        ])
        _drain(200)
        grid._repaint_visible_cells()
        _drain(200)
        # A second pass: pixmaps should now be cached; still no GUI-thread I/O.
        grid.set_groups([
            {
                "uid": "浙江-三门湾-B01-001-A1-20240301",
                "items": [
                    {"path": p, "name": os.path.basename(p), "seq": i + 1}
                    for i, p in enumerate(paths)
                ],
            }
        ])
        _drain(200)

        assert offenders == [], (
            f"thumbnail disk I/O ran on the GUI thread: {sorted(set(offenders))}"
        )
    finally:
        grid.teardown()


def test_repeated_set_groups_does_not_stack_prefetch_passes(monkeypatch):
    """连续 3 次筛选 → 只排队 1 次可见带预取（成员单发 QTimer，start 重置不叠加）."""
    from app.widgets import uid_grouped_grid as mod

    grid = mod.UidGroupedGrid()
    passes: list[int] = []
    real = grid._prefetch_visible_thumbnails

    def _counting():
        passes.append(1)
        return real()

    monkeypatch.setattr(grid, "_prefetch_visible_thumbnails", _counting)
    monkeypatch.setattr(
        grid, "_on_repaint_timer", lambda: (grid._repaint_visible_cells(), _counting())
    )
    grid._repaint_timer.timeout.disconnect()
    grid._repaint_timer.timeout.connect(grid._on_repaint_timer)
    try:
        grid.resize(600, 400)
        grid.show()
        for _ in range(3):
            grid.set_groups(_groups(5, 10))
        _drain()
        assert len(passes) == 1, (
            f"{len(passes)} prefetch passes queued for 3 back-to-back set_groups "
            "— the member single-shot timer must coalesce them"
        )
    finally:
        grid.teardown()
