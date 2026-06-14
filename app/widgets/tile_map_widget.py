"""tile_map_widget.py — native Qt slippy-map widget using OSM tiles.

Replaces QtWebEngine for point picking. Downloads tiles asynchronously via
QNetworkAccessManager; renders via QPainter. Coordinate system: WGS-84 throughout
(OSM tiles are WGS-84 / EPSG:3857 Web Mercator — no GCJ-02 conversion needed).
"""
from __future__ import annotations

import collections
import json
import math
import os
import urllib.parse
import urllib.request
from typing import Optional

from PyQt6.QtCore import (
    QObject,
    QPoint,
    QPointF,
    QRectF,
    Qt,
    QThread,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QFont,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PyQt6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkProxy,
    QNetworkReply,
    QNetworkRequest,
)
from PyQt6.QtWidgets import QLabel, QSizePolicy, QWidget

from app.utils import net_proxy

# ── coordinate math ──────────────────────────────────────────────────────────

_TILE_SIZE = 256
_MIN_ZOOM = 2
_MAX_ZOOM = 18


def clamp_zoom(z: int) -> int:
    return max(_MIN_ZOOM, min(_MAX_ZOOM, z))


def lon_lat_to_tile_xy(lon: float, lat: float, z: int) -> tuple[float, float]:
    n = 2.0 ** z
    lat_r = math.radians(lat)
    tx = (lon + 180.0) / 360.0 * n
    ty = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    return tx, ty


def tile_xy_to_lon_lat(tx: float, ty: float, z: int) -> tuple[float, float]:
    n = 2.0 ** z
    lon = tx / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * ty / n))))
    return lon, lat


def lon_lat_to_pixel(
    lon: float, lat: float,
    center_lon: float, center_lat: float,
    z: int, widget_w: int, widget_h: int,
) -> tuple[int, int]:
    cx, cy = lon_lat_to_tile_xy(center_lon, center_lat, z)
    tx, ty = lon_lat_to_tile_xy(lon, lat, z)
    px = round((tx - cx) * _TILE_SIZE + widget_w / 2)
    py = round((ty - cy) * _TILE_SIZE + widget_h / 2)
    return px, py


def pixel_to_lon_lat(
    px: int, py: int,
    center_lon: float, center_lat: float,
    z: int, widget_w: int, widget_h: int,
) -> tuple[float, float]:
    cx, cy = lon_lat_to_tile_xy(center_lon, center_lat, z)
    tx = cx + (px - widget_w / 2) / _TILE_SIZE
    ty = cy + (py - widget_h / 2) / _TILE_SIZE
    return tile_xy_to_lon_lat(tx, ty, z)


# ── LRU tile cache ────────────────────────────────────────────────────────────

class _TileCache:
    def __init__(self, max_size: int = 200) -> None:
        self._store: collections.OrderedDict = collections.OrderedDict()
        self._max = max_size

    def get(self, key: tuple) -> Optional[QPixmap]:
        if key not in self._store:
            return None
        self._store.move_to_end(key)
        return self._store[key]

    def put(self, key: tuple, pixmap: QPixmap) -> None:
        if key in self._store:
            self._store.move_to_end(key)
        else:
            if len(self._store) >= self._max:
                self._store.popitem(last=False)
        self._store[key] = pixmap


# ── IP geolocation worker ─────────────────────────────────────────────────────

