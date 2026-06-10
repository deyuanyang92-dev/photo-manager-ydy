"""test_tile_map_widget.py — TDD tests for TileMapWidget and related utilities.

Runs headless (QT_QPA_PLATFORM=offscreen).

Covers:
  - Coordinate math pure functions (lon_lat_to_tile_xy, inverse, pixel conversion, clamp_zoom).
  - _TileCache LRU eviction and access refresh.
  - TileMapWidget construction, public API, signals.
  - MapPickDialog.available() always True; dialog construction; OK enable flow.
"""
from __future__ import annotations

import math
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton

_APP = QApplication.instance() or QApplication([])


# ── helpers ─────────────────────────────────────────────────────────────────

def _import_math():
    from app.widgets.tile_map_widget import (
        lon_lat_to_tile_xy,
        tile_xy_to_lon_lat,
        pixel_to_lon_lat,
        lon_lat_to_pixel,
        clamp_zoom,
    )
    return lon_lat_to_tile_xy, tile_xy_to_lon_lat, pixel_to_lon_lat, lon_lat_to_pixel, clamp_zoom


def _import_cache():
    from app.widgets.tile_map_widget import _TileCache
    return _TileCache


def _import_widget():
    from app.widgets.tile_map_widget import TileMapWidget
    return TileMapWidget


# ── coordinate math ─────────────────────────────────────────────────────────

