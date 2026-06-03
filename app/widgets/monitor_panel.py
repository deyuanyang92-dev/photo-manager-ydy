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
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.config import icons

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
    """A capture-stream card: gradient preview + caption + attribution pill."""

    activate_requested = pyqtSignal(str)      # path
    deactivate_requested = pyqtSignal(str)    # path
    assign_requested = pyqtSignal(str)        # path

    def __init__(self, entry, active_uid: Optional[str] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._entry = entry
        self._active_uid = active_uid
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

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._scan_result: Optional["ScanResult"] = None
        self._active_uid: Optional[str] = None
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
        self._auto_toggle = QPushButton("新 TIFF 自动压缩")
        self._auto_toggle.setObjectName("Outline")
        self._auto_toggle.setCheckable(True)
        icons.set_button_icon(self._auto_toggle, "mdi6.checkbox-blank-outline",
                              color=icons.TONE_MUTED, size=15)
        self._auto_toggle.toggled.connect(self._on_auto_toggled)
        controls.addWidget(self._auto_toggle)
        refresh_btn = QPushButton("刷新目录")
        refresh_btn.setObjectName("Outline")
        icons.set_button_icon(refresh_btn, "mdi6.refresh", color=icons.TONE_MUTED, size=15)
        refresh_btn.clicked.connect(self._on_refresh)
        controls.addWidget(refresh_btn)
        add_btn = QPushButton("添加照片")
        add_btn.setObjectName("Outline")
        icons.set_button_icon(add_btn, "mdi6.image-plus-outline", color=icons.TONE_MUTED, size=15)
        add_btn.setToolTip("把已有照片添加进来（导入 incoming-jpg/）")
        controls.addWidget(add_btn)
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
        self._sel_count = QLabel("未选中")
        self._sel_count.setObjectName("MutedSmall")
        sh.addWidget(self._sel_count)
        for label, obj in (("全选", "Tiny"), ("清除", "Tiny")):
            btn = QPushButton(label)
            btn.setObjectName(obj)
            btn.setFixedHeight(22)
            sh.addWidget(btn)
        del_btn = QPushButton("删除")
        del_btn.setObjectName("Danger")
        del_btn.setFixedHeight(24)
        del_btn.setEnabled(False)
        icons.set_button_icon(del_btn, "mdi6.delete-outline", color=icons.TONE_DANGER, size=14)
        del_btn.setToolTip("删除选中的 JPG（TIFF 成片不可删）")
        sh.addWidget(del_btn)
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
        self._clear_grid()
        self._stat_label.setText("无项目")
        self._stat_today.setText("0 张")
        self._stat_untidy.setText("0 张")
        self._raw_count.setText("本组原片 0 张")
        self._unattr_warning.hide()
        self._empty_label.show()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _rebuild_grid(self) -> None:
        self._clear_grid()
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
                card = _FileCard(entry, self._active_uid, self)
                card.assign_requested.connect(self.assign_requested)
                card.deactivate_requested.connect(self.unassign_requested)
                self._grid.addWidget(card, idx // cols, idx % cols)
            for c in range(cols):
                self._grid.setColumnStretch(c, 1)

        self._stat_label.setText(f"JPG {jpg_c} · TIFF {tiff_c}")
        self._stat_today.setText(f"{jpg_c} 张")
        self._stat_untidy.setText(f"{len(all_files)} 张")
        self._raw_count.setText(f"本组原片 {jpg_c} 张")

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
