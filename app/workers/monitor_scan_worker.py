"""Background monitor scan worker.

Keeps slow directory scans and SQLite reads off the Qt GUI thread.
"""
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal


class MonitorScanWorker(QThread):
    """Scan one project workspace on a private SQLite connection."""

    finished_scan = pyqtSignal(int, object)
    failed = pyqtSignal(int, object)

    def __init__(
        self,
        request_id: int,
        project_dir: str,
        incoming_subdir: str,
        results_subdir: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.request_id = request_id
        self.project_dir = project_dir
        self.incoming_subdir = incoming_subdir
        self.results_subdir = results_subdir

    def run(self) -> None:
        db = None
        try:
            from app.db.db_manager import open_project_db_private
            from app.services.monitor_service import (
                build_attribution_context,
                scan_project,
            )

            db = open_project_db_private(self.project_dir)
            attr = build_attribution_context(self.project_dir, db)
            result = scan_project(
                self.project_dir,
                db,
                attr=attr,
                incoming_subdir=self.incoming_subdir,
                results_subdir=self.results_subdir,
            )
            self.finished_scan.emit(self.request_id, result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(self.request_id, exc)
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass
