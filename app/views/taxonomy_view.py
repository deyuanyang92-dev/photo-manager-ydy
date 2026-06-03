"""taxonomy_view.py — Taxonomy library management view.

Provides:
  - A paginated, filterable table of all taxonomy records (seed + user)
  - Add / Edit / Delete for user records (seed records are read-only)
  - Excel import via openpyxl

view_id   = "taxonomy"
nav_title = "分类库"
nav_icon  = "🧬"

Oracle: server.js:353-730, app.js taxon table render (~line 9884),
        taxonomy_service.py
"""
from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any, Optional

from PyQt6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
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
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.views.base_view import BaseView
from app.services.taxonomy_service import TaxonomyService

if False:  # TYPE_CHECKING
    from app.app_context import AppContext

# ── Paths — resolved relative to project data directory ──────────────────────
# When running from the v3 project, data files sit alongside the web prototype.
# The service paths are resolved at view construction from ctx.settings or a
# sensible default (adjacent data/ directory relative to the package root).
_HERE = Path(__file__).resolve().parent            # app/views/
_PROJECT_ROOT = _HERE.parent.parent                # photo-platform-ydy-v3/
_DATA_DIR = _PROJECT_ROOT / "data"

_DEFAULT_SEED_PATH = _DATA_DIR / "taxonomy_seed.json"
_DEFAULT_USER_PATH = _DATA_DIR / "user_taxonomy.json"


# ── Table model ───────────────────────────────────────────────────────────────

_COLUMNS = [
    ("纲/门",     "class"),
    ("目",       "order"),
    ("科",       "family"),
    ("种",       "species"),
    ("中文名",   "speciesCn"),
    ("属",       "genus"),
    ("来源",     "_source"),
    ("使用次数",  "useCount"),
]


class _TaxonTableModel(QAbstractTableModel):
    """Table model backed by merged taxonomy records."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._records: list[dict[str, Any]] = []

    def set_records(self, records: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self._records = list(records)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._records)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(_COLUMNS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(_COLUMNS):
            return _COLUMNS[section][0]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if row >= len(self._records):
            return None
        rec = self._records[row]
        if role == Qt.ItemDataRole.DisplayRole:
            col_key = _COLUMNS[col][1]
            if col_key == "_source":
                return "用户" if rec.get("recordId", "").startswith("user:") else "权威"
            val = rec.get(col_key, "")
            return str(val) if val else ""
        if role == Qt.ItemDataRole.UserRole:
            return rec
        if role == Qt.ItemDataRole.ForegroundRole:
            from PyQt6.QtGui import QColor
            is_user = rec.get("recordId", "").startswith("user:")
            return QColor("#60a5fa") if is_user else QColor("#94a3b8")
        return None

    def record_at(self, row: int) -> Optional[dict[str, Any]]:
        if 0 <= row < len(self._records):
            return self._records[row]
        return None


# ── Add/Edit dialog ───────────────────────────────────────────────────────────

class _RecordDialog(QDialog):
    """Simple form dialog for adding or editing a user taxonomy record."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        record: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑分类记录" if record else "新增分类记录")
        self.setMinimumWidth(400)
        self._record = record or {}
        self._inputs: dict[str, QLineEdit] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setSpacing(8)

        fields = [
            ("class",     "纲 / 门（Latin）"),
            ("order",     "目（Latin）"),
            ("family",    "科（Latin）"),
            ("species",   "种（Latin）"),
            ("classCn",   "纲中文"),
            ("orderCn",   "目中文"),
            ("familyCn",  "科中文"),
            ("speciesCn", "种中文"),
            ("genus",     "属（Latin）"),
            ("genusCn",   "属中文"),
        ]

        for key, label in fields:
            inp = QLineEdit()
            inp.setText(self._record.get(key, ""))
            if key in ("class", "order", "family", "species"):
                inp.setPlaceholderText("必填")
            form.addRow(label, inp)
            self._inputs[key] = inp

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        for req in ("class", "order", "family", "species"):
            if not self._inputs[req].text().strip():
                QMessageBox.warning(self, "必填项", f'"{req}" 不能为空')
                return
        self.accept()

    def get_record(self) -> dict[str, Any]:
        """Return the edited field values."""
        return {k: inp.text().strip() for k, inp in self._inputs.items()}


# ── Main view ─────────────────────────────────────────────────────────────────

