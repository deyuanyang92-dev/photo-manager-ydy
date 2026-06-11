"""monitor_panel.py — Incoming-JPG / results-TIFF capture stream.

Faithfully mirrors the web prototype's "目录监控 / 拍照工作台" centre column
(app.js renderDirectoryMonitor):

  ┌ batch/status strip ─ UID + phase + compact JPG/TIFF stats ────────┐
  ├ primary toolbar ─ 刷新 / 添加照片 / 分组 / 自动压缩 / 更多 ──────┤
  ├ stream header ─ 待处理照片 · 右键处理文件 ───────────────────────┤
  ├ contextual selection bar ─ appears only after selecting files ─────┤
  ├ capture stream ─ compact file cards with row/right-click menus ────┤
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
    delete_requested = pyqtSignal(str)        # path

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
        self.setFixedHeight(60)
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
        preview.setFixedWidth(56)
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

        # ── Row actions: keep the file row clean; actions live in this menu
        # and the right-click menu.
        menu_btn = QPushButton()
        menu_btn.setObjectName("Ghost")
        menu_btn.setFixedSize(28, 28)
        menu_btn.setToolTip("文件操作")
        icons.set_button_icon(menu_btn, "mdi6.dots-vertical",
                              color=icons.TONE_MUTED, size=15)
        menu_btn.clicked.connect(
            lambda: self._show_context_menu(menu_btn.mapToGlobal(menu_btn.rect().bottomLeft()))
        )
        lay.addWidget(menu_btn)

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
        self._show_context_menu(event.globalPos())

    def _on_jpg_context_menu(self, pos) -> None:
        self._show_context_menu(self.mapToGlobal(pos))

    def _show_context_menu(self, global_pos) -> None:
        path = getattr(self._entry, "path", "")
        kind = getattr(self._entry, "kind", "")
        menu = QMenu(self)

        action = menu.addAction("复制路径")
        action.triggered.connect(lambda: QApplication.clipboard().setText(path))

        show_action = menu.addAction("在文件夹中显示")
        show_action.triggered.connect(lambda: self._show_in_folder(path))

        if kind == "jpg":
            menu.addSeparator()

            active_action = menu.addAction("归属到激活标本")
            active_action.triggered.connect(lambda: self.assign_requested.emit(path))

            add_action = menu.addAction("加入当前分组")
            if self._on_add_to_group is not None:
                add_action.triggered.connect(lambda: self._on_add_to_group(path))
            else:
                add_action.setEnabled(False)

            assign_action = menu.addAction("指定归属标本")
            if self._on_assign_uid is not None:
                def _do_assign_uid():
                    uid, ok = QInputDialog.getText(self, "指定标本", "输入标本编号：")
                    if ok and uid.strip():
                        self._on_assign_uid(path, uid.strip())
                assign_action.triggered.connect(_do_assign_uid)
            else:
                assign_action.setEnabled(False)

            unassign_action = menu.addAction("取消归属")
            if self._on_unassign is not None:
                unassign_action.triggered.connect(lambda: self._on_unassign(path))
            else:
                unassign_action.setEnabled(False)

        # 删除此文件：JPG 和 TIFF 都给（TIFF 删除带确认框，见 _delete_paths）。
        if kind in ("jpg", "tiff"):
            menu.addSeparator()
            delete_action = menu.addAction("删除此文件")
            delete_action.triggered.connect(lambda: self.delete_requested.emit(path))

        menu.exec(global_pos)

    def _show_in_folder(self, path: str) -> None:
        import os
        import subprocess
        import sys
        if not path:
            return
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                try:
                    from app.utils.path_utils import wsl_to_windows
                    win_path = wsl_to_windows(path) or path
                    subprocess.Popen(["explorer.exe", "/select,", win_path])
                except Exception:
                    subprocess.Popen(["xdg-open", str(Path(path).parent)])
        except Exception:
            pass


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
    grouping_requested = pyqtSignal()  # emitted when user clicks "分组工具" (opens popup)
    settings_requested = pyqtSignal()  # emitted from the compact "更多" menu
    phase_clicked = pyqtSignal(str)    # status code: shooting/shot_done/organizing/done

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._scan_result: Optional["ScanResult"] = None
        self._active_uid: Optional[str] = None
        self._current_phase: Optional[str] = None
        self._last_scan_sig = None  # change-detection: skip rebuild when unchanged
        self._cards: list[_FileCard] = []  # all current cards (for selection ops)
        # Incremental rebuild: reuse card widgets across scans keyed by file
        # path, so a single new photo builds one card instead of rebuilding all.
        self._card_by_key: dict[str, _FileCard] = {}
        self._card_sig_by_key: dict[str, tuple] = {}
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
        sec.setContentsMargins(18, 16, 18, 16)
        sec.setSpacing(11)
        root.addWidget(section)
        from app.config.effects import apply_card_shadow
        apply_card_shadow(section)

        # ── Compact batch/status strip ──
        batch = QFrame()
        batch.setObjectName("BatchIdentBar")
        b_lay = QHBoxLayout(batch)
        b_lay.setContentsMargins(12, 8, 12, 8)
        b_lay.setSpacing(8)
        b_title = QLabel("批次")
        b_title.setObjectName("Section")
        b_lay.addWidget(b_title)
        self._batch_uid = QLabel("—")
        self._batch_uid.setObjectName("BatchUid")
        b_lay.addWidget(self._batch_uid)
        self._activate_state = QLabel("未激活")
        self._activate_state.setObjectName("ActivateState")
        b_lay.addWidget(self._activate_state)

        # Oracle app.js:8368-8383 — click → collabUpdateTaskStatus(uid, code).
        # checked is driven only by set_phase() (confirmed status), never by
        # the click itself.  Pills stay enabled even without an active
        # specimen: clicking then yields a status-bar hint (零反馈=bug).
        self._phase_pills: dict[str, QPushButton] = {}
        for label, code in (
            ("拍摄中", "shooting"),
            ("已拍完", "shot_done"),
            ("整理中", "organizing"),
            ("完成",   "done"),
        ):
            btn = QPushButton(label)
            btn.setObjectName("PhasePill")
            btn.setCheckable(True)
            btn.setFixedHeight(22)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, c=code: self._on_phase_pill_clicked(c))
            b_lay.addWidget(btn)
            self._phase_pills[code] = btn

        b_lay.addStretch()
        self._stat_today = QLabel("JPG 0")
        self._stat_today.setObjectName("ChipArchived")
        b_lay.addWidget(self._stat_today)
        self._stat_recent = QLabel("TIFF 0")
        self._stat_recent.setObjectName("ChipTiff")
        b_lay.addWidget(self._stat_recent)
        self._stat_untidy = QLabel("未整理 0")
        self._stat_untidy.setObjectName("ChipRaw")
        b_lay.addWidget(self._stat_untidy)
        sec.addWidget(batch)

        # Keep legacy compact stat label for tests / status text.
        self._stat_label = QLabel("无项目")
        self._stat_label.setObjectName("MutedSmall")
        self._stat_label.hide()

        # ── Primary toolbar: only the daily path stays visible ──
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(8)
        title = QLabel("拍摄队列")
        title.setObjectName("WorkspaceTitle")
        controls.addWidget(title)
        controls.addStretch()
        refresh_btn = QPushButton("刷新")
        refresh_btn.setObjectName("Outline")
        refresh_btn.setFixedHeight(28)
        icons.set_button_icon(refresh_btn, "mdi6.refresh", color=icons.TONE_MUTED, size=15)
        refresh_btn.clicked.connect(self._on_refresh)
        controls.addWidget(refresh_btn)
        add_btn = QPushButton("添加照片")
        add_btn.setObjectName("Outline")
        add_btn.setFixedHeight(28)
        icons.set_button_icon(add_btn, "mdi6.image-plus-outline", color=icons.TONE_MUTED, size=15)
        add_btn.setToolTip("把已有照片添加进来（导入 incoming-jpg/）")
        add_btn.clicked.connect(self.add_jpg_requested.emit)
        controls.addWidget(add_btn)
        self._grouping_btn = QPushButton("分组")
        self._grouping_btn.setObjectName("Primary")
        self._grouping_btn.setFixedHeight(28)
        icons.set_button_icon(self._grouping_btn, "mdi6.layers-triple-outline",
                              color=icons.TONE_ON_ACCENT, size=15)
        self._grouping_btn.setToolTip("打开分组 / 合成工具")
        self._grouping_btn.clicked.connect(self.grouping_requested.emit)
        controls.addWidget(self._grouping_btn)
        self._auto_toggle = QPushButton("自动压缩")
        self._auto_toggle.setObjectName("Ghost")
        self._auto_toggle.setFixedHeight(28)
        self._auto_toggle.setCheckable(True)
        self._auto_toggle.setToolTip("新 TIFF 写入后自动压缩")
        icons.set_button_icon(self._auto_toggle, "mdi6.checkbox-blank-outline",
                              color=icons.TONE_MUTED, size=15)
        self._auto_toggle.toggled.connect(self._on_auto_toggled)
        controls.addWidget(self._auto_toggle)
        more_btn = QPushButton()
        more_btn.setObjectName("Ghost")
        more_btn.setFixedSize(28, 28)
        more_btn.setToolTip("更多")
        icons.set_button_icon(more_btn, "mdi6.dots-horizontal",
                              color=icons.TONE_MUTED, size=16)
        more_btn.clicked.connect(
            lambda: self._open_more_menu(more_btn.mapToGlobal(more_btn.rect().bottomLeft()))
        )
        controls.addWidget(more_btn)
        self._raw_count = QLabel("本组原片 0 张")
        self._raw_count.setObjectName("MutedSmall")
        self._raw_count.hide()
        sec.addLayout(controls)

        sec.addWidget(_divider())

        # Hidden real checkbox kept for state + tests; the visible control is in
        # the "更多" menu.
        self._hide_archived_cb = QCheckBox("隐藏已分组原片", self)
        self._hide_archived_cb.toggled.connect(self._on_hide_archived_toggled)
        self._hide_archived_cb.hide()

        # ── Stream header ──
        sh = QHBoxLayout()
        sh.setContentsMargins(0, 0, 0, 0)
        sh.setSpacing(8)
        sh_title = QLabel("待处理照片")
        sh_title.setObjectName("Section")
        sh.addWidget(sh_title)
        sh_hint = QLabel("单击选择 · 右键处理文件")
        sh_hint.setObjectName("MutedSmall")
        sh.addWidget(sh_hint)
        sh.addStretch()
        self._sel_count = QLabel("")
        self._sel_count.setObjectName("MutedSmall")
        sh.addWidget(self._sel_count)
        sec.addLayout(sh)

        # ── Contextual selection bar: hidden until one or more files selected ──
        self._selection_bar = QFrame()
        self._selection_bar.setObjectName("Panel")
        sel = QHBoxLayout(self._selection_bar)
        sel.setContentsMargins(10, 6, 10, 6)
        sel.setSpacing(8)
        self._selection_count = QLabel("已选 0 个")
        self._selection_count.setObjectName("MutedSmall")
        sel.addWidget(self._selection_count)
        add_group_btn = QPushButton("加入分组")
        add_group_btn.setObjectName("Tiny")
        add_group_btn.setFixedHeight(22)
        add_group_btn.clicked.connect(self._on_selected_add_to_group)
        sel.addWidget(add_group_btn)
        sel_all_btn = QPushButton("全选")
        sel_all_btn.setObjectName("Tiny")
        sel_all_btn.setFixedHeight(22)
        sel_all_btn.clicked.connect(self._on_select_all)
        sel.addWidget(sel_all_btn)
        sel_none_btn = QPushButton("清除")
        sel_none_btn.setObjectName("Tiny")
        sel_none_btn.setFixedHeight(22)
        sel_none_btn.clicked.connect(self._on_select_none)
        sel.addWidget(sel_none_btn)
        self._del_btn = QPushButton("删除")
        self._del_btn.setObjectName("Danger")
        self._del_btn.setFixedHeight(24)
        self._del_btn.setEnabled(False)
        icons.set_button_icon(self._del_btn, "mdi6.delete-outline", color=icons.TONE_DANGER, size=14)
        self._del_btn.setToolTip("删除选中文件（含 TIFF 时二次确认）")
        self._del_btn.clicked.connect(self._on_delete_clicked)
        sel.addWidget(self._del_btn)
        undo_attr_btn = QPushButton("撤销归属")
        undo_attr_btn.setObjectName("Tiny")
        undo_attr_btn.setFixedHeight(22)
        undo_attr_btn.setToolTip("撤销选中 JPG 的归属")
        icons.set_button_icon(undo_attr_btn, "mdi6.undo", color=icons.TONE_MUTED, size=14)
        undo_attr_btn.clicked.connect(self._on_selected_unassign)
        sel.addWidget(undo_attr_btn)
        sel.addStretch()
        self._selection_bar.hide()
        sec.addWidget(self._selection_bar)

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

        if not active_uid:
            self.set_phase(None)

    def set_phase(self, status: Optional[str]) -> None:
        """Reflect the confirmed task status on the phase pills (exclusive)."""
        self._current_phase = status if status in self._phase_pills else None
        for code, btn in self._phase_pills.items():
            btn.setChecked(code == self._current_phase)

    def _on_phase_pill_clicked(self, code: str) -> None:
        # Roll back Qt's automatic toggle; the workbench confirms via set_phase.
        self.set_phase(self._current_phase)
        self.phase_clicked.emit(code)

    def load_scan(self, scan_result: "ScanResult") -> None:
        """Populate the grid from a completed scan result.

        The workbench polls this every 2 s.  Rebuilding the whole card grid
        each tick (tearing down + recreating every ``_FileCard``) is the main
        UI-jank source, so skip the rebuild when nothing the grid renders has
        changed since the last scan.
        """
        sig = self._scan_signature(scan_result)
        self._scan_result = scan_result
        if sig == self._last_scan_sig:
            return
        self._last_scan_sig = sig
        self._rebuild_grid()

    def _scan_signature(self, scan_result: "ScanResult"):
        """Cheap fingerprint of everything the card grid renders.

        Includes the active UID because it changes card highlighting.
        """
        if scan_result is None:
            return None

        def fp(e):
            return (
                e.name, e.mtime, e.size,
                e.attributed_specimen_id, e.is_grouped, e.composed_tiff,
                e.has_zip,
            )

        return (
            self._active_uid,
            tuple(fp(e) for e in scan_result.jpg_files),
            tuple(fp(e) for e in scan_result.tiff_files),
        )

    def clear(self) -> None:
        """Remove all cards and show empty state."""
        self._scan_result = None
        self._last_scan_sig = None
        self._cards = []
        self._clear_grid()
        self._stat_label.setText("无项目")
        self._stat_today.setText("JPG 0")
        self._stat_recent.setText("TIFF 0")
        self._stat_untidy.setText("未整理 0")
        self._raw_count.setText("本组原片 0 张")
        self._del_btn.setEnabled(False)
        self._sel_count.setText("")
        self._selection_count.setText("已选 0 个")
        self._selection_bar.hide()
        self._unattr_warning.hide()
        self._empty_label.show()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _rebuild_grid(self) -> None:
        if not self._scan_result:
            self._clear_grid()
            self._cards = []
            self._empty_label.show()
            return

        jpgs = list(self._scan_result.jpg_files)
        # Oracle app.js:3577 — results/ 里已完整归档(同名 ZIP 存在)的 TIFF
        # 不进待处理 feed;incoming 目录里的 TIFF 照常显示。
        tiffs = [
            t for t in self._scan_result.tiff_files
            if not (getattr(t, "has_zip", False)
                    and getattr(t, "detail", "")
                    and "incoming" not in t.detail)
        ]
        all_files = jpgs + tiffs

        if not all_files:
            self._clear_grid()
            self._cards = []
            self._empty_label.show()
            jpg_c = tiff_c = 0
        else:
            self._empty_label.hide()
            jpg_c, tiff_c = len(jpgs), len(tiffs)
            self._sync_cards(all_files)

        self._stat_label.setText(f"JPG {jpg_c} · TIFF {tiff_c}")
        self._stat_today.setText(f"JPG {jpg_c}")
        self._stat_recent.setText(f"TIFF {tiff_c}")
        self._stat_untidy.setText(f"未整理 {len(all_files)}")
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

    def _card_render_sig(self, entry) -> tuple:
        """Everything that changes how a card looks (path is the reuse key)."""
        uid = getattr(entry, "attributed_specimen_id", None)
        return (
            getattr(entry, "kind", "jpg"),
            uid,
            bool(uid) and uid == self._active_uid,
            getattr(entry, "composed_tiff", None),
            getattr(entry, "archived", None),
            getattr(entry, "is_grouped", False),
        )

    def _make_card(self, entry) -> "_FileCard":
        card = _FileCard(
            entry, self._active_uid, self,
            on_add_to_group=self._on_ctx_add_to_group,
            on_assign_uid=self._on_ctx_assign_uid,
            on_unassign=self._on_ctx_unassign,
        )
        card.assign_requested.connect(self.assign_requested)
        card.deactivate_requested.connect(self.unassign_requested)
        card.selection_toggled.connect(self._on_card_selection_toggled)
        card.delete_requested.connect(self._on_delete_single_requested)
        return card

    def _sync_cards(self, all_files: list) -> None:
        """Reconcile the card grid with *all_files*, reusing widgets.

        Only files that are new or whose visual signature changed get a fresh
        card; everything else is repositioned in place (cheap), so the common
        "one new photo arrived" case builds a single card instead of all.
        """
        cols = 2
        desired = {getattr(f, "path", ""): f for f in all_files}

        # Drop cards for files that vanished.
        for key in list(self._card_by_key):
            if key not in desired:
                card = self._card_by_key.pop(key)
                self._card_sig_by_key.pop(key, None)
                card.setParent(None)
                card.deleteLater()

        # Detach all remaining cards from the grid (reposition without delete).
        while self._grid.count():
            self._grid.takeAt(0)

        self._cards = []
        for idx, f in enumerate(all_files):
            key = getattr(f, "path", "")
            sig = self._card_render_sig(f)
            card = self._card_by_key.get(key)
            if card is None or self._card_sig_by_key.get(key) != sig:
                if card is not None:
                    card.setParent(None)
                    card.deleteLater()
                card = self._make_card(f)
                self._card_by_key[key] = card
                self._card_sig_by_key[key] = sig
            self._grid.addWidget(card, idx // cols, idx % cols)
            self._cards.append(card)
        for c in range(cols):
            self._grid.setColumnStretch(c, 1)

    def _clear_grid(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self._card_by_key = {}
        self._card_sig_by_key = {}

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
        self._refresh_selection_bar()

    def _on_select_all(self) -> None:
        for card in self._cards:
            card.set_selected(True)
        self._refresh_selection_bar()

    def _on_select_none(self) -> None:
        for card in self._cards:
            card.set_selected(False)
        self._refresh_selection_bar()

    def _refresh_selection_bar(self) -> None:
        n = len(self._selected_cards())
        if n:
            self._sel_count.setText(f"已选 {n}")
            self._selection_count.setText(f"已选 {n} 个")
            self._del_btn.setEnabled(True)
            self._selection_bar.show()
        else:
            self._sel_count.setText("")
            self._selection_count.setText("已选 0 个")
            self._del_btn.setEnabled(False)
            self._selection_bar.hide()

    def selected_jpg_paths(self) -> list[str]:
        """Return absolute paths of all currently selected JPG cards."""
        return [
            c._entry.path
            for c in self._selected_cards()
            if getattr(c._entry, "kind", "") == "jpg" and getattr(c._entry, "path", "")
        ]

    def selected_tiff_paths(self) -> list[str]:
        """Return absolute paths of all currently selected TIFF cards."""
        return [
            c._entry.path
            for c in self._selected_cards()
            if getattr(c._entry, "kind", "") == "tiff" and getattr(c._entry, "path", "")
        ]

    def selected_all_paths(self) -> list[str]:
        """Return absolute paths of every currently selected card (any kind)."""
        return [
            c._entry.path
            for c in self._selected_cards()
            if getattr(c._entry, "path", "")
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
        self._delete_paths(paths, clear_selection=True)

    def _on_delete_single_requested(self, path: str) -> None:
        self._delete_paths([path], clear_selection=False)

    def _delete_paths(self, paths: list[str], *, clear_selection: bool) -> None:
        tiff_paths = [p for p in paths if p.lower().endswith((".tif", ".tiff"))]
        jpg_paths  = [p for p in paths if p and not p.lower().endswith((".tif", ".tiff"))]

        if not tiff_paths and not jpg_paths:
            QMessageBox.information(self, "删除", "请先选中要删除的文件。")
            return

        # TIFF 可删（用户主权，覆盖旧「TIFF 永不删」红线），但因 TIFF 是无损母片、
        # 删除不可恢复 → 单独弹确认框；JPG 同样确认。各自确认、删各自确认通过的。
        to_delete: list[str] = []
        if tiff_paths:
            reply = QMessageBox.question(
                self, "确认删除 TIFF",
                f"确认删除 {len(tiff_paths)} 个 TIFF 成片？\n"
                "TIFF 是无损母片，删除后不可恢复。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                to_delete += tiff_paths
        if jpg_paths:
            reply = QMessageBox.question(
                self, "确认删除",
                f"确认删除 {len(jpg_paths)} 张 JPG？\n"
                "此操作直接从磁盘删除原片，不可恢复。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                to_delete += jpg_paths
        if not to_delete:
            return

        import os as _os
        errors: list[str] = []
        deleted: list[str] = []
        for p in to_delete:
            try:
                if _os.path.isfile(p):
                    _os.unlink(p)
                    deleted.append(p)
            except OSError as exc:
                errors.append(f"{_os.path.basename(p)}: {exc}")

        if errors:
            QMessageBox.warning(self, "删除部分失败", "\n".join(errors[:5]))

        if deleted:
            if clear_selection:
                self._on_select_none()
            self.refresh_requested.emit()

    def _on_selected_add_to_group(self) -> None:
        jpg_paths = self.selected_jpg_paths()
        if not jpg_paths:
            QMessageBox.information(self, "加入分组", "请先选中 JPG。")
            return
        db = self.ctx.get_db()
        if db is None:
            return
        active_uid = activation_service.get_active_uid(db)
        if not active_uid:
            QMessageBox.warning(self, "无激活标本", "请先激活一个标本")
            return
        for jpg_path in jpg_paths:
            self._add_jpg_to_grouping(db, active_uid, jpg_path)
        self._on_select_none()
        self.refresh_requested.emit()

    def _on_selected_unassign(self) -> None:
        jpg_paths = self.selected_jpg_paths()
        if not jpg_paths:
            QMessageBox.information(self, "撤销归属", "请先选中 JPG。")
            return
        for jpg_path in jpg_paths:
            self._on_ctx_unassign(jpg_path)
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
        # 主动归属 = 解除"取消归属"黑名单，否则取消后再归属会被 P0 永久卡住
        # (oracle server.js:4216-4219：重新加入分组从 explicitUnassigns 移除)。
        grouping_service.remove_explicit_unassign(db, jpg_path)
        self.refresh_requested.emit()

    def _on_ctx_assign_uid(self, jpg_path: str, uid: str) -> None:
        db = self.ctx.get_db()
        if db is None:
            return
        self._add_jpg_to_grouping(db, uid, jpg_path)
        grouping_service.remove_explicit_unassign(db, jpg_path)  # 解除黑名单
        self.refresh_requested.emit()

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
        """取消归属 = 让这张照片彻底"变无主"。

        关键修复：原来只从分组(P1)里删，对拍摄期自动归属(P3 激活时间窗)的照片
        无效——它们不在任何分组，点了没反应。现在写入 P0 黑名单
        (add_explicit_unassign)，它在归属判定里最优先、打败一切来源
        (oracle server.js:4281-4294)，所以任何来源归属的照片都能取消。
        同时（用户选定行为，偏离 oracle 的"只写黑名单"）把它踢出合成组，
        避免废片仍被合成。
        """
        db = self.ctx.get_db()
        if db is None:
            return
        # P0：加入黑名单（打败 P1 分组 / P2 手动 / P3 激活时间窗）
        grouping_service.add_explicit_unassign(db, jpg_path)
        # 同时从任何合成组移除
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
        self.refresh_requested.emit()

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_refresh(self) -> None:
        self.refresh_requested.emit()

    def _on_auto_toggled(self, on: bool) -> None:
        """Swap the auto-compress toggle glyph between checked / unchecked."""
        glyph = "mdi6.checkbox-marked" if on else "mdi6.checkbox-blank-outline"
        tone = icons.TONE_ACCENT if on else icons.TONE_MUTED
        icons.set_button_icon(self._auto_toggle, glyph, color=tone, size=15)

    def _open_more_menu(self, global_pos) -> None:
        menu = QMenu(self)

        settings_action = menu.addAction("项目设置")
        settings_action.triggered.connect(self.settings_requested.emit)

        choose_dir_action = menu.addAction("选目录")
        choose_dir_action.setEnabled(False)
        choose_dir_action.setToolTip("请从项目树切换工作目录")

        menu.addSeparator()

        hide_action = menu.addAction("隐藏已分组原片")
        hide_action.setCheckable(True)
        hide_action.setChecked(self._hide_archived_cb.isChecked())
        hide_action.toggled.connect(self._hide_archived_cb.setChecked)

        auto_action = menu.addAction("新 TIFF 自动压缩")
        auto_action.setCheckable(True)
        auto_action.setChecked(self._auto_toggle.isChecked())
        auto_action.toggled.connect(self._auto_toggle.setChecked)

        menu.addSeparator()

        scan_action = menu.addAction("扫描旧文件")
        scan_action.setEnabled(False)
        scan_action.setToolTip("旧文件扫描在分组工具/批量整理流程中执行")

        menu.exec(global_pos)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFixedHeight(1)
    return line
