"""settings.py — QSettings wrapper for persistent app preferences.

Stores: window geometry, last used project directory, UI preferences.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QSettings, QByteArray


_ORG = "SpecimenPhotoWorkbench"
_APP = "标本照片工作台"
_DELETE_JPG_DEFAULT_MIGRATION_KEY = "archive/delete_jpg_default_v2_applied"
_ARCHIVE_MODE_V2_MIGRATION_KEY = "archive/mode_v2_fast_default_applied"


class AppSettings:
    """Thin wrapper around QSettings using IniFormat for portability."""

    def __init__(self) -> None:
        self._qs = QSettings(_ORG, _APP)
        self._migrate_delete_jpg_default()
        self._migrate_archive_mode_default()

    def _migrate_delete_jpg_default(self) -> None:
        """Move legacy installs to the current default: no loose JPG after organise."""
        if str(self._qs.value(_DELETE_JPG_DEFAULT_MIGRATION_KEY, "false")).lower() == "true":
            return
        self._qs.setValue("archive/delete_jpg", "true")
        self._qs.setValue(_DELETE_JPG_DEFAULT_MIGRATION_KEY, "true")

    def _migrate_archive_mode_default(self) -> None:
        """Move legacy automatic JXL installs to the fast default archive mode."""
        if str(self._qs.value(_ARCHIVE_MODE_V2_MIGRATION_KEY, "false")).lower() == "true":
            return
        self._qs.setValue("archive/jxl_effort", 0)
        self._qs.setValue(_ARCHIVE_MODE_V2_MIGRATION_KEY, "true")

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

    @property
    def project_tree_view_mode(self) -> str:
        """``all`` = flat list of every recorded project; ``rooted`` = scan one survey root."""
        mode = str(self._qs.value("project/tree_view_mode", "all") or "all")
        return mode if mode in {"all", "rooted"} else "all"

    @project_tree_view_mode.setter
    def project_tree_view_mode(self, mode: str) -> None:
        mode = str(mode or "all")
        if mode not in {"all", "rooted"}:
            mode = "all"
        self._qs.setValue("project/tree_view_mode", mode)

    @property
    def project_tree_layout_mode(self) -> str:
        """``tree`` = 三栏树视图; ``cards`` = 全部项目卡片."""
        mode = str(self._qs.value("project/tree_layout_mode", "tree") or "tree")
        return mode if mode in {"tree", "cards"} else "tree"

    @project_tree_layout_mode.setter
    def project_tree_layout_mode(self, mode: str) -> None:
        mode = str(mode or "tree")
        if mode not in {"tree", "cards"}:
            mode = "tree"
        self._qs.setValue("project/tree_layout_mode", mode)

    @property
    def project_tree_ux_v2(self) -> bool:
        """精简顶栏/中栏/右栏主操作；关闭后恢复旧版控件布局。"""
        return self._qs.value("project/tree_ux_v2", True, type=bool)

    @project_tree_ux_v2.setter
    def project_tree_ux_v2(self, val: bool) -> None:
        self._qs.setValue("project/tree_ux_v2", bool(val))

    @property
    def project_tree_tip_dismissed(self) -> bool:
        return self._qs.value("project/tree_tip_dismissed", False, type=bool)

    @project_tree_tip_dismissed.setter
    def project_tree_tip_dismissed(self, val: bool) -> None:
        self._qs.setValue("project/tree_tip_dismissed", bool(val))

    @property
    def project_tree_grid_density(self) -> int:
        from app.config.project_tree_layout import (
            DEFAULT_GRID_DENSITY_INDEX,
            clamp_density_index,
            density_index_from_legacy_thumb,
        )

        raw = self._qs.value("project/tree_grid_density")
        if raw is not None:
            try:
                return clamp_density_index(int(raw))
            except (TypeError, ValueError):
                pass
        # 迁移旧像素滑块
        legacy = self._qs.value("project/tree_thumb_size")
        if legacy is not None:
            try:
                return density_index_from_legacy_thumb(900, int(legacy))
            except (TypeError, ValueError):
                pass
        return DEFAULT_GRID_DENSITY_INDEX

    @project_tree_grid_density.setter
    def project_tree_grid_density(self, index: int) -> None:
        from app.config.project_tree_layout import clamp_density_index

        self._qs.setValue("project/tree_grid_density", clamp_density_index(index))

    @property
    def project_tree_grid_sort(self) -> str:
        from app.config.project_tree_layout import DEFAULT_GRID_SORT, normalize_grid_sort

        raw = self._qs.value("project/tree_grid_sort", DEFAULT_GRID_SORT)
        return normalize_grid_sort(raw)

    @project_tree_grid_sort.setter
    def project_tree_grid_sort(self, mode: str) -> None:
        from app.config.project_tree_layout import normalize_grid_sort

        self._qs.setValue("project/tree_grid_sort", normalize_grid_sort(mode))

    @property
    def project_tree_grid_caption(self) -> str:
        from app.config.project_tree_layout import (
            DEFAULT_GRID_CAPTION,
            normalize_grid_caption_mode,
        )

        raw = self._qs.value("project/tree_grid_caption", DEFAULT_GRID_CAPTION)
        return normalize_grid_caption_mode(raw)

    @project_tree_grid_caption.setter
    def project_tree_grid_caption(self, mode: str) -> None:
        from app.config.project_tree_layout import normalize_grid_caption_mode

        self._qs.setValue("project/tree_grid_caption", normalize_grid_caption_mode(mode))

    @property
    def project_tree_content_mode(self) -> str:
        from app.config.project_tree_layout import (
            DEFAULT_CONTENT_MODE,
            normalize_content_mode,
        )

        raw = self._qs.value("project/tree_content_mode", DEFAULT_CONTENT_MODE)
        return normalize_content_mode(raw)

    @project_tree_content_mode.setter
    def project_tree_content_mode(self, mode: str) -> None:
        from app.config.project_tree_layout import normalize_content_mode

        self._qs.setValue("project/tree_content_mode", normalize_content_mode(mode))

    @property
    def project_tree_show_photos(self) -> bool:
        """数据汇总是否显示下方成片区；关闭后表格占满，便于大量数据浏览."""
        raw = self._qs.value("project/tree_show_photos", True)
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() not in {"0", "false", "no", "off"}

    @project_tree_show_photos.setter
    def project_tree_show_photos(self, show: bool) -> None:
        self._qs.setValue("project/tree_show_photos", bool(show))

    @property
    def project_tree_thumb_size(self) -> int:
        from app.config.project_tree_layout import DEFAULT_THUMB_SIZE, clamp_thumb_size

        raw = self._qs.value("project/tree_thumb_size", DEFAULT_THUMB_SIZE)
        return clamp_thumb_size(raw)

    @project_tree_thumb_size.setter
    def project_tree_thumb_size(self, size: int) -> None:
        from app.config.project_tree_layout import clamp_thumb_size

        self._qs.setValue("project/tree_thumb_size", clamp_thumb_size(size))

    @property
    def project_tree_preview_master_size(self) -> int:
        from app.config.project_tree_layout import (
            DEFAULT_PREVIEW_MASTER_SIZE,
            clamp_preview_master_size,
        )

        raw = self._qs.value(
            "project/tree_preview_master_size",
            DEFAULT_PREVIEW_MASTER_SIZE,
        )
        return clamp_preview_master_size(raw)

    @project_tree_preview_master_size.setter
    def project_tree_preview_master_size(self, size: int) -> None:
        from app.config.project_tree_layout import clamp_preview_master_size

        self._qs.setValue(
            "project/tree_preview_master_size",
            clamp_preview_master_size(size),
        )

    @property
    def project_tree_split_state(self):
        from PyQt6.QtCore import QByteArray

        v = self._qs.value("project/tree_split_state")
        return v if isinstance(v, QByteArray) else None

    @project_tree_split_state.setter
    def project_tree_split_state(self, state) -> None:
        if state:
            self._qs.setValue("project/tree_split_state", state)
        else:
            self._qs.remove("project/tree_split_state")

    @property
    def project_tree_grid_inner_split_state(self):
        from PyQt6.QtCore import QByteArray

        v = self._qs.value("project/tree_grid_inner_split_state")
        return v if isinstance(v, QByteArray) else None

    @project_tree_grid_inner_split_state.setter
    def project_tree_grid_inner_split_state(self, state) -> None:
        if state:
            self._qs.setValue("project/tree_grid_inner_split_state", state)
        else:
            self._qs.remove("project/tree_grid_inner_split_state")

    @property
    def project_tree_summary_body_split_state(self):
        from PyQt6.QtCore import QByteArray

        v = self._qs.value("project/tree_summary_body_split_state")
        return v if isinstance(v, QByteArray) else None

    @project_tree_summary_body_split_state.setter
    def project_tree_summary_body_split_state(self, state) -> None:
        if state:
            self._qs.setValue("project/tree_summary_body_split_state", state)
        else:
            self._qs.remove("project/tree_summary_body_split_state")

    @property
    def project_tree_summary_visible_columns(self) -> list[str]:
        import json

        raw = self._qs.value("project_tree/summary_visible_columns")
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [str(k) for k in parsed if str(k).strip()]
            except json.JSONDecodeError:
                pass
        from app.services.cross_workspace_query_service import DEFAULT_SUMMARY_VISIBLE_KEYS

        return list(DEFAULT_SUMMARY_VISIBLE_KEYS)

    @project_tree_summary_visible_columns.setter
    def project_tree_summary_visible_columns(self, keys: list[str]) -> None:
        import json

        clean = [str(k) for k in (keys or []) if str(k).strip()]
        self._qs.setValue("project_tree/summary_visible_columns", json.dumps(clean))

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
        # 「自动归档」开关（默认关）。开 → 有激活编号时可自动取该编号未占用
        # JPG 合成；合成出 TIFF 后自动把源 JPG 打包 ZIP+命名+移 results。
        return str(
            self._qs.value("workbench/auto_organize_after_compose", "false")
        ).lower() == "true"

    @auto_organize_after_compose.setter
    def auto_organize_after_compose(self, val: bool) -> None:
        self._qs.setValue(
            "workbench/auto_organize_after_compose", "true" if val else "false"
        )

    @property
    def silent_compose(self) -> bool:
        # 跳过合成前 JPG 勾选框和合成后确认框，直接按当前分组/选中 JPG 运行 Helicon。
        return str(self._qs.value("workbench/silent_compose", "false")).lower() == "true"

    @silent_compose.setter
    def silent_compose(self, val: bool) -> None:
        self._qs.setValue("workbench/silent_compose", "true" if val else "false")

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

    # ── JPG archive ───────────────────────────────────────────────────

    @property
    def jxl_effort_method(self) -> str:
        """Legacy archive option key kept for settings compatibility."""
        try:
            index = int(self._qs.value("archive/jxl_effort", 0))
        except (TypeError, ValueError):
            index = 0
        return "maximum" if index == 1 else "standard"

    @property
    def jxl_concurrency(self) -> int:
        try:
            value = int(self._qs.value("archive/jxl_concurrency", 4))
        except (TypeError, ValueError):
            value = 4
        return max(1, min(8, value))

    @property
    def delete_jpg_after_archive(self) -> bool:
        return str(self._qs.value("archive/delete_jpg", "true")).lower() == "true"

    @delete_jpg_after_archive.setter
    def delete_jpg_after_archive(self, val: bool) -> None:
        self._qs.setValue("archive/delete_jpg", "true" if val else "false")
        self._qs.setValue(_DELETE_JPG_DEFAULT_MIGRATION_KEY, "true")

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

    # ── Remote collaboration relay ────────────────────────────────────

    @property
    def remote_collab_enabled(self) -> bool:
        return self._qs.value("remote_collab/enabled", False, type=bool)

    @remote_collab_enabled.setter
    def remote_collab_enabled(self, val: bool) -> None:
        self._qs.setValue("remote_collab/enabled", bool(val))

    @property
    def remote_relay_url(self) -> str:
        return str(self._qs.value("remote_collab/relay_url", "", type=str)).strip()

    @remote_relay_url.setter
    def remote_relay_url(self, url: Optional[str]) -> None:
        self._qs.setValue("remote_collab/relay_url", (url or "").strip())

    @property
    def remote_account_id(self) -> str:
        return str(self._qs.value("remote_collab/account_id", "", type=str)).strip()

    @remote_account_id.setter
    def remote_account_id(self, account_id: Optional[str]) -> None:
        self._qs.setValue("remote_collab/account_id", (account_id or "").strip())

    @property
    def remote_device_token(self) -> str:
        return str(self._qs.value("remote_collab/device_token", "", type=str)).strip()

    @remote_device_token.setter
    def remote_device_token(self, token: Optional[str]) -> None:
        self._qs.setValue("remote_collab/device_token", (token or "").strip())

    # ── Geocoding ─────────────────────────────────────────────────────

    @property
    def amap_web_key(self) -> str:
        """高德「Web 服务」API key.  Empty = use OpenStreetMap/Nominatim."""
        return str(self._qs.value("geocode/amap_web_key", "", type=str)).strip()

    @amap_web_key.setter
    def amap_web_key(self, key: Optional[str]) -> None:
        self._qs.setValue("geocode/amap_web_key", (key or "").strip())

    # ── Sync ──────────────────────────────────────────────────────────

    def flush_to_disk(self) -> None:
        """Write any pending QSettings changes to persistent storage."""
        self._qs.sync()
