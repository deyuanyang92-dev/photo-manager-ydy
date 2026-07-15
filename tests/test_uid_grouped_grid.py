"""Tests for :mod:`app.widgets.uid_grouped_grid` — virtualized UID-grouped
thumbnail grid (spec ``survey-summary-view`` Task T3, §2 / §5 / §6 红线).

Covers:
  * Model/data correctness (rowCount, roles, abbreviation).
  * Two input modes: ``set_groups`` (verbatim ``get_project_results`` payload)
    and flat ``set_paths``.
  * **Performance red-line (§5):** 2000 items → first-screen repaint < 200 ms
    (only visible cells paint; off-screen cells never decode).
  * Async decode wiring: delegate paint → worker → ``QPixmapCache`` hit → cell
    repaint; no QPixmap is constructed off the main thread (deferred to
    ``test_thumbnail_worker`` red-line test — re-asserted lightly here).
  * Stale-reply drop on model reset (no crash, no pixmap leaks into new model).
  * Negative cache: a failed path is not re-requested on every paint.
  * Thread lifecycle: ``teardown()`` and ``destroyed`` both quit the worker
    thread (memory: workbench-timer-leak-hang, shutdown-lock-leak-must-reboot).

Per spec §7: this is a NEW file, so the comment-preservation rule does not
apply here.
"""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap, QPixmapCache


def _grid_stub(*, unified: bool = False):
    from app.widgets.uid_grouped_grid import UidGroupedGrid

    class Stub:
        _failed_paths: set[str] = set()
        _pending_paths: set[str] = set()

        def thumb_size(self) -> int:
            return UidGroupedGrid.THUMB_SIZE

        def caption_fg(self) -> QColor:
            return QColor("#334155")

        def caption_bg(self) -> QColor:
            return QColor("#eef2f6")

        def unified_grid(self) -> bool:
            return unified

    return Stub()
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _clear_pixmap_cache():
    """Each test starts with a clean QPixmapCache so cached pixmaps from a
    previous test can't satisfy a miss silently."""
    QPixmapCache.clear()
    yield
    QPixmapCache.clear()


def _write_image(path, color="#4477aa", w=80, h=60) -> None:
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(color))
    assert img.save(str(path)), f"failed to write test image {path}"


def _make_groups(n_groups: int, items_per: int, base_dir, *,
                 prefix: str = "g") -> list[dict]:
    groups = []
    for gi in range(n_groups):
        items = []
        for ii in range(items_per):
            p = base_dir / f"{prefix}{gi}_{ii}.png"
            _write_image(p)
            items.append({"path": str(p), "name": p.name, "seq": ii + 1})
        groups.append({
            "uid": f"浙江-三门湾-B{gi:02d}-{gi * 100 + 1:03d}-A1-20240301",
            "items": items,
        })
    return groups


# ---------------------------------------------------------------------------
# Abbreviation helper
# ---------------------------------------------------------------------------

def test_uid_abbreviation_station_and_species_id():
    from app.widgets.uid_grouped_grid import uid_abbreviation
    assert uid_abbreviation("浙江-三门湾-B2-001-A1-20240301") == "B2-001"
    assert uid_abbreviation("P-Site-ST-007-X-2024") == "ST-007"


def test_uid_abbreviation_fallbacks():
    from app.widgets.uid_grouped_grid import uid_abbreviation
    # empty → 未分组
    assert uid_abbreviation("") == "未分组"
    # too few segments (no dashes) → tail verbatim
    assert uid_abbreviation("short") == "short"
    # very long non-standard (no dashes) → trimmed tail with ellipsis
    long_uid = "verylonguid_no_dashes_" + "x" * 40
    ab = uid_abbreviation(long_uid)
    assert ab.endswith("x"), f"expected trimmed tail, got {ab!r}"
    assert len(ab) <= 25, f"trimmed abbreviation too long: {len(ab)}"


# ---------------------------------------------------------------------------
# set_groups: structure + model roles
# ---------------------------------------------------------------------------

def test_set_groups_creates_sections_with_counts_and_summary(tmp_path):
    from app.widgets.uid_grouped_grid import UidGroupedGrid
    grid = UidGroupedGrid()
    try:
        groups = [
            {"uid": "浙江-三门湾-B2-001-A1-20240301", "items": [
                {"path": "/a.tif", "name": "a.tif", "seq": 1},
                {"path": "/b.tif", "name": "b.tif", "seq": 2},
            ]},
            {"uid": "浙江-三门湾-B3-DLC007-A1-20240302", "items": [
                {"path": "/c.tif", "name": "c.tif", "seq": 1},
            ]},
        ]
        grid.set_groups(groups)
        assert grid.section_count() == 2
        assert grid.section(0).model.rowCount() == 2
        assert grid.section(1).model.rowCount() == 1
        # header abbreviation
        assert grid.section(0).title_label.text() == "<b>B2-001</b>"
        assert grid.section(1).title_label.text() == "<b>B3-DLC007</b>"
        # count label
        assert grid.section(0).count_label.text() == "×2"
        assert grid.section(1).count_label.text() == "×1"
        # Headers stay unobtrusive while the user scrolls or resizes the grid.
        assert grid.section(0).title_label.toolTip() == ""
        # summary line
        summary = grid.summary_text()
        assert "2 个编号" in summary
        assert "3 张照片" in summary
    finally:
        grid.teardown()


