"""Row and drop-zone widgets used by the specimen grouping editor."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.config import icons
from app.widgets.grouping_media_helpers import (
    _JPG_EXTS,
    _THUMB_ICON_CACHE,
    _THUMB_ICON_CACHE_LIMIT,
    _TIFF_EXTS,
    _archive_jpg_count,
    _paths_from_mime,
    _resolved_archive_zip,
    _show_path_in_folder,
    _split_media_paths,
)

if TYPE_CHECKING:
    from app.services.grouping_service import Group
    from app.widgets.grouping_panel import GroupingPanel


class _CrossGroupList(QListWidget):
    """QListWidget that supports cross-group JPG drag-drop.

    When a drop arrives from a *different* list, the dragged item is removed
    from the source list and the parent GroupingPanel._persist_grouping_after_editor_change() is
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

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if isinstance(event.source(), _CrossGroupList):
            event.acceptProposedAction()
            return
        jpgs, tiffs = _split_media_paths(_paths_from_mime(event))
        if jpgs or tiffs:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.dragEnterEvent(event)

    def dropEvent(self, event) -> None:
        if not isinstance(event.source(), _CrossGroupList):
            jpgs, tiffs = _split_media_paths(_paths_from_mime(event))
            if jpgs or tiffs:
                if len(tiffs) > 1:
                    from PyQt6.QtWidgets import QMessageBox
                    QMessageBox.warning(
                        self, "拖入文件", "一次只能拖入 1 个 TIFF。"
                    )
                    event.ignore()
                    return
                self._panel.drop_external_files(
                    self._group_index,
                    jpgs,
                    tiffs[0] if tiffs else None,
                )
                event.acceptProposedAction()
                return

        src = event.source()
        if src is self:
            # Same-list reorder — delegate to Qt's default implementation.
            super().dropEvent(event)
            self._panel._persist_grouping_after_editor_change()
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
        new_item.setToolTip("")
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
    link_jpg_clicked = pyqtSignal(int)   # group_index
    undo_clicked = pyqtSignal(int)       # group_index
    selected_changed = pyqtSignal(int, bool)  # group_index, checked
    register_zip_clicked = pyqtSignal(int)  # group_index
    tiff_naming_check_requested = pyqtSignal(str)  # tiff_path
    tiff_delete_requested = pyqtSignal(int)  # group_index

    def __init__(
        self,
        group: "Group",
        parent: Optional[QWidget] = None,
        *,
        selected: bool = False,
        display_number: Optional[int] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._group = group
        self._selected = selected
        self._display_number = display_number or (group.group_index + 1)
        self._tiff_path = self._group.composed_tiff_path or ""
        self._setup_ui()

    def _setup_ui(self) -> None:
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(10)

        sel = QCheckBox()
        sel.setToolTip("勾选后，顶部合成/整理只处理勾选组；未勾选任何组时处理全部。")
        sel.setChecked(self._selected)
        sel.toggled.connect(
            lambda checked: self.selected_changed.emit(self._group.group_index, checked)
        )
        lay.addWidget(sel)

        # Composed-state chip + angle label
        chip = QLabel(self._group.angle_label or f"组{self._display_number}")
        chip.setObjectName("ChipTiff")
        lay.addWidget(chip)

        # TIFF basename
        tiff_path = self._tiff_path
        tiff_name = Path(tiff_path).name if tiff_path else "(无 TIFF)"
        tiff_lbl = QLabel(tiff_name)
        tiff_lbl.setObjectName("Mono")
        tiff_lbl.setToolTip("")
        tiff_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if tiff_path:
            tiff_lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            tiff_lbl.customContextMenuRequested.connect(
                lambda pos, label=tiff_lbl: self._show_tiff_menu(
                    label.mapToGlobal(pos)
                )
            )
        lay.addWidget(tiff_lbl)

        # JPG / archive state badge.  A composed row can have only a TIFF when
        # the user imported a finished TIFF but has not linked the original JPGs.
        jpg_count = len(self._group.jpg_paths)
        archive_zip = _resolved_archive_zip(self._group)
        archived_count = _archive_jpg_count(archive_zip)
        if archive_zip:
            count_text = (
                f"已归档 {archived_count} JPG"
                if archived_count is not None
                else "已归档 ZIP"
            )
        elif jpg_count:
            count_text = f"{jpg_count} JPG 待整理"
        elif tiff_path:
            count_text = "仅TIF 待整理"
        else:
            count_text = "未关联 JPG"
        count_lbl = QLabel(count_text)
        count_lbl.setObjectName("MutedSmall")
        count_lbl.setToolTip(
            "此组已关联原始 JPG，可整理归档。"
            if jpg_count
            else (
                "已注册 ZIP 归档。"
                if archive_zip
                else "此组只有 TIF，可先整理登记到编号；需要 ZIP 时再关联 JPG。"
            )
        )
        lay.addWidget(count_lbl)

        # Main action button
        needs_jpg_link = bool(tiff_path) and not jpg_count and not archive_zip
        org_btn = QPushButton(
            "已整理" if archive_zip else ("整理TIF" if needs_jpg_link else "整理")
        )
        org_btn.setObjectName("Primary")
        org_btn.setFixedHeight(28)
        icons.set_button_icon(
            org_btn,
            "mdi6.file-image-outline" if needs_jpg_link else "mdi6.folder-zip-outline",
            color=icons.TONE_ON_ACCENT,
            size=14,
        )
        org_btn.setEnabled((bool(jpg_count) and not bool(archive_zip)) or needs_jpg_link)
        org_btn.setToolTip(
            "仅整理登记 TIFF 到当前编号；不生成 ZIP"
            if needs_jpg_link
            else (
                "归档 JPG → ZIP，按设置删除 JPG"
                if jpg_count and not archive_zip
                else (
                    "本组已有 ZIP 归档。"
                    if archive_zip
                    else "无法整理：此组未关联 JPG 原片。"
                )
            )
        )
        org_btn.clicked.connect(lambda: self.organise_clicked.emit(self._group.group_index))
        lay.addWidget(org_btn)

        if needs_jpg_link:
            link_btn = QPushButton("关联JPG")
            link_btn.setObjectName("Ghost")
            link_btn.setFixedHeight(28)
            icons.set_button_icon(
                link_btn, "mdi6.image-plus-outline", color=icons.TONE_MUTED, size=15
            )
            link_btn.setToolTip("按此 TIFF 时间，从原片目录选择并关联 JPG")
            link_btn.clicked.connect(
                lambda: self.link_jpg_clicked.emit(self._group.group_index)
            )
            lay.addWidget(link_btn)

        zip_btn = QPushButton("换ZIP" if archive_zip else "注册ZIP")
        zip_btn.setObjectName("Ghost")
        zip_btn.setFixedHeight(28)
        icons.set_button_icon(zip_btn, "mdi6.folder-zip-outline",
                              color=icons.TONE_MUTED, size=15)
        zip_btn.setToolTip(
            "更换已注册 ZIP 归档（不重新压缩）"
            if archive_zip
            else "注册已有 ZIP 归档（不重新压缩）"
        )
        zip_btn.clicked.connect(
            lambda: self.register_zip_clicked.emit(self._group.group_index)
        )
        lay.addWidget(zip_btn)

        # Undo button
        undo_btn = QPushButton()
        undo_btn.setObjectName("Ghost")
        undo_btn.setFixedSize(30, 28)
        icons.set_button_icon(undo_btn, "mdi6.undo-variant", color=icons.TONE_MUTED, size=15)
        undo_btn.setToolTip(
            "撤销整理：从 ZIP 恢复 JPG，退回待整理，不删除 TIFF"
            if archive_zip
            else "撤销合成：确认后删除 TIFF，并把 JPG 放回自由池"
        )
        undo_btn.clicked.connect(lambda: self.undo_clicked.emit(self._group.group_index))
        lay.addWidget(undo_btn)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        if self._tiff_path:
            self._show_tiff_menu(event.globalPos())
            event.accept()
            return
        super().contextMenuEvent(event)

    def _show_tiff_menu(self, global_pos) -> None:
        path = self._tiff_path
        if not path:
            return
        menu = QMenu(self)
        check_action = menu.addAction("检查 TIF 命名格式")
        copy_action = menu.addAction("复制路径")
        show_action = menu.addAction("在文件夹中显示")
        menu.addSeparator()
        delete_action = menu.addAction("删除 TIF")

        chosen = menu.exec(global_pos)
        if chosen == check_action:
            self.tiff_naming_check_requested.emit(path)
        elif chosen == copy_action:
            QApplication.clipboard().setText(path)
        elif chosen == show_action:
            _show_path_in_folder(path)
        elif chosen == delete_action:
            self.tiff_delete_requested.emit(self._group.group_index)


# ── Draft group ───────────────────────────────────────────────────────────────

class _ThumbAreaResizeHandle(QFrame):
    """Drag vertically to resize the thumbnail grid inside a group card."""

    def __init__(
        self,
        target: QListWidget,
        *,
        min_h: int = 88,
        max_h: int = 320,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._target = target
        self._min_h = min_h
        self._max_h = max_h
        self._drag_y = 0
        self._start_h = 0
        self.setObjectName("ThumbResizeGrip")
        self.setFixedHeight(8)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setToolTip("")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_y = int(event.globalPosition().y())
            self._start_h = self._target.height()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() & Qt.MouseButton.LeftButton:
            delta = int(event.globalPosition().y()) - self._drag_y
            h = max(self._min_h, min(self._max_h, self._start_h + delta))
            self._target.setFixedHeight(h)
            event.accept()
        else:
            super().mouseMoveEvent(event)


class _DraftGroupRow(QFrame):
    """Editable draft group card (angle label + drag-reorderable JPG list)."""

    compose_clicked = pyqtSignal(int)         # group_index
    organise_clicked = pyqtSignal(int)        # group_index — 已关联 TIF 时整理
    label_changed = pyqtSignal(int, str)      # group_index, new_label
    jpg_removed = pyqtSignal(int, str)        # group_index, jpg_path (kept for compat)
    add_selected_to_group = pyqtSignal(int)   # group_index
    jpg_remove_requested = pyqtSignal(int, str)  # group_index, jpg_path
    clear_group_requested = pyqtSignal(int)   # group_index  #cursor
    delete_group_requested = pyqtSignal(int)  # group_index  #cursor
    import_tiff_requested = pyqtSignal(int)   # group_index  #cursor groupingImportTiff
    add_photos_requested = pyqtSignal(int)    # group_index — 从文件夹选图加入
    output_name_changed = pyqtSignal(int, str)  # group_index, 用户编辑的输出命名
    selected_changed = pyqtSignal(int, bool)  # group_index, checked
    tiff_naming_check_requested = pyqtSignal(str)  # tiff_path
    tiff_delete_requested = pyqtSignal(int)  # group_index

    def __init__(self, group: "Group", parent: Optional[QWidget] = None,
                 panel: Optional["GroupingPanel"] = None,
                 selected: bool = False,
                 display_number: Optional[int] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self._group = group
        self._panel = panel
        self._selected = selected
        self._display_number = display_number or (group.group_index + 1)
        self._setup_ui()

    # 横向胶片条：每组一个固定宽度的窄竖卡片，卡内 = 角度名 / JPG缩略图网格 /
    # 张数+输出名 / [合成] / 小动作按钮。多组并排横向滚动，一眼看完所有角度组。
    CARD_W = 234

    def _setup_ui(self) -> None:
        self.setFixedWidth(self.CARD_W)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Row1: 组chip + 角度label
        top = QHBoxLayout()
        top.setSpacing(6)
        sel = QCheckBox()
        sel.setToolTip("勾选后，顶部合成/整理只处理勾选组；未勾选任何组时处理全部。")
        sel.setChecked(self._selected)
        sel.toggled.connect(
            lambda checked: self.selected_changed.emit(self._group.group_index, checked)
        )
        top.addWidget(sel)
        chip = QLabel(f"组{self._display_number}")
        chip.setObjectName(f"GroupChip{(self._display_number - 1) % 4}")
        self._group_number_chip = chip
        top.addWidget(chip)
        self._label_edit = QLineEdit(self._group.angle_label or "")
        self._label_edit.setPlaceholderText("角度")
        self._label_edit.setFixedHeight(26)
        self._label_edit.textEdited.connect(
            lambda t: self.label_changed.emit(self._group.group_index, t)
        )
        top.addWidget(self._label_edit, 1)
        root.addLayout(top)

        # JPG 缩略图网格（IconMode，支持拖拽排序 + 组间拖动）
        self._jpg_list = _CrossGroupList(
            panel=self._panel,
            group_index=self._group.group_index,
            parent=self,
        )
        # Dashed drop-zone look when empty, hairline card when filled (theme QSS).
        has_media = bool(self._group.jpg_paths or self._group.composed_tiff_path)
        self._jpg_list.setObjectName(
            "GroupDropZoneEmpty" if not has_media else "GroupDropZone"
        )
        self._jpg_list.setViewMode(QListWidget.ViewMode.IconMode)
        self._jpg_list.setIconSize(QSize(50, 50))
        self._jpg_list.setGridSize(QSize(58, 60))
        self._jpg_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._jpg_list.setMovement(QListWidget.Movement.Snap)
        self._jpg_list.setWrapping(True)
        self._jpg_list.setSpacing(3)
        self._jpg_list.setUniformItemSizes(True)
        self._jpg_list.setFixedHeight(124)  # 2 行 50px 缩略图；不 stretch —— 否则把下方按钮挤出视口
        self._jpg_list.setToolTip("")
        for p in self._group.jpg_paths:
            item = QListWidgetItem(self._thumb_icon(p), "")
            item.setData(Qt.ItemDataRole.UserRole, p)
            item.setData(Qt.ItemDataRole.UserRole + 1, "jpg")
            item.setToolTip("")
            self._jpg_list.addItem(item)
        tiff_path = self._group.composed_tiff_path or ""
        if tiff_path:
            tiff_item = QListWidgetItem(self._tiff_icon(), Path(tiff_path).name)
            tiff_item.setData(Qt.ItemDataRole.UserRole, tiff_path)
            tiff_item.setData(Qt.ItemDataRole.UserRole + 1, "tiff")
            tiff_item.setToolTip("")
            tiff_item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._jpg_list.addItem(tiff_item)
        if not self._group.jpg_paths and not tiff_path:
            empty = QListWidgetItem("空组\n+ / 拖入\nJPG+TIF")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            empty.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._jpg_list.addItem(empty)
        self._jpg_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._jpg_list.customContextMenuRequested.connect(self._on_jpg_context_menu)
        root.addWidget(self._jpg_list)
        root.addWidget(_ThumbAreaResizeHandle(self._jpg_list, parent=self))

        if tiff_path:
            tiff_name_lbl = QLabel(Path(tiff_path).name)
            tiff_name_lbl.setObjectName("Mono")
            tiff_name_lbl.setWordWrap(True)
            tiff_name_lbl.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            tiff_name_lbl.setToolTip("")
            tiff_name_lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            tiff_name_lbl.customContextMenuRequested.connect(
                lambda pos, label=tiff_name_lbl: self._show_tiff_menu(
                    tiff_path, label.mapToGlobal(pos)
                )
            )
            root.addWidget(tiff_name_lbl)

        # 张数 + 输出名（分两行，避免 TIF 命名被挤没）
        meta = QHBoxLayout()
        meta.setSpacing(4)
        if tiff_path and not self._group.jpg_paths:
            count_text = "TIF 已关联"
        elif tiff_path:
            count_text = f"{len(self._group.jpg_paths)} JPG · TIF"
        else:
            count_text = f"{len(self._group.jpg_paths)} 张"
        count_lbl = QLabel(count_text)
        count_lbl.setObjectName("MutedSmall")
        meta.addWidget(count_lbl)
        meta.addStretch()
        root.addLayout(meta)

        out_row = QHBoxLayout()
        out_row.setSpacing(4)
        out_lbl = QLabel("输出")
        out_lbl.setObjectName("MutedSmall")
        out_row.addWidget(out_lbl)
        self._output_edit = QLineEdit(self._effective_output_name())
        self._output_edit.setPlaceholderText("自动")
        self._output_edit.setFixedHeight(24)
        self._output_edit.setToolTip(
            "本组输出文件名（不含路径与扩展名）。\n"
            "留空 = 自动：有编号→编号-序号；临时分组→组序(1/2…)；导入TIF→原名。"
        )
        self._output_edit.textEdited.connect(
            lambda t: self.output_name_changed.emit(self._group.group_index, t)
        )
        out_row.addWidget(self._output_edit, 1)
        root.addLayout(out_row)

        if tiff_path:
            action_btn = QPushButton("整理")
            action_btn.setObjectName("Primary")
            action_btn.setFixedHeight(28)
            icons.set_button_icon(action_btn, "mdi6.folder-zip-outline",
                                  color=icons.TONE_ON_ACCENT, size=12)
            action_btn.setToolTip(
                f"已关联 TIFF：{Path(tiff_path).name}\n"
                "打包 JPG → 与 TIF 同名的 ZIP"
            )
            action_btn.clicked.connect(
                lambda: self.organise_clicked.emit(self._group.group_index)
            )
        else:
            action_btn = QPushButton("合成")
            action_btn.setObjectName("Primary")
            action_btn.setFixedHeight(28)
            icons.set_button_icon(action_btn, "mdi6.layers-triple-outline",
                                  color=icons.TONE_ON_ACCENT, size=12)
            action_btn.setToolTip("调用 Helicon Focus 合成该组 JPG")
            action_btn.clicked.connect(
                lambda: self.compose_clicked.emit(self._group.group_index)
            )
        root.addWidget(action_btn)

        # 小动作行：添加照片 / 加入所选 / 导入TIF / 清空 / 删组
        actions = QHBoxLayout()
        actions.setSpacing(4)
        add_photos_btn = QPushButton()
        add_photos_btn.setObjectName("Ghost")
        add_photos_btn.setFixedSize(24, 24)
        icons.set_button_icon(add_photos_btn, "mdi6.plus",
                              color=icons.TONE_MUTED, size=14)
        add_photos_btn.setToolTip("从文件夹选择 JPG/TIF 加入此组（TIF → 输出/ZIP 名）")
        add_photos_btn.clicked.connect(
            lambda: self.add_photos_requested.emit(self._group.group_index)
        )
        actions.addWidget(add_photos_btn)
        add_sel_btn = QPushButton("← 加入所选")
        add_sel_btn.setObjectName("Ghost")
        add_sel_btn.setFixedHeight(24)
        add_sel_btn.setToolTip(
            "将监控区选中的 JPG/TIF 加入此分组（TIF 基础名 → 输出/ZIP 名）"
        )
        add_sel_btn.clicked.connect(
            lambda: self.add_selected_to_group.emit(self._group.group_index)
        )
        actions.addWidget(add_sel_btn, 1)
        import_tiff_btn = QPushButton()
        import_tiff_btn.setObjectName("Ghost")
        import_tiff_btn.setFixedSize(24, 24)
        icons.set_button_icon(import_tiff_btn, "mdi6.file-import-outline",
                              color=icons.TONE_MUTED, size=12)
        import_tiff_btn.setToolTip("导入已有 TIFF 关联到本组（跳过 Helicon 直接整理）")
        import_tiff_btn.clicked.connect(
            lambda: self.import_tiff_requested.emit(self._group.group_index)
        )
        actions.addWidget(import_tiff_btn)
        clear_btn = QPushButton()
        clear_btn.setObjectName("Ghost")
        clear_btn.setFixedSize(24, 24)
        icons.set_button_icon(clear_btn, "mdi6.eraser", color=icons.TONE_MUTED, size=12)
        clear_btn.setToolTip("清空此组所有 JPG（不删除文件）")
        clear_btn.clicked.connect(lambda: self.clear_group_requested.emit(self._group.group_index))
        actions.addWidget(clear_btn)
        del_btn = QPushButton()
        del_btn.setObjectName("Ghost")
        del_btn.setFixedSize(24, 24)
        icons.set_button_icon(del_btn, "mdi6.delete-outline", color=icons.TONE_DANGER, size=12)
        del_btn.setToolTip("删除此分组（仅删记录，不删文件）")
        del_btn.clicked.connect(lambda: self.delete_group_requested.emit(self._group.group_index))
        actions.addWidget(del_btn)
        root.addLayout(actions)

    def _thumb_icon(self, path: str):
        """生成 JPG 缩略图 QIcon（QImageReader 按比例缩放解码，快且不爆内存）。
        失败/非图片 → 通用图片图标占位。"""
        from PyQt6.QtGui import QImageReader, QIcon, QPixmap
        try:
            stat = Path(path).stat()
            key = (str(Path(path).resolve()), int(stat.st_mtime_ns), int(stat.st_size))
            cached = _THUMB_ICON_CACHE.get(key)
            if cached is not None:
                _THUMB_ICON_CACHE.move_to_end(key)
                return cached
        except Exception:
            key = None
        try:
            r = QImageReader(path)
            r.setAutoTransform(True)
            sz = r.size()
            if sz.isValid() and sz.width() > 0 and sz.height() > 0:
                sz.scale(58, 58, Qt.AspectRatioMode.KeepAspectRatio)
                r.setScaledSize(sz)
            img = r.read()
            if not img.isNull():
                icon = QIcon(QPixmap.fromImage(img))
                if key is not None:
                    _THUMB_ICON_CACHE[key] = icon
                    _THUMB_ICON_CACHE.move_to_end(key)
                    while len(_THUMB_ICON_CACHE) > _THUMB_ICON_CACHE_LIMIT:
                        _THUMB_ICON_CACHE.popitem(last=False)
                return icon
        except Exception:
            pass
        return icons.icon("mdi6.image-outline")

    def _tiff_icon(self):
        """TIFF 关联项占位图标（组内显示绿色 TIF 标记）。"""
        return icons.icon("mdi6.file-image-outline", color="#36c98f")

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
        """Right-click context menu on a media item."""
        item = self._jpg_list.itemAt(pos)
        if not item:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        kind = str(item.data(Qt.ItemDataRole.UserRole + 1) or "jpg")
        if kind == "tiff":
            self._show_tiff_menu(path, self._jpg_list.mapToGlobal(pos))
            return
        menu = QMenu(self)
        action = menu.addAction("移除此 JPG")
        chosen = menu.exec(self._jpg_list.mapToGlobal(pos))
        if chosen == action:
            self.jpg_remove_requested.emit(self._group.group_index, path)

    def _show_tiff_menu(self, path: str, global_pos) -> None:
        if not path:
            return
        menu = QMenu(self)
        check_action = menu.addAction("检查 TIF 命名格式")
        copy_action = menu.addAction("复制路径")
        show_action = menu.addAction("在文件夹中显示")
        menu.addSeparator()
        delete_action = menu.addAction("删除 TIF")

        chosen = menu.exec(global_pos)
        if chosen == check_action:
            self.tiff_naming_check_requested.emit(path)
        elif chosen == copy_action:
            QApplication.clipboard().setText(path)
        elif chosen == show_action:
            _show_path_in_folder(path)
        elif chosen == delete_action:
            self.tiff_delete_requested.emit(self._group.group_index)


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
        from app.utils.path_utils import normalize_path

        md = event.mimeData()
        if md is None or not md.hasUrls():
            event.ignore()
            return
        paths = [
            normalize_path(u.toLocalFile())
            for u in md.urls()
            if u.isLocalFile() and u.toLocalFile()
        ]
        paths = [p for p in paths if Path(p).is_file()]
        event.acceptProposedAction()
        if paths:
            self.files_dropped.emit(paths)


class _AutoGroupDropZone(QFrame):
    """Staging area: drag JPG/TIF here, then click 自动分组整理."""

    files_dropped = pyqtSignal(list)

    def __init__(self, panel: "GroupingPanel", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._panel = panel
        self.setAcceptDrops(True)
        self.setMinimumHeight(140)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(8)
        self._hint = QLabel(
            "拖入 JPG / TIF 到此处，然后点「自动分组整理」\n"
            "WSL 下 Windows 拖入常无效，请用下方「添加文件 / 文件夹」\n"
            "也可直接点「自动分组整理」选择来源"
        )
        self._hint.setObjectName("Muted")
        self._hint.setWordWrap(True)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._hint)
        self._count_lbl = QLabel("")
        self._count_lbl.setObjectName("Mono")
        self._count_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._count_lbl)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._add_files_btn = QPushButton("添加文件…")
        self._add_files_btn.setObjectName("Outline")
        self._add_files_btn.setFixedHeight(26)
        self._add_files_btn.clicked.connect(self._pick_files)
        btn_row.addWidget(self._add_files_btn)
        self._add_folder_btn = QPushButton("添加文件夹…")
        self._add_folder_btn.setObjectName("Outline")
        self._add_folder_btn.setFixedHeight(26)
        self._add_folder_btn.clicked.connect(self._pick_folder)
        btn_row.addWidget(self._add_folder_btn)
        self._clear_btn = QPushButton("清空暂存")
        self._clear_btn.setObjectName("Ghost")
        self._clear_btn.setFixedHeight(26)
        self._clear_btn.clicked.connect(panel.clear_auto_group_staging)
        self._clear_btn.hide()
        btn_row.addWidget(self._clear_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

    def set_staged_count(self, count: int, paths: list[str] | None = None) -> None:
        if count > 0:
            names = ", ".join(Path(p).name for p in (paths or [])[:8])
            if count > 8:
                names += f" …等 {count} 个"
            self._count_lbl.setText(f"已暂存 {count} 个文件：{names}")
            self._clear_btn.show()
        else:
            self._count_lbl.setText("")
            self._clear_btn.hide()

    def _picker_start_dir(self) -> str:
        project_dir = getattr(self._panel.ctx, "current_project_dir", None)
        if project_dir and Path(project_dir).is_dir():
            return str(project_dir)
        return str(Path.home())

    def _pick_files(self) -> None:
        from app.utils import ui

        paths = ui.get_open_file_names(
            self.window(),
            "选择 JPG / TIF 文件",
            start=self._picker_start_dir(),
            filter="图片 (*.jpg *.jpeg *.tif *.tiff);;所有文件 (*.*)",
            sort_by_mtime=True,
        )
        if paths:
            self.files_dropped.emit(paths)

    def _pick_folder(self) -> None:
        from app.utils import ui

        folder = ui.get_existing_directory(
            self.window(),
            "选择含 JPG / TIF 的文件夹",
            start=self._picker_start_dir(),
        )
        if not folder:
            return
        paths = [
            str(p) for p in Path(folder).iterdir()
            if p.is_file() and p.suffix.lower() in (_JPG_EXTS | _TIFF_EXTS)
        ]
        if paths:
            self.files_dropped.emit(paths)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if _paths_from_mime(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        self.dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = _paths_from_mime(event)
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self.files_dropped.emit(paths)

