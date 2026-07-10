"""Read-only mini map for survey overview (station pins)."""
from __future__ import annotations

from typing import Any, Optional

from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from app.widgets.tile_map_widget import TileMapWidget


class SurveyOverviewMiniMap(QWidget):
    """Compact OSM map showing station/site distribution."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        compact: bool = False,
    ) -> None:
        super().__init__(parent)
        self._compact = compact
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        if not compact:
            title = QLabel("站位分布")
            title.setObjectName("MutedSmall")
            title.setStyleSheet("font-weight:600;")
            lay.addWidget(title)
        self._map = TileMapWidget(self)
        self._map.setMinimumHeight(160 if compact else 180)
        self._map.setMaximumHeight(220 if compact else 240)
        self._map.interactive_marker = False
        self._map.set_point_style({
            "fill": "#2e7d32",
            "stroke": "#ffffff",
            "radius": 7,
            "label_src": "label",
        })
        lay.addWidget(self._map)
        self._empty = QLabel("暂无坐标 — 可在采集记录中填写站位经纬度")
        self._empty.setObjectName("MutedSmall")
        self._empty.setWordWrap(True)
        self._empty.hide()
        lay.addWidget(self._empty)

    def set_points(self, points: list[dict[str, Any]]) -> None:
        pts = list(points or [])
        if not pts:
            self._map.hide()
            self._empty.show()
            self._map.clear_points()
            return
        self._empty.hide()
        self._map.show()
        self._map.set_points(pts)
