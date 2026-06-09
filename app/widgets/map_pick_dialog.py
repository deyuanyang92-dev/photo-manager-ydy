"""map_pick_dialog.py — modal map point-picker for the metadata card.

Uses the native TileMapWidget (OSM tiles via QNetworkAccessManager) — no
QtWebEngine required. The user clicks / drags a marker; on 确定 the dialog
emits ``picked(lon, lat)`` in WGS-84.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from app.widgets.tile_map_widget import TileMapWidget


class MapPickDialog(QDialog):
    """Pick a collection point on the map → ``picked(lon, lat)`` (WGS-84)."""

    picked = pyqtSignal(float, float)  # (lon, lat)

    def __init__(self, parent=None, *, lon: str = "", lat: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("地图选点")
        self.setModal(True)
        self.resize(720, 560)
        self._sel: Optional[dict] = None
        self._init_lon = (lon or "").strip()
        self._init_lat = (lat or "").strip()
        self._build_ui()

    @staticmethod
    def available() -> bool:
        return True

    # ── UI ──────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        # search bar
        search_row = QHBoxLayout()
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("搜索地名，如：杭州西湖")
        self._search_btn = QPushButton("搜索")
        self._search_btn.setObjectName("Outline")
        self._search_btn.clicked.connect(self._do_search)
        self._search_edit.returnPressed.connect(self._do_search)
        self._locate_btn = QPushButton("📍当前位置")
        self._locate_btn.setObjectName("Outline")
        self._locate_btn.clicked.connect(self._do_locate)
        search_row.addWidget(self._search_edit, 1)
        search_row.addWidget(self._search_btn)
        search_row.addWidget(self._locate_btn)
        lay.addLayout(search_row)

        # tile map
        self._tile_map = TileMapWidget()
        self._tile_map.marker_moved.connect(self._on_marker_moved)
        self._tile_map.location_failed.connect(self._on_locate_failed)
        lay.addWidget(self._tile_map, 1)

        # status label
        self._coord_lbl = QLabel("点击地图或拖拽标记选点")
        self._coord_lbl.setObjectName("MutedSmall")
        lay.addWidget(self._coord_lbl)

        # buttons (must exist before set_marker triggers _on_marker_moved → _ok)
        foot = QHBoxLayout()
        foot.addStretch()
        cancel = QPushButton("取消")
        cancel.setObjectName("Outline")
        cancel.clicked.connect(self.reject)
        self._ok = QPushButton("确定")
        self._ok.setObjectName("Primary")
        self._ok.setEnabled(False)
        self._ok.clicked.connect(self._confirm)
        foot.addWidget(cancel)
        foot.addWidget(self._ok)
        lay.addLayout(foot)

        # set initial marker after all widgets created
        try:
            if self._init_lon and self._init_lat:
                lon_f = float(self._init_lon)
                lat_f = float(self._init_lat)
                if (-180 <= lon_f <= 180) and (-90 <= lat_f <= 90):
                    self._tile_map.set_marker(lon_f, lat_f)
        except ValueError:
            pass

    def _do_search(self) -> None:
        q = self._search_edit.text().strip()
        if q:
            self._tile_map.search_place(q)

    def _do_locate(self) -> None:
        self._coord_lbl.setText("定位中…")
        self._locate_btn.setEnabled(False)
        self._tile_map.locate_current()

    def _on_locate_failed(self) -> None:
        self._coord_lbl.setText("定位失败，请手动选点或搜索")
        self._locate_btn.setEnabled(True)

    def _on_marker_moved(self, lon: float, lat: float) -> None:
        self._sel = {"lon": lon, "lat": lat}
        self._coord_lbl.setText(f"已选：WGS-84 {lat:.6f}, {lon:.6f}")
        self._ok.setEnabled(True)
        self._locate_btn.setEnabled(True)

    def _confirm(self) -> None:
        if self._sel:
            self.picked.emit(self._sel["lon"], self._sel["lat"])
        self.accept()
