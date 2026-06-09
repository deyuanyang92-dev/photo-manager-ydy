"""marker_style_panel.py — 采集地图站位标识样式面板.

调整站位点的外观：形状 / 大小 / 填充色 / 描边色·宽 / 透明度 / 标签开关·来源·字号。
任意改动发 `style_changed(dict)`，由视图喂给 PublicationMapWidget / TileMapWidget 并持久化
（project_settings key `map_marker_style`）。纯 UI，无持久化副作用，便于单测。

样式 dict 字段与 publication_map_widget._DEFAULT_STYLE 对齐。
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

_DEFAULT = {
    "shape": "圆", "size": 80, "fill": "#29b9ab", "edge": "#ffffff",
    "edge_width": 1.2, "alpha": 0.9,
    "show_label": False, "label_source": "label", "label_size": 9,
    "label_color": "#17212b",
}

_SHAPES = ["圆", "三角", "方", "星", "倒三角"]
_LABEL_SOURCES = [("label", "名称/站位"), ("count", "记录数"), ("none", "无")]


class MarkerStylePanel(QWidget):
    """站位标识样式编辑器。"""

    style_changed = pyqtSignal(dict)

    def __init__(self, initial: Optional[dict] = None, parent=None) -> None:
        super().__init__(parent)
        self._style = {**_DEFAULT, **(initial or {})}
        self._build()
        self._load_into_controls()

    # ── UI ──────────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        from app.widgets._form_row import form_row

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(7)

        _LW = 52  # 右对齐定宽标签（窄卡内对齐，足够「描边宽/不透明」三字）

        def row(label, w, help_text=None):
            if isinstance(w, (QSpinBox, QDoubleSpinBox, QComboBox, QPushButton)):
                w.setFixedHeight(28)
            v.addWidget(form_row(label, w, help_text=help_text, label_width=_LW))

        self._shape = QComboBox()
        self._shape.addItems(_SHAPES)
        self._shape.currentIndexChanged.connect(self._on_change)
        row("形状", self._shape)

        self._size = QSpinBox()
        self._size.setRange(8, 600)
        self._size.valueChanged.connect(self._on_change)
        row("大小", self._size, help_text="站位点直径（地图像素）")

        self._fill_btn = QPushButton()
        self._fill_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._fill_btn.clicked.connect(lambda: self._pick_color("fill", self._fill_btn))
        row("填充", self._fill_btn)

        self._edge_btn = QPushButton()
        self._edge_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._edge_btn.clicked.connect(lambda: self._pick_color("edge", self._edge_btn))
        row("描边", self._edge_btn)

        self._edge_w = QDoubleSpinBox()
        self._edge_w.setRange(0.0, 6.0)
        self._edge_w.setSingleStep(0.2)
        self._edge_w.valueChanged.connect(self._on_change)
        row("描边宽", self._edge_w)

        self._alpha = QDoubleSpinBox()
        self._alpha.setRange(0.1, 1.0)
        self._alpha.setSingleStep(0.05)
        self._alpha.valueChanged.connect(self._on_change)
        row("不透明", self._alpha)

        self._show_label = QCheckBox("显示标签")
        self._show_label.toggled.connect(self._on_change)
        v.addWidget(self._show_label)

        self._label_source = QComboBox()
        for key, txt in _LABEL_SOURCES:
            self._label_source.addItem(txt, key)
        self._label_source.currentIndexChanged.connect(self._on_change)
        row("标签", self._label_source)

        self._label_size = QSpinBox()
        self._label_size.setRange(5, 40)
        self._label_size.valueChanged.connect(self._on_change)
        row("字号", self._label_size)

    # ── 状态同步 ──────────────────────────────────────────────────────────────

    def _load_into_controls(self) -> None:
        self._block(True)
        s = self._style
        self._shape.setCurrentText(s["shape"] if s["shape"] in _SHAPES else "圆")
        self._size.setValue(int(s["size"]))
        self._edge_w.setValue(float(s["edge_width"]))
        self._alpha.setValue(float(s["alpha"]))
        self._show_label.setChecked(bool(s["show_label"]))
        idx = self._label_source.findData(s["label_source"])
        self._label_source.setCurrentIndex(idx if idx >= 0 else 0)
        self._label_size.setValue(int(s["label_size"]))
        self._paint_btn(self._fill_btn, s["fill"])
        self._paint_btn(self._edge_btn, s["edge"])
        self._block(False)

    def _block(self, on: bool) -> None:
        for w in (self._shape, self._size, self._edge_w, self._alpha,
                  self._show_label, self._label_source, self._label_size):
            w.blockSignals(on)

    def _paint_btn(self, btn: QPushButton, color: str) -> None:
        btn.setText(color)
        btn.setStyleSheet(
            f"background:{color};color:{self._contrast(color)};"
            f"border:1px solid rgba(0,0,0,0.2);border-radius:4px;padding:3px 8px;"
        )

    @staticmethod
    def _contrast(hex_color: str) -> str:
        c = QColor(hex_color)
        lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
        return "#000000" if lum > 140 else "#ffffff"

    # ── 事件 ──────────────────────────────────────────────────────────────────

    def _pick_color(self, key: str, btn: QPushButton) -> None:
        cur = QColor(self._style.get(key, "#000000"))
        col = QColorDialog.getColor(
            cur, self, "选择颜色",
            QColorDialog.ColorDialogOption.DontUseNativeDialog,
        )
        if col.isValid():
            self._style[key] = col.name()
            self._paint_btn(btn, col.name())
            self._emit()

    def _on_change(self, *_a) -> None:
        self._collect()
        self._emit()

    def _collect(self) -> None:
        self._style.update({
            "shape": self._shape.currentText(),
            "size": self._size.value(),
            "edge_width": self._edge_w.value(),
            "alpha": self._alpha.value(),
            "show_label": self._show_label.isChecked(),
            "label_source": self._label_source.currentData(),
            "label_size": self._label_size.value(),
        })

    def _emit(self) -> None:
        self.style_changed.emit(dict(self._style))

    # ── 公共 API ──────────────────────────────────────────────────────────────

    def style(self) -> dict:
        self._collect()
        return dict(self._style)

    def set_style(self, style: dict) -> None:
        self._style = {**_DEFAULT, **(style or {})}
        self._load_into_controls()