def test_loading_cover_tracks_nonempty_first_viewport(tmp_path):
    from app.widgets.uid_grouped_grid import UidGroupedGrid

    grid = UidGroupedGrid()
    try:
        grid.set_groups(_make_groups(1, 2, tmp_path, prefix="cover"))
        assert grid._loading_cover_active is True
        assert not grid._loading_cover.isHidden()

        grid.set_groups([])
        assert grid._loading_cover_active is False
        assert grid._loading_cover.isHidden()
    finally:
        grid.teardown()


def test_loading_cover_timeout_does_not_reveal_pending_cells():
    from app.widgets.uid_grouped_grid import UidGroupedGrid

    grid = UidGroupedGrid()
    try:
        grid._show_loading_cover()
        grid._loading_cover_pending = {"slow.jpg"}

        grid._on_loading_cover_timeout()

        assert grid._loading_cover_active is True
        assert not grid._loading_cover.isHidden()
        assert grid._loading_cover_timer.isActive()
    finally:
        grid.teardown()


def test_set_groups_model_data_roles(tmp_path):
    from app.widgets.uid_grouped_grid import UidGroupedGrid, UidSectionModel
    grid = UidGroupedGrid()
    try:
        grid.set_groups([{"uid": "P-S-ST-042-A1-2024", "items": [
            {"path": "/x.tif", "name": "x.tif", "seq": 5},
        ]}])
        m = grid.section(0).model
        ix = m.index(0)
        assert ix.data(UidSectionModel.PATH_ROLE) == "/x.tif"
        assert ix.data(UidSectionModel.NAME_ROLE) == "x.tif"
        assert ix.data(UidSectionModel.SEQ_ROLE) == 5
        assert ix.data(UidSectionModel.UID_ROLE) == "P-S-ST-042-A1-2024"
        assert ix.data(UidSectionModel.ABBREV_ROLE) == "ST-042"
        # display role uses the seq
        assert ix.data() == "#5"
        # tooltip on the model carries the full uid (delegate may also use it)
        assert ix.data(Qt.ItemDataRole.ToolTipRole) == "P-S-ST-042-A1-2024"
    finally:
        grid.teardown()


def test_set_groups_with_empty_list_clears_existing_sections(tmp_path):
    from app.widgets.uid_grouped_grid import UidGroupedGrid
    grid = UidGroupedGrid()
    try:
        grid.set_groups([{"uid": "P-S-ST-001-A1-2024", "items": [
            {"path": "/a", "name": "a", "seq": 1}
        ]}])
        assert grid.section_count() == 1
        grid.set_groups([])
        assert grid.section_count() == 0
        assert "0 个编号" in grid.summary_text()
    finally:
        grid.teardown()


def test_clear_resets_summary(tmp_path):
    from app.widgets.uid_grouped_grid import UidGroupedGrid
    grid = UidGroupedGrid()
    try:
        grid.set_groups([{"uid": "P-S-ST-001-A1-2024", "items": [
            {"path": "/a", "name": "a", "seq": 1}
        ]}])
        grid.clear()
        assert grid.section_count() == 0
    finally:
        grid.teardown()


def test_unified_grid_merges_groups_into_one_list(tmp_path):
    """项目树成片预览：单网格铺满中栏，无 per-UID 灰条."""
    from app.widgets.uid_grouped_grid import UidGroupedGrid, UidSectionModel
    grid = UidGroupedGrid()
    try:
        groups = [
            {"uid": "浙江-三门湾-B2-001-A1-20240301", "items": [
                {"path": "/a.tif", "name": "a.tif", "seq": 1},
            ]},
            {"uid": "浙江-三门湾-B3-DLC007-A1-20240302", "items": [
                {"path": "/b.tif", "name": "b.tif", "seq": 2},
                {"path": "/c.tif", "name": "c.tif", "seq": 3},
            ]},
        ]
        grid.set_unified_grid(True)
        grid.set_groups(groups)
        assert grid.unified_grid()
        assert grid.section_count() == 1
        sec = grid.section(0)
        assert sec.list_view.objectName() == "uidUnifiedGrid"
        assert sec.model.rowCount() == 3
        ix = sec.model.index(1)
        assert ix.data(UidSectionModel.UID_ROLE) == "浙江-三门湾-B3-DLC007-A1-20240302"
        assert "浙江-三门湾-DLC007" in str(ix.data())
        assert "3 张照片" in grid.summary_text()
        assert "2 个编号" in grid.summary_text()
    finally:
        grid.teardown()


def test_set_unified_grid_false_restores_section_headers(tmp_path):
    from app.widgets.uid_grouped_grid import UidGroupedGrid
    grid = UidGroupedGrid()
    try:
        groups = [{"uid": "P-S-ST-001-A1-2024", "items": [
            {"path": "/a", "name": "a", "seq": 1},
        ]}]
        grid.set_unified_grid(True)
        grid.set_groups(groups)
        assert grid.section_count() == 1
        grid.set_unified_grid(False)
        assert not grid.unified_grid()
        assert grid.section_count() == 1
        assert grid.section(0).title_label.text() == "<b>ST-001</b>"
        assert grid.section(0).list_view.objectName() == "uidSectionList"
    finally:
        grid.teardown()


