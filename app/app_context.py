"""app_context.py — Shared application context injected into every view.

AppContext is the single dependency-injection container. Views receive it
via __init__(ctx) and access shared resources through it — they never
import each other directly.
"""
from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Optional

from app.config.settings import AppSettings
from app.db.db_manager import get_db, open_project_db

if TYPE_CHECKING:
    from app.services.collab_service import CollabService


class AppContext:
    """Holds shared state and provides access to DB connections and settings.

    Lifecycle:
        Created once in main.py before MainWindow is constructed.
        Passed to every view via BaseView.__init__(ctx).

    Attributes
    ----------
    settings:
        Persistent app preferences (QSettings wrapper).
    current_project_dir:
        The currently open project directory. None = no project loaded.
    collab_service:
        P2P LAN collaboration service (CollabService).  Set by main.py after
        QApplication is created (FastAPI / uvicorn require a running event
        loop in a QThread, so service is started *after* QApp exists).
        May be None if the service fails to start (no uvicorn / no network).
    """

    def __init__(self) -> None:
        self.settings = AppSettings()
        self._project_dir: Optional[str] = None
        self._project_root: Optional[str] = None
        self.last_db_error: Optional[Exception] = None
        self.collab_service: Optional["CollabService"] = None

    # ── Project dir ───────────────────────────────────────────────────

    @property
    def current_project_dir(self) -> Optional[str]:
        return self._project_dir

    @current_project_dir.setter
    def current_project_dir(self, path: Optional[str]) -> None:
        self._project_dir = path
        if path:
            self.settings.last_project_dir = path

    # ── Project root (folder-tree inheritance anchor) ─────────────────
    # The survey-root folder that bounds the settings-inheritance walk
    # (project_settings_service.get_effective). Distinct from
    # current_project_dir: a workspace may be its own root. Every
    # workspace-entry path MUST set this so inheritance is bounded and
    # behaves identically regardless of how the workspace was opened.

    @property
    def current_project_root(self) -> Optional[str]:
        return self._project_root

    @current_project_root.setter
    def current_project_root(self, path: Optional[str]) -> None:
        self._project_root = path
        self.settings.project_tree_root = path

    # ── Database access ───────────────────────────────────────────────

    def get_db(self, project_dir: Optional[str] = None) -> Optional[sqlite3.Connection]:
        """Return the SQLite connection for *project_dir* (or current project).

        Returns None if no project directory is set.
        Opens and caches the connection on first call.
        """
        target = project_dir or self._project_dir
        if not target:
            return None
        try:
            db = open_project_db(target)
        except (OSError, sqlite3.Error) as exc:
            self.last_db_error = exc
            if target == self._project_dir:
                self._project_dir = None
                self.settings.last_project_dir = None
            return None
        self.last_db_error = None
        return db

    # ── Convenience ───────────────────────────────────────────────────

    @property
    def has_project(self) -> bool:
        """True when a project directory is loaded."""
        return self._project_dir is not None
