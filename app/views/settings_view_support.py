"""Shared constants and small widgets for SettingsView."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.config.theme import COMPARISON_THEME_KEYS

# ── QSettings key constants ───────────────────────────────────────────────────

_K_INCOMING_SUBDIR = "project/incoming_subdir"
_K_RESULTS_SUBDIR = "project/results_subdir"
_K_RECENT_PROJECTS = "project/recent_dirs"  # stored as newline-joined string

# Helicon keys now live in helicon_service (canonical); re-exported here so
# existing `from app.views.settings_view import _K_HELICON_*` imports keep working.
from app.services.helicon_service import (  # noqa: F401
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
)

_K_JXL_EFFORT = "archive/jxl_effort"
_K_JXL_CONCURRENCY = "archive/jxl_concurrency"
_K_DELETE_JPG = "archive/delete_jpg"  # default True; safety checks still mandatory
_K_DELETE_JPG_DEFAULT_MIGRATION = "archive/delete_jpg_default_v2_applied"

_K_CURRENT_USER = "user/current_user"

# ── Workbench auto-watch toggles (mirrors saveV4Settings / loadV4Settings) ───

_K_WB_AUTO_WATCH = "workbench/auto_watch"
_K_WB_AUTO_ACTIVATE_NEW = "workbench/auto_activate_new"
_K_WB_GROUPING_AUTO_WATCH = "workbench/grouping_auto_watch"
_K_WB_GROUPING_AUTO_WATCH_MODE = "workbench/grouping_auto_watch_mode"
_K_WB_AUTO_ORGANIZE = "workbench/auto_organize_after_compose"
_K_WB_SILENT_COMPOSE = "workbench/silent_compose"
_K_WB_FILE_VIEW_MODE = "workbench/file_view_mode"

# ── Global UI settings (mirrors renderGlobalSettings) ────────────────────────

_K_UI_FONT_SCALE = "ui/font_scale"          # float 0.7–1.5, default 1.0
_K_UI_FONT_FAMILY = "ui/font_family"        # str, "" = 系统默认 CJK 栈
_K_UI_ICON_GPS = "ui/icon_gps"             # default "📡"
_K_UI_ICON_MAP = "ui/icon_map"             # default "📍"
_K_UI_ICON_FOLDER = "ui/icon_folder"       # default "📁"
_K_UI_ICON_SEARCH = "ui/icon_search"       # default "🔍"
_K_SCREENSHOT_TOOL_ENABLED = "ui/screenshot_tool_enabled"  # default false
_K_DEBUG_USE_REAL_COMPRESSION = "debug/use_real_compression"  # default False

_THEME_CHOICES = COMPARISON_THEME_KEYS

# Families we surface first in the 字体 picker.  Common Windows/macOS/Linux CJK
# and Latin faces are always listed because Qt may not enumerate cross-platform
# aliases (for example 宋体 vs SimSun) even when the host can resolve them.
_PREFERRED_FONT_FAMILIES = (
    "Microsoft YaHei", "微软雅黑", "SimSun", "宋体", "NSimSun", "新宋体",
    "SimHei", "黑体", "KaiTi", "楷体", "FangSong", "仿宋",
    "Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC",
    "WenQuanYi Micro Hei", "PingFang SC", "Hiragino Sans GB", "Heiti SC",
    "Noto Serif CJK SC", "Noto Serif SC", "Source Han Serif SC", "Songti SC",
    "STSong", "Times New Roman", "Times", "Georgia", "Segoe UI", "Arial",
    "Calibri", "Consolas", "Courier New",
)


def _installed_font_families() -> list:
    """Font picker families: common choices first, installed faces after."""
    try:
        from PyQt6.QtGui import QFontDatabase
        fams = list(QFontDatabase.families())
    except Exception:
        fams = []
    installed = {f for f in fams if not f.startswith(("@", "."))}
    ordered: list[str] = []
    seen: set[str] = set()
    for fam in _PREFERRED_FONT_FAMILIES:
        if fam and fam not in seen:
            ordered.append(fam)
            seen.add(fam)
    for fam in sorted(installed):
        if fam and fam not in seen:
            ordered.append(fam)
            seen.add(fam)
    return ordered

# ── Keyboard shortcuts (mirrors ensureShortcutsSettings) ─────────────────────

_K_SHORTCUT_MONITOR_ACTIVATE = "shortcuts/monitor_activate"
_K_SHORTCUT_MONITOR_DEACTIVATE = "shortcuts/monitor_deactivate"
_K_SHORTCUT_LABELS_PRINT = "shortcuts/labels_print"
_K_SHORTCUT_LABELS_NEXT = "shortcuts/labels_next"
_K_SHORTCUT_SCREENSHOT = "shortcuts/screenshot_region"   # 默认 Alt+A，系统级全局

_RECENT_MAX = 10

# ── Theme colours — resolved from the LIVE active theme ───────────────────────
# Previously these were hardcoded deep-teal constants, which force-painted the
# whole 配置 page dark regardless of the chosen theme → under a light theme the
# group titles / form labels (which use the theme's dark text colour) went
# invisible.  Now they are refreshed from the active theme tokens by
# _refresh_palette(), called at the top of _setup_ui() (the theme is applied
# before any view is built), so every `{_C_TEXT}` f-string picks up the live
# palette.
_C_BG = "#08161b"
_C_PANEL = "#10242a"
_C_TEXT = "#eef3ef"
_C_MUTED = "#87a2a1"
_C_ACCENT = "#29b9ab"
_C_SUCCESS = "#36c98f"
_C_WARN = "#f1bd57"
_C_DANGER = "#e66e63"
_C_BORDER = "rgba(145, 182, 181, 0.18)"


def _refresh_palette() -> None:
    """Rebind the module `_C_*` colours to the current theme tokens."""
    global _C_BG, _C_PANEL, _C_TEXT, _C_MUTED, _C_ACCENT
    global _C_SUCCESS, _C_WARN, _C_DANGER, _C_BORDER
    from app.config.theme import TOKENS
    g = TOKENS.get
    _C_BG = g("bg", _C_BG)
    _C_PANEL = g("panel", _C_PANEL)
    _C_TEXT = g("text", _C_TEXT)
    _C_MUTED = g("muted", _C_MUTED)
    _C_ACCENT = g("accent", _C_ACCENT)
    _C_SUCCESS = g("success", _C_SUCCESS)
    _C_WARN = g("warn", _C_WARN)
    _C_DANGER = g("danger", _C_DANGER)
    _C_BORDER = g("border", _C_BORDER)


def _btn_style(variant: str = "outline") -> str:
    """Return inline QSS for small action buttons (live theme tokens)."""
    from app.config.theme import TOKENS
    accent = TOKENS.get("accent", _C_ACCENT)
    accent_hi = TOKENS.get("accent_hover", accent)
    accent_lo = TOKENS.get("accent_pressed", accent)
    on_accent = TOKENS.get("accent_fg", TOKENS.get("bg", "#ffffff"))
    border = TOKENS.get("border", _C_BORDER)
    if variant == "primary":
        return (
            "QPushButton {"
            f"  background: {accent};"
            f"  color: {on_accent}; border: none; border-radius: 4px;"
            "  padding: 3px 10px; font-weight: 600; font-size: 12px;"
            "}"
            f"QPushButton:hover {{ background: {accent_hi}; }}"
            f"QPushButton:pressed {{ background: {accent_lo}; }}"
        )
    return (
        "QPushButton {"
        "  background: transparent;"
        f"  color: {accent};"
        f"  border: 1px solid {border};"
        "  border-radius: 4px;"
        "  padding: 3px 10px; font-size: 12px;"
        "}"
        f"QPushButton:hover {{ background: rgba(0,0,0,0.06); }}"
        f"QPushButton:pressed {{ background: rgba(0,0,0,0.12); }}"
    )

# ── Private helpers ───────────────────────────────────────────────────────────


def _clear_layout(layout) -> None:
    """Remove all widgets/layouts from an existing Qt layout."""
    while layout.count():
        item = layout.takeAt(0)
        child_layout = item.layout()
        if child_layout is not None:
            _clear_layout(child_layout)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()


class _ConfigRow(QWidget):
    """Horizontal row mirroring web .config-row (label + value widgets)."""

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet("background: transparent;")
        self._hl = QHBoxLayout(self)
        self._hl.setContentsMargins(0, 4, 0, 4)
        self._hl.setSpacing(12)
        self._hl.setAlignment(Qt.AlignmentFlag.AlignVCenter)

    def add_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFixedWidth(110)
        lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {_C_MUTED};"
            "background: transparent;"
        )
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._hl.addWidget(lbl)
        return lbl

    def add_widget(self, widget: QWidget, stretch: int = 0) -> None:
        self._hl.addWidget(widget, stretch=stretch)


class _ScrollTab(QWidget):
    """A QWidget wrapper providing a vertical-scrolling content area.

    Each tab in SettingsView is an instance of this class.
    ``self.body`` is the QVBoxLayout to add rows/groups to.
    """

    def __init__(self) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {_C_BG}; border: none; }}"
            f"QWidget {{ background: {_C_BG}; }}"
        )

        inner = QWidget()
        inner.setStyleSheet(f"background: {_C_BG};")
        self.body = QVBoxLayout(inner)
        self.body.setContentsMargins(28, 24, 28, 24)
        self.body.setSpacing(16)

        scroll.setWidget(inner)
        outer.addWidget(scroll)
