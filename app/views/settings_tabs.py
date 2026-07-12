"""Settings tab construction for the settings view."""
from __future__ import annotations

import os
import platform

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
from app.config.version import APP_VERSION
from app.services.collab_status import build_collab_status
from app.utils import diagnostics
from app.views import settings_view_support as _sv


class SettingsTabsMixin:
    """Settings tab construction for the settings view."""

    def _setup_ui(self) -> None:
        """Build the full widget tree."""
        _sv._refresh_palette()  # bind _C_* to the active theme before building
        existing = self.layout()
        if existing is not None:
            _sv._clear_layout(existing)
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
            f"  background: {_sv._C_BG};"
            f"  border: 1px solid {_sv._C_BORDER};"
            "  border-top: none;"
            "}"
            "QTabBar::tab {"
            f"  background: {_sv._C_PANEL};"
            f"  color: {_sv._C_MUTED};"
            f"  border: 1px solid {_sv._C_BORDER};"
            "  border-bottom: none;"
            "  padding: 6px 16px;"
            "  min-width: 60px;"
            "}"
            "QTabBar::tab:selected {"
            f"  background: {_sv._C_BG};"
            f"  color: {_sv._C_TEXT};"
            f"  border-bottom: 2px solid {_sv._C_ACCENT};"
            "}"
            f"QTabBar::tab:hover:!selected {{ background: {_sv._C_PANEL}; color: {_sv._C_TEXT}; }}"
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
        tab = _sv._ScrollTab()
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
            f"font-size: 20px; font-weight: 700; color: {_sv._C_TEXT}; background: transparent;"
        )
        header_v.addWidget(h2)

        subtitle = QLabel("Helicon Focus CLI 路径与探测设置。")
        subtitle.setStyleSheet(
            f"font-size: 13px; color: {_sv._C_MUTED}; background: transparent;"
        )
        header_v.addWidget(subtitle)
        tab.body.addWidget(header_frame)

        # ── config-section.helicon-config-section ────────────────────────
        section = QFrame()
        section.setObjectName("HeliconConfigSection")
        section.setStyleSheet(
            "QFrame#HeliconConfigSection {"
            f"  background: {_sv._C_PANEL};"
            f"  border: 1px solid {_sv._C_BORDER};"
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
            f"font-size: 15px; font-weight: 600; color: {_sv._C_TEXT};"
            "margin-bottom: 12px; background: transparent;"
        )
        section_v.addWidget(section_title)

        # ── Row 1: 自动探测结果 ────────────────────────────────────────
        detected_row = _sv._ConfigRow()
        detected_row.add_label("自动探测结果")
        self._detected_path_label = QLabel("（未检测到）")
        self._detected_path_label.setObjectName("ConfigPathValue")
        self._detected_path_label.setStyleSheet(
            f"font-family: monospace; font-size: 12px; color: {_sv._C_TEXT};"
            f"background: rgba(0,0,0,0.25); border-radius: 3px;"
            "padding: 2px 6px;"
        )
        detected_row.add_widget(self._detected_path_label, stretch=1)

        # status badge (config-status-ok / config-status-warn)
        self._detect_status_badge = QLabel("未检测到")
        self._detect_status_badge.setObjectName("ConfigStatusWarn")
        self._detect_status_badge.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {_sv._C_WARN};"
            "background: transparent;"
        )
        detected_row.add_widget(self._detect_status_badge)
        section_v.addWidget(detected_row)
        section_v.addSpacing(10)

        # ── Row 2: 当前生效路径 ────────────────────────────────────────
        effective_row = _sv._ConfigRow()
        effective_row.add_label("当前生效路径")
        self._effective_path_label = QLabel("—")
        self._effective_path_label.setObjectName("ConfigPathValue")
        self._effective_path_label.setStyleSheet(
            f"font-family: monospace; font-size: 12px; color: {_sv._C_TEXT};"
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
            f"font-size: 12px; font-weight: 600; color: {_sv._C_MUTED};"
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
            f"  background: {_sv._C_PANEL};"
            f"  color: {_sv._C_TEXT};"
            f"  border: 1px solid {_sv._C_BORDER};"
            "  border-radius: 4px;"
            "  padding: 5px 10px;"
            "  font-family: monospace; font-size: 12px;"
            "}"
            "QLineEdit#ConfigPathInput:focus {"
            f"  border-color: {_sv._C_ACCENT};"
            "}"
        )
        self._helicon_exe_edit.editingFinished.connect(self._save_helicon)
        custom_v.addWidget(self._helicon_exe_edit)

        # config-btn-row: [检测] [保存] [清除自定义] [重新探测]
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(8)

        self._test_btn = QPushButton("检测")
        self._test_btn.setStyleSheet(_sv._btn_style("outline"))
        self._test_btn.clicked.connect(self._on_test_click)

        self._save_btn = QPushButton("保存")
        self._save_btn.setStyleSheet(_sv._btn_style("primary"))
        self._save_btn.clicked.connect(self._on_save_click)

        self._clear_btn = QPushButton("清除自定义")
        self._clear_btn.setStyleSheet(_sv._btn_style("outline"))
        self._clear_btn.clicked.connect(self._on_clear_click)

        self._refresh_btn = QPushButton("重新探测")
        self._refresh_btn.setStyleSheet(_sv._btn_style("outline"))
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
            f"font-weight: 600; color: {_sv._C_TEXT}; font-size: 12px;"
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
                f"font-size: 12px; color: {_sv._C_MUTED}; background: transparent;"
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
        from app.utils.tooltip_policy import suppress_popup_tooltip

        suppress_popup_tooltip(self._preset_list)
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
        preset_name_lbl.setStyleSheet(f"font-size: 12px; color: {_sv._C_MUTED};")
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
        self._save_preset_btn.setStyleSheet(_sv._btn_style("primary"))
        self._save_preset_btn.clicked.connect(self._save_current_as_preset)

        self._apply_preset_btn = QPushButton("应用选中预设")
        self._apply_preset_btn.setStyleSheet(_sv._btn_style("outline"))
        self._apply_preset_btn.clicked.connect(self._apply_selected_preset)

        self._delete_preset_btn = QPushButton("删除选中预设")
        self._delete_preset_btn.setStyleSheet(_sv._btn_style("outline"))
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
        """归档 tab — verified JPG archive + delete-JPG verification."""
        tab = _sv._ScrollTab()
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        format_hint = QLabel(
            "默认使用快速 JPG ZIP：直接打包原始 JPG，删除前仍校验名称、大小和 SHA-256。"
            "需要更小体积时可改用 JPEG XL 高压缩，但速度会明显变慢。"
        )
        format_hint.setObjectName("Muted")
        format_hint.setWordWrap(True)
        form.addRow("归档格式", format_hint)

        self._jxl_effort_combo = QComboBox()
        self._jxl_effort_combo.addItems([
            "快速 JPG ZIP（推荐）",
            "高压缩 JPEG XL（较慢）",
        ])
        self._jxl_effort_combo.setToolTip(
            "日常拍摄整理建议用快速 JPG ZIP；JPEG XL 只适合需要节省空间且能接受等待的场景。"
        )
        self._jxl_effort_combo.currentIndexChanged.connect(self._save_archive)
        form.addRow("归档模式", self._jxl_effort_combo)

        self._jxl_concurrency_spin = QSpinBox()
        self._jxl_concurrency_spin.setRange(1, 8)
        self._jxl_concurrency_spin.setValue(4)
        self._jxl_concurrency_spin.setToolTip(
            "兼容旧设置；快速 JPG ZIP 不使用该参数"
        )
        self._jxl_concurrency_spin.valueChanged.connect(self._save_archive)
        self._jxl_concurrency_spin.hide()

        tab.body.addLayout(form)
        tab.body.addSpacing(16)

        # Delete-JPG section with prerequisite description
        del_box = QGroupBox("整理后的原片处理")
        del_v = QVBoxLayout(del_box)

        prereq_label = QLabel(
            "默认流程：整理后不保留待处理区散落 JPG；JPG 原片已在 ZIP 中，可直接解压使用。\n"
            "只有同时满足以下条件才会删除散落 JPG：\n"
            "  1. ZIP 已生成且大小 > 32 字节\n"
            "  2. ZIP 完整性校验通过\n"
            "  3. ZIP 内每张 JPG 的名称、大小、SHA-256 与原图一致\n"
            "任何一项失败都会保留原 JPG。整理/归档不会自动删除 TIFF；"
            "合成不满意时可手动删除或撤销。"
        )
        prereq_label.setObjectName("Muted")
        prereq_label.setWordWrap(True)
        prereq_label.setStyleSheet("font-size: 12px; line-height: 1.5;")
        del_v.addWidget(prereq_label)

        del_v.addSpacing(12)

        # Product default: the ZIP replaces loose JPGs after exact recovery checks.
        self._delete_jpg_chk = QCheckBox("整理后删除散落 JPG（默认，不保留）")
        self._delete_jpg_chk.setObjectName("DeleteJpgCheckbox")
        self._delete_jpg_chk.setChecked(True)
        self._delete_jpg_chk.setStyleSheet(
            f"QCheckBox {{ color: {_sv._C_SUCCESS}; font-weight: 600; }}"
            f"QCheckBox::indicator:checked {{ background-color: {_sv._C_SUCCESS}; border-color: {_sv._C_SUCCESS}; }}"
        )
        self._delete_jpg_chk.stateChanged.connect(self._save_archive)
        del_v.addWidget(self._delete_jpg_chk)

        tab.body.addWidget(del_box)
        tab.body.addStretch()
        self._tabs.addTab(tab, tr("归档"))

    def _build_tab_project(self) -> None:
        """项目 tab — directory, sub-dirs, recent projects."""
        tab = _sv._ScrollTab()
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
          silentCompose              → workbench/silent_compose
          fileViewMode               → workbench/file_view_mode
        """
        tab = _sv._ScrollTab()

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

        # （已移除「JPG 入库后自动分组处理」死开关：自动归档只在已有激活编号时
        #   自动取该编号未占用 JPG；没有激活编号时不猜。）

        # 自动归档：有激活编号时可不手选 JPG 直接合成；合成成功后自动把源 JPG
        # 打包成 ZIP+命名+移 results。默认关。
        self._auto_organize_chk = QCheckBox("自动归档（激活编号可自动取 JPG）")
        self._auto_organize_chk.setChecked(False)
        self._auto_organize_chk.setToolTip(
            "打开后：有激活编号时，点击合成可自动取该编号未占用 JPG；合成后自动打包 "
            "ZIP 并移入 results；不自动删 TIFF。"
        )
        self._auto_organize_chk.stateChanged.connect(self._save_workbench)
        watch_v.addWidget(self._auto_organize_chk)

        self._silent_compose_chk = QCheckBox("静默合成（跳过 JPG 预览和结果确认）")
        self._silent_compose_chk.setChecked(False)
        self._silent_compose_chk.setToolTip(
            "打开后：选中 JPG 点合成会直接运行 Helicon；合成+整理会继续归档并移 results。"
        )
        self._silent_compose_chk.stateChanged.connect(self._save_workbench)
        watch_v.addWidget(self._silent_compose_chk)

        tab.body.addWidget(watch_box)
        tab.body.addSpacing(12)

        mode_box = QGroupBox("文件视图")
        mode_v = QVBoxLayout(mode_box)
        mode_form = QFormLayout()
        mode_form.setHorizontalSpacing(16)
        mode_form.setVerticalSpacing(8)

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
        tab = _sv._ScrollTab()
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
        tab = _sv._ScrollTab()

        def _section(title: str) -> tuple[QGroupBox, QVBoxLayout]:
            box = QGroupBox(title)
            lay = QVBoxLayout(box)
            lay.setContentsMargins(16, 16, 16, 14)
            lay.setSpacing(10)
            return box, lay

        def _form() -> QFormLayout:
            form = QFormLayout()
            form.setHorizontalSpacing(14)
            form.setVerticalSpacing(10)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            return form

        content = QWidget()
        content.setMinimumWidth(760)
        content.setMaximumWidth(880)
        content.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(14)

        tip = QLabel(
            "日常协作请用主菜单「协作」或工作台「协作面板」。这里可手动填写团队永久码、连接设备和配对。"
        )
        tip.setObjectName("MutedSmall")
        tip.setWordWrap(True)
        content_lay.addWidget(tip)

        status_box, status_lay = _section("协作状态")
        status_head = QHBoxLayout()
        status_head.setContentsMargins(0, 0, 0, 0)
        status_head.setSpacing(10)
        self._collab_enabled_chk = QCheckBox("启用局域网协作（立即生效，与协作中心同步）")
        self._collab_enabled_chk.toggled.connect(self._on_collab_enabled_toggled)
        status_head.addWidget(self._collab_enabled_chk, stretch=1)
        self._collab_diagnose_btn = QPushButton("协作诊断")
        self._collab_diagnose_btn.clicked.connect(self._on_collab_diagnose)
        status_head.addWidget(self._collab_diagnose_btn)
        status_lay.addLayout(status_head)

        status_form = _form()
        self._collab_health_light = QLabel("●")
        self._collab_health_text = QLabel("—")
        health_row = QHBoxLayout()
        health_row.setContentsMargins(0, 0, 0, 0)
        health_row.setSpacing(6)
        health_row.addWidget(self._collab_health_light)
        health_row.addWidget(self._collab_health_text, stretch=1)
        health_wrap = QWidget()
        health_wrap.setLayout(health_row)
        status_form.addRow("状态", health_wrap)

        self._collab_team_code_edit = QLineEdit()
        self._collab_team_code_edit.setPlaceholderText("与队友相同 · 软件重启或更新后自动生效")
        self._collab_team_code_edit.setMaxLength(64)
        self._collab_team_code_edit.setMaximumWidth(360)
        self._collab_team_code_edit.editingFinished.connect(self._save_collab)
        status_form.addRow("团队永久码", self._collab_team_code_edit)

        self._collab_addr_edit = QLineEdit()
        self._collab_addr_edit.setReadOnly(True)
        self._collab_addr_edit.setPlaceholderText("—")
        self._collab_addr_edit.setMinimumWidth(220)
        self._collab_addr_edit.setMaximumWidth(360)
        addr_row = QHBoxLayout()
        addr_row.setContentsMargins(0, 0, 0, 0)
        addr_row.setSpacing(8)
        addr_row.addWidget(self._collab_addr_edit)
        copy_btn = QPushButton("复制")
        copy_btn.clicked.connect(self._copy_collab_addr)
        addr_row.addWidget(copy_btn)
        addr_row.addStretch()
        addr_wrap = QWidget()
        addr_wrap.setLayout(addr_row)
        status_form.addRow("本机地址", addr_wrap)
        status_lay.addLayout(status_form)
        content_lay.addWidget(status_box)

        connect_box, connect_lay = _section("连接设备")
        scan_row = QHBoxLayout()
        scan_row.setContentsMargins(0, 0, 0, 0)
        scan_row.setSpacing(10)
        scan_hint = QLabel("同一局域网内可直接搜索；跨网段或自动发现失败时使用手动连接。")
        scan_hint.setObjectName("MutedSmall")
        scan_hint.setWordWrap(True)
        scan_row.addWidget(scan_hint, stretch=1)
        self._collab_scan_btn = QPushButton("搜索局域网队友")
        self._collab_scan_btn.clicked.connect(self._on_collab_scan)
        scan_row.addWidget(self._collab_scan_btn)
        connect_lay.addLayout(scan_row)

        connect_form = _form()
        self._collab_peer_ip_edit = QLineEdit()
        self._collab_peer_ip_edit.setPlaceholderText("对方 IP")
        self._collab_peer_ip_edit.setMaximumWidth(420)
        self._collab_peer_port_edit = QLineEdit()
        self._collab_peer_port_edit.setPlaceholderText("端口")
        self._collab_peer_port_edit.setFixedWidth(84)
        peer_row = QHBoxLayout()
        peer_row.setContentsMargins(0, 0, 0, 0)
        peer_row.setSpacing(8)
        peer_row.addWidget(self._collab_peer_ip_edit)
        peer_row.addWidget(self._collab_peer_port_edit)
        add_peer_btn = QPushButton("连接")
        add_peer_btn.clicked.connect(self._on_add_manual_peer)
        peer_row.addWidget(add_peer_btn)
        peer_row.addStretch()
        peer_wrap = QWidget()
        peer_wrap.setLayout(peer_row)
        connect_form.addRow("手动连接", peer_wrap)
        connect_lay.addLayout(connect_form)

        self._collab_members_empty = QLabel("暂无在线成员")
        self._collab_members_empty.setObjectName("MutedSmall")
        self._collab_members_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._collab_members_empty.setMinimumHeight(48)
        self._collab_members_empty.setStyleSheet(
            f"border: 1px solid {_sv._C_BORDER}; border-radius: 4px;"
            "background: transparent;"
        )
        self._collab_members_list = QListWidget()
        self._collab_members_list.setMaximumHeight(120)
        connect_form.addRow("在线成员", self._collab_members_empty)
        connect_form.addRow("", self._collab_members_list)
        content_lay.addWidget(connect_box)

        pairing_box, pairing_lay = _section("配对")
        pairing_hint = QLabel("连接码会携带对方地址和团队永久码，适合发给队友快速加入。")
        pairing_hint.setObjectName("MutedSmall")
        pairing_hint.setWordWrap(True)
        pairing_lay.addWidget(pairing_hint)

        pairing_form = _form()
        self._collab_pairing_show_btn = QPushButton("显示我的配对码")
        self._collab_pairing_show_btn.clicked.connect(self._on_collab_show_pairing)
        pairing_form.addRow("我的配对码", self._collab_pairing_show_btn)

        self._collab_pairing_input = QLineEdit()
        self._collab_pairing_input.setPlaceholderText("粘贴队友的配对码")
        pair_join_btn = QPushButton("加入")
        pair_join_btn.clicked.connect(self._on_collab_join_pairing)
        pair_row = QHBoxLayout()
        pair_row.setContentsMargins(0, 0, 0, 0)
        pair_row.setSpacing(8)
        pair_row.addWidget(self._collab_pairing_input, stretch=1)
        pair_row.addWidget(pair_join_btn)
        pair_wrap = QWidget()
        pair_wrap.setLayout(pair_row)
        pairing_form.addRow("输入配对码", pair_wrap)
        pairing_lay.addLayout(pairing_form)
        content_lay.addWidget(pairing_box)

        note = QLabel(
            "同一团队永久码的设备会同步标本编号占用，避免重复编号。\n"
            "撤销占用（在「协作管理」中作废）会释放编号，供任何人重新使用。"
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        content_lay.addWidget(note)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(content)
        row.addStretch()
        tab.body.addLayout(row)
        tab.body.addStretch()
        self._tabs.addTab(tab, tr("协作"))

        # Live updates from the running service, if present.
        svc = getattr(self.ctx, "collab_service", None)
        if svc is not None:
            try:
                svc.peers_changed.connect(self._refresh_collab_members)
                svc.peers_changed.connect(self._refresh_collab_health)
                svc.server_ready.connect(lambda _p: self._refresh_collab_addr())
                svc.server_ready.connect(lambda _p: self._refresh_collab_health())
                svc.diagnostics_changed.connect(self._refresh_collab_health)
            except Exception:  # noqa: BLE001
                pass

    def _build_tab_ui(self) -> None:
        """界面 tab — mirrors renderGlobalSettings():
        fontScale slider, icon emoji fields, useRealCompression debug switch,
        and keyboard shortcut recording (QKeySequenceEdit, Qt-native equivalent
        of web's ensureShortcutsSettings / renderShortcutScope).
        """
        tab = _sv._ScrollTab()

        # ── 界面风格 ──────────────────────────────────────────────────────────
        theme_box = QGroupBox(tr("界面风格"))
        theme_form = QFormLayout(theme_box)
        theme_form.setHorizontalSpacing(16)
        theme_form.setVerticalSpacing(8)

        self._theme_combo = QComboBox()
        from app.config.theme import THEME_NAMES
        for key in _sv._THEME_CHOICES:
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
        # theme_form.addRow(tr("界面语言"), self._lang_combo)  # §7 暂藏 (2026-07-10 用户拍板)
        # en.json 仅覆盖 ~190/约2000 条 UI 字符串, 切 English 是"花脸"界面 ——
        # 补译完成前不展示开关, 不承诺兑现不了的功能。已切到 en 的用户保留
        # 此行作回退出口(否则藏了开关就切不回来)。机制代码全保留。
        from app.config.i18n import current_language
        if current_language() != "zh":
            theme_form.addRow(tr("界面语言"), self._lang_combo)
        else:
            self._lang_combo.hide()

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

        self._project_tree_ux_v2_chk = QCheckBox(tr("项目树精简界面（推荐）"))
        self._project_tree_ux_v2_chk.setToolTip(
            tr("顶栏更干净、筛选不误全选、右栏固定「进入」；取消勾选可立即退回旧版布局。")
        )
        self._project_tree_ux_v2_chk.stateChanged.connect(self._on_project_tree_ux_v2_changed)
        theme_form.addRow(tr("项目树"), self._project_tree_ux_v2_chk)

        tree_ux_note = QLabel(tr("关闭后恢复旧顶栏四枚切换钮与中栏全部控件；立即生效。"))
        tree_ux_note.setObjectName("MutedSmall")
        tree_ux_note.setWordWrap(True)
        theme_form.addRow("", tree_ux_note)

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
        for fam in _sv._installed_font_families():
            self._font_family_combo.addItem(fam, fam)
        self._font_family_combo.setToolTip(tr("选择全局字体，立即生效并在下次启动保持；未安装字体会按系统回退"))
        self._font_family_combo.currentIndexChanged.connect(self._on_font_family_changed)
        font_form.addRow(tr("字体"), self._font_family_combo)

        # 字体大小 — 常用档位 + 可选精细倍率
        self._font_scale_preset_combo = QComboBox()
        self._font_scale_preset_combo.addItem(tr("小（90%）"), 0.90)
        self._font_scale_preset_combo.addItem(tr("标准（100%）"), 1.00)
        self._font_scale_preset_combo.addItem(tr("大（115%）"), 1.15)
        self._font_scale_preset_combo.addItem(tr("特大（130%）"), 1.30)
        self._font_scale_preset_combo.addItem(tr("自定义"), None)
        self._font_scale_preset_combo.setCurrentIndex(1)
        self._font_scale_preset_combo.setFixedWidth(132)
        self._font_scale_preset_combo.setToolTip(
            tr("选择常用界面字号档位；立即预览并自动保存")
        )
        self._font_scale_preset_combo.currentIndexChanged.connect(
            self._on_font_scale_preset_changed
        )
        font_form.addRow(tr("字号档位"), self._font_scale_preset_combo)

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
        font_form.addRow(tr("精细调整"), size_row)

        self._font_scale_spin.valueChanged.connect(self._on_font_scale_changed)

        note = QLabel(
            tr(
                "调整后会立即预览并自动保存，下次启动继续使用；"
                "常用中文字体和 Times 等字体固定列在前面。"
            )
        )
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

        # ── 截图工具 ────────────────────────────────────────────────────────
        screenshot_box = QGroupBox(tr("截图工具"))
        screenshot_form = QFormLayout(screenshot_box)
        screenshot_form.setHorizontalSpacing(16)
        screenshot_form.setVerticalSpacing(8)

        self._screenshot_tool_chk = QCheckBox(tr("启用截图工具"))
        self._screenshot_tool_chk.setToolTip(
            tr("在 工具箱 > 工具 > 截图 显示入口，并启用区域截图快捷键。")
        )
        self._screenshot_tool_chk.stateChanged.connect(
            self._on_screenshot_tool_enabled_changed
        )
        screenshot_form.addRow(tr("截图入口"), self._screenshot_tool_chk)

        screenshot_note = QLabel(
            tr("默认关闭。需要截图时，在这里开启；关闭后菜单入口隐藏，Alt+A 不再触发截图。")
        )
        screenshot_note.setObjectName("MutedSmall")
        screenshot_note.setWordWrap(True)
        screenshot_form.addRow("", screenshot_note)

        tab.body.addWidget(screenshot_box)
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

        # ── 调试：真实归档 ────────────────────────────────────────────────────
        debug_box = QGroupBox(tr("调试选项"))
        debug_v = QVBoxLayout(debug_box)

        self._use_real_compression_chk = QCheckBox(
            tr("使用后端真实归档（文件需实际存在于项目目录）")
        )
        self._use_real_compression_chk.setChecked(False)
        self._use_real_compression_chk.setToolTip(
            tr(
                "对应 web useRealCompression 调试开关。"
                "关闭时系统只模拟归档结果状态，不实际写入 ZIP。"
            )
        )
        self._use_real_compression_chk.stateChanged.connect(self._save_ui)
        debug_v.addWidget(self._use_real_compression_chk)

        tab.body.addWidget(debug_box)
        tab.body.addStretch()

        self._tabs.addTab(tab, tr("界面"))

    def _build_tab_about(self) -> None:
        """关于 tab — version, log dir."""
        tab = _sv._ScrollTab()

        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        ver_label = QLabel(APP_VERSION)
        ver_label.setObjectName("Accent")
        form.addRow("版本", ver_label)

        update_btn = QPushButton("检查更新")
        update_btn.setObjectName("Secondary")
        update_btn.setToolTip("检查 GitHub 最新版本，Windows 打包版可自动更新")
        update_btn.clicked.connect(self._request_update_check)
        form.addRow("软件更新", update_btn)

        self._auto_update_chk = QCheckBox("启动时自动检查更新（发现新版本才提示）")
        self._auto_update_chk.setToolTip(
            "对标 VS Code/Cursor：后台静默检查，可在提示里选择「跳过此版本」。"
        )
        from PyQt6.QtCore import QSettings as _QSettings
        _qs_upd = _QSettings("SpecimenPhotoWorkbench", "标本照片工作台")
        self._auto_update_chk.setChecked(
            str(_qs_upd.value("update/auto_check", "true")).lower() in {"true", "1", "yes"}
        )
        self._auto_update_chk.toggled.connect(self._save_auto_update_pref)
        form.addRow("", self._auto_update_chk)

        platform_label = QLabel(
            f"{platform.system()} {platform.release()} / Python {platform.python_version()}"
        )
        platform_label.setObjectName("Muted")
        form.addRow("运行环境", platform_label)

        # QSettings INI file path.
        from PyQt6.QtCore import QSettings
        qs = QSettings("SpecimenPhotoWorkbench", "标本照片工作台")
        settings_path_label = QLabel(qs.fileName())
        settings_path_label.setObjectName("Mono")
        settings_path_label.setWordWrap(True)
        form.addRow("配置文件路径", settings_path_label)

        log_file = diagnostics.setup_logging()
        log_path_label = QLabel(str(log_file))
        log_path_label.setObjectName("Mono")
        log_path_label.setWordWrap(True)
        copy_log_btn = QPushButton("复制路径")
        copy_log_btn.setObjectName("Secondary")
        copy_log_btn.clicked.connect(
            lambda _checked=False, path=str(log_file): QApplication.clipboard().setText(path)
        )
        log_row = QWidget()
        log_layout = QHBoxLayout(log_row)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(8)
        log_layout.addWidget(log_path_label, 1)
        log_layout.addWidget(copy_log_btn, 0)
        form.addRow("日志文件路径", log_row)

        tab.body.addLayout(form)

        tab.body.addSpacing(16)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("Panel")
        tab.body.addWidget(sep)
        tab.body.addSpacing(8)

        about_text = QLabel(
            "标本照片工作台  |  Specimen Photo Workbench\n"
            "分类学标本拍摄 · 景深叠加 · JPG ZIP 归档 · 标签打印\n"
        )
        about_text.setObjectName("Muted")
        about_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_text.setWordWrap(True)
        tab.body.addWidget(about_text)

        tab.body.addStretch()
        self._tabs.addTab(tab, tr("关于"))

    def _request_update_check(self) -> None:
        handler = getattr(self.window(), "_on_upgrade_requested", None)
        if callable(handler):
            handler()

    def _save_auto_update_pref(self, enabled: bool) -> None:
        from PyQt6.QtCore import QSettings
        qs = QSettings("SpecimenPhotoWorkbench", "标本照片工作台")
        qs.setValue("update/auto_check", "true" if enabled else "false")

    # ── Load / save helpers ───────────────────────────────────────────────
