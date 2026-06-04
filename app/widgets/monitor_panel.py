"""monitor_panel.py — Incoming-JPG / results-TIFF capture stream.

Faithfully mirrors the web prototype's "目录监控 / 拍照工作台" centre column
(app.js renderDirectoryMonitor):

  ┌ batch-ident bar ─ current batch UID + activation state ───────────┐
  ├ activity stats ─ 今日新增 / 未整理 / 最近写入 ───────────────────┤
  ├ controls ─ 自动压缩 / 刷新 / 显示模式 / 添加照片 ────────────────┤
  ├ stream header ─ 刚写入目录 · 选中操作（全选/清除/删除）──────────┤
  ├ capture stream ─ gradient-preview file cards, 4-col grid ─────────┤
  └ unattributed warning ─ ⚠️ N 张 JPG 尚未归入任何编号 ─────────────┘

Each capture card carries (web parity):
  - a gradient "preview" tile (amber for JPG, green for TIFF) with a
    corner status pill (未关联 / TIFF 成片 / 已归档)
  - a mono filename + time caption
  - a colour-coded attribution label pill (attributed / unattributed)

Data source: ``monitor_service.scan_project()``.  Service wiring,
``load_scan``/``clear``, ``_stat_label`` and the three signals are kept
stable for the WorkbenchView and the test-suite.

Badge palette (mirrors web styles.css):
  raw        → teal   #29b9ab — unattributed JPG
  attributed → green  #d4edda/#155724 — attributed JPG
  composed   → blue   #4a90d9 — bound to a composed TIFF
  archived   → muted  #87a2a1 — archived in ZIP
  tiff       → green  #36c98f — TIFF result
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.config import icons
from app.services import grouping_service, activation_service

if TYPE_CHECKING:
    from app.app_context import AppContext
    from app.services.monitor_service import ScanResult


# ── Gradient preview tiles (QSS can't do radial gradients cleanly) ────────────
_JPG_PREVIEW = (
    "border:none; border-top-left-radius:12px; border-bottom-left-radius:12px;"
    "background: qradialgradient(cx:0.46, cy:0.42, radius:0.62,"
    " fx:0.46, fy:0.42, stop:0 rgba(232,170,96,0.45),"
    " stop:0.4 rgba(220,145,76,0.10), stop:1 #0a1d23);"
)
_TIFF_PREVIEW = (
    "border:none; border-top-left-radius:12px; border-bottom-left-radius:12px;"
    "background: qradialgradient(cx:0.46, cy:0.42, radius:0.62,"
    " fx:0.46, fy:0.42, stop:0 rgba(66,212,160,0.48),"
    " stop:0.4 rgba(54,201,143,0.10), stop:1 #091b20);"
)

# Corner status pill: low-saturation chip, mapped to a theme object name.
_CORNER = {
    "raw":      ("未关联",   "ChipRaw"),
    "tiff":     ("TIFF 成片", "ChipTiff"),
    "archived": ("已归档",   "ChipArchived"),
    "composed": ("已合成",   "ChipComposed"),
}
_ATTR = {
    "attributed":   "ChipAttributed",
    "active":       "ChipAttributed",
    "unattributed": "ChipUnattributed",
    "readonly":     "ChipArchived",
}


class _FileCard(QFrame):
    """A capture-stream card: gradient preview + caption + attribution pill.

    Cards support selection (toggle via _set_selected) for multi-select
    delete.  Selected cards get a highlight border via object name "CardSelected".
    """

    activate_requested = pyqtSignal(str)      # path
    deactivate_requested = pyqtSignal(str)    # path
    assign_requested = pyqtSignal(str)        # path
    selection_toggled = pyqtSignal(str, bool) # path, selected

    def __init__(self, entry, active_uid: Optional[str] = None,
                 parent: Optional[QWidget] = None,
                 on_add_to_group: Optional[Callable[[str], None]] = None,
                 on_assign_uid: Optional[Callable[[str], None]] = None,
                 on_unassign: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._entry = entry
        self._active_uid = active_uid
        self._selected = False
        self._on_add_to_group = on_add_to_group
        self._on_assign_uid = on_assign_uid
        self._on_unassign = on_unassign
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setFixedHeight(68)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        kind = getattr(self._entry, "kind", "jpg")
        uid = getattr(self._entry, "attributed_specimen_id", None)
        composed = getattr(self._entry, "composed_tiff", None)
        archived = getattr(self._entry, "archived", None)

        if kind == "tiff":
            corner_state = "tiff"
        elif composed:
            corner_state = "composed"
        elif archived:
            corner_state = "archived"
        else:
            corner_state = "raw"

        # ── Preview tile with corner chip ──
        preview = QWidget()
        preview.setFixedWidth(64)
        preview.setStyleSheet(_TIFF_PREVIEW if kind == "tiff" else _JPG_PREVIEW)
        pv_lay = QVBoxLayout(preview)
        pv_lay.setContentsMargins(6, 6, 6, 6)
        pv_lay.setSpacing(0)
        c_text, c_obj = _CORNER.get(corner_state, _CORNER["raw"])
        corner = QLabel(c_text)
        corner.setObjectName(c_obj)
        corner.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        pv_lay.addWidget(corner, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        pv_lay.addStretch()
        # central format glyph, faint, for a "thumbnail" read
        glyph = QLabel()
        glyph_name = "mdi6.image-outline" if kind == "jpg" else "mdi6.file-image-outline"
        glyph.setPixmap(icons.icon(glyph_name, color="#1f4148").pixmap(20, 20))
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pv_lay.addWidget(glyph, alignment=Qt.AlignmentFlag.AlignHCenter)
        pv_lay.addStretch()
        lay.addWidget(preview)

        # ── Caption + attribution column ──
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(12, 8, 8, 8)
        body_lay.setSpacing(5)

        name = getattr(self._entry, "name", None) or Path(getattr(self._entry, "path", "")).name
        name_lbl = QLabel(name)
        name_lbl.setObjectName("Mono")
        name_lbl.setToolTip(getattr(self._entry, "path", name))
        body_lay.addWidget(name_lbl)

        # attribution chip row
        attr_row = QHBoxLayout()
        attr_row.setContentsMargins(0, 0, 0, 0)
        attr_row.setSpacing(6)
        if kind == "jpg":
            if uid:
                attr_lbl = QLabel(uid)
                attr_lbl.setObjectName(_ATTR["active" if uid == self._active_uid else "attributed"])
            else:
                attr_lbl = QLabel("未归属")
                attr_lbl.setObjectName(_ATTR["unattributed"])
        else:
            attr_lbl = QLabel(uid or "只读")
            attr_lbl.setObjectName(_ATTR["readonly"])
        attr_lbl.setToolTip(uid or "")
        attr_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        attr_row.addWidget(attr_lbl)
        attr_row.addStretch()
        body_lay.addLayout(attr_row)
        lay.addWidget(body, stretch=1)

        # ── Action column (JPG only) — vector icon buttons ──
        if kind == "jpg":
            act_col = QVBoxLayout()
            act_col.setContentsMargins(0, 8, 10, 8)
            act_col.setSpacing(5)
            act_btn = QPushButton("归属")
            act_btn.setObjectName("Tiny")
            act_btn.setFixedHeight(24)
            icons.set_button_icon(act_btn, "mdi6.link-variant", color=icons.TONE_ACCENT, size=13)
            act_btn.setToolTip("手动归属到当前激活标本")
            act_btn.clicked.connect(lambda: self.assign_requested.emit(getattr(self._entry, "path", "")))
            act_col.addWidget(act_btn)
            deact_btn = QPushButton("撤销")
            deact_btn.setObjectName("Tiny")
            deact_btn.setFixedHeight(24)
            icons.set_button_icon(deact_btn, "mdi6.link-variant-off", color=icons.TONE_MUTED, size=13)
            deact_btn.setToolTip("撤销此 JPG 的归属（变回无主）")
            deact_btn.clicked.connect(lambda: self.deactivate_requested.emit(getattr(self._entry, "path", "")))
            act_col.addWidget(deact_btn)
            lay.addLayout(act_col)

        from app.config.effects import apply_card_shadow
        apply_card_shadow(self, blur=16, y=3, alpha=55)

    def mousePressEvent(self, event) -> None:
        """Toggle selection on left-click."""
        super().mousePressEvent(event)
        self._selected = not self._selected
        self._update_selection_style()
        self.selection_toggled.emit(getattr(self._entry, "path", ""), self._selected)

    def _update_selection_style(self) -> None:
        self.setObjectName("CardSelected" if self._selected else "Card")
        self.style().unpolish(self)
        self.style().polish(self)

    def is_selected(self) -> bool:
        return self._selected

    def set_selected(self, val: bool) -> None:
        self._selected = val
        self._update_selection_style()

    def contextMenuEvent(self, event) -> None:
        if getattr(self._entry, "kind", "") == "jpg":
            self._on_jpg_context_menu(event.pos())

    def _on_jpg_context_menu(self, pos) -> None:
        jpg_path = getattr(self._entry, "path", "")
        menu = QMenu(self)

        action = menu.addAction("复制路径")
        action.triggered.connect(lambda: QApplication.clipboard().setText(jpg_path))

        menu.addSeparator()

        add_action = menu.addAction("加入当前分组")
        if self._on_add_to_group is not None:
            add_action.triggered.connect(lambda: self._on_add_to_group(jpg_path))
        else:
            add_action.setEnabled(False)

        assign_action = menu.addAction("指定归属标本")
        if self._on_assign_uid is not None:
            def _do_assign_uid():
                uid, ok = QInputDialog.getText(self, "指定标本", "输入标本编号：")
                if ok and uid.strip():
                    self._on_assign_uid(jpg_path, uid.strip())
            assign_action.triggered.connect(_do_assign_uid)
        else:
            assign_action.setEnabled(False)

        unassign_action = menu.addAction("取消归属")
        if self._on_unassign is not None:
            unassign_action.triggered.connect(lambda: self._on_unassign(jpg_path))
        else:
            unassign_action.setEnabled(False)

        menu.exec(self.mapToGlobal(pos))


class MonitorPanel(QWidget):
    """Incoming-JPG + results-TIFF capture stream with batch identity header.

    Signals
    -------
    assign_requested(path)   manual attribution to active specimen
    unassign_requested(path) remove attribution (P0 blacklist)
    refresh_requested()      rescan request
    """

    assign_requested = pyqtSignal(str)
    unassign_requested = pyqtSignal(str)
    refresh_requested = pyqtSignal()
    add_jpg_requested = pyqtSignal()   # emitted when user clicks "添加照片"

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._scan_result: Optional["ScanResult"] = None
        self._active_uid: Optional[str] = None
        self._cards: list[_FileCard] = []  # all current cards (for selection ops)
        self._hide_archived: bool = False
        self._setup_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        section = QFrame()
        section.setObjectName("WorkbenchSection")
        sec = QVBoxLayout(section)
        sec.setContentsMargins(20, 16, 20, 16)
        sec.setSpacing(12)
        root.addWidget(section)
        from app.config.effects import apply_card_shadow
        apply_card_shadow(section)

        # ── Batch-ident bar ──
        batch = QFrame()
        batch.setObjectName("BatchIdentBar")
        b_lay = QHBoxLayout(batch)
        b_lay.setContentsMargins(14, 10, 14, 10)
        b_lay.setSpacing(10)
        b_title = QLabel("当前照片批次")
        b_title.setObjectName("Section")
        b_lay.addWidget(b_title)
        self._batch_uid = QLabel("—")
        self._batch_uid.setObjectName("BatchUid")
        b_lay.addWidget(self._batch_uid)
        b_lay.addStretch()
        self._activate_state = QLabel("未激活")
        self._activate_state.setObjectName("ActivateState")
        b_lay.addWidget(self._activate_state)
        sec.addWidget(batch)

        # ── Phase pills: 拍摄中 / 已拍完 / 整理中 / 完成 ──
        phase_row = QHBoxLayout()
        phase_row.setContentsMargins(2, 0, 2, 0)
        phase_row.setSpacing(6)
        _phase_label = QLabel("阶段：")
        _phase_label.setObjectName("MutedSmall")
        phase_row.addWidget(_phase_label)
        self._phase_pills: dict[str, QPushButton] = {}
        for phase, obj in (
            ("拍摄中", "PhasePillActive"),
            ("已拍完", "PhasePill"),
            ("整理中", "PhasePill"),
            ("完成",   "PhasePill"),
        ):
            btn = QPushButton(phase)
            btn.setObjectName(obj)
            btn.setCheckable(True)
            btn.setFixedHeight(22)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if phase == "拍摄中":
                btn.setChecked(True)
            phase_row.addWidget(btn)
            self._phase_pills[phase] = btn
        phase_row.addStretch()
        sec.addLayout(phase_row)

        # ── Compose-mode hints ──
        hints_row = QHBoxLayout()
        hints_row.setContentsMargins(2, 0, 2, 0)
        hints_row.setSpacing(16)
        hint_auto = QLabel("自动合成 提交后后台执行")
        hint_auto.setObjectName("MutedSmall")
        hints_row.addWidget(hint_auto)
        hint_manual = QLabel("手动 Helicon 选中本组原片拖入外部软件")
        hint_manual.setObjectName("MutedSmall")
        hints_row.addWidget(hint_manual)
        hints_row.addStretch()
        sec.addLayout(hints_row)

        # ── Activity stats ──
        stats = QHBoxLayout()
        stats.setContentsMargins(2, 0, 2, 0)
        stats.setSpacing(28)
        self._stat_today = self._stat_block(stats, "0 张", "今日新增")
        self._stat_untidy = self._stat_block(stats, "0 张", "未整理")
        self._stat_recent = self._stat_block(stats, "刚刚", "最近写入")
        stats.addStretch()
        # Keep legacy compact stat label for tests / status text
        self._stat_label = QLabel("无项目")
        self._stat_label.setObjectName("MutedSmall")
        stats.addWidget(self._stat_label, alignment=Qt.AlignmentFlag.AlignBottom)
        sec.addLayout(stats)

        # ── Controls row ──
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(8)
        settings_btn = QPushButton("⚙ 项目设置")
        settings_btn.setObjectName("Ghost")
        settings_btn.setFixedHeight(26)
        settings_btn.setToolTip("打开项目设置")
        controls.addWidget(settings_btn)
        self._auto_toggle = QPushButton("新 TIFF 自动压缩")
        self._auto_toggle.setObjectName("Outline")
        self._auto_toggle.setCheckable(True)
        icons.set_button_icon(self._auto_toggle, "mdi6.checkbox-blank-outline",
                              color=icons.TONE_MUTED, size=15)
        self._auto_toggle.toggled.connect(self._on_auto_toggled)
        controls.addWidget(self._auto_toggle)
        refresh_btn = QPushButton("↻ 刷新目录")
        refresh_btn.setObjectName("Outline")
        icons.set_button_icon(refresh_btn, "mdi6.refresh", color=icons.TONE_MUTED, size=15)
        refresh_btn.clicked.connect(self._on_refresh)
        controls.addWidget(refresh_btn)
        add_btn = QPushButton("+ 添加照片")
        add_btn.setObjectName("Outline")
        icons.set_button_icon(add_btn, "mdi6.image-plus-outline", color=icons.TONE_MUTED, size=15)
        add_btn.setToolTip("把已有照片添加进来（导入 incoming-jpg/）")
        add_btn.clicked.connect(self.add_jpg_requested.emit)
        controls.addWidget(add_btn)
        dir_btn = QPushButton("📁 选目录")
        dir_btn.setObjectName("Ghost")
        dir_btn.setFixedHeight(26)
        dir_btn.setToolTip("选择工作目录")
        controls.addWidget(dir_btn)
        self._raw_count = QLabel("本组原片 0 张")
        self._raw_count.setObjectName("MutedSmall")
        controls.addWidget(self._raw_count)
        controls.addStretch()
        sec.addLayout(controls)

        sec.addWidget(_divider())

        # ── Stream header + selection bar ──
        sh = QHBoxLayout()
        sh.setContentsMargins(0, 0, 0, 0)
        sh.setSpacing(8)
        sh_title = QLabel("刚写入目录")
        sh_title.setObjectName("Section")
        sh.addWidget(sh_title)
        sh_hint = QLabel("单击可选中")
        sh_hint.setObjectName("MutedSmall")
        sh.addWidget(sh_hint)
        sh.addStretch()
        self._hide_archived_cb = QCheckBox("隐藏已归档")
        self._hide_archived_cb.toggled.connect(self._on_hide_archived_toggled)
        sh.addWidget(self._hide_archived_cb)
        self._sel_count = QLabel("未选中")
        self._sel_count.setObjectName("MutedSmall")
        sh.addWidget(self._sel_count)
        sel_all_btn = QPushButton("全选")
        sel_all_btn.setObjectName("Tiny")
        sel_all_btn.setFixedHeight(22)
        sel_all_btn.clicked.connect(self._on_select_all)
        sh.addWidget(sel_all_btn)
        sel_none_btn = QPushButton("清除")
        sel_none_btn.setObjectName("Tiny")
        sel_none_btn.setFixedHeight(22)
        sel_none_btn.clicked.connect(self._on_select_none)
        sh.addWidget(sel_none_btn)
        self._del_btn = QPushButton("🗑 删除")
        self._del_btn.setObjectName("Danger")
        self._del_btn.setFixedHeight(24)
        self._del_btn.setEnabled(False)
        icons.set_button_icon(self._del_btn, "mdi6.delete-outline", color=icons.TONE_DANGER, size=14)
        self._del_btn.setToolTip("删除选中文件（含 TIFF 时二次确认）")
        self._del_btn.clicked.connect(self._on_delete_clicked)
        sh.addWidget(self._del_btn)
        # Also add ↩ 撤销归属 from spec
        undo_attr_btn = QPushButton("↩ 撤销归属")
        undo_attr_btn.setObjectName("Tiny")
        undo_attr_btn.setFixedHeight(22)
        undo_attr_btn.setToolTip("撤销选中 JPG 的归属")
        sh.addWidget(undo_attr_btn)
        sec.addLayout(sh)

        # ── Capture stream (scrollable grid) ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._grid_widget = QWidget()
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setContentsMargins(0, 4, 0, 4)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(8)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._grid_widget)
        sec.addWidget(scroll, stretch=1)

        # ── Empty state ──
        self._empty_label = QLabel(
            "等待目录中新照片 — 已处理文件不再留在未整理区；TIFF 出现前不会关联原片。"
        )
        self._empty_label.setObjectName("Muted")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        self._empty_label.hide()
        sec.addWidget(self._empty_label)

        # ── Unattributed warning ──
        self._unattr_warning = QLabel("")
        self._unattr_warning.setObjectName("UnattributedWarning")
        self._unattr_warning.hide()
        sec.addWidget(self._unattr_warning)

    def _stat_block(self, layout: QHBoxLayout, value: str, label: str):
        col = QVBoxLayout()
        col.setSpacing(1)
        v = QLabel(value)
        v.setObjectName("StatValue")
        l = QLabel(label)
        l.setObjectName("StatLabel")
        col.addWidget(v)
        col.addWidget(l)
        layout.addLayout(col)
        return v

    # ── Public API ────────────────────────────────────────────────────────────

    def set_batch(self, uid: Optional[str], active_uid: Optional[str] = None,
                  activated_at: Optional[str] = None) -> None:
        """Set the current-batch UID + activation state shown in the header."""
        self._active_uid = active_uid
        self._batch_uid.setText(uid or "—")
        if active_uid:
            txt = f"激活：{active_uid}"
            if activated_at:
                txt += f" · 自 {str(activated_at)[11:19]}"
            self._activate_state.setText(txt)
            self._activate_state.setObjectName("ActivateStateOn")
        else:
            self._activate_state.setText("未激活")
            self._activate_state.setObjectName("ActivateState")
        # re-polish to apply object-name-driven style
        self._activate_state.style().unpolish(self._activate_state)
        self._activate_state.style().polish(self._activate_state)

    def load_scan(self, scan_result: "ScanResult") -> None:
        """Populate the grid from a completed scan result."""
        self._scan_result = scan_result
        self._rebuild_grid()

    def clear(self) -> None:
        """Remove all cards and show empty state."""
        self._scan_result = None
        self._cards = []
        self._clear_grid()
        self._stat_label.setText("无项目")
        self._stat_today.setText("0 张")
        self._stat_untidy.setText("0 张")
        self._raw_count.setText("本组原片 0 张")
        self._del_btn.setEnabled(False)
        self._sel_count.setText("未选中")
        self._unattr_warning.hide()
        self._empty_label.show()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _rebuild_grid(self) -> None:
        self._clear_grid()
        self._cards = []
        if not self._scan_result:
            self._empty_label.show()
            return

        jpgs = list(self._scan_result.jpg_files)
        tiffs = list(self._scan_result.tiff_files)
        all_files = jpgs + tiffs

        if not all_files:
            self._empty_label.show()
            jpg_c = tiff_c = 0
        else:
            self._empty_label.hide()
            jpg_c, tiff_c = len(jpgs), len(tiffs)
            cols = 2
            for idx, entry in enumerate(all_files):
                card = _FileCard(
                    entry, self._active_uid, self,
                    on_add_to_group=self._on_ctx_add_to_group,
                    on_assign_uid=self._on_ctx_assign_uid,
                    on_unassign=self._on_ctx_unassign,
                )
                card.assign_requested.connect(self.assign_requested)
                card.deactivate_requested.connect(self.unassign_requested)
                card.selection_toggled.connect(self._on_card_selection_toggled)
                self._grid.addWidget(card, idx // cols, idx % cols)
                self._cards.append(card)
            for c in range(cols):
                self._grid.setColumnStretch(c, 1)

        self._stat_label.setText(f"JPG {jpg_c} · TIFF {tiff_c}")
        self._stat_today.setText(f"{jpg_c} 张")
        self._stat_untidy.setText(f"{len(all_files)} 张")
        self._raw_count.setText(f"本组原片 {jpg_c} 张")

        self._apply_filter()

        # Unattributed warning
        unattr = [
            f for f in jpgs
            if not getattr(f, "attributed_specimen_id", None)
            and not getattr(f, "composed_tiff", None)
        ]
        if unattr:
            self._unattr_warning.setText(f"{len(unattr)} 张 JPG 尚未归入任何编号")
            self._unattr_warning.show()
        else:
            self._unattr_warning.hide()

    def _clear_grid(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

    def _on_hide_archived_toggled(self, checked: bool) -> None:
        self._hide_archived = checked
        self._apply_filter()

    def _apply_filter(self) -> None:
        """Show/hide cards based on _hide_archived state."""
        for card in self._cards:
            is_grouped = getattr(card._entry, "is_grouped", False)
            if self._hide_archived and is_grouped:
                card.hide()
            else:
                card.show()

    # ── Selection helpers ─────────────────────────────────────────────────────

    def _selected_cards(self) -> list[_FileCard]:
        return [c for c in self._cards if c.is_selected()]

    def _on_card_selection_toggled(self, path: str, selected: bool) -> None:
        """Update selection count label and delete button state."""
        sel = self._selected_cards()
        n = len(sel)
        if n:
            self._sel_count.setText(f"已选 {n} 个")
            self._del_btn.setEnabled(True)
        else:
            self._sel_count.setText("未选中")
            self._del_btn.setEnabled(False)

    def _on_select_all(self) -> None:
        for card in self._cards:
            card.set_selected(True)
        n = len(self._cards)
        self._sel_count.setText(f"已选 {n} 个" if n else "未选中")
        self._del_btn.setEnabled(bool(self._cards))

    def _on_select_none(self) -> None:
        for card in self._cards:
            card.set_selected(False)
        self._sel_count.setText("未选中")
        self._del_btn.setEnabled(False)

    def selected_jpg_paths(self) -> list[str]:
        """Return absolute paths of all currently selected JPG cards."""
        return [
            c._entry.path
            for c in self._selected_cards()
            if getattr(c._entry, "kind", "") == "jpg" and getattr(c._entry, "path", "")
        ]

    def _on_delete_clicked(self) -> None:
        """Delete selected JPG files.

        Hard rule: TIFF 永远保留 — if any TIFF is selected, show a warning
        and abort entirely (user must deselect TIFFs first).
        JPG-only selections go through a confirm dialog then os.unlink.

        Oracle: app.js deleteSelectedFiles() — TIFF guard + os.unlink for JPGs.
        """
        sel = self._selected_cards()
        if not sel:
            return

        paths = [getattr(c._entry, "path", "") for c in sel]
        tiff_paths = [p for p in paths if p.lower().endswith((".tif", ".tiff"))]
        jpg_paths  = [p for p in paths if p and not p.lower().endswith((".tif", ".tiff"))]

        # Hard rule: TIFF 永远保留 — abort with warning
        if tiff_paths:
            QMessageBox.warning(
                self, "无法删除 TIFF",
                f"选中包含 {len(tiff_paths)} 个 TIFF 成片。\n"
                "TIFF 永远保留，只有 JPG 原片可以删除。\n"
                "请取消选择 TIFF 后再操作。"
            )
            return

        if not jpg_paths:
            QMessageBox.information(self, "删除", "请先选中要删除的 JPG。")
            return

        reply = QMessageBox.question(
            self, "确认删除",
            f"确认删除 {len(jpg_paths)} 张 JPG？\n"
            "此操作直接从磁盘删除原片，不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        import os as _os
        errors: list[str] = []
        deleted: list[str] = []
        for p in jpg_paths:
            try:
                if _os.path.isfile(p):
                    _os.unlink(p)
                    deleted.append(p)
            except OSError as exc:
                errors.append(f"{_os.path.basename(p)}: {exc}")

        if errors:
            QMessageBox.warning(self, "删除部分失败", "\n".join(errors[:5]))

        if deleted:
            self._on_select_none()
            self.refresh_requested.emit()

    # ── Context menu handlers ─────────────────────────────────────────────────

    def _on_ctx_add_to_group(self, jpg_path: str) -> None:
        db = self.ctx.get_db()
        if db is None:
            return
        active_uid = activation_service.get_active_uid(db)
        if not active_uid:
            QMessageBox.warning(self, "无激活标本", "请先激活一个标本")
            return
        self._add_jpg_to_grouping(db, active_uid, jpg_path)

    def _on_ctx_assign_uid(self, jpg_path: str, uid: str) -> None:
        db = self.ctx.get_db()
        if db is None:
            return
        self._add_jpg_to_grouping(db, uid, jpg_path)

    def _add_jpg_to_grouping(self, db, uid: str, jpg_path: str) -> None:
        """Add jpg_path to first group of uid's grouping, creating one if needed."""
        sg = grouping_service.load_grouping(db, uid)
        if sg.groups:
            if jpg_path not in sg.groups[0].jpg_paths:
                sg.groups[0].jpg_paths.append(jpg_path)
        else:
            new_group = grouping_service.Group(group_index=0, jpg_paths=[jpg_path])
            sg.groups = [new_group]
        grouping_service.save_grouping(db, uid, sg.groups, clean_phantoms=False)

    def _on_ctx_unassign(self, jpg_path: str) -> None:
        db = self.ctx.get_db()
        if db is None:
            return
        grouping_service._ensure_grouping_table(db)
        rows = db.execute(
            "SELECT uid FROM grouping WHERE jpg_paths LIKE ?",
            (f'%{jpg_path}%',),
        ).fetchall()
        uids = {row[0] for row in rows}
        for uid in uids:
            sg = grouping_service.load_grouping(db, uid)
            changed = False
            for g in sg.groups:
                if jpg_path in g.jpg_paths:
                    g.jpg_paths = [p for p in g.jpg_paths if p != jpg_path]
                    changed = True
            if changed:
                grouping_service.save_grouping(db, uid, sg.groups, clean_phantoms=False)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_refresh(self) -> None:
        self.refresh_requested.emit()

    def _on_auto_toggled(self, on: bool) -> None:
        """Swap the auto-compress toggle glyph between checked / unchecked."""
        glyph = "mdi6.checkbox-marked" if on else "mdi6.checkbox-blank-outline"
        tone = icons.TONE_ACCENT if on else icons.TONE_MUTED
        icons.set_button_icon(self._auto_toggle, glyph, color=tone, size=15)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFixedHeight(1)
    return line
