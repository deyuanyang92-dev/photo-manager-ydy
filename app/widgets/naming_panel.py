"""naming_panel.py — 7-segment specimen UID / result-ID generator.

Provides a form with the six/seven naming segments, a live preview of
the generated UID and result-ID, and an optional R-prefix dual-label
warning when the storage code starts with "R".

Uses ``app.utils.naming.build_uid`` and ``build_result_id`` for
real-time assembly.

Signals
-------
uid_generated(uid: str)
    Emitted whenever the preview UID changes (user edits any field).
result_id_generated(result_id: str)
    Emitted whenever the preview result-ID changes.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.config import icons
from app.utils.naming import (
    build_result_id,
    build_uid,
    species_sequence_summary,
    specimen_date_seg,
)

if TYPE_CHECKING:
    from app.app_context import AppContext


# ── Preservation methods (保存方式) — mirrors the web oracle ──────────────────
# Oracle: app.js:290-308 (standardPreservationMethods + transcriptomePreservationMethods).
STANDARD_PRESERVATION_METHODS = [
    ("T95E", "梯度酒精固定，最终以 95% 酒精保存"),
    ("D95E", "直接使用 95% 酒精固定并保存"),
    ("D75E", "直接使用 75% 酒精固定并保存"),
    ("T75E", "梯度酒精固定，最终以 75% 酒精永久保存"),
    ("D79", "75% 酒精直接固定，之后转 95% 酒精长期保存"),
    ("T79", "梯度固定至 75% 酒精数日，之后转 95% 酒精长期保存"),
    ("T100", "梯度固定，最终以 100% 酒精保存"),
]
TRANSCRIPTOME_PRESERVATION_METHODS = [
    ("R95E", "已取 RNA，组织保存于 RNAlater；剩余标本以 95% 酒精保存"),
] + [
    ("R" + code, "已取 RNA，组织保存于 RNAlater；剩余标本按 " + detail)
    for code, detail in STANDARD_PRESERVATION_METHODS
]

# Sentinel item data values used by the storage combo.
_STORAGE_SENTINEL_CUSTOM = "__custom__"   # "其他… 打开项目设置"

# Section definitions for the ☰ visibility menu (label, settings key, attr getter).
_SECTION_DEFS = [
    ("采集位置", "geo",      lambda p: p._geo_group),
    ("编号规则", "identity", lambda p: p._identity_group),
    ("日期",     "date",     lambda p: p._date_group),
    ("拍照备注", "notes",    lambda p: p._notes_frame),
]

# code → detail text, for the 保存方式说明 gray row (web preservationMethodFor).
_PRES_DETAIL = {
    code: detail
    for code, detail in STANDARD_PRESERVATION_METHODS + TRANSCRIPTOME_PRESERVATION_METHODS
}

_UID_DISPLAY_SETTINGS_KEY = "naming_panel/uid_display_fields"
_UID_DISPLAY_FIELDS = (
    ("collector", "采集人"), ("photographer", "拍摄人"),
    ("identifier", "鉴定人"), ("notes", "备注"),
    ("photo_notes", "拍照备注"),
)
_UID_DISPLAY_DEFAULTS = {"collector", "photographer", "photo_notes"}


class NamingPanel(QWidget):
    """7-segment naming generator with live UID/result-ID preview.

    Signals
    -------
    uid_generated(str)
    result_id_generated(str)
    """

    uid_generated = pyqtSignal(str)
    result_id_generated = pyqtSignal(str)
    save_requested = pyqtSignal()
    delete_requested = pyqtSignal(str)
    uid_corrected = pyqtSignal(str, str)  # (old_uid, new_uid) after storage correction
    open_project_settings = pyqtSignal()  # "其他… 打开项目设置" picked in 保存方式
    keys_committed = pyqtSignal()         # 地区/样地/站位/采集日期 finished editing or picked
                                          # → workbench looks up a collection record and auto-fills

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        from PyQt6.QtCore import QSettings
        self.ctx = ctx
        self._ui_settings = QSettings()
        self._persisted_uid: Optional[str] = None  # UID of the currently loaded saved specimen
        self._storage_syncing = False  # re-entrancy guard between combo ↔ _storage
        self._section_visibility_cache: dict[str, bool] = {}
        self._field_labels: dict[str, QLabel] = {}
        self._field_label_text: dict[str, str] = {}
        self._naming_rules_required: dict[str, bool] = {}
        self._external_naming_values: dict[str, str] = {}
        self._display_metadata: dict[str, str] = {}
        self._display_fields = self._load_display_fields()
        self._display_notes_expanded = False
        self._setup_ui()

    # ── Collapse ────────────────────────────────────────────────────────────

    def set_collapsed(self, collapsed: bool) -> None:
        from app.widgets._collapse import set_layout_children_visible
        self._collapsed = collapsed
        set_layout_children_visible(self._root, 1, not collapsed)
        self._collapse_btn.setText("▸" if collapsed else "▾")
        self._collapse_btn.setToolTip("展开" if collapsed else "收起")

    def is_collapsed(self) -> bool:
        return self._collapsed

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        card = QFrame(self)
        card.setObjectName("PanelCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        from app.config.effects import apply_card_shadow
        apply_card_shadow(card)

        root = QVBoxLayout(card)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)
        self._root = root
        self._collapsed = False

        # Card header: title + save button + collapse toggle
        hdr_row = QHBoxLayout()
        hdr_row.setContentsMargins(0, 0, 0, 0)
        hdr = QLabel("照片编号")
        hdr.setObjectName("CardTitle")
        hdr_row.addWidget(hdr)
        hdr_row.addStretch()
        save_btn = QPushButton("保存")
        save_btn.setObjectName("Outline")
        save_btn.setFixedHeight(28)
        icons.set_button_icon(save_btn, "mdi6.content-save-outline",
                              color=icons.TONE_MUTED, size=14)
        save_btn.setToolTip("把当前输入存到本地，刷新不丢")
        save_btn.clicked.connect(self.save_requested.emit)
        hdr_row.addWidget(save_btn)
        self._record_btn = QPushButton("采集记录")
        self._record_btn.setObjectName("Ghost")
        self._record_btn.setFixedHeight(28)
        self._record_btn.setToolTip("从采集记录簿选站位 → 自动填充经纬度/采集人等")
        self._record_btn.clicked.connect(self._open_record_picker)
        hdr_row.addWidget(self._record_btn)
        self._sections_btn = QPushButton("☰")
        self._sections_btn.setObjectName("Ghost")
        self._sections_btn.setFixedSize(28, 28)
        self._sections_btn.setToolTip("分区显示控制")
        self._sections_btn.clicked.connect(self._open_sections_menu)
        hdr_row.addWidget(self._sections_btn)
        self._collapse_btn = QPushButton("▾")
        self._collapse_btn.setObjectName("Ghost")
        self._collapse_btn.setFixedSize(28, 28)
        self._collapse_btn.setToolTip("收起")
        self._collapse_btn.clicked.connect(
            lambda: self.set_collapsed(not self._collapsed)
        )
        hdr_row.addWidget(self._collapse_btn)
        root.addLayout(hdr_row)

        form = QVBoxLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(9)

        def _mk(placeholder: str, *, auto: bool = False) -> QLineEdit:
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            edit.setFixedHeight(34)
            if auto:
                edit.setObjectName("AutoField")
            return edit

        def _field(label: str, widget: QWidget, *, required: bool = False,
                   key: str = "",
                   help_text: str = "") -> QWidget:
            box = QWidget()
            box.setObjectName("CompactField")
            box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            lay = QVBoxLayout(box)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(3)

            label_row = QHBoxLayout()
            label_row.setContentsMargins(0, 0, 0, 0)
            label_row.setSpacing(4)
            lbl = QLabel()
            lbl.setObjectName("CompactFieldLabel")
            lbl.setTextFormat(Qt.TextFormat.RichText)
            if key:
                self._field_labels[key] = lbl
                self._field_label_text[key] = label
            self._set_required_label(lbl, label, required)
            label_row.addWidget(lbl)
            label_row.addStretch()
            lay.addLayout(label_row)
            lay.addWidget(widget)
            if help_text:
                box.setToolTip(help_text)
                lbl.setToolTip(help_text)
                widget.setToolTip(help_text)
            return box

        def _section(title: str, show_title: bool = True) -> tuple[QFrame, QGridLayout]:
            frame = QFrame()
            frame.setObjectName("NamingGroup")
            lay = QVBoxLayout(frame)
            lay.setContentsMargins(10, 8, 10, 10)
            lay.setSpacing(6)
            if show_title:
                lbl = QLabel(title)
                lbl.setObjectName("NamingGroupTitle")
                lay.addWidget(lbl)
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(8)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            lay.addLayout(grid)
            return frame, grid

        # Auto-derived naming segments (web .auto class)
        self._province = _mk("如 FJ", auto=True)
        self._province.setMinimumWidth(60)
        self._site = _mk("如 YGLZ", auto=True)
        self._site.setMinimumWidth(60)
        self._station = _mk("如 B2", auto=True)
        self._species_id = _mk("如 DLC001")
        self._species_id.setMaximumWidth(150)

        self._geo_group, geo_grid = _section("采集位置", show_title=False)
        geo_grid.setColumnStretch(0, 1)
        geo_grid.setColumnStretch(1, 1)
        geo_grid.setColumnStretch(2, 1)
        geo_grid.addWidget(_field("地区", self._province, required=True,
                                  key="province",
                                  help_text="地区代码，如 FJ＝福建；通常由项目自动推导"), 0, 0)
        geo_grid.addWidget(_field("样地", self._site, required=True,
                                  key="site",
                                  help_text="样地代码，如 YGLZ；通常自动推导"), 0, 1)
        geo_grid.addWidget(_field("站位", self._station,
                                  key="station",
                                  help_text="采集站位，如 B2；缺省时唯一编号自动少一段"), 0, 2)
        form.addWidget(self._geo_group)

        self._identity_group, identity_grid = _section("编号规则", show_title=False)
        identity_grid.setColumnStretch(0, 0)
        identity_grid.setColumnStretch(1, 0)
        identity_grid.setColumnStretch(2, 1)
        identity_grid.addWidget(_field("物种缩写", self._species_id, required=True,
                                       key="species_id",
                                       help_text="物种拼音缩写编号，如 DLC001；这是标本唯一编号的一段，不是完整 voucher number"), 0, 0)

        # Sequence hint + 填入建议 — inline (no popup), aligned under the field column.
        seq_cell = QWidget()
        seq_lay = QHBoxLayout(seq_cell)
        seq_lay.setContentsMargins(0, 0, 0, 0)
        seq_lay.setSpacing(8)
        self._seq_hint_label = QLabel("输入物种缩写前缀后显示下一个可用编号")
        self._seq_hint_label.setObjectName("MutedSmall")
        self._seq_hint_label.setWordWrap(True)
        seq_lay.addWidget(self._seq_hint_label, stretch=1)
        self._seq_apply_btn = QPushButton("填入建议")
        self._seq_apply_btn.setObjectName("Ghost")
        self._seq_apply_btn.setFixedHeight(26)
        self._seq_apply_btn.setEnabled(False)
        self._seq_apply_btn.clicked.connect(self._apply_sequence_suggestion)
        seq_lay.addWidget(self._seq_apply_btn)
        # Span all three columns — pinned to col 0 alone the cell is only as wide
        # as the 物种缩写 field, so the hint label collapsed to ~1 char and wrapped
        # vertically (one CJK glyph per line) on narrow rails.
        identity_grid.addWidget(seq_cell, 1, 0, 1, 3)

        self._seq = QSpinBox()
        self._seq.setMinimum(1)
        self._seq.setMaximum(999)
        self._seq.setValue(1)
        self._seq.setFixedHeight(34)
        self._seq.setMaximumWidth(78)
        identity_grid.addWidget(_field("成果序号", self._seq,
                                       help_text="成果序号（自动递增）"), 0, 1)

        # ── 保存方式 — hidden free-text proxy + grouped dropdown ──
        # The proxy QLineEdit holds the canonical storage value read by
        # workbench_view._on_naming_save and the tests.  The web naming card has
        # NO free-text 自定义编码 field — custom codes go through 其他…打开项目设置.
        self._storage = QLineEdit()
        self._storage.setPlaceholderText("如 T95E / RD75E")
        self._storage.hide()
        self._storage.textChanged.connect(self._update_preview)

        # 保存方式 dropdown — mirrors the web oracle's grouped <select>
        # (常规保存 / 已取RNA / 其他…打开设置, app.js:9259-9307).  QComboBox has no
        # native optgroup, so _build_storage_combo() loads a QStandardItemModel.
        self._storage_combo = QComboBox()
        self._storage_combo.setObjectName("StorageCombo")
        self._storage_combo.setFixedHeight(34)
        self._storage_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        # Long items ("R95E — 已取 RNA…") otherwise bake a ~634px minimumSizeHint
        # that forces the whole rail past its 480px cap and clips every card.
        # Cap closed width to a few chars (text elides); popup keeps full width.
        self._storage_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._storage_combo.setMinimumContentsLength(6)
        self._storage_combo.setMinimumWidth(170)
        self._storage_combo.view().setMinimumWidth(360)
        self._build_storage_combo()
        self._storage_combo.activated.connect(self._on_storage_combo)
        self._storage.textChanged.connect(
            lambda t: self._sync_combo_to_storage(t.strip())
        )
        identity_grid.addWidget(_field("保存方式", self._storage_combo, required=True,
                                       key="storage",
                                       help_text="标本保存方式；R 前缀表示已取 RNA（RNAlater）"), 0, 2)
        # 保存方式说明灰字 + ✓已取RNA·RNAlater 徽标 (web pres-detail-row, app.js:9309)
        self._pres_detail = QLabel("")
        self._pres_detail.setObjectName("PresDetail")
        self._pres_detail.setWordWrap(True)
        self._pres_detail.setStyleSheet(
            "QLabel#PresDetail{color:#7f9aa0;font-size:11px;}"
        )
        pres_cell = QWidget()
        pres_lay = QVBoxLayout(pres_cell)
        pres_lay.setContentsMargins(0, 0, 0, 0)
        pres_lay.addWidget(self._pres_detail, 1)
        identity_grid.addWidget(pres_cell, 1, 2)
        form.addWidget(self._identity_group)

        # 采集日期 / 拍摄日期 —— 紧凑：无段标题（省一行），两字段一行；
        # 整段还可在 ☰ 分区菜单里隐藏（太占空间时关掉）。
        self._date_group, date_grid = _section("日期", show_title=False)
        date_grid.setColumnStretch(0, 1)
        date_grid.setColumnStretch(1, 1)
        self._collection_date = _mk("采集 YYYYMMDD")
        date_grid.addWidget(_field("采集日期", self._collection_date, required=True,
                                   key="collection_date",
                                   help_text="采集日期 YYYYMMDD —— 核心字段，写入唯一编号"), 0, 0)
        self._photo_date = _mk("拍摄 YYYYMMDD")
        date_grid.addWidget(_field("拍摄日期", self._photo_date, required=True,
                                   key="photo_date",
                                   help_text="拍摄日期 YYYYMMDD"), 0, 1)
        form.addWidget(self._date_group)

        root.addLayout(form)

        # 拍照备注（可选）— full-width textarea (web naming-photo-notes, app.js:9344)
        self._notes_frame = QFrame()
        notes_frame = self._notes_frame
        notes_frame.setObjectName("NamingGroup")
        notes_lay = QVBoxLayout(notes_frame)
        notes_lay.setContentsMargins(10, 8, 10, 10)
        notes_lay.setSpacing(6)
        pn_lbl = QLabel("拍照备注")
        pn_lbl.setObjectName("NamingGroupTitle")
        notes_lay.addWidget(pn_lbl)
        self._photo_notes = QTextEdit()
        self._photo_notes.setObjectName("PhotoNotes")
        self._photo_notes.setFixedHeight(72)
        self._photo_notes.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._photo_notes.document().contentsChanged.connect(self._auto_resize_notes)
        self._photo_notes.textChanged.connect(self._on_photo_notes_display_changed)
        self._photo_notes.setPlaceholderText(
            "拍照现场备注，例：曝光异常、对焦点变化、补拍原因…"
        )
        notes_lay.addWidget(self._photo_notes)
        root.addWidget(notes_frame)

        # ── Live preview blocks ──
        preview_frame = QFrame()
        preview_frame.setObjectName("NamingPreviewGroup")
        preview_lay = QVBoxLayout(preview_frame)
        preview_lay.setContentsMargins(10, 8, 10, 10)
        preview_lay.setSpacing(6)
        preview_hdr = QHBoxLayout()
        preview_hdr.setContentsMargins(0, 0, 0, 0)
        preview_lbl = QLabel("标本唯一编号 / voucher number")
        preview_lbl.setObjectName("NamingGroupTitle")
        preview_hdr.addWidget(preview_lbl)
        preview_hdr.addStretch()
        self._pin_btn = QPushButton("添加")
        self._pin_btn.setObjectName("Primary")
        self._pin_btn.setFixedHeight(26)
        self._pin_btn.setToolTip("把当前 voucher number 保存并添加到左侧标本列表")
        icons.set_button_icon(self._pin_btn, "mdi6.pin-outline",
                              color=icons.TONE_ON_ACCENT, size=13)
        self._pin_btn.clicked.connect(self.save_requested.emit)
        preview_hdr.addWidget(self._pin_btn)
        self._delete_btn = QPushButton("删除")
        self._delete_btn.setObjectName("Outline")
        self._delete_btn.setFixedHeight(26)
        self._delete_btn.setToolTip("删除当前选中的标本编号（不删除磁盘成果文件）")
        icons.set_button_icon(self._delete_btn, "mdi6.trash-can-outline",
                              color=icons.TONE_DANGER, size=13)
        self._delete_btn.clicked.connect(self._emit_delete_requested)
        self._delete_btn.hide()
        preview_hdr.addWidget(self._delete_btn)
        copy_uid = QToolButton()
        copy_uid.setObjectName("CompactIconButton")
        copy_uid.setToolTip("复制标本唯一编号 / voucher number")
        copy_uid.setIcon(icons.icon("mdi6.content-copy", color=icons.TONE_MUTED))
        copy_uid.clicked.connect(self._copy_uid)
        preview_hdr.addWidget(copy_uid)
        self._display_fields_btn = QToolButton()
        self._display_fields_btn.setObjectName("CompactIconButton")
        self._display_fields_btn.setToolTip("选择唯一编号下方的展示信息")
        self._display_fields_btn.setIcon(
            icons.icon("mdi6.tune-variant", color=icons.TONE_MUTED)
        )
        self._display_fields_btn.clicked.connect(self._open_display_fields_menu)
        preview_hdr.addWidget(self._display_fields_btn)
        preview_lay.addLayout(preview_hdr)
        self._uid_preview = QLabel("—")
        self._uid_preview.setObjectName("PreviewEmpty")
        self._uid_preview.setWordWrap(True)
        self._uid_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        preview_lay.addWidget(self._uid_preview)
        self._display_people = QLabel("")
        self._display_people.setObjectName("MutedSmall")
        self._display_people.setWordWrap(True)
        self._display_people.hide()
        preview_lay.addWidget(self._display_people)
        notes_row = QHBoxLayout()
        notes_row.setContentsMargins(0, 0, 0, 0)
        notes_row.setSpacing(4)
        self._display_notes = QLabel("")
        self._display_notes.setObjectName("MutedSmall")
        self._display_notes.setWordWrap(True)
        self._display_notes.hide()
        notes_row.addWidget(self._display_notes, 1)
        self._display_notes_btn = QPushButton("展开")
        self._display_notes_btn.setObjectName("Tiny")
        self._display_notes_btn.setFixedHeight(22)
        self._display_notes_btn.clicked.connect(self._toggle_display_notes)
        self._display_notes_btn.hide()
        notes_row.addWidget(self._display_notes_btn)
        preview_lay.addLayout(notes_row)
        root.addWidget(preview_frame)

        # 成果编号（含序号）not shown in the web naming card — kept as a hidden
        # holder so current_result_id() / result_id_generated still work.
        self._result_preview = QLabel("—")
        self._result_preview.setObjectName("PreviewEmpty")
        self._result_preview.hide()

        # R-prefix dual-label note is tooltip-only; no permanent banner.
        self._rna_warning = QLabel(
            "R 前缀（已取 RNA）— 需额外生成 RNAlater 组织管标签"
        )
        self._rna_warning.setObjectName("RnaWarning")
        self._rna_warning.setWordWrap(True)
        self._rna_warning.hide()
        root.addWidget(self._rna_warning)

        # Duplicate-UID warning (naming-dup-warn, hidden by default)  #cursor
        self._dup_warn = QLabel("⚠ 标本唯一编号重复 — 该 voucher number 已存在")
        self._dup_warn.setObjectName("UnattributedWarning")
        self._dup_warn.setWordWrap(True)
        self._dup_warn.hide()
        root.addWidget(self._dup_warn)

        # Design-compliance warning (7-segment format issues)  #cursor
        self._compliance_warn = QLabel("")
        self._compliance_warn.setObjectName("UnattributedWarning")
        self._compliance_warn.setWordWrap(True)
        self._compliance_warn.hide()
        root.addWidget(self._compliance_warn)

        root.addStretch()

        # Restore section visibility from QSettings（日期段现在默认可见——用户要手填
        # 采集/拍摄日期，喂 UID 日期段 + 标本记录；之前被永久隐藏是错的）。
        for _, key, getter in _SECTION_DEFS:
            getter(self).setVisible(self._load_section_vis(key))

        # Wire all edits to live-preview
        for widget in (
            self._province, self._site, self._station, self._species_id,
        ):
            widget.textChanged.connect(
                lambda text, edit=widget: self._normalize_uppercase_edit(edit, text)
            )
        for widget in (
            self._province, self._site, self._station,
            self._species_id, self._storage,
            self._collection_date, self._photo_date,
        ):
            widget.textChanged.connect(self._update_preview)
        self._seq.valueChanged.connect(self._update_preview)

        # When the 4 location keys finish editing, ask the workbench to look up
        # a matching collection record and auto-fill empty capture fields.
        # editingFinished (focus-out / Enter) keeps it low-noise — no DB write here.
        for widget in (self._province, self._site, self._station, self._collection_date):
            widget.editingFinished.connect(self.keys_committed.emit)
        self.refresh_naming_rules()

    # ── Public API ────────────────────────────────────────────────────────────

    def load_specimen(self, sp: dict) -> None:
        """Populate fields from a specimen dict (uses sp field names from Specimen model).

        Chinese fields (taxonGroupCn, orderCn …) are NOT auto-filled — user
        fills those manually per the project constraint.
        """
        self._persisted_uid = sp.get("uid") or sp.get("uniqueId") or None
        self._province.setText(sp.get("province") or "")
        self._site.setText(sp.get("site") or "")
        self._station.setText(sp.get("station") or "")
        self._species_id.setText(sp.get("id") or "")
        storage_val = sp.get("storage") or ""
        self._storage.setText(storage_val)
        # Sync the storage dropdown selection with the loaded value.
        self._sync_combo_to_storage(storage_val)
        self._collection_date.setText(sp.get("collectionDate") or sp.get("collection_date") or "")
        self._photo_date.setText(sp.get("photoDate") or sp.get("photo_date") or "")
        self._photo_notes.blockSignals(True)
        self._photo_notes.setPlainText(sp.get("photoNotes") or sp.get("photo_notes") or "")
        self._photo_notes.blockSignals(False)
        self.set_display_metadata(sp)
        # seq hint
        hint = sp.get("nextResultSequenceHint") or sp.get("next_result_sequence_hint") or 1
        try:
            self._seq.setValue(int(hint))
        except (ValueError, TypeError):
            self._seq.setValue(1)
        self._update_preview()
        self._update_edit_actions()

    def set_display_metadata(self, values: dict) -> None:
        """更新 UID 下方的展示摘要；不参与 UID 构造或复制。"""
        aliases = {"photo_notes": "photoNotes"}
        self._display_metadata = {
            key: str(values.get(key) or values.get(aliases.get(key, "")) or "").strip()
            for key, _label in _UID_DISPLAY_FIELDS
        }
        self._refresh_display_summary()

    def set_display_fields(self, fields: set[str]) -> None:
        allowed = {key for key, _label in _UID_DISPLAY_FIELDS}
        self._display_fields = set(fields) & allowed
        settings = self._ui_settings
        settings.setValue(
            _UID_DISPLAY_SETTINGS_KEY, ",".join(sorted(self._display_fields))
        )
        settings.sync()
        self._refresh_display_summary()

    def _load_display_fields(self) -> set[str]:
        raw = self._ui_settings.value(_UID_DISPLAY_SETTINGS_KEY, None)
        if raw is None:
            return set(_UID_DISPLAY_DEFAULTS)
        if isinstance(raw, str):
            raw = [part for part in raw.split(",") if part]
        allowed = {key for key, _label in _UID_DISPLAY_FIELDS}
        return {str(v) for v in (raw or []) if str(v) in allowed}

    def _build_display_fields_menu(self):
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        for key, label in _UID_DISPLAY_FIELDS:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(key in self._display_fields)
            action.toggled.connect(
                lambda checked, k=key: self._set_display_field_enabled(k, checked)
            )
        return menu

    def _open_display_fields_menu(self) -> None:
        menu = self._build_display_fields_menu()
        menu.exec(self._display_fields_btn.mapToGlobal(
            self._display_fields_btn.rect().bottomLeft()
        ))

    def _set_display_field_enabled(self, key: str, enabled: bool) -> None:
        fields = set(self._display_fields)
        if enabled:
            fields.add(key)
        else:
            fields.discard(key)
        self.set_display_fields(fields)

    def _refresh_display_summary(self) -> None:
        # setup_ui 创建备注框时会先连信号，容忍预览控件尚未创建。
        if not hasattr(self, "_display_people"):
            return
        values = dict(self._display_metadata)
        people_labels = {
            "collector": "采集", "photographer": "拍摄", "identifier": "鉴定",
        }
        people = [
            f"{people_labels[key]}：{values.get(key, '')}"
            for key in ("collector", "photographer", "identifier")
            if key in self._display_fields and values.get(key)
        ]
        self._display_people.setText("  ·  ".join(people))
        self._display_people.setVisible(bool(people))

        notes = []
        for key, label in (("notes", "备注"), ("photo_notes", "拍照备注")):
            if key in self._display_fields and values.get(key):
                notes.append(f"{label}：{values[key]}")
        full = "  ·  ".join(notes)
        clipped = len(full) > 64 and not self._display_notes_expanded
        self._display_notes.setText(full[:64].rstrip() + "…" if clipped else full)
        self._display_notes.setToolTip(full)
        self._display_notes.setVisible(bool(full))
        self._display_notes_btn.setVisible(len(full) > 64)
        self._display_notes_btn.setText("收起" if self._display_notes_expanded else "展开")

    def _on_photo_notes_display_changed(self) -> None:
        self._display_metadata["photo_notes"] = self._photo_notes.toPlainText().strip()
        self._refresh_display_summary()

    def _toggle_display_notes(self) -> None:
        self._display_notes_expanded = not self._display_notes_expanded
        self._refresh_display_summary()

    def current_uid(self) -> str:
        return self._uid_preview.text() if self._uid_preview.text() != "—" else ""

    def current_result_id(self) -> str:
        return self._result_preview.text() if self._result_preview.text() != "—" else ""

    def missing_required_fields(self) -> list[str]:
        """Return labels for project-required naming fields that are empty."""
        required = self._load_required_rules()
        self._naming_rules_required = required
        values = {
            "province": self._province.text().strip(),
            "site": self._site.text().strip(),
            "station": self._station.text().strip(),
            "species_id": self._species_id.text().strip(),
            "storage": self._storage.text().strip(),
            "collection_date": self._collection_date.text().strip(),
            "photo_date": self._photo_date.text().strip(),
        }
        labels = {
            "province": "地区",
            "site": "样地",
            "station": "站位",
            "species_id": "物种编号",
            "storage": "保存方式",
            "collection_date": "采集日期",
            "photo_date": "拍照日期",
        }
        return [
            labels[key]
            for key, is_required in required.items()
            if is_required and not values.get(key)
        ]

    def show_dup_warn(self, show: bool = True) -> None:
        """Show or hide the duplicate-UID warning label."""
        if show:
            self._dup_warn.show()
        else:
            self._dup_warn.hide()

    def persisted_uid(self) -> str:
        """Return the loaded saved UID, or an empty string for a new draft."""
        return self._persisted_uid or ""

    def current_sequence_suggestion(self) -> str:
        """Return the currently suggested species id, e.g. ``DLC004``."""
        return str(self._seq_apply_btn.property("suggested_id") or "")

    def set_external_naming_values(self, values: dict[str, str]) -> None:
        """Provide optional fields from taxonomy/metadata cards for UID templates."""
        self._external_naming_values = {
            str(k): str(v or "").strip()
            for k, v in (values or {}).items()
        }
        self._update_preview()

    # ── Collection-record keys (auto-fill source) ─────────────────────────────

    def current_keys(self) -> tuple[str, str, str, str]:
        """Return (province, site, station, collection_date) as typed."""
        return (
            self._province.text().strip(),
            self._site.text().strip(),
            self._station.text().strip(),
            self._collection_date.text().strip(),
        )

    def set_location_keys(
        self, province: str, site: str, station: str, collection_date: str
    ) -> None:
        """Backfill the four location keys (e.g. from a picked record).

        Updates the preview once, then emits ``keys_committed`` so the workbench
        runs the auto-fill. Other fields (物种/保存方式) are untouched.
        """
        for edit, val in (
            (self._province, str(province or "").upper()),
            (self._site, str(site or "").upper()),
            (self._station, str(station or "").upper()),
            (self._collection_date, collection_date or ""),
        ):
            edit.blockSignals(True)
            edit.setText(val or "")
            edit.blockSignals(False)
        self._update_preview()
        self.keys_committed.emit()

    def _open_record_picker(self) -> None:
        """Pop a menu of this project's collection records; picking one fills keys."""
        from PyQt6.QtWidgets import QMenu
        db = None
        try:
            db = self.ctx.get_db()
        except Exception:
            db = None
        records = []
        if db is not None:
            try:
                from app.services import collection_record_service as crs
                records = crs.list_records(db)
            except Exception:
                records = []
        menu = QMenu(self)
        if not records:
            act = menu.addAction("（无采集记录，请到「采集记录」页录入）")
            act.setEnabled(False)
        else:
            for rec in records:
                label = " · ".join(
                    str(x) for x in (rec.get("station"), rec.get("collection_date"),
                                     rec.get("station_label")) if x
                )
                act = menu.addAction(label or "(未命名记录)")
                act.triggered.connect(lambda _checked=False, r=rec: self._pick_record(r))
        menu.exec(self._record_btn.mapToGlobal(self._record_btn.rect().bottomLeft()))

    def _pick_record(self, rec: dict) -> None:
        self.set_location_keys(
            rec.get("province") or "", rec.get("site") or "",
            rec.get("station") or "", rec.get("collection_date") or "",
        )

    # ── Section visibility (☰ menu) ───────────────────────────────────────────

    def _open_sections_menu(self) -> None:
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        for label, key, getter in _SECTION_DEFS:
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._load_section_vis(key))
            act.toggled.connect(
                lambda on, k=key, g=getter: self._toggle_section(k, g(self), on)
            )
        menu.exec(self._sections_btn.mapToGlobal(
            self._sections_btn.rect().bottomLeft()
        ))

    def _toggle_section(self, key: str, frame: "QFrame", visible: bool) -> None:
        visible = bool(visible)
        frame.setVisible(visible)
        self._section_visibility_cache[key] = visible
        from PyQt6.QtCore import QSettings
        settings = QSettings()
        settings.setValue(f"naming_panel/section_visible/{key}", visible)
        settings.sync()

    def _load_section_vis(self, key: str, default: bool = True) -> bool:
        if key in self._section_visibility_cache:
            return self._section_visibility_cache[key]
        from PyQt6.QtCore import QSettings
        val = QSettings().value(f"naming_panel/section_visible/{key}", default)
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() != "false"
        return bool(val)

    # ── Auto-grow notes ────────────────────────────────────────────────────────

    def _auto_resize_notes(self) -> None:
        doc_h = self._photo_notes.document().size().height()
        new_h = max(72, min(160, int(doc_h) + 24))
        self._photo_notes.setFixedHeight(new_h)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _set_required_label(self, label_widget: QLabel, text: str, required: bool) -> None:
        if required:
            label_widget.setText(f"{text} <span style='color:#e06a5a;'>*</span>")
        else:
            label_widget.setText(text)

    def refresh_naming_rules(self) -> None:
        """Reload per-project required-field rules and update label stars."""
        self._naming_rules_required = self._load_required_rules()
        for key, label_widget in self._field_labels.items():
            self._set_required_label(
                label_widget,
                self._field_label_text.get(key, key),
                bool(self._naming_rules_required.get(key, False)),
            )
        self._check_compliance(self.current_uid())

    def _load_required_rules(self) -> dict[str, bool]:
        try:
            db = self.ctx.get_db()
        except Exception:
            db = None
        try:
            from app.services.project_settings_service import (
                DEFAULT_NAMING_RULES,
                load_setting,
            )
            defaults = DEFAULT_NAMING_RULES["required"]
            if db is None:
                return dict(defaults)
            rules = load_setting(db, "naming_rules", DEFAULT_NAMING_RULES)
            required = rules.get("required", defaults)
            return {key: bool(required.get(key, val)) for key, val in defaults.items()}
        except Exception:
            return {
                "province": True,
                "site": True,
                "station": False,
                "species_id": True,
                "storage": True,
                "collection_date": True,
                "photo_date": True,
            }

    def _load_component_rules(self) -> list[str]:
        try:
            db = self.ctx.get_db()
        except Exception:
            db = None
        try:
            from app.services.project_settings_service import (
                DEFAULT_NAMING_RULES,
                load_setting,
            )
            defaults = DEFAULT_NAMING_RULES["components"]
            if db is None:
                return list(defaults)
            rules = load_setting(db, "naming_rules", DEFAULT_NAMING_RULES)
            components = rules.get("components", defaults)
            if not isinstance(components, list):
                return list(defaults)
            return [str(c) for c in components if c]
        except Exception:
            return ["province", "site", "station", "species_id", "storage", "date_seg"]

    def _safe_uid_segment(self, value: str) -> str:
        text = str(value or "").strip()
        text = re.sub(r"\s+", "_", text)
        return text.replace("-", "_")

    def _component_values(self, date_seg: str) -> dict[str, str]:
        values = {
            "province": self._province.text().strip(),
            "site": self._site.text().strip(),
            "station": self._station.text().strip(),
            "species_id": self._species_id.text().strip(),
            "storage": self._storage.text().strip(),
            "collection_date": self._collection_date.text().strip(),
            "photo_date": self._photo_date.text().strip(),
            "date_seg": date_seg,
        }
        values.update(self._external_naming_values)
        return values

    def _build_configured_uid(self, date_seg: str, seq: int | None = None) -> str:
        values = self._component_values(date_seg)
        parts: list[str] = []
        for key in self._load_component_rules():
            if key == "result_seq":
                continue
            raw_val = values.get(key, "")
            val = str(raw_val or "").strip() if key == "date_seg" else self._safe_uid_segment(raw_val)
            if not val:
                continue
            if seq is not None and key == "storage":
                parts.append(str(seq))
            parts.append(val)
        if seq is not None and "storage" not in self._load_component_rules():
            insert_at = min(4, len(parts))
            parts.insert(insert_at, str(seq))
        return "-".join(parts)

    def _normalize_uppercase_edit(self, edit: QLineEdit, text: str) -> None:
        upper = text.upper()
        if text == upper:
            return
        cursor = edit.cursorPosition()
        edit.blockSignals(True)
        edit.setText(upper)
        edit.setCursorPosition(min(cursor, len(upper)))
        edit.blockSignals(False)

    def _update_preview(self) -> None:
        province = self._province.text().strip()
        site = self._site.text().strip()
        station = self._station.text().strip()
        species_id = self._species_id.text().strip()
        storage = self._storage.text().strip()
        col_date = self._collection_date.text().strip()
        photo_date = self._photo_date.text().strip()
        seq = self._seq.value()

        date_seg = specimen_date_seg(col_date or None, photo_date or None)

        uid = self._build_configured_uid(date_seg)
        result_id = self._build_configured_uid(date_seg, seq=seq)

        self._set_preview(self._uid_preview, uid)
        self._set_preview(self._result_preview, result_id)

        # 保存方式说明灰字 + RNA 徽标 (web pres-detail-row)
        detail = _PRES_DETAIL.get(storage.upper(), "")
        if storage.upper().startswith("R"):
            detail = (detail + "   ✓ 已取RNA · RNAlater").strip()
        self._pres_detail.setText(detail)
        self._pres_detail.setVisible(bool(detail))

        if storage.upper().startswith("R"):
            self._storage_combo.setToolTip(
                "R 前缀表示已取 RNA；需要额外生成 RNAlater 组织管标签"
            )
        else:
            self._storage_combo.setToolTip("标本保存方式；R 前缀表示已取 RNA（RNAlater）")
        self._rna_warning.hide()

        if uid:
            self.uid_generated.emit(uid)
        if result_id:
            self.result_id_generated.emit(result_id)

        self._update_sequence_hint(species_id)
        self._check_duplicate(uid)    # #cursor findDuplicateSpecimen
        self._check_compliance(uid)   # #cursor designComplianceCheck
        self._update_edit_actions()

    def _update_edit_actions(self) -> None:
        editing = bool(self._persisted_uid)
        self._pin_btn.setText("保存" if editing else "添加")
        self._pin_btn.setToolTip(
            "保存对当前编号的修改，编号变化时迁移旧编号"
            if editing else
            "把当前 voucher number 保存并添加到左侧标本列表"
        )
        self._delete_btn.setVisible(editing)

    def _emit_delete_requested(self) -> None:
        if self._persisted_uid:
            self.delete_requested.emit(self._persisted_uid)

    def _update_sequence_hint(self, species_text: str) -> None:
        """Refresh the per-prefix next-number hint from the project DB."""
        suggested = ""
        if not species_text.strip():
            self._seq_hint_label.setText("输入物种缩写前缀后显示下一个可用编号")
            self._seq_apply_btn.setEnabled(False)
            self._seq_apply_btn.setText("填入建议")
            self._seq_apply_btn.setProperty("suggested_id", "")
            return

        db = None
        try:
            db = self.ctx.get_db()
        except Exception:
            db = None
        if not db:
            self._seq_hint_label.setText("打开项目后可检查当前前缀的下一个编号")
            self._seq_apply_btn.setEnabled(False)
            self._seq_apply_btn.setText("填入建议")
            self._seq_apply_btn.setProperty("suggested_id", "")
            return

        try:
            summary = species_sequence_summary(
                db,
                species_text,
                project_dir=getattr(self.ctx, "current_project_dir", None),
            )
        except Exception:
            self._seq_hint_label.setText("编号检查暂不可用")
            self._seq_apply_btn.setEnabled(False)
            self._seq_apply_btn.setText("填入建议")
            self._seq_apply_btn.setProperty("suggested_id", "")
            return

        if not summary.prefix or not summary.next_id:
            self._seq_hint_label.setText("请输入字母前缀，如 DLC")
            self._seq_apply_btn.setEnabled(False)
            self._seq_apply_btn.setText("填入建议")
            self._seq_apply_btn.setProperty("suggested_id", "")
            return

        suggested = summary.next_id
        if summary.max_number:
            max_id = f"{summary.prefix}{summary.max_number:0{summary.width}d}"
            text = (
                f"{summary.prefix} 已用 {len(summary.used_numbers)} 个，"
                f"最大 {max_id}，建议 {summary.next_id}"
            )
            if summary.gaps:
                shown = ", ".join(f"{n:0{summary.width}d}" for n in summary.gaps[:5])
                more = "..." if len(summary.gaps) > 5 else ""
                text += f"；缺号 {shown}{more}"
        else:
            text = f"{summary.prefix} 尚未使用，建议 {summary.next_id}"
        self._seq_hint_label.setText(text)
        self._seq_apply_btn.setText(f"填入 {suggested}")
        self._seq_apply_btn.setProperty("suggested_id", suggested)
        self._seq_apply_btn.setEnabled(True)

    def _check_duplicate(self, uid: str) -> None:
        """Check if *uid* already exists in the DB.

        Mirrors web findDuplicateSpecimen() app.js:3853.
        Shows/hides self._dup_warn accordingly.
        """
        if not uid:
            self._dup_warn.hide()
            return
        db = None
        try:
            db = self.ctx.get_db()
        except Exception:
            db = None
        if not db:
            self._dup_warn.hide()
            return
        # Check if this uid already has a specimens row
        try:
            row = db.execute(
                "SELECT owner_project_dir FROM specimens WHERE uid = ?", (uid,)
            ).fetchone()
        except Exception:
            self._dup_warn.hide()
            return
        if row:
            if self._persisted_uid and uid == self._persisted_uid:
                self._dup_warn.hide()
                return
            owner = row[0] if row[0] else "未知项目"
            from pathlib import Path as _Path
            project_name = _Path(owner).name if owner else "标本库"
            self._dup_warn.setText(
                f"⚠ 标本唯一编号重复 — 该 voucher number 已存在（项目「{project_name}」），请修改字段后再保存"
            )
            self._dup_warn.show()
        else:
            try:
                from app.services import specimen_catalog_service as catalog
                hits = catalog.conflicting_uid_hits(
                    uid,
                    current_project_dir=getattr(self.ctx, "current_project_dir", None),
                    current_project_root=getattr(self.ctx, "current_project_root", None),
                    allowed_current_uid=self._persisted_uid,
                )
            except Exception:
                hits = []
            if hits:
                self._dup_warn.setText("⚠ " + catalog.format_uid_conflict(uid, hits))
                self._dup_warn.show()
            else:
                self._dup_warn.hide()

    def _check_compliance(self, uid: str) -> None:
        """Light 7-segment format compliance check for the live preview UID.

        Mirrors web designComplianceCheck() app.js:1954.
        Shows self._compliance_warn if the UID is partially filled but
        has obvious format issues (empty required segments).
        """
        province = self._province.text().strip()
        site = self._site.text().strip()
        station = self._station.text().strip()
        species_id = self._species_id.text().strip()
        storage = self._storage.text().strip()
        col_date = self._collection_date.text().strip()
        photo_date = self._photo_date.text().strip()

        issues: list[str] = []
        required = self._load_required_rules()
        self._naming_rules_required = required
        values = {
            "province": province,
            "site": site,
            "station": station,
            "species_id": species_id,
            "storage": storage,
            "collection_date": col_date,
            "photo_date": photo_date,
        }
        labels = {
            "province": "地区",
            "site": "样地",
            "station": "站位",
            "species_id": "物种缩写",
            "storage": "保存方式",
            "collection_date": "采集日期",
            "photo_date": "拍摄日期",
        }

        # Only check when the user has started filling in fields
        any_filled = any([province, site, station, species_id, storage, col_date, photo_date])
        if not any_filled:
            self._compliance_warn.hide()
            return

        missing = [
            labels[key]
            for key, is_required in required.items()
            if is_required and not values.get(key)
        ]
        if missing:
            issues.append("缺少必填：" + "、".join(missing))

        if province and not province.isalpha():
            issues.append("地区应为字母（如 FJ）")
        if site and len(site) < 2:
            issues.append("样地代码太短")
        if col_date and len(col_date) != 8:
            issues.append("采集日期应为 8 位 YYYYMMDD")
        if storage and not any(storage.upper().startswith(c) for c in ("T", "D", "R")):
            issues.append("保存方式应以 T/D/R 开头")

        if issues:
            self._compliance_warn.setText("⚠ 格式提示：" + "；".join(issues))
            self._compliance_warn.show()
        else:
            self._compliance_warn.setText("")
            self._compliance_warn.hide()

    def _apply_sequence_suggestion(self) -> None:
        suggested = self.current_sequence_suggestion()
        if suggested:
            self._species_id.setText(suggested)
            self._species_id.setFocus()

    def _set_preview(self, label: QLabel, value: str) -> None:
        """Set preview text and swap filled/empty styling via object name."""
        filled = bool(value)
        label.setText(value if filled else "—")
        label.setObjectName("PreviewBlock" if filled else "PreviewEmpty")
        label.style().unpolish(label)
        label.style().polish(label)

    def _copy_uid(self) -> None:
        from PyQt6.QtWidgets import QApplication
        uid = self.current_uid()
        if uid:
            QApplication.clipboard().setText(uid)

    def _copy_result_id(self) -> None:
        from PyQt6.QtWidgets import QApplication
        rid = self.current_result_id()
        if rid:
            QApplication.clipboard().setText(rid)

    def _build_storage_combo(self) -> None:
        """Populate the 保存方式 dropdown with grouped preservation methods.

        QComboBox has no native <optgroup>; a QStandardItemModel carries
        non-selectable header rows + separators to mimic the web oracle's
        grouped <select> (app.js:9259-9307).
        """
        model = QStandardItemModel(self._storage_combo)

        def _add_header(text: str) -> None:
            item = QStandardItem(text)
            item.setFlags(Qt.ItemFlag.NoItemFlags)  # disabled + non-selectable
            font = item.font()
            font.setBold(True)
            item.setFont(font)
            model.appendRow(item)

        def _add_method(code: str, detail: str) -> None:
            # Oracle app.js:9268-9271 — option 文本只放 code,detail 进 tooltip;
            # 全文说明由灰字行(_pres_detail)承担。
            item = QStandardItem(code)
            item.setData(code, Qt.ItemDataRole.UserRole)
            item.setToolTip(detail)
            model.appendRow(item)

        def _add_separator() -> None:
            item = QStandardItem()
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setData("separator", Qt.ItemDataRole.AccessibleDescriptionRole)
            model.appendRow(item)

        # Row 0: placeholder / empty state (selectable → clears storage).
        placeholder = QStandardItem("选择保存方式…")
        placeholder.setData("", Qt.ItemDataRole.UserRole)
        model.appendRow(placeholder)

        _add_separator()
        _add_header("常规保存")
        for code, detail in STANDARD_PRESERVATION_METHODS:
            _add_method(code, detail)

        _add_separator()
        _add_header("已取 RNA，保存于 RNAlater")
        for code, detail in TRANSCRIPTOME_PRESERVATION_METHODS:
            _add_method(code, detail)

        _add_separator()
        custom = QStandardItem("其他… 打开项目设置")
        custom.setData(_STORAGE_SENTINEL_CUSTOM, Qt.ItemDataRole.UserRole)
        model.appendRow(custom)

        self._storage_combo.setModel(model)
        self._storage_combo.setCurrentIndex(0)

    def _on_storage_combo(self, index: int) -> None:
        """Apply the dropdown pick.  Headers/separators are non-selectable so
        ``activated`` never fires for them."""
        if self._storage_syncing:
            return
        code = self._storage_combo.itemData(index, Qt.ItemDataRole.UserRole)
        if code == _STORAGE_SENTINEL_CUSTOM:
            # Revert visible selection, then open the project settings drawer
            # (oracle app.js:9289-9294).
            self._sync_combo_to_storage(self._storage.text().strip())
            self.open_project_settings.emit()
            return
        if not code:
            # Placeholder → clear storage without triggering a UID migration.
            self._storage.setText("")
            return
        self._on_storage_btn(code)

    def _sync_combo_to_storage(self, value: str) -> None:
        """Select the dropdown row whose code == ``value`` (re-entrancy guarded).

        Falls back to the placeholder row when ``value`` isn't a listed code
        (a manually typed custom code lives only in the free-text field)."""
        if self._storage_syncing:
            return
        self._storage_syncing = True
        try:
            target = 0  # placeholder
            for row in range(self._storage_combo.count()):
                data = self._storage_combo.itemData(row, Qt.ItemDataRole.UserRole)
                if value and data == value:
                    target = row
                    break
            self._storage_combo.setCurrentIndex(target)
        finally:
            self._storage_syncing = False

    def _on_storage_btn(self, code: str) -> None:
        """Canonical entry point for setting the storage code.

        Sets the free-text storage QLineEdit to ``code`` and syncs the
        dropdown selection.  The QLineEdit.textChanged signal triggers
        _update_preview automatically.

        When an existing (saved) specimen is loaded, changing storage type
        triggers applyStorageCorrection() to migrate the UID and all references.
        Oracle: app.js:9303, applyStorageCorrection (line ~3001).
        """
        old_code = self._storage.text().strip()
        self._storage.setText(code)
        self._sync_combo_to_storage(code)

        if not self._persisted_uid or old_code == code:
            return

        from app.services.specimen_rename_service import (
            apply_storage_correction,
            specimen_has_risky_references,
        )
        from PyQt6.QtWidgets import QMessageBox
        db = None
        try:
            db = self.ctx.get_db()
        except Exception:
            db = None
        if not db:
            return

        uid = self._persisted_uid
        if specimen_has_risky_references(db, uid):
            reply = QMessageBox.warning(
                self,
                "保存方式修正",
                "保存方式改变会更新唯一编号，已有记录将迁移。确认？",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Ok:
                self._storage.setText(old_code)
                self._sync_combo_to_storage(old_code)
                return

        try:
            new_uid = apply_storage_correction(db, uid, code)
            if new_uid != uid:
                self._persisted_uid = new_uid
                self.uid_corrected.emit(uid, new_uid)
        except ValueError as exc:
            QMessageBox.critical(self, "错误", str(exc))
            self._storage.setText(old_code)
            self._sync_combo_to_storage(old_code)
