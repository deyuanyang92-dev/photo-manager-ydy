"""settings_view.py — Global settings panel mirroring the real web 「配置」page.

Primary layout (Helicon tab) is a faithful reconstruction of the web
``renderConfigPage()`` DOM (pages_dom.json 「配置」):

  config-page
    config-header  (h2 "配置" + p.config-subtitle)
    section.config-section.helicon-config-section
      h3.config-section-title  "Helicon Focus"
      div.config-row            自动探测结果  (code + status badge)
      div.config-row            当前生效路径  (code)
      div.config-row.config-row--input  自定义路径
        input.config-path-input
        div.config-btn-row  [检测] [保存] [清除自定义] [重新探测]
      div.config-hint           exploration priority list

Additional tabs (归档 / 项目 / 操作人 / 关于) carry settings that live in
project-settings drawers in the web prototype and are required by the test suite.

Hard rule: delete_jpg default = False.  This is enforced here and in tests.
"""

from __future__ import annotations

import os
import platform
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.config.i18n import tr
from app.views.base_view import BaseView

if TYPE_CHECKING:
    from app.app_context import AppContext

# ── App version constant ──────────────────────────────────────────────────────

APP_VERSION = "0.1.0-dev"

# ── QSettings key constants ───────────────────────────────────────────────────

_K_INCOMING_SUBDIR = "project/incoming_subdir"
_K_RESULTS_SUBDIR = "project/results_subdir"
_K_RECENT_PROJECTS = "project/recent_dirs"  # stored as newline-joined string

_K_HELICON_EXE = "helicon/exe_path"
_K_HELICON_METHOD = "helicon/method"
_K_HELICON_RADIUS = "helicon/radius"
_K_HELICON_SMOOTHING = "helicon/smoothing"
_K_HELICON_QUALITY = "helicon/quality"

_K_HELICON_OUTPUT_FORMAT = "helicon/output_format"     # "tif" | "jpg"
_K_HELICON_TIFF_COMPRESSION = "helicon/tiff_compression"  # "u" | "lzw" | "zip"
_K_HELICON_SAVE_DEPTH_MAP = "helicon/save_depth_map"   # "true"/"false"
_K_HELICON_RUN_MODE = "helicon/run_mode"               # "silent" | "progress" | "gui"
_K_HELICON_CONCURRENCY = "helicon/concurrency"          # int 1–8

_K_JXL_EFFORT = "archive/jxl_effort"
_K_DELETE_JPG = "archive/delete_jpg"  # default False — hard rule

_K_CURRENT_USER = "user/current_user"

_K_HELICON_PRESETS_JSON = "helicon/presets_json"

# ── Workbench auto-watch toggles (mirrors saveV4Settings / loadV4Settings) ───

_K_WB_AUTO_WATCH = "workbench/auto_watch"
_K_WB_AUTO_ACTIVATE_NEW = "workbench/auto_activate_new"
_K_WB_GROUPING_AUTO_WATCH = "workbench/grouping_auto_watch"
_K_WB_GROUPING_AUTO_WATCH_MODE = "workbench/grouping_auto_watch_mode"
_K_WB_FILE_VIEW_MODE = "workbench/file_view_mode"

# ── Global UI settings (mirrors renderGlobalSettings) ────────────────────────

_K_UI_FONT_SCALE = "ui/font_scale"          # float 0.7–1.5, default 1.0
_K_UI_FONT_FAMILY = "ui/font_family"        # str, "" = 系统默认 CJK 栈
_K_UI_ICON_GPS = "ui/icon_gps"             # default "📡"
_K_UI_ICON_MAP = "ui/icon_map"             # default "📍"
_K_UI_ICON_FOLDER = "ui/icon_folder"       # default "📁"
_K_UI_ICON_SEARCH = "ui/icon_search"       # default "🔍"
_K_DEBUG_USE_REAL_COMPRESSION = "debug/use_real_compression"  # default False

_THEME_CHOICES = ("classic_light", "lab_light", "graphite_focus")

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


# ── Main view ─────────────────────────────────────────────────────────────────


