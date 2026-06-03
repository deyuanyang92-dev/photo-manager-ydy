"""taxonomy_view.py — Taxonomy library management view.

Faithful PyQt6 replica of the web «内置分类库» page.

Web oracle:
  - pages_dom.json "内置分类库" section (all controls/layout keys)
  - styles.css  .taxon-table-* classes (line 5651 ff.)
  - app.js renderTaxonomyPage() (line 12060 ff.)
  - taxonomy_service.py (seed read-only + user CRUD)

view_id   = "taxonomy"
nav_title = "内置分类库"
nav_icon  = "🧬"
"""
from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any, Optional

from PyQt6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QPoint,
    Qt,
    QThread,
    pyqtSignal,
)
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QSpinBox,
    QSplitter,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.views.base_view import BaseView
from app.services.taxonomy_service import TaxonomyService
from app.services.worms_service import WormsService

if False:  # TYPE_CHECKING
    from app.app_context import AppContext

# ── Paths ─────────────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent           # app/views/
_PROJECT_ROOT = _HERE.parent.parent               # photo-platform-ydy-v3/
_DATA_DIR = _PROJECT_ROOT / "data"

_DEFAULT_SEED_PATH = _DATA_DIR / "taxonomy_seed.json"
_DEFAULT_USER_PATH = _DATA_DIR / "user_taxonomy.json"

# Page size mirrors the web default (server.js limit)
_PAGE_SIZE = 50

# ── Column definitions ────────────────────────────────────────────────────────
# (display_label, record_key, show_in_original, show_in_worms)
# These mirror getVisibleTaxonColumns() logic.

_ALL_COLS: list[dict[str, Any]] = [
    {"label": "纲(中)",   "key": "classCn",       "level": "taxonGroup", "lang": "cn"},
    {"label": "纲(拉丁)",  "key": "class",         "level": "taxonGroup", "lang": "latin"},
    {"label": "目(中)",   "key": "orderCn",        "level": "order",     "lang": "cn"},
    {"label": "目(拉丁)",  "key": "order",         "level": "order",     "lang": "latin"},
    {"label": "科(中)",   "key": "familyCn",       "level": "family",    "lang": "cn"},
    {"label": "科(拉丁)",  "key": "family",        "level": "family",    "lang": "latin"},
    {"label": "属(中)",   "key": "genusCn",        "level": "genus",     "lang": "cn"},
    {"label": "属(拉丁)",  "key": "genus",         "level": "genus",     "lang": "latin"},
    {"label": "种(中)",   "key": "speciesCn",      "level": "species",   "lang": "cn"},
    {"label": "种(拉丁)",  "key": "species",       "level": "species",   "lang": "latin"},
]

# Level keys available as column-group chips
_LEVEL_CHIPS: list[tuple[str, str]] = [
    ("order",   "目"),
    ("family",  "科"),
    ("genus",   "属"),
    ("species", "种"),
]
_LANG_CHIPS: list[tuple[str, str]] = [
    ("cn",    "中文"),
    ("latin", "拉丁名"),
]

# ── Column index constants ────────────────────────────────────────────────────
_COL_CHECK = 0   # checkbox column (taxon-th-check)
_COL_NUM   = 1   # row number column (taxon-th-num / #)
# dynamic data columns start at _COL_DATA_START
_COL_DATA_START = 2


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

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._records: list[dict[str, Any]] = []
        self._vis_levels: dict[str, bool] = {k: True for k, _ in _LEVEL_CHIPS}
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
        for col in _ALL_COLS:
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
        # Clear checked state for rows not in new page
        rec_ids = {r.get("recordId", "") for r in self._records}
        self._checked &= rec_ids
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
            self.index(0, _COL_CHECK),
            self.index(max(0, len(self._records) - 1), _COL_CHECK),
        )

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
            self.index(0, _COL_CHECK),
            self.index(max(0, len(self._records) - 1), _COL_CHECK),
        )

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
        return _COL_DATA_START + len(self._columns) + 2

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            if section == _COL_CHECK:
                return ""    # checkbox painted by delegate / header checkbox
            if section == _COL_NUM:
                return "#"
            data_idx = section - _COL_DATA_START
            if 0 <= data_idx < len(self._columns):
                return self._columns[data_idx]["label"]
            # 来源 column
            after_data = section - _COL_DATA_START - len(self._columns)
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
        if col == _COL_CHECK:
            if role == Qt.ItemDataRole.CheckStateRole:
                return Qt.CheckState.Checked if rec_id in self._checked else Qt.CheckState.Unchecked
            return None

        # ── Row number column ──────────────────────────────────────────
        if col == _COL_NUM:
            if role == Qt.ItemDataRole.DisplayRole:
                return str(self._page_offset + row + 1)
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor("#5f7d7a")
            return None

        # ── Dynamic data columns ───────────────────────────────────────
        data_idx = col - _COL_DATA_START
        n_data = len(self._columns)

        if 0 <= data_idx < n_data:
            if role == Qt.ItemDataRole.DisplayRole:
                val = rec.get(self._columns[data_idx]["key"], "")
                return str(val) if val else ""
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor("#29b9ab") if is_user else QColor("#87a2a1")
            if role == Qt.ItemDataRole.BackgroundRole:
                if is_user:
                    return QColor(41, 185, 171, 10)
            if role == Qt.ItemDataRole.UserRole:
                return rec
            return None

        # ── 来源 column ────────────────────────────────────────────────
        after_data = col - _COL_DATA_START - n_data
        if after_data == 0:
            if role == Qt.ItemDataRole.DisplayRole:
                return "用户" if is_user else "种子"
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor("#29b9ab") if is_user else QColor("#5f7d7a")
            return None

        # ── 操作 column ────────────────────────────────────────────────
        if after_data == 1:
            if role == Qt.ItemDataRole.UserRole:
                return rec
            return None

        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = super().flags(index)
        if index.column() == _COL_CHECK:
            return base | Qt.ItemFlag.ItemIsUserCheckable
        return base

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if not index.isValid():
            return False
        if index.column() == _COL_CHECK and role == Qt.ItemDataRole.CheckStateRole:
            rec = self._records[index.row()]
            rec_id = rec.get("recordId", "")
            if value == Qt.CheckState.Checked:
                self._checked.add(rec_id)
            else:
                self._checked.discard(rec_id)
            self.dataChanged.emit(index, index, [role])
            return True
        return False


# ── Add/Edit dialog ───────────────────────────────────────────────────────────

_DIALOG_FIELDS: list[tuple[str, str, bool]] = [
    ("class",     "纲 / 门（Latin）",   True),
    ("order",     "目（Latin）",         True),
    ("family",    "科（Latin）",         True),
    ("species",   "种（Latin）",         True),
    ("classCn",   "纲中文",              False),
    ("orderCn",   "目中文",              False),
    ("familyCn",  "科中文",              False),
    ("speciesCn", "种中文",              False),
    ("genus",     "属（Latin）",         False),
    ("genusCn",   "属中文",              False),
]


