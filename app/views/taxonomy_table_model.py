"""Taxonomy table model and small table UI helpers."""
from __future__ import annotations

from typing import Any, Optional

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QPushButton, QStyledItemDelegate, QStyleOptionViewItem, QWidget

from app.views import taxonomy_view_support as _tv

# ── Table model ───────────────────────────────────────────────────────────────

class _TaxonTableModel(QAbstractTableModel):
    """Flat list model; columns are selected dynamically via vis_levels/vis_langs.

    Column layout (mirrors DOM):
      0 — checkbox (taxon-th-check)
      1 — # row number (taxon-th-num)
      2..N — dynamic data columns
      N+1 — 来源 (source)
      N+2 — 操作 (action placeholder — delegate renders buttons)
    """

    checked_changed = pyqtSignal()   # emitted whenever the checkbox set changes

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._records: list[dict[str, Any]] = []
        self._vis_levels: dict[str, bool] = {k: True for k, _ in _tv._LEVEL_CHIPS}
        # taxonGroup (纲) always visible; individual level toggles affect order/family/genus/species
        self._vis_langs: dict[str, bool] = {"cn": True, "latin": True}
        self._columns: list[dict[str, Any]] = []
        self._checked: set[str] = set()    # recordIds that are checked
        self._page_offset: int = 0         # for row # display
        self._rebuild_columns()

    # -- Column management -------------------------------------------------

    def _rebuild_columns(self) -> None:
        """Recompute the ordered list of visible data columns."""
        visible = []
        for col in _tv._ALL_COLS:
            level = col["level"]
            lang = col["lang"]
            if level == "taxonGroup":
                if self._vis_langs.get(lang, True):
                    visible.append(col)
            elif level in self._vis_levels:
                if self._vis_levels[level] and self._vis_langs.get(lang, True):
                    visible.append(col)
            elif self._vis_langs.get(lang, True):
                visible.append(col)
        self._columns = visible

    def set_vis_level(self, level: str, show: bool) -> None:
        self._vis_levels[level] = show
        self.beginResetModel()
        self._rebuild_columns()
        self.endResetModel()

    def set_vis_lang(self, lang: str, show: bool) -> None:
        self._vis_langs[lang] = show
        self.beginResetModel()
        self._rebuild_columns()
        self.endResetModel()

    def columns(self) -> list[dict[str, Any]]:
        return list(self._columns)

    # -- Data loading -------------------------------------------------------

    def set_records(self, records: list[dict[str, Any]], page_offset: int = 0) -> None:
        self.beginResetModel()
        self._records = list(records)
        self._page_offset = page_offset
        # NOTE: checked state persists across pages — selecting entries on page 1,
        # paging away, and clicking "WoRMS 更新所选" must still target page-1 rows.
        # The set is keyed by recordId (globally unique); 取消选择 clears it.
        self.endResetModel()

    def record_at(self, row: int) -> Optional[dict[str, Any]]:
        if 0 <= row < len(self._records):
            return self._records[row]
        return None

    def set_page_offset(self, offset: int) -> None:
        self._page_offset = offset

    def checked_ids(self) -> list[str]:
        return list(self._checked)

    def clear_checked(self) -> None:
        self._checked.clear()
        self.dataChanged.emit(
            self.index(0, _tv._COL_CHECK),
            self.index(max(0, len(self._records) - 1), _tv._COL_CHECK),
        )
        self.checked_changed.emit()

    def set_all_page_checked(self, checked: bool) -> None:
        if checked:
            self._checked.update(
                r.get("recordId", "") for r in self._records
                if r.get("recordId")
            )
        else:
            page_ids = {r.get("recordId", "") for r in self._records}
            self._checked -= page_ids
        self.dataChanged.emit(
            self.index(0, _tv._COL_CHECK),
            self.index(max(0, len(self._records) - 1), _tv._COL_CHECK),
        )
        self.checked_changed.emit()

    def is_page_all_checked(self) -> bool:
        if not self._records:
            return False
        return all(
            r.get("recordId", "") in self._checked
            for r in self._records
            if r.get("recordId")
        )

    # -- Qt model interface -----------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._records)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        # checkbox + # + dynamic cols + 来源 + 操作
        return _tv._COL_DATA_START + len(self._columns) + 2

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            if section == _tv._COL_CHECK:
                return ""    # checkbox painted by delegate / header checkbox
            if section == _tv._COL_NUM:
                return "#"
            data_idx = section - _tv._COL_DATA_START
            if 0 <= data_idx < len(self._columns):
                return self._columns[data_idx]["label"]
            # 来源 column
            after_data = section - _tv._COL_DATA_START - len(self._columns)
            if after_data == 0:
                return "来源"
            if after_data == 1:
                return "操作"
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if row >= len(self._records):
            return None
        rec = self._records[row]
        is_user = rec.get("recordId", "").startswith("user:")
        rec_id = rec.get("recordId", "")

        # ── Checkbox column ────────────────────────────────────────────
        if col == _tv._COL_CHECK:
            if role == Qt.ItemDataRole.CheckStateRole:
                return Qt.CheckState.Checked if rec_id in self._checked else Qt.CheckState.Unchecked
            return None

        # ── Row number column ──────────────────────────────────────────
        if col == _tv._COL_NUM:
            if role == Qt.ItemDataRole.DisplayRole:
                return str(self._page_offset + row + 1)
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor(_tv._C_DIM)
            return None

        # ── Dynamic data columns ───────────────────────────────────────
        data_idx = col - _tv._COL_DATA_START
        n_data = len(self._columns)

        if 0 <= data_idx < n_data:
            if role == Qt.ItemDataRole.DisplayRole:
                val = rec.get(self._columns[data_idx]["key"], "")
                return str(val) if val else ""
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor(_tv._C_ACCENT) if is_user else QColor(_tv._C_MUTED)
            if role == Qt.ItemDataRole.BackgroundRole:
                if is_user:
                    c = QColor(_tv._C_ACCENT)
                    c.setAlpha(10)
                    return c
            if role == Qt.ItemDataRole.UserRole:
                return rec
            return None

        # ── 来源 column ────────────────────────────────────────────────
        after_data = col - _tv._COL_DATA_START - n_data
        if after_data == 0:
            if role == Qt.ItemDataRole.DisplayRole:
                return "用户" if is_user else "种子"
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor(_tv._C_ACCENT) if is_user else QColor(_tv._C_DIM)
            return None

        # ── 操作 column ────────────────────────────────────────────────
        if after_data == 1:
            if role == Qt.ItemDataRole.UserRole:
                return rec
            return None

        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = super().flags(index)
        if index.column() == _tv._COL_CHECK:
            return base | Qt.ItemFlag.ItemIsUserCheckable
        return base

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if not index.isValid():
            return False
        if index.column() == _tv._COL_CHECK and role == Qt.ItemDataRole.CheckStateRole:
            rec = self._records[index.row()]
            rec_id = rec.get("recordId", "")
            if value == Qt.CheckState.Checked:
                self._checked.add(rec_id)
            else:
                self._checked.discard(rec_id)
            self.dataChanged.emit(index, index, [role])
            self.checked_changed.emit()
            return True
        return False


