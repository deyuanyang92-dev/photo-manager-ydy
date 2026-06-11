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
    QLineEdit,
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
# 标签来源与 collection_record_service.MARKER_LABEL_SOURCES 对齐（站位/断面/地区/经纬度…）。
from app.services.collection_record_service import MARKER_LABEL_SOURCES as _LABEL_SOURCES


class MarkerStylePanel(QWidget):
    """站位标识样式编辑器。"""

    style_changed = pyqtSignal(dict)

    def __init__(self, initial: Optional[dict] = None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("MarkerStylePanel")
        self._style = {**_DEFAULT, **(initial or {})}
        self._build()
        self._load_into_controls()

    # ── UI ──────────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        from app.widgets._form_row import form_row

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        _LW = 56  # 右对齐定宽标签（窄卡内对齐，足够「描边宽/不透明」三字）

        def section(text):
            # 段标题前留出气口，与上一段拉开，读起来不挤（首段无上间距）。
            if v.count():
                v.addSpacing(4)
            lbl = QLabel(text)
            lbl.setObjectName("StyleSection")
            v.addWidget(lbl)

        def row(label, w, help_text=None):
            if isinstance(w, (QSpinBox, QDoubleSpinBox, QComboBox, QPushButton)):
                w.setFixedHeight(30)
            fr = form_row(label, w, help_text=help_text, label_width=_LW)
            v.addWidget(fr)
            return fr

        section("外观")

        self._shape = QComboBox()
        self._shape.addItems(_SHAPES)
        self._shape.currentIndexChanged.connect(self._on_change)
        row("形状", self._shape)

        self._size = QSpinBox()
        self._size.setRange(8, 600)
        self._size.valueChanged.connect(self._on_change)
        row("大小", self._size, help_text="站位点直径（地图像素）")

        fill_color, self._fill_edit, self._fill_btn = self._color_input("fill")
        row("填充", fill_color)

        edge_color, self._edge_edit, self._edge_btn = self._color_input("edge")
        row("描边", edge_color)

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

        section("标签")

        self._show_label = QCheckBox("显示标签")
        self._show_label.toggled.connect(self._on_change)
        v.addWidget(self._show_label)

        self._label_source = QComboBox()
        for key, txt in _LABEL_SOURCES:
            self._label_source.addItem(txt, key)
        self._label_source.currentIndexChanged.connect(self._on_change)
        self._row_label_src = row("字段", self._label_source, help_text="标签显示哪个字段")

        self._label_size = QSpinBox()
        self._label_size.setRange(5, 40)
        self._label_size.valueChanged.connect(self._on_change)
        self._row_label_size = row("字号", self._label_size)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 2, 0, 0)
        footer.addStretch(1)
        self._reset_btn = QPushButton("恢复默认")
        self._reset_btn.setObjectName("Ghost")
        self._reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reset_btn.setToolTip("恢复默认站位标识样式")
        self._reset_btn.clicked.connect(self.reset_style)
        footer.addWidget(self._reset_btn)
        v.addLayout(footer)

    def _color_input(self, key: str) -> tuple[QWidget, QLineEdit, QPushButton]:
        """Hex input + swatch button. Editing the text is faster than opening a dialog."""
        wrap = QWidget()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)

        edit = QLineEdit()
        edit.setFixedHeight(30)
        edit.setClearButtonEnabled(True)
        edit.setPlaceholderText("#29b9ab" if key == "fill" else "#ffffff")
        edit.setToolTip("输入颜色，例如 #29b9ab；也可以点右侧色块选择")
        edit.editingFinished.connect(lambda k=key: self._on_color_edited(k))
        h.addWidget(edit, 1)

        btn = QPushButton()
        btn.setFixedSize(36, 30)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip("选择颜色")
        btn.clicked.connect(lambda _=False, k=key: self._pick_color(k))
        h.addWidget(btn)

        # Keep the widget visually flat inside form_row; the line edit and swatch
        # provide the actual controls.
        wrap.setObjectName(f"{key.title()}ColorInput")
        return wrap, edit, btn

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
        self._paint_color("fill", s["fill"])
        self._paint_color("edge", s["edge"])
        self._block(False)
        self._sync_label_enabled()

    def _sync_label_enabled(self) -> None:
        """未勾「显示标签」→ 灰掉字段/字号行，视觉上分清主次。"""
        on = self._show_label.isChecked()
        self._row_label_src.setEnabled(on)
        self._row_label_size.setEnabled(on)

    def _block(self, on: bool) -> None:
        for w in (self._shape, self._size, self._edge_w, self._alpha,
                  self._show_label, self._label_source, self._label_size,
                  self._fill_edit, self._edge_edit):
            w.blockSignals(on)

    def _paint_color(self, key: str, color: str) -> None:
        edit = self._fill_edit if key == "fill" else self._edge_edit
        btn = self._fill_btn if key == "fill" else self._edge_btn
        color = self._normal_color(color, _DEFAULT[key])
        edit.setText(color)
        edit.setProperty("invalid", False)
        edit.style().unpolish(edit)
        edit.style().polish(edit)
        btn.setText("")
        btn.setStyleSheet(
            f"background:{color};"
            f"border:1px solid rgba(0,0,0,0.25);border-radius:4px;padding:0;"
        )

    @staticmethod
    def _normal_color(value: str, fallback: str) -> str:
        col = QColor((value or "").strip())
        return col.name() if col.isValid() else fallback

    # ── 事件 ──────────────────────────────────────────────────────────────────

    def _pick_color(self, key: str) -> None:
        cur = QColor(self._style.get(key, "#000000"))
        col = QColorDialog.getColor(
            cur, self, "选择颜色",
            QColorDialog.ColorDialogOption.DontUseNativeDialog,
        )
        if col.isValid():
            self._style[key] = col.name()
            self._paint_color(key, col.name())
            self._emit()

    def _on_color_edited(self, key: str) -> None:
        edit = self._fill_edit if key == "fill" else self._edge_edit
        raw = edit.text().strip()
        col = QColor(raw)
        if not col.isValid():
            edit.setProperty("invalid", True)
            edit.style().unpolish(edit)
            edit.style().polish(edit)
            return
        self._style[key] = col.name()
        self._paint_color(key, col.name())
        self._emit()

    def _on_change(self, *_a) -> None:
        self._collect()
        self._sync_label_enabled()
        self._emit()

    def _collect(self) -> None:
        self._style.update({
            "shape": self._shape.currentText(),
            "size": self._size.value(),
            "fill": self._normal_color(
                self._fill_edit.text(), self._style.get("fill", _DEFAULT["fill"])
            ),
            "edge": self._normal_color(
                self._edge_edit.text(), self._style.get("edge", _DEFAULT["edge"])
            ),
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

    def reset_style(self) -> None:
        self.set_style(dict(_DEFAULT))
        self._emit()
