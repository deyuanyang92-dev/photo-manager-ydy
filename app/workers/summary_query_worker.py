"""summary_query_worker.py — 数据汇总跨工作区查询后台线程.

``query_summary_scope`` 内含 N 库全表扫 + results 目录 glob + 每标本 EXIF 读 +
result-tif 索引 sync —— 多断面大表在主线程直接跑会假死数秒(用户裁定:
点击零反馈=bug)。本 worker 把整链搬进 QThread,附带 ``summary_all_columns``
(同样逐库 PRAGMA)。服务层按阶段/行粒度支持 cancel_callback;取消后不 emit,
调用方另按 token 丢弃过期结果。

模式对齐 ``tiff_preview_warmup_worker.py``。
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal


class SummaryQueryWorker(QThread):
    finished_result = pyqtSignal(object, object)  # (SummaryScopeResult, all_columns)
    failed = pyqtSignal(str)

    def __init__(
        self,
        dirs: list[str],
        conditions: list | None,
        labels: Optional[list[str]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._dirs = list(dirs or [])
        self._conditions = list(conditions or [])
        self._labels = list(labels) if labels else None
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def is_cancel_requested(self) -> bool:
        QThread.yieldCurrentThread()
        return bool(self._cancel_requested)

    def run(self) -> None:
        from app.services import cross_workspace_query_service as cwq

        try:
            result = cwq.query_summary_scope(
                self._dirs,
                self._conditions,
                labels=self._labels,
                cancel_callback=self.is_cancel_requested,
            )
            if self._cancel_requested:
                return
            all_columns = cwq.summary_all_columns(self._dirs)
        except Exception as exc:  # 失败也要给用户反馈, 不能静默假死→无结果
            if not self._cancel_requested:
                self.failed.emit(str(exc))
            return
        if self._cancel_requested:
            return
        self.finished_result.emit(result, all_columns)
