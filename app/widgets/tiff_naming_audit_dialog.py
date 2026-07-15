"""Read-only table showing whether TIFF files follow result naming rules."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.tiff_naming_service import TiffNamingAudit, export_tiff_naming_audit_csv
from app.utils import ui
from app.utils.tooltip_policy import suppress_popup_tooltip


class TiffNamingAuditDialog(QDialog):
    def __init__(
        self,
        audit: TiffNamingAudit,
        parent: QWidget | None = None,
        *,
        auto_applied_path: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._audit = audit
        self._auto_applied_path = auto_applied_path
        self._selected_tiff_path: str | None = None
        self.setWindowTitle("TIF 命名检查")
        self.resize(980, 520)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        rule_line = self._audit.rules_summary or "项目命名规则：默认"
        summary = QLabel(
            f"{rule_line}\n"
            f"共 {self._audit.total} 个 TIF："
            f"符合 {self._audit.valid_count}，"
            f"不符合 {self._audit.invalid_count}。\n"
            "「解析标本编号」= 标本唯一编号（不含成果序号）；"
            "文件名末尾可附加采集人/备注等信息，不影响识别。"
            "识别内容可填入右侧后修改、补充；不会修改原 TIF 文件。"
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        self._table = QTableWidget(self._audit.total, 5)
        self._table.setHorizontalHeaderLabels(
            ["修改时间", "TIF 文件名", "状态", "解析标本编号", "建议名称"]
        )
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        suppress_popup_tooltip(self._table)

        for row, item in enumerate(self._audit.items):
            try:
                time_text = datetime.fromisoformat(item.modified_at).astimezone().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            except ValueError:
                time_text = item.modified_at
            values = [
                time_text,
                item.name,
                "符合" if item.valid else "不符合",
                item.uid or "—",
                item.suggested_name or "—",
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip("")
                cell.setData(
                    Qt.ItemDataRole.UserRole,
                    item.path if item.valid and item.uid else None,
                )
                if column == 2:
                    cell.setForeground(QColor("#16856b" if item.valid else "#c43d3d"))
                self._table.setItem(row, column, cell)
        layout.addWidget(self._table, 1)

        btn_row = QHBoxLayout()
        export_btn = QPushButton("导出 CSV")
        export_btn.setToolTip("导出检查结果（含解析标本编号），可用 Excel 打开")
        export_btn.clicked.connect(self._on_export_csv)
        btn_row.addWidget(export_btn)
        btn_row.addStretch()
        self._apply_btn = QPushButton("填入右侧")
        self._apply_btn.setObjectName("Primary")
        self._apply_btn.setToolTip("将所选规范 TIF 的编号预填到右侧，已有内容不会被覆盖")
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._on_apply_selected)
        btn_row.addWidget(self._apply_btn)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._table.itemSelectionChanged.connect(self._update_apply_action)
        if self._auto_applied_path:
            self.mark_auto_applied(self._auto_applied_path)

    def mark_auto_applied(self, path: str) -> None:
        """Show that a single valid result was already prefilled."""
        self._auto_applied_path = path
        for row, item in enumerate(self._audit.items):
            if item.path == path:
                self._table.selectRow(row)
                break
        self._apply_btn.setText("已填入右侧")
        self._apply_btn.setToolTip("已自动填入右侧空字段；关闭后可继续修改和补充")
        self._apply_btn.setEnabled(False)

    def selected_tiff_path(self) -> str | None:
        """Return the valid TIFF explicitly chosen for right-rail prefill."""
        return self._selected_tiff_path

    def _current_valid_path(self) -> str | None:
        row = self._table.currentRow()
        if row < 0:
            return None
        cell = self._table.item(row, 0)
        return str(cell.data(Qt.ItemDataRole.UserRole)) if cell and cell.data(
            Qt.ItemDataRole.UserRole
        ) else None

    def _update_apply_action(self) -> None:
        if self._auto_applied_path:
            self._apply_btn.setEnabled(False)
            return
        self._apply_btn.setEnabled(bool(self._current_valid_path()))

    def _on_apply_selected(self) -> None:
        path = self._current_valid_path()
        if not path:
            return
        self._selected_tiff_path = path
        self.accept()

    def _on_export_csv(self) -> None:
        default_name = str(Path(self._audit.folder).name or "tiff-naming") + "-audit.csv"
        path = ui.get_save_file_name(
            self,
            "导出 TIF 命名检查结果",
            start=default_name,
            filter="CSV 文件 (*.csv)",
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            export_tiff_naming_audit_csv(self._audit, path)
        except OSError as exc:
            ui.warn(self, "导出失败", str(exc))
            return
        ui.info(self, "导出完成", f"已保存：\n{path}")
