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
        # 必须与 settings_view 的复选框同一个 key（_K_WB_AUTO_ACTIVATE_NEW =
        # "workbench/auto_activate_new"），它持久化 "true"/"false" 字符串。
        # 旧实现读的是另一个 key（..._on_new_specimen）+ bool 类型，与复选框
        # 对不上 → 设置永远读不到用户的勾选。这里对齐 key + 字符串解析。
        return str(
            self._qs.value("workbench/auto_activate_new", "false")
        ).lower() == "true"

    @auto_activate_on_new_specimen.setter
    def auto_activate_on_new_specimen(self, val: bool) -> None:
        self._qs.setValue(
            "workbench/auto_activate_new", "true" if val else "false"
        )

    @property
    def auto_organize_after_compose(self) -> bool:
        # 「合成后自动整理归档」开关（默认关）。开 → 手动合成出 TIFF 后，自动把
        # 源 JPG 打包压缩+命名+移 results。合成本身仍手动。
        return str(
            self._qs.value("workbench/auto_organize_after_compose", "false")
        ).lower() == "true"

    @auto_organize_after_compose.setter
    def auto_organize_after_compose(self, val: bool) -> None:
        self._qs.setValue(
            "workbench/auto_organize_after_compose", "true" if val else "false"
        )

    @property
    def incoming_subdir(self) -> str:
        # 与 settings_view 的 _K_INCOMING_SUBDIR 同 key；用户可在设置页改 incoming
        # 目录名（不一定是 incoming-jpg）。监控的监听+扫描都应读这里。
        return str(self._qs.value("project/incoming_subdir", "incoming-jpg")) or "incoming-jpg"

    @incoming_subdir.setter
    def incoming_subdir(self, name: str) -> None:
        self._qs.setValue("project/incoming_subdir", name or "incoming-jpg")

    @property
    def results_subdir(self) -> str:
        return str(self._qs.value("project/results_subdir", "results")) or "results"

    @results_subdir.setter
    def results_subdir(self, name: str) -> None:
        self._qs.setValue("project/results_subdir", name or "results")

    # ── Appearance ────────────────────────────────────────────────────

    @property
    def current_theme(self) -> str:
        return self._qs.value("appearance/theme", "classic_light", type=str)

    @current_theme.setter
    def current_theme(self, name: str) -> None:
        self._qs.setValue("appearance/theme", name)

    @property
    def current_language(self) -> str:
        """UI language: "zh" (default) or "en"; views refresh it live."""
        return self._qs.value("appearance/language", "zh", type=str)

    @current_language.setter
    def current_language(self, lang: str) -> None:
        self._qs.setValue("appearance/language", lang)

    @property
    def performance_mode(self) -> bool:
        """Drop card shadows + canvas gradients for smoother remote-desktop use."""
        return self._qs.value("appearance/performance_mode", False, type=bool)

    @performance_mode.setter
    def performance_mode(self, val: bool) -> None:
        self._qs.setValue("appearance/performance_mode", bool(val))

    # ── Typography (字体 / 字体大小, 设置→界面) ────────────────────────
    @property
    def ui_font_scale(self) -> float:
        try:
            return float(self._qs.value("ui/font_scale", 1.0))
        except (TypeError, ValueError):
            return 1.0

    @ui_font_scale.setter
    def ui_font_scale(self, val: float) -> None:
        self._qs.setValue("ui/font_scale", float(val))

    @property
    def ui_font_family(self) -> str:
        return self._qs.value("ui/font_family", "", type=str) or ""

    @ui_font_family.setter
    def ui_font_family(self, val: str) -> None:
        self._qs.setValue("ui/font_family", val or "")

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
