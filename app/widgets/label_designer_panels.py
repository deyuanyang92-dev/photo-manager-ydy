"""Auxiliary toolbar and layer list widgets for the label designer."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QWidget

from app.widgets import label_designer_support as _ld

# ── Dialog ──────────────────────────────────────────────────────────────────────

class _FloatingToolbar(QWidget):
    """Compact quick-format bar that floats over a selected text/field element.

    Mirrors the web designer's floating toolbar (app.js:16279-16449): font size,
    bold/italic, alignment, colour — applied to the element under the cursor."""

    size_delta = pyqtSignal(int)      # font-size step (±)
    bold_toggled = pyqtSignal()
    italic_toggled = pyqtSignal()
    align_set = pyqtSignal(str)       # "left" | "center" | "right"
    color_pick = pyqtSignal()
    z_delta = pyqtSignal(int)         # raise / lower one layer

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._index = -1
        self.setStyleSheet(
            "background:#13303a; border:1px solid #29b9ab; border-radius:6px;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2); lay.setSpacing(3)

        def _add_format_toolbar_button(text, tooltip, slot, width=26):
            button = _ld._make_designer_button(text)
            button.setToolTip(tooltip)
            button.setFixedWidth(width)
            button.clicked.connect(slot)
            lay.addWidget(button)
            return button
        _add_format_toolbar_button("A−", "缩小字号", lambda: self.size_delta.emit(-1))
        _add_format_toolbar_button("A＋", "放大字号", lambda: self.size_delta.emit(1))
        _add_format_toolbar_button("B", "加粗", self.bold_toggled.emit)
        _add_format_toolbar_button("I", "斜体", self.italic_toggled.emit)
        _add_format_toolbar_button("⇤", "左对齐", lambda: self.align_set.emit("left"))
        _add_format_toolbar_button("⇆", "居中", lambda: self.align_set.emit("center"))
        _add_format_toolbar_button("⇥", "右对齐", lambda: self.align_set.emit("right"))
        _add_format_toolbar_button("🎨", "颜色", self.color_pick.emit)
        _add_format_toolbar_button("↑", "上移一层", lambda: self.z_delta.emit(1))
        _add_format_toolbar_button("↓", "下移一层", lambda: self.z_delta.emit(-1))
        self.hide()

    def target_index(self) -> int:
        return self._index

    def show_for(self, index: int, rect) -> None:
        self._index = index
        self.adjustSize()
        if rect is not None and self.parent() is not None:
            x = max(0, rect.x())
            y = rect.y() - self.height() - 4
            if y < 0:
                y = rect.y() + rect.height() + 4
            self.move(x, y)
        self.show()
        self.raise_()

    def hide_bar(self) -> None:
        self._index = -1
        self.hide()


class LayersPanel(QListWidget):
    """Z-order layer list for the free-form elements (topmost shown first).

    Rows mirror the ``elements`` list reversed (last element = topmost = row 0).
    Click a row to select; per-row 👁/🔒 toggles emit visibility/lock ops.
    Drag-reorder emits a reorder request (wired by the dialog).
    """

    layer_selected = pyqtSignal(int)   # element index
    layer_action = pyqtSignal(dict)    # element_hidden / element_locked op dict
    layer_reordered = pyqtSignal(int, int)  # src element index, dst element index

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._elements: list = []
        self._row_to_index: list = []
        self.setObjectName("LayersPanel")
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.currentRowChanged.connect(self._on_row_changed)
        self.model().rowsMoved.connect(self._on_rows_moved)

    # ── population ────────────────────────────────────────────────────────────
    def set_elements(self, elements: list, sel_index: int = -1) -> None:
        self._elements = list(elements or [])
        n = len(self._elements)
        # topmost first: row r ↔ element index (n-1-r)
        self._row_to_index = [n - 1 - r for r in range(n)]
        self.blockSignals(True)
        self.clear()
        for r, idx in enumerate(self._row_to_index):
            it = QListWidgetItem(self._row_label(self._elements[idx], idx))
            self.addItem(it)
            if idx == sel_index:
                self.setCurrentRow(r)
        self.blockSignals(False)

    @staticmethod
    def _row_label(el: dict, idx: int) -> str:
        et = el.get("type")
        name = _ld.ELEMENT_TYPE_LABELS.get(et, et)
        extra = ""
        if et == "text":
            extra = f"：{(el.get('text') or '')[:8]}"
        elif et == "field":
            extra = f"：{el.get('key') or ''}"
        flags = ""
        if el.get("hidden"):
            flags += " ◌"   # hidden
        if el.get("locked"):
            flags += " 🔒"
        if el.get("group"):
            flags += " ⛓"
        return f"{name}{extra}{flags}"

    def element_index_at_row(self, row: int) -> int:
        if 0 <= row < len(self._row_to_index):
            return self._row_to_index[row]
        return -1

    # ── interactions ──────────────────────────────────────────────────────────
    def _on_row_changed(self, row: int) -> None:
        idx = self.element_index_at_row(row)
        if idx >= 0:
            self.layer_selected.emit(idx)

    def toggle_hidden_at_row(self, row: int) -> None:
        idx = self.element_index_at_row(row)
        if idx < 0:
            return
        cur = bool(self._elements[idx].get("hidden"))
        self.layer_action.emit({"op": "element_hidden", "index": idx,
                                "value": not cur})

    def toggle_locked_at_row(self, row: int) -> None:
        idx = self.element_index_at_row(row)
        if idx < 0:
            return
        cur = bool(self._elements[idx].get("locked"))
        self.layer_action.emit({"op": "element_locked", "index": idx,
                                "value": not cur})

    def _on_rows_moved(self, parent, start, end, dest, drow) -> None:
        src_idx = self.element_index_at_row(start)
        # destination element index in the OLD list coords
        dst_row = drow if drow < start else drow - 1
        dst_idx = self.element_index_at_row(dst_row)
        if src_idx >= 0 and dst_idx >= 0 and src_idx != dst_idx:
            self.layer_reordered.emit(src_idx, dst_idx)

