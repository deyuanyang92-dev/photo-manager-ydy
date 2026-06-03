"""grouping_panel.py — Specimen grouping editor.

Displays two sections:
  1. **Draft groups** (未合成): editable list — angle label + drag-and-drop
     reorderable JPG list per group.  Groups without a composed TIFF.
  2. **Composed rows** (已合成): read-only summary — composedTiffPath basename,
     📦 Organise button, ↩ Undo-compose button.

Data source: ``grouping_service.load_grouping(db, uid)``

Emits
-----
compose_requested(uid: str, group_index: int)
    User clicked "合成" for a draft group.
organise_requested(uid: str, group_index: int)
    User clicked "📦整理" on a composed row.
undo_compose_requested(uid: str, group_index: int)
    User clicked "↩撤销" on a composed row.
grouping_changed()
    Emitted after any in-memory edit (label rename, JPG removal) so the
    parent view knows a save is needed.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.config import icons

if TYPE_CHECKING:
    from app.app_context import AppContext
    from app.services.grouping_service import Group, SpecimenGrouping


# ── Composed row ──────────────────────────────────────────────────────────────

class _ComposedRow(QFrame):
    """A single row in the "已合成" section."""

    organise_clicked = pyqtSignal(int)   # group_index
    undo_clicked = pyqtSignal(int)       # group_index

    def __init__(self, group: "Group", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._group = group
        self._setup_ui()

    def _setup_ui(self) -> None:
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(10)

        # Composed-state chip + angle label
        chip = QLabel(self._group.angle_label or f"组 {self._group.group_index}")
        chip.setObjectName("ChipTiff")
        lay.addWidget(chip)

        # TIFF basename
        tiff_path = self._group.composed_tiff_path or ""
        tiff_name = Path(tiff_path).name if tiff_path else "(无 TIFF)"
        tiff_lbl = QLabel(tiff_name)
        tiff_lbl.setObjectName("Mono")
        tiff_lbl.setToolTip(tiff_path)
        tiff_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay.addWidget(tiff_lbl)

        # JPG count badge
        jpg_count = len(self._group.jpg_paths)
        count_lbl = QLabel(f"{jpg_count} JPG")
        count_lbl.setObjectName("MutedSmall")
        lay.addWidget(count_lbl)

        # Organise button
        org_btn = QPushButton("整理")
        org_btn.setObjectName("Primary")
        org_btn.setFixedHeight(28)
        icons.set_button_icon(org_btn, "mdi6.archive-arrow-down-outline",
                              color=icons.TONE_ON_ACCENT, size=14)
        org_btn.setToolTip("归档 JPG → ZIP，按设置删除 JPG")
        org_btn.clicked.connect(lambda: self.organise_clicked.emit(self._group.group_index))
        lay.addWidget(org_btn)

        # Undo button
        undo_btn = QPushButton()
        undo_btn.setObjectName("Ghost")
        undo_btn.setFixedSize(30, 28)
        icons.set_button_icon(undo_btn, "mdi6.undo-variant", color=icons.TONE_MUTED, size=15)
        undo_btn.setToolTip("解除合成关联（TIFF 移到 _retired-tiff/，不删除）")
        undo_btn.clicked.connect(lambda: self.undo_clicked.emit(self._group.group_index))
        lay.addWidget(undo_btn)


# ── Draft group ───────────────────────────────────────────────────────────────

class _DraftGroupRow(QFrame):
    """Editable draft group card (angle label + drag-reorderable JPG list)."""

    compose_clicked = pyqtSignal(int)         # group_index
    label_changed = pyqtSignal(int, str)      # group_index, new_label
    jpg_removed = pyqtSignal(int, str)        # group_index, jpg_path (kept for compat)
    add_selected_to_group = pyqtSignal(int)   # group_index
    jpg_remove_requested = pyqtSignal(int, str)  # group_index, jpg_path
    clear_group_requested = pyqtSignal(int)   # group_index  #cursor
    delete_group_requested = pyqtSignal(int)  # group_index  #cursor

    def __init__(self, group: "Group", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self._group = group
        self._setup_ui()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # Header row: group chip + angle label edit + compose button
        header = QHBoxLayout()
        header.setSpacing(8)

        chip = QLabel(f"组 {self._group.group_index}")
        chip.setObjectName("ChipArchived")
        header.addWidget(chip)

        self._label_edit = QLineEdit(self._group.angle_label or "")
        self._label_edit.setPlaceholderText("角度标签（如：正面、背面）")
        self._label_edit.setFixedHeight(30)
        self._label_edit.textEdited.connect(
            lambda t: self.label_changed.emit(self._group.group_index, t)
        )
        header.addWidget(self._label_edit)

        compose_btn = QPushButton("合成")
        compose_btn.setObjectName("Primary")
        compose_btn.setFixedHeight(30)
        icons.set_button_icon(compose_btn, "fa5s.layer-group",
                              color=icons.TONE_ON_ACCENT, size=13)
        compose_btn.setToolTip("调用 Helicon Focus CLI 合成该组 JPG")
        compose_btn.clicked.connect(lambda: self.compose_clicked.emit(self._group.group_index))
        header.addWidget(compose_btn)

        add_sel_btn = QPushButton("← 加入所选")
        add_sel_btn.setObjectName("Ghost")
        add_sel_btn.setFixedHeight(26)
        add_sel_btn.setToolTip("将监控区选中的 JPG 加入此分组（其他组自动移除）")
        add_sel_btn.clicked.connect(
            lambda: self.add_selected_to_group.emit(self._group.group_index)
        )
        header.addWidget(add_sel_btn)

        # ── 清空 / 删组 按钮  #cursor ──────────────────────────────────────
        clear_btn = QPushButton()
        clear_btn.setObjectName("Ghost")
        clear_btn.setFixedSize(26, 26)
        icons.set_button_icon(clear_btn, "mdi6.eraser", color=icons.TONE_MUTED, size=13)
        clear_btn.setToolTip("清空此组所有 JPG（不删除文件）")
        clear_btn.clicked.connect(lambda: self.clear_group_requested.emit(self._group.group_index))
        header.addWidget(clear_btn)

        del_btn = QPushButton()
        del_btn.setObjectName("Ghost")
        del_btn.setFixedSize(26, 26)
        icons.set_button_icon(del_btn, "mdi6.delete-outline", color=icons.TONE_DANGER, size=13)
        del_btn.setToolTip("删除此分组（仅删记录，不删文件）")
        del_btn.clicked.connect(lambda: self.delete_group_requested.emit(self._group.group_index))
        header.addWidget(del_btn)

        root.addLayout(header)

        # JPG list (drag-reorderable)
        self._jpg_list = QListWidget()
        self._jpg_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self._jpg_list.setMaximumHeight(104)
        self._jpg_list.setToolTip("拖拽可重新排序；右键 → 移除此 JPG")
        for p in self._group.jpg_paths:
            item = QListWidgetItem(Path(p).name)
            item.setData(Qt.ItemDataRole.UserRole, p)
            item.setToolTip(p)
            self._jpg_list.addItem(item)

        if not self._group.jpg_paths:
            empty = QListWidgetItem("空组 — 从监控区拖入 JPG 或点「← 加入所选」")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self._jpg_list.addItem(empty)

        # Context menu for right-click remove
        self._jpg_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._jpg_list.customContextMenuRequested.connect(self._on_jpg_context_menu)
        root.addWidget(self._jpg_list)

        # JPG count line
        count_lbl = QLabel(f"{len(self._group.jpg_paths)} 张 JPG")
        count_lbl.setObjectName("MutedSmall")
        root.addWidget(count_lbl)


    def _on_jpg_context_menu(self, pos) -> None:
        """Right-click context menu on the JPG list — offers 「移除此 JPG」."""
        from PyQt6.QtWidgets import QMenu
        item = self._jpg_list.itemAt(pos)
        if not item:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        menu = QMenu(self)
        action = menu.addAction("移除此 JPG")
        chosen = menu.exec(self._jpg_list.mapToGlobal(pos))
        if chosen == action:
            self.jpg_remove_requested.emit(self._group.group_index, path)


# ── Grouping panel ────────────────────────────────────────────────────────────

class GroupingPanel(QWidget):
    """Full grouping editor: draft groups above, composed rows below.

    Signals
    -------
    compose_requested(uid, group_index)
    organise_requested(uid, group_index)
    undo_compose_requested(uid, group_index)
    grouping_changed()
    """

    compose_requested = pyqtSignal(str, int)
    organise_requested = pyqtSignal(str, int)
    undo_compose_requested = pyqtSignal(str, int)
    grouping_changed = pyqtSignal()
    # Bulk-action signals (capture-main-actions row)
    compose_all_requested = pyqtSignal(str)    # uid — compose all pending groups
    organise_all_requested = pyqtSignal(str)   # uid — organise all composed groups
    # Add-to-group / free-compose / retroactive signals
    add_selection_to_group_requested = pyqtSignal(int)  # group_index
    free_compose_requested = pyqtSignal()
    retroactive_requested = pyqtSignal()

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._uid: Optional[str] = None
        self._grouping: Optional["SpecimenGrouping"] = None
        self._setup_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        section = QFrame()
        section.setObjectName("WorkbenchSection")
        outer.addWidget(section)
        from app.config.effects import apply_card_shadow
        apply_card_shadow(section)

        root = QVBoxLayout(section)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # ── capture-main-actions row (web parity: ⚡合成/合成+整理/🗜整理/⋯更多) ──
        main_actions = QHBoxLayout()
        main_actions.setContentsMargins(0, 0, 0, 0)
        main_actions.setSpacing(8)
        compose_btn = QPushButton("⚡ 合成")
        compose_btn.setObjectName("Primary")
        compose_btn.setFixedHeight(30)
        icons.set_button_icon(compose_btn, "fa5s.layer-group",
                              color=icons.TONE_ON_ACCENT, size=13)
        compose_btn.setToolTip("对所有待合成组调用 Helicon Focus")
        compose_btn.clicked.connect(self._on_compose_all)
        main_actions.addWidget(compose_btn)

        compose_org_btn = QPushButton("合成+整理")
        compose_org_btn.setObjectName("Primary")
        compose_org_btn.setFixedHeight(30)
        compose_org_btn.setToolTip("合成后立即整理归档")
        compose_org_btn.clicked.connect(self._on_compose_and_organise_all)
        main_actions.addWidget(compose_org_btn)

        org_btn = QPushButton("🗜 整理")
        org_btn.setObjectName("Outline")
        org_btn.setFixedHeight(30)
        icons.set_button_icon(org_btn, "mdi6.archive-arrow-down-outline",
                              color=icons.TONE_MUTED, size=13)
        org_btn.setToolTip("整理所有已合成组（归档 JPG）")
        org_btn.clicked.connect(self._on_organise_all)
        main_actions.addWidget(org_btn)

        more_btn = QPushButton("⋯ 更多 ▾")
        more_btn.setObjectName("Ghost")
        more_btn.setFixedHeight(30)
        more_btn.setToolTip("更多操作")
        more_btn.setMenu(self._build_more_menu())
        main_actions.addWidget(more_btn)

        main_actions.addStretch()

        self._target_label = QLabel("—")
        self._target_label.setObjectName("Mono")
        self._target_label.setToolTip("当前目标标本编号")
        main_actions.addWidget(self._target_label)
        root.addLayout(main_actions)

        # ── ▸ 分组工具 collapsible header ──
        group_toggle_row = QHBoxLayout()
        group_toggle_row.setContentsMargins(0, 0, 0, 0)
        group_toggle_row.setSpacing(6)
        self._group_toggle_btn = QPushButton("▸ 分组工具")
        self._group_toggle_btn.setObjectName("Ghost")
        self._group_toggle_btn.setFixedHeight(26)
        self._group_toggle_btn.setCheckable(True)
        self._group_toggle_btn.setChecked(True)
        self._group_toggle_btn.clicked.connect(self._on_group_toggle)
        group_toggle_row.addWidget(self._group_toggle_btn)
        hint_drag = QLabel("拖入所选 JPG + TIFF 补处理")
        hint_drag.setObjectName("MutedSmall")
        group_toggle_row.addWidget(hint_drag)
        group_toggle_row.addStretch()

        self._uid_label = QLabel("无激活标本")
        self._uid_label.setObjectName("Mono")
        group_toggle_row.addWidget(self._uid_label)

        add_btn = QPushButton("新组")
        add_btn.setObjectName("Outline")
        add_btn.setFixedHeight(28)
        icons.set_button_icon(add_btn, "mdi6.plus", color=icons.TONE_ACCENT, size=14)
        add_btn.clicked.connect(self._add_group)
        group_toggle_row.addWidget(add_btn)
        root.addLayout(group_toggle_row)

        line = QFrame()
        line.setObjectName("Divider")
        line.setFixedHeight(1)
        root.addWidget(line)

        # Collapsible body
        self._group_body = QWidget()
        body_lay = QVBoxLayout(self._group_body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(8)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._content = QWidget()
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.setContentsMargins(0, 4, 0, 4)
        self._content_lay.setSpacing(8)
        self._content_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._content)
        body_lay.addWidget(scroll, stretch=1)

        # Empty state
        self._empty_lbl = QLabel("激活一个标本后查看或编辑分组")
        self._empty_lbl.setObjectName("Muted")
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body_lay.addWidget(self._empty_lbl)

        root.addWidget(self._group_body, stretch=1)

    # ── Public API ────────────────────────────────────────────────────────────

    def load_grouping(self, uid: str, grouping: "SpecimenGrouping") -> None:
        """Display all groups for *uid*."""
        self._uid = uid
        self._grouping = grouping
        short = uid[:30] + ("…" if len(uid) > 30 else "")
        self._uid_label.setText(short)
        self._target_label.setText(short)
        self._rebuild()

    def clear(self) -> None:
        self._uid = None
        self._grouping = None
        self._uid_label.setText("— 无激活标本 —")
        self._target_label.setText("—")
        self._clear_content()
        self._empty_lbl.show()

    def add_jpgs_to_group(self, group_index: int, jpg_paths: list[str]) -> None:
        """Add *jpg_paths* to the group at *group_index* (mutual exclusion).

        Removes paths from all other groups first, then appends (no duplicates).
        Mirrors web groupingAddSelectedToGroup() app.js:5258–5271.
        """
        if not self._grouping:
            return
        # P1: remove paths from all other groups (mutual exclusion)
        for g in self._grouping.groups:
            if g.group_index != group_index:
                g.jpg_paths = [p for p in g.jpg_paths if p not in jpg_paths]
        # P2: add to target group (no duplicates)
        target = next((g for g in self._grouping.groups if g.group_index == group_index), None)
        if target is None:
            return
        for p in jpg_paths:
            if p not in target.jpg_paths:
                target.jpg_paths.append(p)
        self._rebuild()
        self.grouping_changed.emit()

    def remove_jpg_from_group(self, group_index: int, jpg_path: str) -> None:
        """Remove *jpg_path* from the specified group.

        Mirrors web groupingRemoveFile() app.js:5274–5280.
        """
        if not self._grouping:
            return
        for g in self._grouping.groups:
            if g.group_index == group_index:
                g.jpg_paths = [p for p in g.jpg_paths if p != jpg_path]
                break
        self._rebuild()
        self.grouping_changed.emit()

    def clear_group(self, group_index: int) -> None:
        """Clear all JPGs from *group_index* (does not delete files).

        Mirrors web groupingClearGroup() app.js:5291–5297.
        """
        if not self._grouping:
            return
        for g in self._grouping.groups:
            if g.group_index == group_index:
                g.jpg_paths = []
                break
        self._rebuild()
        self.grouping_changed.emit()

    def delete_group(self, group_index: int) -> None:
        """Delete the group entirely (in-memory only, no file deletion).

        Mirrors web groupingDeleteGroup() app.js:5283–5289.
        Composed-TIFF groups cannot be deleted without undo-compose first.
        """
        if not self._grouping:
            return
        # Guard: refuse deletion of composed groups (caller should undo first)
        target = next((g for g in self._grouping.groups if g.group_index == group_index), None)
        if target is None:
            return
        if target.composed_tiff_path:
            return  # composed groups: caller must undo-compose first
        self._grouping.groups = [g for g in self._grouping.groups if g.group_index != group_index]
        self._rebuild()
        self.grouping_changed.emit()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _build_more_menu(self):
        """Build the ⋯ 更多 dropdown menu."""
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        free_act = menu.addAction("无号合成（选中 JPG → incoming-jpg/）")
        free_act.triggered.connect(self.free_compose_requested.emit)
        retro_act = menu.addAction("存量整理…")
        retro_act.triggered.connect(self.retroactive_requested.emit)
        return menu

    def _rebuild(self) -> None:
        self._clear_content()
        if not self._grouping or not self._uid:
            self._empty_lbl.show()
            return

        self._empty_lbl.hide()
        groups = self._grouping.groups

        draft = [g for g in groups if not g.composed_tiff_path]
        composed = [g for g in groups if g.composed_tiff_path]

        if draft:
            sec_lbl = QLabel("未合成组")
            sec_lbl.setObjectName("Section")
            self._content_lay.addWidget(sec_lbl)
            for g in draft:
                row = _DraftGroupRow(g, self)
                row.compose_clicked.connect(self._on_compose)
                row.label_changed.connect(self._on_label_changed)
                row.add_selected_to_group.connect(self._on_add_selected_to_group)
                row.jpg_remove_requested.connect(self._on_jpg_remove)
                row.clear_group_requested.connect(self._on_clear_group)   # #cursor
                row.delete_group_requested.connect(self._on_delete_group) # #cursor
                self._content_lay.addWidget(row)

        if composed:
            sep = QFrame()
            sep.setObjectName("Divider")
            sep.setFixedHeight(1)
            self._content_lay.addWidget(sep)
            sec_lbl2 = QLabel("已合成")
            sec_lbl2.setObjectName("Section")
            self._content_lay.addWidget(sec_lbl2)
            for g in composed:
                row2 = _ComposedRow(g, self)
                row2.organise_clicked.connect(self._on_organise)
                row2.undo_clicked.connect(self._on_undo)
                self._content_lay.addWidget(row2)

        if not groups:
            no_lbl = QLabel("此标本暂无分组 — 点「+ 新组」创建")
            no_lbl.setObjectName("Muted")
            self._content_lay.addWidget(no_lbl)

    def _clear_content(self) -> None:
        while self._content_lay.count():
            item = self._content_lay.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

    def _add_group(self) -> None:
        """Add a new empty draft group in memory and emit grouping_changed."""
        if not self._grouping or not self._uid:
            return
        from app.services.grouping_service import Group
        new_index = max((g.group_index for g in self._grouping.groups), default=-1) + 1
        new_group = Group(group_index=new_index, angle_label="", jpg_paths=[])
        self._grouping.groups.append(new_group)
        self._rebuild()
        self.grouping_changed.emit()

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_group_toggle(self, checked: bool) -> None:
        """Show/hide the group editor body; update toggle button label."""
        self._group_body.setVisible(checked)
        self._group_toggle_btn.setText("▾ 分组工具" if checked else "▸ 分组工具")

    def _on_compose_all(self) -> None:
        """Compose all draft groups for the current specimen."""
        if not self._uid or not self._grouping:
            return
        draft = [g for g in self._grouping.groups if not g.composed_tiff_path]
        for g in draft:
            self.compose_requested.emit(self._uid, g.group_index)

    def _on_organise_all(self) -> None:
        """Organise all composed groups for the current specimen."""
        if not self._uid or not self._grouping:
            return
        composed = [g for g in self._grouping.groups if g.composed_tiff_path]
        for g in composed:
            self.organise_requested.emit(self._uid, g.group_index)

    def _on_compose_and_organise_all(self) -> None:
        """Compose all draft groups then organise — sequential per group."""
        if not self._uid or not self._grouping:
            return
        # Compose all pending first (each emits compose_requested)
        draft = [g for g in self._grouping.groups if not g.composed_tiff_path]
        for g in draft:
            self.compose_requested.emit(self._uid, g.group_index)
        # Then organise already-composed ones
        composed = [g for g in self._grouping.groups if g.composed_tiff_path]
        for g in composed:
            self.organise_requested.emit(self._uid, g.group_index)

    def _on_compose(self, group_index: int) -> None:
        if self._uid:
            self.compose_requested.emit(self._uid, group_index)

    def _on_organise(self, group_index: int) -> None:
        if self._uid:
            self.organise_requested.emit(self._uid, group_index)

    def _on_undo(self, group_index: int) -> None:
        if self._uid:
            self.undo_compose_requested.emit(self._uid, group_index)

    def _on_label_changed(self, group_index: int, new_label: str) -> None:
        if not self._grouping:
            return
        for g in self._grouping.groups:
            if g.group_index == group_index:
                g.angle_label = new_label
                break
        self.grouping_changed.emit()

    def _on_add_selected_to_group(self, group_index: int) -> None:
        """Request workbench view to resolve monitor selection and add to group."""
        self.add_selection_to_group_requested.emit(group_index)

    def _on_jpg_remove(self, group_index: int, jpg_path: str) -> None:
        """Handle right-click remove from _DraftGroupRow."""
        self.remove_jpg_from_group(group_index, jpg_path)

    def _on_clear_group(self, group_index: int) -> None:  # #cursor
        """Handle clear-group button from _DraftGroupRow."""
        self.clear_group(group_index)

    def _on_delete_group(self, group_index: int) -> None:  # #cursor
        """Handle delete-group button from _DraftGroupRow."""
        self.delete_group(group_index)
