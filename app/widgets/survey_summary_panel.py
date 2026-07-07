"""survey_summary_panel.py — 物种名录面板 (spec survey-summary-view T4).

嵌入 ProjectTreeView 右栏 QStackedWidget 的右侧面板:跨选中断面聚合物种名录
(``taxon_inventory_service.aggregate_taxon_inventory``) → 可排序表格 + 底部
[导出 Excel][导出 CSV][Darwin Core] 三按钮。

红线遵守:
- 无 ``species`` / ``species_cn`` 列 → 用 ``scientific_name`` /
  ``scientific_name_cn`` (CLAUDE.md domain gotcha)。
- 只读聚合:不创建 / 不修改任何 workspace db。
- Darwin Core 导出跨多 db 合并 ``darwin_core`` 视图行 (只 SELECT)。
- 文件对话框一律走 ``app.utils.ui.get_save_file_name`` (DontUseNativeDialog,
  多屏居中),绝不直接 ``QFileDialog``。
"""
from __future__ import annotations

from typing import Any, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services import export_service
from app.services.taxon_inventory_service import aggregate_taxon_inventory
from app.utils import ui

#: 表格列顺序与 export_service.INVENTORY_HEADERS 对齐 (物种名录面板的真相源)。
_HEADERS: list[str] = list(export_service.INVENTORY_HEADERS)


class _NumericItem(QTableWidgetItem):
    """按整数值排序的表格项 (避免 "10" < "2" 的字典序误排)。

    合计编号数列用它,DisplayRole 给人看文字、UserRole 给排序用数值。
    """

    def __init__(self, value: int, text: Optional[str] = None) -> None:
        super().__init__(text if text is not None else str(value))
        self._value = int(value)
        super().setData(Qt.ItemDataRole.UserRole, self._value)
        # 数值列右对齐,更像表格。
        self.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def __lt__(self, other: object) -> bool:  # noqa: D401 - Qt override
        if isinstance(other, QTableWidgetItem):
            try:
                ov = int(other.data(Qt.ItemDataRole.UserRole))
            except (TypeError, ValueError):
                ov = 0
        else:
            try:
                ov = int(other)
            except (TypeError, ValueError):
                ov = 0
        return self._value < ov