# ── Column-group chip button (taxon-col-chip style) ───────────────────────────

class _ChipButton(QPushButton):
    """Toggle chip matching .taxon-col-chip in styles.css."""

    def __init__(self, text: str, checked: bool = True, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        _tv._refresh_palette()
        self.setCheckable(True)
        self.setChecked(checked)
        self._refresh_style()
        self.toggled.connect(lambda _: self._refresh_style())

    def _refresh_style(self) -> None:
        if self.isChecked():
            self.setStyleSheet(
                f"QPushButton {{ background: {_tv._C_ACCENT}; color: {_tv._C_INPUT}; font-weight: 600;"
                f" border: none; border-radius: 6px; padding: 4px 10px; font-size: 12px; }}"
                f"QPushButton:hover {{ background: {_tv._C_ACCENT_HI}; }}"
            )
        else:
            self.setStyleSheet(
                f"QPushButton {{ background: {_tv._C_PANEL}; color: {_tv._C_MUTED};"
                f" border: 1px solid {_tv._C_BORDER}; border-radius: 6px;"
                f" padding: 4px 10px; font-size: 12px; }}"
                f"QPushButton:hover {{ color: {_tv._C_TEXT}; border-color: {_tv._C_ACCENT}; }}"
            )


# ── View-switch button (taxon-view-btn style) ─────────────────────────────────

class _ViewTabButton(QPushButton):
    """Segmented-switch tab button matching .taxon-view-btn."""

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        _tv._refresh_palette()
        self.setCheckable(True)
        self._refresh_style()
        self.toggled.connect(lambda _: self._refresh_style())

    def _refresh_style(self) -> None:
        if self.isChecked():
            self.setStyleSheet(
                f"QPushButton {{ background: {_tv._C_ACCENT}; color: {_tv._C_INPUT}; font-weight: 600;"
                f" border: none; border-radius: 6px; padding: 6px 14px; font-size: 12px; }}"
            )
        else:
            self.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {_tv._C_MUTED};"
                f" border: none; border-radius: 6px; padding: 6px 14px; font-size: 12px; }}"
                f"QPushButton:hover {{ color: {_tv._C_TEXT}; background: {_tv._C_ACCENT_SOFT}; }}"
            )


