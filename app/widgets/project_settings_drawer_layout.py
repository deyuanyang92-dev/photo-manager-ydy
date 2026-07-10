"""Tab and static layout construction for ProjectSettingsDrawer."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.widgets.project_settings_drawer_support import (
    _CustomFieldEditor,
    _KVEditor,
    _divider,
    _scrollable_tab,
    _settings_form_row,
    _settings_group,
)


class ProjectSettingsLayoutMixin:
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
        title_box = QWidget()
        title_lay = QVBoxLayout(title_box)
        title_lay.setContentsMargins(0, 0, 0, 0)
        title_lay.setSpacing(2)
        self._title_label = QLabel("当前项目设置")
        self._title_label.setObjectName("WorkspaceTitle")
        self._header_scope_lbl = QLabel("未选择项目")
        self._header_scope_lbl.setObjectName("MutedSmall")
        self._header_scope_lbl.setWordWrap(True)
        title_lay.addWidget(self._title_label)
        title_lay.addWidget(self._header_scope_lbl)
        head.addWidget(title_box, 1)
        head.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setObjectName("Ghost")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self._hide_drawer_and_emit_closed)
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

        self._project_scope_lbl = QLabel("未选择项目")
        self._project_scope_lbl.setObjectName("MutedSmall")
        self._project_scope_lbl.setWordWrap(True)
        lay.addWidget(self._project_scope_lbl)

        project_lbl = QLabel("项目资料（保存到当前项目）")
        project_lbl.setObjectName("Section")
        lay.addWidget(project_lbl)

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
            lay.addWidget(_settings_form_row(label, edit))

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

        local_lbl = QLabel("本机工作台偏好（所有项目共用）")
        local_lbl.setObjectName("Section")
        local_lbl.setWordWrap(True)
        lay.addWidget(local_lbl)
        local_hint = QLabel("这些开关跟当前电脑和操作习惯有关，不写入项目数据库。")
        local_hint.setObjectName("MutedSmall")
        local_hint.setWordWrap(True)
        lay.addWidget(local_hint)

        # Auto-activate toggle
        self._auto_activate_cb = QCheckBox("新建编号后自动激活")
        self._auto_activate_cb.setToolTip("本机偏好：切换项目后仍沿用。")
        self._auto_activate_cb.toggled.connect(self._save_auto_activate_new_specimen_setting)
        lay.addWidget(self._auto_activate_cb)

        self._silent_compose_cb = QCheckBox("静默合成（跳过预览确认）")
        self._silent_compose_cb.setToolTip(
            "本机偏好：打开后，选中 JPG 点合成会直接运行 Helicon，成果先生成在 incoming。"
        )
        self._silent_compose_cb.toggled.connect(self._save_silent_compose_setting)
        lay.addWidget(self._silent_compose_cb)

        self._delete_jpg_after_archive_cb = QCheckBox("整理后删除散落 JPG（默认）")
        self._delete_jpg_after_archive_cb.setToolTip(
            "整理会先把 JPG 原片写入 ZIP；校验通过后删除待处理区散落 JPG。"
            "关闭后保留散落 JPG。整理不会自动删除 TIFF；不满意的 TIFF 可手动删除。"
        )
        self._delete_jpg_after_archive_cb.setChecked(True)
        self._delete_jpg_after_archive_cb.toggled.connect(
            self._save_delete_jpg_after_archive_setting
        )
        lay.addWidget(self._delete_jpg_after_archive_cb)

        lay.addWidget(_divider())

        # Helicon section (Qt-specific, web oracle uses separate modal)
        hel_lbl = QLabel("Helicon Focus 配置（本机）")
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
        detect_btn.clicked.connect(self._detect_and_apply_helicon_path)
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
        clear_btn.clicked.connect(self._start_blank_custom_storage_form)
        add_btn_row.addWidget(clear_btn)
        self._storage_save_btn = QPushButton("添加")
        self._storage_save_btn.setObjectName("Primary")
        self._storage_save_btn.setFixedHeight(28)
        self._storage_save_btn.clicked.connect(self._save_custom_storage_option)
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
            lbl_widget = _settings_form_row(label, edit, width=100)
            lay.addWidget(lbl_widget)

        lay.addStretch()
        return w

    # ── Tab 4: 命名规则 ───────────────────────────────────────────────────────

    def _build_tab_code_labels(self) -> QWidget:
        w, lay = _scrollable_tab()
        lay.setSpacing(10)

        common_lbl = QLabel("常用编号前缀")
        common_lbl.setObjectName("Section")
        lay.addWidget(common_lbl)
        common_hint = QLabel("通常只需要设置地区代码和样地代码；下面会实时显示编号示例。")
        common_hint.setObjectName("MutedSmall")
        common_hint.setWordWrap(True)
        lay.addWidget(common_hint)

        # Basic code inputs
        self._province_edit = QLineEdit()
        self._province_edit.setFixedHeight(30)
        self._province_edit.setPlaceholderText("如 ZJ")
        self._province_edit.editingFinished.connect(self._save_code_labels)
        lay.addWidget(_settings_form_row("地区代码", self._province_edit, width=80))

        self._site_edit = QLineEdit()
        self._site_edit.setFixedHeight(30)
        self._site_edit.setPlaceholderText("如 SMW")
        self._site_edit.editingFinished.connect(self._save_code_labels)
        lay.addWidget(_settings_form_row("样地代码", self._site_edit, width=80))

        lay.addWidget(_divider())

        self._naming_advanced_btn = QPushButton("展开高级命名设置")
        self._naming_advanced_btn.setObjectName("Ghost")
        self._naming_advanced_btn.setFixedHeight(28)
        self._naming_advanced_btn.clicked.connect(self._toggle_naming_advanced)
        lay.addWidget(self._naming_advanced_btn)

        self._naming_advanced_body = QWidget()
        self._naming_advanced_body.setVisible(False)
        advanced_lay = QVBoxLayout(self._naming_advanced_body)
        advanced_lay.setContentsMargins(0, 0, 0, 0)
        advanced_lay.setSpacing(10)
        lay.addWidget(self._naming_advanced_body)

        from app.services.naming_field_catalog import (
            component_fields,
            default_components,
        )

        custom_lbl = QLabel("自定义命名字段（一般不用）")
        custom_lbl.setObjectName("Section")
        custom_lbl.setWordWrap(True)
        advanced_lay.addWidget(custom_lbl)
        self._naming_custom_fields = _CustomFieldEditor()
        self._naming_custom_fields.changed.connect(self._save_naming_rules)
        advanced_lay.addWidget(self._naming_custom_fields)

        advanced_lay.addWidget(_divider())

        fields_lbl = QLabel("编号字段规则")
        fields_lbl.setObjectName("Section")
        advanced_lay.addWidget(fields_lbl)
        fields_hint = QLabel("控制哪些字段必填、哪些字段参与编号，以及编号拼接顺序。默认规则通常够用。")
        fields_hint.setObjectName("MutedSmall")
        fields_hint.setWordWrap(True)
        advanced_lay.addWidget(fields_hint)

        # Column header
        hdr = QWidget()
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(2, 0, 2, 2)
        hdr_lay.setSpacing(0)
        hdr_field = QLabel("字段")
        hdr_field.setObjectName("MutedSmall")
        hdr_lay.addWidget(hdr_field, 1)
        for _txt, _w in (("必填", 52), ("参与编号", 68), ("顺序", 56)):
            lbl = QLabel(_txt)
            lbl.setObjectName("MutedSmall")
            lbl.setFixedWidth(_w)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hdr_lay.addWidget(lbl)
        advanced_lay.addWidget(hdr)

        self._naming_required_checks: dict[str, QCheckBox] = {}
        self._naming_component_checks: dict[str, QCheckBox] = {}
        self._naming_component_order = [f.key for f in component_fields()]
        self._naming_unified_rows = QWidget()
        self._naming_unified_rows_lay = QVBoxLayout(self._naming_unified_rows)
        self._naming_unified_rows_lay.setContentsMargins(0, 0, 0, 0)
        self._naming_unified_rows_lay.setSpacing(2)
        advanced_lay.addWidget(self._naming_unified_rows)
        self._rebuild_naming_unified_table({}, set(default_components()))

        advanced_lay.addWidget(_divider())

        # 默认采集坐标 / 地理区（项目级兜底）。新建标本自动带；选定具体站位后，
        # 该站采集记录会以更高优先级覆盖（见 metadata_panel.apply_autofill）。
        cap_lbl = QLabel("默认采集坐标 / 地理区（可选）")
        cap_lbl.setObjectName("Section")
        cap_lbl.setWordWrap(True)
        advanced_lay.addWidget(cap_lbl)
        cap_hint = QLabel("仅在每个新标本都使用同一默认经纬度/地理区时填写；选中具体站位后会被采集记录覆盖。")
        cap_hint.setObjectName("MutedSmall")
        cap_hint.setWordWrap(True)
        advanced_lay.addWidget(cap_hint)
        self._cap_lon_edit = QLineEdit()
        self._cap_lon_edit.setFixedHeight(30)
        self._cap_lon_edit.setPlaceholderText("可留空")
        self._cap_lon_edit.editingFinished.connect(self._save_capture_defaults)
        advanced_lay.addWidget(_settings_form_row("默认经度", self._cap_lon_edit, width=80))
        self._cap_lat_edit = QLineEdit()
        self._cap_lat_edit.setFixedHeight(30)
        self._cap_lat_edit.setPlaceholderText("可留空")
        self._cap_lat_edit.editingFinished.connect(self._save_capture_defaults)
        advanced_lay.addWidget(_settings_form_row("默认纬度", self._cap_lat_edit, width=80))
        self._cap_geo_edit = QLineEdit()
        self._cap_geo_edit.setFixedHeight(30)
        self._cap_geo_edit.setPlaceholderText("可留空，如 三门湾")
        self._cap_geo_edit.editingFinished.connect(self._save_capture_defaults)
        advanced_lay.addWidget(_settings_form_row("默认地理区", self._cap_geo_edit, width=80))

        advanced_lay.addWidget(_divider())

        # Stations dict
        sta_lbl = QLabel("站位说明（可选）")
        sta_lbl.setObjectName("Section")
        advanced_lay.addWidget(sta_lbl)
        sta_hint = QLabel("只是把站位缩写翻译成中文备注，不影响编号生成；没有站位说明时可以不填。")
        sta_hint.setObjectName("MutedSmall")
        sta_hint.setWordWrap(True)
        advanced_lay.addWidget(sta_hint)
        self._stations_kv = _KVEditor(key_placeholder="缩写", val_placeholder="中文说明")
        self._stations_kv.changed.connect(self._save_code_labels)
        advanced_lay.addWidget(self._stations_kv)

        advanced_lay.addWidget(_divider())

        # Sample/species label dictionary
        sp_lbl = QLabel("物种编号说明（可选）")
        sp_lbl.setObjectName("Section")
        advanced_lay.addWidget(sp_lbl)
        sp_hint = QLabel("只是给 DLC001、MIX01 这类标签加中文说明，方便查看；不填写也不影响拍照整理。")
        sp_hint.setObjectName("MutedSmall")
        sp_hint.setWordWrap(True)
        advanced_lay.addWidget(sp_hint)
        self._species_kv = _KVEditor(key_placeholder="缩写", val_placeholder="中文说明")
        self._species_kv.changed.connect(self._save_code_labels)
        advanced_lay.addWidget(self._species_kv)

        lay.addWidget(_divider())

        # Preview card
        preview_lbl = QLabel("编号示例")
        preview_lbl.setObjectName("Section")
        lay.addWidget(preview_lbl)

        self._code_preview_card = QFrame()
        self._code_preview_card.setObjectName("NamingPreviewGroup")
        self._code_preview_card_lay = QHBoxLayout(self._code_preview_card)
        self._code_preview_card_lay.setContentsMargins(10, 8, 10, 8)
        self._code_preview_card_lay.setSpacing(0)
        lay.addWidget(self._code_preview_card)

        # fallback label (shown when no segments)
        self._code_preview_uid = QLabel("—")
        self._code_preview_uid.setObjectName("Muted")
        self._code_preview_card_lay.addWidget(self._code_preview_uid)
        self._code_preview_card_lay.addStretch()

        # keep old attr name so existing call sites still work
        self._code_preview_lbl = self._code_preview_uid

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

        self._tiff_write_enabled_cb = QCheckBox("整理完成后自动写入 TIFF 元数据")
        self._tiff_write_enabled_cb.setToolTip("写入项目/标本业务信息；相机、镜头、曝光等原始 EXIF 不会被覆盖。")
        self._tiff_write_enabled_cb.stateChanged.connect(self._save_tiff_metadata_write)
        lay.addWidget(self._tiff_write_enabled_cb)

        self._tiff_write_mode_combo = QComboBox()
        self._tiff_write_mode_combo.setFixedHeight(30)
        self._tiff_write_mode_combo.addItem("只补空字段（推荐）", "fill_empty")
        self._tiff_write_mode_combo.addItem("跳过已写入照片", "skip_written")
        self._tiff_write_mode_combo.addItem("强制覆盖软件字段", "force")
        self._tiff_write_mode_combo.currentIndexChanged.connect(self._save_tiff_metadata_write)
        lay.addWidget(_settings_form_row("写入策略", self._tiff_write_mode_combo, width=72))

        lay.addWidget(_divider())

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
        self._quick_print_mode.addItem("直接发送到默认/指定打印机（推荐）", "direct")
        self._quick_print_mode.addItem("打开打印窗口再确认", "dialog")
        self._quick_print_mode.currentIndexChanged.connect(self._save_print_settings)

        self._print_tissue_cb = QCheckBox("RNA 编号加打 RNA签")
        self._print_tissue_cb.setToolTip(
            "关闭后，工作台编号旁打印按钮只输出瓶签；"
            "开启后 R 前缀编号额外输出 RNAlater 组织管标签。"
        )
        self._print_tissue_cb.stateChanged.connect(self._save_print_settings)

        lay.addWidget(_settings_group("单张打印", [
            _settings_form_row("点击打印", self._quick_print_mode, width=72),
            self._print_tissue_cb,
        ]))

        # ── 瓶签 ─────────────────────────────────────────────────────────────
        self._sample_printer_combo = QComboBox()
        self._sample_printer_combo.setFixedHeight(30)
        self._sample_printer_combo.currentIndexChanged.connect(self._save_print_settings)
        self._sample_printer_combo.currentIndexChanged.connect(self._sync_niimbot_print_hint)

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

        self._sample_imposition_btn = QPushButton("瓶签排版…")
        self._sample_imposition_btn.setObjectName("Outline")
        self._sample_imposition_btn.setFixedHeight(28)
        self._sample_imposition_btn.clicked.connect(
            lambda: self._open_imposition_designer("sample")
        )

        sample_grp = _settings_group("瓶签", [
            _settings_form_row("打印机", self._sample_printer_combo, width=64),
            _settings_form_row("模板",   self._sample_template_combo, width=64),
            _settings_form_row("",       self._sample_template_btn, width=64),
            _settings_form_row("纸张",   self._sample_paper_combo, width=64),
            _settings_form_row("排版",   self._sample_imposition_btn, width=64),
        ])
        sample_grp.setToolTip("")
        lay.addWidget(sample_grp)

        # ── RNA签 ────────────────────────────────────────────────────────────
        self._tissue_printer_combo = QComboBox()
        self._tissue_printer_combo.setFixedHeight(30)
        self._tissue_printer_combo.currentIndexChanged.connect(self._save_print_settings)
        self._tissue_printer_combo.currentIndexChanged.connect(self._sync_niimbot_print_hint)

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

        self._tissue_imposition_btn = QPushButton("RNA签排版…")
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

        tissue_grp = _settings_group("RNA签", [
            _settings_form_row("打印机", self._tissue_printer_combo, width=64),
            _settings_form_row("模板",   self._tissue_template_combo, width=64),
            _settings_form_row("",       self._tissue_template_btn, width=64),
            _settings_form_row("纸张",   self._tissue_paper_combo, width=64),
            _settings_form_row("排版",   self._tissue_imposition_btn, width=64),
            _settings_form_row("策略",   self._tissue_strategy_combo, width=64),
        ])
        tissue_grp.setToolTip("")
        lay.addWidget(tissue_grp)

        self._niimbot_print_hint = QLabel("")
        self._niimbot_print_hint.setObjectName("MutedSmall")
        self._niimbot_print_hint.setWordWrap(True)
        self._niimbot_print_hint.setVisible(False)
        lay.addWidget(self._niimbot_print_hint)

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