class _RecordDialog(QDialog):
    """Form dialog for adding or editing a user taxonomy record.

    Mirrors openTaxonomyTableModal() in app.js.
    Shows a '查看历史' button when the record has a history[] list.
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        record: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑分类条目" if record else "新增分类条目")
        self.setMinimumWidth(420)
        self._record = record or {}
        self._inputs: dict[str, QLineEdit] = {}
        self._btn_history: QPushButton = QPushButton("查看历史")
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 12)

        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        for key, label_text, required in _DIALOG_FIELDS:
            inp = QLineEdit()
            inp.setText(self._record.get(key, ""))
            if required:
                inp.setPlaceholderText("必填")
            form.addRow(label_text, inp)
            self._inputs[key] = inp

        layout.addLayout(form)

        # History button — visible only when record has history entries
        history = self._record.get("history", [])
        self._btn_history.setObjectName("Outline")
        self._btn_history.setVisible(bool(history))
        self._btn_history.clicked.connect(self._show_history)
        layout.addWidget(self._btn_history)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        for key, label_text, required in _DIALOG_FIELDS:
            if required and not self._inputs[key].text().strip():
                QMessageBox.warning(self, "必填项", f"「{label_text}」不能为空")
                self._inputs[key].setFocus()
                return
        self.accept()

    def _show_history(self) -> None:
        history = self._record.get("history", [])
        if not history:
            return
        dlg = _HistoryDialog(self, history=history)
        restored = dlg.exec_and_get()
        if restored is not None:
            # Apply restored snapshot to the form inputs
            for k, v in restored.items():
                inp = self._inputs.get(k)
                if inp is not None:
                    inp.setText(str(v))

    def get_record(self) -> dict[str, Any]:
        return {k: inp.text().strip() for k, inp in self._inputs.items()}


class _HistoryDialog(QDialog):
    """Shows history entries for a user record and allows 1-level rollback.

    Each entry in history[] has: { "at": ISO8601, "before": {10 fields} }
    Selecting an entry and clicking "回滚到此版本" fills the parent form
    with the snapshot values (the parent dialog still needs OK to persist).
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        history: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑历史")
        self.setMinimumWidth(560)
        self.setMinimumHeight(320)
        self._history = list(reversed(history or []))  # newest first
        self._selected_before: Optional[dict[str, Any]] = None
        self._build_ui()

    def _build_ui(self) -> None:
        from PyQt6.QtWidgets import QListWidget, QListWidgetItem

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        info = QLabel("选择历史版本后点击「回滚」，将把该快照填入编辑框（需再点确定保存）。")
        info.setWordWrap(True)
        info.setStyleSheet("color:#87a2a1; font-size:11px;")
        layout.addWidget(info)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        for entry in self._history:
            at = entry.get("at", "")
            before = entry.get("before", {})
            species = before.get("species", "")
            family  = before.get("family", "")
            label   = f"{at[:19].replace('T', ' ')}  →  {species} ({family})"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, before)
            self._list.addItem(item)
        layout.addWidget(self._list, 1)

        buttons = QDialogButtonBox()
        btn_rollback = buttons.addButton("回滚到此版本", QDialogButtonBox.ButtonRole.AcceptRole)
        btn_cancel   = buttons.addButton("取消",         QDialogButtonBox.ButtonRole.RejectRole)
        btn_rollback.clicked.connect(self._on_rollback)
        btn_cancel.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _on_rollback(self) -> None:
        item = self._list.currentItem()
        if item is None:
            QMessageBox.information(self, "提示", "请先选择一条历史记录")
            return
        self._selected_before = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def exec_and_get(self) -> Optional[dict[str, Any]]:
        """Run dialog modally; return the snapshot dict if user confirmed, else None."""
        if self.exec() == QDialog.DialogCode.Accepted:
            return self._selected_before
        return None


# ── Column-group chip button (taxon-col-chip style) ───────────────────────────

