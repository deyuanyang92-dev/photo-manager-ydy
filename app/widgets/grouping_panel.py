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
    QDialog,
    QDialogButtonBox,
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
from app.services import grouping_service

if TYPE_CHECKING:
    from app.app_context import AppContext
    from app.services.grouping_service import Group, SpecimenGrouping


# ── Cross-group drag list ─────────────────────────────────────────────────────

class _CrossGroupList(QListWidget):
    """QListWidget that supports cross-group JPG drag-drop.

    When a drop arrives from a *different* list, the dragged item is removed
    from the source list and the parent GroupingPanel._on_groups_changed() is
    called to persist the change.

    Within the same list, items reorder normally (InternalMove behaviour is
    preserved by letting Qt handle it via the base dropEvent).
    """

    def __init__(self, panel: "GroupingPanel", group_index: int,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._panel = panel
        self._group_index = group_index
        self.setDragDropMode(QListWidget.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAcceptDrops(True)

    def dropEvent(self, event) -> None:
        src = event.source()
        if src is self:
            # Same-list reorder — delegate to Qt's default implementation.
            super().dropEvent(event)
            self._panel._on_groups_changed()
            return

        if not isinstance(src, _CrossGroupList):
            event.ignore()
            return

        # Cross-group move: identify the item being dragged.
        item = src.currentItem()
        if item is None:
            event.ignore()
            return

        jpg_path = item.data(Qt.ItemDataRole.UserRole)
        if not jpg_path:
            event.ignore()
            return

        # Remove from source list widget and source group model.
        src.takeItem(src.row(item))

        # Add to this list widget.
        new_item = QListWidgetItem(item.text())
        new_item.setData(Qt.ItemDataRole.UserRole, jpg_path)
        new_item.setToolTip(jpg_path)
        self.addItem(new_item)

        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()

        # Persist via panel (updates the in-memory model and saves to DB).
        self._panel._move_jpg_between_groups(
            src_group_index=src._group_index,
            dst_group_index=self._group_index,
            jpg_path=jpg_path,
        )


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
    import_tiff_requested = pyqtSignal(int)   # group_index  #cursor groupingImportTiff
    output_name_changed = pyqtSignal(int, str)  # group_index, 用户编辑的输出命名

    def __init__(self, group: "Group", parent: Optional[QWidget] = None,
                 panel: Optional["GroupingPanel"] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self._group = group
        self._panel = panel
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

        # ── 导入 TIFF / 清空 / 删组 按钮  #cursor ─────────────────────────
        import_tiff_btn = QPushButton()
        import_tiff_btn.setObjectName("Ghost")
        import_tiff_btn.setFixedSize(26, 26)
        icons.set_button_icon(import_tiff_btn, "mdi6.file-import-outline",
                              color=icons.TONE_MUTED, size=13)
        import_tiff_btn.setToolTip("导入已有 TIFF 关联到本组（跳过 Helicon 直接整理）")
        import_tiff_btn.clicked.connect(
            lambda: self.import_tiff_requested.emit(self._group.group_index)
        )
        header.addWidget(import_tiff_btn)

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

        # JPG list (drag-reorderable, supports cross-group drops)
        self._jpg_list = _CrossGroupList(
            panel=self._panel,
            group_index=self._group.group_index,
            parent=self,
        )
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

        # ── 输出 TIF 命名（可见 + 可编辑）──────────────────────────────────────
        #   空时自动派生：合成→编号-序号 / 导入TIF→TIF原名；用户也可手输覆盖。
        out_row = QHBoxLayout()
        out_row.setSpacing(6)
        out_lbl = QLabel("输出 TIF")
        out_lbl.setObjectName("MutedSmall")
        out_row.addWidget(out_lbl)
        self._output_edit = QLineEdit(self._effective_output_name())
        self._output_edit.setPlaceholderText("自动：编号-序号 / 导入TIF原名（可手输）")
        self._output_edit.setFixedHeight(28)
        self._output_edit.setToolTip(
            "本组合成/整理的输出文件名（不含路径与扩展名）。\n"
            "留空 = 自动：有激活编号按 编号-序号，导入TIF则用TIF原名。"
        )
        self._output_edit.textEdited.connect(
            lambda t: self.output_name_changed.emit(self._group.group_index, t)
        )
        out_row.addWidget(self._output_edit, 1)
        root.addLayout(out_row)

    def _effective_output_name(self) -> str:
        """当前应显示的输出名：用户覆盖 > 已合成TIF名 > 临时分组默认组序 > 空。"""
        g = self._group
        if g.output_name:
            return g.output_name
        if g.composed_tiff_path:
            return Path(g.composed_tiff_path).stem
        # 临时分组(无编号)：默认显示 组序(1/2/…)，让用户看到不填就用这个。
        from app.services.grouping_service import ADHOC_GROUPING_UID
        panel_uid = getattr(self._panel, "_uid", None) if self._panel else None
        if panel_uid == ADHOC_GROUPING_UID:
            return str(g.group_index + 1)
        return ""


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

class _SuppDropButton(QPushButton):
    """Drop-aware button for 补处理 (拖入所选 JPG + TIFF 补处理).

    Click → caller consumes the monitor selection. OS drag-drop of files →
    ``files_dropped`` carries the dropped local paths directly. Always enabled
    once a project is open — independent of the active-specimen gate.
    """

    files_dropped = pyqtSignal(list)  # list[str] of local file paths

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        md = event.mimeData()
        if md is not None and md.hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        md = event.mimeData()
        if md is not None and md.hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
        md = event.mimeData()
        if md is None or not md.hasUrls():
            event.ignore()
            return
        paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
        paths = [p for p in paths if p]
        event.acceptProposedAction()
        if paths:
            self.files_dropped.emit(paths)


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
    compose_and_organise_all_requested = pyqtSignal(str)  # uid — 合成全部 + 逐组整理
    organise_all_requested = pyqtSignal(str)   # uid — organise all composed groups
    # Add-to-group / free-compose / retroactive signals
    add_selection_to_group_requested = pyqtSignal(int)  # group_index
    free_compose_requested = pyqtSignal()
    retroactive_requested = pyqtSignal()
    import_tiff_requested = pyqtSignal(str, int)  # uid, group_index  #cursor groupingImportTiff
    # 补处理 (supplementary archival) — independent of the active-specimen gate.
    supp_process_requested = pyqtSignal()       # click → consume monitor selection
    supp_files_dropped = pyqtSignal(list)       # OS drop → list[str] of local paths

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
        # Hidden when no specimen active (mirrors app.js:7374-7378 early return)
        self._toolbar_widget = QWidget()
        main_actions = QHBoxLayout(self._toolbar_widget)
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
        root.addWidget(self._toolbar_widget)
        self._toolbar_widget.hide()

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
        self._supp_btn = _SuppDropButton("拖入所选 JPG + TIFF 补处理")
        self._supp_btn.setObjectName("Ghost")
        self._supp_btn.setFixedHeight(26)
        self._supp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._supp_btn.setToolTip(
            "在监控区勾选 JPG 原片 + TIFF 成片后点击，或直接把文件拖到此处。\n"
            "无需激活标本——标本身份从 TIFF 文件名识别。"
        )
        self._supp_btn.clicked.connect(self.supp_process_requested.emit)
        self._supp_btn.files_dropped.connect(self.supp_files_dropped.emit)
        group_toggle_row.addWidget(self._supp_btn)
        group_toggle_row.addStretch()

        self._uid_label = QLabel("未选择标本")
        self._uid_label.setObjectName("Mono")
        group_toggle_row.addWidget(self._uid_label)

        self._add_btn = QPushButton("新组")
        self._add_btn.setObjectName("Outline")
        self._add_btn.setFixedHeight(28)
        icons.set_button_icon(self._add_btn, "mdi6.plus", color=icons.TONE_ACCENT, size=14)
        self._add_btn.clicked.connect(self._add_group)
        self._add_btn.hide()
        group_toggle_row.addWidget(self._add_btn)
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

        # Empty state（无任何编号时的引导；分组无需激活，有编号即可加组）
        self._empty_lbl = QLabel(
            "先在右侧填写标本编号（或选中/激活一个编号），即可点「新组」按角度分组。\n"
            "分组无需激活——隐式主流程直接用监控区上方的[合成]按钮即可。"
        )
        self._empty_lbl.setObjectName("Muted")
        self._empty_lbl.setWordWrap(True)
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
        self._toolbar_widget.show()
        self._add_btn.show()
        self._rebuild()

    def clear(self) -> None:
        self._uid = None
        self._grouping = None
        self._uid_label.setText("— 未选择标本 —")
        self._target_label.setText("—")
        self._toolbar_widget.hide()
        self._add_btn.hide()
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

    def _move_jpg_between_groups(
        self, src_group_index: int, dst_group_index: int, jpg_path: str
    ) -> None:
        """Move *jpg_path* from *src_group_index* to *dst_group_index* in the
        in-memory model, then persist and emit grouping_changed.

        Called by _CrossGroupList.dropEvent on a cross-group drop.
        """
        if not self._grouping:
            return
        for g in self._grouping.groups:
            if g.group_index == src_group_index:
                g.jpg_paths = [p for p in g.jpg_paths if p != jpg_path]
            elif g.group_index == dst_group_index:
                if jpg_path not in g.jpg_paths:
                    g.jpg_paths.append(jpg_path)
        self._on_groups_changed()

    def _on_groups_changed(self) -> None:
        """Persist the current in-memory grouping to DB and emit grouping_changed."""
        if not self._grouping or not self._uid:
            return
        db = self.ctx.get_db()
        if db is not None:
            grouping_service.save_grouping(
                db, self._uid, self._grouping.groups, clean_phantoms=False
            )
        self.grouping_changed.emit()

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
                row = _DraftGroupRow(g, self, panel=self)
                row.compose_clicked.connect(self._on_compose)
                row.label_changed.connect(self._on_label_changed)
                row.add_selected_to_group.connect(self._on_add_selected_to_group)
                row.jpg_remove_requested.connect(self._on_jpg_remove)
                row.clear_group_requested.connect(self._on_clear_group)      # #cursor
                row.delete_group_requested.connect(self._on_delete_group)    # #cursor
                row.import_tiff_requested.connect(self._on_import_tiff)      # #cursor
                row.output_name_changed.connect(self._on_output_name_changed)
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
        """新增一个空草稿组，自动标「角度N」（web 同款 angleLabel:"角度"+n）。"""
        if not self._grouping or not self._uid:
            return
        from app.services.grouping_service import Group
        new_index = max((g.group_index for g in self._grouping.groups), default=-1) + 1
        new_group = Group(
            group_index=new_index,
            angle_label=f"角度{new_index + 1}",  # 角度1 / 角度2 …，省手敲
            jpg_paths=[],
        )
        self._grouping.groups.append(new_group)
        self._rebuild()
        self.grouping_changed.emit()

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_group_toggle(self, checked: bool) -> None:
        """Show/hide the group editor body; update toggle button label."""
        self._group_body.setVisible(checked)
        self._group_toggle_btn.setText("▾ 分组工具" if checked else "▸ 分组工具")

    def _on_compose_all(self) -> None:
        """[⚡合成] 批量:发单信号,由 workbench 驱动顺序队列(异步合成需串行,
        不能在面板里紧循环 emit——会同时启动多个 HeliconWorker 互相覆盖)。"""
        if not self._uid:
            return
        self.compose_all_requested.emit(self._uid)

    def _on_organise_all(self) -> None:
        """[🗜整理] 批量:发单信号,workbench 逐组同步整理已合成组。"""
        if not self._uid:
            return
        self.organise_all_requested.emit(self._uid)

    def _on_compose_and_organise_all(self) -> None:
        """[合成+整理] 批量:发单信号,workbench 顺序队列——每组合成完成(异步回调)
        后再同步整理该组,然后下一组。旧紧循环 emit 在合成完成前就读 composed →
        刚合成的组整理空跑,故移除。"""
        if not self._uid:
            return
        self.compose_and_organise_all_requested.emit(self._uid)

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

    def _on_output_name_changed(self, group_index: int, name: str) -> None:
        """用户编辑某组「输出 TIF」命名 → 写入 group.output_name（空=回到自动派生）。"""
        if not self._grouping:
            return
        for g in self._grouping.groups:
            if g.group_index == group_index:
                g.output_name = name.strip() or None
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

    def _on_import_tiff(self, group_index: int) -> None:  # #cursor groupingImportTiff
        """Open TIFF-import dialog and update the group composedTiffPath."""
        if not self._uid or not self._grouping:
            return
        target = next(
            (g for g in self._grouping.groups if g.group_index == group_index), None
        )
        if target is None:
            return

        # Collect TIFF candidates from the project's results/ and incoming-jpg/
        tiff_candidates: list[str] = []
        try:
            import os
            project_dir = getattr(self.ctx, "current_project_dir", None)
            if project_dir:
                # 用项目配置的 incoming/results 子目录（含遗留 新拍JPG），不写死。
                s = getattr(self.ctx, "settings", None)
                inc = getattr(s, "incoming_subdir", None)
                res = getattr(s, "results_subdir", None)
                inc = inc if isinstance(inc, str) and inc else "incoming-jpg"
                res = res if isinstance(res, str) and res else "results"
                subs = [res, inc]
                if not os.path.isdir(os.path.join(project_dir, inc)) and \
                   os.path.isdir(os.path.join(project_dir, "新拍JPG")):
                    subs.append("新拍JPG")
                for sub in subs:
                    d = os.path.join(project_dir, sub)
                    if os.path.isdir(d):
                        for f in sorted(os.listdir(d)):
                            if f.lower().endswith((".tif", ".tiff")):
                                tiff_candidates.append(os.path.join(d, f))
        except Exception:
            pass

        # Show picker dialog
        dlg = _TiffImportDialog(
            group_index=group_index,
            tiff_candidates=tiff_candidates,
            existing_tiff=target.composed_tiff_path or "",
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        tiff_path = dlg.selected_path()
        if not tiff_path:
            return

        # Guard: group already has a different TIFF  (mirrors web check)
        if target.composed_tiff_path and target.composed_tiff_path != tiff_path:
            from PyQt6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self,
                "替换 TIFF？",
                f"本组已有 TIFF：{Path(target.composed_tiff_path).name}\n\n"
                f"是否替换为：{Path(tiff_path).name}？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        # Apply：导入外部 TIF → 用该 TIF 的名作输出命名（去 .tif/.tiff 后缀），
        # 这样整理时 ZIP 与 TIF 同名。一键整理无需手敲（你的设计）。
        from datetime import datetime, timezone
        target.composed_tiff_path = tiff_path
        target.output_name = Path(tiff_path).stem
        target.status = "composed"
        target.source = target.source or "external-tif"
        target.updated_at = datetime.now(tz=timezone.utc).isoformat()

        self._rebuild()
        self.grouping_changed.emit()
        # Propagate to workbench view as well
        if self._uid:
            self.import_tiff_requested.emit(self._uid, group_index)


# ── TIFF Import Dialog ────────────────────────────────────────────────────────

class _TiffImportDialog(QDialog):
    """Dialog to pick an existing TIFF file and associate it to a group.

    Mirrors web renderTiffImportModal() app.js:6124.
    Shows:
      1. A scrollable list of TIF/TIFF files found in results/ and incoming-jpg/
      2. A text field to paste an arbitrary absolute path
    """

    def __init__(
        self,
        group_index: int,
        tiff_candidates: list[str],
        existing_tiff: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._selected: str = ""
        self.setWindowTitle(f"导入 TIF → 组 {group_index}")
        self.setMinimumWidth(520)
        self.setMinimumHeight(380)
        self._setup_ui(group_index, tiff_candidates, existing_tiff)

    def _setup_ui(
        self,
        group_index: int,
        candidates: list[str],
        existing_tiff: str,
    ) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16,16)
        root.setSpacing(10)

        note = QLabel(
            "选择一张已有 TIF（如在 Helicon 手动合成的）挂到本组，"
            "随后点「整理」把对应 JPG 打包归档，不重跑 Helicon。"
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        root.addWidget(note)

        sec = QLabel("检测到的 TIF 文件")
        sec.setObjectName("Section")
        root.addWidget(sec)

        self._list = QListWidget()
        self._list.setMaximumHeight(200)
        for path in candidates:
            item = QListWidgetItem(Path(path).name)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self._list.addItem(item)
        if not candidates:
            placeholder = QListWidgetItem("（项目目录暂无 TIF 文件）")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(placeholder)
        self._list.itemDoubleClicked.connect(self._on_list_double_clicked)
        root.addWidget(self._list)

        paste_row = QHBoxLayout()
        paste_lbl = QLabel("或粘贴绝对路径：")
        paste_lbl.setObjectName("Muted")
        paste_row.addWidget(paste_lbl)
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("粘贴 TIF 文件的完整路径")
        if existing_tiff:
            self._path_edit.setText(existing_tiff)
        paste_row.addWidget(self._path_edit, stretch=1)
        root.addLayout(paste_row)

        # Browse button
        browse_row = QHBoxLayout()
        browse_btn = QPushButton("浏览…")
        browse_btn.setObjectName("Ghost")
        browse_btn.setFixedHeight(28)
        browse_btn.clicked.connect(self._on_browse)
        browse_row.addWidget(browse_btn)
        browse_row.addStretch()
        root.addLayout(browse_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _on_list_double_clicked(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self._path_edit.setText(path)

    def _on_browse(self) -> None:
        from app.utils.ui import get_open_file_name
        path = get_open_file_name(
            self, "选择 TIF 文件", filter="TIFF 文件 (*.tif *.tiff *.TIF *.TIFF)"
        )
        if path:
            self._path_edit.setText(path)

    def _on_accept(self) -> None:
        # Prefer path_edit; fall back to list selection
        path = self._path_edit.text().strip()
        if not path:
            item = self._list.currentItem()
            if item:
                path = item.data(Qt.ItemDataRole.UserRole) or ""
        import re
        if path and not re.search(r"\.(tif|tiff)$", path, re.IGNORECASE):
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "格式错误", "请选择 .tif / .tiff 文件。")
            return
        self._selected = path
        self.accept()

    def selected_path(self) -> str:
        return self._selected
