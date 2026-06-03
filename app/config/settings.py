"""settings.py — QSettings wrapper for persistent app preferences.

Stores: window geometry, last used project directory, UI preferences.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QSettings, QByteArray


_ORG = "SpecimenPhotoWorkbench"
_APP = "标本照片工作台"


class AppSettings:
    """Thin wrapper around QSettings using IniFormat for portability."""

    def __init__(self) -> None:
        self._qs = QSettings(_ORG, _APP)

    # ── Window geometry ───────────────────────────────────────────────

    def save_geometry(self, geometry: QByteArray) -> None:
        self._qs.setValue("window/geometry", geometry)

    def restore_geometry(self) -> Optional[QByteArray]:
        v = self._qs.value("window/geometry")
        return v if isinstance(v, QByteArray) else None

    def save_window_state(self, state: QByteArray) -> None:
        self._qs.setValue("window/state", state)

    def restore_window_state(self) -> Optional[QByteArray]:
        v = self._qs.value("window/state")
        return v if isinstance(v, QByteArray) else None

    # ── Last project ──────────────────────────────────────────────────

    @property
    def last_project_dir(self) -> Optional[str]:
        return self._qs.value("project/last_dir", None)

    @last_project_dir.setter
    def last_project_dir(self, path: Optional[str]) -> None:
        if path:
            self._qs.setValue("project/last_dir", path)
        else:
            self._qs.remove("project/last_dir")

    # ── Nav selection ─────────────────────────────────────────────────

    @property
    def last_nav_index(self) -> int:
        return int(self._qs.value("nav/last_index", 0))

    @last_nav_index.setter
    def last_nav_index(self, idx: int) -> None:
        self._qs.setValue("nav/last_index", idx)

    # ── Sync ──────────────────────────────────────────────────────────

    def sync(self) -> None:
        self._qs.sync()