class TestTileMath:
    def test_origin_at_z0(self):
        lon_lat_to_tile_xy, *_ = _import_math()
        tx, ty = lon_lat_to_tile_xy(0.0, 0.0, 0)
        assert abs(tx - 0.5) < 1e-9
        assert abs(ty - 0.5) < 1e-9

    def test_inverse_round_trip_origin(self):
        lon_lat_to_tile_xy, tile_xy_to_lon_lat, *_ = _import_math()
        for lon, lat in [(0.0, 0.0), (121.76, 29.11), (-74.0, 40.7), (180.0, 0.0), (-180.0, 0.0)]:
            tx, ty = lon_lat_to_tile_xy(lon, lat, 12)
            lon2, lat2 = tile_xy_to_lon_lat(tx, ty, 12)
            assert abs(lon2 - lon) < 1e-7, f"lon mismatch for ({lon}, {lat})"
            assert abs(lat2 - lat) < 1e-7, f"lat mismatch for ({lon}, {lat})"

    def test_clamp_zoom_below_min(self):
        *_, clamp_zoom = _import_math()
        assert clamp_zoom(1) == 2

    def test_clamp_zoom_above_max(self):
        *_, clamp_zoom = _import_math()
        assert clamp_zoom(20) == 18

    def test_clamp_zoom_in_range(self):
        *_, clamp_zoom = _import_math()
        assert clamp_zoom(12) == 12

    def test_pixel_center_returns_center_coords(self):
        _, _, pixel_to_lon_lat, lon_lat_to_pixel, _ = _import_math()
        lon, lat, z, w, h = 121.76, 29.11, 12, 800, 600
        px, py = lon_lat_to_pixel(lon, lat, lon, lat, z, w, h)
        assert abs(px - w // 2) <= 1
        assert abs(py - h // 2) <= 1

    def test_pixel_round_trip(self):
        _, _, pixel_to_lon_lat, lon_lat_to_pixel, _ = _import_math()
        center_lon, center_lat, z, w, h = 121.76, 29.11, 12, 800, 600
        for lon, lat in [(121.76, 29.11), (122.0, 29.5)]:
            px, py = lon_lat_to_pixel(lon, lat, center_lon, center_lat, z, w, h)
            lon2, lat2 = pixel_to_lon_lat(px, py, center_lon, center_lat, z, w, h)
            assert abs(lon2 - lon) < 1e-4, f"lon round-trip failed: {lon2} vs {lon}"
            assert abs(lat2 - lat) < 1e-4, f"lat round-trip failed: {lat2} vs {lat}"

    def test_tile_xy_at_z1_corners(self):
        lon_lat_to_tile_xy, *_ = _import_math()
        # top-left tile (0,0) at z=1 covers western/northern hemisphere
        tx, ty = lon_lat_to_tile_xy(-90.0, 66.51, 1)
        assert 0 <= tx < 1 and 0 <= ty < 1


# ── LRU tile cache ───────────────────────────────────────────────────────────

class TestTileCache:
    def test_miss_returns_none(self):
        _TileCache = _import_cache()
        c = _TileCache(max_size=10)
        assert c.get((0, 0, 0)) is None

    def test_put_and_hit(self):
        from PyQt6.QtGui import QPixmap
        _TileCache = _import_cache()
        c = _TileCache(max_size=10)
        px = QPixmap(1, 1)
        c.put((0, 0, 0), px)
        assert c.get((0, 0, 0)) is px

    def test_evicts_lru_at_max_plus_one(self):
        from PyQt6.QtGui import QPixmap
        _TileCache = _import_cache()
        c = _TileCache(max_size=3)
        for i in range(4):
            c.put((0, i, 0), QPixmap(1, 1))
        assert c.get((0, 0, 0)) is None   # first inserted, should be evicted

    def test_access_refreshes_lru(self):
        from PyQt6.QtGui import QPixmap
        _TileCache = _import_cache()
        c = _TileCache(max_size=3)
        for i in range(3):
            c.put((0, i, 0), QPixmap(1, 1))
        _ = c.get((0, 0, 0))             # refresh key 0
        c.put((0, 3, 0), QPixmap(1, 1))  # evicts key 1 (LRU now)
        assert c.get((0, 0, 0)) is not None
        assert c.get((0, 1, 0)) is None

    def test_overwrite_moves_to_end(self):
        from PyQt6.QtGui import QPixmap
        _TileCache = _import_cache()
        c = _TileCache(max_size=2)
        px1, px2 = QPixmap(1, 1), QPixmap(2, 2)
        c.put((0, 0, 0), px1)
        c.put((0, 1, 0), px1)
        c.put((0, 0, 0), px2)   # overwrite key 0 → moves to end
        c.put((0, 2, 0), px1)   # evicts key 1
        assert c.get((0, 0, 0)) is px2
        assert c.get((0, 1, 0)) is None


# ── TileMapWidget ────────────────────────────────────────────────────────────

class TestTileMapWidget:
    def test_instantiates(self):
        TileMapWidget = _import_widget()
        w = TileMapWidget()
        assert w is not None

    def test_has_marker_moved_signal(self):
        TileMapWidget = _import_widget()
        w = TileMapWidget()
        assert hasattr(w, "marker_moved")

    def test_marker_initially_none(self):
        TileMapWidget = _import_widget()
        w = TileMapWidget()
        assert w.marker_lon is None
        assert w.marker_lat is None

    def test_set_marker_updates_properties(self):
        TileMapWidget = _import_widget()
        w = TileMapWidget()
        w.set_marker(121.76, 29.11)
        assert abs(w.marker_lon - 121.76) < 1e-9
        assert abs(w.marker_lat - 29.11) < 1e-9

    def test_set_marker_emits_signal(self):
        TileMapWidget = _import_widget()
        w = TileMapWidget()
        received = []
        w.marker_moved.connect(lambda lon, lat: received.append((lon, lat)))
        w.set_marker(121.76, 29.11)
        assert len(received) == 1
        assert abs(received[0][0] - 121.76) < 1e-9
        assert abs(received[0][1] - 29.11) < 1e-9

    def test_clear_marker_sets_none(self):
        TileMapWidget = _import_widget()
        w = TileMapWidget()
        w.set_marker(121.76, 29.11)
        w.clear_marker()
        assert w.marker_lon is None
        assert w.marker_lat is None

    def test_set_center_changes_state(self):
        TileMapWidget = _import_widget()
        w = TileMapWidget()
        w.set_center(100.0, 20.0, zoom=10)
        assert abs(w._center_lon - 100.0) < 1e-9
        assert abs(w._center_lat - 20.0) < 1e-9
        assert w._zoom == 10

    def test_set_center_clamps_zoom(self):
        TileMapWidget = _import_widget()
        w = TileMapWidget()
        w.set_center(0.0, 0.0, zoom=25)
        assert w._zoom == 18

    def test_attribution_label_present(self):
        TileMapWidget = _import_widget()
        w = TileMapWidget()
        w.resize(400, 300)
        labels = w.findChildren(QLabel)
        texts = [lbl.text() for lbl in labels]
        assert any("OpenStreetMap" in t for t in texts), f"No OSM attribution. Labels: {texts}"

    def test_clear_marker_does_not_emit_signal(self):
        TileMapWidget = _import_widget()
        w = TileMapWidget()
        received = []
        w.marker_moved.connect(lambda lon, lat: received.append((lon, lat)))
        w.clear_marker()                  # nothing to clear → no emit expected
        assert len(received) == 0

    def test_search_place_does_not_crash_empty(self):
        TileMapWidget = _import_widget()
        w = TileMapWidget()
        w.search_place("")               # empty query → no-op, no crash

    def test_search_place_sets_search_thread(self):
        TileMapWidget = _import_widget()
        w = TileMapWidget()
        w.search_place("杭州")
        assert w._search_thread is not None
        # Stop the thread so it doesn't abort in teardown
        if w._search_thread.isRunning():
            w._search_thread.quit()
            w._search_thread.wait(2000)

    @staticmethod
    def _pump_until(cond, timeout_s=4.0):
        """Pump the event loop (incl. DeferredDelete) until cond() or timeout."""
        import time
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            QApplication.processEvents()
            if cond():
                for _ in range(10):   # flush queued signals + deleteLater
                    QApplication.processEvents()
                    time.sleep(0.01)
                return True
            time.sleep(0.01)
        return False

    def test_search_place_twice_sets_marker(self, monkeypatch):
        # Regression: thread.deleteLater left a stale _search_thread → the
        # second in-map search raised RuntimeError and never ran.
        import app.services.geocode_service as gs
        monkeypatch.setattr(
            gs, "geocode",
            lambda q, **k: [{"name": "x", "wgs": {"lat": 30.25, "lon": 122.17}}],
        )
        TileMapWidget = _import_widget()
        w = TileMapWidget()
        w.search_place("舟山")
        assert self._pump_until(lambda: w.marker_lat is not None), "first search no marker"
        w.clear_marker()
        w.search_place("舟山")   # must not raise on deleted QThread
        assert self._pump_until(lambda: w.marker_lat is not None), "second search no marker"

    def test_locate_current_twice_no_crash(self, monkeypatch):
        # Same stale-thread pattern in locate_current (_loc_thread).
        import app.widgets.tile_map_widget as tmw
        monkeypatch.setattr(
            tmw._IpGeoWorker, "run", lambda self: self.done.emit(120.0, 30.0)
        )
        TileMapWidget = _import_widget()
        w = TileMapWidget()
        w.locate_current()
        assert self._pump_until(lambda: w.marker_lat is not None), "first locate no marker"
        w.clear_marker()
        w.locate_current()   # must not raise on deleted QThread
        assert self._pump_until(lambda: w.marker_lat is not None), "second locate no marker"


# ── MapPickDialog integration ────────────────────────────────────────────────

class TestMapPickDialog:
    def test_available_always_true(self):
        from app.widgets.map_pick_dialog import MapPickDialog
        assert MapPickDialog.available() is True

    def test_instantiates_without_error(self):
        from app.widgets.map_pick_dialog import MapPickDialog
        dlg = MapPickDialog()
        assert dlg is not None

    def test_ok_initially_disabled(self):
        from app.widgets.map_pick_dialog import MapPickDialog
        dlg = MapPickDialog()
        assert not dlg._ok.isEnabled()

    def test_ok_enabled_after_marker_moved(self):
        from PyQt6.QtWidgets import QApplication
        from app.widgets.map_pick_dialog import MapPickDialog
        dlg = MapPickDialog()
        dlg._tile_map.set_marker(121.76, 29.11)
        QApplication.processEvents()
        assert dlg._ok.isEnabled()

    def test_picked_signal_emitted_on_accept(self):
        from PyQt6.QtWidgets import QApplication
        from app.widgets.map_pick_dialog import MapPickDialog
        dlg = MapPickDialog()
        received = []
        dlg.picked.connect(lambda lon, lat: received.append((lon, lat)))
        dlg._tile_map.set_marker(50.0, 30.0)
        QApplication.processEvents()
        dlg._confirm()
        assert len(received) == 1
        assert abs(received[0][0] - 50.0) < 1e-6

    def test_search_bar_present(self):
        from app.widgets.map_pick_dialog import MapPickDialog
        dlg = MapPickDialog()
        line_edits = dlg.findChildren(QLineEdit)
        buttons = dlg.findChildren(QPushButton)
        btn_texts = [b.text() for b in buttons]
        assert any("搜索" in t for t in btn_texts), f"No search button. Buttons: {btn_texts}"

    def test_init_coords_set_marker(self):
        from app.widgets.map_pick_dialog import MapPickDialog
        dlg = MapPickDialog(lon="121.76", lat="29.11")
        assert dlg._tile_map.marker_lon is not None
        assert abs(dlg._tile_map.marker_lon - 121.76) < 1e-4

    def test_no_webengine_import_at_module_level(self):
        """Importing map_pick_dialog must not require QtWebEngine."""
        import importlib, sys
        # Remove cached module to force re-import
        mods_to_remove = [k for k in sys.modules if "map_pick_dialog" in k]
        for m in mods_to_remove:
            del sys.modules[m]
        # Block WebEngine import
        import builtins
        real_import = builtins.__import__
        def blocking_import(name, *args, **kwargs):
            if "WebEngine" in name:
                raise ImportError(f"WebEngine blocked: {name}")
            return real_import(name, *args, **kwargs)
        builtins.__import__ = blocking_import
        try:
            from app.widgets import map_pick_dialog  # noqa: F401
        finally:
            builtins.__import__ = real_import


# ── 多点层（采集地图）─────────────────────────────────────────────────────────

def _pts(*lonlat):
    return [{"lon": lo, "lat": la, "label": f"P{i}", "count": i + 1}
            for i, (lo, la) in enumerate(lonlat)]


class TestMultiPointLayer:
    def test_points_initially_empty(self):
        TileMapWidget = _import_widget()
        w = TileMapWidget()
        assert w._points == []

    def test_has_point_clicked_signal(self):
        TileMapWidget = _import_widget()
        w = TileMapWidget()
        assert hasattr(w, "point_clicked")

    def test_interactive_marker_default_true(self):
        TileMapWidget = _import_widget()
        w = TileMapWidget()
        assert w.interactive_marker is True

    def test_set_points_stores_and_fits(self):
        TileMapWidget = _import_widget()
        w = TileMapWidget()
        w.resize(800, 600)
        w.set_points(_pts((121.0, 29.0), (123.0, 31.0)))
        assert len(w._points) == 2
        # 自动 fit：中心落在两点之间
        assert 121.0 <= w._center_lon <= 123.0
        assert 29.0 <= w._center_lat <= 31.0

    def test_clear_points(self):
        TileMapWidget = _import_widget()
        w = TileMapWidget()
        w.set_points(_pts((121.0, 29.0)))
        w.clear_points()
        assert w._points == []

    def test_set_points_empty_does_not_crash(self):
        TileMapWidget = _import_widget()
        w = TileMapWidget()
        w.resize(400, 300)
        w.set_points([])
        assert w._points == []

    def test_paint_with_points_no_crash(self):
        from PyQt6.QtGui import QPixmap, QPainter
        TileMapWidget = _import_widget()
        w = TileMapWidget()
        w.resize(400, 300)
        w.set_points(_pts((121.0, 29.0), (122.0, 30.0)))
        pm = QPixmap(400, 300)
        p = QPainter(pm)
        w.render(pm)   # exercises paintEvent → _draw_points
        p.end()

    def test_click_on_point_emits_index(self):
        from PyQt6.QtCore import QPoint, Qt
        from PyQt6.QtGui import QMouseEvent
        from PyQt6.QtWidgets import QApplication
        TileMapWidget = _import_widget()
        w = TileMapWidget()
        w.interactive_marker = False
        w.resize(800, 600)
        w.set_center(121.0, 29.0, 12)
        w.set_points(_pts((121.0, 29.0)))
        # 点 P0 应投影到屏幕中心附近
        from app.widgets.tile_map_widget import lon_lat_to_pixel
        px, py = lon_lat_to_pixel(121.0, 29.0, w._center_lon, w._center_lat, w._zoom, 800, 600)
        received = []
        w.point_clicked.connect(received.append)
        pos = QPoint(px, py)
        for ev_t in ("press", "release"):
            typ = (QMouseEvent.Type.MouseButtonPress if ev_t == "press"
                   else QMouseEvent.Type.MouseButtonRelease)
            from PyQt6.QtCore import QPointF
            ev = QMouseEvent(typ, QPointF(pos), Qt.MouseButton.LeftButton,
                             Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            (w.mousePressEvent if ev_t == "press" else w.mouseReleaseEvent)(ev)
        QApplication.processEvents()
        assert received == [0]

    def test_click_with_interactive_marker_off_does_not_place_marker(self):
        from PyQt6.QtCore import QPoint, QPointF, Qt
        from PyQt6.QtGui import QMouseEvent
        TileMapWidget = _import_widget()
        w = TileMapWidget()
        w.interactive_marker = False
        w.resize(800, 600)
        # 空白处点击（无点）→ 不放置 marker
        pos = QPointF(QPoint(10, 10))
        for typ in (QMouseEvent.Type.MouseButtonPress, QMouseEvent.Type.MouseButtonRelease):
            ev = QMouseEvent(typ, pos, Qt.MouseButton.LeftButton,
                             Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            (w.mousePressEvent if typ == QMouseEvent.Type.MouseButtonPress
             else w.mouseReleaseEvent)(ev)
        assert w.marker_lon is None   # 未放置

    def test_click_default_still_places_marker(self):
        """interactive_marker 默认 True：无点时点击仍放置 marker（CoordsView 不回归）。"""
        from PyQt6.QtCore import QPoint, QPointF, Qt
        from PyQt6.QtGui import QMouseEvent
        TileMapWidget = _import_widget()
        w = TileMapWidget()
        w.resize(800, 600)
        pos = QPointF(QPoint(400, 300))
        for typ in (QMouseEvent.Type.MouseButtonPress, QMouseEvent.Type.MouseButtonRelease):
            ev = QMouseEvent(typ, pos, Qt.MouseButton.LeftButton,
                             Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            (w.mousePressEvent if typ == QMouseEvent.Type.MouseButtonPress
             else w.mouseReleaseEvent)(ev)
        assert w.marker_lon is not None   # 放置成功