# ── 操作 column delegate — renders inline "编辑" / "删除" buttons ──────────────

class _ActionDelegate(QStyledItemDelegate):
    """Renders per-row 编辑 + 删除 buttons in the 操作 column.

    Signals (emitted with the record dict as argument):
      edit_requested(dict)
      delete_requested(dict)
    """

    edit_requested: pyqtSignal = pyqtSignal(dict)
    delete_requested: pyqtSignal = pyqtSignal(dict)

    _BTN_W = 42
    _BTN_H = 22
    _GAP   = 4

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

    def _btn_rects(self, option: QStyleOptionViewItem) -> tuple[Any, Any]:
        """Return (edit_rect, del_rect) for the given option geometry."""
        from PyQt6.QtCore import QRect
        r = option.rect
        y = r.y() + (r.height() - self._BTN_H) // 2
        x_edit = r.x() + 4
        x_del  = x_edit + self._BTN_W + self._GAP
        return (
            QRect(x_edit, y, self._BTN_W, self._BTN_H),
            QRect(x_del,  y, self._BTN_W, self._BTN_H),
        )

    def paint(self, painter: Any, option: QStyleOptionViewItem, index: QModelIndex) -> None:  # type: ignore[override]
        rec = index.data(Qt.ItemDataRole.UserRole)
        if rec is None:
            return
        is_user = rec.get("recordId", "").startswith("user:")
        edit_rect, del_rect = self._btn_rects(option)

        from PyQt6.QtGui import QPainter, QBrush, QPen, QFont
        from PyQt6.QtCore import QRect, Qt as _Qt

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        def _draw_btn(rect: Any, text: str, fg: str, border: str) -> None:
            painter.setPen(QPen(QColor(border)))
            painter.setBrush(QBrush(QColor(0, 0, 0, 0)))
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(QPen(QColor(fg)))
            f = painter.font()
            f.setPixelSize(11)
            painter.setFont(f)
            painter.drawText(rect, _Qt.AlignmentFlag.AlignCenter, text)

        # Edit button — always shown in original view
        _draw_btn(edit_rect, "编辑", _tv._C_MUTED, _tv._C_BORDER)

        # Delete button — only for user records
        if is_user:
            _draw_btn(del_rect, "删除", _tv._C_DANGER, _tv._C_DANGER_SOFT)

        painter.restore()

    def editorEvent(
        self,
        event: Any,
        model: Any,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> bool:
        from PyQt6.QtCore import QEvent
        if event.type() != QEvent.Type.MouseButtonRelease:
            return False
        rec = index.data(Qt.ItemDataRole.UserRole)
        if rec is None:
            return False
        is_user = rec.get("recordId", "").startswith("user:")
        edit_rect, del_rect = self._btn_rects(option)
        pos = event.pos()
        if edit_rect.contains(pos):
            self.edit_requested.emit(rec)
            return True
        if is_user and del_rect.contains(pos):
            self.delete_requested.emit(rec)
            return True
        return False

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> Any:
        from PyQt6.QtCore import QSize
        return QSize(self._BTN_W * 2 + self._GAP + 12, 30)
