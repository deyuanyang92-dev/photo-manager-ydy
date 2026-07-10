"""Read-only table showing whether TIFF files follow result naming rules."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

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
    ) -> None:
        super().__init__(parent)
        self._audit = audit
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
            "文件名末尾可附加采集人/备注等信息，不影响识别。仅检查，不修改文件。"
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        self._table = QTableWidget(self._audit.total, 5)
        self._table.setHorizontalHeaderLabels(
            ["修改时间", "TIF 文件名", "状态", "解析标本编号", "建议名称"]
        )
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
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
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

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
