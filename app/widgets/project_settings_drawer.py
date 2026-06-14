"""project_settings_drawer.py — Project settings side drawer (5-tab redesign).

Mirrors renderProjectSettingsDrawer() (app.js:9418-9931) with tabs:
  概要 / 保存方式 / 人员预设 / 命名规则 / TIFF元数据

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
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
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
        title = QLabel("项目设置")
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
        self._tabs.addTab(self._build_tab_storages(),   "保存方式")
        self._tabs.addTab(self._build_tab_personnel(),  "人员预设")
        self._tabs.addTab(self._build_tab_code_labels(),"命名规则")
        self._tabs.addTab(self._build_tab_tiff_meta(),  "TIFF 元数据")

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
        tbl.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        tbl.setFixedHeight(min(len(BUILTIN_STORAGES) * 26 + 30, 240))
        for i, s in enumerate(BUILTIN_STORAGES):
            tbl.setItem(i, 0, QTableWidgetItem(s["code"]))
            detail = s["detail"]
            if s["transcriptome"]:
                detail = "[RNA] " + detail
            tbl.setItem(i, 1, QTableWidgetItem(detail))
            tbl.setRowHeight(i, 24)
        tbl.setColumnWidth(0, 72)
        lay.addWidget(tbl)

        lay.addWidget(_divider())

        custom_lbl = QLabel("自定义保存方式")
        custom_lbl.setObjectName("Section")
        lay.addWidget(custom_lbl)

        self._custom_list_lay = QVBoxLayout()
        self._custom_list_lay.setContentsMargins(0, 0, 0, 0)
        self._custom_list_lay.setSpacing(4)
        lay.addLayout(self._custom_list_lay)

        lay.addWidget(_divider())

        # Add-new form
        add_lbl = QLabel("添加自定义方式")
        add_lbl.setObjectName("MutedSmall")
        lay.addWidget(add_lbl)

        self._new_code_edit = QLineEdit()
        self._new_code_edit.setPlaceholderText("编码（如 RGLU）")
        self._new_code_edit.setFixedHeight(28)
        self._new_code_edit.textEdited.connect(
            lambda t: self._new_code_edit.setText(t.upper())
        )
        lay.addWidget(self._new_code_edit)

        self._new_detail_edit = QLineEdit()
        self._new_detail_edit.setPlaceholderText("详细说明（必填）")
        self._new_detail_edit.setFixedHeight(28)
        lay.addWidget(self._new_detail_edit)

        self._rna_hint_lbl = QLabel("")
        self._rna_hint_lbl.setObjectName("MutedSmall")
        lay.addWidget(self._rna_hint_lbl)
        self._new_code_edit.textChanged.connect(
            lambda t: self._rna_hint_lbl.setText("已取 RNA / RNAlater" if t.startswith("R") else "")
        )

        add_btn_row = QHBoxLayout()
        clear_btn = QPushButton("清空")
        clear_btn.setObjectName("Ghost")
        clear_btn.setFixedHeight(28)
        clear_btn.clicked.connect(self._on_clear_custom_form)
        add_btn_row.addWidget(clear_btn)
        add_new_btn = QPushButton("添加")
        add_new_btn.setObjectName("Primary")
        add_new_btn.setFixedHeight(28)
        add_new_btn.clicked.connect(self._on_add_custom_storage)
        add_btn_row.addWidget(add_new_btn)
        add_btn_row.addStretch()
        lay.addLayout(add_btn_row)

        lay.addStretch()
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
        sp_lbl = QLabel("物种缩写说明（缩写 → 中文）")
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
            DEFAULT_CAPTURE_DEFAULTS,
            DEFAULT_TIFF_FIELDS,
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
        custom = load_setting(db, "custom_storages", [])
        self._rebuild_custom_list(custom, db)

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

    def _rebuild_custom_list(self, custom: list[dict], db) -> None:
        while self._custom_list_lay.count():
            item = self._custom_list_lay.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        for entry in custom:
            row_w = QWidget()
            h = QHBoxLayout(row_w)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)
            code = entry.get("code", "")
            detail = entry.get("detail", "")
            prefix = "[RNA] " if entry.get("transcriptome") else ""
            lbl = QLabel(f"<b>{code}</b>　{prefix}{detail}")
            lbl.setWordWrap(True)
            h.addWidget(lbl, 1)
            del_btn = QPushButton("×")
            del_btn.setObjectName("Ghost")
            del_btn.setFixedSize(24, 24)
            del_btn.clicked.connect(
                lambda _, c=code: self._on_delete_custom_storage(c)
            )
            h.addWidget(del_btn)
            self._custom_list_lay.addWidget(row_w)

    def _on_add_custom_storage(self) -> None:
        db = self.ctx.get_db()
        if db is None:
            return
        code = self._new_code_edit.text().strip().upper()
        detail = self._new_detail_edit.text().strip()
        if not code or not detail:
            return
        from app.services.project_settings_service import load_setting, save_setting
        custom = load_setting(db, "custom_storages", [])
        if any(s["code"] == code for s in custom):
            return
        custom.append({
            "code": code,
            "detail": detail,
            "transcriptome": code.startswith("R"),
        })
        save_setting(db, "custom_storages", custom)
        self._on_clear_custom_form()
        self._rebuild_custom_list(custom, db)

    def _on_delete_custom_storage(self, code: str) -> None:
        db = self.ctx.get_db()
        if db is None:
            return
        from app.services.project_settings_service import load_setting, save_setting
        custom = load_setting(db, "custom_storages", [])
        custom = [s for s in custom if s["code"] != code]
        save_setting(db, "custom_storages", custom)
        self._rebuild_custom_list(custom, db)

    def _on_clear_custom_form(self) -> None:
        self._new_code_edit.clear()
        self._new_detail_edit.clear()
        self._rna_hint_lbl.setText("")

    # ── Field enable/disable ──────────────────────────────────────────────────

    def _set_fields_enabled(self, enabled: bool) -> None:
        for edit in self._meta_edits.values():
            edit.setEnabled(enabled)
        for edit in self._person_edits.values():
            edit.setEnabled(enabled)
        self._province_edit.setEnabled(enabled)
        self._site_edit.setEnabled(enabled)
        self._stations_kv.setEnabled(enabled)
        self._species_kv.setEnabled(enabled)
        for cb in self._tiff_checks.values():
            cb.setEnabled(enabled)
        self._new_code_edit.setEnabled(enabled)
        self._new_detail_edit.setEnabled(enabled)
        self._silent_compose_cb.setEnabled(True)

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


def _divider() -> QFrame:
    f = QFrame()
    f.setObjectName("Divider")
    f.setFixedHeight(1)
    return f
