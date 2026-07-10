"""tiff_jpeg_export_worker.py — background batch TIFF → JPEG export."""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from app.services.tiff_jpeg_export_service import (
    BatchExportResult,
    OverwritePolicy,
    TiffJpegExportSettings,
)


class TiffJpegExportWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        sources: list[str],
        settings: TiffJpegExportSettings,
        *,
        output_dir: str | None = None,
        source_root: str | None = None,
        recursive: bool = True,
        overwrite: OverwritePolicy = OverwritePolicy.OVERWRITE,
        smart: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._sources = list(sources)
        self._settings = settings
        self._output_dir = output_dir
        self._source_root = source_root
        self._recursive = bool(recursive)
        self._overwrite = overwrite
        self._smart = bool(smart)
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def is_cancel_requested(self) -> bool:
        QThread.yieldCurrentThread()
        return bool(self._cancel_requested)

    def run(self) -> None:
        from app.services import tiff_jpeg_export_service as svc

        try:
            result = svc.batch_convert_tiffs(
                self._sources,
                self._settings,
                output_dir=self._output_dir,
                source_root=self._source_root,
                recursive=self._recursive,
                overwrite=self._overwrite,
                smart=self._smart,
                progress_callback=self._emit_progress,
                cancel_callback=self.is_cancel_requested,
            )
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))

    def _emit_progress(self, current: int, total: int, filename: str) -> None:
        self.progress.emit(current, total, filename)
        QThread.yieldCurrentThread()
