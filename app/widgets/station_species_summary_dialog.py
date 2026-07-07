"""Station species checklist dialog for project-tree summaries."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services import project_summary_service as pss
from app.services.project_tree_service import discover_workspaces, is_workspace
from app.utils import ui


class StationSpeciesSummaryDialog(QDialog):
    """Preview taxon checklist and the separate sample-processing overview."""

    def __init__(
        self,
        ctx=None,
        initial_root: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._root = str(Path(initial_root or ".").resolve())
        self._dirs: list[str] = []
        self._taxon_rows: list[dict] = []
        self._sample_rows: list[dict] = []
        self.setWindowTitle("分类名录与样品概况")
        self.resize(1280, 720)
        self._build_ui()
        self._reload()
        ui.center_on(self, parent)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        title = QLabel("分类名录与样品概况")
        title.setObjectName("PaneTitle")
        layout.addWidget(title)

        self._hint = QLabel("")
        self._hint.setObjectName("Muted")
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        filter_row.addWidget(QLabel("筛选"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.setPlaceholderText("输入地点、站位、时间、物种名、编号等任意关键词")
        self._filter_edit.textChanged.connect(self._apply_filters)
        filter_row.addWidget(self._filter_edit, 1)
        layout.addLayout(filter_row)

        self._tabs = QTabWidget()
        self._taxon_table = self._make_table(pss._TAXON_CHECKLIST_HEADERS)
        self._sample_table = self._make_table(pss._SAMPLE_PROCESSING_HEADERS)
        self._tabs.addTab(self._taxon_table, "分类名录")
        self._tabs.addTab(self._sample_table, "样品处理概况")
        layout.addWidget(self._tabs, 1)

        actions = QHBoxLayout()
        self._count_label = QLabel("")
        self._count_label.setObjectName("Muted")
        actions.addWidget(self._count_label)
        actions.addStretch()
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self._reload)
        self._export_btn = QPushButton("导出 Excel")
        self._export_btn.setObjectName("Primary")
        self._export_btn.clicked.connect(self._export)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        actions.addWidget(refresh)
        actions.addWidget(self._export_btn)
        actions.addWidget(close)
        layout.addLayout(actions)

    def _make_table(self, headers: tuple[tuple[str, str], ...]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels([label for label, _key in headers])
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        header = table.horizontalHeader()
        header.setStretchLastSection(True)
        for idx in range(table.columnCount()):
            header.setSectionResizeMode(idx, QHeaderView.ResizeMode.ResizeToContents)
        return table

    def _discover_dirs(self) -> list[str]:
        try:
            discovered = discover_workspaces(self._root)
        except Exception:
            discovered = []
        dirs = [str(Path(row["path"]).resolve()) for row in discovered]
        if not dirs:
            try:
                if is_workspace(self._root):
                    dirs = [self._root]
            except Exception:
                dirs = []
        return dirs

    def _reload(self) -> None:
        self._dirs = self._discover_dirs()
        self._taxon_rows = pss.collect_taxon_checklist(self._dirs, self._root)
        self._sample_rows = pss.collect_sample_processing_summary(self._dirs, self._root)
        self._hint.setText(
            f"范围：{self._root}；工作区 {len(self._dirs)} 个。"
            " 分类名录只统计已经有分类鉴定的分类单元；未鉴定混合管、照片、TIFF/ZIP "
            "属于样品处理概况，不计入物种名录。"
        )
        self._export_btn.setEnabled(bool(self._dirs))
        self._apply_filters()

    def _filtered_rows(
        self,
        rows: list[dict],
        headers: tuple[tuple[str, str], ...],
    ) -> list[dict]:
        term = self._filter_edit.text().strip().lower()
        if not term:
            return list(rows)
        terms = [part for part in term.split() if part]
        out: list[dict] = []
        for row in rows:
            haystack = " ".join(
                str(row.get(key, "") or "").lower()
                for _label, key in headers
            )
            if all(part in haystack for part in terms):
                out.append(row)
        return out

    def _apply_filters(self) -> None:
        taxon_rows = self._filtered_rows(self._taxon_rows, pss._TAXON_CHECKLIST_HEADERS)
        sample_rows = self._filtered_rows(self._sample_rows, pss._SAMPLE_PROCESSING_HEADERS)
        self._populate_table(
            self._taxon_table,
            pss._TAXON_CHECKLIST_HEADERS,
            taxon_rows,
        )
        self._populate_table(
            self._sample_table,
            pss._SAMPLE_PROCESSING_HEADERS,
            sample_rows,
        )
        self._tabs.setTabText(
            0, f"分类名录 {len(taxon_rows)}/{len(self._taxon_rows)}"
        )
        self._tabs.setTabText(
            1, f"样品处理概况 {len(sample_rows)}/{len(self._sample_rows)}"
        )
        self._count_label.setText(
            f"分类名录 {len(taxon_rows)}/{len(self._taxon_rows)} 条；"
            f"样品概况 {len(sample_rows)}/{len(self._sample_rows)} 条"
        )

    def _populate_table(
        self,
        table: QTableWidget,
        headers: tuple[tuple[str, str], ...],
        rows: list[dict],
    ) -> None:
        table.setRowCount(len(rows))
        for row_idx, row in enumerate(rows):
            for col_idx, (_label, key) in enumerate(headers):
                item = QTableWidgetItem(str(row.get(key, "") or ""))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row_idx, col_idx, item)

    def _export(self) -> None:
        if not self._dirs:
            ui.warn(self, "分类名录", "当前范围下没有可汇总的工作区。")
            return
        written: list[str] = []
        try:
            written.append(str(pss.export_taxon_checklist(self._dirs, self._root)))
            written.append(str(pss.export_sample_processing_summary(self._dirs, self._root)))
        except Exception as exc:
            ui.error(self, "分类名录", f"导出失败：{exc}")
            return
        ui.info(self, "分类名录", "已导出：\n" + "\n".join(written))
