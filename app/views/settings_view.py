"""settings_view.py — Global settings panel facade.

The external Interface remains ``SettingsView`` plus the historical ``_K_*``
constants imported by tests and helper dialogs.  The Implementation is split by
settings area so tab construction, QSettings persistence, collaboration,
Helicon actions, UI actions, and project history each have their own Module.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.config.version import APP_VERSION
from app.views.base_view import BaseView
from app.views.settings_collab_workflow import SettingsCollabWorkflowMixin
from app.views.settings_helicon_actions import SettingsHeliconActionsMixin
from app.views.settings_persistence import SettingsPersistenceMixin
from app.views.settings_project_history import SettingsProjectHistoryMixin
from app.views.settings_tabs import SettingsTabsMixin
from app.views.settings_ui_workflow import SettingsUiWorkflowMixin
from app.views.settings_view_support import (
    _C_ACCENT,
    _C_BG,
    _C_BORDER,
    _C_DANGER,
    _C_MUTED,
    _C_PANEL,
    _C_SUCCESS,
    _C_TEXT,
    _C_WARN,
    _ConfigRow,
    _K_CURRENT_USER,
    _K_HELICON_EXE,
    _K_HELICON_METHOD,
    _K_HELICON_RADIUS,
    _K_HELICON_SMOOTHING,
    _K_HELICON_QUALITY,
    _K_HELICON_OUTPUT_FORMAT,
    _K_HELICON_TIFF_COMPRESSION,
    _K_HELICON_SAVE_DEPTH_MAP,
    _K_HELICON_RUN_MODE,
    _K_HELICON_CONCURRENCY,
    _K_HELICON_PRESETS_JSON,
    _K_DEBUG_USE_REAL_COMPRESSION,
    _K_DELETE_JPG,
    _K_DELETE_JPG_DEFAULT_MIGRATION,
    _K_INCOMING_SUBDIR,
    _K_JXL_CONCURRENCY,
    _K_JXL_EFFORT,
    _K_RECENT_PROJECTS,
    _K_RESULTS_SUBDIR,
    _K_SCREENSHOT_TOOL_ENABLED,
    _K_SHORTCUT_LABELS_NEXT,
    _K_SHORTCUT_LABELS_PRINT,
    _K_SHORTCUT_MONITOR_ACTIVATE,
    _K_SHORTCUT_MONITOR_DEACTIVATE,
    _K_SHORTCUT_SCREENSHOT,
    _K_UI_FONT_FAMILY,
    _K_UI_FONT_SCALE,
    _K_UI_ICON_FOLDER,
    _K_UI_ICON_GPS,
    _K_UI_ICON_MAP,
    _K_UI_ICON_SEARCH,
    _K_WB_AUTO_ACTIVATE_NEW,
    _K_WB_AUTO_ORGANIZE,
    _K_WB_AUTO_WATCH,
    _K_WB_FILE_VIEW_MODE,
    _K_WB_GROUPING_AUTO_WATCH,
    _K_WB_GROUPING_AUTO_WATCH_MODE,
    _K_WB_SILENT_COMPOSE,
    _PREFERRED_FONT_FAMILIES,
    _RECENT_MAX,
    _ScrollTab,
    _THEME_CHOICES,
    _btn_style,
    _clear_layout,
    _installed_font_families,
    _refresh_palette,
)

if TYPE_CHECKING:
    from app.app_context import AppContext


class SettingsView(
    SettingsTabsMixin,
    SettingsPersistenceMixin,
    SettingsCollabWorkflowMixin,
    SettingsUiWorkflowMixin,
    SettingsHeliconActionsMixin,
    SettingsProjectHistoryMixin,
    BaseView,
):
    """Settings panel facade preserving the existing view Interface."""

    view_id = "settings"
    nav_title = "配置"
    nav_icon = "⚙️"

    def __init__(self, ctx: "AppContext") -> None:  # noqa: D107
        super().__init__(ctx)
