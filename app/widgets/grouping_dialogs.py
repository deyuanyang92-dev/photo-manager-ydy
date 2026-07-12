"""Picker dialogs used by the specimen grouping editor.

2026-07-12 (卡顿修复 · 接线): 两个 picker 的行缩略图改走
:class:`~app.widgets.grouping_media_helpers.MediaThumbnailLoader` —— 每个对话框
自己持有一个 loader(单条长生命周期解码线程),`_apply_thumbnail()` 只**派发**请求,
GUI 线程零解码;结果由 worker 以 QImage 发回主线程再转 QPixmap 贴图。
对话框 close/accept/reject 时必须 `shutdown()`(quit()+wait()),否则悬挂线程会挂住
整个 pytest 进程。同步版 `_media_thumbnail_pixmap` 仅在 loader 缺席时兜底(§7)。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.widgets.grouping_media_helpers import (
    _JPG_EXTS,
    _TIFF_EXTS,
    _RELATED_PICKER_DEFAULT_CHECK_SECONDS,
    MediaThumbnailLoader,
    _apply_thumb_to_label,
    _media_entries_in_dir,
    _media_thumbnail_pixmap,
    _mtime_text,
    _name_matches_any_term,
    _normal_dir,
)

_THUMB_BOX = 64  # 行缩略图边长(与 _thumbnail_label 的 68px QLabel 一致)


class _ThumbLoaderMixin:
    """异步行缩略图的公共接线(两个 picker 共用)。

    * `_init_thumb_loader()` —— 建 loader,必须在第一次 `_populate()` 之前调用。
    * `_apply_thumbnail()` —— 只派发,不解码;返回 True 表示"真派了一次解码"
      (缓存命中同帧上屏,不计入批次配额 —— 与 monitor_panel 的节流口径一致)。
    * `_shutdown_thumbs()` —— close/accept/reject 都要走到,quit()+wait() 解码线程。
    """

    _thumbs: MediaThumbnailLoader | None = None

    def _init_thumb_loader(self) -> None:
        self._thumb_queue: list[tuple[QLabel, str, str]] = []
        self._thumbs = MediaThumbnailLoader(self, size=_THUMB_BOX)

    def _reset_thumb_queue(self) -> None:
        """重建表格前调用:清队列 + bump generation,丢弃在途回包。"""
        self._thumb_queue.clear()
        if self._thumbs is not None:
            self._thumbs.reset()

    def _apply_thumbnail(self, label: "QLabel", path: str, kind: str) -> bool:
        """派发一行的缩略图请求。返回 True = 投给了解码线程(未命中缓存)。"""
        loader = self._thumbs
        try:
            if loader is None:  # §7 兜底:loader 不在(已 shutdown)时退回同步版
                _apply_thumb_to_label(label, _media_thumbnail_pixmap(path, _THUMB_BOX), kind)
                return False
            served_from_cache = loader.request(
                label, path, size=_THUMB_BOX, fallback_text=kind or "IMG",
            )
        except RuntimeError:
            return False  # 行已被回收,底层 C++ QLabel 已析构
        return not served_from_cache

    def _load_next_thumbnail_batch(self, batch: int = 4) -> None:
        """定时器槽(主线程):每跳最多派发 *batch* 个解码请求,自己绝不解码。"""
        dispatched = 0
        while self._thumb_queue and dispatched < batch:
            label, path, kind = self._thumb_queue.pop(0)
            if self._apply_thumbnail(label, path, kind):
                dispatched += 1
        if self._thumb_queue:
            QTimer.singleShot(10, self._load_next_thumbnail_batch)

    def _load_all_thumbnails_now(self, timeout_ms: int = 15000) -> None:
        """全部派发并等待回包(解码仍在子线程)。测试/一次性场景用。"""
        while self._thumb_queue:
            label, path, kind = self._thumb_queue.pop(0)
            self._apply_thumbnail(label, path, kind)
        loader = self._thumbs
        if loader is None:
            return
        deadline = time.monotonic() + timeout_ms / 1000.0
        while loader.pending_count() and time.monotonic() < deadline:
            QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)

    def _shutdown_thumbs(self) -> None:
        # 保留 loader 引用（不置 None）: shutdown() 后它自己会把 request() 降级成占位符,
        # 而置 None 会让 _apply_thumbnail 退回**同步解码**兜底 —— 关窗途中若还有行在派发,
        # 那等于又把解码搬回 GUI 线程。shutdown() 幂等, 重复调用无害。
        queue = getattr(self, "_thumb_queue", None)
        if queue is not None:
            queue.clear()
        loader = self._thumbs
        if loader is not None:
            loader.shutdown()

    # QDialog 的三个出口(accept/reject 都汇到 done();用户点 X 走 closeEvent)。
    def done(self, result: int) -> None:  # type: ignore[override]
        self._shutdown_thumbs()
        super().done(result)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._shutdown_thumbs()
        super().closeEvent(event)


class _MediaLocationPickerDialog(_ThumbLoaderMixin, QDialog):
    """File/folder browser for directly selecting JPG/TIF or a scan folder."""

    def __init__(
        self,
        title: str,
        start: str = "",
        *,
        priority_terms: list[str] | None = None,
        file_exts: set[str] | None = None,
        shortcuts: list[tuple[str, str]] | None = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._priority_terms = [t for t in (priority_terms or []) if str(t or "").strip()]
        self._file_exts = {e.lower() for e in (file_exts or (_JPG_EXTS | _TIFF_EXTS))}
        self._shortcuts = self._normal_shortcuts(shortcuts or [])
        self._init_thumb_loader()  # 必须早于第一次 _populate()
        self._selected_paths: list[str] = []
        self._selected_folder = ""
        self._current_dir = self._normal_start_dir(start)

        self.setWindowTitle(title)
        self.setMinimumSize(1040, 640)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("Section")
        root.addWidget(title_lbl)

        path_row = QHBoxLayout()
        self._path_edit = QLineEdit(str(self._current_dir))
        self._path_edit.returnPressed.connect(self._go_to_path_text)
        path_row.addWidget(self._path_edit, stretch=1)
        go_btn = QPushButton("转到")
        go_btn.setObjectName("Ghost")
        go_btn.clicked.connect(self._go_to_path_text)
        path_row.addWidget(go_btn)
        up_btn = QPushButton("上一级")
        up_btn.setObjectName("Ghost")
        up_btn.clicked.connect(self._go_up)
        path_row.addWidget(up_btn)
        use_current = QPushButton("筛选当前目录")
        use_current.setObjectName("Ghost")
        use_current.clicked.connect(self._accept_current_dir)
        path_row.addWidget(use_current)
        root.addLayout(path_row)

        if self._shortcuts:
            shortcut_row = QHBoxLayout()
            shortcut_lbl = QLabel("常用位置")
            shortcut_lbl.setObjectName("Muted")
            shortcut_row.addWidget(shortcut_lbl)
            for label, path in self._shortcuts[:8]:
                btn = QPushButton(label)
                btn.setObjectName("Ghost")
                btn.setToolTip("")
                btn.clicked.connect(lambda _checked=False, p=path: self._go_to_dir(p))
                shortcut_row.addWidget(btn)
            shortcut_row.addStretch()
            root.addLayout(shortcut_row)

        hint = QLabel("双击文件夹进入；多选 JPG/TIF 后确定会直接加入；进入目标目录后点“筛选当前目录”再按编号筛选。")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["缩略图", "名称", "类型", "修改时间", "大小"])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(74)
        header = self._table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(0, 76)
        self._table.setColumnWidth(1, 520)
        self._table.setColumnWidth(2, 90)
        self._table.setColumnWidth(3, 180)
        self._table.setColumnWidth(4, 90)
        self._table.itemDoubleClicked.connect(self._on_item_activated)
        from app.utils.tooltip_policy import suppress_popup_tooltip

        suppress_popup_tooltip(self._table)
        root.addWidget(self._table, stretch=1)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setText("确定")
        self._buttons.accepted.connect(self._accept_selection)
        self._buttons.rejected.connect(self.reject)
        root.addWidget(self._buttons)

        self._populate()
        # §7 原来在这里排队首批缩略图;现在 _populate() 自己排(换目录后也能排上,
        # 旧代码换目录时批次链已耗尽 → 新目录缩略图不会加载)。
        # QTimer.singleShot(0, self._load_next_thumbnail_batch)

    def selected_folder(self) -> str:
        return self._selected_folder

    def selected_paths(self) -> list[str]:
        return list(self._selected_paths)

    @staticmethod
    def _normal_start_dir(start: str) -> Path:
        try:
            path = Path(start).expanduser() if start else Path.cwd()
            if path.is_file():
                path = path.parent
            if path.is_dir():
                return path
        except Exception:
            pass
        return Path.cwd()

    @staticmethod
    def _normal_shortcuts(shortcuts: list[tuple[str, str]]) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for label, path in shortcuts:
            p = _normal_dir(path)
            if p is None:
                continue
            key = str(p.resolve()).casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append((str(label or p.name or p), str(p)))
        return out

    def _entry_info(self, row: int) -> dict | None:
        item = self._table.item(row, 1)
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return data if isinstance(data, dict) else None

    def _current_info(self) -> dict | None:
        row = self._table.currentRow()
        if row < 0:
            return None
        return self._entry_info(row)

    def _kind_for_file(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in _TIFF_EXTS:
            return "TIF"
        if suffix in _JPG_EXTS:
            return "JPG"
        return suffix.lstrip(".").upper()

    def _thumbnail_label(self, path: Path, *, is_dir: bool) -> QLabel:
        label = QLabel()
        label.setFixedSize(68, 68)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setObjectName("RelatedThumb")
        label.setToolTip("")
        if is_dir:
            label.setText("DIR")
            label.setProperty("hasThumbnail", False)
            return label
        kind = self._kind_for_file(path)
        label.setText(kind)
        label.setProperty("hasThumbnail", False)
        self._thumb_queue.append((label, str(path), kind))
        return label

    # ── §7 原同步实现(GUI 线程整解码,冷 TIFF 卡死对话框)—— 注释保留,勿删 ──
    #   缩略图/批次逻辑现由 _ThumbLoaderMixin 统一提供(只派发,不解码)。
    #
    # def _apply_thumbnail(self, label: QLabel, path: str, kind: str) -> None:
    #     pixmap = _media_thumbnail_pixmap(path, 64)
    #     if pixmap is not None and not pixmap.isNull():
    #         label.setPixmap(pixmap)
    #         label.setProperty("hasThumbnail", True)
    #     else:
    #         label.setText(kind or "IMG")
    #         label.setProperty("hasThumbnail", False)
    #
    # def _load_next_thumbnail_batch(self) -> None:
    #     processed = 0
    #     while self._thumb_queue and processed < 4:
    #         label, path, kind = self._thumb_queue.pop(0)
    #         self._apply_thumbnail(label, path, kind)
    #         processed += 1
    #     if self._thumb_queue:
    #         QTimer.singleShot(10, self._load_next_thumbnail_batch)
    #
    # def _load_all_thumbnails_now(self) -> None:
    #     while self._thumb_queue:
    #         label, path, kind = self._thumb_queue.pop(0)
    #         self._apply_thumbnail(label, path, kind)

    def _selected_infos(self) -> list[dict]:
        rows = sorted({index.row() for index in self._table.selectionModel().selectedRows()})
        if not rows:
            current = self._current_info()
            return [current] if current else []
        infos: list[dict] = []
        for row in rows:
            info = self._entry_info(row)
            if info:
                infos.append(info)
        return infos

    def _populate(self) -> None:
        self._table.setSortingEnabled(False)
        # 换目录 = 旧行全部作废:清队列 + bump generation,在途回包一律丢弃。
        self._reset_thumb_queue()
        self._path_edit.setText(str(self._current_dir))
        entries: list[tuple[bool, bool, float, Path]] = []
        try:
            children = list(self._current_dir.iterdir())
        except OSError:
            children = []
        for child in children:
            is_dir = child.is_dir()
            if not is_dir and child.suffix.lower() not in self._file_exts:
                continue
            try:
                mtime = child.stat().st_mtime
            except OSError:
                mtime = 0
            related = _name_matches_any_term(child.name, self._priority_terms)
            entries.append((is_dir, related, mtime, child))
        entries.sort(
            key=lambda item: (
                not item[0],
                not item[1],
                -float(item[2] or 0),
                item[3].name.casefold(),
            )
        )

        self._table.setRowCount(len(entries))
        for row, (is_dir, _related, mtime, path) in enumerate(entries):
            self._table.setCellWidget(row, 0, self._thumbnail_label(path, is_dir=is_dir))
            name_item = QTableWidgetItem(path.name)
            name_item.setToolTip("")
            name_item.setData(
                Qt.ItemDataRole.UserRole,
                {"path": str(path), "is_dir": is_dir},
            )
            self._table.setItem(row, 1, name_item)

            kind = "文件夹" if is_dir else self._kind_for_file(path)
            self._table.setItem(row, 2, QTableWidgetItem(kind))
            self._table.setItem(row, 3, QTableWidgetItem(_mtime_text(mtime)))

            size = ""
            if not is_dir:
                try:
                    size = f"{path.stat().st_size / 1024 / 1024:.1f} MB"
                except OSError:
                    size = ""
            self._table.setItem(row, 4, QTableWidgetItem(size))
            self._table.setRowHeight(row, 74)

        self._table.clearSelection()
        self._table.setCurrentCell(-1, -1)
        self._table.setSortingEnabled(True)
        self._table.sortItems(3, Qt.SortOrder.DescendingOrder)
        QTimer.singleShot(0, self._load_next_thumbnail_batch)

    def _go_up(self) -> None:
        parent = self._current_dir.parent
        if parent != self._current_dir and parent.is_dir():
            self._current_dir = parent
            self._populate()

    def _go_to_dir(self, path: str) -> None:
        target = _normal_dir(path)
        if target is None:
            return
        self._current_dir = target
        self._populate()

    def _go_to_path_text(self) -> None:
        self._go_to_dir(self._path_edit.text().strip())

    def _accept_current_dir(self) -> None:
        self._selected_paths = []
        self._selected_folder = str(self._current_dir)
        self.accept()

    def _on_item_activated(self, item: QTableWidgetItem) -> None:
        info = self._entry_info(item.row())
        if not info:
            return
        path = Path(str(info.get("path") or ""))
        if info.get("is_dir") and path.is_dir():
            self._current_dir = path
            self._populate()
            return
        self._selected_paths = [str(path)]
        self._selected_folder = ""
        self.accept()

    def _accept_selection(self) -> None:
        infos = self._selected_infos()
        if not infos:
            self._selected_paths = []
            self._selected_folder = str(self._current_dir)
            self.accept()
            return

        file_paths = [
            str(Path(str(info.get("path") or "")))
            for info in infos
            if not info.get("is_dir")
        ]
        if file_paths:
            self._selected_paths = file_paths
            self._selected_folder = ""
            self.accept()
            return

        path = Path(str(infos[0].get("path") or ""))
        self._selected_paths = []
        self._selected_folder = str(path)
        self.accept()


def _pick_media_paths_or_folder(
    parent: Optional[QWidget],
    caption: str,
    *,
    start: str = "",
    priority_terms: list[str] | None = None,
    file_exts: set[str] | None = None,
    shortcuts: list[tuple[str, str]] | None = None,
) -> tuple[list[str], str]:
    dlg = _MediaLocationPickerDialog(
        caption,
        start=start,
        priority_terms=priority_terms,
        file_exts=file_exts,
        shortcuts=shortcuts,
        parent=parent,
    )
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return [], ""
    return dlg.selected_paths(), dlg.selected_folder()


def _pick_media_location_folder(
    parent: Optional[QWidget],
    caption: str,
    *,
    start: str = "",
    priority_terms: list[str] | None = None,
    file_exts: set[str] | None = None,
    shortcuts: list[tuple[str, str]] | None = None,
) -> str:
    _paths, folder = _pick_media_paths_or_folder(
        parent,
        caption,
        start=start,
        priority_terms=priority_terms,
        file_exts=file_exts,
        shortcuts=shortcuts,
    )
    return folder


class _ResultPairPickerDialog(QDialog):
    """Pick one unclaimed TIFF+ZIP result pair from results/."""

    def __init__(self, candidates: list[dict],
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._all_candidates = list(candidates or [])
        self._selected: dict | None = None
        self.setWindowTitle("关联成品")
        self.setMinimumSize(760, 460)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        title = QLabel("选择未关联的 TIF + ZIP 成品")
        title.setObjectName("Section")
        root.addWidget(title)

        hint = QLabel("已被其它编号登记的成品默认隐藏，避免重复关联。")
        hint.setObjectName("MutedSmall")
        root.addWidget(hint)

        self._show_associated = QCheckBox("显示已关联成品（只读灰显）")
        self._show_associated.toggled.connect(self._populate)
        root.addWidget(self._show_associated)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.currentItemChanged.connect(lambda *_: self._sync_buttons())
        self._list.itemDoubleClicked.connect(lambda _item: self._accept_selected())
        root.addWidget(self._list, stretch=1)

        self._empty = QLabel("")
        self._empty.setObjectName("Muted")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        self._empty.hide()
        root.addWidget(self._empty)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self._accept_selected)
        self._buttons.rejected.connect(self.reject)
        root.addWidget(self._buttons)

        self._populate()

    def selected_pair(self) -> dict | None:
        return dict(self._selected) if self._selected else None

    def _populate(self) -> None:
        self._list.clear()
        show_associated = self._show_associated.isChecked()
        visible = [
            c for c in self._all_candidates
            if show_associated or not c.get("associated")
        ]
        for candidate in visible:
            self._add_candidate(candidate)
        if not self._all_candidates:
            text = "results/ 中没有找到同名的 TIF + ZIP 成品。"
        elif not visible:
            text = "所有 TIF + ZIP 成品都已经关联到编号。勾选上方选项可查看。"
        else:
            text = ""
        self._empty.setText(text)
        self._empty.setVisible(bool(text))
        self._list.setVisible(bool(visible))
        self._sync_buttons()

    def _add_candidate(self, candidate: dict) -> None:
        owner = str(candidate.get("associated_uid") or "")
        tiff_name = Path(str(candidate.get("tiff") or "")).name
        zip_name = Path(str(candidate.get("zip") or "")).name
        status = f"已关联：{owner}" if owner else "未关联"
        item = QListWidgetItem(f"{status}\n{tiff_name}\n{zip_name}")
        item.setData(Qt.ItemDataRole.UserRole, candidate)
        if owner:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            item.setForeground(Qt.GlobalColor.gray)
            item.setToolTip("")
        else:
            item.setToolTip("")
        self._list.addItem(item)

    def _current_candidate(self) -> dict | None:
        item = self._list.currentItem()
        if item is None:
            return None
        candidate = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(candidate, dict) or candidate.get("associated"):
            return None
        return candidate

    def _sync_buttons(self) -> None:
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setEnabled(self._current_candidate() is not None)

    def _accept_selected(self) -> None:
        candidate = self._current_candidate()
        if candidate is None:
            return
        self._selected = dict(candidate)
        self.accept()


class _RelatedFilesPickerDialog(_ThumbLoaderMixin, QDialog):
    """App-owned picker for current UID related JPG/TIFF files."""

    def __init__(
        self,
        uid: str,
        folder: str,
        candidates: list[dict],
        *,
        all_candidates: list[dict] | None = None,
        all_candidates_loader: Callable[[], list[dict]] | None = None,
        title: str | None = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._block_candidates = list(candidates or [])
        self._all_candidates = list(all_candidates or [])
        self._all_candidates_loader = all_candidates_loader
        self._candidates = list(self._block_candidates)
        self._preserve_checked_paths: set[str] | None = None
        self._init_thumb_loader()  # 必须早于第一次 _populate()
        display_title = title or f"{uid} 相关文件"
        self.setWindowTitle(display_title)
        self.setMinimumSize(860, 540)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        title_lbl = QLabel(display_title)
        title_lbl.setObjectName("Section")
        root.addWidget(title_lbl)

        hint = QLabel(
            f"{folder}\n"
            "已按修改时间排序；按“先拍 JPG，再合成 TIF”的时间块识别。"
            " 选择某个 TIF 时，会勾选它前面到上一个 TIF 之间的 JPG。"
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        btn_row = QHBoxLayout()
        select_all = QPushButton("全选")
        select_all.setObjectName("Ghost")
        select_all.clicked.connect(lambda: self._set_all_checked(True))
        btn_row.addWidget(select_all)
        clear = QPushButton("清空")
        clear.setObjectName("Ghost")
        clear.clicked.connect(lambda: self._set_all_checked(False))
        btn_row.addWidget(clear)
        if self._all_candidates or self._all_candidates_loader is not None:
            block_btn = QPushButton("时间块")
            block_btn.setObjectName("Ghost")
            block_btn.clicked.connect(lambda: self._replace_candidates(self._block_candidates))
            btn_row.addWidget(block_btn)
            all_btn = QPushButton("全部时间线")
            all_btn.setObjectName("Ghost")
            all_btn.clicked.connect(self._show_all_timeline)
            btn_row.addWidget(all_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(["", "缩略图", "文件名", "类型", "修改时间", "距TIF"])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(74)
        header = self._table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(1, 78)
        self._table.setColumnWidth(2, 470)
        self._table.setColumnWidth(3, 80)
        self._table.setColumnWidth(4, 170)
        self._table.setColumnWidth(5, 90)
        from app.utils.tooltip_policy import suppress_popup_tooltip

        suppress_popup_tooltip(self._table)
        root.addWidget(self._table, stretch=1)

        self._syncing_checks = False
        self._populate()
        self._table.itemChanged.connect(self._on_check_changed)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        root.addWidget(self._buttons)

    def selected_paths(self) -> list[str]:
        paths: list[str] = []
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                data = item.data(Qt.ItemDataRole.UserRole)
                path = data.get("path") if isinstance(data, dict) else data
                if path:
                    paths.append(str(path))
        return paths

    def _candidate_for_row(self, row: int) -> dict:
        check_item = self._table.item(row, 0)
        if check_item is not None:
            data = check_item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, dict):
                return data
        if 0 <= row < len(self._candidates):
            return self._candidates[row]
        return {}

    def _replace_candidates(self, candidates: list[dict]) -> None:
        self._preserve_checked_paths = set(self.selected_paths())
        self._candidates = list(candidates or [])
        self._populate()

    def _show_all_timeline(self) -> None:
        if not self._all_candidates and self._all_candidates_loader is not None:
            self._all_candidates = list(self._all_candidates_loader() or [])
        self._replace_candidates(self._all_candidates)

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self._syncing_checks = True
        try:
            for row in range(self._table.rowCount()):
                item = self._table.item(row, 0)
                if item is not None:
                    item.setCheckState(state)
        finally:
            self._syncing_checks = False

    def _default_anchor_path(self) -> str:
        for item in self._candidates:
            if item.get("anchor"):
                path = str(item.get("path") or "")
                if path:
                    return path
        for item in self._candidates:
            if str(item.get("kind") or "").upper() == "TIF":
                path = str(item.get("path") or "")
                if path:
                    return path
        return ""

    def _checked_for_anchor(self, item: dict, anchor_path: str) -> bool:
        if not anchor_path:
            return str(item.get("kind") or "").upper() != "TIF"
        path = str(item.get("path") or "")
        if path == anchor_path:
            return True
        if "default_related" in item:
            return (
                bool(item.get("default_related"))
                and str(item.get("nearest_anchor") or "") == anchor_path
            )
        return (
            str(item.get("kind") or "").upper() != "TIF"
            and str(item.get("nearest_anchor") or "") == anchor_path
            and float(item.get("nearest_seconds") or 0)
            <= _RELATED_PICKER_DEFAULT_CHECK_SECONDS
        )

    def _select_anchor_group(self, anchor_path: str) -> None:
        if not anchor_path:
            return
        self._syncing_checks = True
        try:
            for row in range(self._table.rowCount()):
                candidate = self._candidate_for_row(row)
                item = self._table.item(row, 0)
                if item is None:
                    continue
                checked = self._checked_for_anchor(candidate, anchor_path)
                item.setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
        finally:
            self._syncing_checks = False

    def _on_check_changed(self, item: QTableWidgetItem) -> None:
        if self._syncing_checks or item.column() != 0:
            return
        if item.checkState() != Qt.CheckState.Checked:
            return
        row = item.row()
        if row < 0:
            return
        candidate = self._candidate_for_row(row)
        if str(candidate.get("kind") or "").upper() != "TIF":
            return
        self._select_anchor_group(str(candidate.get("path") or ""))

    def _thumbnail_label(self, item: dict) -> QLabel:
        path = str(item.get("path") or "")
        kind = str(item.get("kind") or "").upper()
        label = QLabel()
        label.setFixedSize(68, 68)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setObjectName("RelatedThumb")
        label.setToolTip("")
        label.setText(kind or "IMG")
        label.setProperty("hasThumbnail", False)
        if path:
            self._thumb_queue.append((label, path, kind))
        return label

    # ── §7 原同步实现(GUI 线程整解码)—— 注释保留,勿删;现由 _ThumbLoaderMixin 提供 ──
    #
    # def _apply_thumbnail(self, label: QLabel, path: str, kind: str) -> None:
    #     pixmap = _media_thumbnail_pixmap(path, 64)
    #     if pixmap is not None and not pixmap.isNull():
    #         label.setPixmap(pixmap)
    #         label.setProperty("hasThumbnail", True)
    #     else:
    #         label.setText(kind or "IMG")
    #         label.setProperty("hasThumbnail", False)
    #
    # def _load_next_thumbnail_batch(self) -> None:
    #     processed = 0
    #     while self._thumb_queue and processed < 2:
    #         label, path, kind = self._thumb_queue.pop(0)
    #         self._apply_thumbnail(label, path, kind)
    #         processed += 1
    #     if self._thumb_queue:
    #         QTimer.singleShot(10, self._load_next_thumbnail_batch)
    #
    # def _load_all_thumbnails_now(self) -> None:
    #     while self._thumb_queue:
    #         label, path, kind = self._thumb_queue.pop(0)
    #         self._apply_thumbnail(label, path, kind)

    def _distance_text(self, item: dict) -> tuple[str, str]:
        if item.get("anchor"):
            return "TIF", "匹配编号的 TIF"
        seconds = int(item.get("nearest_seconds") or 0)
        direction = str(item.get("relative_to_tif") or "")
        prefix = "前" if direction == "before" else "后" if direction == "after" else ""
        text = f"{prefix}{seconds // 60}:{seconds % 60:02d}"
        anchor_name = str(item.get("nearest_anchor_name") or "")
        tooltip = f"距离 {anchor_name} {seconds // 60}分{seconds % 60}秒" if anchor_name else ""
        return text, tooltip

    def _apply_anchor_row_style(self, row: int, candidate: dict) -> None:
        if not candidate.get("anchor"):
            return
        bg = QColor("#edfdf7")
        for col in range(self._table.columnCount()):
            cell = self._table.item(row, col)
            if cell is not None:
                cell.setBackground(bg)

    def _populate(self) -> None:
        self._syncing_checks = True
        preserve = self._preserve_checked_paths
        self._preserve_checked_paths = None
        # 表格重建 = 旧行作废:清队列 + bump generation,在途回包一律丢弃。
        self._reset_thumb_queue()
        try:
            self._table.setSortingEnabled(False)
            self._table.setRowCount(len(self._candidates))
            default_anchor = self._default_anchor_path()
            default_anchor_row = -1
            for row, item in enumerate(self._candidates):
                path = str(item.get("path") or "")
                if path == default_anchor:
                    default_anchor_row = row
                check = QTableWidgetItem("")
                check.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                checked = (
                    path in preserve
                    if preserve is not None else self._checked_for_anchor(item, default_anchor)
                )
                check.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
                check.setData(Qt.ItemDataRole.UserRole, dict(item))
                self._table.setItem(row, 0, check)
                self._table.setCellWidget(row, 1, self._thumbnail_label(item))
                self._table.setRowHeight(row, 74)

                name = QTableWidgetItem(str(item.get("name") or ""))
                name.setToolTip("")
                self._table.setItem(row, 2, name)

                kind = QTableWidgetItem(str(item.get("kind") or ""))
                self._table.setItem(row, 3, kind)

                mtime = QTableWidgetItem(_mtime_text(float(item.get("mtime") or 0)))
                self._table.setItem(row, 4, mtime)

                distance, tooltip = self._distance_text(item)
                distance_item = QTableWidgetItem(distance)
                if tooltip:
                    distance_item.setToolTip("")
                self._table.setItem(row, 5, distance_item)
                self._apply_anchor_row_style(row, item)
        finally:
            self._syncing_checks = False
        self._table.horizontalHeader().setSortIndicator(4, Qt.SortOrder.AscendingOrder)
        self._table.setSortingEnabled(True)
        if default_anchor_row >= 0:
            default_anchor_row = self._row_for_path(default_anchor)
            self._table.setCurrentCell(default_anchor_row, 2)
            anchor_item = self._table.item(default_anchor_row, 2)
            if anchor_item is not None:
                self._table.scrollToItem(
                    anchor_item,
                    QAbstractItemView.ScrollHint.PositionAtCenter,
                )
        QTimer.singleShot(0, self._load_next_thumbnail_batch)

    def _row_for_path(self, path: str) -> int:
        for row in range(self._table.rowCount()):
            candidate = self._candidate_for_row(row)
            if str(candidate.get("path") or "") == path:
                return row
        return -1


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
            item.setToolTip("")
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
        browse_btn.clicked.connect(self._browse_for_tiff_file)
        browse_row.addWidget(browse_btn)
        browse_row.addStretch()
        root.addLayout(browse_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_selected_tiff)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _on_list_double_clicked(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self._path_edit.setText(path)

    def _browse_for_tiff_file(self) -> None:
        from app.utils.ui import get_open_file_name
        path = get_open_file_name(
            self, "选择 TIF 文件", filter="TIFF 文件 (*.tif *.tiff *.TIF *.TIFF)"
        )
        if path:
            self._path_edit.setText(path)

    def _accept_selected_tiff(self) -> None:
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