class SurveySummaryPanel(QWidget):
    """物种名录面板。

    Public API:
        set_workspaces(workspace_dirs, labels=None) → 聚合后填表。
        inventory() → 当前聚合结果 list[dict] (供测试 / 外部读取)。
        workspaces() → 当前加载的工作区目录列表。

    labels 形如 ``{workspace_dir: 断面名}``,与 ``aggregate_taxon_inventory``
    的 ``labels`` 列表参数等价 (本面板内部把 dict 转成顺序对齐的列表)。
    """

    def __init__(self, ctx: Any = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._inventory: list[dict] = []
        self._workspaces: list[str] = []
        self._labels: Optional[list[str]] = None
        self._setup_ui()

    # ── UI 构建 ────────────────────────────────────────────────────────────────
    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._title = QLabel("物种名录")
        self._title.setStyleSheet("font-weight:600;")
        self._status = QLabel("未加载")
        self._status.setStyleSheet("color:#666;")
        layout.addWidget(self._title)
        layout.addWidget(self._status)

        self._table = QTableWidget(0, len(_HEADERS))
        self._table.setHorizontalHeaderLabels(_HEADERS)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        header = self._table.horizontalHeader()
        if header is not None:
            header.setStretchLastSection(False)
            header.setSortIndicatorShown(True)
        # 学名列(0)拉宽;数值列末尾收窄。
        self._table.setColumnWidth(0, 200)
        self._table.setColumnWidth(len(_HEADERS) - 1, 90)
        layout.addWidget(self._table, stretch=1)

        # 底部导出按钮
        btn_row = QHBoxLayout()
        self._btn_excel = QPushButton("导出 Excel")
        self._btn_csv = QPushButton("导出 CSV")
        self._btn_dwc = QPushButton("Darwin Core")
        self._btn_excel.clicked.connect(self._export_excel)
        self._btn_csv.clicked.connect(self._export_csv)
        self._btn_dwc.clicked.connect(self._export_darwin_core)
        btn_row.addWidget(self._btn_excel)
        btn_row.addWidget(self._btn_csv)
        btn_row.addWidget(self._btn_dwc)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self._refresh_export_enabled()

    # ── Public API ─────────────────────────────────────────────────────────────
    def set_workspaces(
        self,
        workspace_dirs: list[str],
        labels: Optional[dict] = None,
    ) -> None:
        """聚合所选工作区的物种名录并填表。

        Args:
            workspace_dirs: 选中的工作区目录列表 (每个含 ``_data/project.db``;
                缺失/损坏的条目由聚合服务静默跳过)。
            labels: 可选的 ``{workspace_dir: 断面名}`` 映射,长度不限但键需匹配
                workspace_dirs 中的条目;未提供的断面用其目录 basename。
        """
        self._workspaces = [str(w) for w in (workspace_dirs or [])]
        if labels:
            self._labels = [labels.get(w) for w in self._workspaces]
        else:
            self._labels = None
        self._inventory = aggregate_taxon_inventory(
            self._workspaces, labels=self._labels
        )
        self._fill_table()
        self._refresh_export_enabled()

    def inventory(self) -> list[dict]:
        """当前聚合结果 (list[dict]),供测试与外部读取。"""
        return self._inventory

    def workspaces(self) -> list[str]:
        """当前加载的工作区目录列表。"""
        return list(self._workspaces)

    # ── 内部 ───────────────────────────────────────────────────────────────────
    def _fill_table(self) -> None:
        # 填表期间关闭排序,避免逐行插入触发反复重排。
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        self._table.setRowCount(len(self._inventory))
        last_col = len(_HEADERS) - 1
        for r, row in enumerate(self._inventory):
            text_cells = [
                str(row.get("scientific_name", "") or ""),
                str(row.get("scientific_name_cn", "") or ""),
                str(row.get("order_name", "") or ""),
                str(row.get("family", "") or ""),
                str(row.get("genus", "") or ""),
                ", ".join(row.get("sites", []) or []),
                ", ".join(
                    f"{site}={n}"
                    for site, n in sorted((row.get("count_per_site") or {}).items())
                ),
            ]
            for c, val in enumerate(text_cells):
                self._table.setItem(r, c, QTableWidgetItem(val))
            # 合计编号数 = 数值列 (用 _NumericItem 排序正确)。
            try:
                total = int(row.get("total_count", 0) or 0)
            except (TypeError, ValueError):
                total = 0
            self._table.setItem(r, last_col, _NumericItem(total))
        self._table.setSortingEnabled(True)
        # 默认按学名升序,与聚合服务返回顺序一致。
        self._table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self._status.setText(self._status_text())

    def _status_text(self) -> str:
        n_species = len(self._inventory)
        n_sites = len(self._workspaces)
        n_records = sum(int(r.get("total_count", 0) or 0) for r in self._inventory)
        if n_sites == 0:
            return "未加载"
        return f"{n_sites} 断面 · {n_species} 物种 · {n_records} 编号"

    def _refresh_export_enabled(self) -> None:
        has_data = bool(self._workspaces)
        self._btn_excel.setEnabled(has_data)
        self._btn_csv.setEnabled(has_data)
        self._btn_dwc.setEnabled(has_data)

    # ── 导出 ───────────────────────────────────────────────────────────────────
    def _export_excel(self) -> None:
        if not self._inventory:
            ui.info(self, "导出 Excel", "当前没有可导出的物种名录。")
            return
        path = ui.get_save_file_name(
            self, "导出物种名录 Excel", "物种名录.xlsx", "Excel 工作簿 (*.xlsx)"
        )
        if not path:
            return
        try:
            out = export_service.export_inventory_excel(self._inventory, path)
        except Exception as exc:  # pragma: no cover - 防御性,导出失败弹窗
            ui.exception(self, "导出失败", exc)
            return
        ui.info(self, "导出完成", f"已导出至:\n{out}")

    def _export_csv(self) -> None:
        if not self._inventory:
            ui.info(self, "导出 CSV", "当前没有可导出的物种名录。")
            return
        path = ui.get_save_file_name(
            self, "导出物种名录 CSV", "物种名录.csv", "CSV (*.csv)"
        )
        if not path:
            return
        try:
            out = export_service.export_inventory_csv(self._inventory, path)
        except Exception as exc:  # pragma: no cover - 防御性
            ui.exception(self, "导出失败", exc)
            return
        ui.info(self, "导出完成", f"已导出至:\n{out}")

    def _export_darwin_core(self) -> None:
        if not self._workspaces:
            ui.info(self, "Darwin Core 导出", "请先选择断面。")
            return
        path = ui.get_save_file_name(
            self, "导出 Darwin Core", "darwin_core.csv", "CSV (*.csv)"
        )
        if not path:
            return
        try:
            out = export_service.export_inventory_darwin_core(
                self._workspaces, path
            )
        except Exception as exc:  # pragma: no cover - 防御性
            ui.exception(self, "导出失败", exc)
            return
        ui.info(self, "导出完成", f"已导出至:\n{out}")