def test_set_sort_mode_reorders_unified_items(tmp_path):
    from app.widgets.uid_grouped_grid import UidGroupedGrid, UidSectionModel
    grid = UidGroupedGrid()
    try:
        groups = [
            {"uid": "Z-uid", "items": [{"path": "/z", "name": "z.tif", "seq": 1}]},
            {"uid": "A-uid", "items": [{"path": "/a", "name": "a.tif", "seq": 1}]},
        ]
        grid.set_unified_grid(True)
        grid.set_groups(groups)
        m = grid.section(0).model
        uids = [m.data(m.index(i), UidSectionModel.UID_ROLE) for i in range(m.rowCount())]
        assert uids == ["A-uid", "Z-uid"]
    finally:
        grid.teardown()


def test_density_index_scales_thumb_for_single_column():
    from app.config import project_tree_layout as ptl
    from app.widgets.uid_grouped_grid import UidGroupedGrid
    grid = UidGroupedGrid()
    try:
        grid.resize(800, 600)
        grid.show()
        QApplication.processEvents()
        dense = ptl.density_index_for_columns(8)
        sparse = ptl.density_index_for_columns(1)
        grid.set_density_index(dense)
        small = grid.thumb_size()
        grid.set_density_index(sparse)
        large = grid.thumb_size()
        assert large > small
        assert ptl.columns_for_density_index(sparse) == 1
    finally:
        grid.teardown()


# ---------------------------------------------------------------------------
# Flat mode
# ---------------------------------------------------------------------------

def test_set_paths_flat_mode_builds_single_ungrouped_section(tmp_path):
    from app.widgets.uid_grouped_grid import UidGroupedGrid
    grid = UidGroupedGrid()
    try:
        p1 = tmp_path / "x.png"; _write_image(p1)
        p2 = tmp_path / "y.png"; _write_image(p2)
        grid.set_paths([str(p1), str(p2)])
        assert grid.section_count() == 1
        sec = grid.section(0)
        assert sec.model.rowCount() == 2
        # abbreviation for empty uid is 未分组
        assert sec.title_label.text() == "<b>未分组</b>"
        # caption falls back to filename when seq is None
        ix = sec.model.index(0)
        assert ix.data() in ("x.png", "y.png")
    finally:
        grid.teardown()


# ---------------------------------------------------------------------------
# Performance red-line (spec §5): 2000 items, first-screen paint < 200 ms
# ---------------------------------------------------------------------------

def test_first_paint_under_200ms_with_2000_items(tmp_path):
    """RED LINE (spec §5): virtualization must keep first-screen repaint
    well under 200 ms even with 2000 items spread across many sections.

    Only visible cells should paint; off-screen cells must never synchronously
    decode. We use non-existent paths so no real file I/O happens — the test
    is about layout/paint cost, not decode cost.
    """
    from app.widgets.uid_grouped_grid import UidGroupedGrid
    grid = UidGroupedGrid()
    try:
        # 50 UIDs × 40 items = 2000
        n_groups, items_per = 50, 40
        groups = [
            {
                "uid": f"P-S-ST{gi:03d}-{gi * 10 + 1:03d}-A1-2024",
                "items": [
                    {
                        "path": f"/nonexistent/g{gi}_{ii}.tif",
                        "name": f"g{gi}_{ii}.tif",
                        "seq": ii + 1,
                    }
                    for ii in range(items_per)
                ],
            }
            for gi in range(n_groups)
        ]
        grid.set_groups(groups)
        grid.resize(800, 600)
        grid.show()
        QTest.qWait(30)
        QApplication.processEvents()

        start = time.perf_counter()
        grid.repaint()
        QApplication.processEvents()
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        assert elapsed_ms < 200, (
            f"first repaint took {elapsed_ms:.1f} ms for {n_groups * items_per} "
            "items across 50 sections; virtualization should keep it < 200 ms"
        )
    finally:
        grid.teardown()


def test_only_visible_cells_post_decode_requests(tmp_path):
    """Spec §5: off-screen cells must not trigger decode requests.

    Virtualization is the QListView's job: it only paints cells intersecting
    the viewport, so the delegate's ``paint`` (which is the only place a
    decode request originates) is never called for off-screen items. We
    verify the contract directly: with a single section + small viewport +
    many items, asking the widget to repaint does NOT flood the worker with
    requests for every item — only ones the delegate actually painted.
    """
    from app.widgets.uid_grouped_grid import UidGroupedGrid
    grid = UidGroupedGrid()
    try:
        # 1 section with 200 items; viewport can show maybe a dozen.
        items = [
            {"path": f"/nonexistent/i{i}.tif", "name": f"i{i}.tif", "seq": i + 1}
            for i in range(200)
        ]
        grid.set_groups([{"uid": "P-S-ST-001-A1-2024", "items": items}])
        grid.resize(400, 200)
        grid.show()
        sec = grid.section(0)
        _, _cw, ch = __import__(
            "app.config.project_tree_layout", fromlist=["cell_dims"]
        ).cell_dims(grid.thumb_size())
        sec.list_view.setFixedHeight(ch * 3 + 4)
        QApplication.processEvents()
        sec.list_view.viewport().repaint()
        QApplication.processEvents()
        posted = len(grid._pending) + len(grid._failed_paths)
        assert posted < 200, (
            f"delegate painted {posted} cells (of 200) — virtualization should "
            "skip off-screen cells entirely"
        )
    finally:
        grid.teardown()


