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

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
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

_K_JXL_EFFORT = "archive/jxl_effort"
_K_DELETE_JPG = "archive/delete_jpg"  # default False — hard rule

_K_CURRENT_USER = "user/current_user"

_K_HELICON_PRESETS_JSON = "helicon/presets_json"

_RECENT_MAX = 10

# ── Theme colours (mirrors CSS :root tokens) ──────────────────────────────────

_C_BG = "#08161b"
_C_PANEL = "#10242a"
_C_TEXT = "#eef3ef"
_C_MUTED = "#87a2a1"
_C_ACCENT = "#29b9ab"
_C_SUCCESS = "#36c98f"
_C_WARN = "#f1bd57"
_C_DANGER = "#e66e63"
_C_BORDER = "rgba(145, 182, 181, 0.18)"


def _btn_style(variant: str = "outline") -> str:
    """Return inline QSS for small action buttons."""
    if variant == "primary":
        return (
            "QPushButton {"
            "  background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "    stop:0 #33c8ba, stop:1 #23a99c);"
            "  color: #08161b; border: none; border-radius: 4px;"
            "  padding: 3px 10px; font-weight: 600; font-size: 12px;"
            "}"
            "QPushButton:hover { background: #31d4c4; }"
            "QPushButton:pressed { background: #1f9288; }"
        )
    return (
        "QPushButton {"
        "  background: transparent;"
        "  color: #29b9ab;"
        "  border: 1px solid rgba(145,182,181,0.34);"
        "  border-radius: 4px;"
        "  padding: 3px 10px; font-size: 12px;"
        "}"
        "QPushButton:hover { background: rgba(41,185,171,0.10); }"
        "QPushButton:pressed { background: rgba(41,185,171,0.18); }"
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
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Tab widget — Helicon is first (the web 配置 page)
        self._tabs = QTabWidget()
        self._tabs.setObjectName("SettingsTabs")
        self._tabs.setStyleSheet(
            "QTabWidget::pane {"
            "  background: #10242a;"
            "  border: 1px solid rgba(145,182,181,0.18);"
            "  border-top: none;"
            "}"
            "QTabBar::tab {"
            "  background: #0c2027;"
            "  color: #87a2a1;"
            "  border: 1px solid rgba(145,182,181,0.14);"
            "  border-bottom: none;"
            "  padding: 6px 16px;"
            "  min-width: 60px;"
            "}"
            "QTabBar::tab:selected {"
            "  background: #10242a;"
            "  color: #eef3ef;"
            "  border-bottom: 2px solid #29b9ab;"
            "}"
            "QTabBar::tab:hover:!selected { background: #0e2b34; color: #cfe0db; }"
        )
        root.addWidget(self._tabs, stretch=1)

        self._build_tab_project()
        self._build_tab_helicon()
        self._build_tab_archive()
        self._build_tab_user()
        self._build_tab_about()

    def on_activate(self) -> None:
        """Reload settings from QSettings each time the user opens this view."""
        self._load_all()

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
            f"  background: #0a1e24;"
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
        self._refresh_btn.clicked.connect(self._detect_helicon)

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

        # Parameter form (method / radius / smoothing / quality)
        preset_form = QFormLayout()
        preset_form.setHorizontalSpacing(16)
        preset_form.setVerticalSpacing(8)

        self._method_combo = QComboBox()
        self._method_combo.addItems(["A — 加权平均 (1)", "B — 景深图 (2)", "C — 金字塔 (3)"])
        self._method_combo.setToolTip("-mp: 参数，A=1 B=2 C=3")
        self._method_combo.currentIndexChanged.connect(self._save_helicon)
        preset_form.addRow("合成方式 (-mp)", self._method_combo)

        self._radius_spin = QSpinBox()
        self._radius_spin.setRange(1, 16)
        self._radius_spin.setValue(4)
        self._radius_spin.setToolTip("-rp: 参数，范围 1–16，推荐 4")
        self._radius_spin.valueChanged.connect(self._save_helicon)
        preset_form.addRow("半径 (-rp)", self._radius_spin)

        self._smoothing_spin = QSpinBox()
        self._smoothing_spin.setRange(0, 8)
        self._smoothing_spin.setValue(4)
        self._smoothing_spin.setToolTip("-sp: 参数，范围 0–8，推荐 4")
        self._smoothing_spin.valueChanged.connect(self._save_helicon)
        preset_form.addRow("平滑度 (-sp)", self._smoothing_spin)

        self._quality_spin = QSpinBox()
        self._quality_spin.setRange(70, 100)
        self._quality_spin.setValue(95)
        self._quality_spin.setToolTip("-j: JPEG 质量，仅当输出格式为 JPEG 时有效")
        self._quality_spin.valueChanged.connect(self._save_helicon)
        preset_form.addRow("JPEG 质量 (-j)", self._quality_spin)

        preset_v.addLayout(preset_form)

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
        tab.body.addStretch()

        # Legacy status label alias (kept for _detect_helicon compat)
        self._helicon_status_label = self._detect_status_badge

        self._tabs.addTab(tab, "Helicon")

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
        self._tabs.addTab(tab, "归档")

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

        self._tabs.addTab(tab, "项目")

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
        self._tabs.addTab(tab, "操作人")

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
        self._tabs.addTab(tab, "关于")

    # ── Load / save helpers ───────────────────────────────────────────────

    def _load_all(self) -> None:
        """Read all settings from QSettings and populate widgets."""
        qs = self.ctx.settings._qs  # direct QSettings access

        # Helicon tab — exe path + preset params
        exe_val = qs.value(_K_HELICON_EXE, "")
        self._helicon_exe_edit.setText(exe_val)
        method_idx = int(qs.value(_K_HELICON_METHOD, 0))
        self._method_combo.setCurrentIndex(
            method_idx if 0 <= method_idx < self._method_combo.count() else 0
        )
        self._radius_spin.setValue(int(qs.value(_K_HELICON_RADIUS, 4)))
        self._smoothing_spin.setValue(int(qs.value(_K_HELICON_SMOOTHING, 4)))
        self._quality_spin.setValue(int(qs.value(_K_HELICON_QUALITY, 95)))
        # Refresh detected / effective display
        self._refresh_helicon_display()

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
        self._load_recent_projects()

        # User tab
        self._current_user_edit.setText(qs.value(_K_CURRENT_USER, ""))

        # Preset list widget
        self._refresh_preset_list_widget()

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
        self.ctx.settings.sync()

    def _save_helicon(self) -> None:
        qs = self.ctx.settings._qs
        qs.setValue(_K_HELICON_EXE, self._helicon_exe_edit.text().strip())
        qs.setValue(_K_HELICON_METHOD, self._method_combo.currentIndex())
        qs.setValue(_K_HELICON_RADIUS, self._radius_spin.value())
        qs.setValue(_K_HELICON_SMOOTHING, self._smoothing_spin.value())
        qs.setValue(_K_HELICON_QUALITY, self._quality_spin.value())
        self.ctx.settings.sync()

    def _save_archive(self) -> None:
        qs = self.ctx.settings._qs
        qs.setValue(_K_JXL_EFFORT, self._jxl_effort_combo.currentIndex())
        # Store as explicit "true"/"false" string for unambiguous retrieval
        qs.setValue(_K_DELETE_JPG, "true" if self._delete_jpg_chk.isChecked() else "false")
        self.ctx.settings.sync()

    def _save_user(self) -> None:
        qs = self.ctx.settings._qs
        name = self._current_user_edit.text().strip()
        qs.setValue(_K_CURRENT_USER, name)
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
            "method": self._method_combo.currentIndex() + 1,
            "radius": self._radius_spin.value(),
            "smoothing": self._smoothing_spin.value(),
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
        method_idx = max(0, min(method_idx, self._method_combo.count() - 1))
        self._method_combo.setCurrentIndex(method_idx)
        self._radius_spin.setValue(int(params.get("radius", 4)))
        self._smoothing_spin.setValue(int(params.get("smoothing", 4)))
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

    def _on_test_click(self) -> None:
        """检测 — save path then auto-detect (mirrors web testBtn handler)."""
        self._save_helicon()
        self._detect_helicon()

    def _on_save_click(self) -> None:
        """保存 — persist custom path."""
        self._save_helicon()
        self._refresh_helicon_display()

    def _on_clear_click(self) -> None:
        """清除自定义 — wipe custom path and re-detect."""
        self._helicon_exe_edit.clear()
        self._save_helicon()
        self._detect_helicon()

    def _detect_helicon(self) -> None:
        """重新探测 / auto-detect — calls helicon_service.detect_helicon()."""
        try:
            from app.services.helicon_service import detect_helicon, reset_helicon_cache
            reset_helicon_cache()
            found = detect_helicon()
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
        else:
            self._detected_path_label.setText("（未检测到）")
            self._effective_path_label.setText("—")
            self._detect_status_badge.setText("未检测到")
            self._detect_status_badge.setStyleSheet(
                f"font-size: 12px; font-weight: 600; color: {_C_WARN};"
                "background: transparent;"
            )

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
