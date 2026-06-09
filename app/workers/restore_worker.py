"""restore_worker.py — QThread for non-blocking 还原归档 JPG.

Wraps app.services.archive_service.restore_archive so that re-decoding N JXLs
back to original JPGs (via djxl) never blocks the UI. Contains NO logic of its
own — all extraction / integrity checks live inside restore_archive.

Mirrors the SuppCompressionWorker pattern (QThread + Qt signals for thread-safe
result delivery).
"""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from app.services.archive_service import restore_archive


class RestoreWorker(QThread):
    started = pyqtSignal(int)        # 文件数(预估,= ZIP 内条目)→ 初始 toast
    finished = pyqtSignal(object)    # RestoreResult
    failed = pyqtSignal(str)         # error message

    def __init__(
        self,
        zip_path: str,
        output_dir: str,
        overwrite: bool = False,
        file_count: int = 0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._zip_path = zip_path
        self._output_dir = output_dir
        self._overwrite = bool(overwrite)
        self._file_count = int(file_count)

    def run(self) -> None:
        try:
            self.started.emit(self._file_count)
            result = restore_archive(
                self._zip_path,
                self._output_dir,
                overwrite=self._overwrite,
            )
            self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
            self.failed.emit(str(exc))
