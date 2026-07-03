"""Background worker for importing external JPG/TIFF files.

File copies can be slow on WSL /mnt drives and network shares.  Keep the
copy loop off the Qt GUI thread; business rules stay in photo_import_service.
"""
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal


class PhotoImportWorker(QThread):
    started_import = pyqtSignal(int)
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        source_paths: list[str],
        incoming_dir: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._source_paths = list(source_paths)
        self._incoming_dir = incoming_dir

    def run(self) -> None:
        try:
            from app.services.photo_import_service import import_media_to_project

            self.started_import.emit(len(self._source_paths))
            result = import_media_to_project(self._source_paths, self._incoming_dir)
            self.completed.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
