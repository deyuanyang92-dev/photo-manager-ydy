"""UI construction for the taxonomy view."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.views import taxonomy_view_support as _tv
from app.views.taxonomy_table_model import (
    _ActionDelegate,
    _ChipButton,
    _TaxonTableModel,
    _ViewTabButton,
)


class TaxonomyLayoutMixin:
    # ── BaseView._setup_ui ────────────────────────────────────────────

    def _setup_ui(self) -> None:
        _tv._refresh_palette()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Scrollable controls area (header + toolbar) ───────────────
        # Wrapped in QScrollArea so it never squishes the table.
        ctrl_container = QWidget()
        ctrl_container.setObjectName("TaxonCtrlContainer")
        ctrl_container.setStyleSheet(
            "QWidget#TaxonCtrlContainer { background: transparent; }"
        )
        ctrl_v = QVBoxLayout(ctrl_container)
        ctrl_v.setContentsMargins(0, 0, 0, 0)
        ctrl_v.setSpacing(0)

        # ── Header (taxon-table-header) ───────────────────────────────
        header_frame = QFrame()
        header_frame.setObjectName("TaxonHeader")
        header_frame.setStyleSheet(
            f"QFrame#TaxonHeader {{ border: none;"
            f" border-bottom: 1px solid {_tv._C_BORDER}; }}"
        )
        header_layout = QVBoxLayout(header_frame)
        header_layout.setContentsMargins(24, 12, 24, 0)
        header_layout.setSpacing(8)

        # taxon-table-title-row
        title_row = QHBoxLayout()
        title_row.setSpacing(14)

        page_title = QLabel("内置分类库")
        page_title.setObjectName("TaxonPageTitle")
        page_title.setStyleSheet(
            f"QLabel {{ font-size: 18px; font-weight: 600; color: {_tv._C_TEXT};"
            f" background: transparent; }}"
        )
        title_row.addWidget(page_title)

        self._stats_label = QLabel("共 0 条")
        self._stats_label.setObjectName("Muted")
        self._stats_label.setStyleSheet(
            f"QLabel {{ font-size: 13px; color: {_tv._C_MUTED}; background: transparent; }}"
        )
        title_row.addWidget(self._stats_label)

        title_row.addStretch()

        # taxon-view-switch (3 tabs + "图表" toggle) in a capsule frame
        switch_wrapper = QFrame()
        switch_wrapper.setStyleSheet(
            f"QFrame {{ background: {_tv._C_PANEL}; border-radius: 8px; padding: 2px; }}"
        )
        switch_inner = QHBoxLayout(switch_wrapper)
        switch_inner.setContentsMargins(3, 3, 3, 3)
        switch_inner.setSpacing(2)

        self._btn_original = _ViewTabButton("原始分类")
        self._btn_worms    = _ViewTabButton("WoRMS 分类")
        self._btn_compare  = _ViewTabButton("对照视图")
        self._btn_original.setChecked(True)

        for btn, view_key in [
            (self._btn_original, "original"),
            (self._btn_worms,    "worms"),
            (self._btn_compare,  "compare"),
        ]:
            btn.clicked.connect(lambda checked, k=view_key: self._on_view_switch(k))
            switch_inner.addWidget(btn)

        title_row.addWidget(switch_wrapper)

        # "图表" toggle button — only active in "original" view (mirrors app.js)
        self._btn_chart = _ViewTabButton("图表")
        self._btn_chart.setChecked(False)
        self._btn_chart.clicked.connect(self._on_chart_toggle)
        title_row.addWidget(self._btn_chart)

        header_layout.addLayout(title_row)

        # taxon-col-controls (only in "original" view)
        self._col_ctrl_frame = QFrame()
        col_ctrl_layout = QHBoxLayout(self._col_ctrl_frame)
        col_ctrl_layout.setContentsMargins(0, 2, 0, 6)
        col_ctrl_layout.setSpacing(16)

        # 类群 group
        level_group = QHBoxLayout()
        level_group.setSpacing(6)
        level_label = QLabel("类群")
        level_label.setStyleSheet(
            f"QLabel {{ color: {_tv._C_DIM}; font-size: 11px; font-weight: 600;"
            f" background: transparent; }}"
        )
        level_group.addWidget(level_label)

        self._level_chips: dict[str, _ChipButton] = {}
        for level_key, level_text in _tv._LEVEL_CHIPS:
            chip = _ChipButton(level_text, checked=True)
            chip.toggled.connect(
                lambda checked, k=level_key: self._on_level_chip(k, checked)
            )
            level_group.addWidget(chip)
            self._level_chips[level_key] = chip
        col_ctrl_layout.addLayout(level_group)

        # 语言 group
        lang_group = QHBoxLayout()
        lang_group.setSpacing(6)
        lang_label = QLabel("语言")
        lang_label.setStyleSheet(
            f"QLabel {{ color: {_tv._C_DIM}; font-size: 11px; font-weight: 600;"
            f" background: transparent; }}"
        )
        lang_group.addWidget(lang_label)

        self._lang_chips: dict[str, _ChipButton] = {}
        for lang_key, lang_text in _tv._LANG_CHIPS:
            chip = _ChipButton(lang_text, checked=True)
            chip.toggled.connect(
                lambda checked, k=lang_key: self._on_lang_chip(k, checked)
            )
            lang_group.addWidget(chip)
            self._lang_chips[lang_key] = chip
        col_ctrl_layout.addLayout(lang_group)
        col_ctrl_layout.addStretch()

        # ── 字体 / 行高 调节 ─────────────────────────────────────────
        font_group = QHBoxLayout()
        font_group.setSpacing(4)
        font_lbl = QLabel("字号")
        font_lbl.setStyleSheet(
            f"QLabel {{ color: {_tv._C_DIM}; font-size: 11px; font-weight: 600;"
            f" background: transparent; }}"
        )
        font_group.addWidget(font_lbl)

        self._font_size: int = 12
        btn_font_minus = QPushButton("−")
        btn_font_minus.setFixedSize(22, 22)
        btn_font_minus.setStyleSheet(
            f"QPushButton {{ background: {_tv._C_PANEL}; border: 1px solid {_tv._C_BORDER};"
            f" border-radius: 4px; color: {_tv._C_TEXT_SOFT}; font-size: 13px; font-weight: bold; }}"
            f"QPushButton:hover {{ border-color: {_tv._C_ACCENT}; color: {_tv._C_ACCENT}; }}"
        )
        self._font_size_lbl = QLabel("12")
        self._font_size_lbl.setFixedWidth(22)
        self._font_size_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._font_size_lbl.setStyleSheet(f"color: {_tv._C_TEXT_SOFT}; font-size: 11px; background: transparent;")
        btn_font_plus = QPushButton("+")
        btn_font_plus.setFixedSize(22, 22)
        btn_font_plus.setStyleSheet(btn_font_minus.styleSheet())

        btn_font_minus.clicked.connect(lambda: self._adjust_font(-1))
        btn_font_plus.clicked.connect(lambda: self._adjust_font(1))

        font_group.addWidget(btn_font_minus)
        font_group.addWidget(self._font_size_lbl)
        font_group.addWidget(btn_font_plus)
        col_ctrl_layout.addLayout(font_group)

        header_layout.addWidget(self._col_ctrl_frame)
        ctrl_v.addWidget(header_frame)

        # ── Toolbar (taxon-table-toolbar) ─────────────────────────────
        toolbar_frame = QFrame()
        toolbar_frame.setObjectName("TaxonToolbar")
        toolbar_frame.setStyleSheet(
            f"QFrame#TaxonToolbar {{ border: none;"
            f" border-bottom: 1px solid {_tv._C_BORDER}; }}"
        )
        toolbar_layout = QVBoxLayout(toolbar_frame)
        toolbar_layout.setContentsMargins(24, 8, 24, 8)
        toolbar_layout.setSpacing(8)

        # filter bar (taxon-table-filter-bar)
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        self._col_select = QComboBox()
        self._col_select.setFixedWidth(116)
        for val, label in [
            ("",           "全部列"),
            ("classCn",    "纲(中)"),
            ("class",      "纲(拉丁)"),
            ("orderCn",    "目(中)"),
            ("order",      "目(拉丁)"),
            ("familyCn",   "科(中)"),
            ("family",     "科(拉丁)"),
            ("genusCn",    "属(中)"),
            ("genus",      "属(拉丁)"),
            ("speciesCn",  "种(中)"),
            ("species",    "种(拉丁)"),
        ]:
            self._col_select.addItem(label, val)
        filter_row.addWidget(self._col_select)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("搜索…")
        self._search_input.setMinimumWidth(180)
        self._search_input.returnPressed.connect(self._on_search)
        filter_row.addWidget(self._search_input)

        btn_search = QPushButton("搜索")
        btn_search.setObjectName("Outline")
        btn_search.setFixedWidth(60)
        btn_search.clicked.connect(self._on_search)
        filter_row.addWidget(btn_search)

        btn_clear = QPushButton("清除")
        btn_clear.setObjectName("Outline")
        btn_clear.setFixedWidth(60)
        btn_clear.clicked.connect(self._on_clear_filter)
        filter_row.addWidget(btn_clear)

        self._filter_active_label = QLabel("")
        self._filter_active_label.setStyleSheet(
            f"QLabel {{ color: {_tv._C_ACCENT}; font-size: 11px; background: transparent; }}"
        )
        self._filter_active_label.hide()
        filter_row.addWidget(self._filter_active_label)

        filter_row.addStretch()
        toolbar_layout.addLayout(filter_row)

        # action bar (taxon-table-actions)
        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        self._btn_add = QPushButton("+ 新增条目")
        self._btn_add.setObjectName("Primary")
        self._btn_add.setFixedWidth(96)
        self._btn_add.clicked.connect(self._on_add)
        action_row.addWidget(self._btn_add)

        # Thin separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"QFrame {{ color: {_tv._C_BORDER}; }}")
        action_row.addWidget(sep)

        self._selection_note = QLabel("已选 0 条")
        self._selection_note.setStyleSheet(
            f"QLabel {{ color: {_tv._C_MUTED}; font-size: 11px; background: transparent; }}"
        )
        action_row.addWidget(self._selection_note)

        btn_sel_all = QPushButton("全选筛选结果")
        btn_sel_all.setObjectName("Outline")
        btn_sel_all.clicked.connect(self._on_select_all_filtered)
        action_row.addWidget(btn_sel_all)

        btn_desel = QPushButton("取消选择")
        btn_desel.setObjectName("Outline")
        btn_desel.clicked.connect(self._on_deselect)
        action_row.addWidget(btn_desel)

        # Thin separator
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet(f"QFrame {{ color: {_tv._C_BORDER}; }}")
        action_row.addWidget(sep2)

        self._btn_worms_sel = QPushButton("WoRMS 更新所选")
        self._btn_worms_sel.setObjectName("Primary")
        self._btn_worms_sel.setEnabled(False)
        self._btn_worms_sel.clicked.connect(
            lambda: self._on_worms_update(selected_only=True)
        )
        action_row.addWidget(self._btn_worms_sel)

        btn_worms_filt = QPushButton("WoRMS 更新筛选结果")
        btn_worms_filt.setObjectName("Outline")
        btn_worms_filt.clicked.connect(
            lambda: self._on_worms_update(selected_only=False)
        )
        action_row.addWidget(btn_worms_filt)

        # Thin separator
        sep3 = QFrame()
        sep3.setFrameShape(QFrame.Shape.VLine)
        sep3.setStyleSheet(f"QFrame {{ color: {_tv._C_BORDER}; }}")
        action_row.addWidget(sep3)

        btn_export_xlsx = QPushButton("导出 Excel")
        btn_export_xlsx.setObjectName("Outline")
        btn_export_xlsx.clicked.connect(lambda: self._on_export("xlsx"))
        action_row.addWidget(btn_export_xlsx)

        btn_export_csv = QPushButton("导出 CSV")
        btn_export_csv.setObjectName("Outline")
        btn_export_csv.clicked.connect(lambda: self._on_export("csv"))
        action_row.addWidget(btn_export_csv)

        # 导入 Excel/CSV — file-picker button (taxon-import-label in DOM)
        btn_import = QPushButton("导入 Excel/CSV")
        btn_import.setObjectName("Outline")
        btn_import.clicked.connect(self._on_import)
        action_row.addWidget(btn_import)

        action_row.addStretch()
        toolbar_layout.addLayout(action_row)
        ctrl_v.addWidget(toolbar_frame)

        # Scroll area wrapping the controls
        ctrl_scroll = QScrollArea()
        ctrl_scroll.setWidget(ctrl_container)
        ctrl_scroll.setWidgetResizable(True)
        ctrl_scroll.setFrameShape(QFrame.Shape.NoFrame)
        ctrl_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        ctrl_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # Cap scroll area height so it never steals table space
        ctrl_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        ctrl_scroll.setMaximumHeight(200)
        ctrl_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        root.addWidget(ctrl_scroll)

        # ── Table area (taxon-table-wrap) ─────────────────────────────
        self._model = _TaxonTableModel(self)

        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(False)  # server-side sort via API
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionsMovable(True)   # drag to reorder columns
        self._table.horizontalHeader().setMinimumSectionSize(40)
        self._table.verticalHeader().setVisible(False)
        self._table.doubleClicked.connect(self._on_row_double_click)
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        # Checkbox toggles update the "已选 N 条" note + enable WoRMS-update button,
        # independent of row selection (clicking a checkbox does not select a full row).
        self._model.checked_changed.connect(self._update_selection_note)
        # Row height (default 32px, scales with font size)
        self._table.verticalHeader().setDefaultSectionSize(32)

        # Column widths for checkbox / # columns
        self._table.setColumnWidth(_tv._COL_CHECK, 32)
        self._table.setColumnWidth(_tv._COL_NUM,   40)
        # Action column delegate
        self._action_delegate = _ActionDelegate(self._table)
        self._action_delegate.edit_requested.connect(self._edit_record)
        self._action_delegate.delete_requested.connect(self._delete_record)

        # Ctrl+C → copy selected cells in Excel format (tab-separated cols, newline rows)
        self._table.keyPressEvent = self._table_key_press

        # Right-click context menu (mirrors openTaxonRowMenu in app.js)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_row_context_menu)

        # Column header right-click → facet filter (mirrors openTaxonFacetMenu)
        self._table.horizontalHeader().setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._table.horizontalHeader().customContextMenuRequested.connect(
            self._on_header_context_menu
        )

        # Table in a wrapper frame for the loading overlay
        table_wrapper = QFrame()
        table_wrapper.setObjectName("TaxonTableWrap")
        table_wrapper.setStyleSheet(
            "QFrame#TaxonTableWrap { border: none; }"
        )
        table_wrap_layout = QVBoxLayout(table_wrapper)
        table_wrap_layout.setContentsMargins(0, 0, 0, 0)
        table_wrap_layout.setSpacing(0)

        # Loading indicator (taxon-table-loading)
        self._loading_label = QLabel("加载中…")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label.setStyleSheet(
            f"QLabel {{ color: {_tv._C_ACCENT}; font-size: 14px; background: transparent;"
            f" padding: 24px; }}"
        )
        self._loading_label.hide()

        table_wrap_layout.addWidget(self._loading_label)
        table_wrap_layout.addWidget(self._table, 1)

        root.addWidget(table_wrapper, 1)

        # ── WoRMS job panel (mirrors renderTaxonJobPanel in app.js) ──────────
        self._job_panel_frame = QFrame()
        self._job_panel_frame.setObjectName("TaxonJobPanel")
        self._job_panel_frame.setStyleSheet(
            f"QFrame#TaxonJobPanel {{ background: {_tv._C_PANEL};"
            f" border: 1px solid {_tv._C_BORDER}; border-radius: 6px;"
            f" margin: 4px 24px; }}"
        )
        _jpanel_layout = QHBoxLayout(self._job_panel_frame)
        _jpanel_layout.setContentsMargins(12, 8, 12, 8)
        _jpanel_layout.setSpacing(12)
        self._job_title_label = QLabel("WoRMS 任务")
        self._job_title_label.setStyleSheet(f"font-weight: 600; color: {_tv._C_ACCENT}; font-size: 12px; background: transparent;")
        _jpanel_layout.addWidget(self._job_title_label)
        self._job_progress_label = QLabel("")
        self._job_progress_label.setStyleSheet(f"color: {_tv._C_MUTED}; font-size: 11px; background: transparent;")
        _jpanel_layout.addWidget(self._job_progress_label)
        self._job_bar = QProgressBar()
        self._job_bar.setFixedHeight(8)
        self._job_bar.setTextVisible(False)
        self._job_bar.setStyleSheet(
            f"QProgressBar {{ background: {_tv._C_INPUT}; border: none; border-radius: 4px; }}"
            f"QProgressBar::chunk {{ background: {_tv._C_ACCENT}; border-radius: 4px; }}"
        )
        self._job_bar.setFixedWidth(120)
        _jpanel_layout.addWidget(self._job_bar)
        self._job_counts_label = QLabel("")
        self._job_counts_label.setStyleSheet(f"color: {_tv._C_MUTED}; font-size: 11px; background: transparent;")
        _jpanel_layout.addWidget(self._job_counts_label)
        self._btn_job_pause = QPushButton("暂停")
        self._btn_job_pause.setObjectName("Outline")
        self._btn_job_pause.setFixedHeight(24)
        self._btn_job_pause.hide()
        _jpanel_layout.addWidget(self._btn_job_pause)
        self._btn_job_resume = QPushButton("继续")
        self._btn_job_resume.setObjectName("Outline")
        self._btn_job_resume.setFixedHeight(24)
        self._btn_job_resume.hide()
        _jpanel_layout.addWidget(self._btn_job_resume)
        self._btn_job_retry = QPushButton("重试失败")
        self._btn_job_retry.setObjectName("Outline")
        self._btn_job_retry.setFixedHeight(24)
        self._btn_job_retry.hide()
        _jpanel_layout.addWidget(self._btn_job_retry)
        _jpanel_layout.addStretch()
        self._btn_job_pause.clicked.connect(self._on_job_pause)
        self._btn_job_resume.clicked.connect(self._on_job_resume)
        self._btn_job_retry.clicked.connect(self._on_job_retry)
        self._job_panel_frame.hide()
        root.addWidget(self._job_panel_frame)

        # ── Pager (taxon-table-pager) ─────────────────────────────────
        pager_frame = QFrame()
        pager_frame.setObjectName("TaxonPager")
        pager_frame.setStyleSheet(
            f"QFrame#TaxonPager {{ border: none;"
            f" border-top: 1px solid {_tv._C_BORDER}; }}"
        )
        pager_layout = QHBoxLayout(pager_frame)
        pager_layout.setContentsMargins(24, 10, 24, 10)
        pager_layout.setSpacing(12)
        pager_frame.setFixedHeight(48)

        self._btn_prev = QPushButton("上一页")
        self._btn_prev.setObjectName("Outline")
        self._btn_prev.setFixedWidth(76)
        self._btn_prev.clicked.connect(self._on_prev_page)
        pager_layout.addWidget(self._btn_prev)

        self._page_info = QLabel("第 1 / 1 页（共 0 条）")
        self._page_info.setObjectName("TaxonPageInfo")
        self._page_info.setStyleSheet(
            f"QLabel {{ color: {_tv._C_MUTED}; font-size: 12px; background: transparent; }}"
        )
        pager_layout.addWidget(self._page_info)

        jump_label = QLabel("跳到")
        jump_label.setStyleSheet(
            f"QLabel {{ color: {_tv._C_DIM}; font-size: 12px; background: transparent; }}"
        )
        pager_layout.addWidget(jump_label)

        self._page_jump = QSpinBox()
        self._page_jump.setMinimum(1)
        self._page_jump.setMaximum(9999)
        self._page_jump.setValue(1)
        self._page_jump.setFixedWidth(68)
        self._page_jump.editingFinished.connect(self._on_jump_page)
        pager_layout.addWidget(self._page_jump)

        self._btn_next = QPushButton("下一页")
        self._btn_next.setObjectName("Outline")
        self._btn_next.setFixedWidth(76)
        self._btn_next.clicked.connect(self._on_next_page)
        pager_layout.addWidget(self._btn_next)

        pager_layout.addStretch()

        # stats summary at right of pager
        self._footer_label = QLabel("")
        self._footer_label.setStyleSheet(
            f"QLabel {{ color: {_tv._C_DIM}; font-size: 11px; background: transparent; }}"
        )
        pager_layout.addWidget(self._footer_label)

        root.addWidget(pager_frame)

        # ── Init service ──────────────────────────────────────────────
        self._try_init_service()