# ---------------------------------------------------------------------------
# Async decode wiring (real image) — driven directly for determinism
# ---------------------------------------------------------------------------

def test_request_decode_posts_to_worker_and_caches_on_reply(tmp_path):
    """``_request_decode`` (the same code path the delegate calls on a cache
    miss) posts a queued ``decode`` to the worker. The worker emits ``QImage``;
    the widget's ``_on_decoded`` slot converts to ``QPixmap`` via
    ``make_pixmap`` and stores it in ``QPixmapCache``.
    """
    from app.widgets.uid_grouped_grid import UidGroupedGrid, _cache_key
    p = tmp_path / "thumb.png"
    _write_image(p, "#cc2222")
    grid = UidGroupedGrid()
    try:
        grid.set_groups([{"uid": "P-S-ST-001-A1-2024", "items": [
            {"path": str(p), "name": "thumb.png", "seq": 1}
        ]}])
        spy = QSignalSpy(grid._worker.decoded)
        grid._request_decode(0, 0, str(p))
        assert spy.wait(5000), "worker did not emit decoded"

        # Let the queued _on_decoded slot run on the main thread.
        for _ in range(30):
            QApplication.processEvents()
            if QPixmapCache.find(_cache_key(str(p))) is not None:
                break
            QTest.qWait(15)

        cached = QPixmapCache.find(_cache_key(str(p)))
        assert cached is not None, "pixmap not cached after async decode"
        assert not cached.isNull()
        assert isinstance(cached, QPixmap), (
            "cached thumbnail must be a QPixmap (worker QImage converted on main thread)"
        )
    finally:
        grid.teardown()


def test_decoded_reply_notifies_the_model_cell(tmp_path):
    """After the pixmap lands in cache, the model emits ``dataChanged`` for
    that one cell so the delegate repaints and finds the cached pixmap.
    Verified via QSignalSpy on the section model.
    """
    from app.widgets.uid_grouped_grid import UidGroupedGrid, _cache_key
    p = tmp_path / "k.png"
    _write_image(p)
    grid = UidGroupedGrid()
    try:
        grid.set_groups([{"uid": "P-S-ST-001-A1-2024", "items": [
            {"path": str(p), "name": "k.png", "seq": 1}
        ]}])
        model = grid.section(0).model
        spy_model = QSignalSpy(model.dataChanged)
        grid._request_decode(0, 0, str(p))
        # Wait for worker + queued slot chain.
        for _ in range(40):
            QApplication.processEvents()
            if len(spy_model) > 0:
                break
            QTest.qWait(15)
        assert len(spy_model) >= 1, "model.dataChanged not emitted after decode"
        top_left, bottom_right, _roles = spy_model[0]
        assert top_left.row() == 0 and bottom_right.row() == 0
    finally:
        grid.teardown()


def test_delegate_paint_invokes_request_fn_on_miss(tmp_path):
    """Unit-test the delegate in isolation: painting a cell whose path is not
    in ``QPixmapCache`` must call the supplied ``request_fn(section, row,
    path)``. Painting a cell that IS cached must not call it again.
    """
    from app.widgets.uid_grouped_grid import (
        _ThumbDelegate, UidSectionModel, _cache_key,
    )

    requests: list[tuple[int, int, str]] = []

    def fake_request(section_idx: int, row: int, path: str) -> None:
        requests.append((section_idx, row, path))

    p_cached = str(tmp_path / "cached.png")
    p_miss = str(tmp_path / "miss.png")
    # Seed the cache for one of the two paths.
    seed = QPixmap(40, 30)
    seed.fill(QColor("#11aa11"))
    QPixmapCache.insert(_cache_key(p_cached), seed)

    model = UidSectionModel("P-S-ST-001-A1-2024", [
        {"path": p_cached, "name": "cached.png", "seq": 1},
        {"path": p_miss, "name": "miss.png", "seq": 2},
    ])

    class _GridStub:
        _failed_paths: set[str] = set()
        _pending_paths: set[str] = set()

        def thumb_size(self) -> int:
            from app.widgets.uid_grouped_grid import _THUMB_SIZE
            return _THUMB_SIZE

        def caption_fg(self) -> QColor:
            return QColor("#334155")

        def caption_bg(self) -> QColor:
            return QColor("#eef2f6")

        def unified_grid(self) -> bool:
            return False

    delegate = _ThumbDelegate(_GridStub(), fake_request, section_idx=7)

    canvas = QPixmap(_THUMB_CELL_W(), _THUMB_CELL_H())
    canvas.fill(QColor("#000000"))
    painter = QPainter(canvas)

    from PyQt6.QtWidgets import QStyleOptionViewItem
    for row in (0, 1):
        opt = QStyleOptionViewItem()
        opt.rect = _cell_rect(row)
        delegate.paint(painter, opt, model.index(row))
    painter.end()

    # Only the cache miss should have produced a request, with the section
    # index the delegate was constructed with.
    assert requests == [(7, 1, p_miss)], (
        f"expected exactly one request for the miss, got {requests}"
    )


