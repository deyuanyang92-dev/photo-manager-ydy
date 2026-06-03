"""app_context.py — Shared application context injected into every view.

AppContext is the single dependency-injection container. Views receive it
via __init__(ctx) and access shared resources through it — they never
import each other directly.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from app.config.settings import AppSettings
from app.db.db_manager import get_db, open_project_db


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
    """

    def __init__(self) -> None:
        self.settings = AppSettings()
        self._project_dir: Optional[str] = None

    # ── Project dir ───────────────────────────────────────────────────

    @property
    def current_project_dir(self) -> Optional[str]:
        return self._project_dir

    @current_project_dir.setter
    def current_project_dir(self, path: Optional[str]) -> None:
        self._project_dir = path
        if path:
            self.settings.last_project_dir = path

    # ── Database access ───────────────────────────────────────────────

    def get_db(self, project_dir: Optional[str] = None) -> Optional[sqlite3.Connection]:
        """Return the SQLite connection for *project_dir* (or current project).

        Returns None if no project directory is set.
        Opens and caches the connection on first call.
        """
        target = project_dir or self._project_dir
        if not target:
            return None
        return open_project_db(target)

    # ── Convenience ───────────────────────────────────────────────────

    @property
    def has_project(self) -> bool:
        """True when a project directory is loaded."""
        return self._project_dir is not None