class SettingsView(BaseView):
    """Settings panel: Helicon tab mirrors web 配置 page DOM exactly.

    view_id   = "settings"
    nav_title = "配置"
    nav_icon  = "⚙️"
    """

    view_id = "settings"
    nav_title = "配置"
    nav_icon = "⚙️"

    def __init__(self, ctx: "AppContext") -> None:  # noqa: D107
        super().__init__(ctx)

    # ── BaseView contract ─────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        """Build the full widget tree."""
        _refresh_palette()  # bind _C_* to the active theme before building
        existing = self.layout()
        if existing is not None:
            _clear_layout(existing)
            root = existing
        else:
            root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Tab widget — Helicon is first (the web 配置 page)
        self._tabs = QTabWidget()
        self._tabs.setObjectName("SettingsTabs")
        self._tabs.setStyleSheet(
            "QTabWidget::pane {"
            f"  background: {_C_BG};"
            f"  border: 1px solid {_C_BORDER};"
            "  border-top: none;"
            "}"
            "QTabBar::tab {"
            f"  background: {_C_PANEL};"
            f"  color: {_C_MUTED};"
            f"  border: 1px solid {_C_BORDER};"
            "  border-bottom: none;"
            "  padding: 6px 16px;"
            "  min-width: 60px;"
            "}"
            "QTabBar::tab:selected {"
            f"  background: {_C_BG};"
            f"  color: {_C_TEXT};"
            f"  border-bottom: 2px solid {_C_ACCENT};"
            "}"
            f"QTabBar::tab:hover:!selected {{ background: {_C_PANEL}; color: {_C_TEXT}; }}"
        )
        root.addWidget(self._tabs, stretch=1)

        self._build_tab_project()
        self._build_tab_helicon()
        self._build_tab_archive()
        self._build_tab_workbench()
        self._build_tab_user()
        self._build_tab_collab()
        self._build_tab_ui()
        self._build_tab_about()

    def on_activate(self) -> None:
        """Reload settings from QSettings each time the user opens this view."""
        self._load_all()

    def retranslate_ui(self) -> None:
        """Rebuild this settings page with the active language, preserving tab."""
        current_tab = self._tabs.currentIndex() if hasattr(self, "_tabs") else 0
        self._setup_ui()
        self._load_all()
        self._tabs.setCurrentIndex(min(current_tab, self._tabs.count() - 1))

    # ── Tab builders ──────────────────────────────────────────────────────

    def _build_tab_helicon(self) -> None:
        """Helicon tab — faithful 1:1 mirror of web renderConfigPage() DOM.

        DOM classes reproduced:
          config-page > config-header, config-section.helicon-config-section
          config-section > config-section-title, config-row (×3), config-hint
          config-row--input: label / input.config-path-input / config-btn-row
          config-btn-row: [检测] [保存] [清除自定义] [重新探测]
        """
        tab = _ScrollTab()
        tab.body.setSpacing(0)  # Helicon tab manages its own gaps with addSpacing()

        # ── config-header ─────────────────────────────────────────────────
        header_frame = QFrame()
        header_frame.setObjectName("ConfigHeader")
        header_frame.setStyleSheet("QFrame#ConfigHeader { background: transparent; }")
        header_v = QVBoxLayout(header_frame)
        header_v.setContentsMargins(0, 0, 0, 16)
        header_v.setSpacing(4)

        h2 = QLabel("配置")
        h2.setStyleSheet(
            f"font-size: 20px; font-weight: 700; color: {_C_TEXT}; background: transparent;"
        )
        header_v.addWidget(h2)

        subtitle = QLabel("Helicon Focus CLI 路径与探测设置。")
        subtitle.setStyleSheet(
            f"font-size: 13px; color: {_C_MUTED}; background: transparent;"
        )
        header_v.addWidget(subtitle)
        tab.body.addWidget(header_frame)

        # ── config-section.helicon-config-section ────────────────────────
        section = QFrame()
        section.setObjectName("HeliconConfigSection")
        section.setStyleSheet(
            "QFrame#HeliconConfigSection {"
            f"  background: {_C_PANEL};"
            f"  border: 1px solid {_C_BORDER};"
            "  border-radius: 6px;"
            "  padding: 0px;"
            "}"
        )
        section_v = QVBoxLayout(section)
        section_v.setContentsMargins(20, 16, 20, 16)
        section_v.setSpacing(0)

        # config-section-title
        section_title = QLabel("Helicon Focus")
        section_title.setStyleSheet(
            f"font-size: 15px; font-weight: 600; color: {_C_TEXT};"
            "margin-bottom: 12px; background: transparent;"
        )
        section_v.addWidget(section_title)

        # ── Row 1: 自动探测结果 ────────────────────────────────────────
        detected_row = _ConfigRow()
        detected_row.add_label("自动探测结果")
        self._detected_path_label = QLabel("（未检测到）")
        self._detected_path_label.setObjectName("ConfigPathValue")
        self._detected_path_label.setStyleSheet(
            f"font-family: monospace; font-size: 12px; color: {_C_TEXT};"
            f"background: rgba(0,0,0,0.25); border-radius: 3px;"
            "padding: 2px 6px;"
        )
        detected_row.add_widget(self._detected_path_label, stretch=1)

        # status badge (config-status-ok / config-status-warn)
        self._detect_status_badge = QLabel("未检测到")
        self._detect_status_badge.setObjectName("ConfigStatusWarn")
        self._detect_status_badge.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {_C_WARN};"
            "background: transparent;"
        )
        detected_row.add_widget(self._detect_status_badge)
        section_v.addWidget(detected_row)
        section_v.addSpacing(10)

        # ── Row 2: 当前生效路径 ────────────────────────────────────────
        effective_row = _ConfigRow()
        effective_row.add_label("当前生效路径")
        self._effective_path_label = QLabel("—")
        self._effective_path_label.setObjectName("ConfigPathValue")
        self._effective_path_label.setStyleSheet(
            f"font-family: monospace; font-size: 12px; color: {_C_TEXT};"
            "background: rgba(0,0,0,0.25); border-radius: 3px;"
            "padding: 2px 6px;"
        )
        effective_row.add_widget(self._effective_path_label, stretch=1)
        section_v.addWidget(effective_row)
        section_v.addSpacing(14)

        # ── Row 3: 自定义路径 (config-row--input) ─────────────────────
        custom_row_frame = QFrame()
        custom_row_frame.setStyleSheet("QFrame { background: transparent; }")
        custom_v = QVBoxLayout(custom_row_frame)
        custom_v.setContentsMargins(0, 0, 0, 0)
        custom_v.setSpacing(8)

        custom_label = QLabel("自定义路径")
        custom_label.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {_C_MUTED};"
            "background: transparent;"
        )
        custom_v.addWidget(custom_label)

        # config-path-input
        self._helicon_exe_edit = QLineEdit()
        self._helicon_exe_edit.setObjectName("ConfigPathInput")
        self._helicon_exe_edit.setPlaceholderText(
            r"如 I:\Helicon Focus 8 或完整 HeliconFocus.exe 路径"
        )
        self._helicon_exe_edit.setStyleSheet(
            "QLineEdit#ConfigPathInput {"
            f"  background: {_C_PANEL};"
            f"  color: {_C_TEXT};"
            f"  border: 1px solid {_C_BORDER};"
            "  border-radius: 4px;"
            "  padding: 5px 10px;"
            "  font-family: monospace; font-size: 12px;"
            "}"
            "QLineEdit#ConfigPathInput:focus {"
            f"  border-color: {_C_ACCENT};"
            "}"
        )
        self._helicon_exe_edit.editingFinished.connect(self._save_helicon)
        custom_v.addWidget(self._helicon_exe_edit)

        # config-btn-row: [检测] [保存] [清除自定义] [重新探测]
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(8)

        self._test_btn = QPushButton("检测")
        self._test_btn.setStyleSheet(_btn_style("outline"))
        self._test_btn.clicked.connect(self._on_test_click)

        self._save_btn = QPushButton("保存")
        self._save_btn.setStyleSheet(_btn_style("primary"))
        self._save_btn.clicked.connect(self._on_save_click)

        self._clear_btn = QPushButton("清除自定义")
        self._clear_btn.setStyleSheet(_btn_style("outline"))
        self._clear_btn.clicked.connect(self._on_clear_click)

        self._refresh_btn = QPushButton("重新探测")
        self._refresh_btn.setStyleSheet(_btn_style("outline"))
        self._refresh_btn.clicked.connect(self._on_redetect_click)

        btn_row.addWidget(self._test_btn)
        btn_row.addWidget(self._save_btn)
        btn_row.addWidget(self._clear_btn)
        btn_row.addWidget(self._refresh_btn)
        btn_row.addStretch()
        custom_v.addLayout(btn_row)
        section_v.addWidget(custom_row_frame)
        section_v.addSpacing(12)

        # ── config-hint ────────────────────────────────────────────────
        hint_frame = QFrame()
        hint_frame.setStyleSheet(
            "QFrame {"
            f"  background: rgba(41,185,171,0.06);"
            f"  border: 1px solid rgba(41,185,171,0.14);"
            "  border-radius: 4px;"
            "}"
        )
        hint_v = QVBoxLayout(hint_frame)
        hint_v.setContentsMargins(12, 10, 12, 10)
        hint_v.setSpacing(3)

        hint_title = QLabel("探测优先级：")
        hint_title.setStyleSheet(
            f"font-weight: 600; color: {_C_TEXT}; font-size: 12px;"
            "background: transparent;"
        )
        hint_v.addWidget(hint_title)

        for line in [
            "1. 自定义路径（上方填写）",
            "2. HELICON_FOCUS_PATH 环境变量",
            "3. HELICON_FOCUS_DIR 环境变量",
            "4. Windows 注册表",
            "5. 已知安装目录（I:\\Helicon Focus 8 等）",
        ]:
            lbl = QLabel(line)
            lbl.setStyleSheet(
                f"font-size: 12px; color: {_C_MUTED}; background: transparent;"
            )
            hint_v.addWidget(lbl)

        section_v.addWidget(hint_frame)
        tab.body.addWidget(section)
        tab.body.addSpacing(16)

        # ── 合成参数预设 CRUD ─────────────────────────────────────────────────────
        preset_box = QGroupBox("合成参数预设")
        preset_v = QVBoxLayout(preset_box)
        preset_v.setSpacing(8)

        # Preset list
        self._preset_list = QListWidget()
        self._preset_list.setFixedHeight(100)
        self._preset_list.setAlternatingRowColors(True)
        self._preset_list.setToolTip("已保存的合成参数预设，双击应用")
        self._preset_list.itemDoubleClicked.connect(self._apply_selected_preset)
        preset_v.addWidget(self._preset_list)

        # 渲染参数编辑器 —— 复刻 Helicon Focus 桌面端 "Rendering" 面板:
        # 渲染方法单选 (Method A weighted average / B depth map / C pyramid) +
        # Radius / Smoothing 滑块+数字框 + 右键滑块复位默认, Method C 禁用 Radius。
        from app.widgets.helicon_params_panel import HeliconParamsPanel
        self._helicon_param_panel = HeliconParamsPanel()
        self._helicon_param_panel.params_changed.connect(self._save_helicon)
        preset_v.addWidget(self._helicon_param_panel)

        # Preset name input + action buttons
        preset_name_row = QHBoxLayout()
        preset_name_row.setContentsMargins(0, 0, 0, 0)
        preset_name_row.setSpacing(8)

        preset_name_lbl = QLabel("预设名称")
        preset_name_lbl.setFixedWidth(60)
        preset_name_lbl.setStyleSheet(f"font-size: 12px; color: {_C_MUTED};")
        self._preset_name_edit = QLineEdit()
        self._preset_name_edit.setPlaceholderText("输入预设名称后保存")
        self._preset_name_edit.setMaxLength(60)
        preset_name_row.addWidget(preset_name_lbl)
        preset_name_row.addWidget(self._preset_name_edit, stretch=1)
        preset_v.addLayout(preset_name_row)

        preset_btn_row = QHBoxLayout()
        preset_btn_row.setContentsMargins(0, 0, 0, 0)
        preset_btn_row.setSpacing(8)

        self._save_preset_btn = QPushButton("保存为预设")
        self._save_preset_btn.setStyleSheet(_btn_style("primary"))
        self._save_preset_btn.clicked.connect(self._save_current_as_preset)

        self._apply_preset_btn = QPushButton("应用选中预设")
        self._apply_preset_btn.setStyleSheet(_btn_style("outline"))
        self._apply_preset_btn.clicked.connect(self._apply_selected_preset)

        self._delete_preset_btn = QPushButton("删除选中预设")
        self._delete_preset_btn.setStyleSheet(_btn_style("outline"))
        self._delete_preset_btn.clicked.connect(self._delete_selected_preset)

        preset_btn_row.addWidget(self._save_preset_btn)
        preset_btn_row.addWidget(self._apply_preset_btn)
        preset_btn_row.addWidget(self._delete_preset_btn)
        preset_btn_row.addStretch()
        preset_v.addLayout(preset_btn_row)

        tab.body.addWidget(preset_box)
        tab.body.addSpacing(16)

        # ── 高级输出选项 (mirrors web renderHeliconConfigModal <details>输出选项) ─────────
        adv_box = QGroupBox("高级输出选项")
        adv_box.setCheckable(True)
        adv_box.setChecked(False)  # collapsed by default, like web <details>
        adv_v = QVBoxLayout(adv_box)
        adv_form = QFormLayout()
        adv_form.setHorizontalSpacing(16)
        adv_form.setVerticalSpacing(8)

        # outputFormat: tif | jpg
        self._output_format_combo = QComboBox()
        self._output_format_combo.addItems(["TIF（推荐，无损）", "JPG"])
        self._output_format_combo.setToolTip("输出格式：tif 或 jpg")
        self._output_format_combo.currentIndexChanged.connect(self._save_helicon_advanced)
        adv_form.addRow("输出格式", self._output_format_combo)

        # JPEG 质量 (-j) — 输出选项，仅输出格式为 JPG 时有效（默认输出 TIF）。
        # 桌面端 Rendering 页无此项，故归入输出选项，不放渲染参数区。
        self._quality_spin = QSpinBox()
        self._quality_spin.setRange(70, 100)
        self._quality_spin.setValue(95)
        self._quality_spin.setToolTip("-j: JPEG 质量，仅当输出格式为 JPG 时有效")
        self._quality_spin.valueChanged.connect(self._save_helicon)
        adv_form.addRow("JPEG 质量 (-j)", self._quality_spin)

        # tiffCompression: u | lzw | zip
        self._tiff_compression_combo = QComboBox()
        self._tiff_compression_combo.addItems(["无压缩 (u)", "LZW", "ZIP"])
        self._tiff_compression_combo.setToolTip("-tc: TIFF 压缩方式，仅输出格式为 TIF 时有效")
        self._tiff_compression_combo.currentIndexChanged.connect(self._save_helicon_advanced)
        adv_form.addRow("TIFF 压缩", self._tiff_compression_combo)

        # runMode: silent | progress | gui
        self._run_mode_combo = QComboBox()
        self._run_mode_combo.addItems([
            "静默（silent，默认）",
            "显示进度（progress）",
            "Helicon 界面（gui）",
        ])
        self._run_mode_combo.setToolTip(
            "运行方式：silent=无窗跑完；progress=本软件显示进度；gui=弹 Helicon 界面"
        )
        self._run_mode_combo.currentIndexChanged.connect(self._save_helicon_advanced)
        adv_form.addRow("运行方式", self._run_mode_combo)

        # concurrency: 1–8
        self._concurrency_spin = QSpinBox()
        self._concurrency_spin.setRange(1, 8)
        self._concurrency_spin.setValue(1)
        self._concurrency_spin.setToolTip("并发数，默认 1；Helicon 吃满 GPU，调大未必更快")
        self._concurrency_spin.valueChanged.connect(self._save_helicon_advanced)
        adv_form.addRow("并发数", self._concurrency_spin)

        adv_v.addLayout(adv_form)

        # saveDepthMap checkbox
        self._save_depth_map_chk = QCheckBox("保存深度图（saveDepthMap）")
        self._save_depth_map_chk.setChecked(False)
        self._save_depth_map_chk.stateChanged.connect(self._save_helicon_advanced)
        adv_v.addWidget(self._save_depth_map_chk)

        tab.body.addWidget(adv_box)
        tab.body.addStretch()

        # Legacy status label alias (kept for _detect_helicon compat)
        self._helicon_status_label = self._detect_status_badge

        self._tabs.addTab(tab, tr("Helicon"))

    def _build_tab_archive(self) -> None:
        """归档 tab — JXL effort + delete-JPG (default OFF, hard rule)."""
        tab = _ScrollTab()
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # JXL effort
        self._jxl_effort_combo = QComboBox()
        self._jxl_effort_combo.addItems([
            "standard — cjxl -e 7（推荐）",
            "maximum  — cjxl -e 9（慢，文件更小）",
        ])
        self._jxl_effort_combo.setToolTip("对应 compress.js EFFORT_MAP：standard=7，maximum=9")
        self._jxl_effort_combo.currentIndexChanged.connect(self._save_archive)
        form.addRow("JXL 压缩等级", self._jxl_effort_combo)

        tab.body.addLayout(form)
        tab.body.addSpacing(16)

        # Delete-JPG section with prerequisite description
        del_box = QGroupBox("删除原片 JPG")
        del_v = QVBoxLayout(del_box)

        prereq_label = QLabel(
            "⚠️ 只有同时满足以下四项前置条件，才允许开启删除：\n"
            "  1. cjxl 可用（JPEG XL 无损压缩工具已安装）\n"
            "  2. ZIP 已生成且大小 > 32 字节\n"
            "  3. 清单完整（文件数 + 名称 + 大小全部核验通过）\n"
            "  4. JXL 可恢复（djxl 能重解码每一帧，输出大小 > 0）"
        )
        prereq_label.setObjectName("Muted")
        prereq_label.setWordWrap(True)
        prereq_label.setStyleSheet("font-size: 12px; line-height: 1.5;")
        del_v.addWidget(prereq_label)

        del_v.addSpacing(12)

        # The actual checkbox — DEFAULT OFF (hard rule)
        self._delete_jpg_chk = QCheckBox("归档完成后删除原片 JPG（危险操作，默认关闭）")
        self._delete_jpg_chk.setObjectName("DeleteJpgCheckbox")
        self._delete_jpg_chk.setChecked(False)  # default = False — hard rule
        self._delete_jpg_chk.setStyleSheet(
            f"QCheckBox {{ color: {_C_DANGER}; font-weight: 600; }}"
            f"QCheckBox::indicator:checked {{ background-color: {_C_DANGER}; border-color: {_C_DANGER}; }}"
        )
        self._delete_jpg_chk.stateChanged.connect(self._save_archive)
        del_v.addWidget(self._delete_jpg_chk)

        tab.body.addWidget(del_box)
        tab.body.addStretch()
        self._tabs.addTab(tab, tr("归档"))

    def _build_tab_project(self) -> None:
        """项目 tab — directory, sub-dirs, recent projects."""
        tab = _ScrollTab()
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Current project directory (read-only display + browse button)
        proj_row = QWidget()
        proj_hl = QHBoxLayout(proj_row)
        proj_hl.setContentsMargins(0, 0, 0, 0)
        proj_hl.setSpacing(8)
        self._project_dir_edit = QLineEdit()
        self._project_dir_edit.setReadOnly(True)
        self._project_dir_edit.setPlaceholderText("（无项目）")
        self._project_dir_edit.setObjectName("Mono")
        self._project_dir_browse = QPushButton("浏览…")
        self._project_dir_browse.setFixedWidth(72)
        self._project_dir_browse.clicked.connect(self._browse_project_dir)
        proj_hl.addWidget(self._project_dir_edit, stretch=1)
        proj_hl.addWidget(self._project_dir_browse)
        form.addRow("当前项目目录", proj_row)

        # incoming-jpg sub-dir name
        self._incoming_edit = QLineEdit()
        self._incoming_edit.setPlaceholderText("incoming-jpg")
        self._incoming_edit.editingFinished.connect(self._save_project)
        form.addRow("原片子目录名", self._incoming_edit)

        # results sub-dir name
        self._results_edit = QLineEdit()
        self._results_edit.setPlaceholderText("results")
        self._results_edit.editingFinished.connect(self._save_project)
        form.addRow("成果子目录名", self._results_edit)

        # 高德 Web 服务 key — empty falls back to OpenStreetMap/Nominatim
        self._amap_key_edit = QLineEdit()
        self._amap_key_edit.setPlaceholderText("留空 = 用 OpenStreetMap 地名搜索")
        self._amap_key_edit.setToolTip(
            "高德开放平台「Web 服务」类型 key（非 JS API key）。\n"
            "填入后坐标工具地名搜索改用高德，中国 POI 质量更好。"
        )
        self._amap_key_edit.editingFinished.connect(self._save_project)
        form.addRow("高德 Web 服务 Key", self._amap_key_edit)

        tab.body.addLayout(form)
        tab.body.addSpacing(16)

        # Recent projects list
        recent_box = QGroupBox("最近项目")
        recent_v = QVBoxLayout(recent_box)
        self._recent_list = QListWidget()
        self._recent_list.setFixedHeight(160)
        self._recent_list.setAlternatingRowColors(True)
        recent_v.addWidget(self._recent_list)

        # Open selected / clear buttons
        recent_btn_row = QHBoxLayout()
        self._open_recent_btn = QPushButton("打开选中")
        self._open_recent_btn.clicked.connect(self._open_recent)
        self._clear_recent_btn = QPushButton("清空历史")
        self._clear_recent_btn.clicked.connect(self._clear_recent)
        recent_btn_row.addWidget(self._open_recent_btn)
        recent_btn_row.addStretch()
        recent_btn_row.addWidget(self._clear_recent_btn)
        recent_v.addLayout(recent_btn_row)
        tab.body.addWidget(recent_box)
        tab.body.addStretch()

        self._tabs.addTab(tab, tr("项目"))

    def _build_tab_workbench(self) -> None:
        """工作台 tab — auto-watch toggles (mirrors saveV4Settings / loadV4Settings).

        Persists:
          autoWatch                  → workbench/auto_watch
          autoActivateOnNewSpecimen  → workbench/auto_activate_new
          groupingAutoWatch          → workbench/grouping_auto_watch
          groupingAutoWatchMode      → workbench/grouping_auto_watch_mode
          fileViewMode               → workbench/file_view_mode
        """
        tab = _ScrollTab()

        watch_box = QGroupBox("自动监控")
        watch_v = QVBoxLayout(watch_box)
        watch_v.setSpacing(10)

        self._auto_watch_chk = QCheckBox("新 TIFF 入库后自动激活（autoWatch）")
        self._auto_watch_chk.setChecked(True)  # web default = true
        self._auto_watch_chk.stateChanged.connect(self._save_workbench)
        watch_v.addWidget(self._auto_watch_chk)

        self._auto_activate_new_chk = QCheckBox("新建编号后自动激活（autoActivateOnNewSpecimen）")
        self._auto_activate_new_chk.setChecked(False)  # web default = false
        self._auto_activate_new_chk.stateChanged.connect(self._save_workbench)
        watch_v.addWidget(self._auto_activate_new_chk)

        self._grouping_auto_watch_chk = QCheckBox("JPG 入库后自动分组处理（groupingAutoWatch）")
        self._grouping_auto_watch_chk.setChecked(False)  # web default = false
        self._grouping_auto_watch_chk.stateChanged.connect(self._save_workbench)
        watch_v.addWidget(self._grouping_auto_watch_chk)

        tab.body.addWidget(watch_box)
        tab.body.addSpacing(12)

        mode_box = QGroupBox("分组自动处理模式")
        mode_v = QVBoxLayout(mode_box)
        mode_form = QFormLayout()
        mode_form.setHorizontalSpacing(16)
        mode_form.setVerticalSpacing(8)

        # groupingAutoWatchMode: compose | organize | compose+organize
        self._grouping_mode_combo = QComboBox()
        self._grouping_mode_combo.addItems([
            "合成 (compose)",
            "整理 (organize)",
            "合成+整理 (compose+organize)",
        ])
        self._grouping_mode_combo.setCurrentIndex(2)  # default: compose+organize
        self._grouping_mode_combo.setToolTip("触发 groupingAutoWatch 时的处理模式")
        self._grouping_mode_combo.currentIndexChanged.connect(self._save_workbench)
        mode_form.addRow("自动处理模式", self._grouping_mode_combo)

        # fileViewMode: jpg-tif | with-zip | all
        self._file_view_mode_combo = QComboBox()
        self._file_view_mode_combo.addItems([
            "jpg-tif（JPG + TIFF，默认）",
            "with-zip（含 ZIP）",
            "all（全部文件）",
        ])
        self._file_view_mode_combo.setCurrentIndex(0)
        self._file_view_mode_combo.setToolTip("文件列表视图模式")
        self._file_view_mode_combo.currentIndexChanged.connect(self._save_workbench)
        mode_form.addRow("文件视图模式", self._file_view_mode_combo)

        mode_v.addLayout(mode_form)
        tab.body.addWidget(mode_box)
        tab.body.addStretch()

        self._tabs.addTab(tab, tr("工作台"))

    def _build_tab_user(self) -> None:
        """操作人 tab — currentUser name used as modifiedBy in taxonomy edits."""
        tab = _ScrollTab()
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._current_user_edit = QLineEdit()
        self._current_user_edit.setPlaceholderText("匿名")
        self._current_user_edit.setMaxLength(80)
        self._current_user_edit.editingFinished.connect(self._save_user)
        form.addRow("当前操作人", self._current_user_edit)

        note = QLabel(
            "操作人姓名会记录在分类修改（modifiedBy）和标本创建（createdBy）等字段中。\n"
            "与协作模式中的设备注册姓名共享同一字段。"
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        form.addRow("", note)

        tab.body.addLayout(form)
        tab.body.addStretch()
        self._tabs.addTab(tab, tr("操作人"))

    def _build_tab_collab(self) -> None:
        """协作 tab — LAN collaboration: enable, group code, peers, share addr.

        Group-scoped sync: machines sharing the same non-empty 协作组码 claim
        UIDs across the LAN so teammates can't reuse a number.  Empty code = no
        group = no sync.  Mirrors the workbench sidebar 协作管理 dialog, but
        adds the persistent settings (enable + code) the dialog cannot.
        """
        tab = _ScrollTab()
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Tip: point users to the new collab panel
        tip = QLabel(
            "💡 日常协作操作已移至工作台的「协作面板」"
            "（点击侧边栏底部的「协作面板」按钮即可打开）。以下为高级设置。"
        )
        tip.setObjectName("MutedSmall")
        tip.setWordWrap(True)
        form.addRow(tip)

        self._collab_enabled_chk = QCheckBox("启用局域网协作（重启或切换后生效）")
        self._collab_enabled_chk.toggled.connect(self._on_collab_enabled_toggled)
        form.addRow("协作", self._collab_enabled_chk)

        # Health traffic-light + one-click doctor / LAN search.
        self._collab_health_light = QLabel("●")
        self._collab_health_text = QLabel("—")
        self._collab_diagnose_btn = QPushButton("协作诊断")
        self._collab_diagnose_btn.clicked.connect(self._on_collab_diagnose)
        self._collab_scan_btn = QPushButton("搜索局域网队友")
        self._collab_scan_btn.clicked.connect(self._on_collab_scan)
        health_row = QHBoxLayout()
        health_row.addWidget(self._collab_health_light)
        health_row.addWidget(self._collab_health_text, stretch=1)
        health_row.addWidget(self._collab_diagnose_btn)
        health_row.addWidget(self._collab_scan_btn)
        health_wrap = QWidget()
        health_wrap.setLayout(health_row)
        form.addRow("状态", health_wrap)

        self._collab_team_code_edit = QLineEdit()
        self._collab_team_code_edit.setPlaceholderText("例如 SMW-2026（留空 = 不同步）")
        self._collab_team_code_edit.setMaxLength(64)
        self._collab_team_code_edit.editingFinished.connect(self._save_collab)
        form.addRow("协作组码", self._collab_team_code_edit)

        self._collab_addr_edit = QLineEdit()
        self._collab_addr_edit.setReadOnly(True)
        self._collab_addr_edit.setPlaceholderText("—")
        addr_row = QHBoxLayout()
        addr_row.addWidget(self._collab_addr_edit, stretch=1)
        copy_btn = QPushButton("复制")
        copy_btn.clicked.connect(self._copy_collab_addr)
        addr_row.addWidget(copy_btn)
        addr_wrap = QWidget()
        addr_wrap.setLayout(addr_row)
        form.addRow("本机地址", addr_wrap)

        # Manual peer (mDNS fallback across VLANs / strict firewalls)
        self._collab_peer_ip_edit = QLineEdit()
        self._collab_peer_ip_edit.setPlaceholderText("对方 IP")
        self._collab_peer_port_edit = QLineEdit()
        self._collab_peer_port_edit.setPlaceholderText("端口")
        self._collab_peer_port_edit.setFixedWidth(80)
        peer_row = QHBoxLayout()
        peer_row.addWidget(self._collab_peer_ip_edit, stretch=1)
        peer_row.addWidget(self._collab_peer_port_edit)
        add_peer_btn = QPushButton("连接")
        add_peer_btn.clicked.connect(self._on_add_manual_peer)
        peer_row.addWidget(add_peer_btn)
        peer_wrap = QWidget()
        peer_wrap.setLayout(peer_row)
        form.addRow("手动连接", peer_wrap)

        self._collab_members_list = QListWidget()
        self._collab_members_list.setMaximumHeight(140)
        form.addRow("在线成员", self._collab_members_list)

        # Pairing code — connect without knowing IPs (mDNS-failure fallback).
        self._collab_pairing_show_btn = QPushButton("显示我的配对码")
        self._collab_pairing_show_btn.clicked.connect(self._on_collab_show_pairing)
        form.addRow("配对码", self._collab_pairing_show_btn)

        self._collab_pairing_input = QLineEdit()
        self._collab_pairing_input.setPlaceholderText("粘贴队友的配对码")
        pair_join_btn = QPushButton("加入")
        pair_join_btn.clicked.connect(self._on_collab_join_pairing)
        pair_row = QHBoxLayout()
        pair_row.addWidget(self._collab_pairing_input, stretch=1)
        pair_row.addWidget(pair_join_btn)
        pair_wrap = QWidget()
        pair_wrap.setLayout(pair_row)
        form.addRow("输入配对码", pair_wrap)

        note = QLabel(
            "同一协作组码的设备会自动同步标本编号占用情况，避免重复编号。\n"
            "撤销占用（在「协作管理」中作废）会释放编号，供任何人重新使用。"
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        form.addRow("", note)

        tab.body.addLayout(form)
        tab.body.addStretch()
        self._tabs.addTab(tab, tr("协作"))

        # Live updates from the running service, if present.
        svc = getattr(self.ctx, "collab_service", None)
        if svc is not None:
            try:
                svc.peers_changed.connect(self._refresh_collab_members)
                svc.peers_changed.connect(self._refresh_collab_health)
                svc.server_ready.connect(lambda _p: self._refresh_collab_addr())
                svc.diagnostics_changed.connect(self._refresh_collab_health)
            except Exception:  # noqa: BLE001
                pass

    def _build_tab_ui(self) -> None:
        """界面 tab — mirrors renderGlobalSettings():
        fontScale slider, icon emoji fields, useRealCompression debug switch,
        and keyboard shortcut recording (QKeySequenceEdit, Qt-native equivalent
        of web's ensureShortcutsSettings / renderShortcutScope).
        """
        tab = _ScrollTab()

        # ── 界面风格 ──────────────────────────────────────────────────────────
        theme_box = QGroupBox(tr("界面风格"))
        theme_form = QFormLayout(theme_box)
        theme_form.setHorizontalSpacing(16)
        theme_form.setVerticalSpacing(8)

        self._theme_combo = QComboBox()
        from app.config.theme import THEME_NAMES
        for key in _THEME_CHOICES:
            self._theme_combo.addItem(tr(THEME_NAMES.get(key, key)), key)
        self._theme_combo.setToolTip(tr("切换后立即生效，并在下次启动时保持"))
        self._theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        theme_form.addRow(tr("风格"), self._theme_combo)

        # 界面语言 (UI language) — endonyms in the picker; applies immediately.
        self._lang_combo = QComboBox()
        self._lang_combo.addItem(tr("中文"), "zh")
        self._lang_combo.addItem(tr("English"), "en")
        self._lang_combo.setToolTip(tr("语言切换会立即生效。"))
        self._lang_combo.currentIndexChanged.connect(self._on_language_changed)
        theme_form.addRow(tr("界面语言"), self._lang_combo)

        theme_note = QLabel(tr("保留当前风格，同时提供两套新设计用于对比整体观感。"))
        theme_note.setObjectName("MutedSmall")
        theme_note.setWordWrap(True)
        theme_form.addRow("", theme_note)

        self._perf_mode_chk = QCheckBox(tr("性能模式（关闭卡片阴影与背景渐变）"))
        self._perf_mode_chk.setToolTip(
            tr("远程桌面/低性能环境下减少重绘，操作更顺滑；重启后生效。")
        )
        self._perf_mode_chk.stateChanged.connect(self._on_perf_mode_changed)
        theme_form.addRow(tr("性能"), self._perf_mode_chk)

        perf_note = QLabel(tr("远程控制 Linux 卡顿时建议开启；界面会变扁平，重启生效。"))
        perf_note.setObjectName("MutedSmall")
        perf_note.setWordWrap(True)
        theme_form.addRow("", perf_note)

        tab.body.addWidget(theme_box)
        tab.body.addSpacing(12)

        # ── 字体 ──────────────────────────────────────────────────────────────
        font_box = QGroupBox(tr("字体"))
        font_form = QFormLayout(font_box)
        font_form.setHorizontalSpacing(16)
        font_form.setVerticalSpacing(8)

        # 字体族选择 — 系统默认 + 常用字体 + 已安装字体列表
        self._font_family_combo = QComboBox()
        self._font_family_combo.addItem(tr("系统默认（自动）"), "")
        for fam in _installed_font_families():
            self._font_family_combo.addItem(fam, fam)
        self._font_family_combo.setToolTip(tr("选择全局字体，立即生效并在下次启动保持；未安装字体会按系统回退"))
        self._font_family_combo.currentIndexChanged.connect(self._on_font_family_changed)
        font_form.addRow(tr("字体"), self._font_family_combo)

        # 字体大小 — 缩放倍率 + 百分比
        size_row = QHBoxLayout()
        size_row.setContentsMargins(0, 0, 0, 0)
        size_row.setSpacing(10)

        self._font_scale_spin = QDoubleSpinBox()
        self._font_scale_spin.setRange(0.7, 1.5)
        self._font_scale_spin.setSingleStep(0.05)
        self._font_scale_spin.setDecimals(2)
        self._font_scale_spin.setValue(1.0)
        self._font_scale_spin.setFixedWidth(80)
        self._font_scale_spin.setToolTip(tr("全局字体大小倍率，0.7–1.5；立即生效"))
        size_row.addWidget(self._font_scale_spin)

        self._font_scale_pct_label = QLabel("100%")
        self._font_scale_pct_label.setObjectName("Muted")
        size_row.addWidget(self._font_scale_pct_label)
        size_row.addStretch()
        font_form.addRow(tr("字体大小"), size_row)

        self._font_scale_spin.valueChanged.connect(self._on_font_scale_changed)

        note = QLabel(tr("常用中文字体和 Times 等字体固定列在前面；未安装字体会自动回退。"))
        note.setObjectName("MutedSmall")
        note.setWordWrap(True)
        font_form.addRow("", note)

        tab.body.addWidget(font_box)
        tab.body.addSpacing(12)

        # ── 图标自定义 ────────────────────────────────────────────────────────
        icon_box = QGroupBox(tr("图标自定义（emoji 替换）"))
        icon_form = QFormLayout(icon_box)
        icon_form.setHorizontalSpacing(16)
        icon_form.setVerticalSpacing(8)

        self._icon_gps_edit = QLineEdit()
        self._icon_gps_edit.setPlaceholderText("📡")
        self._icon_gps_edit.setMaxLength(8)
        self._icon_gps_edit.setToolTip(tr("GPS / 定位 图标（默认 📡）"))
        self._icon_gps_edit.editingFinished.connect(self._save_ui)
        icon_form.addRow(tr("GPS / 定位"), self._icon_gps_edit)

        self._icon_map_edit = QLineEdit()
        self._icon_map_edit.setPlaceholderText("📍")
        self._icon_map_edit.setMaxLength(8)
        self._icon_map_edit.setToolTip(tr("地图选点 图标（默认 📍）"))
        self._icon_map_edit.editingFinished.connect(self._save_ui)
        icon_form.addRow(tr("地图选点"), self._icon_map_edit)

        self._icon_folder_edit = QLineEdit()
        self._icon_folder_edit.setPlaceholderText("📁")
        self._icon_folder_edit.setMaxLength(8)
        self._icon_folder_edit.setToolTip(tr("文件夹 图标（默认 📁）"))
        self._icon_folder_edit.editingFinished.connect(self._save_ui)
        icon_form.addRow(tr("文件夹"), self._icon_folder_edit)

        self._icon_search_edit = QLineEdit()
        self._icon_search_edit.setPlaceholderText("🔍")
        self._icon_search_edit.setMaxLength(8)
        self._icon_search_edit.setToolTip(tr("搜索 图标（默认 🔍）"))
        self._icon_search_edit.editingFinished.connect(self._save_ui)
        icon_form.addRow(tr("搜索"), self._icon_search_edit)

        tab.body.addWidget(icon_box)
        tab.body.addSpacing(12)

        # ── 键盘快捷键 (mirrors ensureShortcutsSettings / renderShortcutScope) ──
        shortcut_box = QGroupBox(tr("键盘快捷键"))
        shortcut_form = QFormLayout(shortcut_box)
        shortcut_form.setHorizontalSpacing(16)
        shortcut_form.setVerticalSpacing(8)

        # 截图（系统级全局，默认 Alt+A）
        self._sc_screenshot = QKeySequenceEdit()
        self._sc_screenshot.setToolTip(
            tr("区域截图快捷键，默认 Alt+A；改动立即生效，系统级全局（后台也可触发）")
        )
        self._sc_screenshot.editingFinished.connect(self._on_screenshot_shortcut_changed)
        shortcut_form.addRow(tr("区域截图（全局）"), self._sc_screenshot)

        from app.utils.global_hotkey import available as _gh_available
        if not _gh_available():
            gh_note = QLabel(
                tr(
                    "提示：未安装 pynput，截图快捷键仅在本软件窗口前台时可用；"
                    "安装 pynput 后支持系统级全局触发（pip install pynput）。"
                )
            )
            gh_note.setObjectName("MutedSmall")
            gh_note.setWordWrap(True)
            shortcut_form.addRow("", gh_note)

        # monitor scope
        self._sc_monitor_activate = QKeySequenceEdit()
        self._sc_monitor_activate.setToolTip(tr("工作台：激活标本（monitor/activate）"))
        self._sc_monitor_activate.editingFinished.connect(self._save_ui)
        shortcut_form.addRow(tr("激活标本（监控）"), self._sc_monitor_activate)

        self._sc_monitor_deactivate = QKeySequenceEdit()
        self._sc_monitor_deactivate.setToolTip(tr("工作台：去激活（monitor/deactivate）"))
        self._sc_monitor_deactivate.editingFinished.connect(self._save_ui)
        shortcut_form.addRow(tr("去激活（监控）"), self._sc_monitor_deactivate)

        # labels scope
        self._sc_labels_print = QKeySequenceEdit()
        self._sc_labels_print.setToolTip(tr("标签打印：打印（labels/print）"))
        self._sc_labels_print.editingFinished.connect(self._save_ui)
        shortcut_form.addRow(tr("打印标签"), self._sc_labels_print)

        self._sc_labels_next = QKeySequenceEdit()
        self._sc_labels_next.setToolTip(tr("标签打印：下一个（labels/next）"))
        self._sc_labels_next.editingFinished.connect(self._save_ui)
        shortcut_form.addRow(tr("下一个标签"), self._sc_labels_next)

        sc_note = QLabel(tr("录制快捷键：点击输入框后按下组合键。留空使用默认值。"))
        sc_note.setObjectName("MutedSmall")
        sc_note.setWordWrap(True)
        shortcut_box.layout().addRow("", sc_note)  # type: ignore[union-attr]

        tab.body.addWidget(shortcut_box)
        tab.body.addSpacing(12)

        # ── 调试：真实压缩 ────────────────────────────────────────────────────
        debug_box = QGroupBox(tr("调试选项"))
        debug_v = QVBoxLayout(debug_box)

        self._use_real_compression_chk = QCheckBox(
            tr("使用后端真实压缩（文件需实际存在于项目目录）")
        )
        self._use_real_compression_chk.setChecked(False)
        self._use_real_compression_chk.setToolTip(
            tr(
                "对应 web useRealCompression 调试开关。"
                "关闭时系统只模拟压缩结果状态，不调用 cjxl / archiver。"
            )
        )
        self._use_real_compression_chk.stateChanged.connect(self._save_ui)
        debug_v.addWidget(self._use_real_compression_chk)

        tab.body.addWidget(debug_box)
        tab.body.addStretch()

        self._tabs.addTab(tab, tr("界面"))

    def _build_tab_about(self) -> None:
        """关于 tab — version, log dir."""
        tab = _ScrollTab()

        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        ver_label = QLabel(APP_VERSION)
        ver_label.setObjectName("Accent")
        form.addRow("版本", ver_label)

        platform_label = QLabel(
            f"{platform.system()} {platform.release()} / Python {platform.python_version()}"
        )
        platform_label.setObjectName("Muted")
        form.addRow("运行环境", platform_label)

        # Log directory (QSettings INI file path)
        from PyQt6.QtCore import QSettings
        qs = QSettings("SpecimenPhotoWorkbench", "标本照片工作台")
        log_path_label = QLabel(qs.fileName())
        log_path_label.setObjectName("Mono")
        log_path_label.setWordWrap(True)
        form.addRow("配置文件路径", log_path_label)

        tab.body.addLayout(form)

        tab.body.addSpacing(16)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("Panel")
        tab.body.addWidget(sep)
        tab.body.addSpacing(8)

        about_text = QLabel(
            "标本照片工作台  |  Specimen Photo Workbench\n"
            "分类学标本拍摄 · 景深叠加 · JPEG XL 归档 · 标签打印\n"
        )
        about_text.setObjectName("Muted")
        about_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_text.setWordWrap(True)
        tab.body.addWidget(about_text)

        tab.body.addStretch()
        self._tabs.addTab(tab, tr("关于"))

    # ── Load / save helpers ───────────────────────────────────────────────

    def _load_all(self) -> None:
        """Read all settings from QSettings and populate widgets."""
        qs = self.ctx.settings._qs  # direct QSettings access

        # Helicon tab — exe path + preset params
        exe_val = qs.value(_K_HELICON_EXE, "")
        self._helicon_exe_edit.setText(exe_val)
        if exe_val:
            os.environ["HELICON_FOCUS_PATH"] = exe_val
        method_idx = int(qs.value(_K_HELICON_METHOD, 1))
        self._helicon_param_panel.set_params({
            "method": method_idx if 0 <= method_idx <= 2 else 1,
            "radius": float(qs.value(_K_HELICON_RADIUS, 8.0)),
            "smoothing": int(qs.value(_K_HELICON_SMOOTHING, 4)),
        })
        self._quality_spin.setValue(int(qs.value(_K_HELICON_QUALITY, 95)))
        # Refresh detected / effective display
        self._refresh_helicon_display()

        # Helicon advanced params
        fmt = qs.value(_K_HELICON_OUTPUT_FORMAT, "tif")
        fmt_idx = 1 if fmt == "jpg" else 0
        self._output_format_combo.setCurrentIndex(fmt_idx)
        tiff_map = {"u": 0, "lzw": 1, "zip": 2}
        tiff_val = str(qs.value(_K_HELICON_TIFF_COMPRESSION, "u"))
        self._tiff_compression_combo.setCurrentIndex(tiff_map.get(tiff_val, 0))
        run_map = {"silent": 0, "progress": 1, "gui": 2}
        run_val = str(qs.value(_K_HELICON_RUN_MODE, "silent"))
        self._run_mode_combo.setCurrentIndex(run_map.get(run_val, 0))
        self._concurrency_spin.setValue(
            max(1, min(8, int(qs.value(_K_HELICON_CONCURRENCY, 1))))
        )
        raw_depth = qs.value(_K_HELICON_SAVE_DEPTH_MAP, "false")
        self._save_depth_map_chk.setChecked(str(raw_depth).lower() == "true")

        # Archive tab
        effort_idx = int(qs.value(_K_JXL_EFFORT, 0))
        self._jxl_effort_combo.setCurrentIndex(
            effort_idx if 0 <= effort_idx < self._jxl_effort_combo.count() else 0
        )
        # delete_jpg: stored as string "true"/"false" — default False (hard rule)
        raw_del = qs.value(_K_DELETE_JPG, "false")
        delete_jpg = str(raw_del).lower() == "true"
        self._delete_jpg_chk.setChecked(delete_jpg)

        # Project tab
        proj_dir = self.ctx.current_project_dir or ""
        self._project_dir_edit.setText(proj_dir)
        self._incoming_edit.setText(qs.value(_K_INCOMING_SUBDIR, "incoming-jpg"))
        self._results_edit.setText(qs.value(_K_RESULTS_SUBDIR, "results"))
        self._amap_key_edit.setText(self.ctx.settings.amap_web_key)
        self._load_recent_projects()

        # User tab
        self._current_user_edit.setText(qs.value(_K_CURRENT_USER, ""))

        # Collaboration tab
        self._collab_enabled_chk.setChecked(self.ctx.settings.collab_enabled)
        self._collab_team_code_edit.setText(self.ctx.settings.team_code)
        self._refresh_collab_addr()
        self._refresh_collab_members()
        self._refresh_collab_health()

        # Workbench tab
        self._auto_watch_chk.setChecked(
            str(qs.value(_K_WB_AUTO_WATCH, "true")).lower() != "false"
        )
        self._auto_activate_new_chk.setChecked(
            str(qs.value(_K_WB_AUTO_ACTIVATE_NEW, "false")).lower() == "true"
        )
        self._grouping_auto_watch_chk.setChecked(
            str(qs.value(_K_WB_GROUPING_AUTO_WATCH, "false")).lower() == "true"
        )
        mode_map = {"compose": 0, "organize": 1, "compose+organize": 2}
        mode_val = str(qs.value(_K_WB_GROUPING_AUTO_WATCH_MODE, "compose+organize"))
        self._grouping_mode_combo.setCurrentIndex(mode_map.get(mode_val, 2))
        fv_map = {"jpg-tif": 0, "with-zip": 1, "all": 2}
        fv_val = str(qs.value(_K_WB_FILE_VIEW_MODE, "jpg-tif"))
        self._file_view_mode_combo.setCurrentIndex(fv_map.get(fv_val, 0))

        # UI / 界面 tab
        current_theme = self.ctx.settings.current_theme
        theme_idx = self._theme_combo.findData(current_theme)
        if theme_idx < 0:
            theme_idx = self._theme_combo.findData("classic_light")
        self._theme_combo.blockSignals(True)
        self._theme_combo.setCurrentIndex(max(0, theme_idx))
        self._theme_combo.blockSignals(False)

        lang_idx = self._lang_combo.findData(self.ctx.settings.current_language)
        self._lang_combo.blockSignals(True)
        self._lang_combo.setCurrentIndex(max(0, lang_idx))
        self._lang_combo.blockSignals(False)

        self._perf_mode_chk.blockSignals(True)
        self._perf_mode_chk.setChecked(self.ctx.settings.performance_mode)
        self._perf_mode_chk.blockSignals(False)

        try:
            font_scale = float(qs.value(_K_UI_FONT_SCALE, 1.0))
        except (TypeError, ValueError):
            font_scale = 1.0
        font_scale = max(0.7, min(1.5, font_scale))
        self._font_scale_spin.blockSignals(True)
        self._font_scale_spin.setValue(font_scale)
        self._font_scale_spin.blockSignals(False)
        self._font_scale_pct_label.setText(f"{round(font_scale * 100)}%")

        saved_family = qs.value(_K_UI_FONT_FAMILY, "", type=str) or ""
        fam_idx = self._font_family_combo.findData(saved_family)
        self._font_family_combo.blockSignals(True)
        self._font_family_combo.setCurrentIndex(fam_idx if fam_idx >= 0 else 0)
        self._font_family_combo.blockSignals(False)

        self._icon_gps_edit.setText(qs.value(_K_UI_ICON_GPS, ""))
        self._icon_map_edit.setText(qs.value(_K_UI_ICON_MAP, ""))
        self._icon_folder_edit.setText(qs.value(_K_UI_ICON_FOLDER, ""))
        self._icon_search_edit.setText(qs.value(_K_UI_ICON_SEARCH, ""))

        raw_real = qs.value(_K_DEBUG_USE_REAL_COMPRESSION, "false")
        self._use_real_compression_chk.setChecked(str(raw_real).lower() == "true")

        # Shortcuts
        sc_ma = qs.value(_K_SHORTCUT_MONITOR_ACTIVATE, "")
        self._sc_monitor_activate.setKeySequence(QKeySequence(str(sc_ma)))
        sc_md = qs.value(_K_SHORTCUT_MONITOR_DEACTIVATE, "")
        self._sc_monitor_deactivate.setKeySequence(QKeySequence(str(sc_md)))
        sc_lp = qs.value(_K_SHORTCUT_LABELS_PRINT, "")
        self._sc_labels_print.setKeySequence(QKeySequence(str(sc_lp)))
        sc_ln = qs.value(_K_SHORTCUT_LABELS_NEXT, "")
        self._sc_labels_next.setKeySequence(QKeySequence(str(sc_ln)))
        sc_shot = qs.value(_K_SHORTCUT_SCREENSHOT, "") or "Alt+A"
        self._sc_screenshot.blockSignals(True)
        self._sc_screenshot.setKeySequence(QKeySequence(str(sc_shot)))
        self._sc_screenshot.blockSignals(False)

        # Preset list widget
        self._refresh_preset_list_widget()

    def _on_perf_mode_changed(self) -> None:
        self.ctx.settings.performance_mode = self._perf_mode_chk.isChecked()
        self.ctx.settings.sync()
        from app.utils import ui
        ui.info(self, "性能模式", "已保存。重启软件后生效。")

    def _on_language_changed(self) -> None:
        lang = self._lang_combo.currentData() or "zh"
        self.ctx.settings.current_language = str(lang)
        self.ctx.settings.sync()
        from app.config.i18n import set_language
        set_language(str(lang))
        win = self.window()
        handler = getattr(win, "retranslate_ui", None)
        if callable(handler):
            handler()
        else:
            self.retranslate_ui()

    def _on_theme_changed(self) -> None:
        current_tab = self._tabs.currentIndex()
        key = self._theme_combo.currentData() or "classic_light"
        self.ctx.settings.current_theme = str(key)
        self.ctx.settings.sync()

        from app.config.theme import apply_theme
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(apply_theme(str(key)))
        _refresh_palette()
        self._setup_ui()
        self._tabs.setCurrentIndex(min(current_tab, self._tabs.count() - 1))
        self._load_all()

    def _refresh_helicon_display(self) -> None:
        """Update auto-detected and effective path labels from stored/detected state."""
        qs = self.ctx.settings._qs
        stored_exe = qs.value(_K_HELICON_EXE, "") or ""
        if stored_exe and os.path.isfile(stored_exe):
            self._detected_path_label.setText(stored_exe)
            self._effective_path_label.setText(stored_exe)
            self._detect_status_badge.setText("✓ 可用")
            self._detect_status_badge.setStyleSheet(
                f"font-size: 12px; font-weight: 600; color: {_C_SUCCESS};"
                "background: transparent;"
            )
        else:
            self._detected_path_label.setText("（未检测到）")
            self._effective_path_label.setText("—")
            self._detect_status_badge.setText("未检测到")
            self._detect_status_badge.setStyleSheet(
                f"font-size: 12px; font-weight: 600; color: {_C_WARN};"
                "background: transparent;"
            )

    def _load_recent_projects(self) -> None:
        self._recent_list.clear()
        qs = self.ctx.settings._qs
        raw = qs.value(_K_RECENT_PROJECTS, "")
        paths = [p for p in str(raw).split("\n") if p.strip()] if raw else []
        for p in paths:
            self._recent_list.addItem(p)

    def _save_project(self) -> None:
        qs = self.ctx.settings._qs
        incoming = self._incoming_edit.text().strip() or "incoming-jpg"
        results = self._results_edit.text().strip() or "results"
        qs.setValue(_K_INCOMING_SUBDIR, incoming)
        qs.setValue(_K_RESULTS_SUBDIR, results)
        self.ctx.settings.amap_web_key = self._amap_key_edit.text()
        self.ctx.settings.sync()

    def _save_helicon(self) -> None:
        qs = self.ctx.settings._qs
        qs.setValue(_K_HELICON_EXE, self._helicon_exe_edit.text().strip())
        _p = self._helicon_param_panel.get_params()
        qs.setValue(_K_HELICON_METHOD, _p["method"])
        qs.setValue(_K_HELICON_RADIUS, _p["radius"])
        qs.setValue(_K_HELICON_SMOOTHING, _p["smoothing"])
        qs.setValue(_K_HELICON_QUALITY, self._quality_spin.value())
        self.ctx.settings.sync()

    def _save_archive(self) -> None:
        qs = self.ctx.settings._qs
        qs.setValue(_K_JXL_EFFORT, self._jxl_effort_combo.currentIndex())
        # Store as explicit "true"/"false" string for unambiguous retrieval
        qs.setValue(_K_DELETE_JPG, "true" if self._delete_jpg_chk.isChecked() else "false")
        self.ctx.settings.sync()

    def _save_helicon_advanced(self) -> None:
        """Persist Helicon advanced output params (mirrors web 高级参数 block)."""
        qs = self.ctx.settings._qs
        fmt_idx = self._output_format_combo.currentIndex()
        qs.setValue(_K_HELICON_OUTPUT_FORMAT, "jpg" if fmt_idx == 1 else "tif")
        tiff_vals = ["u", "lzw", "zip"]
        qs.setValue(
            _K_HELICON_TIFF_COMPRESSION,
            tiff_vals[self._tiff_compression_combo.currentIndex()]
            if self._tiff_compression_combo.currentIndex() < len(tiff_vals)
            else "u",
        )
        run_vals = ["silent", "progress", "gui"]
        qs.setValue(
            _K_HELICON_RUN_MODE,
            run_vals[self._run_mode_combo.currentIndex()]
            if self._run_mode_combo.currentIndex() < len(run_vals)
            else "silent",
        )
        qs.setValue(_K_HELICON_CONCURRENCY, self._concurrency_spin.value())
        qs.setValue(
            _K_HELICON_SAVE_DEPTH_MAP,
            "true" if self._save_depth_map_chk.isChecked() else "false",
        )
        self.ctx.settings.sync()

    def _save_workbench(self) -> None:
        """Persist workbench auto-watch toggles (mirrors saveV4Settings)."""
        qs = self.ctx.settings._qs
        qs.setValue(_K_WB_AUTO_WATCH, "true" if self._auto_watch_chk.isChecked() else "false")
        qs.setValue(
            _K_WB_AUTO_ACTIVATE_NEW,
            "true" if self._auto_activate_new_chk.isChecked() else "false",
        )
        qs.setValue(
            _K_WB_GROUPING_AUTO_WATCH,
            "true" if self._grouping_auto_watch_chk.isChecked() else "false",
        )
        mode_vals = ["compose", "organize", "compose+organize"]
        idx = self._grouping_mode_combo.currentIndex()
        qs.setValue(
            _K_WB_GROUPING_AUTO_WATCH_MODE,
            mode_vals[idx] if 0 <= idx < len(mode_vals) else "compose+organize",
        )
        fv_vals = ["jpg-tif", "with-zip", "all"]
        fv_idx = self._file_view_mode_combo.currentIndex()
        qs.setValue(
            _K_WB_FILE_VIEW_MODE,
            fv_vals[fv_idx] if 0 <= fv_idx < len(fv_vals) else "jpg-tif",
        )
        self.ctx.settings.sync()

    def _save_user(self) -> None:
        qs = self.ctx.settings._qs
        name = self._current_user_edit.text().strip()
        qs.setValue(_K_CURRENT_USER, name)
        self.ctx.settings.sync()

    # ── Collaboration ─────────────────────────────────────────────────────

    def _save_collab(self) -> None:
        """Persist collab enable + team code, and push code to a live service."""
        self.ctx.settings.collab_enabled = self._collab_enabled_chk.isChecked()
        code = self._collab_team_code_edit.text().strip()
        self.ctx.settings.team_code = code
        self.ctx.settings.sync()
        svc = getattr(self.ctx, "collab_service", None)
        if svc is not None:
            try:
                svc.set_group_code(code)
            except Exception:  # noqa: BLE001
                pass

    def _on_collab_enabled_toggled(self, on: bool) -> None:
        """Persist the flag and start/stop the live service immediately."""
        self._save_collab()
        svc = getattr(self.ctx, "collab_service", None)
        if svc is None:
            return
        try:
            if on and not svc.is_running():
                svc.start(
                    project_name=self.ctx.current_project_dir or "",
                    group_code=self._collab_team_code_edit.text().strip(),
                )
            elif not on and svc.is_running():
                svc.stop()
        except Exception:  # noqa: BLE001
            pass

    def _copy_collab_addr(self) -> None:
        from PyQt6.QtWidgets import QApplication
        text = self._collab_addr_edit.text().strip()
        if text and text != "—":
            QApplication.clipboard().setText(text)

    def _on_add_manual_peer(self) -> None:
        svc = getattr(self.ctx, "collab_service", None)
        if svc is None:
            return
        ip = self._collab_peer_ip_edit.text().strip()
        port_raw = self._collab_peer_port_edit.text().strip()
        if not ip or not port_raw.isdigit():
            return
        try:
            svc.add_manual_peer(ip, int(port_raw))
            self._collab_peer_ip_edit.clear()
            self._collab_peer_port_edit.clear()
        except Exception:  # noqa: BLE001
            pass

    def _refresh_collab_addr(self) -> None:
        svc = getattr(self.ctx, "collab_service", None)
        if svc is not None:
            try:
                self._collab_addr_edit.setText(svc.local_address())
            except Exception:  # noqa: BLE001
                pass

    def _refresh_collab_members(self) -> None:
        svc = getattr(self.ctx, "collab_service", None)
        self._collab_members_list.clear()
        if svc is None:
            return
        try:
            for peer in svc.peers():
                name = peer.hostname or peer.ip
                self._collab_members_list.addItem(f"{name}  ({peer.ip}:{peer.port})")
        except Exception:  # noqa: BLE001
            pass

    _HEALTH_COLOR = {"green": "#2e7d32", "yellow": "#f9a825", "red": "#c62828"}
    _HEALTH_LABEL = {"green": "正常", "yellow": "有注意事项", "red": "有阻断问题"}

    def _refresh_collab_health(self) -> None:
        svc = getattr(self.ctx, "collab_service", None)
        if svc is None:
            self._collab_health_light.setStyleSheet("color: #999;")
            self._collab_health_text.setText("未启用")
            return
        try:
            svc.run_diagnostics()
            health = svc.overall_health()
        except Exception:  # noqa: BLE001
            health = "red"
        self._collab_health_light.setStyleSheet(
            f"color: {self._HEALTH_COLOR.get(health, '#999')};")
        self._collab_health_text.setText(self._HEALTH_LABEL.get(health, "—"))

    def _on_collab_diagnose(self) -> None:
        from app.widgets.collab_diagnostics_dialog import CollabDiagnosticsDialog
        svc = getattr(self.ctx, "collab_service", None)
        dlg = CollabDiagnosticsDialog(svc, parent=self)
        # Persist a group code adopted via a one-click fix.
        dlg.group_adopted.connect(self._on_group_adopted)
        dlg.exec()
        self._refresh_collab_health()

    def _on_group_adopted(self, code: str) -> None:
        self._collab_team_code_edit.setText(code)
        self._save_collab()

    def _on_collab_scan(self) -> None:
        svc = getattr(self.ctx, "collab_service", None)
        if svc is None:
            from app.utils.ui import info as _info
            _info(self, "搜索局域网", "请先启用协作。")
            return
        try:
            found = svc.scan_lan()
        except Exception:  # noqa: BLE001
            found = []
        from app.utils.ui import info as _info
        _info(self, "搜索完成", f"发现 {len(found)} 台设备。")
        self._refresh_collab_members()
        self._refresh_collab_health()

    def _on_collab_show_pairing(self) -> None:
        from app.utils.ui import info as _info
        from app.widgets.collab_pairing import encode_pairing
        svc = getattr(self.ctx, "collab_service", None)
        if svc is None:
            _info(self, "配对码", "请先启用协作。")
            return
        addr = svc.local_address()
        try:
            ip, port = addr.split(":")
            code = encode_pairing(ip, int(port), svc.group_code)
        except Exception:  # noqa: BLE001
            _info(self, "配对码", "暂时无法生成配对码。")
            return
        _info(self, "我的配对码",
              f"把这串配对码发给队友,他们粘贴即可连接:\n\n{code}")

    def _on_collab_join_pairing(self) -> None:
        from app.utils.ui import info as _info, warn as _warn
        from app.widgets.collab_pairing import decode_pairing
        svc = getattr(self.ctx, "collab_service", None)
        if svc is None:
            _info(self, "配对码", "请先启用协作。")
            return
        raw = self._collab_pairing_input.text().strip()
        try:
            pi = decode_pairing(raw)
        except ValueError as exc:
            _warn(self, "配对码无效", str(exc))
            return
        if pi.group_code:
            self._collab_team_code_edit.setText(pi.group_code)
            self._save_collab()
        try:
            svc.add_manual_peer(pi.ip, pi.port)
        except Exception:  # noqa: BLE001
            pass
        self._collab_pairing_input.clear()
        _info(self, "已加入", f"已连接 {pi.ip}:{pi.port}。")
        self._refresh_collab_members()
        self._refresh_collab_health()

    def _on_font_scale_changed(self, value: float) -> None:
        """Realtime: update percentage label, persist, and re-skin the app."""
        self._font_scale_pct_label.setText(f"{round(value * 100)}%")
        self._save_ui()
        self._apply_typography_live()

    def _on_font_family_changed(self) -> None:
        """字体族切换 → 立即生效 + 持久化。"""
        self._save_ui()
        self._apply_typography_live()

    def _on_screenshot_shortcut_changed(self) -> None:
        """截图快捷键录制完成 → 持久化 + 立即重绑（窗口内 + 系统全局）。"""
        self._save_ui()
        seq = self._sc_screenshot.keySequence().toString() or "Alt+A"
        from app.main_window import MainWindow
        for w in QApplication.instance().topLevelWidgets():
            if isinstance(w, MainWindow):
                w.rebind_screenshot_shortcut(seq)
                break

    def _apply_typography_live(self) -> None:
        """Push current 字体 / 字体大小 into the live theme + default font."""
        from app.config.theme import set_typography, apply_theme, apply_default_font
        family = self._font_family_combo.currentData() or ""
        set_typography(scale=self._font_scale_spin.value(), family=str(family))
        app = QApplication.instance()
        if app is not None:
            apply_default_font(app)
            app.setStyleSheet(apply_theme(self.ctx.settings.current_theme))
        _refresh_palette()

    def _save_ui(self) -> None:
        """Persist 界面 tab settings (fontScale / icons / shortcuts / useRealCompression)."""
        qs = self.ctx.settings._qs
        qs.setValue(_K_UI_FONT_SCALE, self._font_scale_spin.value())
        qs.setValue(_K_UI_FONT_FAMILY, self._font_family_combo.currentData() or "")
        qs.setValue(_K_UI_ICON_GPS, self._icon_gps_edit.text())
        qs.setValue(_K_UI_ICON_MAP, self._icon_map_edit.text())
        qs.setValue(_K_UI_ICON_FOLDER, self._icon_folder_edit.text())
        qs.setValue(_K_UI_ICON_SEARCH, self._icon_search_edit.text())
        qs.setValue(
            _K_DEBUG_USE_REAL_COMPRESSION,
            "true" if self._use_real_compression_chk.isChecked() else "false",
        )
        # Shortcuts — store as portable string (QKeySequence.toString)
        qs.setValue(
            _K_SHORTCUT_MONITOR_ACTIVATE,
            self._sc_monitor_activate.keySequence().toString(),
        )
        qs.setValue(
            _K_SHORTCUT_MONITOR_DEACTIVATE,
            self._sc_monitor_deactivate.keySequence().toString(),
        )
        qs.setValue(
            _K_SHORTCUT_LABELS_PRINT,
            self._sc_labels_print.keySequence().toString(),
        )
        qs.setValue(
            _K_SHORTCUT_LABELS_NEXT,
            self._sc_labels_next.keySequence().toString(),
        )
        qs.setValue(
            _K_SHORTCUT_SCREENSHOT,
            self._sc_screenshot.keySequence().toString(),
        )
        self.ctx.settings.sync()

    # ── Helicon preset CRUD ───────────────────────────────────────────────────

    def _load_presets(self) -> list:
        """Read preset list from QSettings (JSON)."""
        import json
        qs = self.ctx.settings._qs
        raw = qs.value(_K_HELICON_PRESETS_JSON, "[]")
        try:
            data = json.loads(str(raw))
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, TypeError):
            pass
        return []

    def _save_preset_list(self, presets: list) -> None:
        """Persist preset list to QSettings."""
        import json
        qs = self.ctx.settings._qs
        qs.setValue(_K_HELICON_PRESETS_JSON, json.dumps(presets))
        self.ctx.settings.sync()

    def _refresh_preset_list_widget(self) -> None:
        """Reload QListWidget from QSettings."""
        self._preset_list.clear()
        for p in self._load_presets():
            self._preset_list.addItem(p.get("name", ""))

    def _save_current_as_preset(self) -> None:
        """保存为预设 — upsert by name (mirrors server.js:2449-2452)."""
        name = self._preset_name_edit.text().strip()
        if not name:
            return  # 空名称：静默忽略
        from datetime import datetime, timezone
        params = {
            "method": self._helicon_param_panel.get_params()["method"] + 1,
            "radius": self._helicon_param_panel.get_params()["radius"],
            "smoothing": self._helicon_param_panel.get_params()["smoothing"],
            "quality": self._quality_spin.value(),
        }
        preset = {
            "name": name,
            "params": params,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        presets = self._load_presets()
        existing = next((i for i, p in enumerate(presets) if p.get("name") == name), -1)
        if existing >= 0:
            presets[existing] = preset
        else:
            presets.append(preset)
        self._save_preset_list(presets)
        self._refresh_preset_list_widget()

    def _apply_selected_preset(self) -> None:
        """应用选中预设 — fill spinboxes + save."""
        item = self._preset_list.currentItem()
        if not item:
            return
        name = item.text()
        presets = self._load_presets()
        preset = next((p for p in presets if p.get("name") == name), None)
        if not preset:
            return
        params = preset.get("params", {})
        method_idx = int(params.get("method", 1)) - 1  # 1-based → 0-based index
        method_idx = max(0, min(method_idx, 2))
        self._helicon_param_panel.set_params({
            "method": method_idx,
            "radius": float(params.get("radius", 8.0)),
            "smoothing": int(params.get("smoothing", 4)),
        })
        self._quality_spin.setValue(int(params.get("quality", 95)))
        self._save_helicon()

    def _delete_selected_preset(self) -> None:
        """删除选中预设 — remove from list (mirrors server.js:2462-2471)."""
        item = self._preset_list.currentItem()
        if not item:
            return
        name = item.text()
        presets = [p for p in self._load_presets() if p.get("name") != name]
        self._save_preset_list(presets)
        self._preset_name_edit.clear()
        self._refresh_preset_list_widget()

    # ── Helicon button handlers (web: 检测 / 保存 / 清除自定义 / 重新探测) ───

    def _show_status(self, msg: str, timeout_ms: int = 3000) -> None:
        """Show a brief message on the main window status bar."""
        win = self.window()
        if hasattr(win, "statusBar"):
            bar = win.statusBar()
            if bar:
                bar.showMessage(msg, timeout_ms)

    def _flash_button(self, btn: QPushButton, text: str, duration_ms: int = 1500) -> None:
        """按钮文字瞬时确认后恢复 — 路径/标签不变时唯一可见反馈（同 40ce2c9 对话框）。"""
        if btn.property("_flashing"):
            return
        orig = btn.text()
        btn.setProperty("_flashing", True)
        btn.setText(text)

        def _restore() -> None:
            btn.setText(orig)
            btn.setProperty("_flashing", False)

        # Timer parented to the button: it dies with the button, so leaving the
        # page within the flash window can't fire _restore on a dead widget.
        timer = QTimer(btn)
        timer.setSingleShot(True)
        timer.timeout.connect(_restore)
        timer.timeout.connect(timer.deleteLater)
        timer.start(duration_ms)

    def _on_test_click(self) -> None:
        """检测 — validate custom path then auto-detect (mirrors web testBtn handler)."""
        custom = self._helicon_exe_edit.text().strip()
        if custom:
            self._show_status("正在验证自定义路径…")
        else:
            self._show_status("正在自动探测 Helicon Focus…")
        self._save_helicon()
        self._detect_helicon(custom_path=custom)
        self._flash_button(self._test_btn, "已检测 ✓")

    def _on_save_click(self) -> None:
        """保存 — persist custom path."""
        self._save_helicon()
        self._refresh_helicon_display()
        self._show_status("✓ Helicon 设置已保存")
        self._flash_button(self._save_btn, "已保存 ✓")

    def _on_clear_click(self) -> None:
        """清除自定义 — wipe custom path and re-detect."""
        self._helicon_exe_edit.clear()
        self._save_helicon()
        self._detect_helicon()
        self._show_status("已清除自定义路径，已重新探测")
        self._flash_button(self._clear_btn, "已清除 ✓")

    def _on_redetect_click(self) -> None:
        """重新探测按钮 — 结果不变时标签一字不动，必须闪按钮确认。"""
        self._detect_helicon()
        self._flash_button(self._refresh_btn, "已重新探测 ✓")

    def _detect_helicon(self, custom_path: str = "") -> None:
        """重新探测 / auto-detect — calls helicon_service.detect_helicon()."""
        try:
            from app.services.helicon_service import detect_helicon, reset_helicon_cache
            reset_helicon_cache()
            found = detect_helicon(custom_path=custom_path)
        except Exception:
            found = None

        if found:
            self._helicon_exe_edit.setText(found)
            self._save_helicon()
            self._detected_path_label.setText(found)
            self._effective_path_label.setText(found)
            self._detect_status_badge.setText("✓ 可用")
            self._detect_status_badge.setStyleSheet(
                f"font-size: 12px; font-weight: 600; color: {_C_SUCCESS};"
                "background: transparent;"
            )
            self._show_status(f"✓ 检测成功: {found}", 5000)
        else:
            self._detected_path_label.setText("（未检测到）")
            self._effective_path_label.setText("—")
            self._detect_status_badge.setText("未检测到")
            self._detect_status_badge.setStyleSheet(
                f"font-size: 12px; font-weight: 600; color: {_C_WARN};"
                "background: transparent;"
            )
            if custom_path:
                self._show_status("✗ 自定义路径无效，自动探测也未找到 Helicon Focus", 5000)
            else:
                self._show_status("✗ 未检测到 Helicon Focus（Windows 专有工具，Linux 下需通过 WSL 路径配置）", 5000)

    # ── Project directory ─────────────────────────────────────────────────

    def _browse_project_dir(self) -> None:
        start = self.ctx.current_project_dir or os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(
            self, "选择项目目录", start
        )
        if chosen:
            self.ctx.current_project_dir = chosen
            self._project_dir_edit.setText(chosen)
            self._add_to_recent(chosen)

    def _add_to_recent(self, path: str) -> None:
        qs = self.ctx.settings._qs
        raw = qs.value(_K_RECENT_PROJECTS, "")
        paths = [p for p in str(raw).split("\n") if p.strip()] if raw else []
        # Move to front, deduplicate, cap at RECENT_MAX
        if path in paths:
            paths.remove(path)
        paths.insert(0, path)
        paths = paths[:_RECENT_MAX]
        qs.setValue(_K_RECENT_PROJECTS, "\n".join(paths))
        self.ctx.settings.sync()
        self._load_recent_projects()

    def _open_recent(self) -> None:
        item = self._recent_list.currentItem()
        if item:
            path = item.text()
            if os.path.isdir(path):
                self.ctx.current_project_dir = path
                self._project_dir_edit.setText(path)

    def _clear_recent(self) -> None:
        qs = self.ctx.settings._qs
        qs.remove(_K_RECENT_PROJECTS)
        self.ctx.settings.sync()
        self._recent_list.clear()

    # ── Legacy helicon exe accessors (kept for round-trip tests) ──────────

    def _browse_helicon_exe(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "选择 HeliconFocus.exe",
            self._helicon_exe_edit.text() or "",
            "Executable (*.exe);;All files (*)"
        )
        if chosen:
            self._helicon_exe_edit.setText(chosen)
            self._save_helicon()


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
