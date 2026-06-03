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
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(8)

        # Angle label badge
        angle_lbl = QLabel(self._group.angle_label or f"组{self._group.group_index}")
        angle_lbl.setObjectName("Muted")
        angle_lbl.setFixedWidth(60)
        lay.addWidget(angle_lbl)

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
        count_lbl.setObjectName("Muted")
        count_lbl.setFixedWidth(50)
        lay.addWidget(count_lbl)

        # Organise button
        org_btn = QPushButton("📦 整理")
        org_btn.setObjectName("Primary")
        org_btn.setFixedHeight(26)
        org_btn.setToolTip("归档 JPG → ZIP，按设置删除 JPG")
        org_btn.clicked.connect(lambda: self.organise_clicked.emit(self._group.group_index))
        lay.addWidget(org_btn)

        # Undo button
        undo_btn = QPushButton("↩ 撤销")
        undo_btn.setFixedHeight(26)
        undo_btn.setToolTip("解除合成关联（TIFF 移到 _retired-tiff/，不删除）")
        undo_btn.clicked.connect(lambda: self.undo_clicked.emit(self._group.group_index))
        lay.addWidget(undo_btn)


# ── Draft group ───────────────────────────────────────────────────────────────

class _DraftGroupRow(QFrame):
    """Editable draft group card (angle label + drag-reorderable JPG list)."""

    compose_clicked = pyqtSignal(int)    # group_index
    label_changed = pyqtSignal(int, str) # group_index, new_label
    jpg_removed = pyqtSignal(int, str)   # group_index, jpg_path

    def __init__(self, group: "Group", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self._group = group
        self._setup_ui()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(4)

        # Header row: angle label edit + compose button
        header = QHBoxLayout()

        lbl_prefix = QLabel(f"组 {self._group.group_index}  ")
        lbl_prefix.setObjectName("Muted")
        header.addWidget(lbl_prefix)

        self._label_edit = QLineEdit(self._group.angle_label or "")
        self._label_edit.setPlaceholderText("角度标签（如：正面、背面）")
        self._label_edit.setFixedHeight(26)
        self._label_edit.textEdited.connect(
            lambda t: self.label_changed.emit(self._group.group_index, t)
        )
        header.addWidget(self._label_edit)

        compose_btn = QPushButton("⚡ 合成")
        compose_btn.setObjectName("Primary")
        compose_btn.setFixedHeight(26)
        compose_btn.setToolTip("调用 Helicon Focus CLI 合成该组 JPG")
        compose_btn.clicked.connect(lambda: self.compose_clicked.emit(self._group.group_index))
        header.addWidget(compose_btn)
        root.addLayout(header)

        # JPG list (drag-reorderable)
        self._jpg_list = QListWidget()
        self._jpg_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self._jpg_list.setMaximumHeight(100)
        self._jpg_list.setToolTip("拖拽可重新排序；双击删除（暂不支持，用右键）")
        for p in self._group.jpg_paths:
            item = QListWidgetItem(Path(p).name)
            item.setData(Qt.ItemDataRole.UserRole, p)
            item.setToolTip(p)
            self._jpg_list.addItem(item)

        if not self._group.jpg_paths:
            empty = QListWidgetItem("（空组 — 从监控区拖入 JPG）")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self._jpg_list.addItem(empty)

        root.addWidget(self._jpg_list)

        # JPG count line
        count_lbl = QLabel(f"{len(self._group.jpg_paths)} 张 JPG")
        count_lbl.setObjectName("Muted")
        root.addWidget(count_lbl)


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

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._uid: Optional[str] = None
        self._grouping: Optional["SpecimenGrouping"] = None
        self._setup_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 6, 8, 6)
        title = QLabel("分组工具")
        title.setObjectName("Section")
        toolbar.addWidget(title)
        toolbar.addStretch()

        self._uid_label = QLabel("— 无激活标本 —")
        self._uid_label.setObjectName("Muted")
        toolbar.addWidget(self._uid_label)

        add_btn = QPushButton("+ 新组")
        add_btn.setFixedHeight(26)
        add_btn.clicked.connect(self._add_group)
        toolbar.addWidget(add_btn)
        root.addLayout(toolbar)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: rgba(145,182,181,0.13);")
        root.addWidget(line)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._content = QWidget()
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.setContentsMargins(8, 8, 8, 8)
        self._content_lay.setSpacing(6)
        self._content_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._content)
        root.addWidget(scroll)

        # Empty state
        self._empty_lbl = QLabel("激活一个标本后查看或编辑分组")
        self._empty_lbl.setObjectName("Muted")
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._empty_lbl)

    # ── Public API ────────────────────────────────────────────────────────────

    def load_grouping(self, uid: str, grouping: "SpecimenGrouping") -> None:
        """Display all groups for *uid*."""
        self._uid = uid
        self._grouping = grouping
        self._uid_label.setText(uid[:30] + ("…" if len(uid) > 30 else ""))
        self._rebuild()

    def clear(self) -> None:
        self._uid = None
        self._grouping = None
        self._uid_label.setText("— 无激活标本 —")
        self._clear_content()
        self._empty_lbl.show()

    # ── Internal ──────────────────────────────────────────────────────────────

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
            sec_lbl = QLabel("📋  未合成组")
            sec_lbl.setObjectName("Muted")
            self._content_lay.addWidget(sec_lbl)
            for g in draft:
                row = _DraftGroupRow(g, self)
                row.compose_clicked.connect(self._on_compose)
                row.label_changed.connect(self._on_label_changed)
                self._content_lay.addWidget(row)

        if composed:
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setStyleSheet("color: rgba(145,182,181,0.13);")
            self._content_lay.addWidget(sep)
            sec_lbl2 = QLabel("✅  已合成")
            sec_lbl2.setObjectName("Muted")
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
