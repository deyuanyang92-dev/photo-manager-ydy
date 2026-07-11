"""monitor_panel.py — capture stream for pending JPG/TIFF files.

Faithfully mirrors the web prototype's "目录监控 / 拍照工作台" centre column
(app.js renderDirectoryMonitor):

  ┌ batch/status strip ─ UID + phase + compact JPG/TIFF stats ────────┐
  ├ primary toolbar ─ 刷新 / 添加照片 / 合成 / 自动归档 / 更多 ─┤
  ├ stream header ─ 待处理照片 · 右键处理文件 ───────────────────────┤
  ├ contextual selection bar ─ appears only after selecting files ─────┤
  ├ capture stream ─ Explorer-like file rows with row/right-click menus ┤
  └ unattributed warning ─ ⚠️ N 张 JPG 尚未归入任何编号 ─────────────┘

Each capture row carries:
  - a small file-type icon
  - a mono filename
  - a subdued status/attribution label

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

from collections import OrderedDict, deque
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from PyQt6.QtCore import Qt, QEvent, QMimeData, QSize, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDrag, QKeySequence, QPixmap, QShortcut
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
from app.config.theme import TOKENS
from app.services import grouping_service, activation_service
from app.services.photo_import_service import is_media_path
from app.utils.image_thumbnail import decode_image_thumbnail

if TYPE_CHECKING:
    from app.app_context import AppContext
    from app.services.monitor_service import ScanResult


# File status label: low-saturation chip, mapped to a theme object name.
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
_FILE_THUMB_SIZE = 56
_FILE_TILE_MIN_WIDTH = 190
_FILE_TILE_MAX_COLUMNS = 4
_FILE_THUMB_DECODE_SIZE = 220
_FILE_THUMB_MIN_SIZE = 56
_FILE_THUMB_MAX_SIZE = 180
_FILE_THUMB_STEP = 16
_FILE_THUMB_CACHE_LIMIT = 512
_FILE_THUMB_CACHE: "OrderedDict[tuple[str, int, int], Optional[QPixmap]]" = OrderedDict()
_FILE_THUMB_ICON_CACHE: dict[str, QPixmap] = {}


def clear_file_thumb_cache() -> None:
    """Drop monitor-panel QPixmap thumbnails (e.g. when leaving workbench)."""
    _FILE_THUMB_CACHE.clear()


def _file_thumb_cache_limit() -> int:
    from app.config.memory_profile import MONITOR_THUMB_CACHE_LIMIT
    return MONITOR_THUMB_CACHE_LIMIT


def _file_thumb_cache_key(path: str) -> tuple[str, int, int] | None:
    try:
        p = Path(path)
        st = p.stat()
        return (str(p.resolve()), int(st.st_mtime_ns), int(st.st_size))
    except Exception:
        return None


def _file_thumb_pixmap(path: str) -> Optional[QPixmap]:
    key = _file_thumb_cache_key(path)
    if key is not None and key in _FILE_THUMB_CACHE:
        _FILE_THUMB_CACHE.move_to_end(key)
        return _FILE_THUMB_CACHE[key]

    pm = decode_image_thumbnail(path, _FILE_THUMB_DECODE_SIZE, use_cache=False)
    if key is not None:
        _FILE_THUMB_CACHE[key] = pm
        _FILE_THUMB_CACHE.move_to_end(key)
        while len(_FILE_THUMB_CACHE) > _file_thumb_cache_limit():
            _FILE_THUMB_CACHE.popitem(last=False)
    return pm


def _fallback_thumb_icon(kind: str) -> QPixmap:
    key = "jpg" if kind == "jpg" else "tiff"
    cached = _FILE_THUMB_ICON_CACHE.get(key)
    if cached is not None:
        return cached
    glyph_name = "mdi6.image-outline" if key == "jpg" else "mdi6.file-image-outline"
    pixmap = icons.icon(glyph_name, color=icons.TONE_MUTED).pixmap(22, 22)
    _FILE_THUMB_ICON_CACHE[key] = pixmap
    return pixmap


def _clamp_file_thumb_size(size: int) -> int:
    return max(_FILE_THUMB_MIN_SIZE, min(_FILE_THUMB_MAX_SIZE, int(size)))


def _file_card_height_for_thumb(size: int) -> int:
    return max(78, int(size) + 18)


class _ElideLabel(QLabel):
    """一行提示文字, 窄了自动省略号, 且**永不**把所在栏撑宽。

    场景: 批次条(UID | 状态 | 下一步提示 | JPG/TIFF 计数)在 1366 窄屏上要和
      左右两栏抢宽度。普通 QLabel 的 minimumSizeHint = 整句文字宽度 ->
      直接把中间栏的最小宽度顶起来 -> 三栏挤爆(这次 UI 重构踩过的坑)。
    理由(Fable 5, 2026-07-12): 最小宽度写死一个很小的值, 空间不够就 elide,
      提示是"锦上添花"信息, 宁可被省略也不能挤压主界面。
    """

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self._full = text
        self._short = text
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def setText(self, text: str) -> None:  # type: ignore[override]
        self.set_texts(text or "", text or "")

    def set_texts(self, full: str, short: str = "") -> None:
        """三档降级: 宽 -> 全句; 窄 -> 短句; 再窄 -> 留白。

        理由(Fable 5, 2026-07-12): 单纯 elide 在窄栏上会缩成「下…」这种半截字,
        比不显示还难看。宁可整句让位, 也不留残字。
        """
        self._full = full or ""
        self._short = short or self._full
        self._apply_elide()
        self.updateGeometry()

    def full_text(self) -> str:
        return self._full

    def sizeHint(self) -> QSize:  # type: ignore[override]
        # 必须按**全文**算, 不能按当前(可能已省略的)文字算 —— 否则:
        # elide -> sizeHint 变小 -> 布局给的宽度更小 -> 再 elide ... 一路缩成"下"。
        base = super().sizeHint()
        pad = 12
        return QSize(self.fontMetrics().horizontalAdvance(self._full) + pad, base.height())

    def minimumSizeHint(self) -> QSize:  # type: ignore[override]
        # 0 宽: 挤到极限时这句提示整个让位, 一个像素都不跟 UID / 计数胶囊抢。
        base = super().minimumSizeHint()
        return QSize(0, base.height())

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        avail = max(0, self.width())
        if avail <= 0:  # 还没布局 -> 先按全句(sizeHint 才拿得到正确宽度)
            QLabel.setText(self, self._full)
            return
        fm = self.fontMetrics()
        for candidate in (self._full, self._short):
            if candidate and fm.horizontalAdvance(candidate) <= avail:
                QLabel.setText(self, candidate)
                return
        QLabel.setText(self, "")  # 连短句都放不下 -> 留白, 不留「下…」这种半截字


class _FileCard(QFrame):
    """A capture-stream row: file icon + caption + attribution pill.

    Cards support selection (toggle via _set_selected) for multi-select
    actions. Selected cards use object name "CardSelected" plus a fixed
    left-side selection mark so the state is visible in dense lists.
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
                 on_unassign: Optional[Callable[[str], None]] = None,
                 on_workflow_action: Optional[Callable[[str, str], None]] = None,
                 drag_paths_for: Optional[Callable[[str], list[str]]] = None,
                 defer_thumbnail: bool = False,
                 thumb_size: int = _FILE_THUMB_SIZE) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._entry = entry
        self._active_uid = active_uid
        self._defer_thumbnail = defer_thumbnail
        self._thumbnail_loaded = False
        self._thumbnail_kind = getattr(entry, "kind", "jpg")
        self._selected = False
        self._on_add_to_group = on_add_to_group
        self._on_assign_uid = on_assign_uid
        self._on_unassign = on_unassign
        self._on_workflow_action = on_workflow_action
        self._drag_paths_for = drag_paths_for
        self._thumb_size = _clamp_file_thumb_size(thumb_size)
        self._press_pos = None
        self._drag_started = False
        self._context_menu_from_mouse = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setFixedHeight(_file_card_height_for_thumb(self._thumb_size))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(7, 5, 7, 5)
        lay.setSpacing(7)

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

        self._select_mark = QLabel("✓")
        self._select_mark.setObjectName("FileSelectMark")
        self._select_mark.setProperty("selected", False)
        self._select_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._select_mark.setFixedSize(16, self._thumb_size)
        self._select_mark.setToolTip("未选")
        lay.addWidget(self._select_mark)

        self._thumb_label = QLabel()
        self._thumb_label.setObjectName("FileThumb")
        self._thumb_label.setFixedSize(self._thumb_size, self._thumb_size)
        self._thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_label.setToolTip("")
        if self._defer_thumbnail and kind in {"jpg", "tiff"}:
            self._apply_thumbnail_placeholder(kind)
        else:
            self.load_thumbnail_now()
        lay.addWidget(self._thumb_label)

        # ── Caption + attribution column ──
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(2)

        name = getattr(self._entry, "name", None) or Path(getattr(self._entry, "path", "")).name
        name_lbl = QLabel(name)
        name_lbl.setObjectName("FileName")
        name_lbl.setToolTip("")
        body_lay.addWidget(name_lbl)

        # Attribution/status row.
        attr_row = QHBoxLayout()
        attr_row.setContentsMargins(0, 0, 0, 0)
        attr_row.setSpacing(6)
        if kind == "jpg":
            if uid:
                attr_lbl = QLabel(uid)
                attr_lbl.setObjectName("FileUidActive" if uid == self._active_uid else "FileUid")
            else:
                attr_lbl = QLabel("未归属")
                attr_lbl.setObjectName("FileUidMissing")
        else:
            attr_lbl = QLabel(uid or "只读")
            attr_lbl.setObjectName("FileUidMuted")
        attr_lbl.setToolTip("")
        attr_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        attr_row.addWidget(attr_lbl)
        c_text, c_obj = _CORNER.get(corner_state, _CORNER["raw"])
        state_lbl = QLabel(c_text)
        state_lbl.setObjectName({
            "raw": "FileStateRaw",
            "tiff": "FileStateTiff",
            "archived": "FileStateArchived",
            "composed": "FileStateComposed",
        }.get(corner_state, "FileStateRaw"))
        state_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        attr_row.addWidget(state_lbl)
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

    def _apply_thumbnail_placeholder(self, kind: str) -> None:
        self._thumb_label.setProperty("hasThumbnail", False)
        self._thumb_label.setPixmap(_fallback_thumb_icon(kind))

    def load_thumbnail_now(self) -> None:
        if self._thumbnail_loaded:
            return
        self._thumbnail_loaded = True
        self._apply_thumbnail(self._thumbnail_kind)

    def _apply_thumbnail(self, kind: str) -> None:
        path = getattr(self._entry, "path", "")
        pm = _file_thumb_pixmap(path) if kind in {"jpg", "tiff"} else None
        if pm is not None and not pm.isNull():
            self._thumb_label.setProperty("hasThumbnail", True)
            self._thumb_label.setPixmap(pm.scaled(
                self._thumb_size,
                self._thumb_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        else:
            self._thumb_label.setProperty("hasThumbnail", False)
            self._thumb_label.setPixmap(_fallback_thumb_icon(kind))
        self._thumb_label.style().unpolish(self._thumb_label)
        self._thumb_label.style().polish(self._thumb_label)

    def set_thumb_size(self, size: int) -> None:
        size = _clamp_file_thumb_size(size)
        if size == self._thumb_size:
            return
        self._thumb_size = size
        self.setFixedHeight(_file_card_height_for_thumb(size))
        self._select_mark.setFixedSize(16, size)
        self._thumb_label.setFixedSize(size, size)
        if self._thumbnail_loaded:
            self._apply_thumbnail(self._thumbnail_kind)
        else:
            self._apply_thumbnail_placeholder(self._thumbnail_kind)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
            self._drag_started = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if self._press_pos is None or self._drag_started:
            return
        if (event.position().toPoint() - self._press_pos).manhattanLength() < QApplication.startDragDistance():
            return
        self._drag_started = True
        self._start_file_drag()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton and not self._drag_started:
            self._context_menu_from_mouse = True
            self._show_context_menu(self.mapToGlobal(event.position().toPoint()))
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and not self._drag_started:
            path = getattr(self._entry, "path", "")
            self._selected = not self._selected
            self._update_selection_style()
            self.selection_toggled.emit(path, self._selected)
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def _start_file_drag(self) -> None:
        path = getattr(self._entry, "path", "")
        if not path:
            return
        if self._drag_paths_for is not None:
            paths = self._drag_paths_for(path)
        else:
            paths = [path]
        paths = [p for p in paths if p]
        if not paths:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    def _update_selection_style(self) -> None:
        self.setObjectName("CardSelected" if self._selected else "Card")
        self._select_mark.setProperty("selected", self._selected)
        self._select_mark.setToolTip("已选" if self._selected else "未选")
        self._select_mark.style().unpolish(self._select_mark)
        self._select_mark.style().polish(self._select_mark)
        self.style().unpolish(self)
        self.style().polish(self)

    def is_selected(self) -> bool:
        return self._selected

    def set_selected(self, val: bool) -> None:
        self._selected = val
        self._update_selection_style()

    def contextMenuEvent(self, event) -> None:
        if self._context_menu_from_mouse:
            self._context_menu_from_mouse = False
            event.accept()
            return
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

        if kind in ("jpg", "tiff"):
            menu.addSeparator()
            compose_action = menu.addAction("合成")
            organise_action = menu.addAction("整理")
            compose_organise_action = menu.addAction("合成+整理")
            if self._on_workflow_action is not None:
                compose_action.triggered.connect(
                    lambda: self._on_workflow_action("compose", path)
                )
                organise_action.triggered.connect(
                    lambda: self._on_workflow_action("organise", path)
                )
                compose_organise_action.triggered.connect(
                    lambda: self._on_workflow_action("compose_organise", path)
                )
            else:
                compose_action.setEnabled(False)
                organise_action.setEnabled(False)
                compose_organise_action.setEnabled(False)

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
    """Pending JPG/TIFF capture stream with batch identity header.

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
    legacy_organize_requested = pyqtSignal()  # "旧照片批量整理" from the compact menu
    compose_implicit_requested = pyqtSignal()  # 主界面[合成]
    organise_selected_requested = pyqtSignal()  # 主界面[整理]：选中 JPG+TIFF 直接归档
    compose_implicit_organise_requested = pyqtSignal()  # 主界面[合成+整理]
    auto_compress_toggled = pyqtSignal(bool)  # 自动归档开关
    compose_preview_toggled = pyqtSignal(bool)  # 主界面“预览”开关
    settings_requested = pyqtSignal()  # emitted from the compact "更多" menu
    phase_clicked = pyqtSignal(str)    # status code: shooting/shot_done/organizing/done
    external_jpgs_dropped = pyqtSignal(list)  # OS drop → list[str] JPG paths
    clear_pending_requested = pyqtSignal(list)  # queue clear, never disk delete

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._scan_result: Optional["ScanResult"] = None
        self._active_uid: Optional[str] = None
        self._current_phase: Optional[str] = None
        self._last_scan_sig = None  # change-detection: skip rebuild when unchanged
        self._cards: list[_FileCard] = []  # all current cards (for selection ops)
        self._thumb_queue: deque[_FileCard] = deque()
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setInterval(16)
        self._thumb_timer.timeout.connect(self._load_next_thumbnail_batch)
        # Incremental rebuild: reuse card widgets across scans keyed by file
        # path, so a single new photo builds one card instead of rebuilding all.
        self._card_by_key: dict[str, _FileCard] = {}
        self._card_sig_by_key: dict[str, tuple] = {}
        self._hide_archived: bool = False
        self._view_mode: str = "tiles"
        self._sort_key: str = "default"
        self._sort_reverse: bool = False
        self._pending_thumb_size: int = _FILE_THUMB_SIZE
        self._current_grid_cols: int = 1
        self._shortcuts: list[QShortcut] = []
        self._drop_targets: list[QWidget] = []
        self._setup_ui()
        self._install_shortcuts()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        self.setAcceptDrops(True)
        self.installEventFilter(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        section = QFrame()
        self._register_drop_target(section)
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
            btn.setVisible(False)  # only the current phase is shown (sidebar has the full set)
            self._phase_pills[code] = btn

        # 场景: 新手打开工作区, 面前 6 个按钮 + 一堆照片, 不知道先点哪个。
        #   app/widgets/workbench_dashboard.py::_WorkflowDashboard 本来做过这件事
        #   (顶部一整条"下一步"仪表盘), 但它同时重复了项目名/编号/JPG-TIFF 计数/
        #   合成整理按钮 —— 全都已经在别处有了, 白吃一整行高度, 所以那条胖面板
        #   一直没被接上(死代码, 保留不删, 见 §7)。
        # 理由(Fable 5, 2026-07-12 用户拍板"你决定, 但保证美观"): 只把那面板里
        #   **唯一不重复**的东西 —— 那句"下一步该干嘛" —— 救出来, 塞进批次条现有
        #   空白处(stretch 前), 零新增高度; 没待处理照片时隐藏, 不唠叨。
        self._next_hint = _ElideLabel("")
        self._next_hint.setObjectName("NextStepHint")
        self._next_hint.hide()
        b_lay.addWidget(self._next_hint)  # 不给 stretch: 取自然宽, 富余宽度归下面的 stretch

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
        title.setObjectName("WorkbenchTitle")
        controls.addWidget(title)
        controls.addStretch()
        refresh_btn = QPushButton("刷新")
        refresh_btn.setObjectName("Outline")
        refresh_btn.setFixedHeight(28)
        icons.set_button_icon(refresh_btn, "mdi6.refresh", color=icons.TONE_MUTED, size=15)
        refresh_btn.clicked.connect(self._emit_refresh_requested)
        controls.addWidget(refresh_btn)
        add_btn = QPushButton("添加照片")
        add_btn.setObjectName("Outline")
        add_btn.setFixedHeight(28)
        icons.set_button_icon(add_btn, "mdi6.image-plus-outline", color=icons.TONE_MUTED, size=15)
        add_btn.setToolTip("把已有照片添加进来（JPG / TIF 进入待处理目录）")
        add_btn.clicked.connect(self.add_jpg_requested.emit)
        self._add_btn = add_btn
        controls.addWidget(add_btn)
        # 主流程一键合成：手选优先；自动归档开时可取激活编号未占用 JPG。
        self._compose_btn = QPushButton("合成")
        self._compose_btn.setObjectName("Primary")
        self._compose_btn.setFixedHeight(28)
        icons.set_button_icon(self._compose_btn, "mdi6.layers-triple-outline",
                              color=icons.TONE_ON_ACCENT, size=14)
        self._compose_btn.setToolTip("合成选中的 JPG；自动归档开时可取激活编号未占用 JPG")
        self._compose_btn.clicked.connect(self.compose_implicit_requested.emit)
        controls.addWidget(self._compose_btn)

        # 场景: 用户看到「合成」旁边写着「预览」, 以为点了能看图。
        # 理由(Fable 5, 2026-07-11 用户确认改名): 它其实是**合成流程开关** ——
        #   勾选=合成前先确认 JPG、合成后进 TIFF 预览工作台; 取消=直接合成。
        #   叫「预览」严重误导(用户以为是看图按钮)。改叫「先确认」: 放在「合成」
        #   旁边读作"合成, 先确认一下", 一目了然。
        # §7 旧: QCheckBox("预览")
        self._compose_preview_cb = QCheckBox("先确认")
        self._compose_preview_cb.setObjectName("InlineCheck")
        self._compose_preview_cb.setChecked(True)
        self._compose_preview_cb.setToolTip(
            "勾选：合成前先确认要用哪些 JPG，合成后进入 TIFF 预览工作台\n"
            "取消：直接合成，不打断"
        )
        self._compose_preview_cb.toggled.connect(self.compose_preview_toggled.emit)
        controls.addWidget(self._compose_preview_cb)

        self._organise_btn = QPushButton("整理")
        self._organise_btn.setObjectName("Outline")
        self._organise_btn.setFixedHeight(28)
        icons.set_button_icon(self._organise_btn, "mdi6.folder-zip-outline",
                              color=icons.TONE_ACCENT, size=14)
        self._organise_btn.setToolTip("整理选中的 JPG + 1 个 TIFF，生成同名 ZIP 并移入 results")
        self._organise_btn.clicked.connect(self.organise_selected_requested.emit)
        controls.addWidget(self._organise_btn)

        self._compose_org_btn = QPushButton("合成+整理")
        self._compose_org_btn.setObjectName("Outline")
        self._compose_org_btn.setFixedHeight(28)
        icons.set_button_icon(self._compose_org_btn, "mdi6.archive-check-outline",
                              color=icons.TONE_MUTED, size=14)
        self._compose_org_btn.setToolTip("合成选中 JPG 后立即整理；自动归档开时可取激活编号未占用 JPG")
        self._compose_org_btn.clicked.connect(
            self.compose_implicit_organise_requested.emit
        )
        controls.addWidget(self._compose_org_btn)

        self._auto_toggle = QPushButton("自动归档")
        self._auto_toggle.setObjectName("Ghost")
        self._auto_toggle.setFixedHeight(28)
        self._auto_toggle.setCheckable(True)
        self._auto_toggle.setToolTip(
            "激活编号时可不手选 JPG 直接合成；合成后自动整理；外部 TIF 出现时自动整理"
        )
        icons.set_button_icon(self._auto_toggle, "mdi6.checkbox-blank-outline",
                              color=icons.TONE_MUTED, size=15)
        self._auto_toggle.toggled.connect(self._sync_auto_archive_toggle_and_emit)
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

        # §7 旧①: sec.addLayout(controls) —— 8 个文字按钮硬撑 600px, 把中栏
        #         最小值顶到 717, 三栏在窄屏放不下 → 内容互挤。
        # §7 旧②: 放进横向滚动容器 —— 窄时"预览"等按钮被切、要滚才看得到,
        #         用户不满意(2026-07-11)。
        # 现: FlowLayout 自动换行 —— 宽度够时一行、窄时折到第 2 行, 不滚动、
        #     不裁切; 且其最小宽只取单个最宽按钮(不是按钮之和), 不撑爆邻栏。
        from app.widgets._flow_layout import FlowLayout
        _toolbar_host = QWidget()
        _flow = FlowLayout(_toolbar_host, margin=0, h_spacing=8, v_spacing=6)
        # 把原 controls 里的控件按顺序搬进 flow(丢弃 stretch, flow 不需要)。
        while controls.count():
            it = controls.takeAt(0)
            w = it.widget()
            if w is not None:
                _flow.addWidget(w)
        self._toolbar_flow = _flow
        sec.addWidget(_toolbar_host)

        self._workflow_notice_hidden_by_user = False
        self._workflow_notice_collapsed = False
        self._workflow_notice_task_key = ""
        self._workflow_notice = QFrame()
        self._workflow_notice.setObjectName("WorkflowNotice")
        n_lay = QHBoxLayout(self._workflow_notice)
        n_lay.setContentsMargins(10, 7, 10, 7)
        n_lay.setSpacing(8)
        self._workflow_stage = QLabel("进行中")
        self._workflow_stage.setObjectName("WorkflowNoticeStage")
        self._workflow_stage.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._workflow_stage.setFixedWidth(54)
        n_lay.addWidget(self._workflow_stage)
        notice_text = QWidget()
        notice_text_lay = QVBoxLayout(notice_text)
        notice_text_lay.setContentsMargins(0, 0, 0, 0)
        notice_text_lay.setSpacing(1)
        self._workflow_title = QLabel("")
        self._workflow_title.setObjectName("WorkflowNoticeTitle")
        self._workflow_detail = QLabel("")
        self._workflow_detail.setObjectName("WorkflowNoticeDetail")
        self._workflow_detail.setWordWrap(True)
        notice_text_lay.addWidget(self._workflow_title)
        notice_text_lay.addWidget(self._workflow_detail)
        n_lay.addWidget(notice_text, 1)
        self._workflow_collapse_btn = QPushButton("收起")
        self._workflow_collapse_btn.setObjectName("Tiny")
        self._workflow_collapse_btn.setFixedHeight(22)
        self._workflow_collapse_btn.setToolTip("收起任务详情")
        icons.set_button_icon(
            self._workflow_collapse_btn, "mdi6.chevron-up",
            color=icons.TONE_MUTED, size=13,
        )
        self._workflow_collapse_btn.clicked.connect(
            self._toggle_workflow_notice_collapsed
        )
        n_lay.addWidget(self._workflow_collapse_btn)
        self._workflow_hide_btn = QPushButton("隐藏")
        self._workflow_hide_btn.setObjectName("Tiny")
        self._workflow_hide_btn.setFixedHeight(22)
        self._workflow_hide_btn.setToolTip("隐藏当前任务提示；任务会继续在后台运行")
        icons.set_button_icon(
            self._workflow_hide_btn, "mdi6.eye-off-outline",
            color=icons.TONE_MUTED, size=13,
        )
        self._workflow_hide_btn.clicked.connect(self._hide_workflow_notice_for_current_task)
        n_lay.addWidget(self._workflow_hide_btn)
        self._workflow_notice.hide()
        sec.addWidget(self._workflow_notice)

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
        sh_title.setObjectName("WorkbenchSubTitle")
        sh.addWidget(sh_title)
        sh_hint = QLabel("单击选择 · 右键处理文件")
        sh_hint.setObjectName("WorkbenchHint")
        sh.addWidget(sh_hint)
        sh.addStretch()
        self._view_btn = QPushButton("平铺")
        self._view_btn.setObjectName("Ghost")
        self._view_btn.setFixedHeight(26)
        self._view_btn.setToolTip("切换待处理照片的查看方式")
        icons.set_button_icon(self._view_btn, "mdi6.view-grid-outline",
                              color=icons.TONE_MUTED, size=14)
        self._view_btn.clicked.connect(
            lambda: self._show_view_menu(self._view_btn.mapToGlobal(self._view_btn.rect().bottomLeft()))
        )
        sh.addWidget(self._view_btn)
        self._sort_btn = QPushButton("排序")
        self._sort_btn.setObjectName("Ghost")
        self._sort_btn.setFixedHeight(26)
        self._sort_btn.setToolTip("按名称、类型、大小、时间或归属排序")
        icons.set_button_icon(self._sort_btn, "mdi6.sort", color=icons.TONE_MUTED, size=14)
        self._sort_btn.clicked.connect(
            lambda: self._show_sort_menu(self._sort_btn.mapToGlobal(self._sort_btn.rect().bottomLeft()))
        )
        sh.addWidget(self._sort_btn)
        self._sel_count = QLabel("")
        self._sel_count.setObjectName("MutedSmall")
        sh.addWidget(self._sel_count)
        sec.addLayout(sh)

        # ── Contextual selection bar: hidden until one or more files selected ──
        self._selection_bar = QFrame()
        self._selection_bar.setObjectName("WorkbenchSelectionBar")
        sel = QHBoxLayout(self._selection_bar)
        sel.setContentsMargins(10, 6, 10, 6)
        sel.setSpacing(8)
        self._selection_count = QLabel("已选 0 个")
        self._selection_count.setObjectName("WorkbenchSelectionText")
        sel.addWidget(self._selection_count)
        add_group_btn = QPushButton("加入分组")
        add_group_btn.setObjectName("Tiny")
        add_group_btn.setFixedHeight(22)
        add_group_btn.clicked.connect(self._add_selected_jpgs_to_group)
        sel.addWidget(add_group_btn)
        sel_all_btn = QPushButton("全选")
        sel_all_btn.setObjectName("Tiny")
        sel_all_btn.setFixedHeight(22)
        sel_all_btn.clicked.connect(self._on_select_all)
        sel.addWidget(sel_all_btn)
        sel_none_btn = QPushButton("取消选择")
        sel_none_btn.setObjectName("Tiny")
        sel_none_btn.setFixedHeight(22)
        sel_none_btn.clicked.connect(self._on_select_none)
        sel.addWidget(sel_none_btn)
        clear_pending_btn = QPushButton("清空队列")
        clear_pending_btn.setObjectName("Outline")
        clear_pending_btn.setFixedHeight(24)
        clear_pending_btn.setToolTip("移出当前待处理队列，不删除原片")
        icons.set_button_icon(clear_pending_btn, "mdi6.tray-arrow-up", color=icons.TONE_MUTED, size=14)
        clear_pending_btn.clicked.connect(self._on_clear_pending_files)
        sel.addWidget(clear_pending_btn)
        self._del_btn = QPushButton("删除")
        self._del_btn.setObjectName("Danger")
        self._del_btn.setFixedHeight(24)
        self._del_btn.setEnabled(False)
        icons.set_button_icon(self._del_btn, "mdi6.delete-outline", color=icons.TONE_DANGER, size=14)
        self._del_btn.setToolTip("删除选中文件（含 TIFF 时二次确认）")
        self._del_btn.clicked.connect(self._delete_selected_pending_files)
        sel.addWidget(self._del_btn)
        undo_attr_btn = QPushButton("撤销归属")
        undo_attr_btn.setObjectName("Tiny")
        undo_attr_btn.setFixedHeight(22)
        undo_attr_btn.setToolTip("撤销选中 JPG 的归属")
        icons.set_button_icon(undo_attr_btn, "mdi6.undo", color=icons.TONE_MUTED, size=14)
        undo_attr_btn.clicked.connect(self._unassign_selected_jpgs)
        sel.addWidget(undo_attr_btn)
        sel.addStretch()
        self._selection_bar.hide()
        sec.addWidget(self._selection_bar)

        # ── Capture stream (scrollable grid) ──
        scroll = QScrollArea()
        self._register_drop_target(scroll)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAcceptDrops(True)
        scroll.viewport().installEventFilter(self)
        self._drop_targets.append(scroll.viewport())
        scroll.viewport().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        scroll.viewport().customContextMenuRequested.connect(
            lambda pos: self._show_stream_menu(scroll.viewport().mapToGlobal(pos))
        )
        self._stream_scroll = scroll
        self._grid_widget = QWidget()
        self._register_drop_target(self._grid_widget)
        self._grid_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._grid_widget.customContextMenuRequested.connect(
            lambda pos: self._show_stream_menu(self._grid_widget.mapToGlobal(pos))
        )
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setContentsMargins(0, 4, 0, 4)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(8)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._grid_widget)
        sec.addWidget(scroll, stretch=1)

        # ── Empty state ──
        self._empty_label = QLabel(
            "等待目录中新照片\n已处理文件不再留在未整理区；TIFF 出现前不会关联原片。"
        )
        self._register_drop_target(self._empty_label)
        self._empty_label.setObjectName("EmptyState")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        self._empty_label.setMinimumHeight(160)
        self._empty_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._empty_label.customContextMenuRequested.connect(
            lambda pos: self._show_stream_menu(self._empty_label.mapToGlobal(pos))
        )
        self._empty_label.hide()
        sec.addWidget(self._empty_label)

        # ── Unattributed warning ──
        self._unattr_warning = QLabel("")
        self._unattr_warning.setObjectName("UnattributedWarning")
        self._unattr_warning.hide()
        sec.addWidget(self._unattr_warning)

    def _register_drop_target(self, widget: QWidget) -> None:
        widget.setAcceptDrops(True)
        widget.installEventFilter(self)
        self._drop_targets.append(widget)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 (Qt override)
        if (
            event.type() == QEvent.Type.Wheel
            and watched in self._drop_targets
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self._zoom_pending_thumbnails_by_wheel_delta(event.angleDelta().y())
            event.accept()
            return True
        if (
            event.type() == QEvent.Type.Resize
            and hasattr(self, "_stream_scroll")
            and watched is self._stream_scroll.viewport()
        ):
            self._reflow_cards_for_viewport()
        if watched in self._drop_targets:
            if event.type() in (
                QEvent.Type.DragEnter,
                QEvent.Type.DragMove,
            ):
                return self._handle_drop_probe(event)
            if event.type() == QEvent.Type.Drop:
                return self._handle_file_drop(event)
        return super().eventFilter(watched, event)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if not self._handle_drop_probe(event):
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if not self._handle_drop_probe(event):
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if not self._handle_file_drop(event):
            event.ignore()

    def _handle_drop_probe(self, event) -> bool:
        if self._jpg_paths_from_mime(event.mimeData()):
            event.acceptProposedAction()
            return True
        event.ignore()
        return True

    def _handle_file_drop(self, event) -> bool:
        paths = self._jpg_paths_from_mime(event.mimeData())
        if not paths:
            event.ignore()
            return True
        event.acceptProposedAction()
        self.external_jpgs_dropped.emit(paths)
        return True

    @staticmethod
    def _jpg_paths_from_mime(mime: QMimeData | None) -> list[str]:
        if mime is None or not mime.hasUrls():
            return []
        paths: list[str] = []
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            raw = url.toLocalFile() or url.path()
            if not raw:
                continue
            path = Path(raw)
            if path.is_file() and is_media_path(path):
                paths.append(str(path))
        return paths

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
        # The active phase is shown by the single visible phase pill right after
        # the UID (see set_phase). Only the "未激活" hint shows when nothing is
        # active — the old "激活：<uid> · 自 HH:MM" banner duplicated the UID.
        # 场景: 用户在左侧「单击」某编号 -> 右侧命名/分类表单立刻填满 -> 他以为
        #   "我已经在用这个号了", 接着拍照, 结果照片没归到该号(拍照自动归属只认
        #   **已激活**编号, 要双击才激活)。旧文案不管选没选都只写「未激活」, 和
        #   旁边明晃晃的 UID 互相打架, 看不出"我明明选了啊"。
        # 理由(Fable 5, 2026-07-11 用户确认): 把三态分开显示, 选中态直接告诉用户
        #   下一步动作(双击激活), 不用他去猜。
        # §7 旧:
        #   if active_uid:
        #       self._activate_state.hide()
        #   else:
        #       self._activate_state.setText("未激活")
        #       ... show()
        if active_uid:
            self._activate_state.hide()
        else:
            if uid:
                # 选中但未激活 —— 有号、但拍照不会自动归到它
                self._activate_state.setText("已选中 · 未激活")
                self._activate_state.setToolTip(
                    "只是选中查看，拍照不会自动归到这个编号。\n"
                    "双击左侧编号（或点「激活此编号」）后才会自动归属。"
                )
            else:
                self._activate_state.setText("未激活")
                self._activate_state.setToolTip(
                    "还没有当前编号。在左侧双击一个编号即可激活。"
                )
            self._activate_state.setObjectName("ActivateState")
            self._activate_state.style().unpolish(self._activate_state)
            self._activate_state.style().polish(self._activate_state)
            self._activate_state.show()

        if not active_uid:
            self.set_phase(None)

    def _update_next_hint(self) -> None:
        """批次条里的「下一步 › …」一句话引导。

        规则(取自 workbench_dashboard._WorkflowDashboard.update_state 的
        title/detail 分支, 压成一行):
          待处理为空        -> 隐藏(没事干就别唠叨)
          只有 JPG          -> 选 JPG -> 合成
          有 TIFF(外部成片)  -> 选 TIFF + 对应 JPG -> 整理
        """
        scan = getattr(self, "_scan_result", None)
        jpg_c = len(getattr(scan, "jpg_files", []) or []) if scan else 0
        tiff_c = len(getattr(scan, "tiff_files", []) or []) if scan else 0

        if not jpg_c and not tiff_c:
            self._next_hint.hide()
            self._next_hint.setText("")
            return

        if tiff_c and jpg_c:
            text, short = "下一步 › 选中 TIFF 和它的原片 JPG，点「整理」", "下一步 › 整理"
        elif tiff_c:
            text, short = "下一步 › 选中 TIFF，再选对应 JPG，点「整理」", "下一步 › 整理"
        else:
            text, short = "下一步 › 选中要用的 JPG，点「合成」", "下一步 › 合成"
        self._next_hint.set_texts(text, short)
        self._next_hint.setToolTip(text)
        self._next_hint.show()

    def set_phase(self, status: Optional[str]) -> None:
        """Reflect the confirmed task status on the phase pills (exclusive)."""
        self._current_phase = status if status in self._phase_pills else None
        for code, btn in self._phase_pills.items():
            is_cur = code == self._current_phase
            btn.setChecked(is_cur)
            btn.setVisible(is_cur)  # show only the current phase; sidebar has the full clickable set

    def _on_phase_pill_clicked(self, code: str) -> None:
        # Roll back Qt's automatic toggle; the workbench confirms via set_phase.
        self.set_phase(self._current_phase)
        self.phase_clicked.emit(code)

    def load_scan(self, scan_result: "ScanResult") -> None:
        """Populate the grid from a completed scan result.

        The workbench refreshes this from filesystem events plus a low-frequency
        fallback scan. Rebuilding the whole card grid each scan (tearing down +
        recreating every ``_FileCard``) is a UI-jank source, so skip the rebuild
        when nothing the grid renders has changed since the last scan.
        """
        sig = self._scan_signature(scan_result)
        self._scan_result = scan_result
        if sig == self._last_scan_sig:
            return
        self._last_scan_sig = sig
        self._rebuild_grid()
        self._update_next_hint()  # 计数变了 -> 「下一步」跟着变(Fable 5, 2026-07-12)

    def set_import_busy(self, busy: bool) -> None:
        """Reflect a background add-photo import in the toolbar."""
        btn = getattr(self, "_add_btn", None)
        if btn is None:
            return
        btn.setEnabled(not busy)
        btn.setText("导入中..." if busy else "添加照片")
        btn.setToolTip(
            "正在把照片复制到待处理目录"
            if busy
            else "把已有照片添加进来（JPG / TIF 进入待处理目录）"
        )

    def set_workflow_notice(
        self,
        title: str,
        detail: str = "",
        *,
        state: str = "busy",
        force_show: bool = False,
        task_key: str | None = None,
    ) -> None:
        """Show a persistent workflow notice above the pending-photo stream."""
        key = str(task_key or "")
        if key and key != self._workflow_notice_task_key:
            self._workflow_notice_task_key = key
            self._workflow_notice_hidden_by_user = False
            self._workflow_notice_collapsed = False
        if force_show:
            self._workflow_notice_hidden_by_user = False
            self._workflow_notice_collapsed = False
        state = state if state in {"busy", "success", "error", "info"} else "info"
        bg, fg, border, stage_text = {
            "busy": ("#e8f4ff", "#0b5cad", "#90cdf4", "进行中"),
            "success": ("#ecfdf3", TOKENS["success"], "#86efac", "完成"),
            "error": ("#fff1f0", TOKENS["danger"], "#fca5a5", "失败"),
            "info": ("#f4f6f8", TOKENS["accent"], TOKENS["border_medium"], "提示"),
        }[state]
        self._workflow_notice.setStyleSheet(
            "QFrame#WorkflowNotice {"
            f" background:{bg}; border:1px solid {border}; border-radius:8px;"
            "}"
            "QLabel#WorkflowNoticeStage {"
            f" background:{fg}; color:white; border-radius:4px;"
            " padding:2px 6px; font-size:11px; font-weight:700;"
            "}"
            "QLabel#WorkflowNoticeTitle {"
            f" color:{TOKENS['text']}; font-size:12px; font-weight:700;"
            "}"
            "QLabel#WorkflowNoticeDetail {"
            f" color:{TOKENS['muted']}; font-size:11px;"
            "}"
        )
        detail_text = str(detail or "").strip()
        self._workflow_stage.setText(stage_text)
        self._workflow_title.setText(str(title or "").strip())
        self._workflow_detail.setText(detail_text)
        self._apply_workflow_notice_presentation()
        if not self._workflow_notice_hidden_by_user:
            self._workflow_notice.show()

    def clear_workflow_notice(self) -> None:
        self._workflow_notice.hide()
        self._workflow_notice_hidden_by_user = False
        self._workflow_notice_collapsed = False
        self._workflow_notice_task_key = ""
        self._workflow_title.setText("")
        self._workflow_detail.setText("")

    def _apply_workflow_notice_presentation(self) -> None:
        detail_text = self._workflow_detail.text().strip()
        has_detail = bool(detail_text)
        self._workflow_detail.setVisible(has_detail and not self._workflow_notice_collapsed)
        self._workflow_collapse_btn.setEnabled(has_detail)
        if self._workflow_notice_collapsed:
            self._workflow_collapse_btn.setText("展开")
            self._workflow_collapse_btn.setToolTip("展开任务详情")
            icons.set_button_icon(
                self._workflow_collapse_btn, "mdi6.chevron-down",
                color=icons.TONE_MUTED, size=13,
            )
        else:
            self._workflow_collapse_btn.setText("收起")
            self._workflow_collapse_btn.setToolTip("收起任务详情")
            icons.set_button_icon(
                self._workflow_collapse_btn, "mdi6.chevron-up",
                color=icons.TONE_MUTED, size=13,
            )

    def _toggle_workflow_notice_collapsed(self) -> None:
        self._workflow_notice_collapsed = not self._workflow_notice_collapsed
        self._apply_workflow_notice_presentation()

    def _hide_workflow_notice_for_current_task(self) -> None:
        self._workflow_notice_hidden_by_user = True
        self._workflow_notice.hide()

    def workflow_notice_text(self) -> tuple[str, str, str]:
        """Return (stage, title, detail) for tests."""
        return (
            self._workflow_stage.text(),
            self._workflow_title.text(),
            self._workflow_detail.text(),
        )

    def _scan_signature(self, scan_result: "ScanResult"):
        """Cheap fingerprint of everything the card grid renders.

        Includes the active UID because it changes card highlighting.
        """
        if scan_result is None:
            return None

        def rendered_entry_fingerprint(entry):
            return (
                entry.name, entry.mtime, entry.size,
                entry.attributed_specimen_id, entry.is_grouped, entry.composed_tiff,
                entry.has_zip,
            )

        return (
            self._active_uid,
            tuple(rendered_entry_fingerprint(e) for e in scan_result.jpg_files),
            tuple(rendered_entry_fingerprint(e) for e in scan_result.tiff_files),
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
        self._update_next_hint()  # 清空项目 -> 隐藏引导(Fable 5, 2026-07-12)

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
        all_files = self._sort_entries(jpgs + tiffs)

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
            getattr(entry, "mtime", ""),
            getattr(entry, "size", 0),
            uid,
            bool(uid) and uid == self._active_uid,
            getattr(entry, "composed_tiff", None),
            getattr(entry, "archived", None),
            getattr(entry, "is_grouped", False),
        )

    def _sort_entries(self, entries: list) -> list:
        entries = list(entries or [])
        if self._sort_key == "default":
            return entries

        def name_value(entry) -> str:
            return str(
                getattr(entry, "name", "") or Path(getattr(entry, "path", "")).name
            ).lower()

        def sort_key_for_media_entry(entry):
            name = name_value(entry)
            if self._sort_key == "name":
                return (name,)
            if self._sort_key == "type":
                kind = str(getattr(entry, "kind", "") or "").lower()
                return (kind, Path(name).suffix.lower(), name)
            if self._sort_key == "size":
                return (int(getattr(entry, "size", 0) or 0), name)
            if self._sort_key == "mtime":
                return (str(getattr(entry, "mtime", "") or ""), name)
            if self._sort_key == "uid":
                uid = str(getattr(entry, "attributed_specimen_id", "") or "")
                return (uid.lower(), name)
            return (name,)

        return sorted(entries, key=sort_key_for_media_entry, reverse=self._sort_reverse)

    def _drag_paths_for(self, path: str) -> list[str]:
        selected = self.selected_all_paths()
        if path in selected:
            return selected
        return [path]

    def _make_card(self, entry) -> "_FileCard":
        card = _FileCard(
            entry, self._active_uid, self,
            on_add_to_group=self._on_ctx_add_to_group,
            on_assign_uid=self._on_ctx_assign_uid,
            on_unassign=self._on_ctx_unassign,
            on_workflow_action=self._on_ctx_workflow_action,
            drag_paths_for=self._drag_paths_for,
            # §7 旧: defer_thumbnail=False —— 每张新 JPG/TIFF 卡片构造时同步解码,
            # 相机连拍时这些解码与用户点击争主线程(2026-07-10 报障:反应迟钝)。
            # 已有的 16ms 分批队列(_queue_thumbnail)因这里恒 False 从未被用上。
            defer_thumbnail=True,
            thumb_size=self._pending_thumb_size,
        )
        card.assign_requested.connect(self.assign_requested)
        card.deactivate_requested.connect(self.unassign_requested)
        card.selection_toggled.connect(self._on_card_selection_toggled)
        card.delete_requested.connect(self._on_delete_single_requested)
        self._register_drop_target(card)
        return card

    def _queue_thumbnail(self, card: "_FileCard") -> None:
        self._thumb_queue.append(card)
        if not self._thumb_timer.isActive():
            self._thumb_timer.start()

    def _load_next_thumbnail_batch(self) -> None:
        from app.config.memory_profile import MONITOR_THUMB_BATCH_SIZE

        loaded = 0
        batch = max(1, int(MONITOR_THUMB_BATCH_SIZE))
        while self._thumb_queue and loaded < batch:
            card = self._thumb_queue.popleft()
            try:
                if card.parent() is not None:
                    card.load_thumbnail_now()
                    loaded += 1
            except RuntimeError:
                continue
        if not self._thumb_queue:
            self._thumb_timer.stop()

    def _sync_cards(self, all_files: list) -> None:
        """Reconcile the card grid with *all_files*, reusing widgets.

        Only files that are new or whose visual signature changed get a fresh
        card; everything else is repositioned in place (cheap), so the common
        "one new photo arrived" case builds a single card instead of all.
        """
        was_enabled = self._grid_widget.updatesEnabled()
        self._grid_widget.setUpdatesEnabled(False)
        try:
            cols = self._grid_column_count()
            desired = {getattr(f, "path", ""): f for f in all_files}

            # Drop cards for files that vanished.
            for key in list(self._card_by_key):
                if key not in desired:
                    from app.utils.ui import dispose_widget

                    card = self._card_by_key.pop(key)
                    self._card_sig_by_key.pop(key, None)
                    dispose_widget(card)

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
                        from app.utils.ui import dispose_widget

                        dispose_widget(card)
                    card = self._make_card(f)
                    self._card_by_key[key] = card
                    self._card_sig_by_key[key] = sig
                    # 新建卡片才排队解码(签名未变的复用卡片已带图, 不重复解码)
                    self._queue_thumbnail(card)
                self._cards.append(card)
            self._layout_cards(cols)
        finally:
            self._grid_widget.setUpdatesEnabled(was_enabled)

    def _grid_column_count(self) -> int:
        if self._view_mode == "list":
            return 1
        viewport = getattr(self, "_stream_scroll", None)
        width = viewport.viewport().width() if viewport is not None else self.width()
        width = max(1, int(width or 1))
        return max(1, min(_FILE_TILE_MAX_COLUMNS, width // _FILE_TILE_MIN_WIDTH))

    def _layout_cards(self, cols: int) -> None:
        cols = max(1, int(cols or 1))
        while self._grid.count():
            self._grid.takeAt(0)
        for idx, card in enumerate(self._cards):
            self._grid.addWidget(card, idx // cols, idx % cols)
        for c in range(_FILE_TILE_MAX_COLUMNS):
            self._grid.setColumnStretch(c, 1 if c < cols else 0)
        self._current_grid_cols = cols

    def _reflow_cards_for_viewport(self) -> None:
        if not getattr(self, "_cards", None):
            return
        cols = self._grid_column_count()
        if cols == self._current_grid_cols:
            return
        self._layout_cards(cols)
        self._grid_widget.updateGeometry()

    def _clear_grid(self) -> None:
        self._thumb_queue.clear()
        self._thumb_timer.stop()
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

    # ── View / sort menus ─────────────────────────────────────────────────────

    def _show_view_menu(self, global_pos) -> None:
        menu = QMenu(self)
        self._populate_view_menu(menu)
        menu.exec(global_pos)

    def _show_sort_menu(self, global_pos) -> None:
        menu = QMenu(self)
        self._populate_sort_menu(menu)
        menu.exec(global_pos)

    def _show_stream_menu(self, global_pos) -> None:
        menu = QMenu(self)

        view_menu = menu.addMenu("查看")
        self._populate_view_menu(view_menu)

        sort_menu = menu.addMenu("排序方式")
        self._populate_sort_menu(sort_menu)

        menu.addSeparator()
        refresh_action = menu.addAction("刷新")
        refresh_action.triggered.connect(self.refresh_requested.emit)

        menu.addSeparator()
        hide_action = menu.addAction("隐藏已分组原片")
        hide_action.setCheckable(True)
        hide_action.setChecked(self._hide_archived_cb.isChecked())
        hide_action.toggled.connect(self._hide_archived_cb.setChecked)

        menu.exec(global_pos)

    def _populate_view_menu(self, menu: QMenu) -> None:
        for key, label in (
            ("tiles", "平铺"),
            ("list", "列表"),
        ):
            action = menu.addAction(("✓ " if self._view_mode == key else "") + label)
            action.triggered.connect(lambda _=False, k=key: self._set_view_mode(k))

    def _populate_sort_menu(self, menu: QMenu) -> None:
        for key, label in (
            ("default", "默认顺序"),
            ("name", "名称"),
            ("type", "类型"),
            ("size", "大小"),
            ("mtime", "修改时间"),
            ("uid", "归属编号"),
        ):
            action = menu.addAction(("✓ " if self._sort_key == key else "") + label)
            action.triggered.connect(lambda _=False, k=key: self._set_sort_key(k))

        menu.addSeparator()
        asc_action = menu.addAction(("✓ " if not self._sort_reverse else "") + "升序")
        asc_action.triggered.connect(lambda: self._set_sort_reverse(False))
        desc_action = menu.addAction(("✓ " if self._sort_reverse else "") + "降序")
        desc_action.triggered.connect(lambda: self._set_sort_reverse(True))

    def _set_view_mode(self, mode: str) -> None:
        if mode not in {"tiles", "list"} or mode == self._view_mode:
            return
        self._view_mode = mode
        self._view_btn.setText("列表" if mode == "list" else "平铺")
        icon_name = "mdi6.view-list-outline" if mode == "list" else "mdi6.view-grid-outline"
        icons.set_button_icon(self._view_btn, icon_name, color=icons.TONE_MUTED, size=14)
        self._rebuild_grid()

    def _set_sort_key(self, key: str) -> None:
        if key not in {"default", "name", "type", "size", "mtime", "uid"}:
            return
        self._sort_key = key
        self._sort_btn.setText({
            "default": "排序",
            "name": "名称",
            "type": "类型",
            "size": "大小",
            "mtime": "修改时间",
            "uid": "归属",
        }[key])
        self._rebuild_grid()

    def _set_sort_reverse(self, reverse: bool) -> None:
        self._sort_reverse = bool(reverse)
        self._rebuild_grid()

    def _install_shortcuts(self) -> None:
        def add(seq, callback) -> None:
            shortcut = QShortcut(QKeySequence(seq), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)

        add("Ctrl+A", self._on_select_all)
        add("Delete", self._delete_selected_pending_files)
        add("F5", self._emit_refresh_requested)
        add("Ctrl++", lambda: self._set_pending_thumb_size(self._pending_thumb_size + _FILE_THUMB_STEP))
        add("Ctrl+=", lambda: self._set_pending_thumb_size(self._pending_thumb_size + _FILE_THUMB_STEP))
        add("Ctrl+-", lambda: self._set_pending_thumb_size(self._pending_thumb_size - _FILE_THUMB_STEP))
        add("Ctrl+0", lambda: self._set_pending_thumb_size(_FILE_THUMB_SIZE))

    def _zoom_pending_thumbnails_by_wheel_delta(self, delta: int) -> None:
        if delta == 0:
            return
        steps = max(1, abs(delta) // 120)
        direction = 1 if delta > 0 else -1
        self._set_pending_thumb_size(
            self._pending_thumb_size + direction * steps * _FILE_THUMB_STEP
        )

    def _set_pending_thumb_size(self, size: int) -> None:
        size = _clamp_file_thumb_size(size)
        if size == self._pending_thumb_size:
            return
        self._pending_thumb_size = size
        for card in self._cards:
            card.set_thumb_size(size)
        self._grid.setVerticalSpacing(max(8, min(16, size // 8)))
        self._grid_widget.updateGeometry()

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

    def selected_jpg_owner_uids(self) -> list[str]:
        """Return unique non-empty owner UIDs from selected JPG cards."""
        owners: list[str] = []
        seen: set[str] = set()
        for card in self._selected_cards():
            entry = card._entry
            if getattr(entry, "kind", "") != "jpg":
                continue
            uid = str(getattr(entry, "attributed_specimen_id", "") or "").strip()
            if uid and uid not in seen:
                seen.add(uid)
                owners.append(uid)
        return owners

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

    def _delete_selected_pending_files(self) -> None:
        """Delete selected files after explicit confirmation."""
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

        # TIFF 可删，但必须是用户明确删除；整理/归档流程不能顺手删 TIFF。
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

    def _add_selected_jpgs_to_group(self) -> None:
        jpg_paths = self.selected_jpg_paths()
        if not jpg_paths:
            QMessageBox.information(self, "加入分组", "请先选中 JPG。")
            return
        db = self.ctx.get_db()
        if db is None:
            return
        owners = self.selected_jpg_owner_uids()
        if len(owners) > 1:
            QMessageBox.warning(self, "加入分组", "选中的 JPG 属于多个编号，请分开加入分组。")
            return
        active_uid = activation_service.get_active_uid(db)
        if owners:
            target_uid = owners[0]
        else:
            target_uid = active_uid or grouping_service.ADHOC_GROUPING_UID
        for jpg_path in jpg_paths:
            self._add_jpg_to_grouping(db, target_uid, jpg_path)
        self._on_select_none()
        self.refresh_requested.emit()

    def _unassign_selected_jpgs(self) -> None:
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
        grouping_service.remove_jpg_from_all_groups(db, jpg_path)
        self.refresh_requested.emit()

    def _on_ctx_workflow_action(self, action: str, path: str) -> None:
        if path and path not in self.selected_all_paths():
            for card in self._cards:
                card.set_selected(getattr(card._entry, "path", "") == path)
            self._refresh_selection_bar()
        if action == "compose":
            self.compose_implicit_requested.emit()
        elif action == "organise":
            self.organise_selected_requested.emit()
        elif action == "compose_organise":
            self.compose_implicit_organise_requested.emit()

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _emit_refresh_requested(self) -> None:
        self.refresh_requested.emit()

    def _set_auto_toggle_icon(self, on: bool) -> None:
        glyph = "mdi6.checkbox-marked" if on else "mdi6.checkbox-blank-outline"
        tone = icons.TONE_ACCENT if on else icons.TONE_MUTED
        icons.set_button_icon(self._auto_toggle, glyph, color=tone, size=15)

    def _sync_auto_archive_toggle_and_emit(self, on: bool) -> None:
        """Swap the auto-archive toggle glyph between checked / unchecked."""
        self._set_auto_toggle_icon(on)
        self.auto_compress_toggled.emit(on)

    def auto_compress_enabled(self) -> bool:
        return self._auto_toggle.isChecked()

    def set_auto_archive_enabled(self, on: bool, *, emit: bool = False) -> None:
        previous = self._auto_toggle.blockSignals(not emit)
        try:
            self._auto_toggle.setChecked(bool(on))
        finally:
            self._auto_toggle.blockSignals(previous)
        self._set_auto_toggle_icon(bool(on))

    def compose_preview_enabled(self) -> bool:
        return self._compose_preview_cb.isChecked()

    def set_compose_preview_enabled(self, on: bool, *, emit: bool = False) -> None:
        previous = self._compose_preview_cb.blockSignals(not emit)
        try:
            self._compose_preview_cb.setChecked(bool(on))
        finally:
            self._compose_preview_cb.blockSignals(previous)

    def _open_more_menu(self, global_pos) -> None:
        self._build_more_menu().exec(global_pos)

    def _build_more_menu(self) -> QMenu:
        menu = QMenu(self)

        # 分组入口从工具栏移到这里（用户指令）；信号链路不变。
        grouping_action = menu.addAction("分组工具")
        grouping_action.setToolTip("打开分组 / 合成工具")
        grouping_action.triggered.connect(self.grouping_requested.emit)

        menu.addSeparator()

        clear_pending_action = menu.addAction("清空待处理文件...")
        clear_pending_action.setEnabled(bool(self._cards))
        clear_pending_action.setToolTip("移出当前待处理队列，不删除原片")
        clear_pending_action.triggered.connect(self._on_clear_pending_files)

        menu.addSeparator()

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

        auto_action = menu.addAction("自动归档")
        auto_action.setCheckable(True)
        auto_action.setChecked(self._auto_toggle.isChecked())
        auto_action.toggled.connect(self._auto_toggle.setChecked)

        menu.addSeparator()

        legacy_action = menu.addAction("旧照片批量整理…")
        legacy_action.setToolTip("选择旧照片文件夹，按时间把 JPG 原片配给后续 TIF，预览确认后归档")
        legacy_action.triggered.connect(self.legacy_organize_requested.emit)

        return menu

    def _on_clear_pending_files(self) -> None:
        paths = [
            getattr(card._entry, "path", "")
            for card in self._cards
            if getattr(card._entry, "path", "")
        ]
        if not paths:
            QMessageBox.information(self, "清空队列", "当前队列为空。")
            return
        self.clear_pending_requested.emit(paths)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFixedHeight(1)
    return line
