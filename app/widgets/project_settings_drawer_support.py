"""Support widgets and small layout helpers for ProjectSettingsDrawer."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

# ── collapsible section helper ────────────────────────────────────────────────

class _CollapsibleSection(QWidget):
    """A disclosure-style section: click header to show/hide body."""

    def __init__(self, title: str, collapsed: bool = True,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        self._toggle = QPushButton(f"▸ {title}" if collapsed else f"▾ {title}")
        self._toggle.setObjectName("Ghost")
        self._toggle.setFixedHeight(28)
        self._toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle.setStyleSheet("text-align: left; padding-left: 2px;")
        root.addWidget(self._toggle)

        self._body = QWidget()
        body_lay = QVBoxLayout(self._body)
        body_lay.setContentsMargins(8, 0, 0, 0)
        body_lay.setSpacing(6)
        self._body_lay = body_lay
        self._body.setVisible(not collapsed)
        root.addWidget(self._body)

        self._collapsed = collapsed
        self._title = title
        self._toggle.clicked.connect(self._on_toggle)

    def _on_toggle(self) -> None:
        self._collapsed = not self._collapsed
        self._body.setVisible(not self._collapsed)
        self._toggle.setText(
            f"▸ {self._title}" if self._collapsed else f"▾ {self._title}"
        )

    def body_layout(self) -> QVBoxLayout:
        return self._body_lay


# ── inline KV dict editor ──────────────────────────────────────────────────────

class _KVEditor(QWidget):
    """Editable list of key→value pairs (for stations / species in 命名规则)."""

    changed = pyqtSignal()

    def __init__(self, key_placeholder: str = "缩写", val_placeholder: str = "中文说明",
                 force_upper: bool = True, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._key_ph = key_placeholder
        self._val_ph = val_placeholder
        self._force_upper = force_upper
        self._rows: list[tuple[QLineEdit, QLineEdit]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        self._rows_widget = QWidget()
        self._rows_lay = QVBoxLayout(self._rows_widget)
        self._rows_lay.setContentsMargins(0, 0, 0, 0)
        self._rows_lay.setSpacing(4)
        root.addWidget(self._rows_widget)

        add_btn = QPushButton("+ 添加")
        add_btn.setObjectName("Ghost")
        add_btn.setFixedHeight(26)
        add_btn.clicked.connect(self._add_key_value_row)
        root.addWidget(add_btn)

    def load_entries(self, data: dict[str, str]) -> None:
        self._clear_rows()
        for k, v in data.items():
            self._add_key_value_row(k, v)

    def entries(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for k_edit, v_edit in self._rows:
            k = k_edit.text().strip()
            if k:
                result[k] = v_edit.text().strip()
        return result

    def _clear_rows(self) -> None:
        while self._rows_lay.count():
            item = self._rows_lay.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self._rows.clear()

    def _add_key_value_row(self, key: str = "", val: str = "") -> None:
        row_w = QWidget()
        h = QHBoxLayout(row_w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)

        k_edit = QLineEdit(key)
        k_edit.setPlaceholderText(self._key_ph)
        k_edit.setFixedWidth(72)
        k_edit.setFixedHeight(28)
        if self._force_upper:
            k_edit.textEdited.connect(lambda t, e=k_edit: e.setText(t.upper()))
        k_edit.editingFinished.connect(self.changed.emit)

        arrow = QLabel("→")
        arrow.setObjectName("Muted")
        arrow.setFixedWidth(16)
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)

        v_edit = QLineEdit(val)
        v_edit.setPlaceholderText(self._val_ph)
        v_edit.setFixedHeight(28)
        v_edit.editingFinished.connect(self.changed.emit)

        del_btn = QPushButton("×")
        del_btn.setObjectName("Ghost")
        del_btn.setFixedSize(24, 28)
        del_btn.clicked.connect(lambda: self._remove_row(row_w, k_edit, v_edit))

        h.addWidget(k_edit)
        h.addWidget(arrow)
        h.addWidget(v_edit, 1)
        h.addWidget(del_btn)

        self._rows.append((k_edit, v_edit))
        self._rows_lay.addWidget(row_w)

    def _remove_row(self, row_w: QWidget, k_edit: QLineEdit, v_edit: QLineEdit) -> None:
        if (k_edit, v_edit) in self._rows:
            self._rows.remove((k_edit, v_edit))
        row_w.deleteLater()
        self.changed.emit()


class _CustomFieldEditor(QWidget):
    """Editable project custom naming fields."""

    changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._rows: list[tuple[QLineEdit, QLineEdit]] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        self._rows_widget = QWidget()
        self._rows_lay = QVBoxLayout(self._rows_widget)
        self._rows_lay.setContentsMargins(0, 0, 0, 0)
        self._rows_lay.setSpacing(4)
        root.addWidget(self._rows_widget)

        add_btn = QPushButton("+ 添加字段")
        add_btn.setObjectName("Ghost")
        add_btn.setFixedHeight(26)
        add_btn.setToolTip("添加可参与编号的项目字段，例如 水深、潮位、天气。")
        add_btn.clicked.connect(lambda _checked=False: self._add_row())
        root.addWidget(add_btn)

    def load_fields(self, fields: object) -> None:
        from app.services.naming_field_catalog import normalize_custom_fields

        self._clear_rows()
        for item in normalize_custom_fields(fields):
            self._add_row(item.get("label", ""), item.get("key", ""))

    def fields(self) -> list[dict[str, str]]:
        from app.services.naming_field_catalog import normalize_custom_fields

        return normalize_custom_fields([
            {"label": label.text().strip(), "key": key.text().strip()}
            for label, key in self._rows
        ])

    def _clear_rows(self) -> None:
        while self._rows_lay.count():
            item = self._rows_lay.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self._rows.clear()

    def _add_row(self, label: str = "", key: str = "") -> None:
        row_w = QWidget()
        h = QHBoxLayout(row_w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)

        label_edit = QLineEdit(label)
        label_edit.setPlaceholderText("字段名，如 水深")
        label_edit.setFixedHeight(28)
        label_edit.editingFinished.connect(self.changed.emit)

        key_edit = QLineEdit(key)
        key_edit.setPlaceholderText("key 自动")
        key_edit.setFixedWidth(100)
        key_edit.setFixedHeight(28)
        key_edit.editingFinished.connect(self.changed.emit)

        del_btn = QPushButton("×")
        del_btn.setObjectName("Ghost")
        del_btn.setFixedSize(24, 28)
        del_btn.clicked.connect(lambda: self._remove_row(row_w, label_edit, key_edit))

        h.addWidget(label_edit, 1)
        h.addWidget(key_edit)
        h.addWidget(del_btn)

        self._rows.append((label_edit, key_edit))
        self._rows_lay.addWidget(row_w)

    def _remove_row(self, row_w: QWidget, label_edit: QLineEdit, key_edit: QLineEdit) -> None:
        if (label_edit, key_edit) in self._rows:
            self._rows.remove((label_edit, key_edit))
        row_w.deleteLater()
        self.changed.emit()




# ── helpers ───────────────────────────────────────────────────────────────────

def _scrollable_tab() -> tuple[QWidget, QVBoxLayout]:
    """Return (outer widget, inner VBoxLayout) where outer is a scroll area."""
    outer = QWidget()
    outer_lay = QVBoxLayout(outer)
    outer_lay.setContentsMargins(0, 0, 0, 0)
    outer_lay.setSpacing(0)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)

    inner = QWidget()
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(16, 12, 16, 12)
    lay.setSpacing(8)
    scroll.setWidget(inner)
    outer_lay.addWidget(scroll)
    return outer, lay


def _settings_form_row(label: str, field: QWidget, width: int = 90) -> QWidget:
    from app.widgets._form_row import form_row
    return form_row(label, field, label_width=width)


def _settings_group(title: str, widgets: list[QWidget]) -> QGroupBox:
    """Bordered sub-section grouping related form rows inside a settings tab."""
    gb = QGroupBox(title)
    lay = QVBoxLayout(gb)
    lay.setSpacing(8)
    lay.setContentsMargins(12, 10, 12, 12)
    for wd in widgets:
        lay.addWidget(wd)
    return gb


def _divider() -> QFrame:
    f = QFrame()
    f.setObjectName("Divider")
    f.setFixedHeight(1)
    return f
