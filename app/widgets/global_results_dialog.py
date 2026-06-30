"""Dialog for the global TIFF/ZIP result ledger."""
from __future__ import annotations

from pathlib import Path
from typing import Optional
import os
import subprocess
import sys

from PyQt6.QtCore import Qt, QSortFilterProxyModel
from PyQt6.QtGui import QBrush, QColor, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
)

from app.services.global_results_service import (
    ResultLedgerRow,
    clear_global_results_cache,
    collect_global_result_ledger,
    summarize_ledger,
)


_HEADERS = [
    "项目",
    "状态",
    "标本唯一编号",
    "组",
    "TIF",
    "ZIP",
    "入库",
    "标本",
    "物种",
    "保存",
    "采集日期",
    "拍照日期",
]

_AUTOSIZE_CELL_LIMIT = 5000
_FAST_COLUMN_WIDTHS = [150, 90, 180, 48, 220, 220, 80, 70, 160, 80, 90, 90]


class GlobalResultsDialog(QDialog):
    """Read-only cross-project ledger for specimen IDs and result files."""

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("全局成果台账")
        self.resize(1180, 720)
        self._rows: list[ResultLedgerRow] = []
        self._filtered_rows: list[ResultLedgerRow] = []
        self._model: Optional[QStandardItemModel] = None
        self._proxy: Optional[QSortFilterProxyModel] = None
        self._setup_ui()
        self.refresh()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        top = QHBoxLayout()
        title = QLabel("全局成果台账")
        title.setStyleSheet("font-size:16px;font-weight:600;")
        top.addWidget(title)

        self._summary = QLabel("")
        self._summary.setStyleSheet("color:#64748b;")
        top.addWidget(self._summary, stretch=1)

        self._refresh_btn = QPushButton("刷新")
        self._refresh_btn.clicked.connect(lambda: self.refresh(force=True))
        top.addWidget(self._refresh_btn)
        root.addLayout(top)

        filters = QHBoxLayout()
        self._project_combo = QComboBox()
        self._project_combo.setMinimumWidth(180)
        self._project_combo.currentIndexChanged.connect(self._rebuild_table)
        filters.addWidget(self._project_combo)

        self._status_combo = QComboBox()
        self._status_combo.setMinimumWidth(130)
        self._status_combo.currentIndexChanged.connect(self._rebuild_table)
        filters.addWidget(self._status_combo)

        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索编号 / TIF / ZIP / 物种 / 项目")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._rebuild_table)
        filters.addWidget(self._search, stretch=1)
        root.addLayout(filters)

        self._table = QTableView()
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        root.addWidget(self._table, stretch=1)

        hint = QLabel("右键可打开文件夹、复制路径、查看入库/关联信息。TIF 和 ZIP 分列显示，便于逐组检查。")
        hint.setStyleSheet("color:#64748b;font-size:12px;")
        root.addWidget(hint)

    def refresh(self, *, force: bool = False) -> None:
        if force:
            clear_global_results_cache()
        self._rows = collect_global_result_ledger(
            current_project_dir=getattr(self.ctx, "current_project_dir", None),
            current_project_root=getattr(self.ctx, "current_project_root", None),
            use_cache=not force,
        )
        self._rebuild_filter_options()
        self._rebuild_table()

    def _rebuild_filter_options(self) -> None:
        project = self._project_combo.currentData() if self._project_combo.count() else ""
        status = self._status_combo.currentText() if self._status_combo.count() else "全部状态"

        self._project_combo.blockSignals(True)
        self._project_combo.clear()
        self._project_combo.addItem("全部项目", "")
        seen: dict[str, str] = {}
        for row in self._rows:
            seen.setdefault(row.project_dir, row.project_name)
        for project_dir, name in sorted(seen.items(), key=lambda kv: kv[1].lower()):
            self._project_combo.addItem(name, project_dir)
        idx = self._project_combo.findData(project)
        self._project_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._project_combo.blockSignals(False)

        self._status_combo.blockSignals(True)
        self._status_combo.clear()
        self._status_combo.addItem("全部状态")
        for value in sorted({row.status for row in self._rows if row.status}):
            self._status_combo.addItem(value)
        idx = self._status_combo.findText(status)
        self._status_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._status_combo.blockSignals(False)

    def _rebuild_table(self) -> None:
        project_filter = self._project_combo.currentData() or ""
        status_filter = self._status_combo.currentText()
        query = self._search.text().strip().lower()

        rows = list(self._rows)
        if project_filter:
            rows = [row for row in rows if row.project_dir == project_filter]
        if status_filter and status_filter != "全部状态":
            rows = [row for row in rows if row.status == status_filter]
        if query:
            rows = [row for row in rows if query in self._search_blob(row)]
        self._filtered_rows = rows

        model = QStandardItemModel(len(rows), len(_HEADERS))
        model.setHorizontalHeaderLabels(_HEADERS)
        for r, row in enumerate(rows):
            values = [
                row.project_name,
                row.status,
                row.display_uid,
                "" if row.group_index is None else str(row.group_index + 1),
                self._file_label(row.tiff_path, row.tiff_exists),
                self._file_label(row.zip_path, row.zip_exists),
                "已入库" if row.registered else "未入库",
                "有标本" if row.has_specimen else "无标本",
                row.scientific_name,
                row.storage,
                row.collection_date,
                row.photo_date,
            ]
            for c, value in enumerate(values):
                item = QStandardItem(value)
                item.setEditable(False)
                item.setToolTip(self._tooltip(row, c))
                item.setForeground(self._brush_for(row, c))
                model.setItem(r, c, item)

        proxy = QSortFilterProxyModel()
        proxy.setSourceModel(model)
        proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        proxy.setFilterKeyColumn(-1)
        self._model = model
        self._proxy = proxy
        sorting = self._table.isSortingEnabled()
        self._table.setSortingEnabled(False)
        self._table.setModel(proxy)
        if len(rows) * len(_HEADERS) <= _AUTOSIZE_CELL_LIMIT:
            self._table.resizeColumnsToContents()
        else:
            for idx, width in enumerate(_FAST_COLUMN_WIDTHS):
                self._table.setColumnWidth(idx, width)
        self._table.setSortingEnabled(sorting)
        counts = summarize_ledger(rows)
        self._summary.setText(
            f"{counts['rows']} 行 · 标本 {counts['specimens']} · "
            f"TIF {counts['tiffs']} · ZIP {counts['zips']} · "
            f"完整 {counts['complete']} · 待检查 {counts['orphan'] + counts['missing']}"
        )

    @staticmethod
    def _file_label(path: str, exists: bool) -> str:
        if not path:
            return ""
        suffix = "" if exists else "  [缺文件]"
        return Path(path).name + suffix

    @staticmethod
    def _search_blob(row: ResultLedgerRow) -> str:
        parts = [
            row.project_name,
            row.project_dir,
            row.status,
            row.display_uid,
            row.scientific_name,
            row.storage,
            row.collection_date,
            row.photo_date,
            row.tiff_path,
            row.zip_path,
        ]
        return "\n".join(str(p or "").lower() for p in parts)

    @staticmethod
    def _tooltip(row: ResultLedgerRow, column: int) -> str:
        if column == 4:
            return row.tiff_path
        if column == 5:
            return row.zip_path
        return (
            f"项目: {row.project_dir}\n"
            f"编号: {row.display_uid or '未识别'}\n"
            f"状态: {row.status}\n"
            f"TIF: {row.tiff_path or '无'}\n"
            f"ZIP: {row.zip_path or '无'}"
        )

    @staticmethod
    def _brush_for(row: ResultLedgerRow, column: int) -> QBrush:
        if column == 1:
            if row.status == "完整":
                return QBrush(QColor("#047857"))
            if row.status in {"未入库", "孤儿文件"}:
                return QBrush(QColor("#b45309"))
            if row.status.startswith("缺") or row.status == "无标本":
                return QBrush(QColor("#b91c1c"))
        if column in (4, 5):
            path = row.tiff_path if column == 4 else row.zip_path
            exists = row.tiff_exists if column == 4 else row.zip_exists
            if path and not exists:
                return QBrush(QColor("#b91c1c"))
        if column in (6, 7) and not (row.registered if column == 6 else row.has_specimen):
            return QBrush(QColor("#b45309"))
        return QBrush(QColor("#0f172a"))

    def _selected_row(self) -> Optional[ResultLedgerRow]:
        index = self._table.currentIndex()
        if not index.isValid() or self._proxy is None:
            return None
        source = self._proxy.mapToSource(index)
        row_idx = source.row()
        if 0 <= row_idx < len(self._filtered_rows):
            return self._filtered_rows[row_idx]
        return None

    def _show_context_menu(self, pos) -> None:
        clicked = self._table.indexAt(pos)
        if clicked.isValid():
            self._table.setCurrentIndex(clicked)
        row = self._selected_row()
        if row is None:
            return
        menu = QMenu(self)
        open_tif = menu.addAction("打开 TIF 所在文件夹")
        open_tif.setEnabled(bool(row.tiff_path))
        open_zip = menu.addAction("打开 ZIP 所在文件夹")
        open_zip.setEnabled(bool(row.zip_path))
        copy_tif = menu.addAction("复制 TIF 路径")
        copy_tif.setEnabled(bool(row.tiff_path))
        copy_zip = menu.addAction("复制 ZIP 路径")
        copy_zip.setEnabled(bool(row.zip_path))
        menu.addSeparator()
        info = menu.addAction("查看入库/关联信息")
        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen == open_tif:
            self._open_in_explorer(row.tiff_path)
        elif chosen == open_zip:
            self._open_in_explorer(row.zip_path)
        elif chosen == copy_tif:
            QApplication.clipboard().setText(row.tiff_path)
        elif chosen == copy_zip:
            QApplication.clipboard().setText(row.zip_path)
        elif chosen == info:
            self._show_info(row)

    def _show_info(self, row: ResultLedgerRow) -> None:
        QMessageBox.information(
            self,
            "入库/关联信息",
            "\n".join([
                f"项目: {row.project_name}",
                f"项目目录: {row.project_dir}",
                f"状态: {row.status}",
                f"编号: {row.display_uid or '未识别'}",
                f"分组: {'' if row.group_index is None else row.group_index + 1}",
                f"入库: {'是' if row.registered else '否'}",
                f"关联标本: {'是' if row.has_specimen else '否'}",
                f"TIF: {row.tiff_path or '无'}",
                f"ZIP: {row.zip_path or '无'}",
            ]),
        )

    @staticmethod
    def _open_in_explorer(path: str) -> None:
        if not path:
            return
        p = Path(path)
        target = p if p.is_dir() else p.parent
        try:
            if sys.platform == "win32":
                if p.is_file():
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(str(p))])
                else:
                    subprocess.Popen(["explorer", os.path.normpath(str(target))])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except (OSError, subprocess.SubprocessError):
            pass
