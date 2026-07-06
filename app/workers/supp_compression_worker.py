"""supp_compression_worker.py — QThread for non-blocking 补处理 archival.

Wraps app.services.archive_service.archive_group so that packing N JPGs into
ZIP never blocks the UI. Contains NO safety / sha logic of its own — every
delete gate lives inside archive_group and is reached only through it.

Mirrors the HeliconWorker pattern (QThread + Qt signals for thread-safe
result delivery).
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

class SuppCompressionWorker(QThread):
    started_archiving = pyqtSignal(int, str)  # (jpg_count, tiff_stem) → initial toast
    progress = pyqtSignal(int, int, str)       # current, total, JPG filename
    finished = pyqtSignal(object)             # ZipResult
    cancelled = pyqtSignal(str)               # user-visible cancel reason
    failed = pyqtSignal(str)                  # error message

    def __init__(
        self,
        jpg_paths: list[str],
        tiff_path: str,
        project_dir: str,
        delete_jpg: bool = True,
        method: str = "standard",
        concurrency: int = 4,
        output_dir: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._jpg_paths = list(jpg_paths)
        self._tiff_path = tiff_path
        self._project_dir = project_dir
        self._delete_jpg = bool(delete_jpg)
        self._method = method
        self._concurrency = max(1, min(8, int(concurrency)))
        self._output_dir = output_dir
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def is_cancel_requested(self) -> bool:
        QThread.yieldCurrentThread()
        return bool(self._cancel_requested)

    def _emit_progress(self, current: int, total: int, filename: str) -> None:
        self.progress.emit(current, total, filename)
        QThread.yieldCurrentThread()

    def _cleanup_partial_zip(self) -> None:
        zip_dir = Path(self._output_dir) if self._output_dir else Path(self._tiff_path).parent
        zip_path = zip_dir / (Path(self._tiff_path).stem + ".zip")
        try:
            if zip_path.exists():
                zip_path.unlink()
        except OSError:
            pass

    def run(self) -> None:
        from app.services import archive_service

        try:
            self.started_archiving.emit(
                len(self._jpg_paths), Path(self._tiff_path).stem
            )
            result = archive_service.archive_group(
                self._jpg_paths,
                self._tiff_path,
                self._project_dir,
                delete_jpg=self._delete_jpg,
                method=self._method,
                concurrency=self._concurrency,
                progress_callback=self._emit_progress,
                cancel_callback=self.is_cancel_requested,
                output_dir=self._output_dir,
            )
            self.finished.emit(result)
        except archive_service.ArchiveCancelled:
            self._cleanup_partial_zip()
            self.cancelled.emit("用户取消")
        except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
            self.failed.emit(str(exc))
