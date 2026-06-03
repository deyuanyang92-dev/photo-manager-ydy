"""settings_view.py — Global settings panel with five tabs.

Tabs
----
项目      Project directory, incoming-jpg / results sub-dirs, recent projects.
Helicon   Exe path (auto-detect + manual override), synthesis preset params.
归档      JXL effort level, delete-JPG toggle (default OFF — hard rule).
操作人    currentUser name (modifiedBy in specimen records).
关于      App version string, log directory path.

All settings are stored and retrieved via ``AppContext.settings`` (QSettings
wrapper). Key namespace follows the ``section/key`` convention already in use
by AppSettings.

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
    QDoubleSpinBox,
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

_RECENT_MAX = 10


class SettingsView(BaseView):
    """Full-width settings panel with tabbed sections.

    view_id   = "settings"
    nav_title = "全局设置"  (registered in registry.py)
    nav_icon  = "⚙️"
    """

    view_id = "settings"
    nav_title = "全局设置"
    nav_icon = "⚙️"

    def __init__(self, ctx: "AppContext") -> None:  # noqa: D107
        super().__init__(ctx)

    # ── BaseView contract ─────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        """Build the full widget tree."""
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # Title row
        title = QLabel("全局设置")
        title.setObjectName("Title")
        root.addWidget(title)

        # Tab widget
        self._tabs = QTabWidget()
        self._tabs.setObjectName("SettingsTabs")
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
        tab.body.addSpacing(12)

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

    def _build_tab_helicon(self) -> None:
        """Helicon tab — exe path, composite params."""
        tab = _ScrollTab()
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # ── Exe path ────────────────────────────────────────────────
        exe_row = QWidget()
        exe_hl = QHBoxLayout(exe_row)
        exe_hl.setContentsMargins(0, 0, 0, 0)
        exe_hl.setSpacing(8)
        self._helicon_exe_edit = QLineEdit()
        self._helicon_exe_edit.setPlaceholderText("（自动探测）")
        self._helicon_exe_edit.editingFinished.connect(self._save_helicon)
        exe_browse = QPushButton("浏览…")
        exe_browse.setFixedWidth(72)
        exe_browse.clicked.connect(self._browse_helicon_exe)
        exe_hl.addWidget(self._helicon_exe_edit, stretch=1)
        exe_hl.addWidget(exe_browse)

        detect_btn = QPushButton("自动探测")
        detect_btn.clicked.connect(self._detect_helicon)
        exe_hl.addWidget(detect_btn)
        form.addRow("HeliconFocus 路径", exe_row)

        # Detection status label
        self._helicon_status_label = QLabel("未探测")
        self._helicon_status_label.setObjectName("Muted")
        form.addRow("探测状态", self._helicon_status_label)

        tab.body.addLayout(form)
        tab.body.addSpacing(12)

        # ── Synthesis preset group ───────────────────────────────────
        preset_box = QGroupBox("合成参数预设")
        preset_form = QFormLayout(preset_box)
        preset_form.setHorizontalSpacing(16)
        preset_form.setVerticalSpacing(8)

        # Method (A / B / C strings matching Helicon CLI 1/2/3)
        self._method_combo = QComboBox()
        self._method_combo.addItems(["A — 加权平均 (1)", "B — 景深图 (2)", "C — 金字塔 (3)"])
        self._method_combo.setToolTip("-mp: 参数，A=1 B=2 C=3")
        self._method_combo.currentIndexChanged.connect(self._save_helicon)
        preset_form.addRow("合成方式 (-mp)", self._method_combo)

        # Radius
        self._radius_spin = QSpinBox()
        self._radius_spin.setRange(1, 16)
        self._radius_spin.setValue(4)
        self._radius_spin.setToolTip("-rp: 参数，范围 1–16，推荐 4")
        self._radius_spin.valueChanged.connect(self._save_helicon)
        preset_form.addRow("半径 (-rp)", self._radius_spin)

        # Smoothing
        self._smoothing_spin = QSpinBox()
        self._smoothing_spin.setRange(0, 8)
        self._smoothing_spin.setValue(4)
        self._smoothing_spin.setToolTip("-sp: 参数，范围 0–8，推荐 4")
        self._smoothing_spin.valueChanged.connect(self._save_helicon)
        preset_form.addRow("平滑度 (-sp)", self._smoothing_spin)

        # JPEG quality (for -j flag)
        self._quality_spin = QSpinBox()
        self._quality_spin.setRange(70, 100)
        self._quality_spin.setValue(95)
        self._quality_spin.setToolTip("-j: JPEG 质量，仅当输出格式为 JPEG 时有效")
        self._quality_spin.valueChanged.connect(self._save_helicon)
        preset_form.addRow("JPEG 质量 (-j)", self._quality_spin)

        tab.body.addWidget(preset_box)
        tab.body.addStretch()
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

        # Four prerequisite bullets (red line conditions)
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

        del_v.addSpacing(8)

        # The actual checkbox — DEFAULT OFF (hard rule)
        self._delete_jpg_chk = QCheckBox("归档完成后删除原片 JPG（危险操作，默认关闭）")
        self._delete_jpg_chk.setObjectName("DeleteJpgCheckbox")
        self._delete_jpg_chk.setChecked(False)  # default = False — hard rule
        self._delete_jpg_chk.setStyleSheet(
            "QCheckBox { color: #e66e63; font-weight: 600; }"
            "QCheckBox::indicator:checked { background-color: #e66e63; border-color: #e66e63; }"
        )
        self._delete_jpg_chk.stateChanged.connect(self._save_archive)
        del_v.addWidget(self._delete_jpg_chk)

        tab.body.addWidget(del_box)
        tab.body.addStretch()
        self._tabs.addTab(tab, "归档")

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

        # Project tab
        proj_dir = self.ctx.current_project_dir or ""
        self._project_dir_edit.setText(proj_dir)
        self._incoming_edit.setText(qs.value(_K_INCOMING_SUBDIR, "incoming-jpg"))
        self._results_edit.setText(qs.value(_K_RESULTS_SUBDIR, "results"))
        self._load_recent_projects()

        # Helicon tab
        self._helicon_exe_edit.setText(qs.value(_K_HELICON_EXE, ""))
        method_idx = int(qs.value(_K_HELICON_METHOD, 0))
        self._method_combo.setCurrentIndex(
            method_idx if 0 <= method_idx < self._method_combo.count() else 0
        )
        self._radius_spin.setValue(int(qs.value(_K_HELICON_RADIUS, 4)))
        self._smoothing_spin.setValue(int(qs.value(_K_HELICON_SMOOTHING, 4)))
        self._quality_spin.setValue(int(qs.value(_K_HELICON_QUALITY, 95)))

        # Archive tab
        effort_idx = int(qs.value(_K_JXL_EFFORT, 0))
        self._jxl_effort_combo.setCurrentIndex(
            effort_idx if 0 <= effort_idx < self._jxl_effort_combo.count() else 0
        )
        # delete_jpg: stored as string "true"/"false" — default False (hard rule)
        raw_del = qs.value(_K_DELETE_JPG, "false")
        delete_jpg = str(raw_del).lower() == "true"
        self._delete_jpg_chk.setChecked(delete_jpg)

        # User tab
        self._current_user_edit.setText(qs.value(_K_CURRENT_USER, ""))

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

    # ── Helicon detection ─────────────────────────────────────────────────

    def _browse_helicon_exe(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "选择 HeliconFocus.exe",
            self._helicon_exe_edit.text() or "",
            "Executable (*.exe);;All files (*)"
        )
        if chosen:
            self._helicon_exe_edit.setText(chosen)
            self._save_helicon()

    def _detect_helicon(self) -> None:
        from app.services.helicon_service import detect_helicon, reset_helicon_cache
        reset_helicon_cache()
        found = detect_helicon()
        if found:
            self._helicon_exe_edit.setText(found)
            self._helicon_status_label.setText(f"✅ 已找到：{found}")
            self._helicon_status_label.setObjectName("Accent")
            self._save_helicon()
        else:
            self._helicon_status_label.setText("❌ 未检测到 Helicon Focus")
            self._helicon_status_label.setStyleSheet("color: #e66e63;")
        self._helicon_status_label.update()


# ── Private helpers ───────────────────────────────────────────────────────────


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

        inner = QWidget()
        self.body = QVBoxLayout(inner)
        self.body.setContentsMargins(12, 16, 12, 16)
        self.body.setSpacing(8)

        scroll.setWidget(inner)
        outer.addWidget(scroll)
