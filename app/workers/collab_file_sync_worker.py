"""QThread wrapper for LAN collaboration file sync."""
from __future__ import annotations

from typing import Iterable, Optional

from PyQt6.QtCore import QThread, pyqtSignal

from app.services.collab_file_sync import SyncSummary, sync_from_peers


class CollabFileSyncWorker(QThread):
    progress = pyqtSignal(int, int, str)  # current, total, relative path
    finished_summary = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        *,
        project_dir: str,
        peers: Iterable,
        group_code: str,
        uids: Optional[list[str]] = None,
        mode: str = "smart",
        max_workers: int = 4,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._project_dir = project_dir
        self._peers = list(peers)
        self._group_code = group_code
        self._uids = list(uids or []) or None
        self._mode = mode
        self._max_workers = max_workers

    def run(self) -> None:
        try:
            summary = sync_from_peers(
                project_dir=self._project_dir,
                peers=self._peers,
                group_code=self._group_code,
                uids=self._uids,
                mode=self._mode,
                max_workers=self._max_workers,
                progress_cb=self.progress.emit,
            )
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        if not isinstance(summary, SyncSummary):
            summary = SyncSummary()
        self.finished_summary.emit(summary)
