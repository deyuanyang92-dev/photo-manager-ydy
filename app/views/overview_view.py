"""overview_view.py — Project overview: specimen table + statistics + export.

Displays all specimens in the current project with:
  - Top stat bar  (total / named / archived counts)
  - Column selector + text filter
  - QTableWidget listing specimens
  - Export buttons (Excel / CSV / Darwin Core)

Oracle:
  - app.js:13856-14026 (renderOverview / renderSpecimenTable)
  - server.js:595-721  (export columns)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QSortFilterProxyModel, QTimer
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.models.specimen import Specimen
from app.services.export_service import COLUMN_HEADERS, export_csv, export_darwin_core, export_excel
from app.views.base_view import BaseView

if __name__ == "__main__":
    from app.app_context import AppContext  # noqa: F401  (import guard for type hint)


# ── Stat card widget ───────────────────────────────────────────────────────────

class _StatCard(QFrame):
    """Compact stat card: big number + label + hint."""

    def __init__(self, value: str | int, label: str, hint: str = "") -> None:
        super().__init__()
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)

        self._value_lbl = QLabel(str(value))
        self._value_lbl.setStyleSheet(
            "font-size: 22px; font-weight: 700; color: #29b9ab; background: transparent;"
        )
        self._value_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._label_lbl = QLabel(label)
        self._label_lbl.setObjectName("Muted")
        self._label_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._hint_lbl = QLabel(hint)
        self._hint_lbl.setObjectName("Muted")
        self._hint_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint_lbl.setStyleSheet(
            "font-size: 11px; opacity: 0.7; background: transparent;"
        )

        layout.addWidget(self._value_lbl)
        layout.addWidget(self._label_lbl)
        if hint:
            layout.addWidget(self._hint_lbl)

    def update_value(self, value: str | int) -> None:
        self._value_lbl.setText(str(value))


# ── Column selector dialog ─────────────────────────────────────────────────────

class _ColumnSelectorDialog(QDialog):
    """Modal dialog for choosing which columns to show in the table."""

    def __init__(self, visible_headers: list[str], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择显示列")
        self.setMinimumWidth(340)
        self._checks: dict[str, QCheckBox] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(6)

        lbl = QLabel("选择要在表格中显示的列：")
        lbl.setObjectName("Section")
        layout.addWidget(lbl)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(420)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setSpacing(4)
        inner_layout.setContentsMargins(4, 4, 4, 4)

        for header in COLUMN_HEADERS:
            cb = QCheckBox(header)
            cb.setChecked(header in visible_headers)
            inner_layout.addWidget(cb)
            self._checks[header] = cb

        inner_layout.addStretch()
        scroll.setWidget(inner)
        layout.addWidget(scroll)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def selected_headers(self) -> list[str]:
        """Return ordered list of checked column headers."""
        return [h for h in COLUMN_HEADERS if self._checks[h].isChecked()]


# ── Main overview view ─────────────────────────────────────────────────────────

#: Default columns shown in the table (subset of all 34)
_DEFAULT_VISIBLE = [
    "标本唯一编号",
    "物种拼音编号",
    "物种中名",
    "物种拉丁名",
    "科拉丁名",
    "省份代码",
    "样地代码",
    "站位",
    "保存方式代码",
    "采集日期",
    "经度",
    "纬度",
    "分类完整",
    "Metadata完整度(%)",
    "备注",
]


class OverviewView(BaseView):
    """项目总览 — specimen list, statistics, and export.

    Displays the current project's specimens in a sortable/filterable table.
    Export actions trigger file-save dialogs and call export_service functions.
    """

    view_id = "overview"
    nav_title = "项目总览"
    nav_icon = "📊"

    def __init__(self, ctx: "AppContext") -> None:  # noqa: F821
        self._visible_headers: list[str] = list(_DEFAULT_VISIBLE)
        self._specimens: list[Specimen] = []
        self._model: Optional[QStandardItemModel] = None
        self._proxy: Optional[QSortFilterProxyModel] = None
        super().__init__(ctx)

    # ── BaseView contract ──────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # ── Page title + action buttons ────────────────────────────────────────
        title_row = QHBoxLayout()
        title_lbl = QLabel("项目总览")
        title_lbl.setObjectName("Title")
        title_row.addWidget(title_lbl)
        title_row.addStretch()

        self._btn_cols = QPushButton("选择列")
        self._btn_cols.setToolTip("选择在表格中显示的列")
        self._btn_cols.clicked.connect(self._open_column_selector)
        title_row.addWidget(self._btn_cols)

        self._btn_excel = QPushButton("导出 Excel")
        self._btn_excel.setObjectName("Primary")
        self._btn_excel.setToolTip("将当前项目所有标本导出为 Excel (.xlsx)")
        self._btn_excel.clicked.connect(self._export_excel)
        title_row.addWidget(self._btn_excel)

        self._btn_csv = QPushButton("导出 CSV")
        self._btn_csv.setToolTip("将当前项目所有标本导出为 CSV")
        self._btn_csv.clicked.connect(self._export_csv)
        title_row.addWidget(self._btn_csv)

        self._btn_dwc = QPushButton("导出 DwC")
        self._btn_dwc.setToolTip("导出 Darwin Core 格式 CSV（来自 darwin_core 视图）")
        self._btn_dwc.clicked.connect(self._export_dwc)
        title_row.addWidget(self._btn_dwc)

        root.addLayout(title_row)

        # ── Stat bar ───────────────────────────────────────────────────────────
        stat_row = QHBoxLayout()
        stat_row.setSpacing(12)
        self._stat_total = _StatCard("—", "标本数", "当前项目")
        self._stat_named = _StatCard("—", "已命名", "scientific_name 非空")
        self._stat_archived = _StatCard("—", "已归档", "有 archive_zip 记录")
        for card in (self._stat_total, self._stat_named, self._stat_archived):
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            stat_row.addWidget(card)
        root.addLayout(stat_row)

        # ── Filter bar ─────────────────────────────────────────────────────────
        filter_row = QHBoxLayout()
        filter_lbl = QLabel("过滤：")
        filter_lbl.setObjectName("Muted")
        self._filter_input = QLineEdit()
        self._filter_input.setPlaceholderText("输入 UID / 物种名 / 采集人…")
        self._filter_input.setClearButtonEnabled(True)
        self._filter_input.textChanged.connect(self._on_filter_changed)
        filter_row.addWidget(filter_lbl)
        filter_row.addWidget(self._filter_input)
        root.addLayout(filter_row)

        # ── Table ──────────────────────────────────────────────────────────────
        self._table = QTableView()
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self._table, stretch=1)

        # ── Status label ────────────────────────────────────────────────────────
        self._status_lbl = QLabel("尚未加载项目")
        self._status_lbl.setObjectName("Muted")
        root.addWidget(self._status_lbl)

        self._rebuild_model()

    def on_activate(self) -> None:
        """Reload specimens from DB each time the user navigates here."""
        self._load_specimens()

    # ── Data loading ───────────────────────────────────────────────────────────

    def _load_specimens(self) -> None:
        """Query specimens from the current project's SQLite DB."""
        self._specimens = []

        db: Optional[sqlite3.Connection] = self.ctx.get_db()
        if db is None:
            self._status_lbl.setText("未打开项目")
            self._update_stats()
            self._rebuild_model()
            return

        try:
            rows = db.execute("SELECT * FROM specimens ORDER BY uid").fetchall()
            self._specimens = [Specimen.from_row(r) for r in rows]
        except Exception as exc:  # pragma: no cover
            self._status_lbl.setText(f"数据库查询错误：{exc}")
            self._specimens = []

        self._update_stats()
        self._rebuild_model()
        n = len(self._specimens)
        self._status_lbl.setText(f"已加载 {n} 条标本记录")

    def _update_stats(self) -> None:
        """Refresh the three stat cards from current specimen list."""
        total = len(self._specimens)
        named = sum(1 for s in self._specimens if s.scientific_name)

        # "已归档" = has at least one grouping row with archive_zip set
        archived = 0
        db: Optional[sqlite3.Connection] = self.ctx.get_db()
        if db is not None and total > 0:
            try:
                row = db.execute(
                    "SELECT COUNT(DISTINCT uid) FROM grouping WHERE archive_zip IS NOT NULL AND archive_zip != ''"
                ).fetchone()
                archived = row[0] if row else 0
            except Exception:
                archived = 0

        self._stat_total.update_value(total)
        self._stat_named.update_value(named)
        self._stat_archived.update_value(archived)

    # ── Table model ────────────────────────────────────────────────────────────

    def _rebuild_model(self) -> None:
        """Rebuild QStandardItemModel from current specimen list and visible headers."""
        from app.services.export_service import COLUMNS  # local import avoids circular risk

        col_map = {h: acc for h, acc in COLUMNS}
        active_cols = [(h, col_map[h]) for h in self._visible_headers if h in col_map]

        model = QStandardItemModel(len(self._specimens), len(active_cols))
        model.setHorizontalHeaderLabels([h for h, _ in active_cols])

        for row_idx, sp in enumerate(self._specimens):
            for col_idx, (_h, accessor) in enumerate(active_cols):
                try:
                    val = accessor(sp)
                except Exception:
                    val = ""
                item = QStandardItem(str(val) if val is not None else "")
                item.setEditable(False)
                model.setItem(row_idx, col_idx, item)

        proxy = QSortFilterProxyModel()
        proxy.setSourceModel(model)
        proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        proxy.setFilterKeyColumn(-1)  # search all columns

        self._model = model
        self._proxy = proxy
        self._table.setModel(proxy)
        self._table.resizeColumnsToContents()

        # Re-apply any existing filter text
        if self._filter_input.text():
            proxy.setFilterFixedString(self._filter_input.text())

    # ── Column selector ────────────────────────────────────────────────────────

    def _open_column_selector(self) -> None:
        dlg = _ColumnSelectorDialog(self._visible_headers, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            selected = dlg.selected_headers()
            if not selected:
                QMessageBox.warning(self, "选择列", "至少选择一列。")
                return
            self._visible_headers = selected
            self._rebuild_model()

    # ── Filter ─────────────────────────────────────────────────────────────────

    def _on_filter_changed(self, text: str) -> None:
        if self._proxy is not None:
            self._proxy.setFilterFixedString(text)

    # ── Export actions ─────────────────────────────────────────────────────────

    def _export_excel(self) -> None:
        """Prompt for save path and export specimens to Excel."""
        if not self._specimens:
            QMessageBox.information(self, "导出 Excel", "当前项目没有标本数据。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 Excel",
            f"标本数据_{self._today()}.xlsx",
            "Excel 文件 (*.xlsx)",
        )
        if not path:
            return
        try:
            out = export_excel(self._specimens, path)
            QMessageBox.information(self, "导出成功", f"已保存到：\n{out}")
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))

    def _export_csv(self) -> None:
        """Prompt for save path and export specimens to CSV."""
        if not self._specimens:
            QMessageBox.information(self, "导出 CSV", "当前项目没有标本数据。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 CSV",
            f"标本数据_{self._today()}.csv",
            "CSV 文件 (*.csv)",
        )
        if not path:
            return
        try:
            out = export_csv(self._specimens, path)
            QMessageBox.information(self, "导出成功", f"已保存到：\n{out}")
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))

    def _export_dwc(self) -> None:
        """Prompt for save path and export darwin_core view to CSV."""
        db: Optional[sqlite3.Connection] = self.ctx.get_db()
        if db is None:
            QMessageBox.information(self, "导出 DwC", "未打开项目。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 Darwin Core",
            f"darwin_core_{self._today()}.csv",
            "CSV 文件 (*.csv)",
        )
        if not path:
            return
        try:
            out = export_darwin_core(db, path)
            QMessageBox.information(self, "导出成功", f"Darwin Core 已保存到：\n{out}")
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _today() -> str:
        from datetime import date as _date
        return _date.today().isoformat()