class _IpGeoWorker(QObject):
    done = pyqtSignal(float, float)   # lon, lat
    failed = pyqtSignal()

    _UA = "photo-platform-gui/1.0 (specimen-workbench)"

    def run(self) -> None:
        try:
            req = urllib.request.Request(
                "http://ip-api.com/json",
                headers={"User-Agent": self._UA},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
            if data.get("status") == "success":
                self.done.emit(float(data["lon"]), float(data["lat"]))
            else:
                self.failed.emit()
        except Exception:
            self.failed.emit()


_ORIGINAL_IP_GEO_RUN = _IpGeoWorker.run


# ── TileMapWidget ─────────────────────────────────────────────────────────────

class TileMapWidget(QWidget):
    """Native Qt slippy-map widget. Emits marker_moved(lon, lat) in WGS-84."""

    marker_moved = pyqtSignal(float, float)
    location_failed = pyqtSignal()
    point_clicked = pyqtSignal(int)   # index into the multi-point layer
    zoom_changed = pyqtSignal(int)    # new zoom level

    _TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    _UA = "photo-platform-gui/1.0 (specimen-workbench)"
    _MARKER_COLOR = QColor(41, 185, 171)
    _MARKER_HIT_R = 14  # pixels, hit-test radius
    _POINT_R = 11       # base bubble radius for multi-point layer
    _SHAPE_ALIASES = {
        "圆": "circle", "o": "circle", "circle": "circle",
        "三角": "triangle", "^": "triangle", "triangle": "triangle",
        "方": "square", "s": "square", "square": "square",
        "星": "star", "*": "star", "star": "star",
        "倒三角": "triangle_down", "v": "triangle_down", "triangle_down": "triangle_down",
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._zoom: int = 12
        self._center_lon: float = 121.76
        self._center_lat: float = 29.11
        self._marker_lon: Optional[float] = None
        self._marker_lat: Optional[float] = None

        # multi-point layer (采集地图): list of {lon, lat, label, count, ...}
        self._points: list[dict] = []
        self._point_pix: list[tuple[int, int]] = []  # per-frame screen coords for hit-test
        self._point_style: dict = {}                  # 站位样式（空 = v1 默认外观）
        self.interactive_marker: bool = True          # False → click never places a marker

        self._drag_start: Optional[QPoint] = None
        self._drag_center_start: Optional[tuple[float, float]] = None
        self._marker_drag: bool = False
        self._press_pos: Optional[QPoint] = None

        self._cache = _TileCache(200)
        self._nam = QNetworkAccessManager(self)
        self._pending: dict[tuple, QNetworkReply] = {}
        # 拉取失败（超时/网络不可达）的瓦片 key：跳过重试直到任一瓦片成功，
        # 否则每次 repaint 都会重新发起请求形成 15s 超时风暴。
        self._failed_keys: set[tuple] = set()
        # OSM 直连在大陆被拦 → 后台探测本地 Clash 代理（net_proxy），只应用到
        # 本 widget 的 NAM，绝不全局（全局会劫持局域网协作同步）。
        self._applied_proxy: Optional[str] = None
        self._proxy_retry = QTimer(self)
        self._proxy_retry.setInterval(5000)
        self._proxy_retry.timeout.connect(self._retry_after_failure)
        self._proxy_retry_left = 0
        net_proxy.start_detection()
        self._search_thread: Optional[QThread] = None
        self._search_worker = None
        self._loc_thread: Optional[QThread] = None
        # geocode backend for the in-map search box (injected by the host view)
        self._geo_backend: str = "nominatim"
        self._geo_amap_key: str = ""

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(300, 200)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(False)
        self._build_attribution()

    # ── attribution ──────────────────────────────────────────────────────────

    def _build_attribution(self) -> None:
        self._attr_lbl = QLabel(
            "© OpenStreetMap contributors",
            self,
        )
        self._attr_lbl.setStyleSheet(
            "background: rgba(255,255,255,0.85); color: #333;"
            " font-size: 10px; padding: 1px 4px; border-radius: 2px;"
        )
        self._attr_lbl.adjustSize()
        self._attr_lbl.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._attr_lbl.adjustSize()
        self._attr_lbl.move(
            self.width() - self._attr_lbl.width() - 4,
            self.height() - self._attr_lbl.height() - 4,
        )
        self.update()

    # ── public API ────────────────────────────────────────────────────────────

    def set_center(self, lon: float, lat: float, zoom: int = 12) -> None:
        self._center_lon = lon
        self._center_lat = lat
        self._zoom = clamp_zoom(zoom)
        self.update()

    def set_marker(self, lon: float, lat: float) -> None:
        self._marker_lon = lon
        self._marker_lat = lat
        self.set_center(lon, lat, self._zoom)
        self.marker_moved.emit(lon, lat)

    def clear_marker(self) -> None:
        self._marker_lon = None
        self._marker_lat = None
        self.update()

    # ── multi-point layer (采集地图) ───────────────────────────────────────────

    def set_points(self, points: list[dict]) -> None:
        """Replace the multi-point layer and auto-fit the view to its bounds.

        Each point is a dict with at least ``lon``/``lat``; ``label``/``count``
        are used when drawing. Does not touch the single marker.
        """
        self._points = list(points or [])
        self._fit_points()
        self.update()

    def clear_points(self) -> None:
        self._points = []
        self._point_pix = []
        self.update()

    def zoom_in(self) -> None:
        """Zoom one step in (towards widget center)."""
        new_zoom = clamp_zoom(self._zoom + 1)
        if new_zoom != self._zoom:
            self._zoom = new_zoom
            self._abort_stale_pending()
            self.zoom_changed.emit(self._zoom)
            self.update()

    def zoom_out(self) -> None:
        """Zoom one step out (towards widget center)."""
        new_zoom = clamp_zoom(self._zoom - 1)
        if new_zoom != self._zoom:
            self._zoom = new_zoom
            self._abort_stale_pending()
            self.zoom_changed.emit(self._zoom)
            self.update()

    def zoom_to_fit(self) -> None:
        """Re-fit the view to the current point layer's bounding box."""
        if self._points:
            self._fit_points()
        self._abort_stale_pending()
        self.zoom_changed.emit(self._zoom)
        self.update()

    def current_zoom(self) -> int:
        """Return current integer zoom level."""
        return self._zoom

    def set_zoom(self, value: int) -> None:
        """Set zoom level directly (clamped to [2, 18])."""
        new_zoom = clamp_zoom(value)
        if new_zoom != self._zoom:
            self._zoom = new_zoom
            self._abort_stale_pending()
            self.zoom_changed.emit(self._zoom)
            self.update()

    def set_point_style(self, style: dict) -> None:
        """设置多点层样式（fill/edge/size/show_label/label_source）。空 = 默认外观。"""
        self._point_style = dict(style or {})
        self.update()

    def _fit_points(self) -> None:
        """Centre on the points' centroid; pick a zoom that spans their bbox."""
        if not self._points:
            return
        valid = [p for p in self._points
                 if p.get("lon") is not None and p.get("lat") is not None]
        if not valid:
            return
        lons = [p["lon"] for p in valid]
        lats = [p["lat"] for p in valid]
        self._center_lon = sum(lons) / len(lons)
        self._center_lat = sum(lats) / len(lats)
        span_lon = max(lons) - min(lons)
        span_lat = max(lats) - min(lats)
        span = max(span_lon, span_lat)
        if span <= 0:
            z = 12                       # single point / coincident → city zoom
        else:
            # 360° spans the whole world at z0; halve span per zoom step. Leave
            # ~1.4× margin so edge points aren't clipped.
            z = int(math.floor(math.log2(360.0 / (span * 1.4)))) if span > 0 else 12
        self._zoom = clamp_zoom(z)

    def set_geocode_backend(self, backend: str, amap_key: str = "") -> None:
        """Inject the geocode backend/key used by the in-map search box."""
        self._geo_backend = backend or "nominatim"
        self._geo_amap_key = amap_key or ""

    def search_place(self, query: str) -> None:
        if not query.strip():
            return
        # Quit previous search if still running
        if self._search_thread is not None and self._search_thread.isRunning():
            self._search_thread.quit()
            self._search_thread.wait(500)

        from app.services.geocode_service import GeocodeWorker

        thread = QThread()   # no parent — managed via finished signal
        worker = GeocodeWorker(
            query.strip(), backend=self._geo_backend, amap_key=self._geo_amap_key
        )
        worker.moveToThread(thread)
        # Connect to a *method of self* (main-thread affinity) → queued connection.
        worker.done.connect(self._on_search_results)
        worker.done.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.started.connect(worker.run)
        thread.finished.connect(thread.deleteLater)
        # deleteLater destroys the C++ QThread — clear the refs when it fires,
        # or the next search dies in isRunning() on the zombie wrapper.
        thread.finished.connect(self._on_search_thread_finished)
        self._search_thread = thread
        self._search_worker = worker  # keep alive until thread finishes
        thread.start()

    def _on_search_thread_finished(self) -> None:
        if self.sender() is self._search_thread:
            self._search_thread = None
            self._search_worker = None

    def _on_search_results(self, results: list) -> None:
        if results:
            wgs = results[0]["wgs"]
            self.set_marker(wgs["lon"], wgs["lat"])

    def locate_current(self) -> None:
        """IP 定位当前位置；成功后 set_center + set_marker → marker_moved。"""
        if (
            os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen"
            and _IpGeoWorker.run is _ORIGINAL_IP_GEO_RUN
        ):
            self.location_failed.emit()
            return
        if self._loc_thread is not None and self._loc_thread.isRunning():
            return
        worker = _IpGeoWorker()
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._on_loc_done)
        worker.failed.connect(self.location_failed)
        worker.done.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        # Same zombie-wrapper hazard as search_place — clear refs on finish.
        thread.finished.connect(self._on_loc_thread_finished)
        self._loc_thread = thread
        # keep worker alive until thread finishes
        self._loc_worker = worker
        thread.start()

    def _on_loc_thread_finished(self) -> None:
        if self.sender() is self._loc_thread:
            self._loc_thread = None
            self._loc_worker = None

    def _on_loc_done(self, lon: float, lat: float) -> None:
        self.set_center(lon, lat, zoom=10)
        self.set_marker(lon, lat)

    def stop_threads(self) -> None:
        """Quit + wait any in-flight search/locate threads.

        Call before the widget is destroyed so a finishing worker never calls
        back into freed C++ (same zombie-thread hazard guarded elsewhere).
        """
        for attr in ("_search_thread", "_loc_thread"):
            th = getattr(self, attr, None)
            if th is not None:
                try:
                    if th.isRunning():
                        th.quit()
                        th.wait(800)
                except RuntimeError:
                    pass  # already deleted

    def closeEvent(self, event) -> None:  # noqa: N802
        self.stop_threads()
        super().closeEvent(event)

    @property
    def marker_lon(self) -> Optional[float]:
        return self._marker_lon

    @property
    def marker_lat(self) -> Optional[float]:
        return self._marker_lat

    # ── painting ─────────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self._draw_tiles(painter)
        if self._points:
            self._draw_points(painter)
        if self._marker_lon is not None and self._marker_lat is not None:
            self._draw_marker(painter)
        painter.end()

    def _draw_tiles(self, painter: QPainter) -> None:
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        z = self._zoom
        n = 2 ** z
        cx, cy = lon_lat_to_tile_xy(self._center_lon, self._center_lat, z)

        # tile range visible in the widget
        half_tx = w / (2.0 * _TILE_SIZE)
        half_ty = h / (2.0 * _TILE_SIZE)
        x0 = math.floor(cx - half_tx)
        x1 = math.floor(cx + half_tx)
        y0 = max(0, math.floor(cy - half_ty))
        y1 = min(n - 1, math.floor(cy + half_ty))

        painter.fillRect(0, 0, w, h, QColor("#e0e8e8"))

        any_drawn = False
        for ty_i in range(y0, y1 + 1):
            for tx_i in range(x0, x1 + 1):
                tx_w = tx_i % n  # wrap x
                key = (z, tx_w, ty_i)
                px = round((tx_i - cx) * _TILE_SIZE + w / 2)
                py = round((ty_i - cy) * _TILE_SIZE + h / 2)
                pixmap = self._cache.get(key)
                if pixmap is not None:
                    painter.drawPixmap(px, py, pixmap)
                    any_drawn = True
                else:
                    painter.fillRect(px, py, _TILE_SIZE, _TILE_SIZE, QColor("#d8e4e4"))
                    self._request_tile(z, tx_w, ty_i)

        if self._failed_keys and not any_drawn:
            painter.setPen(QPen(QColor("#6f8585")))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter,
                "地图瓦片加载失败：无法连接 OpenStreetMap\n（直连可能被拦截，请确认代理已开启后平移/缩放重试）",
            )

    def _point_radius(self, count: int) -> int:
        """Bubble radius grows slowly with the aggregated record count."""
        extra = int(min(10, math.log2(count + 1) * 3)) if count > 0 else 0
        return self._POINT_R + extra

    @staticmethod
    def _style_float(style: dict, key: str, default: float) -> float:
        try:
            return float(style.get(key, default))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _style_color(style: dict, key: str, default: QColor) -> QColor:
        col = QColor(style.get(key, ""))
        return col if col.isValid() else QColor(default)

    def _point_visual_radius(self, point: dict) -> int:
        style = self._point_style or {}
        size_scale = self._style_float(style, "size", 80.0) / 80.0 if style else 1.0
        count = int(point.get("count", 1) or 1)
        return max(4, int(self._point_radius(count) * size_scale))

    def _point_shape(self) -> str:
        raw = (self._point_style or {}).get("shape", "圆")
        return self._SHAPE_ALIASES.get(str(raw), "circle")

    def _point_path(self, px: int, py: int, r: int, shape: str) -> QPainterPath:
        path = QPainterPath()
        if shape == "square":
            path.addRoundedRect(QRectF(px - r, py - r, 2 * r, 2 * r), 2, 2)
            return path
        if shape == "triangle":
            path.moveTo(QPointF(px, py - r))
            path.lineTo(QPointF(px + r, py + r))
            path.lineTo(QPointF(px - r, py + r))
            path.closeSubpath()
            return path
        if shape == "triangle_down":
            path.moveTo(QPointF(px, py + r))
            path.lineTo(QPointF(px + r, py - r))
            path.lineTo(QPointF(px - r, py - r))
            path.closeSubpath()
            return path
        if shape == "star":
            for i in range(10):
                ang = -math.pi / 2 + i * math.pi / 5
                rr = r if i % 2 == 0 else r * 0.45
                pt = QPointF(px + math.cos(ang) * rr, py + math.sin(ang) * rr)
                if i == 0:
                    path.moveTo(pt)
                else:
                    path.lineTo(pt)
            path.closeSubpath()
            return path
        path.addEllipse(QPointF(px, py), r, r)
        return path

    def _draw_points(self, painter: QPainter) -> None:
        w, h = self.width(), self.height()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._point_pix = []
        style = self._point_style or {}
        fill = self._style_color(style, "fill", self._MARKER_COLOR)
        edge = self._style_color(style, "edge", QColor(255, 255, 255))
        alpha = max(0.05, min(1.0, self._style_float(style, "alpha", 0.9)))
        fill.setAlphaF(alpha)
        edge.setAlphaF(alpha)
        edge_width = max(0.0, self._style_float(style, "edge_width", 2.0))
        shape = self._point_shape()
        show_label = style.get("show_label", True)   # 默认显示计数 = v1 外观
        label_color = self._style_color(style, "label_color", edge)
        label_size = max(5, int(self._style_float(style, "label_size", 9.0)))
        for p in self._points:
            if p.get("lon") is None or p.get("lat") is None:
                self._point_pix.append((0, 0))
                continue
            px, py = lon_lat_to_pixel(
                p["lon"], p["lat"], self._center_lon, self._center_lat,
                self._zoom, w, h,
            )
            self._point_pix.append((px, py))
            r = self._point_visual_radius(p)
            painter.setBrush(fill)
            painter.setPen(QPen(edge, edge_width))
            painter.drawPath(self._point_path(px, py, r, shape))
            if not show_label:
                continue
            src = style.get("label_source", "count")  # 无样式时默认 count = v1 外观
            if src == "none":
                continue
            from app.services.collection_record_service import marker_label
            txt = marker_label(p, src)
            if txt:
                old_font = painter.font()
                font = QFont(old_font)
                font.setPointSize(label_size)
                painter.setFont(font)
                painter.setPen(QPen(label_color))
                from PyQt6.QtCore import QRect
                painter.drawText(
                    QRect(px - r, py - r, 2 * r, 2 * r),
                    Qt.AlignmentFlag.AlignCenter,
                    txt,
                )
                painter.setFont(old_font)

    def _point_at(self, pos: QPoint) -> Optional[int]:
        """Return the index of the multi-point bubble under *pos*, or None.

        Projects points live (independent of the last paint) so hit-testing
        works even before the first repaint.
        """
        w, h = self.width(), self.height()
        for idx in range(len(self._points) - 1, -1, -1):  # topmost first
            p = self._points[idx]
            px, py = lon_lat_to_pixel(
                p["lon"], p["lat"], self._center_lon, self._center_lat,
                self._zoom, w, h,
            )
            r = self._point_visual_radius(p) + 4
            if math.hypot(pos.x() - px, pos.y() - py) <= r:
                return idx
        return None

    def _draw_marker(self, painter: QPainter) -> None:
        w, h = self.width(), self.height()
        px, py = lon_lat_to_pixel(
            self._marker_lon, self._marker_lat,  # type: ignore[arg-type]
            self._center_lon, self._center_lat,
            self._zoom, w, h,
        )
        # pin shape: circle (centre px, py-18) + downward triangle tip at (px, py)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addEllipse(px - 9, py - 28, 18, 18)
        path.moveTo(px - 6, py - 14)
        path.lineTo(px + 6, py - 14)
        path.lineTo(px, py)
        path.closeSubpath()

        painter.setBrush(self._MARKER_COLOR)
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.drawPath(path)

        # white dot in pin head
        painter.setBrush(QColor(255, 255, 255))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(px - 4, py - 24, 8, 8)

    # ── tile fetching ─────────────────────────────────────────────────────────

    def _request_tile(self, z: int, x: int, y: int) -> None:
        key = (z, x, y)
        if key in self._pending or key in self._failed_keys \
                or self._cache.get(key) is not None:
            return
        self._apply_osm_proxy()
        url = QUrl(self._TILE_URL.format(z=z, x=x, y=y))
        req = QNetworkRequest(url)
        req.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, self._UA)
        # 直连 OSM 不通（如无代理）时请求会无限挂起、key 永远占住 _pending
        req.setTransferTimeout(15000)
        reply = self._nam.get(req)
        self._pending[key] = reply
        reply.finished.connect(lambda r=reply, k=key: self._on_tile_received(k, r))

    def _on_tile_received(self, key: tuple, reply: QNetworkReply) -> None:
        self._pending.pop(key, None)
        if key[0] != self._zoom:          # stale zoom — discard
            reply.deleteLater()
            return
        if reply.error() == QNetworkReply.NetworkError.NoError:
            data = reply.readAll()
            px = QPixmap()
            if px.loadFromData(data):
                self._cache.put(key, px)
                self._failed_keys.clear()   # 网络恢复，允许重试旧失败区域
                self.update()
        else:
            self._failed_keys.add(key)
            # 探测可能晚于首批请求完成 —— 有限次轮询，代理一就绪即自动重试
            if not self._proxy_retry.isActive():
                self._proxy_retry_left = 6
                self._proxy_retry.start()
            self.update()   # 触发重绘以显示失败提示
        reply.deleteLater()

    def _apply_osm_proxy(self) -> None:
        """把 net_proxy 探测到的代理应用到本 widget 的 NAM（幂等）。"""
        proxy = net_proxy.osm_proxy()
        if not proxy or proxy == self._applied_proxy:
            return
        parts = urllib.parse.urlsplit(proxy)
        if not parts.hostname:
            return
        self._nam.setProxy(QNetworkProxy(
            QNetworkProxy.ProxyType.HttpProxy, parts.hostname, parts.port or 80,
        ))
        self._applied_proxy = proxy

    def _retry_after_failure(self) -> None:
        """失败后轮询：代理探测完成 → 清失败标记重绘（重绘即重新请求）。"""
        self._proxy_retry_left -= 1
        proxy = net_proxy.osm_proxy()
        if proxy and proxy != self._applied_proxy:
            self._proxy_retry.stop()
            self._failed_keys.clear()
            self.update()
        elif self._proxy_retry_left <= 0 or not self._failed_keys:
            self._proxy_retry.stop()

    def _abort_stale_pending(self) -> None:
        stale = [(k, r) for k, r in list(self._pending.items()) if k[0] != self._zoom]
        for k, r in stale:
            r.abort()
            self._pending.pop(k, None)

    # ── mouse interaction ─────────────────────────────────────────────────────

    def _marker_pixel(self) -> Optional[tuple[int, int]]:
        if self._marker_lon is None:
            return None
        return lon_lat_to_pixel(
            self._marker_lon, self._marker_lat,  # type: ignore[arg-type]
            self._center_lon, self._center_lat,
            self._zoom, self.width(), self.height(),
        )

    def _near_marker(self, pos: QPoint) -> bool:
        mp = self._marker_pixel()
        if mp is None:
            return False
        dx, dy = pos.x() - mp[0], pos.y() - (mp[1] - 14)  # hit centre of pin head
        return math.hypot(dx, dy) <= self._MARKER_HIT_R

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._press_pos = event.pos()
        if self._near_marker(event.pos()):
            self._marker_drag = True
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self._drag_start = event.pos()
            self._drag_center_start = (self._center_lon, self._center_lat)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._marker_drag and self._marker_lon is not None:
            lon, lat = pixel_to_lon_lat(
                event.pos().x(), event.pos().y(),
                self._center_lon, self._center_lat,
                self._zoom, self.width(), self.height(),
            )
            self._marker_lon = lon
            self._marker_lat = lat
            self.marker_moved.emit(lon, lat)
            self.update()
        elif self._drag_start is not None and self._drag_center_start is not None:
            delta = event.pos() - self._drag_start
            clon, clat = self._drag_center_start
            cx, cy = lon_lat_to_tile_xy(clon, clat, self._zoom)
            new_cx = cx - delta.x() / _TILE_SIZE
            new_cy = cy - delta.y() / _TILE_SIZE
            n = 2 ** self._zoom
            new_cy = max(0.0, min(float(n), new_cy))
            self._center_lon, self._center_lat = tile_xy_to_lon_lat(new_cx, new_cy, self._zoom)
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        was_marker_drag = self._marker_drag
        self._marker_drag = False
        self.setCursor(Qt.CursorShape.CrossCursor)

        if not was_marker_drag and self._press_pos is not None:
            delta = event.pos() - self._press_pos
            if math.hypot(delta.x(), delta.y()) < 4:
                # multi-point layer takes priority: a click on a bubble selects it
                idx = self._point_at(event.pos()) if self._points else None
                if idx is not None:
                    self.point_clicked.emit(idx)
                elif self.interactive_marker:
                    self._place_marker_at(event.pos())

        self._drag_start = None
        self._drag_center_start = None
        self._press_pos = None

    def _place_marker_at(self, pos: QPoint) -> None:
        lon, lat = pixel_to_lon_lat(
            pos.x(), pos.y(),
            self._center_lon, self._center_lat,
            self._zoom, self.width(), self.height(),
        )
        self._marker_lon = lon
        self._marker_lat = lat
        self.marker_moved.emit(lon, lat)
        self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        new_zoom = clamp_zoom(self._zoom + (1 if delta > 0 else -1))
        if new_zoom != self._zoom:
            # zoom toward cursor position
            mx, my = event.position().x(), event.position().y()
            lon, lat = pixel_to_lon_lat(
                int(mx), int(my),
                self._center_lon, self._center_lat,
                self._zoom, self.width(), self.height(),
            )
            self._zoom = new_zoom
            # re-centre so the point under cursor stays fixed
            px, py = lon_lat_to_pixel(
                lon, lat,
                self._center_lon, self._center_lat,
                self._zoom, self.width(), self.height(),
            )
            cx, cy = lon_lat_to_tile_xy(self._center_lon, self._center_lat, self._zoom)
            cx += (px - mx) / _TILE_SIZE
            cy += (py - my) / _TILE_SIZE
            self._center_lon, self._center_lat = tile_xy_to_lon_lat(cx, cy, self._zoom)
            self._abort_stale_pending()
            self.zoom_changed.emit(self._zoom)
            self.update()
        event.accept()  # prevent QScrollArea from consuming the wheel event
