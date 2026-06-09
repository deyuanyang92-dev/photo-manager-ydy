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

    # 新增（folder-tree 步骤 3）：记住「项目树」选定的根目录，下次打开复原。
    @property
    def project_tree_root(self) -> Optional[str]:
        return self._qs.value("project/tree_root", None)

    @project_tree_root.setter
    def project_tree_root(self, path: Optional[str]) -> None:
        if path:
            self._qs.setValue("project/tree_root", path)
        else:
            self._qs.remove("project/tree_root")

    # ── Nav selection ─────────────────────────────────────────────────

    @property
    def last_nav_index(self) -> int:
        return int(self._qs.value("nav/last_index", 0))

    @last_nav_index.setter
    def last_nav_index(self, idx: int) -> None:
        self._qs.setValue("nav/last_index", idx)

    # ── Workbench behaviour ───────────────────────────────────────────────

    @property
    def auto_activate_on_new_specimen(self) -> bool:
        return self._qs.value(
            "workbench/auto_activate_on_new_specimen", False, type=bool
        )

    @auto_activate_on_new_specimen.setter
    def auto_activate_on_new_specimen(self, val: bool) -> None:
        self._qs.setValue("workbench/auto_activate_on_new_specimen", val)

    # ── Appearance ────────────────────────────────────────────────────

    @property
    def current_theme(self) -> str:
        return self._qs.value("appearance/theme", "classic_light", type=str)

    @current_theme.setter
    def current_theme(self, name: str) -> None:
        self._qs.setValue("appearance/theme", name)

    @property
    def performance_mode(self) -> bool:
        """Drop card shadows + canvas gradients for smoother remote-desktop use."""
        return self._qs.value("appearance/performance_mode", False, type=bool)

    @performance_mode.setter
    def performance_mode(self, val: bool) -> None:
        self._qs.setValue("appearance/performance_mode", bool(val))

    # ── LAN collaboration ─────────────────────────────────────────────

    @property
    def collab_enabled(self) -> bool:
        """Whether the LAN collaboration service should run.  Default OFF."""
        return self._qs.value("collab/enabled", False, type=bool)

    @collab_enabled.setter
    def collab_enabled(self, val: bool) -> None:
        self._qs.setValue("collab/enabled", bool(val))

    @property
    def team_code(self) -> str:
        """Explicit collaboration-group code.  Empty = no group = no sync."""
        return str(self._qs.value("collab/team_code", "", type=str)).strip()

    @team_code.setter
    def team_code(self, code: Optional[str]) -> None:
        self._qs.setValue("collab/team_code", (code or "").strip())

    # ── Geocoding ─────────────────────────────────────────────────────

    @property
    def amap_web_key(self) -> str:
        """高德「Web 服务」API key.  Empty = use OpenStreetMap/Nominatim."""
        return str(self._qs.value("geocode/amap_web_key", "", type=str)).strip()

    @amap_web_key.setter
    def amap_web_key(self, key: Optional[str]) -> None:
        self._qs.setValue("geocode/amap_web_key", (key or "").strip())

    # ── Sync ──────────────────────────────────────────────────────────

    def sync(self) -> None:
        self._qs.sync()
