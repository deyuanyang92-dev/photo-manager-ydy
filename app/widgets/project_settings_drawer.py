"""project_settings_drawer.py — Project settings side drawer.

Mirrors renderProjectSettingsDrawer() (app.js:9418-9931) with tabs:
  概要 / 保存方式 / 人员预设 / 命名规则 / TIFF元数据 / 打印

Public API (unchanged):
  .refresh()           — reload from DB + Helicon detection
  .closed              — signal emitted on close
  .helicon_path_changed — signal emitted with new exe path
"""
from __future__ import annotations

import os
from typing import Any, Callable, Optional, TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from app.app_context import AppContext


# ── inline KV dict editor ──────────────────────────────────────────────────────

class _KVEditor(QWidget):
    """Editable list of key→value pairs (for stations / species in 命名规则)."""

    changed = pyqtSignal()

    def __init__(self, key_placeholder: str = "缩写", val_placeholder: str = "中文说明",
                 force_upper: bool = True, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._key_ph = key_placeholder
        self._val_ph = val_placeholder
        self._force_upper = force_upper
        self._rows: list[tuple[QLineEdit, QLineEdit]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        self._rows_widget = QWidget()
        self._rows_lay = QVBoxLayout(self._rows_widget)
        self._rows_lay.setContentsMargins(0, 0, 0, 0)
        self._rows_lay.setSpacing(4)
        root.addWidget(self._rows_widget)

        add_btn = QPushButton("+ 添加")
        add_btn.setObjectName("Ghost")
        add_btn.setFixedHeight(26)
        add_btn.clicked.connect(self._add_row)
        root.addWidget(add_btn)

    def load(self, data: dict[str, str]) -> None:
        self._clear_rows()
        for k, v in data.items():
            self._add_row(k, v)

    def get_data(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for k_edit, v_edit in self._rows:
            k = k_edit.text().strip()
            if k:
                result[k] = v_edit.text().strip()
        return result

    def _clear_rows(self) -> None:
        while self._rows_lay.count():
            item = self._rows_lay.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self._rows.clear()

    def _add_row(self, key: str = "", val: str = "") -> None:
        row_w = QWidget()
        h = QHBoxLayout(row_w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)

        k_edit = QLineEdit(key)
        k_edit.setPlaceholderText(self._key_ph)
        k_edit.setFixedWidth(72)
        k_edit.setFixedHeight(28)
        if self._force_upper:
            k_edit.textEdited.connect(lambda t, e=k_edit: e.setText(t.upper()))
        k_edit.editingFinished.connect(self.changed.emit)

        arrow = QLabel("→")
        arrow.setObjectName("Muted")
        arrow.setFixedWidth(16)
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)

        v_edit = QLineEdit(val)
        v_edit.setPlaceholderText(self._val_ph)
        v_edit.setFixedHeight(28)
        v_edit.editingFinished.connect(self.changed.emit)

        del_btn = QPushButton("×")
        del_btn.setObjectName("Ghost")
        del_btn.setFixedSize(24, 28)
        del_btn.clicked.connect(lambda: self._remove_row(row_w, k_edit, v_edit))

        h.addWidget(k_edit)
        h.addWidget(arrow)
        h.addWidget(v_edit, 1)
        h.addWidget(del_btn)

        self._rows.append((k_edit, v_edit))
        self._rows_lay.addWidget(row_w)

    def _remove_row(self, row_w: QWidget, k_edit: QLineEdit, v_edit: QLineEdit) -> None:
        if (k_edit, v_edit) in self._rows:
            self._rows.remove((k_edit, v_edit))
        row_w.deleteLater()
        self.changed.emit()


# ── main drawer ───────────────────────────────────────────────────────────────

class ProjectSettingsDrawer(QWidget):
    """Overlay drawer for project-level settings (5 tabs).

    Show by calling .show(); hide with .hide().
    """

    closed = pyqtSignal()
    helicon_path_changed = pyqtSignal(str)
    naming_rules_changed = pyqtSignal()
    personnel_changed = pyqtSignal(dict)
    storages_changed = pyqtSignal()

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("SettingsDrawer")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._setup_ui()
        self.hide()

    # ── build ─────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header row
        head_w = QWidget()
        head_w.setObjectName("DrawerHeader")
        head = QHBoxLayout(head_w)
        head.setContentsMargins(20, 14, 12, 14)
        head.setSpacing(8)
        title = QLabel("当前项目设置")
        title.setObjectName("WorkspaceTitle")
        head.addWidget(title)
        head.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setObjectName("Ghost")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self._on_close)
        head.addWidget(close_btn)
        root.addWidget(head_w)

        sep = QFrame()
        sep.setObjectName("Divider")
        sep.setFixedHeight(1)
        root.addWidget(sep)

        # Tab widget
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        root.addWidget(self._tabs, stretch=1)

        self._tabs.addTab(self._build_tab_overview(),   "概要")
        self._tabs.addTab(self._build_tab_storages(),   "保存")
        self._tabs.addTab(self._build_tab_personnel(),  "人员")
        self._tabs.addTab(self._build_tab_code_labels(),"命名")
        self._tabs.addTab(self._build_tab_tiff_meta(),  "TIFF")
        self._tabs.addTab(self._build_tab_printing(),   "打印")

    # ── Tab 1: 概要 ───────────────────────────────────────────────────────────

    def _build_tab_overview(self) -> QWidget:
        w, lay = _scrollable_tab()
        lay.setSpacing(10)

        # Project meta fields
        meta_fields = [
            ("项目编号", "project_code", ""),
            ("项目名",   "name",         ""),
            ("年份",     "year",         ""),
            ("日期段",   "date_range",   ""),
            ("采集地点", "location",     ""),
            ("拍摄位置", "photo_location", "如：厦门大学海洋生物标本馆"),
        ]
        self._meta_edits: dict[str, QLineEdit] = {}
        for label, key, ph in meta_fields:
            edit = QLineEdit()
            edit.setPlaceholderText(ph)
            edit.setFixedHeight(30)
            edit.editingFinished.connect(self._save_project_meta)
            self._meta_edits[key] = edit
            lay.addWidget(_row(label, edit))

        sep = _divider()
        lay.addWidget(sep)

        # Read-only subdir info
        sub_lbl = QLabel("工作目录子目录")
        sub_lbl.setObjectName("Section")
        lay.addWidget(sub_lbl)
        self._dir_info_lbl = QLabel("（未选择项目）")
        self._dir_info_lbl.setObjectName("MutedSmall")
        self._dir_info_lbl.setWordWrap(True)
        lay.addWidget(self._dir_info_lbl)

        lay.addWidget(_divider())

        # Auto-activate toggle
        self._auto_activate_cb = QCheckBox("新建编号后自动激活")
        self._auto_activate_cb.toggled.connect(self._on_auto_activate_changed)
        lay.addWidget(self._auto_activate_cb)

        self._silent_compose_cb = QCheckBox("静默合成（跳过预览确认）")
        self._silent_compose_cb.setToolTip(
            "打开后：选中 JPG 点合成会直接运行 Helicon，成果先生成在 incoming。"
        )
        self._silent_compose_cb.toggled.connect(self._on_silent_compose_changed)
        lay.addWidget(self._silent_compose_cb)

        lay.addWidget(_divider())

        # Helicon section (Qt-specific, web oracle uses separate modal)
        hel_lbl = QLabel("Helicon Focus 配置")
        hel_lbl.setObjectName("Section")
        lay.addWidget(hel_lbl)
        self._helicon_status_lbl = QLabel("检测中…")
        self._helicon_status_lbl.setObjectName("MutedSmall")
        self._helicon_status_lbl.setWordWrap(True)
        lay.addWidget(self._helicon_status_lbl)
        path_row = QHBoxLayout()
        path_row.setSpacing(6)
        self._helicon_path_edit = QLineEdit()
        self._helicon_path_edit.setPlaceholderText("自定义 Helicon.exe 路径（留空=自动检测）")
        self._helicon_path_edit.setFixedHeight(30)
        path_row.addWidget(self._helicon_path_edit)
        detect_btn = QPushButton("检测")
        detect_btn.setObjectName("Outline")
        detect_btn.setFixedSize(52, 30)
        detect_btn.clicked.connect(self._on_detect_helicon)
        path_row.addWidget(detect_btn)
        lay.addLayout(path_row)

        lay.addStretch()
        return w

    # ── Tab 2: 保存方式 ───────────────────────────────────────────────────────

    def _build_tab_storages(self) -> QWidget:
        from app.services.project_settings_service import BUILTIN_STORAGES
        w, lay = _scrollable_tab()
        lay.setSpacing(10)

        builtin_lbl = QLabel("内置保存方式")
        builtin_lbl.setObjectName("Section")
        lay.addWidget(builtin_lbl)

        tbl = QTableWidget(len(BUILTIN_STORAGES), 2)
        tbl.setHorizontalHeaderLabels(["编码", "详细说明"])
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.verticalHeader().setVisible(False)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        tbl.setFixedHeight(min(len(BUILTIN_STORAGES) * 26 + 30, 240))
        self._builtin_storage_table = tbl
        lay.addWidget(tbl)
        self._refresh_builtin_storage_table(None)

        lay.addWidget(_divider())

        custom_head = QWidget()
        custom_head_lay = QHBoxLayout(custom_head)
        custom_head_lay.setContentsMargins(0, 0, 0, 0)
        custom_lbl = QLabel("新增的保存方式")
        custom_lbl.setObjectName("Section")
        custom_head_lay.addWidget(custom_lbl)
        custom_head_lay.addStretch()
        self._new_storage_btn = QPushButton("＋ 新增保存方式")
        self._new_storage_btn.setObjectName("Outline")
        self._new_storage_btn.setFixedHeight(28)
        self._new_storage_btn.clicked.connect(self._start_new_storage)
        custom_head_lay.addWidget(self._new_storage_btn)
        lay.addWidget(custom_head)

        pick_hint = QLabel(
            "修改已有方式：点击上方表格。新增成功后，新编码会显示在这里。"
        )
        pick_hint.setObjectName("MutedSmall")
        pick_hint.setWordWrap(True)
        lay.addWidget(pick_hint)

        self._custom_list_lay = QVBoxLayout()
        self._custom_list_lay.setContentsMargins(0, 0, 0, 0)
        self._custom_list_lay.setSpacing(4)
        lay.addLayout(self._custom_list_lay)

        lay.addWidget(_divider())

        # Add / edit form
        self._storage_editor_title = QLabel("新增保存方式")
        self._storage_editor_title.setObjectName("Section")
        lay.addWidget(self._storage_editor_title)

        self._new_code_edit = QLineEdit()
        self._new_code_edit.setPlaceholderText("编码（如 T95E、RD79）")
        self._new_code_edit.setFixedHeight(28)
        self._new_code_edit.textEdited.connect(
            lambda t: self._new_code_edit.setText(t.upper())
        )
        lay.addWidget(self._new_code_edit)

        self._new_detail_edit = QTextEdit()
        self._new_detail_edit.setPlaceholderText("详细说明（必填）")
        self._new_detail_edit.setFixedHeight(72)
        lay.addWidget(self._new_detail_edit)

        self._rna_hint_lbl = QLabel("")
        self._rna_hint_lbl.setObjectName("MutedSmall")
        lay.addWidget(self._rna_hint_lbl)
        self._new_code_edit.textChanged.connect(
            lambda t: self._rna_hint_lbl.setText("已取 RNA / RNAlater" if t.startswith("R") else "")
        )

        add_btn_row = QHBoxLayout()
        clear_btn = QPushButton("取消")
        clear_btn.setObjectName("Ghost")
        clear_btn.setFixedHeight(28)
        clear_btn.clicked.connect(self._on_clear_custom_form)
        add_btn_row.addWidget(clear_btn)
        self._storage_save_btn = QPushButton("添加")
        self._storage_save_btn.setObjectName("Primary")
        self._storage_save_btn.setFixedHeight(28)
        self._storage_save_btn.clicked.connect(self._on_save_storage)
        add_btn_row.addWidget(self._storage_save_btn)
        add_btn_row.addStretch()
        lay.addLayout(add_btn_row)

        self._storage_save_status = QLabel("")
        self._storage_save_status.setObjectName("MutedSmall")
        self._storage_save_status.setWordWrap(True)
        lay.addWidget(self._storage_save_status)

        lay.addStretch()
        tbl.itemSelectionChanged.connect(self._on_builtin_storage_selected)
        self._storage_edit_mode = "new"
        return w

    # ── Tab 3: 人员预设 ───────────────────────────────────────────────────────

    def _build_tab_personnel(self) -> QWidget:
        w, lay = _scrollable_tab()
        lay.setSpacing(10)

        hint = QLabel("预设人员信息将在新建标本时预填对应字段。")
        hint.setObjectName("MutedSmall")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        lay.addWidget(_divider())

        person_fields = [
            ("数据核对人员", "verifier",    None),
            ("物资准备人员", "logistics",   None),
            ("采集人员",     "collector",   "→ 新建标本预填「采集人」"),
            ("拍摄人员",     "photographer","→ 新建标本预填「拍摄人」"),
            ("鉴定人员",     "identifier",  "→ 新建标本预填「鉴定人」"),
        ]
        self._person_edits: dict[str, QLineEdit] = {}
        for label, key, hint_text in person_fields:
            edit = QLineEdit()
            edit.setFixedHeight(30)
            if hint_text:
                edit.setToolTip(hint_text)
            edit.editingFinished.connect(self._save_personnel)
            self._person_edits[key] = edit
            lbl_widget = _row(label, edit, width=100)
            lay.addWidget(lbl_widget)

        lay.addStretch()
        return w

    # ── Tab 4: 命名规则 ───────────────────────────────────────────────────────

    def _build_tab_code_labels(self) -> QWidget:
        w, lay = _scrollable_tab()
        lay.setSpacing(10)

        # Basic code inputs
        self._province_edit = QLineEdit()
        self._province_edit.setFixedHeight(30)
        self._province_edit.setPlaceholderText("如 ZJ")
        self._province_edit.editingFinished.connect(self._save_code_labels)
        lay.addWidget(_row("地区代码", self._province_edit, width=80))

        self._site_edit = QLineEdit()
        self._site_edit.setFixedHeight(30)
        self._site_edit.setPlaceholderText("如 SMW")
        self._site_edit.editingFinished.connect(self._save_code_labels)
        lay.addWidget(_row("样地代码", self._site_edit, width=80))

        lay.addWidget(_divider())

        req_lbl = QLabel("必填字段（右侧编号卡按此显示 * 并提示缺项）")
        req_lbl.setObjectName("Section")
        req_lbl.setWordWrap(True)
        lay.addWidget(req_lbl)
        self._naming_required_checks: dict[str, QCheckBox] = {}
        required_fields = [
            ("地区", "province"),
            ("样地", "site"),
            ("站位", "station"),
            ("物种缩写", "species_id"),
            ("保存方式", "storage"),
            ("采集日期", "collection_date"),
            ("拍摄日期", "photo_date"),
        ]
        for label, key in required_fields:
            cb = QCheckBox(label)
            cb.stateChanged.connect(self._save_naming_rules)
            self._naming_required_checks[key] = cb
            lay.addWidget(cb)

        lay.addWidget(_divider())

        comp_lbl = QLabel("编号组成（按固定顺序拼接，分类/备注字段默认不参与）")
        comp_lbl.setObjectName("Section")
        comp_lbl.setWordWrap(True)
        lay.addWidget(comp_lbl)
        self._naming_component_checks: dict[str, QCheckBox] = {}
        component_fields = [
            ("地区", "province"),
            ("样地", "site"),
            ("站位", "station"),
            ("物种缩写", "species_id"),
            ("保存方式", "storage"),
            ("日期段", "date_seg"),
            ("类群", "taxon_group"),
            ("目", "order_name"),
            ("科", "family"),
            ("属", "genus"),
            ("物种学名", "scientific_name"),
            ("物种中文名", "scientific_name_cn"),
            ("备注标签", "notes"),
            ("拍照备注", "photo_notes"),
            ("采集人", "collector"),
            ("拍摄人", "photographer"),
        ]
        for label, key in component_fields:
            cb = QCheckBox(label)
            cb.stateChanged.connect(self._save_naming_rules)
            self._naming_component_checks[key] = cb
            lay.addWidget(cb)

        lay.addWidget(_divider())

        # 默认采集坐标 / 地理区（项目级兜底）。新建标本自动带；选定具体站位后，
        # 该站采集记录会以更高优先级覆盖（见 metadata_panel.apply_autofill）。
        cap_lbl = QLabel("默认采集坐标 / 地理区（新标本兜底，选站位后由采集记录覆盖）")
        cap_lbl.setObjectName("Section")
        cap_lbl.setWordWrap(True)
        lay.addWidget(cap_lbl)
        self._cap_lon_edit = QLineEdit()
        self._cap_lon_edit.setFixedHeight(30)
        self._cap_lon_edit.setPlaceholderText("默认经度，如 121.5")
        self._cap_lon_edit.editingFinished.connect(self._save_capture_defaults)
        lay.addWidget(_row("默认经度", self._cap_lon_edit, width=80))
        self._cap_lat_edit = QLineEdit()
        self._cap_lat_edit.setFixedHeight(30)
        self._cap_lat_edit.setPlaceholderText("默认纬度，如 29.1")
        self._cap_lat_edit.editingFinished.connect(self._save_capture_defaults)
        lay.addWidget(_row("默认纬度", self._cap_lat_edit, width=80))
        self._cap_geo_edit = QLineEdit()
        self._cap_geo_edit.setFixedHeight(30)
        self._cap_geo_edit.setPlaceholderText("默认采集地理区，如 三门湾")
        self._cap_geo_edit.editingFinished.connect(self._save_capture_defaults)
        lay.addWidget(_row("默认地理区", self._cap_geo_edit, width=80))

        lay.addWidget(_divider())

        # Stations dict
        sta_lbl = QLabel("站位说明（缩写 → 中文）")
        sta_lbl.setObjectName("Section")
        lay.addWidget(sta_lbl)
        self._stations_kv = _KVEditor(key_placeholder="缩写", val_placeholder="中文说明")
        self._stations_kv.changed.connect(self._save_code_labels)
        lay.addWidget(self._stations_kv)

        lay.addWidget(_divider())

        # Species dict
        sp_lbl = QLabel("物种缩写说明（如 DLC001 的前缀 → 中文）")
        sp_lbl.setObjectName("Section")
        lay.addWidget(sp_lbl)
        self._species_kv = _KVEditor(key_placeholder="缩写", val_placeholder="中文说明")
        self._species_kv.changed.connect(self._save_code_labels)
        lay.addWidget(self._species_kv)

        lay.addWidget(_divider())

        # Preview
        preview_lbl = QLabel("解析预览（第一个标本）")
        preview_lbl.setObjectName("Section")
        lay.addWidget(preview_lbl)
        self._code_preview_lbl = QLabel("（无标本）")
        self._code_preview_lbl.setObjectName("Mono")
        self._code_preview_lbl.setWordWrap(True)
        lay.addWidget(self._code_preview_lbl)

        lay.addStretch()
        return w

    # ── Tab 5: TIFF 元数据 ────────────────────────────────────────────────────

    def _build_tab_tiff_meta(self) -> QWidget:
        w, lay = _scrollable_tab()
        lay.setSpacing(8)

        hint = QLabel("选择嵌入 TIFF 文件的元数据字段。")
        hint.setObjectName("MutedSmall")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        groups: list[tuple[str, list[tuple[str, str, bool]]]] = [
            ("标识", [
                ("照片编号",   "uniqueId",        True),
                ("项目名",     "projectName",     True),
            ]),
            ("分类（拉丁名）", [
                ("物种学名",   "scientificName",  True),
                ("物种中文名", "scientificNameCn",True),
                ("类群",       "taxonGroup",      False),
                ("目",         "order",           False),
                ("科",         "family",          False),
            ]),
            ("时间", [
                ("采集日期",   "collectionDate",  True),
                ("拍照日期",   "photoDate",       True),
            ]),
            ("人员", [
                ("采集人",     "collector",       True),
                ("拍摄人",     "photographer",    True),
                ("鉴定人",     "identifier",      True),
            ]),
            ("地理", [
                ("站位经度",   "lon",             True),
                ("站位纬度",   "lat",             True),
                ("采集地理区", "geoArea",         False),
            ]),
            ("备注", [
                ("备注",       "notes",           False),
                ("拍照备注",   "photoNotes",      True),
            ]),
        ]

        self._tiff_checks: dict[str, QCheckBox] = {}
        for group_name, fields in groups:
            gb = QGroupBox(group_name)
            gb_lay = QVBoxLayout(gb)
            gb_lay.setSpacing(4)
            gb_lay.setContentsMargins(8, 4, 8, 8)
            for label, key, default in fields:
                cb = QCheckBox(label)
                cb.setChecked(default)
                cb.stateChanged.connect(self._save_tiff_fields)
                self._tiff_checks[key] = cb
                gb_lay.addWidget(cb)
            lay.addWidget(gb)

        lay.addStretch()
        return w

    # ── Tab 6: 打印 ──────────────────────────────────────────────────────────

    def _build_tab_printing(self) -> QWidget:
        w, lay = _scrollable_tab()
        lay.setSpacing(12)

        hint = QLabel("工作台编号旁的打印按钮使用此设置；模板留空则沿用「标签打印」页最近选择。")
        hint.setObjectName("MutedSmall")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        # ── 单张打印 ──────────────────────────────────────────────────────────
        self._quick_print_mode = QComboBox()
        self._quick_print_mode.setFixedHeight(30)
        self._quick_print_mode.addItem("打开 Windows 打印窗口（推荐）", "dialog")
        self._quick_print_mode.addItem("直接发送到指定打印机", "direct")
        self._quick_print_mode.currentIndexChanged.connect(self._save_print_settings)

        self._print_tissue_cb = QCheckBox("RNA 编号同时打印 RNAlater 组织管标签")
        self._print_tissue_cb.setToolTip("关闭后，工作台编号旁打印按钮只输出一张样品标签。")
        self._print_tissue_cb.stateChanged.connect(self._save_print_settings)

        lay.addWidget(_settings_group("单张打印", [
            _row("点击打印", self._quick_print_mode, width=72),
            self._print_tissue_cb,
        ]))

        # ── 样品瓶 / 酒精 ─────────────────────────────────────────────────────
        self._sample_printer_combo = QComboBox()
        self._sample_printer_combo.setFixedHeight(30)
        self._sample_printer_combo.currentIndexChanged.connect(self._save_print_settings)

        self._sample_template_combo = QComboBox()
        self._sample_template_combo.setFixedHeight(30)
        self._sample_template_combo.currentIndexChanged.connect(self._save_print_settings)
        self._sample_template_btn = QPushButton("管理 / 刷新…")
        self._sample_template_btn.setObjectName("Outline")
        self._sample_template_btn.setFixedHeight(28)
        self._sample_template_btn.clicked.connect(lambda: self._open_template_manager("sample"))

        self._sample_paper_combo = QComboBox()
        self._sample_paper_combo.setFixedHeight(30)
        self._populate_quick_paper_combo(self._sample_paper_combo)
        self._sample_paper_combo.currentIndexChanged.connect(self._on_sample_paper_changed)

        self._sample_imposition_btn = QPushButton("酒精标签排版设计…")
        self._sample_imposition_btn.setObjectName("Outline")
        self._sample_imposition_btn.setFixedHeight(28)
        self._sample_imposition_btn.clicked.connect(
            lambda: self._open_imposition_designer("sample")
        )

        sample_grp = _settings_group("样品瓶 / 酒精", [
            _row("打印机", self._sample_printer_combo, width=64),
            _row("模板",   self._sample_template_combo, width=64),
            _row("",       self._sample_template_btn, width=64),
            _row("纸张",   self._sample_paper_combo, width=64),
            _row("排版",   self._sample_imposition_btn, width=64),
        ])
        sample_grp.setToolTip("需要设计或修改模板时，进入「标签打印」页编辑。")
        lay.addWidget(sample_grp)

        # ── RNAlater 组织管 ───────────────────────────────────────────────────
        self._tissue_printer_combo = QComboBox()
        self._tissue_printer_combo.setFixedHeight(30)
        self._tissue_printer_combo.currentIndexChanged.connect(self._save_print_settings)

        self._tissue_template_combo = QComboBox()
        self._tissue_template_combo.setFixedHeight(30)
        self._tissue_template_combo.currentIndexChanged.connect(self._save_print_settings)
        self._tissue_template_btn = QPushButton("管理 / 刷新…")
        self._tissue_template_btn.setObjectName("Outline")
        self._tissue_template_btn.setFixedHeight(28)
        self._tissue_template_btn.clicked.connect(lambda: self._open_template_manager("tissue"))

        self._tissue_paper_combo = QComboBox()
        self._tissue_paper_combo.setFixedHeight(30)
        self._populate_quick_paper_combo(self._tissue_paper_combo)
        self._tissue_paper_combo.currentIndexChanged.connect(self._on_tissue_paper_changed)

        self._tissue_imposition_btn = QPushButton("RNA 标签排版设计…")
        self._tissue_imposition_btn.setObjectName("Outline")
        self._tissue_imposition_btn.setFixedHeight(28)
        self._tissue_imposition_btn.clicked.connect(
            lambda: self._open_imposition_designer("tissue")
        )

        self._tissue_strategy_combo = QComboBox()
        self._tissue_strategy_combo.setFixedHeight(30)
        self._tissue_strategy_combo.addItem("自动选择（推荐）", "auto")
        self._tissue_strategy_combo.addItem("直接打印", "direct")
        self._tissue_strategy_combo.addItem("加入合版队列", "queue")
        self._tissue_strategy_combo.currentIndexChanged.connect(self._save_print_settings)

        tissue_grp = _settings_group("RNAlater 组织管", [
            _row("打印机", self._tissue_printer_combo, width=64),
            _row("模板",   self._tissue_template_combo, width=64),
            _row("",       self._tissue_template_btn, width=64),
            _row("纸张",   self._tissue_paper_combo, width=64),
            _row("排版",   self._tissue_imposition_btn, width=64),
            _row("策略",   self._tissue_strategy_combo, width=64),
        ])
        tissue_grp.setToolTip(
            "样品瓶与 RNAlater 可绑同一台或不同打印机；同台且 RNA 用 A4/A5 合版纸时，"
            "自动策略会把 RNAlater 标签加入合版队列。模板编辑请进「标签打印」页。"
        )
        lay.addWidget(tissue_grp)

        default_row = QHBoxLayout()
        default_row.setSpacing(6)
        self._save_print_default_btn = QPushButton("设为全局默认")
        self._save_print_default_btn.setObjectName("Outline")
        self._save_print_default_btn.setFixedHeight(28)
        self._save_print_default_btn.clicked.connect(self._save_print_defaults)
        default_row.addWidget(self._save_print_default_btn)
        default_row.addStretch()
        lay.addLayout(default_row)

        lay.addStretch()
        return w

    @staticmethod
    def _populate_quick_paper_combo(combo: QComboBox) -> None:
        combo.addItem("标签纸（每张标签单独打印）", "label")
        combo.addItem("A4 纸（多张标签合版）", "a4")
        combo.addItem("A5 纸（多张标签合版）", "a5")

    def _refresh_printer_combo(self, combo: QComboBox, selected: str) -> None:
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("系统默认打印机", "")
        try:
            from app.utils import windows_print
            if windows_print.is_available():
                names = windows_print.windows_printer_names()
            else:
                from PyQt6.QtPrintSupport import QPrinterInfo
                names = [p.printerName() for p in QPrinterInfo.availablePrinters()]
        except Exception:
            names = []
        for name in sorted({n for n in names if n}):
            combo.addItem(name, name)
        if selected and combo.findData(selected) < 0:
            combo.addItem(f"{selected}（未检测到）", selected)
        idx = combo.findData(selected or "")
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _refresh_template_combo(self, combo: QComboBox, bucket: str, selected: str) -> None:
        combo.blockSignals(True)
        combo.clear()
        try:
            from app.services import label_service
            lib = label_service.LabelTemplateLibrary(bucket)
            selected = (
                selected
                or lib.selected_key()
                or label_service.DEFAULT_TEMPLATE_KEY[bucket]
            )
            for key, tmpl in label_service.BUILTIN_TEMPLATES.items():
                if bucket == "tissue" and tmpl.get("flavor") != "tissue":
                    continue
                if bucket == "sample" and tmpl.get("flavor") == "tissue":
                    continue
                size = tmpl.get("minSize") or {}
                size_text = f" · {size.get('w')}×{size.get('h')} mm" if size else ""
                combo.addItem(
                    f"内置：{tmpl.get('code', '?')} · {tmpl.get('name') or key}{size_text}", key
                )
            for rec in lib.records():
                tid = rec.get("id")
                if tid:
                    size = (rec.get("template") or {}).get("minSize") or {}
                    size_text = f" · {size.get('w')}×{size.get('h')} mm" if size else ""
                    combo.addItem(
                        f"自定义：{rec.get('name') or tid}{size_text}",
                        label_service.key_from_id(tid),
                    )
        except Exception:
            pass
        if selected and combo.findData(selected) < 0:
            combo.addItem(f"{selected}（未找到）", selected)
        idx = combo.findData(selected or "")
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _open_template_manager(self, bucket: str) -> None:
        """Edit/create templates, then bind the chosen template to this project."""
        from app.services import label_service
        from app.widgets.label_step2_templates import LabelStep2Templates

        libs = {
            "sample": label_service.LabelTemplateLibrary("sample"),
            "tissue": label_service.LabelTemplateLibrary("tissue"),
        }
        dlg = QDialog(self)
        dlg.setWindowTitle("管理标签模板")
        dlg.resize(900, 700)
        layout = QVBoxLayout(dlg)
        manager = LabelStep2Templates(libs, dlg)
        try:
            specimens = label_service.load_specimen_dicts(self.ctx.get_db())
        except Exception:
            specimens = []
        # The settings dialog must always allow configuring both routes, even
        # before the current project contains its first RNA specimen.
        if not any(str(item.get("storage") or "").upper().startswith("R") for item in specimens):
            specimens.append(self._demo_specimen_for_bucket("tissue"))
        manager.set_data(specimens, [])
        layout.addWidget(manager)
        close_btn = QPushButton("完成")
        close_btn.setObjectName("Primary")
        close_btn.clicked.connect(dlg.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(close_btn)
        layout.addLayout(row)
        dlg.exec()

        combo = self._sample_template_combo if bucket == "sample" else self._tissue_template_combo
        chosen = libs[bucket].selected_key() or label_service.DEFAULT_TEMPLATE_KEY[bucket]
        self._refresh_template_combo(combo, bucket, chosen)
        self._save_print_settings()

    def _paper_type_for_imposition(self, bucket: str) -> str:
        return self._effective_sheet_paper_for_bucket(bucket)

    def _effective_sheet_paper_for_bucket(self, bucket: str) -> str:
        combo = self._tissue_paper_combo if bucket == "tissue" else self._sample_paper_combo
        paper_type = str(combo.currentData() or "")
        if not paper_type:
            try:
                from app.services import label_service
                paper_type = label_service.persisted_paper_type(bucket)
            except Exception:
                paper_type = ""
        if paper_type not in {"a4", "a5"}:
            return ""
        return paper_type

    def _sync_imposition_buttons(self) -> None:
        for bucket, btn in (
            ("sample", self._sample_imposition_btn),
            ("tissue", self._tissue_imposition_btn),
        ):
            combo = self._tissue_paper_combo if bucket == "tissue" else self._sample_paper_combo
            paper_type = self._effective_sheet_paper_for_bucket(bucket)
            enabled = bool(paper_type) and combo.isEnabled()
            btn.setEnabled(enabled)
            label = "酒精标签" if bucket == "sample" else "RNA 标签"
            if paper_type:
                btn.setText(f"设置 {paper_type.upper()} 多标签排版…")
                btn.setToolTip(f"编辑 {paper_type.upper()} 合版的边距、间距、行列、方向和起始格。")
            else:
                btn.setText("无需排版（每张标签单独打印）")
                btn.setToolTip("标签卷纸/单张标签是一张一张直接打印，不使用整页合版排版。")

    def _template_key_for_bucket(self, bucket: str) -> str:
        combo = self._tissue_template_combo if bucket == "tissue" else self._sample_template_combo
        return str(combo.currentData() or "")

    def _demo_specimen_for_bucket(self, bucket: str) -> dict:
        storage = "RD95E" if bucket == "tissue" else "D95E"
        return {
            "uid": f"FJ-XM-B2-DLC001-{storage}-20260602",
            "id": "DLC001",
            "province": "FJ",
            "site": "XM",
            "station": "B2",
            "storage": storage,
            "collectionDate": "20260602",
            "photoDate": "20260602",
            "collector": "采集人",
            "species": "样品标签",
            "latin": "Marphysa sp.",
            "family": "Eunicidae",
        }

    def _imposition_job(self, bucket: str) -> dict:
        from app.services import label_service
        lib = label_service.LabelTemplateLibrary(bucket)
        tmpl = label_service.resolve_template_key(lib, self._template_key_for_bucket(bucket))
        size = tmpl.get("minSize") or {}
        dims = (
            {"w": float(size["w"]), "h": float(size["h"])}
            if size.get("w") and size.get("h")
            else label_service.resolve_dims(lib, lib.selected_custom_dims())
        )
        paper_type = self._paper_type_for_imposition(bucket)
        return label_service.LabelService.build_print_job(
            [self._demo_specimen_for_bucket(bucket)],
            tmpl,
            bucket,
            selected_indices=[0],
            dims=dims,
            copies=12,
            paper_type=paper_type,
            paper=label_service.PAPER_SIZES.get(paper_type),
        )

    def _open_imposition_designer(self, bucket: str) -> None:
        from app.services import label_service
        from app.widgets.label_imposition_dialog import LabelImpositionDialog

        if not self._paper_type_for_imposition(bucket):
            QMessageBox.information(
                self,
                "排版设计",
                "当前纸张是标签卷纸/单张标签，不需要 A4/A5 合版排版。请先把纸张切换为 A4 合版或 A5 合版。",
            )
            return
        job = self._imposition_job(bucket)
        snapshot = label_service.persisted_imposition(bucket)
        dlg = LabelImpositionDialog(
            job,
            snapshot,
            self,
            demo_data=self._demo_specimen_for_bucket(bucket),
        )
        dlg.setWindowTitle(
            "酒精标签排版设计" if bucket == "sample" else "RNAlater 标签排版设计"
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            label_service.persist_imposition(bucket, dlg.imposition())

    # ── Public API ────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Reload from DB + Helicon detection. Call after project open/change."""
        # Helicon
        try:
            from app.services.helicon_service import detect_helicon
            exe = detect_helicon()
            if exe:
                self._helicon_status_lbl.setText(f"✅ 已检测到：{exe}")
            else:
                self._helicon_status_lbl.setText(
                    "⚠️ 未检测到 Helicon Focus。请安装后重新检测，"
                    "或在下方填写自定义路径。"
                )
        except Exception as e:
            self._helicon_status_lbl.setText(f"检测失败：{e}")

        # Subdir info
        project_dir = getattr(self.ctx, "current_project_dir", None)
        if project_dir:
            from app.services.project_service import INCOMING_JPG_DIR, RESULTS_DIR
            self._dir_info_lbl.setText(
                f"相机 JPG：{INCOMING_JPG_DIR}/\n成果 TIFF/ZIP：{RESULTS_DIR}/"
            )
        else:
            self._dir_info_lbl.setText("（未选择项目）")

        # Auto-activate
        try:
            val = bool(getattr(self.ctx.settings, "auto_activate_on_new_specimen", False))
            self._auto_activate_cb.setChecked(val)
        except Exception:
            pass
        try:
            val = bool(getattr(self.ctx.settings, "silent_compose", False))
            self._silent_compose_cb.setChecked(val)
        except Exception:
            pass

        db = self.ctx.get_db()
        if db is None:
            self._set_fields_enabled(False)
            return
        self._set_fields_enabled(True)
        self._load_from_db(db)

    # ── Private: load/save ────────────────────────────────────────────────────

    def _load_from_db(self, db) -> None:
        from app.services.project_settings_service import (
            load_setting,
            DEFAULT_PROJECT_META,
            DEFAULT_PERSONNEL,
            DEFAULT_CODE_LABELS,
            DEFAULT_NAMING_RULES,
            DEFAULT_CAPTURE_DEFAULTS,
            DEFAULT_TIFF_FIELDS,
            DEFAULT_PRINT_SETTINGS,
            effective_print_settings,
            load_setting_if_present,
            load_global_print_defaults,
            merge_print_settings,
        )

        # 概要
        meta = load_setting(db, "project_meta", DEFAULT_PROJECT_META)
        for key, edit in self._meta_edits.items():
            edit.setText(meta.get(key, ""))

        # 人员预设
        pers = load_setting(db, "personnel", DEFAULT_PERSONNEL)
        for key, edit in self._person_edits.items():
            edit.setText(pers.get(key, ""))

        # 命名规则
        cl = load_setting(db, "code_labels", DEFAULT_CODE_LABELS)
        self._province_edit.setText(cl.get("province", ""))
        self._site_edit.setText(cl.get("site", ""))
        self._stations_kv.load(cl.get("stations", {}))
        self._species_kv.load(cl.get("species", {}))
        rules = load_setting(db, "naming_rules", DEFAULT_NAMING_RULES)
        required = rules.get("required", DEFAULT_NAMING_RULES["required"])
        for key, cb in self._naming_required_checks.items():
            cb.blockSignals(True)
            cb.setChecked(bool(required.get(key, DEFAULT_NAMING_RULES["required"].get(key, False))))
            cb.blockSignals(False)
        components = rules.get("components", DEFAULT_NAMING_RULES["components"])
        if not isinstance(components, list):
            components = DEFAULT_NAMING_RULES["components"]
        for key, cb in self._naming_component_checks.items():
            cb.blockSignals(True)
            cb.setChecked(key in components)
            cb.blockSignals(False)
        # 默认采集坐标 / 地理区
        cap = load_setting(db, "capture_defaults", DEFAULT_CAPTURE_DEFAULTS)
        self._cap_lon_edit.setText(str(cap.get("lon", "") or ""))
        self._cap_lat_edit.setText(str(cap.get("lat", "") or ""))
        self._cap_geo_edit.setText(cap.get("geoArea", "") or "")
        self._update_code_preview(db)

        # TIFF 字段
        tf = load_setting(db, "tiff_fields", DEFAULT_TIFF_FIELDS)
        for key, cb in self._tiff_checks.items():
            cb.blockSignals(True)
            cb.setChecked(tf.get(key, DEFAULT_TIFF_FIELDS.get(key, False)))
            cb.blockSignals(False)

        # 自定义保存方式
        from app.services.project_settings_service import load_custom_storages

        custom = load_custom_storages(db)
        self._refresh_builtin_storage_table(db)
        self._rebuild_custom_list(custom, db)

        # 工作台单张打印
        project_dir = getattr(self.ctx, "current_project_dir", None)
        project_root = getattr(self.ctx, "current_project_root", None)
        if not isinstance(project_root, str):
            project_root = None
        if project_dir:
            pr = effective_print_settings(
                project_dir,
                root=project_root,
            )
        else:
            pr = load_setting(db, "print_settings", load_global_print_defaults())
        local_print_settings = load_setting_if_present(db, "print_settings")
        if local_print_settings is not None:
            pr = merge_print_settings(pr, local_print_settings)
            if "quick_print_mode" not in local_print_settings and "quick_print" in local_print_settings:
                pr["quick_print_mode"] = (
                    "direct" if bool(local_print_settings["quick_print"]) else "studio"
                )
        # backward compat: new quick_print_mode string wins;
        # old quick_print bool maps True→"direct", False→"studio".
        # DEFAULT_PRINT_SETTINGS now carries quick_print_mode="direct", so
        # default must be remapped when legacy quick_print=False is present.
        quick_mode = str(pr.get("quick_print_mode") or "")
        if not quick_mode:
            quick_mode = "direct" if bool(pr.get("quick_print", True)) else "studio"
        elif quick_mode == "direct" and not bool(pr.get("quick_print", True)):
            quick_mode = "studio"
        # Older versions exposed "open label studio" as a print action.  A
        # print button must print; template design now has its own explicit
        # management button.
        migrated_studio_mode = quick_mode == "studio"
        if migrated_studio_mode:
            quick_mode = "dialog"
        idx = self._quick_print_mode.findData(quick_mode)
        if idx < 0:
            idx = 0
        self._quick_print_mode.blockSignals(True)
        self._quick_print_mode.setCurrentIndex(idx)
        self._quick_print_mode.blockSignals(False)
        self._print_tissue_cb.blockSignals(True)
        self._print_tissue_cb.setChecked(bool(pr.get(
            "include_tissue", DEFAULT_PRINT_SETTINGS["include_tissue"]
        )))
        self._print_tissue_cb.blockSignals(False)
        self._refresh_printer_combo(
            self._sample_printer_combo,
            str(pr.get("sample_printer", DEFAULT_PRINT_SETTINGS["sample_printer"]) or ""),
        )
        self._refresh_printer_combo(
            self._tissue_printer_combo,
            str(pr.get("tissue_printer", DEFAULT_PRINT_SETTINGS["tissue_printer"]) or ""),
        )
        self._refresh_template_combo(
            self._sample_template_combo,
            "sample",
            str(pr.get("sample_template_key", DEFAULT_PRINT_SETTINGS["sample_template_key"]) or ""),
        )
        self._refresh_template_combo(
            self._tissue_template_combo,
            "tissue",
            str(pr.get("tissue_template_key", DEFAULT_PRINT_SETTINGS["tissue_template_key"]) or ""),
        )
        for combo, key in (
            (self._sample_paper_combo, "sample_paper_type"),
            (self._tissue_paper_combo, "tissue_paper_type"),
        ):
            value = str(pr.get(key, DEFAULT_PRINT_SETTINGS[key]) or "label")
            idx = combo.findData(value)
            combo.blockSignals(True)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)
        strategy = str(pr.get("tissue_strategy", DEFAULT_PRINT_SETTINGS["tissue_strategy"]) or "auto")
        idx = self._tissue_strategy_combo.findData(strategy)
        self._tissue_strategy_combo.blockSignals(True)
        self._tissue_strategy_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._tissue_strategy_combo.blockSignals(False)
        self._sync_imposition_buttons()
        if migrated_studio_mode:
            self._save_print_settings()

    def _save_project_meta(self) -> None:
        db = self.ctx.get_db()
        if db is None:
            return
        from app.services.project_settings_service import load_setting, save_setting, DEFAULT_PROJECT_META
        data = load_setting(db, "project_meta", DEFAULT_PROJECT_META)
        for key, edit in self._meta_edits.items():
            data[key] = edit.text().strip()
        save_setting(db, "project_meta", data)

    def _save_personnel(self) -> None:
        db = self.ctx.get_db()
        if db is None:
            return
        from app.services.project_settings_service import save_setting
        data = {key: edit.text().strip() for key, edit in self._person_edits.items()}
        save_setting(db, "personnel", data)
        self.personnel_changed.emit(dict(data))

    def _save_code_labels(self) -> None:
        db = self.ctx.get_db()
        if db is None:
            return
        from app.services.project_settings_service import save_setting
        data = {
            "province": self._province_edit.text().strip(),
            "site": self._site_edit.text().strip(),
            "stations": self._stations_kv.get_data(),
            "species": self._species_kv.get_data(),
        }
        save_setting(db, "code_labels", data)
        self._update_code_preview(db)

    def _save_naming_rules(self) -> None:
        db = self.ctx.get_db()
        if db is None:
            return
        from app.services.project_settings_service import (
            DEFAULT_NAMING_RULES,
            load_setting,
            save_setting,
        )
        data = load_setting(db, "naming_rules", DEFAULT_NAMING_RULES)
        data["required"] = {
            key: cb.isChecked()
            for key, cb in self._naming_required_checks.items()
        }
        data["components"] = [
            key for key, cb in self._naming_component_checks.items()
            if cb.isChecked()
        ]
        save_setting(db, "naming_rules", data)
        self.naming_rules_changed.emit()

    def _save_capture_defaults(self) -> None:
        """保存项目级默认采集坐标 / 地理区（capture_defaults）。"""
        db = self.ctx.get_db()
        if db is None:
            return
        from app.services.project_settings_service import save_setting
        save_setting(db, "capture_defaults", {
            "lon": self._cap_lon_edit.text().strip(),
            "lat": self._cap_lat_edit.text().strip(),
            "geoArea": self._cap_geo_edit.text().strip(),
        })

    def _save_tiff_fields(self) -> None:
        db = self.ctx.get_db()
        if db is None:
            return
        from app.services.project_settings_service import save_setting
        data = {key: cb.isChecked() for key, cb in self._tiff_checks.items()}
        save_setting(db, "tiff_fields", data)

    def _save_print_settings(self) -> None:
        db = self.ctx.get_db()
        if db is None:
            return
        from app.services.project_settings_service import save_setting
        save_setting(db, "print_settings", self._collect_print_settings())

    def _collect_print_settings(self) -> dict:
        quick_mode = str(self._quick_print_mode.currentData() or "dialog")
        return {
            # Kept true for old readers: both modern modes are printing modes.
            "quick_print": True,
            "quick_print_mode": quick_mode,
            "include_tissue": self._print_tissue_cb.isChecked(),
            "sample_printer": str(self._sample_printer_combo.currentData() or ""),
            "tissue_printer": str(self._tissue_printer_combo.currentData() or ""),
            "sample_template_key": str(self._sample_template_combo.currentData() or ""),
            "tissue_template_key": str(self._tissue_template_combo.currentData() or ""),
            "sample_paper_type": str(self._sample_paper_combo.currentData() or ""),
            "tissue_paper_type": str(self._tissue_paper_combo.currentData() or ""),
            "tissue_strategy": str(self._tissue_strategy_combo.currentData() or "auto"),
        }

    def _save_print_defaults(self) -> None:
        from app.services.project_settings_service import save_global_print_defaults
        save_global_print_defaults(self._collect_print_settings())
        QMessageBox.information(self, "打印默认值", "已保存为全局打印默认值。")

    def _on_sample_paper_changed(self) -> None:
        self._save_print_settings()
        self._sync_imposition_buttons()

    def _on_tissue_paper_changed(self) -> None:
        self._save_print_settings()
        self._sync_imposition_buttons()

    def _update_code_preview(self, db) -> None:
        try:
            row = db.execute(
                "SELECT uid FROM specimens ORDER BY rowid LIMIT 1"
            ).fetchone()
            if row:
                self._code_preview_lbl.setText(row[0])
            else:
                self._code_preview_lbl.setText("（无标本）")
        except Exception:
            self._code_preview_lbl.setText("（无标本）")

    # ── Custom storages list ──────────────────────────────────────────────────

    def _refresh_builtin_storage_table(self, db) -> None:
        from app.services.project_settings_service import (
            BUILTIN_STORAGES,
            load_custom_storages,
            resolve_storage_detail,
            resolve_storage_transcriptome,
        )

        custom = load_custom_storages(db) if db is not None else []
        tbl = self._builtin_storage_table
        for i, entry in enumerate(BUILTIN_STORAGES):
            code = entry["code"]
            detail = resolve_storage_detail(code, custom) or entry["detail"]
            transcriptome = resolve_storage_transcriptome(code, custom)
            if transcriptome:
                detail = "[RNA] " + detail
            code_cell = QTableWidgetItem(code)
            detail_cell = QTableWidgetItem(detail)
            detail_cell.setToolTip(detail)
            tbl.setItem(i, 0, code_cell)
            tbl.setItem(i, 1, detail_cell)
            tbl.setRowHeight(i, 24)
        tbl.setColumnWidth(0, 72)

    def _load_storage_edit_form(self, code: str, detail: str) -> None:
        self._storage_edit_mode = "edit"
        self._storage_editor_title.setText(f"修改保存方式：{code}")
        self._storage_save_btn.setText("保存修改")
        self._new_code_edit.setReadOnly(True)
        self._new_code_edit.setText(code)
        self._new_detail_edit.setPlainText(detail)
        self._rna_hint_lbl.setText(
            "已取 RNA / RNAlater" if str(code).startswith("R") else ""
        )
        self._storage_save_status.clear()

    def _start_new_storage(self) -> None:
        """Enter an explicit blank form for creating a new storage code."""
        self._storage_edit_mode = "new"
        self._storage_editor_title.setText("新增保存方式")
        self._storage_save_btn.setText("添加")
        self._new_code_edit.setReadOnly(False)
        self._new_code_edit.clear()
        self._new_detail_edit.clear()
        self._rna_hint_lbl.clear()
        self._storage_save_status.setText("新增模式：填写新编码和详细说明，然后点击“添加”。")
        self._builtin_storage_table.clearSelection()
        self._new_code_edit.setFocus()

    def _on_builtin_storage_selected(self) -> None:
        row = self._builtin_storage_table.currentRow()
        if row < 0:
            return
        code_item = self._builtin_storage_table.item(row, 0)
        detail_item = self._builtin_storage_table.item(row, 1)
        if code_item is None or detail_item is None:
            return
        detail = detail_item.text()
        if detail.startswith("[RNA] "):
            detail = detail[6:]
        self._load_storage_edit_form(code_item.text(), detail)

    def _rebuild_custom_list(self, custom: list[dict], db) -> None:
        from app.services.project_settings_service import builtin_storage_codes

        while self._custom_list_lay.count():
            item = self._custom_list_lay.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        builtin_codes = builtin_storage_codes()
        if not custom:
            empty = QLabel("还没有新增保存方式。")
            empty.setObjectName("MutedSmall")
            empty.setWordWrap(True)
            self._custom_list_lay.addWidget(empty)
            return
        for entry in custom:
            code = str(entry.get("code", "")).upper()
            detail = entry.get("detail", "")
            prefix = "[RNA] " if entry.get("transcriptome") else ""
            kind = "修改内置" if code in builtin_codes else "自定义"
            row_w = QWidget()
            h = QHBoxLayout(row_w)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)
            pick_btn = QPushButton(f"{code}　[{kind}]　{prefix}{detail}")
            pick_btn.setObjectName("Ghost")
            pick_btn.setFlat(True)
            pick_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            pick_btn.setToolTip("点击后在下方继续编辑；× 表示撤销该项目修改")
            pick_btn.clicked.connect(
                lambda _, c=code, d=detail: self._load_storage_edit_form(c, d)
            )
            h.addWidget(pick_btn, 1)
            del_btn = QPushButton("×")
            del_btn.setObjectName("Ghost")
            del_btn.setFixedSize(24, 24)
            del_btn.clicked.connect(
                lambda _, c=code: self._on_delete_custom_storage(c)
            )
            h.addWidget(del_btn)
            self._custom_list_lay.addWidget(row_w)

    def _on_save_storage(self) -> None:
        db = self.ctx.get_db()
        if db is None:
            self._storage_save_status.setText("保存失败：当前项目数据库未打开。")
            QMessageBox.warning(self, "保存方式", "保存失败：当前项目数据库未打开。")
            return
        code = self._new_code_edit.text().strip().upper()
        detail = self._new_detail_edit.toPlainText().strip()
        if not code or not detail:
            self._storage_save_status.setText("保存失败：编码和详细说明都必须填写。")
            return
        from app.services.project_settings_service import (
            load_custom_storages,
            save_setting,
        )

        custom = load_custom_storages(db)
        payload = {
            "code": code,
            "detail": detail,
            "transcriptome": code.startswith("R"),
        }
        replaced = False
        for index, entry in enumerate(custom):
            if str(entry.get("code", "")).upper() == code:
                custom[index] = payload
                replaced = True
                break
        if not replaced:
            custom.append(payload)
        try:
            save_setting(db, "custom_storages", custom)
            saved = load_custom_storages(db)
            verified = next(
                (
                    entry for entry in saved
                    if str(entry.get("code", "")).upper() == code
                    and str(entry.get("detail", "")) == detail
                ),
                None,
            )
            if verified is None:
                raise RuntimeError("数据库回读结果与保存内容不一致")
        except Exception as exc:
            self._storage_save_status.setText(f"保存失败：{exc}")
            QMessageBox.warning(self, "保存方式", f"保存失败：{exc}")
            return

        # Keep the edited values visible so the user can verify exactly what
        # was saved; the project list above is rebuilt from the DB read-back.
        self._refresh_builtin_storage_table(db)
        self._rebuild_custom_list(saved, db)
        action = "已添加" if self._storage_edit_mode == "new" else "已保存修改"
        self._storage_save_status.setText(f"{action}：{code}")
        self._load_storage_edit_form(code, detail)
        self._storage_save_status.setText(f"{action}：{code}")
        self.storages_changed.emit()

    def _on_add_custom_storage(self) -> None:
        self._on_save_storage()

    def _on_delete_custom_storage(self, code: str) -> None:
        db = self.ctx.get_db()
        if db is None:
            return
        from app.services.project_settings_service import (
            load_custom_storages,
            save_setting,
        )
        custom = load_custom_storages(db)
        custom = [s for s in custom if s["code"] != code]
        save_setting(db, "custom_storages", custom)
        self._refresh_builtin_storage_table(db)
        self._rebuild_custom_list(custom, db)
        self.storages_changed.emit()

    def _on_clear_custom_form(self) -> None:
        self._start_new_storage()

    # ── Field enable/disable ──────────────────────────────────────────────────

    def _set_fields_enabled(self, enabled: bool) -> None:
        for edit in self._meta_edits.values():
            edit.setEnabled(enabled)
        for edit in self._person_edits.values():
            edit.setEnabled(enabled)
        self._province_edit.setEnabled(enabled)
        self._site_edit.setEnabled(enabled)
        for cb in self._naming_required_checks.values():
            cb.setEnabled(enabled)
        for cb in self._naming_component_checks.values():
            cb.setEnabled(enabled)
        self._stations_kv.setEnabled(enabled)
        self._species_kv.setEnabled(enabled)
        for cb in self._tiff_checks.values():
            cb.setEnabled(enabled)
        self._new_code_edit.setEnabled(enabled)
        self._new_detail_edit.setEnabled(enabled)
        self._quick_print_mode.setEnabled(enabled)
        self._print_tissue_cb.setEnabled(enabled)
        self._sample_printer_combo.setEnabled(enabled)
        self._tissue_printer_combo.setEnabled(enabled)
        self._sample_template_combo.setEnabled(enabled)
        self._tissue_template_combo.setEnabled(enabled)
        self._sample_template_btn.setEnabled(enabled)
        self._tissue_template_btn.setEnabled(enabled)
        self._sample_paper_combo.setEnabled(enabled)
        self._tissue_paper_combo.setEnabled(enabled)
        self._sample_imposition_btn.setEnabled(enabled)
        self._tissue_imposition_btn.setEnabled(enabled)
        self._tissue_strategy_combo.setEnabled(enabled)
        self._save_print_default_btn.setEnabled(True)
        self._silent_compose_cb.setEnabled(True)
        self._sync_imposition_buttons()

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_close(self) -> None:
        self.hide()
        self.closed.emit()

    def _on_detect_helicon(self) -> None:
        custom_path = self._helicon_path_edit.text().strip()
        if custom_path:
            os.environ["HELICON_FOCUS_PATH"] = custom_path
        self.refresh()
        if custom_path:
            self.helicon_path_changed.emit(custom_path)

    def _on_auto_activate_changed(self, checked: bool) -> None:
        try:
            self.ctx.settings.auto_activate_on_new_specimen = checked
        except Exception:
            pass

    def _on_silent_compose_changed(self, checked: bool) -> None:
        try:
            self.ctx.settings.silent_compose = checked
            self.ctx.settings.sync()
        except Exception:
            pass


# ── helpers ───────────────────────────────────────────────────────────────────

def _scrollable_tab() -> tuple[QWidget, QVBoxLayout]:
    """Return (outer widget, inner VBoxLayout) where outer is a scroll area."""
    outer = QWidget()
    outer_lay = QVBoxLayout(outer)
    outer_lay.setContentsMargins(0, 0, 0, 0)
    outer_lay.setSpacing(0)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)

    inner = QWidget()
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(16, 12, 16, 12)
    lay.setSpacing(8)
    scroll.setWidget(inner)
    outer_lay.addWidget(scroll)
    return outer, lay


def _row(label: str, field: QWidget, width: int = 90) -> QWidget:
    from app.widgets._form_row import form_row
    return form_row(label, field, label_width=width)


def _settings_group(title: str, widgets: list[QWidget]) -> QGroupBox:
    """Bordered sub-section grouping related form rows inside a settings tab."""
    gb = QGroupBox(title)
    lay = QVBoxLayout(gb)
    lay.setSpacing(8)
    lay.setContentsMargins(12, 10, 12, 12)
    for wd in widgets:
        lay.addWidget(wd)
    return gb


def _divider() -> QFrame:
    f = QFrame()
    f.setObjectName("Divider")
    f.setFixedHeight(1)
    return f