class TaxonomyView(BaseView):
    """Taxonomy library management view (view_id="taxonomy").

    Layout:
        ┌─ toolbar ────────────────────────────────────┐
        │  [Filter...] [来源▼] [Reload] [Add] [Delete] │
        │  [Import Excel]                              │
        ├─ table ──────────────────────────────────────┤
        │  纲 | 目 | 科 | 种 | 中文名 | 属 | 来源 | ✦ │
        ├─ footer ─────────────────────────────────────┤
        │  共 N 条（种子库 M | 用户 K）                 │
        └──────────────────────────────────────────────┘
    """

    view_id = "taxonomy"
    nav_title = "内置分类库"
    nav_icon = "🧬"

    def __init__(self, ctx: "AppContext") -> None:
        self._svc: Optional[TaxonomyService] = None
        super().__init__(ctx)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 8)
        layout.setSpacing(8)

        # ── Header ────────────────────────────────────────────────────
        header = QLabel("🧬 分类库")
        header.setObjectName("ViewTitle")
        header.setStyleSheet("font-size:16px; font-weight:600; color:#e2e8f0;")
        layout.addWidget(header)

        # ── Toolbar ───────────────────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("过滤（纲/目/科/种/中文）…")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        toolbar.addWidget(self._filter_edit)

        self._source_combo = QComboBox()
        self._source_combo.addItems(["全部", "用户", "权威"])
        self._source_combo.setFixedWidth(80)
        toolbar.addWidget(self._source_combo)

        btn_reload = QPushButton("刷新")
        btn_reload.setFixedWidth(56)
        btn_reload.clicked.connect(self._reload)
        toolbar.addWidget(btn_reload)

        toolbar.addSpacing(8)

        btn_add = QPushButton("新增")
        btn_add.setObjectName("Primary")
        btn_add.setFixedWidth(56)
        btn_add.clicked.connect(self._on_add)
        toolbar.addWidget(btn_add)

        btn_edit = QPushButton("编辑")
        btn_edit.setFixedWidth(56)
        btn_edit.clicked.connect(self._on_edit)
        toolbar.addWidget(btn_edit)

        btn_delete = QPushButton("删除")
        btn_delete.setObjectName("Danger")
        btn_delete.setFixedWidth(56)
        btn_delete.clicked.connect(self._on_delete)
        toolbar.addWidget(btn_delete)

        btn_import = QPushButton("导入 Excel")
        btn_import.setFixedWidth(90)
        btn_import.clicked.connect(self._on_import)
        toolbar.addWidget(btn_import)

        layout.addLayout(toolbar)

        # ── Table ──────────────────────────────────────────────────────
        self._model = _TaxonTableModel(self)
        self._proxy = QSortFilterProxyModel(self)
        self._proxy.setSourceModel(self._model)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._proxy.setFilterKeyColumn(-1)   # search all columns

        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self._table.setStyleSheet(
            "QTableView { background:#1a2535; color:#e2e8f0; gridline-color:#334155;"
            " selection-background-color:#1e3a5f; border:none; }"
            "QHeaderView::section { background:#0f1926; color:#94a3b8;"
            " padding:4px 8px; border:none; border-right:1px solid #334155; }"
            "QTableView::item:alternate { background:#1f2d40; }"
        )
        layout.addWidget(self._table)

        # ── Footer ────────────────────────────────────────────────────
        self._footer = QLabel("正在加载…")
        self._footer.setStyleSheet("color:#64748b; font-size:11px;")
        layout.addWidget(self._footer)

        # ── Connect filter / source ────────────────────────────────────
        self._filter_edit.textChanged.connect(self._apply_filter)
        self._source_combo.currentIndexChanged.connect(self._apply_filter)

        # ── Service (may be None until project data dir is set) ────────
        self._try_init_service()

    def _try_init_service(self) -> None:
        """Initialise TaxonomyService if data files are accessible."""
        # Prefer files adjacent to the web prototype's data/ directory
        # (when running in the v3 project directory).
        # Fall back to a local data/ folder.
        candidates = [
            (
                Path(__file__).parent.parent.parent.parent
                / "photo-platform-ydy"
                / "prototype-photo-gui"
                / "data"
                / "taxonomy_seed.json",
                Path(__file__).parent.parent.parent
                / "data"
                / "user_taxonomy.json",
            ),
            (_DEFAULT_SEED_PATH, _DEFAULT_USER_PATH),
        ]
        for seed_p, user_p in candidates:
            if seed_p.exists():
                self._svc = TaxonomyService(seed_p, user_p)
                return

        # No seed file found — create an empty service in the local data/ dir
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._svc = TaxonomyService(_DEFAULT_SEED_PATH, _DEFAULT_USER_PATH)

    # ── BaseView contract ──────────────────────────────────────────────

    def on_activate(self) -> None:
        """Refresh the table each time the user navigates to this view."""
        self._reload()

    # ── Data loading ───────────────────────────────────────────────────

    def _reload(self) -> None:
        if self._svc is None:
            self._footer.setText("分类服务未初始化")
            return
        self._svc.reload()
        self._load_page()

    def _load_page(self) -> None:
        if self._svc is None:
            return
        source_idx = self._source_combo.currentIndex()
        source_filter = {0: None, 1: "user", 2: "seed"}.get(source_idx)
        records, total = self._svc.all_records(source_filter=source_filter, page=0, page_size=5000)
        self._model.set_records(records)
        # Update filter
        self._apply_filter()

        seed_n = self._svc.seed_count()
        user_n = self._svc.user_count()
        self._footer.setText(
            f"共 {total} 条（种子库 {seed_n} | 用户 {user_n}）"
        )

    def _apply_filter(self) -> None:
        text = self._filter_edit.text().strip()
        self._proxy.setFilterFixedString(text)
        # Re-fetch if source filter changed
        source_idx = self._source_combo.currentIndex()
        source_filter = {0: None, 1: "user", 2: "seed"}.get(source_idx)
        if self._svc:
            records, total = self._svc.all_records(
                source_filter=source_filter, page=0, page_size=5000
            )
            self._model.set_records(records)
            seed_n = self._svc.seed_count()
            user_n = self._svc.user_count()
            self._footer.setText(
                f"共 {total} 条（种子库 {seed_n} | 用户 {user_n}）"
            )

    # ── CRUD ───────────────────────────────────────────────────────────

    def _on_add(self) -> None:
        if self._svc is None:
            return
        dlg = _RecordDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        rec = dlg.get_record()
        self._svc.learn(rec)
        self._load_page()

    def _on_edit(self) -> None:
        if self._svc is None:
            return
        rec = self._selected_record()
        if rec is None:
            QMessageBox.information(self, "提示", "请先选择一条记录")
            return
        if not rec.get("recordId", "").startswith("user:"):
            QMessageBox.warning(self, "只读", "种子库记录不可编辑")
            return
        dlg = _RecordDialog(self, record=rec)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        updates = dlg.get_record()
        self._svc.update(rec["recordId"], updates)
        self._load_page()

    def _on_delete(self) -> None:
        if self._svc is None:
            return
        rec = self._selected_record()
        if rec is None:
            QMessageBox.information(self, "提示", "请先选择一条记录")
            return
        if not rec.get("recordId", "").startswith("user:"):
            QMessageBox.warning(self, "只读", "种子库记录不可删除")
            return
        name = f"{rec.get('species', '')} ({rec.get('class', '')})"
        reply = QMessageBox.question(
            self,
            "确认删除",
            f'删除用户记录 "{name}"？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._svc.delete(rec["recordId"])
        self._load_page()

    def _selected_record(self) -> Optional[dict[str, Any]]:
        idx = self._table.currentIndex()
        if not idx.isValid():
            return None
        src_idx = self._proxy.mapToSource(idx)
        return self._model.record_at(src_idx.row())

    # ── Excel import ───────────────────────────────────────────────────

    def _on_import(self) -> None:
        """Import taxonomy records from an Excel file.

        Expected columns (case-insensitive, any order):
          class, order, family, species (required)
          classCn, orderCn, familyCn, speciesCn, genus, genusCn (optional)
        """
        if self._svc is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Excel 文件",
            "",
            "Excel 文件 (*.xlsx *.xls)",
        )
        if not path:
            return
        try:
            import openpyxl  # type: ignore[import-untyped]

            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            ws = wb.active

            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                QMessageBox.warning(self, "空文件", "Excel 文件中没有数据")
                return

            # First row = header
            header_row = rows[0]
            col_map: dict[str, int] = {}
            for i, h in enumerate(header_row):
                if h:
                    col_map[str(h).strip().lower()] = i

            # Canonical field name mapping
            _alias: dict[str, str] = {
                "class": "class", "纲": "class",
                "order": "order", "目": "order",
                "family": "family", "科": "family",
                "species": "species", "种": "species",
                "classcn": "classCn", "纲中文": "classCn",
                "ordercn": "orderCn", "目中文": "orderCn",
                "familycn": "familyCn", "科中文": "familyCn",
                "speciescn": "speciesCn", "种中文": "speciesCn",
                "genus": "genus", "属": "genus",
                "genuscn": "genusCn", "属中文": "genusCn",
            }

            field_idx: dict[str, int] = {}
            for raw, canon in _alias.items():
                if raw in col_map:
                    field_idx[canon] = col_map[raw]

            imported = 0
            skipped = 0
            for row in rows[1:]:
                def _cell(field: str) -> str:
                    i = field_idx.get(field)
                    if i is None or i >= len(row):
                        return ""
                    v = row[i]
                    return str(v).strip() if v is not None else ""

                rec = {f: _cell(f) for f in (
                    "class", "order", "family", "species",
                    "classCn", "orderCn", "familyCn", "speciesCn",
                    "genus", "genusCn",
                )}
                result = self._svc.learn(rec)
                if result:
                    imported += 1
                else:
                    skipped += 1

            self._load_page()
            QMessageBox.information(
                self,
                "导入完成",
                f"成功导入 {imported} 条，跳过不完整 {skipped} 条。",
            )
        except ImportError:
            QMessageBox.critical(self, "缺少依赖", "需要 openpyxl 库。请运行: pip install openpyxl")
        except Exception as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
