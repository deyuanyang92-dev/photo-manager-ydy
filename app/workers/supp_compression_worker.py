"""supp_compression_worker.py — QThread for non-blocking 补处理 archival.

Wraps app.services.archive_service.archive_group so that compressing N JPGs to
JXL never blocks the UI. Contains NO safety / sha logic of its own — every
red-line gate (TIFF never deleted, 4 deletion preconditions, cjxl flags) lives
inside archive_group and is reached only through it.

Mirrors the HeliconWorker pattern (QThread + Qt signals for thread-safe
result delivery).
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from app.services.archive_service import archive_group


class SuppCompressionWorker(QThread):
    started_archiving = pyqtSignal(int, str)  # (jpg_count, tiff_stem) → initial toast
    finished = pyqtSignal(object)             # ZipResult
    failed = pyqtSignal(str)                  # error message

    def __init__(
        self,
        jpg_paths: list[str],
        tiff_path: str,
        project_dir: str,
        delete_jpg: bool = False,
        method: str = "maximum",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._jpg_paths = list(jpg_paths)
        self._tiff_path = tiff_path
        self._project_dir = project_dir
        self._delete_jpg = bool(delete_jpg)
        self._method = method

    def run(self) -> None:
        try:
            self.started_archiving.emit(
                len(self._jpg_paths), Path(self._tiff_path).stem
            )
            result = archive_group(
                self._jpg_paths,
                self._tiff_path,
                self._project_dir,
                delete_jpg=self._delete_jpg,
                method=self._method,
            )
            self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
            self.failed.emit(str(exc))