def test_delegate_paint_skips_request_when_path_empty():
    """An empty path (malformed item) must not post a request."""
    from app.widgets.uid_grouped_grid import _ThumbDelegate, UidSectionModel

    requests: list[tuple[int, int, str]] = []

    delegate = _ThumbDelegate(
        _grid_stub(),
        lambda s, r, p: requests.append((s, r, p)),
        section_idx=0,
    )
    model = UidSectionModel("", [{"path": "", "name": "", "seq": None}])

    canvas = QPixmap(_THUMB_CELL_W(), _THUMB_CELL_H())
    painter = QPainter(canvas)
    from PyQt6.QtWidgets import QStyleOptionViewItem
    opt = QStyleOptionViewItem()
    opt.rect = _cell_rect(0)
    delegate.paint(painter, opt, model.index(0))
    painter.end()
    assert requests == [], "empty path must not post a decode request"


# ---------------------------------------------------------------------------
# Stale replies + negative cache
# ---------------------------------------------------------------------------

def test_stale_reply_after_reset_is_dropped(tmp_path):
    """If ``set_groups([])`` is called while a decode is in flight, the stale
    reply must be dropped silently — no pixmap leaks into the new (empty)
    model, no crash. Driven directly via ``_request_decode`` for determinism.
    """
    from app.widgets.uid_grouped_grid import UidGroupedGrid, _cache_key
    p = tmp_path / "stale.png"
    _write_image(p)
    grid = UidGroupedGrid()
    try:
        grid.set_groups([{"uid": "P-S-ST-001-A1-2024", "items": [
            {"path": str(p), "name": "stale.png", "seq": 1}
        ]}])
        # Post the request and capture its id, then blow away pending state
        # before the reply lands (simulates a fast model reset).
        grid._request_decode(0, 0, str(p))
        req_id = grid._req_counter  # the id just handed out
        assert req_id in grid._pending
        grid.set_groups([])
        assert grid.section_count() == 0
        assert grid._pending == {}, "reset must drop all in-flight requests"

        # Drain events — the stale decoded() will fire here and must be a no-op.
        for _ in range(40):
            QApplication.processEvents()
            QTest.qWait(15)
        # The stale pixmap must NOT be in cache (its slot returned early).
        assert QPixmapCache.find(_cache_key(str(p))) is None
        assert grid.section_count() == 0  # still intact, no crash
    finally:
        grid.teardown()


def test_failed_path_is_negative_cached(tmp_path):
    """A path that fails to decode (missing file → ``image is None``) is
    negative-cached: a second ``_request_decode`` for the same path does NOT
    post a new decode (would otherwise busy-loop on every paint).
    """
    from app.widgets.uid_grouped_grid import UidGroupedGrid
    grid = UidGroupedGrid()
    try:
        bad = "/nonexistent/definitely_missing_12345.png"
        grid.set_groups([{"uid": "P-S-ST-001-A1-2024", "items": [
            {"path": bad, "name": "missing.png", "seq": 1}
        ]}])
        spy = QSignalSpy(grid._worker.decoded)
        grid._request_decode(0, 0, bad)
        assert spy.wait(5000), "first decode must fire even for missing file"

        # Drain so _on_decoded runs and negative-caches the path.
        for _ in range(30):
            QApplication.processEvents()
            if bad in grid._failed_paths:
                break
            QTest.qWait(15)
        assert bad in grid._failed_paths, "missing-file path should be negative-cached"
        assert bad not in grid._pending_paths

        # Second call: must NOT post anything (returns early).
        spy2 = QSignalSpy(grid._worker.decoded)
        grid._request_decode(0, 0, bad)
        arrived = spy2.wait(150)
        assert not arrived, (
            "negative-cached path was re-requested (busy-loop risk)"
        )
    finally:
        grid.teardown()


def test_pending_paths_dedupes_in_flight_requests(tmp_path):
    """Two calls for the same path before the first reply lands: only one
    decode is posted; the cache is populated once and the second caller
    finds the cached pixmap."""
    from app.widgets.uid_grouped_grid import UidGroupedGrid, _cache_key
    p = tmp_path / "shared.png"
    _write_image(p)
    grid = UidGroupedGrid()
    try:
        grid.set_groups([
            {"uid": "P-S-ST-001-A1-2024", "items": [
                {"path": str(p), "name": "shared.png", "seq": 1}
            ]},
            {"uid": "P-S-ST-002-A1-2024", "items": [
                {"path": str(p), "name": "shared.png", "seq": 1}
            ]},
        ])
        spy = QSignalSpy(grid._worker.decoded)
        # Two sections ask for the same path back-to-back.
        grid._request_decode(0, 0, str(p))
        grid._request_decode(1, 0, str(p))
        # Only one in-flight entry should exist.
        assert len(grid._pending) == 1, (
            f"duplicate request not deduped, pending={grid._pending}"
        )
        assert spy.wait(5000)
        for _ in range(30):
            QApplication.processEvents()
            if QPixmapCache.find(_cache_key(str(p))) is not None:
                break
            QTest.qWait(15)
        assert QPixmapCache.find(_cache_key(str(p))) is not None
    finally:
        grid.teardown()


# ---------------------------------------------------------------------------
# Test helpers for delegate unit tests
# ---------------------------------------------------------------------------

def _THUMB_CELL_W() -> int:
    from app.widgets.uid_grouped_grid import _CELL_W
    return _CELL_W


