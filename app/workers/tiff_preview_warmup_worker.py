"""tiff_preview_warmup_worker.py — 数据汇总后台 TIFF 预览预热线程。"""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from app.services.tiff_preview_warmup_service import WarmupResult


class TiffPreviewWarmupWorker(QThread):
    finished_result = pyqtSignal(object)

    def __init__(self, paths: list[str], parent=None) -> None:
        super().__init__(parent)
        self._paths = list(paths)
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def is_cancel_requested(self) -> bool:
        QThread.yieldCurrentThread()
        return bool(self._cancel_requested)

    def run(self) -> None:
        from app.services import tiff_preview_warmup_service as svc

        result = svc.warmup_tif_paths(
            self._paths,
            cancel_callback=self.is_cancel_requested,
        )
        self.finished_result.emit(result)
