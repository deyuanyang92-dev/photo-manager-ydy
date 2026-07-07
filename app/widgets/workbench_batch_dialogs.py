"""workbench_batch_dialogs.py — 工作台批量结果 / RNAlater 队列对话框。

从 app/views/workbench_view.py 原样拆出（行为逐字节一致）；
workbench_view 仍以原名 `_BatchResultDialog` / `_RnaQueueDialog` re-export。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class _BatchResultDialog(QDialog):
    """Batch retroactive archive result detail dialog.

    Shows a per-file table with status, size, and error column.
    Replaces the plain QMessageBox.information summary after batch archiving.
    """

    def __init__(self, results: list, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("批量归档结果")
        self.resize(700, 400)
        layout = QVBoxLayout(self)

        ok_count = sum(1 for r in results if r.ok)
        fail_count = len(results) - ok_count
        self._summary = QLabel(f"✓ {ok_count} 成功  ✗ {fail_count} 失败")
        layout.addWidget(self._summary)

        self._table = QTableWidget(len(results), 4)
        self._table.setHorizontalHeaderLabels(["文件名", "状态", "大小", "错误"])
        self._table.horizontalHeader().setStretchLastSection(True)
        for i, r in enumerate(results):
            self._table.setItem(i, 0, QTableWidgetItem(r.name))
            status_item = QTableWidgetItem("✓" if r.ok else "✗")
            status_item.setForeground(QColor("green" if r.ok else "red"))
            self._table.setItem(i, 1, status_item)
            size_str = f"{r.size_bytes // 1024} KB" if r.size_bytes else "-"
            self._table.setItem(i, 2, QTableWidgetItem(size_str))
            self._table.setItem(i, 3, QTableWidgetItem(r.error or ""))
        layout.addWidget(self._table)

        btn = QPushButton("关闭")
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)


class _RnaQueueDialog(QDialog):
    """RNAlater sheet queue preview with explicit print/clear actions."""

    def __init__(self, uids: list[str], job: dict, grid_opts: dict, parent=None) -> None:
        super().__init__(parent)
        self.action: str = ""
        self.setWindowTitle("RNAlater 合版队列")
        self.resize(880, 620)

        root = QHBoxLayout(self)
        left = QVBoxLayout()
        root.addLayout(left, 0)

        title = QLabel(f"待打印 RNAlater 标签：{len(uids)}")
        title.setStyleSheet("font-size:15px;font-weight:700;")
        left.addWidget(title)

        self._list = QListWidget()
        self._list.setMinimumWidth(300)
        for uid in uids:
            self._list.addItem(uid)
        left.addWidget(self._list, 1)

        hint = QLabel("确认排版后打印；清空只会移出待打印队列，不删除标本记录。")
        hint.setWordWrap(True)
        hint.setObjectName("MutedSmall")
        left.addWidget(hint)

        btn_row = QHBoxLayout()
        print_btn = QPushButton("打印合版")
        print_btn.setObjectName("Primary")
        clear_btn = QPushButton("清空队列")
        clear_btn.setObjectName("Outline")
        close_btn = QPushButton("关闭")
        print_btn.clicked.connect(lambda: self._finish("print"))
        clear_btn.clicked.connect(lambda: self._finish("clear"))
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(print_btn)
        btn_row.addWidget(clear_btn)
        btn_row.addWidget(close_btn)
        left.addLayout(btn_row)

        preview_box = QVBoxLayout()
        root.addLayout(preview_box, 1)
        pv_title = QLabel("合版预览")
        pv_title.setStyleSheet("font-size:13px;font-weight:700;")
        preview_box.addWidget(pv_title)
        self._preview = QLabel()
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setMinimumSize(500, 560)
        self._preview.setStyleSheet("background:#ffffff;border:1px solid #c8d0d8;")
        preview_box.addWidget(self._preview, 1)

        self._render_preview(job, grid_opts)

    def _finish(self, action: str) -> None:
        self.action = action
        self.accept()

    def _render_preview(self, job: dict, grid_opts: dict) -> None:
        from app.utils.label_sheet import compute_sheet_geometry, paint_sheet_page

        w, h = 520, 560
        pm = QPixmap(w, h)
        pm.fill(QColor("#f3f6f8"))
        painter = QPainter(pm)
        try:
            paper_type = job.get("paperType") or "a4"
            geom = compute_sheet_geometry(
                job.get("dims") or {},
                paper_type,
                job.get("paper"),
                grid_opts,
                w,
                h,
            )
            info = paint_sheet_page(
                painter,
                job,
                grid_opts,
                0,
                geom,
                cut_marks=bool(grid_opts.get("cutMarks", True)),
            )
            painter.setPen(QColor("#475569"))
            painter.drawText(
                14,
                h - 14,
                f"{paper_type.upper()} · 每页 {info.get('per_page', 0)} 个 · 共 {info.get('total_pages', 1)} 页",
            )
        finally:
            painter.end()
        self._preview.setPixmap(pm)