def _THUMB_CELL_H() -> int:
    from app.widgets.uid_grouped_grid import _CELL_H
    return _CELL_H


def _cell_rect(row: int):
    from PyQt6.QtCore import QRect
    from app.widgets.uid_grouped_grid import _CELL_W, _CELL_H
    return QRect(0, row * _CELL_H, _CELL_W, _CELL_H)


# ---------------------------------------------------------------------------
# Thread lifecycle
# ---------------------------------------------------------------------------

def test_teardown_quits_worker_thread(tmp_path):
    from app.widgets.uid_grouped_grid import UidGroupedGrid
    grid = UidGroupedGrid()
    thread = grid._thread
    assert thread.isRunning()
    grid.teardown()
    assert not thread.isRunning(), "teardown() must quit+wait the worker thread"


def test_destroyed_auto_quits_worker_thread(tmp_path):
    """Spec §6 / memory shutdown-lock-leak-must-reboot: the widget's
    ``destroyed`` signal must stop the worker thread without an explicit
    teardown() call.
    """
    from app.widgets.uid_grouped_grid import UidGroupedGrid
    grid = UidGroupedGrid()
    thread = grid._thread
    assert thread.isRunning()
    grid.deleteLater()
    # Process events until destruction fires (and the cleanup closure runs).
    deadline = time.perf_counter() + 3.0
    while time.perf_counter() < deadline:
        QApplication.processEvents()
        QTest.qWait(15)
        if not thread.isRunning():
            break
    assert not thread.isRunning(), (
        "destroyed did not quit the worker thread within 3 s"
    )


def test_widget_module_does_not_construct_qpixmap_on_worker_thread():
    """Light static check: the worker module is the one that must avoid
    QPixmap (asserted in test_thumbnail_worker). Here we just confirm the
    grid widget keeps that contract by importing make_pixmap and using it
    only in a main-thread slot — i.e. the widget module references QPixmap
    only via the main-thread QPixmapCache + delegate paint path.
    """
    import inspect
    from app.widgets import uid_grouped_grid
    source = inspect.getsource(uid_grouped_grid)
    # The widget module references QPixmap (legitimately, on main thread) but
    # must NOT construct it from any worker callback. We assert the module
    # does not import decode_image_thumbnail's QPixmap-returning path.
    assert "decode_image_thumbnail" not in source
    assert "decode_image_pixmap" not in source
    # The only image→pixmap bridge used is make_pixmap (main thread).
    assert "make_pixmap" in source


def test_grid_list_has_context_menu_policy(tmp_path):
    from app.widgets.uid_grouped_grid import UidGroupedGrid

    path = tmp_path / "a.jpg"
    from PyQt6.QtGui import QColor, QImage

    img = QImage(32, 32, QImage.Format.Format_RGB32)
    img.fill(QColor("#336699"))
    assert img.save(str(path))
    grid = UidGroupedGrid()
    grid.set_paths([str(path)])
    sec = grid.section(0)
    assert sec is not None
    assert sec.list_view.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
    grid.teardown()


def test_context_menu_keeps_multi_selection_and_returns_all_paths(tmp_path):
    from PyQt6.QtCore import QItemSelectionModel
    from app.widgets.uid_grouped_grid import UidGroupedGrid

    paths = []
    for name in ("a.jpg", "b.jpg"):
        path = tmp_path / name
        _write_image(path)
        paths.append(str(path))
    grid = UidGroupedGrid()
    grid.set_paths(paths)
    section = grid.section(0)
    assert section is not None
    selection = section.list_view.selectionModel()
    for row in range(2):
        selection.select(
            section.model.index(row, 0),
            QItemSelectionModel.SelectionFlag.Select,
        )

    entries = grid._context_selected_photo_entries(
        section.list_view, section.model.index(1, 0)
    )

    assert [path for path, _item in entries] == paths
    assert len(selection.selectedIndexes()) == 2
    grid.teardown()


def test_batch_export_selected_tiffs_uses_parallel_worker(tmp_path, monkeypatch):
    from app.services.tiff_jpeg_export_service import OverwritePolicy
    from app.widgets.uid_grouped_grid import UidGroupedGrid

    paths = []
    for name in ("a.tif", "b.tif"):
        path = tmp_path / name
        path.write_bytes(b"tiff")
        paths.append(str(path))
    captured = {}

    class _Signal:
        def connect(self, fn):
            pass

    class _FakeWorker:
        def __init__(self, sources, settings, **kwargs):
            captured.update(sources=sources, kwargs=kwargs)
            self.progress = _Signal()
            self.finished = _Signal()
            self.failed = _Signal()

        def isRunning(self):
            return False

        def start(self):
            captured["started"] = True

    monkeypatch.setattr(
        "app.workers.tiff_jpeg_export_worker.TiffJpegExportWorker",
        _FakeWorker,
    )
    grid = UidGroupedGrid()

    grid._export_photos_jpg(paths)

    assert captured["sources"] == paths
    assert captured["kwargs"]["smart"] is True
    assert captured["kwargs"]["overwrite"] == OverwritePolicy.RENAME
    assert captured["started"] is True
    grid._batch_export_worker = None
    grid.teardown()