class _ChipButton(QPushButton):
    """Toggle chip matching .taxon-col-chip in styles.css."""

    def __init__(self, text: str, checked: bool = True, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self._refresh_style()
        self.toggled.connect(lambda _: self._refresh_style())

    def _refresh_style(self) -> None:
        if self.isChecked():
            self.setStyleSheet(
                "QPushButton { background: #29b9ab; color: #061c1e; font-weight: 600;"
                " border: none; border-radius: 6px; padding: 4px 10px; font-size: 12px; }"
                "QPushButton:hover { background: #31d4c4; }"
            )
        else:
            self.setStyleSheet(
                "QPushButton { background: #10242a; color: #87a2a1;"
                " border: 1px solid rgba(145,182,181,0.18); border-radius: 6px;"
                " padding: 4px 10px; font-size: 12px; }"
                "QPushButton:hover { color: #eef3ef; border-color: #29b9ab; }"
            )


# ── View-switch button (taxon-view-btn style) ─────────────────────────────────

class _ViewTabButton(QPushButton):
    """Segmented-switch tab button matching .taxon-view-btn."""

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setCheckable(True)
        self._refresh_style()
        self.toggled.connect(lambda _: self._refresh_style())

    def _refresh_style(self) -> None:
        if self.isChecked():
            self.setStyleSheet(
                "QPushButton { background: #29b9ab; color: #061c1e; font-weight: 600;"
                " border: none; border-radius: 6px; padding: 6px 14px; font-size: 12px; }"
            )
        else:
            self.setStyleSheet(
                "QPushButton { background: transparent; color: #87a2a1;"
                " border: none; border-radius: 6px; padding: 6px 14px; font-size: 12px; }"
                "QPushButton:hover { color: #eef3ef; background: rgba(41,185,171,0.1); }"
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
        _draw_btn(edit_rect, "编辑", "#87a2a1", "rgba(145,182,181,0.3)")

        # Delete button — only for user records
        if is_user:
            _draw_btn(del_rect, "删除", "#e66e63", "rgba(230,110,99,0.4)")

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


# ── Main view ─────────────────────────────────────────────────────────────────

class TaxonomyView(BaseView):
    """Taxonomy library management view (view_id="taxonomy").

    Faithful replica of the web 内置分类库 page.

    Layout (matches DOM):

        [QScrollArea — controls area, prevents top from squishing table]
          taxon-table-header
            taxon-table-title-row  (h2 + stats + taxon-view-switch + 图表-btn)
            taxon-col-controls     (类群 chips + 语言 chips)  [original only]
          taxon-table-toolbar
            taxon-table-filter-bar (col-select + search-input + 搜索 + 清除)
            taxon-table-actions    (+ 新增 | selection note | 全选 | 取消 |
                                    WoRMS更新所选 | WoRMS更新筛选 | 导出Excel |
                                    导出CSV | 导入Excel/CSV)
        taxon-table-wrap
          QTableView  (☑ # <dynamic cols> 来源 操作)
          [loading overlay label when _loading=True]
        taxon-table-pager
          上一页 | 第N/M页（共K条） | 跳到 [spin] | 下一页  | footer stats
    """

    view_id = "taxonomy"
    nav_title = "内置分类库"
    nav_icon = "🧬"

    def __init__(self, ctx: "AppContext") -> None:
        # View state — mirrors state.taxonTable in app.js
        self._view: str = "original"          # "original" | "worms" | "compare"
        self._show_chart: bool = False
        self._page: int = 1
        self._total: int = 0
        self._selected_ids: list[str] = []
        self._select_all_filtered: bool = False
        self._filter_col: str = ""
        self._filter_text: str = ""
        self._loading: bool = False
        self._svc: Optional[TaxonomyService] = None
        # New state for facet/sort/WoRMS
        self._worms_svc: Optional[WormsService] = None
        self._chart_dialog: Optional[QDialog] = None
        self._col_filters: dict[str, Optional[dict[str, Any]]] = {}
        self._sort_col: str = ""
        self._sort_dir: str = "asc"
        self._facet_panel: Optional[_TaxonFacetPanel] = None
        super().__init__(ctx)

    # ── BaseView._setup_ui ────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Scrollable controls area (header + toolbar) ───────────────
        # Wrapped in QScrollArea so it never squishes the table.
        ctrl_container = QWidget()
        ctrl_container.setObjectName("TaxonCtrlContainer")
        ctrl_container.setStyleSheet(
            "QWidget#TaxonCtrlContainer { background: transparent; }"
        )
        ctrl_v = QVBoxLayout(ctrl_container)
        ctrl_v.setContentsMargins(0, 0, 0, 0)
        ctrl_v.setSpacing(0)

        # ── Header (taxon-table-header) ───────────────────────────────
        header_frame = QFrame()
        header_frame.setObjectName("TaxonHeader")
        header_frame.setStyleSheet(
            "QFrame#TaxonHeader { border: none;"
            " border-bottom: 1px solid rgba(145,182,181,0.10); }"
        )
        header_layout = QVBoxLayout(header_frame)
        header_layout.setContentsMargins(24, 20, 24, 0)
        header_layout.setSpacing(12)

        # taxon-table-title-row
        title_row = QHBoxLayout()
        title_row.setSpacing(14)

        page_title = QLabel("内置分类库")
        page_title.setObjectName("TaxonPageTitle")
        page_title.setStyleSheet(
            "QLabel { font-size: 18px; font-weight: 600; color: #eef3ef;"
            " background: transparent; }"
        )
        title_row.addWidget(page_title)

        self._stats_label = QLabel("共 0 条")
        self._stats_label.setObjectName("Muted")
        self._stats_label.setStyleSheet(
            "QLabel { font-size: 13px; color: #87a2a1; background: transparent; }"
        )
        title_row.addWidget(self._stats_label)

        title_row.addStretch()

        # taxon-view-switch (3 tabs + "图表" toggle) in a capsule frame
        switch_wrapper = QFrame()
        switch_wrapper.setStyleSheet(
            "QFrame { background: #10242a; border-radius: 8px; padding: 2px; }"
        )
        switch_inner = QHBoxLayout(switch_wrapper)
        switch_inner.setContentsMargins(3, 3, 3, 3)
        switch_inner.setSpacing(2)

        self._btn_original = _ViewTabButton("原始分类")
        self._btn_worms    = _ViewTabButton("WoRMS 分类")
        self._btn_compare  = _ViewTabButton("对照视图")
        self._btn_original.setChecked(True)

        for btn, view_key in [
            (self._btn_original, "original"),
            (self._btn_worms,    "worms"),
            (self._btn_compare,  "compare"),
        ]:
            btn.clicked.connect(lambda checked, k=view_key: self._on_view_switch(k))
            switch_inner.addWidget(btn)

        title_row.addWidget(switch_wrapper)

        # "图表" toggle button — only active in "original" view (mirrors app.js)
        self._btn_chart = _ViewTabButton("图表")
        self._btn_chart.setChecked(False)
        self._btn_chart.clicked.connect(self._on_chart_toggle)
        title_row.addWidget(self._btn_chart)

        header_layout.addLayout(title_row)

        # taxon-col-controls (only in "original" view)
        self._col_ctrl_frame = QFrame()
        col_ctrl_layout = QHBoxLayout(self._col_ctrl_frame)
        col_ctrl_layout.setContentsMargins(0, 4, 0, 12)
        col_ctrl_layout.setSpacing(16)

        # 类群 group
        level_group = QHBoxLayout()
        level_group.setSpacing(6)
        level_label = QLabel("类群")
        level_label.setStyleSheet(
            "QLabel { color: #5f7d7a; font-size: 11px; font-weight: 600;"
            " background: transparent; }"
        )
        level_group.addWidget(level_label)

        self._level_chips: dict[str, _ChipButton] = {}
        for level_key, level_text in _LEVEL_CHIPS:
            chip = _ChipButton(level_text, checked=True)
            chip.toggled.connect(
                lambda checked, k=level_key: self._on_level_chip(k, checked)
            )
            level_group.addWidget(chip)
            self._level_chips[level_key] = chip
        col_ctrl_layout.addLayout(level_group)

        # 语言 group
        lang_group = QHBoxLayout()
        lang_group.setSpacing(6)
        lang_label = QLabel("语言")
        lang_label.setStyleSheet(
            "QLabel { color: #5f7d7a; font-size: 11px; font-weight: 600;"
            " background: transparent; }"
        )
        lang_group.addWidget(lang_label)

        self._lang_chips: dict[str, _ChipButton] = {}
        for lang_key, lang_text in _LANG_CHIPS:
            chip = _ChipButton(lang_text, checked=True)
            chip.toggled.connect(
                lambda checked, k=lang_key: self._on_lang_chip(k, checked)
            )
            lang_group.addWidget(chip)
            self._lang_chips[lang_key] = chip
        col_ctrl_layout.addLayout(lang_group)
        col_ctrl_layout.addStretch()

        header_layout.addWidget(self._col_ctrl_frame)
        ctrl_v.addWidget(header_frame)

        # ── Toolbar (taxon-table-toolbar) ─────────────────────────────
        toolbar_frame = QFrame()
        toolbar_frame.setObjectName("TaxonToolbar")
        toolbar_frame.setStyleSheet(
            "QFrame#TaxonToolbar { border: none;"
            " border-bottom: 1px solid rgba(145,182,181,0.10); }"
        )
        toolbar_layout = QVBoxLayout(toolbar_frame)
        toolbar_layout.setContentsMargins(24, 12, 24, 12)
        toolbar_layout.setSpacing(10)

        # filter bar (taxon-table-filter-bar)
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        self._col_select = QComboBox()
        self._col_select.setFixedWidth(116)
        for val, label in [
            ("",           "全部列"),
            ("classCn",    "纲(中)"),
            ("class",      "纲(拉丁)"),
            ("orderCn",    "目(中)"),
            ("order",      "目(拉丁)"),
            ("familyCn",   "科(中)"),
            ("family",     "科(拉丁)"),
            ("genusCn",    "属(中)"),
            ("genus",      "属(拉丁)"),
            ("speciesCn",  "种(中)"),
            ("species",    "种(拉丁)"),
        ]:
            self._col_select.addItem(label, val)
        filter_row.addWidget(self._col_select)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("搜索…")
        self._search_input.setMinimumWidth(180)
        self._search_input.returnPressed.connect(self._on_search)
        filter_row.addWidget(self._search_input)

        btn_search = QPushButton("搜索")
        btn_search.setObjectName("Outline")
        btn_search.setFixedWidth(60)
        btn_search.clicked.connect(self._on_search)
        filter_row.addWidget(btn_search)

        btn_clear = QPushButton("清除")
        btn_clear.setObjectName("Outline")
        btn_clear.setFixedWidth(60)
        btn_clear.clicked.connect(self._on_clear_filter)
        filter_row.addWidget(btn_clear)

        self._filter_active_label = QLabel("")
        self._filter_active_label.setStyleSheet(
            "QLabel { color: #29b9ab; font-size: 11px; background: transparent; }"
        )
        self._filter_active_label.hide()
        filter_row.addWidget(self._filter_active_label)

        filter_row.addStretch()
        toolbar_layout.addLayout(filter_row)

        # action bar (taxon-table-actions)
        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        self._btn_add = QPushButton("+ 新增条目")
        self._btn_add.setObjectName("Primary")
        self._btn_add.setFixedWidth(96)
        self._btn_add.clicked.connect(self._on_add)
        action_row.addWidget(self._btn_add)

        # Thin separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("QFrame { color: rgba(145,182,181,0.15); }")
        action_row.addWidget(sep)

        self._selection_note = QLabel("已选 0 条")
        self._selection_note.setStyleSheet(
            "QLabel { color: #87a2a1; font-size: 11px; background: transparent; }"
        )
        action_row.addWidget(self._selection_note)

        btn_sel_all = QPushButton("全选筛选结果")
        btn_sel_all.setObjectName("Outline")
        btn_sel_all.clicked.connect(self._on_select_all_filtered)
        action_row.addWidget(btn_sel_all)

        btn_desel = QPushButton("取消选择")
        btn_desel.setObjectName("Outline")
        btn_desel.clicked.connect(self._on_deselect)
        action_row.addWidget(btn_desel)

        # Thin separator
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet("QFrame { color: rgba(145,182,181,0.15); }")
        action_row.addWidget(sep2)

        self._btn_worms_sel = QPushButton("WoRMS 更新所选")
        self._btn_worms_sel.setObjectName("Primary")
        self._btn_worms_sel.setEnabled(False)
        self._btn_worms_sel.clicked.connect(
            lambda: self._on_worms_update(selected_only=True)
        )
        action_row.addWidget(self._btn_worms_sel)

        btn_worms_filt = QPushButton("WoRMS 更新筛选结果")
        btn_worms_filt.setObjectName("Outline")
        btn_worms_filt.clicked.connect(
            lambda: self._on_worms_update(selected_only=False)
        )
        action_row.addWidget(btn_worms_filt)

        # Thin separator
        sep3 = QFrame()
        sep3.setFrameShape(QFrame.Shape.VLine)
        sep3.setStyleSheet("QFrame { color: rgba(145,182,181,0.15); }")
        action_row.addWidget(sep3)

        btn_export_xlsx = QPushButton("导出 Excel")
        btn_export_xlsx.setObjectName("Outline")
        btn_export_xlsx.clicked.connect(lambda: self._on_export("xlsx"))
        action_row.addWidget(btn_export_xlsx)

        btn_export_csv = QPushButton("导出 CSV")
        btn_export_csv.setObjectName("Outline")
        btn_export_csv.clicked.connect(lambda: self._on_export("csv"))
        action_row.addWidget(btn_export_csv)

        # 导入 Excel/CSV — file-picker button (taxon-import-label in DOM)
        btn_import = QPushButton("导入 Excel/CSV")
        btn_import.setObjectName("Outline")
        btn_import.clicked.connect(self._on_import)
        action_row.addWidget(btn_import)

        action_row.addStretch()
        toolbar_layout.addLayout(action_row)
        ctrl_v.addWidget(toolbar_frame)

        # Scroll area wrapping the controls
        ctrl_scroll = QScrollArea()
        ctrl_scroll.setWidget(ctrl_container)
        ctrl_scroll.setWidgetResizable(True)
        ctrl_scroll.setFrameShape(QFrame.Shape.NoFrame)
        ctrl_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        ctrl_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # Cap scroll area height so it never steals table space
        ctrl_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        ctrl_scroll.setMaximumHeight(220)
        ctrl_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        root.addWidget(ctrl_scroll)

        # ── Table area (taxon-table-wrap) ─────────────────────────────
        self._model = _TaxonTableModel(self)

        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(False)  # server-side sort via API
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.verticalHeader().setVisible(False)
        self._table.doubleClicked.connect(self._on_row_double_click)
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        # Row height
        self._table.verticalHeader().setDefaultSectionSize(32)

        # Column widths for checkbox / # columns
        self._table.setColumnWidth(_COL_CHECK, 32)
        self._table.setColumnWidth(_COL_NUM,   40)
        # Action column delegate
        self._action_delegate = _ActionDelegate(self._table)
        self._action_delegate.edit_requested.connect(self._edit_record)
        self._action_delegate.delete_requested.connect(self._delete_record)

        # Right-click context menu (mirrors openTaxonRowMenu in app.js)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_row_context_menu)

        # Column header right-click → facet filter (mirrors openTaxonFacetMenu)
        self._table.horizontalHeader().setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._table.horizontalHeader().customContextMenuRequested.connect(
            self._on_header_context_menu
        )

        # Table in a wrapper frame for the loading overlay
        table_wrapper = QFrame()
        table_wrapper.setObjectName("TaxonTableWrap")
        table_wrapper.setStyleSheet(
            "QFrame#TaxonTableWrap { border: none; }"
        )
        table_wrap_layout = QVBoxLayout(table_wrapper)
        table_wrap_layout.setContentsMargins(0, 0, 0, 0)
        table_wrap_layout.setSpacing(0)

        # Loading indicator (taxon-table-loading)
        self._loading_label = QLabel("加载中…")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label.setStyleSheet(
            "QLabel { color: #29b9ab; font-size: 14px; background: transparent;"
            " padding: 24px; }"
        )
        self._loading_label.hide()

        table_wrap_layout.addWidget(self._loading_label)
        table_wrap_layout.addWidget(self._table, 1)

        root.addWidget(table_wrapper, 1)

        # ── WoRMS job panel (mirrors renderTaxonJobPanel in app.js) ──────────
        self._job_panel_frame = QFrame()
        self._job_panel_frame.setObjectName("TaxonJobPanel")
        self._job_panel_frame.setStyleSheet(
            "QFrame#TaxonJobPanel { background: #0d2630;"
            " border: 1px solid rgba(145,182,181,0.18); border-radius: 6px;"
            " margin: 4px 24px; }"
        )
        _jpanel_layout = QHBoxLayout(self._job_panel_frame)
        _jpanel_layout.setContentsMargins(12, 8, 12, 8)
        _jpanel_layout.setSpacing(12)
        self._job_title_label = QLabel("WoRMS 任务")
        self._job_title_label.setStyleSheet("font-weight: 600; color: #29b9ab; font-size: 12px; background: transparent;")
        _jpanel_layout.addWidget(self._job_title_label)
        self._job_progress_label = QLabel("")
        self._job_progress_label.setStyleSheet("color: #87a2a1; font-size: 11px; background: transparent;")
        _jpanel_layout.addWidget(self._job_progress_label)
        self._job_bar = QProgressBar()
        self._job_bar.setFixedHeight(8)
        self._job_bar.setTextVisible(False)
        self._job_bar.setStyleSheet(
            "QProgressBar { background: #061c1e; border: none; border-radius: 4px; }"
            "QProgressBar::chunk { background: #29b9ab; border-radius: 4px; }"
        )
        self._job_bar.setFixedWidth(120)
        _jpanel_layout.addWidget(self._job_bar)
        self._job_counts_label = QLabel("")
        self._job_counts_label.setStyleSheet("color: #87a2a1; font-size: 11px; background: transparent;")
        _jpanel_layout.addWidget(self._job_counts_label)
        self._btn_job_pause = QPushButton("暂停")
        self._btn_job_pause.setObjectName("Outline")
        self._btn_job_pause.setFixedHeight(24)
        self._btn_job_pause.hide()
        _jpanel_layout.addWidget(self._btn_job_pause)
        self._btn_job_resume = QPushButton("继续")
        self._btn_job_resume.setObjectName("Outline")
        self._btn_job_resume.setFixedHeight(24)
        self._btn_job_resume.hide()
        _jpanel_layout.addWidget(self._btn_job_resume)
        self._btn_job_retry = QPushButton("重试失败")
        self._btn_job_retry.setObjectName("Outline")
        self._btn_job_retry.setFixedHeight(24)
        self._btn_job_retry.hide()
        _jpanel_layout.addWidget(self._btn_job_retry)
        _jpanel_layout.addStretch()
        self._job_panel_frame.hide()
        root.addWidget(self._job_panel_frame)

        # ── Pager (taxon-table-pager) ─────────────────────────────────
        pager_frame = QFrame()
        pager_frame.setObjectName("TaxonPager")
        pager_frame.setStyleSheet(
            "QFrame#TaxonPager { border: none;"
            " border-top: 1px solid rgba(145,182,181,0.10); }"
        )
        pager_layout = QHBoxLayout(pager_frame)
        pager_layout.setContentsMargins(24, 10, 24, 10)
        pager_layout.setSpacing(12)
        pager_frame.setFixedHeight(48)

        self._btn_prev = QPushButton("上一页")
        self._btn_prev.setObjectName("Outline")
        self._btn_prev.setFixedWidth(76)
        self._btn_prev.clicked.connect(self._on_prev_page)
        pager_layout.addWidget(self._btn_prev)

        self._page_info = QLabel("第 1 / 1 页（共 0 条）")
        self._page_info.setObjectName("TaxonPageInfo")
        self._page_info.setStyleSheet(
            "QLabel { color: #87a2a1; font-size: 12px; background: transparent; }"
        )
        pager_layout.addWidget(self._page_info)

        jump_label = QLabel("跳到")
        jump_label.setStyleSheet(
            "QLabel { color: #5f7d7a; font-size: 12px; background: transparent; }"
        )
        pager_layout.addWidget(jump_label)

        self._page_jump = QSpinBox()
        self._page_jump.setMinimum(1)
        self._page_jump.setMaximum(9999)
        self._page_jump.setValue(1)
        self._page_jump.setFixedWidth(68)
        self._page_jump.editingFinished.connect(self._on_jump_page)
        pager_layout.addWidget(self._page_jump)

        self._btn_next = QPushButton("下一页")
        self._btn_next.setObjectName("Outline")
        self._btn_next.setFixedWidth(76)
        self._btn_next.clicked.connect(self._on_next_page)
        pager_layout.addWidget(self._btn_next)

        pager_layout.addStretch()

        # stats summary at right of pager
        self._footer_label = QLabel("")
        self._footer_label.setStyleSheet(
            "QLabel { color: #5f7d7a; font-size: 11px; background: transparent; }"
        )
        pager_layout.addWidget(self._footer_label)

        root.addWidget(pager_frame)

        # ── Init service ──────────────────────────────────────────────
        self._try_init_service()

    # ── Service init ──────────────────────────────────────────────────────────

    def _try_init_service(self) -> None:
        candidates = [
            (
                Path(__file__).parent.parent.parent.parent
                / "photo-platform-ydy"
                / "prototype-photo-gui"
                / "data"
                / "taxonomy_seed.json",
                _PROJECT_ROOT / "data" / "user_taxonomy.json",
            ),
            (_DEFAULT_SEED_PATH, _DEFAULT_USER_PATH),
        ]
        for seed_p, user_p in candidates:
            if seed_p.exists():
                self._svc = TaxonomyService(seed_p, user_p)
                return
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._svc = TaxonomyService(_DEFAULT_SEED_PATH, _DEFAULT_USER_PATH)

    # ── BaseView contract ─────────────────────────────────────────────────────

    def on_activate(self) -> None:
        if self._svc:
            self._svc.reload()
        self._load_page()

    # ── View-switch ───────────────────────────────────────────────────────────

    def _on_view_switch(self, view_key: str) -> None:
        self._view = view_key
        self._page = 1
        self._selected_ids.clear()
        self._select_all_filtered = False
        self._model.clear_checked()

        # Sync button checked states (avoid recursive toggling)
        self._btn_original.blockSignals(True)
        self._btn_worms.blockSignals(True)
        self._btn_compare.blockSignals(True)
        self._btn_original.setChecked(view_key == "original")
        self._btn_worms.setChecked(view_key == "worms")
        self._btn_compare.setChecked(view_key == "compare")
        self._btn_original.blockSignals(False)
        self._btn_worms.blockSignals(False)
        self._btn_compare.blockSignals(False)

        # col controls and add/chart only in original view
        in_original = view_key == "original"
        self._col_ctrl_frame.setVisible(in_original)
        self._btn_add.setVisible(in_original)
        self._btn_chart.setVisible(in_original)

        # Action column delegate — only in original view
        action_col = _COL_DATA_START + len(self._model.columns()) + 1
        if in_original:
            self._table.setItemDelegateForColumn(action_col, self._action_delegate)
        else:
            self._table.setItemDelegateForColumn(action_col, None)

        self._load_page()

    # ── Chart toggle ──────────────────────────────────────────────────────────

    def _on_chart_toggle(self) -> None:
        """Toggle chart dialog (mirrors renderTaxonChart in app.js)."""
        self._show_chart = self._btn_chart.isChecked()
        if self._show_chart:
            self._open_chart_dialog()
        elif self._chart_dialog is not None:
            self._chart_dialog.close()

    def _chart_entries(self) -> list[tuple[str, int]]:
        """Return Top-12 order buckets (mirrors web renderTaxonChart)."""
        if self._svc is None:
            return []
        rows, total = self._svc.all_records(page=0, page_size=1_000_000)
        if len(rows) < total:
            rows, _ = self._svc.all_records(page=0, page_size=max(total, 1))
        buckets: dict[str, int] = {}
        for rec in rows:
            if self._filter_text and not self._record_matches_filter(rec):
                continue
            label = str(rec.get("orderCn") or rec.get("order") or "—").strip() or "—"
            buckets[label] = buckets.get(label, 0) + 1
        return sorted(buckets.items(), key=lambda item: (-item[1], item[0]))[:12]

    def _record_matches_filter(self, rec: dict[str, Any]) -> bool:
        needle = self._filter_text.strip().lower()
        if not needle:
            return True
        if self._filter_col:
            return needle in str(rec.get(self._filter_col, "")).lower()
        return any(needle in str(rec.get(col["key"], "")).lower() for col in _ALL_COLS)

    def _open_chart_dialog(self) -> None:
        entries = self._chart_entries()
        if self._chart_dialog is not None:
            self._chart_dialog.close()
        dlg = QDialog(self)
        dlg.setWindowTitle("分类群分布图表")
        dlg.setMinimumWidth(520)
        dlg.setMinimumHeight(360)
        dlg.finished.connect(self._on_chart_dialog_finished)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(18, 18, 18, 14)
        layout.setSpacing(12)
        title = QLabel("按目统计")
        title.setStyleSheet("font-size: 16px; font-weight: 600; color: #eef3ef;")
        layout.addWidget(title)
        subtitle = QLabel("显示当前筛选条件下数量最多的前 12 个目。")
        subtitle.setStyleSheet("font-size: 12px; color: #87a2a1;")
        layout.addWidget(subtitle)
        body = QFrame()
        body.setStyleSheet(
            "QFrame { background: #10242a; border: 1px solid rgba(145,182,181,0.14); border-radius: 8px; }"
        )
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(14, 14, 14, 14)
        body_layout.setSpacing(10)
        if entries:
            max_count = max(count for _, count in entries)
            for label, count in entries:
                row_layout = QHBoxLayout()
                row_layout.setSpacing(10)
                name = QLabel(label)
                name.setMinimumWidth(130)
                name.setStyleSheet("color: #eef3ef; font-size: 12px;")
                row_layout.addWidget(name)
                bar = QProgressBar()
                bar.setRange(0, max_count)
                bar.setValue(count)
                bar.setTextVisible(False)
                bar.setFixedHeight(12)
                bar.setStyleSheet(
                    "QProgressBar { background: #061c1e; border: none; border-radius: 6px; }"
                    "QProgressBar::chunk { background: #29b9ab; border-radius: 6px; }"
                )
                row_layout.addWidget(bar, 1)
                value = QLabel(str(count))
                value.setMinimumWidth(36)
                value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                value.setStyleSheet("color: #87a2a1; font-size: 12px;")
                row_layout.addWidget(value)
                body_layout.addLayout(row_layout)
        else:
            empty = QLabel("暂无可统计记录")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: #87a2a1; padding: 28px;")
            body_layout.addWidget(empty)
        layout.addWidget(body, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dlg.close)
        layout.addWidget(buttons)
        self._chart_dialog = dlg
        dlg.show()

    def _on_chart_dialog_finished(self) -> None:
        self._chart_dialog = None
        self._show_chart = False
        if hasattr(self, "_btn_chart"):
            self._btn_chart.blockSignals(True)
            self._btn_chart.setChecked(False)
            self._btn_chart.blockSignals(False)

    # ── Column chip handlers ──────────────────────────────────────────────────

    def _on_level_chip(self, level_key: str, show: bool) -> None:
        self._model.set_vis_level(level_key, show)
        self._update_action_delegate_column()

    def _on_lang_chip(self, lang_key: str, show: bool) -> None:
        self._model.set_vis_lang(lang_key, show)
        self._update_action_delegate_column()

    def _update_action_delegate_column(self) -> None:
        """Re-attach delegate to the correct 操作 column after column visibility change."""
        if self._view != "original":
            return
        action_col = _COL_DATA_START + len(self._model.columns()) + 1
        self._table.setItemDelegateForColumn(action_col, self._action_delegate)
        self._table.resizeColumnToContents(action_col)

    # ── Search / filter ───────────────────────────────────────────────────────

    def _on_search(self) -> None:
        self._filter_text = self._search_input.text().strip()
        self._filter_col = self._col_select.currentData() or ""
        self._page = 1
        self._selected_ids.clear()
        self._select_all_filtered = False
        self._model.clear_checked()
        self._load_page()

    def _on_clear_filter(self) -> None:
        self._search_input.clear()
        self._col_select.setCurrentIndex(0)
        self._filter_text = ""
        self._filter_col = ""
        self._page = 1
        self._selected_ids.clear()
        self._select_all_filtered = False
        self._model.clear_checked()
        self._filter_active_label.hide()
        self._load_page()

    # ── Pagination ────────────────────────────────────────────────────────────

    def _on_prev_page(self) -> None:
        if self._page > 1:
            self._page -= 1
            self._load_page()

    def _on_next_page(self) -> None:
        total_pages = max(1, (self._total + _PAGE_SIZE - 1) // _PAGE_SIZE)
        if self._page < total_pages:
            self._page += 1
            self._load_page()

    def _on_jump_page(self) -> None:
        p = self._page_jump.value()
        total_pages = max(1, (self._total + _PAGE_SIZE - 1) // _PAGE_SIZE)
        p = max(1, min(p, total_pages))
        if p != self._page:
            self._page = p
            self._load_page()

    # ── Data loading ──────────────────────────────────────────────────────────

    def _load_page(self) -> None:
        if self._svc is None:
            return

        self._loading = True
        self._loading_label.show()
        self._table.hide()

        # Fetch all records for client-side filtering and sorting
        source_filter = None
        if self._view == "worms":
            source_filter = "seed"

        _fetch_size = max(self._svc.seed_count() + self._svc.user_count(), _PAGE_SIZE)
        all_recs, _ = self._svc.all_records(source_filter=source_filter, page=0, page_size=_fetch_size)

        # Apply text filter client-side
        if self._filter_text:
            q = self._filter_text.lower()
            col_key = self._filter_col
            if col_key:
                all_recs = [r for r in all_recs if q in str(r.get(col_key, "")).lower()]
            else:
                all_recs = [r for r in all_recs if any(q in str(v).lower() for v in r.values())]

        # Apply per-column facet filters (mirrors web taxon filter predicates)
        for col_key, pred in self._col_filters.items():
            if not pred:
                continue
            mode = pred.get("mode", "all")
            if mode == "all":
                continue
            if mode == "include":
                include_vals = set(pred.get("values") or [])
                all_recs = [r for r in all_recs if str(r.get(col_key, "")) in include_vals]
            elif mode == "exclude":
                excluded = set(pred.get("excluded") or [])
                all_recs = [r for r in all_recs if str(r.get(col_key, "")) not in excluded]
            elif mode == "search":
                sq = (pred.get("search") or "").lower()
                excluded = set(pred.get("excluded") or [])
                all_recs = [
                    r for r in all_recs
                    if (not sq or sq in str(r.get(col_key, "")).lower())
                    and str(r.get(col_key, "")) not in excluded
                ]

        # Apply column sort
        if self._sort_col:
            all_recs = sorted(
                all_recs,
                key=lambda r: str(r.get(self._sort_col, "")).lower(),
                reverse=(self._sort_dir == "desc"),
            )

        self._total = len(all_recs)

        # Update filter active label
        has_active_filter = bool(self._filter_text) or bool(self._col_filters)
        if has_active_filter:
            self._filter_active_label.setText(f"已筛选 {self._total} 条")
            self._filter_active_label.show()
        else:
            self._filter_active_label.hide()

        # Client-side pagination
        page_offset = (self._page - 1) * _PAGE_SIZE
        records = all_recs[page_offset : page_offset + _PAGE_SIZE]

        self._model.set_records(records, page_offset=page_offset)

        # Re-attach action delegate after model reset (column count may change)
        if self._view == "original":
            action_col = _COL_DATA_START + len(self._model.columns()) + 1
            self._table.setItemDelegateForColumn(action_col, self._action_delegate)

        self._loading = False
        self._loading_label.hide()
        self._table.show()

        self._update_pager()
        self._update_selection_note()

        seed_n = self._svc.seed_count()
        user_n = self._svc.user_count()
        self._stats_label.setText(f"共 {self._total} 条")
        self._footer_label.setText(f"种子库 {seed_n} 条 | 用户 {user_n} 条")

        # Refresh WoRMS job panel (mirrors renderTaxonJobPanel in web)
        self._refresh_job_panel()

    def _update_pager(self) -> None:
        total_pages = max(1, (self._total + _PAGE_SIZE - 1) // _PAGE_SIZE)
        self._page_info.setText(
            f"第 {self._page} / {total_pages} 页（共 {self._total} 条）"
        )
        self._page_jump.setMaximum(total_pages)
        self._page_jump.setValue(self._page)
        self._btn_prev.setEnabled(self._page > 1)
        self._btn_next.setEnabled(self._page < total_pages)

    def _update_selection_note(self) -> None:
        checked_ids = self._model.checked_ids()
        if self._select_all_filtered:
            note = f"已选择全部筛选结果（{self._total} 条）"
        else:
            n = len(checked_ids) or len(self._selected_ids)
            note = f"已选 {n} 条"
        self._selection_note.setText(note)
        has_sel = bool(checked_ids or self._selected_ids) or self._select_all_filtered
        self._btn_worms_sel.setEnabled(has_sel)

    # ── Selection ─────────────────────────────────────────────────────────────

    def _on_selection_changed(self) -> None:
        idxs = self._table.selectionModel().selectedRows()
        self._selected_ids = []
        for idx in idxs:
            rec = self._model.record_at(idx.row())
            if rec and rec.get("recordId"):
                self._selected_ids.append(rec["recordId"])
        self._select_all_filtered = False
        self._update_selection_note()

    def _on_select_all_filtered(self) -> None:
        self._select_all_filtered = True
        self._model.set_all_page_checked(True)
        self._update_selection_note()

    def _on_deselect(self) -> None:
        self._selected_ids.clear()
        self._select_all_filtered = False
        self._model.clear_checked()
        self._table.clearSelection()
        self._update_selection_note()

    # ── Double-click edit ─────────────────────────────────────────────────────

    def _on_row_double_click(self, index: QModelIndex) -> None:
        if self._view != "original":
            return
        rec = self._model.record_at(index.row())
        if rec is None:
            return
        if not rec.get("recordId", "").startswith("user:"):
            QMessageBox.information(
                self, "只读",
                "种子库条目不可编辑（双击用户条目可编辑）"
            )
            return
        self._edit_record(rec)

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def _on_add(self) -> None:
        if self._svc is None:
            return
        dlg = _RecordDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._svc.learn(dlg.get_record())
        self._page = 1
        self._load_page()

    def _edit_record(self, rec: dict[str, Any]) -> None:
        if self._svc is None:
            return
        if not rec.get("recordId", "").startswith("user:"):
            QMessageBox.information(
                self, "只读",
                "种子库条目不可编辑（仅用户新增的条目支持编辑）"
            )
            return
        dlg = _RecordDialog(self, record=rec)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._svc.update(rec["recordId"], dlg.get_record())
        self._load_page()

    def _delete_record(self, rec: dict[str, Any]) -> None:
        if self._svc is None:
            return
        if not rec.get("recordId", "").startswith("user:"):
            QMessageBox.warning(self, "只读", "种子库条目不可删除")
            return
        name = f"{rec.get('species', '')} ({rec.get('class', '')})"
        reply = QMessageBox.question(
            self, "确认删除", f"删除用户条目「{name}」？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._svc.delete(rec["recordId"])
        self._load_page()

    # ── WoRMS service helpers ─────────────────────────────────────────────────

    def _worms_data_dir(self) -> Path:
        """Return data dir for WoRMS files (project-scoped or global)."""
        project_dir = getattr(self.ctx, "current_project_dir", None)
        if project_dir:
            return Path(project_dir) / "_data"
        return Path.home() / ".photo_workbench" / "data"

    def _ensure_worms_svc(self) -> Optional[WormsService]:
        """Lazily create (or re-use) the WoRMS service."""
        if self._worms_svc is not None:
            return self._worms_svc
        data_dir = self._worms_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        self._worms_svc = WormsService(
            cache_path=str(data_dir / "worms_cache.json"),
            jobs_path=str(data_dir / "worms_jobs.json"),
        )
        return self._worms_svc

    # ── Row context menu (mirrors openTaxonRowMenu / renderTaxonRowMenu) ──────

    def _on_row_context_menu(self, pos: QPoint) -> None:
        """Right-click on a row → context menu with WoRMS actions."""
        index = self._table.indexAt(pos)
        if not index.isValid():
            return
        rec = self._model.record_at(index.row())
        if rec is None:
            return

        menu = QMenu(self._table)
        menu.setStyleSheet(
            "QMenu { background: #10242a; color: #eef3ef;"
            " border: 1px solid rgba(145,182,181,0.2); border-radius: 6px; }"
            "QMenu::item { padding: 6px 18px; font-size: 12px; }"
            "QMenu::item:selected { background: rgba(41,185,171,0.15); }"
            "QMenu::separator { background: rgba(145,182,181,0.12); height: 1px; margin: 4px 0; }"
        )

        species_name = rec.get("species") or rec.get("class") or "当前记录"
        title_action = menu.addAction(species_name)
        title_action.setEnabled(False)
        menu.addSeparator()

        worms_match = menu.addAction("WoRMS 匹配当前物种")
        worms_match.triggered.connect(lambda: self._on_worms_match_row(rec))

        mapping_candidates = rec.get("mappingCandidates") or []
        if mapping_candidates:
            review_action = menu.addAction(f"审核 WoRMS 候选（{len(mapping_candidates)} 个）")
            review_action.triggered.connect(lambda: self._on_review_worms_row(rec))

        checked_ids = self._model.checked_ids() or self._selected_ids
        if len(checked_ids) > 1 and rec.get("recordId") in checked_ids:
            menu.addSeparator()
            bulk_action = menu.addAction(f"WoRMS 更新已选 {len(checked_ids)} 条")
            bulk_action.triggered.connect(lambda: self._on_worms_update(selected_only=True))

        if self._select_all_filtered:
            menu.addSeparator()
            filt_action = menu.addAction(f"WoRMS 更新全部筛选结果 {self._total} 条")
            filt_action.triggered.connect(lambda: self._on_worms_update(selected_only=False))

        is_user = rec.get("recordId", "").startswith("user:")
        if is_user:
            menu.addSeparator()
            edit_action = menu.addAction("编辑")
            edit_action.triggered.connect(lambda: self._edit_record(rec))
            del_action = menu.addAction("删除")
            del_action.triggered.connect(lambda: self._delete_record(rec))

        menu.exec(self._table.viewport().mapToGlobal(pos))

    # ── Column header facet filter (mirrors openTaxonFacetMenu) ──────────────

    def _on_header_context_menu(self, pos: QPoint) -> None:
        """Right-click on a column header → open facet filter panel."""
        logical_col = self._table.horizontalHeader().logicalIndexAt(pos)
        self._open_facet_for_column(logical_col)

    def _open_facet_for_column(self, logical_col: int) -> None:
        if self._svc is None:
            return
        data_idx = logical_col - _COL_DATA_START
        cols = self._model.columns()
        if data_idx < 0 or data_idx >= len(cols):
            return
        col_def = cols[data_idx]

        all_recs, total = self._svc.all_records(page=0, page_size=1_000_000)
        if len(all_recs) < total:
            all_recs, _ = self._svc.all_records(page=0, page_size=max(total, 1))

        current_pred = self._col_filters.get(col_def["key"])

        if self._facet_panel is not None:
            self._facet_panel.close()

        panel = _TaxonFacetPanel(
            col_def["key"],
            col_def["label"],
            all_recs,
            current_predicate=current_pred,
            parent=self,
        )
        panel.filter_applied.connect(self._on_facet_filter_applied)
        panel.sort_requested.connect(self._on_facet_sort)

        header = self._table.horizontalHeader()
        x = header.sectionViewportPosition(logical_col)
        gp = self._table.mapToGlobal(QPoint(x, header.height()))
        panel.move(gp)
        panel.show()
        panel.raise_()
        panel.activateWindow()
        self._facet_panel = panel

    def _on_facet_filter_applied(self, col_key: str, predicate: Optional[dict[str, Any]]) -> None:
        """Store facet predicate and reload."""
        if predicate is None:
            self._col_filters.pop(col_key, None)
        else:
            self._col_filters[col_key] = predicate
        self._page = 1
        self._selected_ids.clear()
        self._select_all_filtered = False
        self._model.clear_checked()
        self._facet_panel = None
        self._load_page()

    def _on_facet_sort(self, col_key: str, direction: str) -> None:
        self._sort_col = col_key
        self._sort_dir = direction
        self._page = 1
        self._load_page()

    # ── WoRMS update (mirrors startTaxonomyWormsJob in app.js) ───────────────

    def _on_worms_update(self, selected_only: bool) -> None:
        record_ids = self._worms_update_record_ids(selected_only)
        if not record_ids:
            QMessageBox.information(self, "WoRMS 更新", "没有可更新的分类条目。")
            return

        service = self._ensure_worms_svc()
        if service is None:
            return
        try:
            job = service.create_job(
                record_ids,
                source="selected" if selected_only and not self._select_all_filtered else "filtered",
            )
        except Exception as exc:
            QMessageBox.warning(self, "WoRMS 更新", f"创建任务失败：{exc}")
            return

        setattr(self.ctx, "pending_worms_job_id", job.id)
        self._navigate_to_worms()
        QMessageBox.information(
            self, "WoRMS 更新",
            f"已创建 WoRMS 批量任务 {job.id[:8]}…，共 {len(record_ids)} 条。",
        )

    def _worms_update_record_ids(self, selected_only: bool) -> list[str]:
        if self._svc is None:
            return []
        if selected_only and not self._select_all_filtered:
            ids = self._model.checked_ids() or self._selected_ids
            return list(dict.fromkeys(rid for rid in ids if rid))

        source_filter = "seed" if self._view == "worms" else None
        rows, total = self._svc.all_records(source_filter=source_filter, page=0, page_size=1_000_000)
        if len(rows) < total:
            rows, _ = self._svc.all_records(source_filter=source_filter, page=0, page_size=max(total, 1))
        ids: list[str] = []
        for idx, rec in enumerate(rows):
            if self._filter_text and not self._record_matches_filter(rec):
                continue
            ids.append(self._taxonomy_record_id(rec, idx, source_filter))
        return ids

    def _taxonomy_record_id(self, rec: dict[str, Any], index: int, source_filter: Optional[str] = None) -> str:
        rid = str(rec.get("recordId") or "").strip()
        if rid:
            return rid
        source = source_filter or ("user" if str(rec.get("recordId", "")).startswith("user:") else "seed")
        return f"{source}:{index}"

    def _navigate_to_worms(self) -> None:
        win = self.window()
        nav = getattr(win, "navigate_to", None)
        if callable(nav):
            nav("worms")
        worms_view = getattr(win, "_views", {}).get("worms") if hasattr(win, "_views") else None
        refresh = getattr(worms_view, "_refresh_jobs", None)
        if callable(refresh):
            refresh()

    # ── WoRMS match for single row (mirrors openWormsMatchModal) ─────────────

    def _on_worms_match_row(self, rec: dict[str, Any]) -> None:
        worms_svc = self._ensure_worms_svc()
        if worms_svc is None:
            QMessageBox.warning(self, "WoRMS", "WoRMS 服务不可用")
            return
        dlg = _WormsMatchDialog(rec, worms_svc, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        result = dlg.get_result()
        if result:
            self._on_resolve_mapping(rec, result)

    def _on_review_worms_row(self, rec: dict[str, Any]) -> None:
        """Open review modal for auto-found WoRMS candidates."""
        dlg = _TaxonReviewDialog(rec, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        result = dlg.get_result()
        if result:
            self._on_resolve_mapping(rec, result)

    def _on_resolve_mapping(self, rec: dict[str, Any], result: dict[str, Any]) -> None:
        """Apply WoRMS resolution decision (mirrors resolveTaxonMapping in app.js)."""
        worms_svc = self._ensure_worms_svc()
        if worms_svc is None:
            return
        record_id = rec.get("recordId", "")
        if not record_id:
            return
        try:
            if result.get("no_match"):
                worms_svc.resolve_mapping(record_id, None, no_match=True)
            else:
                aphia_id = result.get("aphia_id")
                worms_svc.resolve_mapping(
                    record_id,
                    int(aphia_id) if aphia_id else None,
                    worms_record=result.get("worms_record") or {},
                    chain=result.get("chain") or [],
                )
            QMessageBox.information(self, "WoRMS", "审核结果已保存")
        except Exception as exc:
            QMessageBox.warning(self, "WoRMS 错误", f"审核失败：{exc}")
        self._load_page()

    # ── Job panel (mirrors renderTaxonJobPanel in app.js) ─────────────────────

    def _refresh_job_panel(self) -> None:
        """Update the WoRMS job progress panel."""
        worms_svc = self._ensure_worms_svc()
        if worms_svc is None:
            self._job_panel_frame.hide()
            return

        jobs = worms_svc.list_jobs()
        if not jobs:
            self._job_panel_frame.hide()
            return

        active = next((j for j in jobs if j.get("status") in ("running", "paused")), None)
        job = active or jobs[0]
        record_ids = job.get("record_ids") or []
        total = len(record_ids)
        cursor = job.get("cursor", 0)
        status = job.get("status", "")
        source = job.get("source", "")

        _STATUS_LABELS = {"running": "运行中", "paused": "已暂停", "completed": "已完成", "cancelled": "已取消"}
        source_label = "选中条目" if source == "selected" else "筛选结果"
        self._job_title_label.setText(f"WoRMS 任务 · {source_label}")
        self._job_progress_label.setText(f"{cursor} / {total} · {_STATUS_LABELS.get(status, status)}")
        self._job_bar.setRange(0, max(total, 1))
        self._job_bar.setValue(cursor)

        counts = job.get("counts") or {}
        _COUNT_LABELS = {"matched": "匹配", "renamed": "改名", "review": "待审", "not_found": "未找到", "error": "错误", "stale": "旧缓存"}
        count_parts = [f"{_COUNT_LABELS.get(k, k)} {v}" for k, v in counts.items() if v]
        self._job_counts_label.setText("  ".join(count_parts))

        self._btn_job_pause.setVisible(status == "running")
        self._btn_job_resume.setVisible(status == "paused")
        self._btn_job_retry.setVisible(bool(counts.get("error")))
        self._job_panel_frame.show()

    # ── Export ────────────────────────────────────────────────────────────────

    def _on_export(self, fmt: str) -> None:
        if self._svc is None:
            return
        source_filter = None
        if self._view == "worms":
            source_filter = "seed"
        all_recs, _ = self._svc.all_records(
            source_filter=source_filter, page=0, page_size=999999
        )
        if fmt == "csv":
            self._export_csv(all_recs)
        else:
            self._export_xlsx(all_recs)

    def _export_csv(self, records: list[dict[str, Any]]) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 CSV", "taxonomy_export.csv", "CSV 文件 (*.csv)"
        )
        if not path:
            return
        import csv
        cols = self._model.columns()
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            header = [c["label"] for c in cols] + ["来源"]
            writer.writerow(header)
            for rec in records:
                row = [rec.get(c["key"], "") for c in cols]
                row.append("用户" if rec.get("recordId", "").startswith("user:") else "种子")
                writer.writerow(row)
        QMessageBox.information(self, "导出完成", f"已导出 {len(records)} 条到\n{path}")

    def _export_xlsx(self, records: list[dict[str, Any]]) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 Excel", "taxonomy_export.xlsx", "Excel 文件 (*.xlsx)"
        )
        if not path:
            return
        try:
            import openpyxl
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "分类库"
            cols = self._model.columns()
            ws.append([c["label"] for c in cols] + ["来源"])
            for rec in records:
                row = [rec.get(c["key"], "") for c in cols]
                row.append("用户" if rec.get("recordId", "").startswith("user:") else "种子")
                ws.append(row)
            wb.save(path)
            QMessageBox.information(self, "导出完成", f"已导出 {len(records)} 条到\n{path}")
        except ImportError:
            QMessageBox.critical(self, "缺少依赖", "需要 openpyxl 库：pip install openpyxl")
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))

    # ── Import ────────────────────────────────────────────────────────────────

    def _on_import(self) -> None:
        """Import taxonomy records from Excel or CSV.

        Mirrors the importInput handler in app.js (POST /api/taxonomy/import).
        Expected columns (case-insensitive, any order):
          class, order, family, species (required)
          classCn, orderCn, familyCn, speciesCn, genus, genusCn (optional)
        """
        if self._svc is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Excel / CSV 文件",
            "",
            "表格文件 (*.xlsx *.xls *.csv)",
        )
        if not path:
            return
        try:
            if path.lower().endswith(".csv"):
                self._import_csv(path)
            else:
                self._import_xlsx(path)
        except Exception as exc:
            QMessageBox.critical(
                self, "导入失败",
                f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"
            )

    def _import_xlsx(self, path: str) -> None:
        try:
            import openpyxl
        except ImportError:
            QMessageBox.critical(self, "缺少依赖", "需要 openpyxl 库：pip install openpyxl")
            return
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            QMessageBox.warning(self, "空文件", "Excel 文件中没有数据")
            return
        imported, skipped = self._import_rows(rows[0], rows[1:])
        self._load_page()
        QMessageBox.information(
            self, "导入完成", f"成功导入 {imported} 条，跳过 {skipped} 条。"
        )

    def _import_csv(self, path: str) -> None:
        import csv
        with open(path, encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            rows = list(reader)
        if not rows:
            QMessageBox.warning(self, "空文件", "CSV 文件中没有数据")
            return
        imported, skipped = self._import_rows(rows[0], rows[1:])
        self._load_page()
        QMessageBox.information(
            self, "导入完成", f"成功导入 {imported} 条，跳过 {skipped} 条。"
        )

    def _import_rows(
        self,
        header_row: Any,
        data_rows: list[Any],
    ) -> tuple[int, int]:
        _alias: dict[str, str] = {
            "class":     "class",   "纲":    "class",
            "order":     "order",   "目":    "order",
            "family":    "family",  "科":    "family",
            "species":   "species", "种":    "species",
            "classcn":   "classCn",  "纲中文": "classCn",
            "ordercn":   "orderCn",  "目中文": "orderCn",
            "familycn":  "familyCn", "科中文": "familyCn",
            "speciescn": "speciesCn","种中文": "speciesCn",
            "genus":     "genus",    "属":    "genus",
            "genuscn":   "genusCn",  "属中文": "genusCn",
        }

        col_map: dict[str, int] = {}
        for i, h in enumerate(header_row):
            if h:
                col_map[str(h).strip().lower()] = i

        field_idx: dict[str, int] = {}
        for raw, canon in _alias.items():
            if raw in col_map:
                field_idx[canon] = col_map[raw]

        def _cell(row: Any, field: str) -> str:
            i = field_idx.get(field)
            if i is None:
                return ""
            try:
                v = row[i]
            except IndexError:
                return ""
            return str(v).strip() if v is not None else ""

        imported = skipped = 0
        for row in data_rows:
            rec = {f: _cell(row, f) for f in (
                "class", "order", "family", "species",
                "classCn", "orderCn", "familyCn", "speciesCn",
                "genus", "genusCn",
            )}
            result = self._svc.learn(rec)  # type: ignore[union-attr]
            if result:
                imported += 1
            else:
                skipped += 1
        return imported, skipped
