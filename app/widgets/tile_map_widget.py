"""tile_map_widget.py — native Qt slippy-map widget using OSM tiles.

Replaces QtWebEngine for point picking. Downloads tiles asynchronously via
QNetworkAccessManager; renders via QPainter. Coordinate system: WGS-84 throughout
(OSM tiles are WGS-84 / EPSG:3857 Web Mercator — no GCJ-02 conversion needed).
"""
from __future__ import annotations

import collections
import json
import math
import urllib.parse
import urllib.request
from typing import Optional

from PyQt6.QtCore import (
    QObject,
    QPoint,
    Qt,
    QThread,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import QLabel, QSizePolicy, QWidget

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


# ── Nominatim search worker ───────────────────────────────────────────────────

class _NominatimWorker(QObject):
    done = pyqtSignal(float, float)
    failed = pyqtSignal()

    _UA = "photo-platform-gui/1.0 (specimen-workbench)"

    def __init__(self, query: str) -> None:
        super().__init__()
        self._q = query

    def run(self) -> None:
        try:
            url = (
                "https://nominatim.openstreetmap.org/search?"
                + urllib.parse.urlencode({
                    "q": self._q,
                    "format": "json",
                    "limit": 1,
                    "accept-language": "zh",
                })
            )
            req = urllib.request.Request(url, headers={"User-Agent": self._UA})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
            if data:
                self.done.emit(float(data[0]["lon"]), float(data[0]["lat"]))
            else:
                self.failed.emit()
        except Exception:
            self.failed.emit()


# ── TileMapWidget ─────────────────────────────────────────────────────────────

class TileMapWidget(QWidget):
    """Native Qt slippy-map widget. Emits marker_moved(lon, lat) in WGS-84."""

    marker_moved = pyqtSignal(float, float)

    _TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    _UA = "photo-platform-gui/1.0 (specimen-workbench)"
    _MARKER_COLOR = QColor(41, 185, 171)
    _MARKER_HIT_R = 14  # pixels, hit-test radius

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._zoom: int = 12
        self._center_lon: float = 121.76
        self._center_lat: float = 29.11
        self._marker_lon: Optional[float] = None
        self._marker_lat: Optional[float] = None

        self._drag_start: Optional[QPoint] = None
        self._drag_center_start: Optional[tuple[float, float]] = None
        self._marker_drag: bool = False
        self._press_pos: Optional[QPoint] = None

        self._cache = _TileCache(200)
        self._nam = QNetworkAccessManager(self)
        self._pending: dict[tuple, QNetworkReply] = {}
        self._search_thread: Optional[QThread] = None

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

    def search_place(self, query: str) -> None:
        if not query.strip():
            return
        # Quit previous search if still running
        if self._search_thread is not None and self._search_thread.isRunning():
            self._search_thread.quit()
            self._search_thread.wait(500)

        thread = QThread()   # no parent — managed via finished signal
        worker = _NominatimWorker(query.strip())
        worker.moveToThread(thread)

        def _on_done(lon: float, lat: float) -> None:
            self.set_marker(lon, lat)
            thread.quit()

        worker.done.connect(_on_done)
        worker.failed.connect(thread.quit)
        thread.started.connect(worker.run)
        thread.finished.connect(thread.deleteLater)
        self._search_thread = thread
        thread.start()

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

        for ty_i in range(y0, y1 + 1):
            for tx_i in range(x0, x1 + 1):
                tx_w = tx_i % n  # wrap x
                key = (z, tx_w, ty_i)
                px = round((tx_i - cx) * _TILE_SIZE + w / 2)
                py = round((ty_i - cy) * _TILE_SIZE + h / 2)
                pixmap = self._cache.get(key)
                if pixmap is not None:
                    painter.drawPixmap(px, py, pixmap)
                else:
                    painter.fillRect(px, py, _TILE_SIZE, _TILE_SIZE, QColor("#d8e4e4"))
                    self._request_tile(z, tx_w, ty_i)

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
        if key in self._pending or self._cache.get(key) is not None:
            return
        url = QUrl(self._TILE_URL.format(z=z, x=x, y=y))
        req = QNetworkRequest(url)
        req.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, self._UA)
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
                self.update()
        reply.deleteLater()

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
            self.update()