def test_merge_groups_by_catalog_key_combines_same_unique_id():
    from app.widgets.uid_grouped_grid import merge_groups_by_catalog_key

    merged = merge_groups_by_catalog_key([
        {
            "uid": "GXFCG-BLW-BZC003-R-20260618",
            "items": [{"path": "/a.tif", "name": "a.tif", "seq": 1}],
        },
        {
            "uid": "GXFCG-BLW-BZC003-R-20260618",
            "items": [{"path": "/b.tif", "name": "b.tif", "seq": 2}],
        },
        {
            "uid": "GXFCG-BLW-PGC001-R-20260618",
            "items": [{"path": "/c.tif", "name": "c.tif", "seq": 1}],
        },
    ])
    assert len(merged) == 2
    assert len(merged[0]["items"]) == 2
    assert len(merged[1]["items"]) == 1


def test_uid_catalog_merges_same_unique_id(qtbot):
    from app.widgets.uid_grouped_grid import UidGroupedGrid

    grid = UidGroupedGrid()
    qtbot.addWidget(grid)
    uid = "GXFCG-BLW-BZC003-R-20260618"
    grid.set_groups([
        {"uid": uid, "items": [{"path": "/a.tif", "name": "a.tif", "seq": 1}]},
        {"uid": uid, "items": [{"path": "/b.tif", "name": "b.tif", "seq": 2}]},
    ])
    catalog = grid.uid_catalog()
    assert len(catalog) == 1
    assert catalog[0]["abbrev"] == "GXFCG-BLW-BZC003"
    assert catalog[0]["count"] == 2
    assert grid.scroll_to_uid("GXFCG-BLW-BZC003") is True
    grid.teardown()


def test_uid_catalog_and_scroll_to_uid(qtbot):
    from app.widgets.uid_grouped_grid import UidGroupedGrid

    grid = UidGroupedGrid()
    qtbot.addWidget(grid)
    grid.set_groups([
        {
            "uid": "GXFCG-BLW-B2-SC001-R-20260621",
            "items": [{"path": "/a.tif", "name": "a.tif", "seq": 1}],
        },
        {
            "uid": "GXFCG-BLW-B3-SC002-T95E-20260622",
            "items": [{"path": "/b.tif", "name": "b.tif", "seq": 1}],
        },
    ])
    catalog = grid.uid_catalog()
    assert len(catalog) == 2
    assert catalog[0]["abbrev"] == "GXFCG-BLW-SC001"
    assert grid.scroll_to_uid(catalog[1]["uid"]) is True
    first = grid.first_photo_for_uid(catalog[0]["uid"])
    assert first is not None and first[0] == "/a.tif"
    grid.teardown()


def test_uid_catalog_unified_mode(qtbot):
    from app.widgets.uid_grouped_grid import UidGroupedGrid

    grid = UidGroupedGrid()
    qtbot.addWidget(grid)
    grid.set_unified_grid(True)
    grid.set_groups([
        {
            "uid": "浙江-三门湾-B2-1-R95E-260621",
            "items": [{"path": "/a.tif", "name": "a.tif", "seq": 1}],
        },
    ])
    assert len(grid.uid_catalog()) == 1
    assert grid.scroll_to_uid("浙江-三门湾-B2-1-R95E-260621") is True
    grid.teardown()


def test_format_grid_caption_smart_and_core():
    from app.config import project_tree_layout as ptl
    from app.utils.naming import uid_display_core, uid_group_key

    uid = "GXFCG-BLW-BZC003-R-20260618"
    item = {"path": "/a.tif", "name": "a.tif", "seq": 3, "uid": uid, "_uid": uid}
    kw = dict(
        group_uid="",
        unified=True,
        catalog_counts={uid_group_key(uid): 5},
        effective_uid_fn=lambda gu, it: str(gu or it.get("_uid") or it.get("uid") or ""),
    )
    assert ptl.format_grid_caption(item, mode="smart", **kw) == "GXFCG-BLW-BZC003 · #3"
    assert ptl.format_grid_caption(item, mode="core", **kw) == "GXFCG-BLW-BZC003"
    assert ptl.format_grid_caption(item, mode="core_seq", **kw) == "GXFCG-BLW-BZC003 · #3"
    kw_single = dict(kw, catalog_counts={uid_group_key(uid): 1})
    assert ptl.format_grid_caption(item, mode="smart", **kw_single) == "GXFCG-BLW-BZC003"
    assert uid_display_core(uid) == "GXFCG-BLW-BZC003"


def test_unified_caption_mode_core_only(qtbot):
    from app.widgets.uid_grouped_grid import UidGroupedGrid

    grid = UidGroupedGrid()
    qtbot.addWidget(grid)
    grid.set_unified_grid(True)
    grid.set_caption_mode("core")
    uid = "GXFCG-BLW-BZC003-R-20260618"
    grid.set_groups([
        {"uid": uid, "items": [{"path": "/a.tif", "name": "a.tif", "seq": 1}]},
        {"uid": uid, "items": [{"path": "/b.tif", "name": "b.tif", "seq": 10}]},
    ])
    m = grid.section(0).model
    assert m.data(m.index(0)) == "GXFCG-BLW-BZC003"
    assert m.data(m.index(1)) == "GXFCG-BLW-BZC003"
    grid.set_caption_mode("core_seq")
    m = grid.section(0).model
    assert "· #10" in m.data(m.index(1))
    grid.teardown()


# ---------------------------------------------------------------------------
# Loading-cover robustness (BUG 1 / BUG 2 — code-review 2026-07-14)
# ---------------------------------------------------------------------------

def test_prefetch_budget_never_skips_strictly_visible_rows(monkeypatch):
    """BUG 1: the ``_PREFETCH_BUDGET`` cutoff must apply only to the extra
    ±1-screen prefetch band, never to strictly-visible viewport rows. Every
    strictly-visible cell must be requested regardless of budget, otherwise
    the first-screen loading cover can hide while genuinely-visible cells are
    still unrequested (their paths never entered ``_loading_cover_pending``).
    """
    import app.widgets.uid_grouped_grid as ug
    from app.widgets.uid_grouped_grid import UidGroupedGrid

    n = 40
    # Budget far smaller than the strictly-visible span — with the old early
    # ``return`` this stops after ``budget`` rows and skips the rest + cover.
    monkeypatch.setattr(ug, "_PREFETCH_BUDGET", 5)

    grid = UidGroupedGrid()
    try:
        items = [
            {"path": f"/nonexistent/vis{i}.tif", "name": f"vis{i}.tif", "seq": i + 1}
            for i in range(n)
        ]
        grid.set_groups([{"uid": "P-S-ST-001-A1-2024", "items": items}])

        # Every row is strictly visible (prefetch band == strict span == all).
        def _fake_span(sec, prefetch=True):
            return (0, n - 1)

        monkeypatch.setattr(grid, "_visible_row_span", _fake_span)

        grid._prefetch_visible_thumbnails()

        # All strictly-visible rows requested, not just the first ``budget``.
        assert len(grid._pending_paths) == n, (
            f"budget cutoff skipped strictly-visible rows: only "
            f"{len(grid._pending_paths)}/{n} requested"
        )
        # A late row (well past the budget) must have been requested.
        assert "/nonexistent/vis39.tif" in grid._pending_paths
    finally:
        grid.teardown()


def test_visible_prefetch_bursts_are_bounded_then_drain(qtbot, monkeypatch):
    """codex 回归发现: BUG1 的"严格可见行无视预算"修法在小缩略图/大视口场景下,
    单次 ``_prefetch_visible_thumbnails`` 调用可能一口气派发几百个解码请求,
    全堆给单一 worker 线程的消息队列(2000 张照片测出 424 条 vs 预算 96)。

    修复后契约: 单次调用只发 ``_VISIBLE_PREFETCH_BUDGET`` 条(有界爆发),剩下的
    严格可见格子不放弃(不能回到 BUG1),靠短延时的自续 tick 分批排空,最终全部
    请求到——不是"发一次就完事"，也不是"发到底"，是"分批、但保证不漏"。
    """
    import app.widgets.uid_grouped_grid as ug
    from app.widgets.uid_grouped_grid import UidGroupedGrid

    n = 300
    monkeypatch.setattr(ug, "_VISIBLE_PREFETCH_BUDGET", 50)
    monkeypatch.setattr(ug, "_PREFETCH_CONTINUATION_MS", 5)

    grid = UidGroupedGrid()
    qtbot.addWidget(grid)
    try:
        items = [
            {"path": f"/nonexistent/vis{i}.tif", "name": f"vis{i}.tif", "seq": i + 1}
            for i in range(n)
        ]
        grid.set_groups([{"uid": "P-S-ST-001-A1-2024", "items": items}])

        def _fake_span(sec, prefetch=True):
            return (0, n - 1)

        monkeypatch.setattr(grid, "_visible_row_span", _fake_span)

        # Count distinct decode dispatches directly (robust against the real
        # worker thread racing in and moving paths pending -> failed while we
        # wait, which would otherwise make a raw len(_pending_paths) check racy).
        requested = set()
        orig_request_decode = grid._request_decode

        def _counting_request_decode(section_idx, row, path):
            requested.add(path)
            return orig_request_decode(section_idx, row, path)

        monkeypatch.setattr(grid, "_request_decode", _counting_request_decode)

        grid._prefetch_visible_thumbnails()

        # Bounded burst: this single call must not have dispatched all 300 at once.
        assert len(requested) == 50, (
            f"expected a single call to be capped at 50, got {len(requested)}"
        )

        # But it must not be abandoned either — the continuation timer drains
        # the rest within a few ticks, and every row is eventually requested.
        qtbot.waitUntil(lambda: len(requested) == n, timeout=2000)
    finally:
        grid.teardown()


def test_clear_sections_empties_loading_cover_pending():
    """BUG 2: ``_clear_sections()`` must drop ``_loading_cover_pending`` along
    with ``_pending``/``_pending_paths``. Otherwise stale in-flight paths keep
    the pending set non-empty forever (their replies land stale and never
    discard), permanently blocking the cover from ever hiding again.
    """
    from app.widgets.uid_grouped_grid import UidGroupedGrid

    grid = UidGroupedGrid()
    try:
        grid._show_loading_cover()
        grid._loading_cover_pending = {"stale1.jpg", "stale2.jpg"}

        grid._clear_sections()

        assert grid._loading_cover_pending == set(), (
            "_clear_sections left stale paths in _loading_cover_pending"
        )
    finally:
        grid.teardown()
