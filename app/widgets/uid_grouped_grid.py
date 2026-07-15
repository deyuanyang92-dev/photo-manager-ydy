"""uid_grouped_grid.py — virtualized thumbnail grid grouped by specimen UID.

Spec ``survey-summary-view`` Task T3 (§2 UI / §5 性能 / §6 红线).

The middle column of the survey-summary view: photos grouped by specimen UID.
Each UID gets a section header (站点+序号 缩写 like ``B2-001``, full UID in
tooltip, item count on the right) followed by an IconMode thumbnail row.

**Virtualization (spec §5 红线).** Each UID section owns a ``QListView``
(IconMode) backed by a ``QAbstractListModel`` + ``QStyledItemDelegate``.
``QListView`` only paints the cells intersecting its viewport, so 2000+ items
spread across many sections still paint the first screen in well under the
200 ms budget. Sections stack in a ``QScrollArea``; header labels are cheap
(``QLabel``) so the section count itself does not dominate first-paint time.

**Async decode (spec §5).** Thumbnails are decoded off the GUI thread by a
single long-lived :class:`GridThumbnailWorker` (already-implemented T2). The
delegate's ``paint``:

  1. ``QPixmapCache.find(key)`` → hit → ``drawPixmap`` (synchronous, cheap);
  2. miss → ``QMetaObject.invokeMethod(worker, "decode", QueuedConnection,
     Q_ARG(int, request_id), Q_ARG(str, path), Q_ARG(int, thumb_size))``
     and return immediately (does **not** block paint).

The worker emits ``QImage`` (never ``QPixmap`` — see ``thumbnail_worker`` red
line). The widget's ``_on_decoded`` slot runs on the main thread, calls
:func:`image_thumbnail.make_pixmap`, stores the result in ``QPixmapCache`` and
notifies that one cell via ``dataChanged`` so the delegate repaints and finds
the cached pixmap. ``request_id`` is a per-widget monotonic counter; results
whose id was dropped (model reset / section rebuilt) are silently discarded,
so stale replies from the previous selection never paint into the new one.

Failed decodes (missing/undecodable file → ``image is None``) are
negative-cached in ``_failed_paths`` to avoid a busy re-request loop.

**Lifecycle (memory: workbench-timer-leak-hang, shutdown-lock-leak-must-reboot).**
The worker ``QThread`` is **not** parented to the widget (parented QObjects are
torn down by Qt before ``destroyed`` fires, racing ``quit()+wait()``). It is
stopped via the ``destroyed`` signal — a closure captures the thread ref so it
survives widget teardown — and via an explicit :meth:`teardown` the parent view
should call from ``on_deactivate`` / ``closeEvent``.

Red lines (spec §6):
  * Worker thread NEVER constructs ``QPixmap`` — emits ``QImage`` only.
  * No ``species`` / ``species_cn`` columns touched (this widget is path-only).
  * TIFF is never deleted here; import is never invoked here; the widget is
    pure-presentational and reads nothing from disk except thumbnail decode.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from PyQt6.QtCore import (
    QAbstractListModel,
    Q_ARG,
    QEvent,
    QItemSelectionModel,
    QMetaObject,
    QModelIndex,
    QObject,
    QPoint,
    QTimer,
    QRect,
    QSize,
    Qt,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPixmap, QPixmapCache
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QMenu,
    QScrollArea,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from app.config.i18n import tr
from app.config.project_tree_layout import PREVIEW_MASTER_PRESETS
from app.utils import ui
from app.utils.file_manager import reveal_in_directory
from app.utils.image_thumbnail import clear_thumbnail_cache, make_pixmap, try_cached_image_data
from app.utils.thumbnail_disk_cache import normalize_thumb_cache_size
from app.workers.thumbnail_worker import GridThumbnailWorker

__all__ = [
    "UidGroupedGrid",
    "UidSectionModel",
    "uid_abbreviation",
    "merge_groups_by_catalog_key",
    "catalog_key_for_uid",
    "catalog_label_for_uid",
]


from app.config.project_tree_layout import (
    DEFAULT_THUMB_SIZE,
    GRID_CELL_SPACING,
    GRID_VIEWPORT_PAD,
    SPLIT_MIN_GRID,
    auto_fit_thumb_for_viewport,
    cell_dims,
    clamp_thumb_size,
    sort_flat_grid_items,
    sort_result_groups,
    thumb_size_for_density,
)
_PLACEHOLDER_BG = QColor("#2a3548")     # empty cell fill (theme-neutral dark)
_PLACEHOLDER_FG = QColor("#64748b")     # empty cell border / icon stroke
_LOADING_BG = QColor("#1e293b")
_LOADING_FG = QColor("#94a3b8")
_FAILED_FG = QColor("#f87171")
_SELECTED_BG = QColor("#1e3a5f")        # selected cell tint
_HEADER_H = 26

_THUMB_SIZE = DEFAULT_THUMB_SIZE  # legacy alias for tests / cache keys

# 预取/合并参数（性能修复，见本模块 docstring 顶部的 "Async decode" 段）:
#   * 只预取「可见带 = 视口 ±1 屏」内的格子，且单轮最多投递这么多解码请求，
#     防止单个 worker 被上千条过期请求塞死。
#   * 滚动用 debounce 定时器合并；解码回来的单元格用 ~16ms 定时器攒批刷新。
_PREFETCH_BUDGET = 96
# Claude Code 修改 2026-07-14 — codex 回归发现: BUG1 修复(严格可见行无视预算)在
# 小缩略图/大视口下单次调用可能一口气派发数百个解码请求, 全堆给单一 worker 线程的
# 消息队列。严格可见格子仍然必须"最终全部被请求"(否则回到 BUG1 那个 bug), 但
# 单次调用里只应该发这么多个, 剩下的下一轮 tick 接着发, 分批排空而不是一次性爆发。
_VISIBLE_PREFETCH_BUDGET = 128
_PREFETCH_CONTINUATION_MS = 30
_PREFETCH_SCROLL_DEBOUNCE_MS = 80
_DIRTY_FLUSH_MS = 16
_INITIAL_COVER_TIMEOUT_MS = 5000

# QPixmapCache 全局上限（KB）。main.py 只设 8MB(性能模式 4MB)，按「一屏 30 格 ×
# 每格 ~0.25MB」根本装不下一屏，导致 paint 永远未命中 → 反复解码 → 抖动。
# 这里在首个网格构造时「只升不降」地抬高上限（低内存/性能模式减半）。
_PIXMAP_CACHE_TARGET_KB = 65536          # 64 MB
_PIXMAP_CACHE_TARGET_KB_LOW = 32768      # 32 MB（低内存 / 性能模式）
_pixmap_cache_limit_applied = False


def _ensure_pixmap_cache_limit() -> None:
    """Raise the process-wide ``QPixmapCache`` ceiling (never lowers it)."""
    global _pixmap_cache_limit_applied
    if _pixmap_cache_limit_applied:
        return
    _pixmap_cache_limit_applied = True
    try:
        from app.config.memory_profile import QPIXMAP_CACHE_KB, is_low_memory_machine

        low = QPIXMAP_CACHE_KB <= 4096 or is_low_memory_machine()
    except Exception:
        low = False
    target = _PIXMAP_CACHE_TARGET_KB_LOW if low else _PIXMAP_CACHE_TARGET_KB
    try:
        if QPixmapCache.cacheLimit() < target:
            QPixmapCache.setCacheLimit(target)
    except Exception:
        pass


def _cell_dims(thumb_size: int) -> tuple[int, int, int]:
    return cell_dims(thumb_size)


_, _CELL_W, _CELL_H = _cell_dims(_THUMB_SIZE)  # legacy test constants


def _display_box(thumb_size: int) -> QSize:
    """The on-screen thumbnail box for one cell — must mirror ``_ThumbDelegate``'s
    ``thumb_rect`` (cell width - 4, cell height - 22)."""
    _, cw, ch = _cell_dims(thumb_size)
    return QSize(max(1, cw - 4), max(1, ch - 22))


def _cache_key_for(thumb_size: int, path: str) -> str:
    # §7 旧: return f"uidgrid#{bucket}#{path}"
    #   —— 缓存的是 bucket 原尺寸 pixmap（最大 720px≈2MB），且同一 bucket 下不同
    #      网格密度共用一条目。现在缓存的是「已缩放到显示尺寸」的 pixmap，所以
    #      key 必须带上显示框尺寸，否则密度变化会拿到尺寸不符的图去放大（糊）。
    bucket = normalize_thumb_cache_size(thumb_size) or int(thumb_size)
    box = _display_box(int(thumb_size))
    return f"uidgrid#{bucket}#{box.width()}x{box.height()}#{path}"


def _cache_key(path: str) -> str:
    """Legacy alias — default thumb size."""
    return _cache_key_for(_THUMB_SIZE, path)


def uid_abbreviation(uid: str) -> str:
    """Short workbench-style label (station-species or species-storage)."""
    from app.utils.naming import uid_display_station_species

    return uid_display_station_species(uid)


def catalog_key_for_uid(uid: str) -> str:
    """Sidebar / grid merge key — one row per specimen uniqueId."""
    from app.utils.naming import uid_group_key

    return uid_group_key(uid)


def catalog_label_for_uid(uid: str) -> str:
    """Default sidebar label: province-site-speciesId."""
    from app.utils.naming import uid_display_core

    return uid_display_core(uid)


def effective_item_uid(group_uid: str, item: dict[str, Any]) -> str:
    """Specimen uid for one grid cell (group uid, else per-file parse)."""
    return str(group_uid or item.get("_uid") or item.get("uid") or "")


def uid_matches_selection(target: str, candidate: str) -> bool:
    """True if *target* (full uid or sidebar label) matches *candidate*."""
    from app.utils.naming import normalize_uid, uid_display_core, uid_group_key

    t = str(target or "").strip()
    c = str(candidate or "").strip()
    if not t:
        return not c
    if t == c:
        return True
    if uid_group_key(t) == uid_group_key(c):
        return True
    tc, cc = uid_display_core(t), uid_display_core(c)
    if tc and (t == tc or c == tc or t == cc or c == cc or tc == cc):
        return True
    ta, ca = uid_abbreviation(t), uid_abbreviation(c)
    return ta == t or ta == c or ta == ca


def merge_groups_by_catalog_key(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge result groups that share the same specimen uniqueId."""
    from app.utils.naming import uid_group_key

    buckets: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for g in groups or []:
        uid = str(g.get("uid") or "")
        items = list(g.get("items") or [])
        if not items:
            continue
        rep = uid or str(next((it.get("uid") for it in items if it.get("uid")), "") or "")
        key = uid_group_key(rep) if rep else uid_group_key(
            str(items[0].get("uid") or "")
        )
        if key not in buckets:
            buckets[key] = {"uid": rep, "catalog_key": key, "items": []}
            order.append(key)
        bucket = buckets[key]
        if not bucket["uid"]:
            for it in items:
                if it.get("uid"):
                    bucket["uid"] = str(it["uid"])
                    break
        bucket["items"].extend(items)
    return [{"uid": buckets[k]["uid"], "items": buckets[k]["items"]} for k in order]


class UidSectionModel(QAbstractListModel):
    """One UID section: each row is a single photo (thumbnail item).

    Stores items as the raw dicts that ``project_service.get_project_results``
    returns (``{"path","name","seq", ...}``), so callers can pass that payload
    through verbatim — no reshaping required.
    """

    PATH_ROLE = Qt.ItemDataRole.UserRole          # str: absolute file path
    NAME_ROLE = Qt.ItemDataRole.UserRole + 1      # str: display filename
    SEQ_ROLE = Qt.ItemDataRole.UserRole + 2       # int | None: ordinal within UID
    UID_ROLE = Qt.ItemDataRole.UserRole + 3       # str: parent UID (full)
    ABBREV_ROLE = Qt.ItemDataRole.UserRole + 4    # str: uid_abbreviation(uid)

    def __init__(
        self,
        uid: str,
        items: list[dict[str, Any]],
        cell_size: Optional[QSize] = None,
        parent: Optional[QObject] = None,
        *,
        caption_mode: str = "smart",
        catalog_counts: Optional[dict[str, int]] = None,
    ) -> None:
        super().__init__(parent)
        self._uid = uid
        self._abbrev = uid_abbreviation(uid)
        self._items: list[dict[str, Any]] = list(items)
        self._caption_mode = caption_mode
        self._catalog_counts: dict[str, int] = dict(catalog_counts or {})
        if cell_size is None:
            _, cw, ch = cell_dims(_THUMB_SIZE)
            cell_size = QSize(cw, ch)
        self._cell_size = cell_size

    # -- QAbstractItemModel ---------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._items)

    def data(
        self,
        index: QModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._items)):
            return None
        item = self._items[index.row()]
        item_uid = effective_item_uid(str(item.get("_uid") or self._uid or ""), item)
        item_abbrev = uid_abbreviation(item_uid)
        unified_item = "_uid" in item
        if role == Qt.ItemDataRole.DisplayRole:
            from app.config.project_tree_layout import format_grid_caption

            return format_grid_caption(
                item,
                mode=self._caption_mode,
                group_uid=str(self._uid or ""),
                unified=unified_item,
                catalog_counts=self._catalog_counts,
                abbrev_fn=uid_abbreviation,
                catalog_key_fn=catalog_key_for_uid,
                effective_uid_fn=effective_item_uid,
            )
        if role == Qt.ItemDataRole.ToolTipRole:
            path = str(item.get("path", "") or "")
            if unified_item and item_uid:
                return f"{item_uid}\n{path}"
            if item_uid:
                return item_uid
            return path
        if role == Qt.ItemDataRole.SizeHintRole:
            return self._cell_size
        if role == self.PATH_ROLE:
            return str(item.get("path", "") or "")
        if role == self.NAME_ROLE:
            return str(item.get("name", "") or "")
        if role == self.SEQ_ROLE:
            seq = item.get("seq")
            return int(seq) if isinstance(seq, (int, float)) and seq == seq else seq
        if role == self.UID_ROLE:
            return item_uid
        if role == self.ABBREV_ROLE:
            return item_abbrev
        return None

    # -- helpers --------------------------------------------------------------

    def uid(self) -> str:
        return self._uid

    def abbreviation(self) -> str:
        return self._abbrev

    def item_at(self, row: int) -> Optional[dict[str, Any]]:
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    def notify_cell_changed(self, row: int) -> None:
        """Emit ``dataChanged`` for a single row so the delegate repaints it.

        Called by the widget after a thumbnail finishes async-decoding and has
        been placed in ``QPixmapCache``.
        """
        if 0 <= row < len(self._items):
            ix = self.index(row)
            self.dataChanged.emit(ix, ix)


# ── Delegate: paints one cell, requests async decode on miss ──────────────────

class _ThumbDelegate(QStyledItemDelegate):
    """Paint thumbnail + caption; on cache miss, ask the widget to decode.

    The delegate stays GUI-thread-only. The actual file decode happens in the
    shared :class:`GridThumbnailWorker`; the delegate merely reports the miss
    via ``request_fn(section_idx, row, path)`` and lets paint return.
    """

    def __init__(
        self,
        grid: "UidGroupedGrid",
        request_fn: Callable[[int, int, str], None],
        section_idx: int,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._grid = grid
        self._request = request_fn
        self._section_idx = section_idx

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: ARG002
        _, cw, ch = _cell_dims(self._grid.thumb_size())
        return QSize(cw, ch)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        painter.save()
        rect = option.rect
        ts, _, _ = _cell_dims(self._grid.thumb_size())

        # Background / selection
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, _SELECTED_BG)

        path = index.data(UidSectionModel.PATH_ROLE) or ""

        # Thumbnail fills the cell (minimal top padding)
        thumb_rect = QRect(
            rect.left() + 2,
            rect.top() + 2,
            rect.width() - 4,
            rect.height() - 22,
        )

        pixmap: Optional[QPixmap] = None
        if path:
            cache_key = _cache_key_for(ts, path)
            pixmap = QPixmapCache.find(cache_key)

        if pixmap is not None and not pixmap.isNull():
            # §7 旧: scaled = pixmap.scaled(...)  # 每帧都做一次 SmoothTransformation
            # 现在缓存里已是「显示尺寸」的 pixmap（见 _store_decoded_pixmap），
            # 命中时直接 drawPixmap；只有外部塞进来的超大 pixmap 才走缩放兜底。
            if (
                pixmap.width() <= thumb_rect.width()
                and pixmap.height() <= thumb_rect.height()
            ):
                scaled = pixmap
            else:
                scaled = pixmap.scaled(
                    thumb_rect.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            x = thumb_rect.left() + (thumb_rect.width() - scaled.width()) // 2
            y = thumb_rect.top() + (thumb_rect.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        elif path and path in self._grid._failed_paths:
            painter.fillRect(thumb_rect, _PLACEHOLDER_BG)
            painter.setPen(_FAILED_FG)
            font = QFont()
            font.setPointSize(8)
            painter.setFont(font)
            painter.drawText(thumb_rect, Qt.AlignmentFlag.AlignCenter, tr("无法加载"))
        elif path and path in self._grid._pending_paths:
            painter.fillRect(thumb_rect, _LOADING_BG)
            painter.setPen(_LOADING_FG)
            font = QFont()
            font.setPointSize(8)
            painter.setFont(font)
            painter.drawText(thumb_rect, Qt.AlignmentFlag.AlignCenter, tr("加载中…"))
            if self._section_idx >= 0:
                self._request(self._section_idx, index.row(), path)
        else:
            # Placeholder box
            painter.fillRect(thumb_rect, _PLACEHOLDER_BG)
            painter.setPen(_PLACEHOLDER_FG)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(thumb_rect.adjusted(0, 0, -1, -1))
            # Post async decode (returns immediately, never blocks paint)
            if path and self._section_idx >= 0:
                self._request(self._section_idx, index.row(), path)

        # Caption strip under the thumbnail — theme-aware, elided, high contrast
        caption = index.data(Qt.ItemDataRole.DisplayRole) or ""
        caption_h = 22
        caption_rect = QRect(
            rect.left() + 1,
            rect.bottom() - caption_h,
            rect.width() - 2,
            caption_h - 1,
        )
        painter.fillRect(caption_rect, self._grid.caption_bg())
        font = QFont()
        font.setPointSize(9 if getattr(self._grid, "unified_grid", lambda: False)() else 8)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(self._grid.caption_fg())
        elided = QFontMetrics(font).elidedText(
            str(caption),
            Qt.TextElideMode.ElideMiddle,
            max(24, caption_rect.width() - 4),
        )
        painter.drawText(
            caption_rect.adjusted(2, 0, -2, 0),
            Qt.AlignmentFlag.AlignCenter,
            elided,
        )
        painter.restore()


# ── Internal section record ──────────────────────────────────────────────────

class _Section:
    """Bundled references for one UID section (header + list + model)."""

    __slots__ = ("widget", "header", "title_label", "count_label",
                 "list_view", "model", "delegate")

    def __init__(
        self,
        widget: QFrame,
        header: QFrame,
        title_label: QLabel,
        count_label: QLabel,
        list_view: QListView,
        model: UidSectionModel,
        delegate: _ThumbDelegate,
    ) -> None:
        self.widget = widget
        self.header = header
        self.title_label = title_label
        self.count_label = count_label
        self.list_view = list_view
        self.model = model
        self.delegate = delegate


# ── Main widget ───────────────────────────────────────────────────────────────

class UidGroupedGrid(QFrame):
    """Virtualized thumbnail grid grouped by UID.

    Two input modes (spec §3 — reuse ``project_service.get_project_results``):

      * ``set_groups(groups)`` where each ``group = {"uid": str, "items": [...]}``
        and each ``item = {"path": str, "name": str, "seq": int | None, ...}``.
        This is the verbatim shape of ``get_project_results(...)["groups"]``.
      * ``set_paths(paths)`` flat mode: wraps all paths in one unnamed section
        (``uid_abbreviation("") == "未分组"``).
    """

    photo_selected = pyqtSignal(str, object)  # path, item dict
    catalog_changed = pyqtSignal()

    THUMB_SIZE = _THUMB_SIZE

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._sections: list[_Section] = []
        self._req_counter: int = 0
        self._thumb_size: int = _THUMB_SIZE
        self._density_index: int | None = None
        self._sort_mode: str = "uid_seq"
        from app.config.project_tree_layout import DEFAULT_GRID_CAPTION

        self._caption_mode: str = DEFAULT_GRID_CAPTION
        self._catalog_counts: dict[str, int] = {}
        self._last_groups: list[dict[str, Any]] = []
        self._unified = False
        self._unified_host: Optional[QWidget] = None
        self._unified_list: Optional[QListView] = None
        # request_id -> (section_idx, row, path). Cleared on every set_groups;
        # a decoded() whose id is missing here is stale and dropped.
        self._pending: dict[int, tuple[int, int, str]] = {}
        # Paths with an in-flight request (avoids duplicate decode posts).
        self._pending_paths: set[str] = set()
        # Paths whose decode returned None (negative cache; no busy re-request).
        self._failed_paths: set[str] = set()
        self._settings = None
        # path -> 已插入 QPixmapCache 的 key 集合（refresh 时精确剔除；key 现在
        # 含显示框尺寸，不能再靠 CACHE_SIZE_BUCKETS 反推）。
        self._pixmap_keys: dict[str, set[str]] = {}
        # 解码回填的脏格子：section_idx -> {row}，由 _dirty_timer 攒批刷新，
        # 避免上千次单格 dataChanged 自身构成信号风暴。
        self._dirty_cells: dict[int, set[int]] = {}
        self._loading_cover_active = False
        self._loading_cover_pending: set[str] = set()
        self._loading_cover_timer = QTimer(self)
        self._loading_cover_timer.setSingleShot(True)
        self._loading_cover_timer.setInterval(_INITIAL_COVER_TIMEOUT_MS)
        self._loading_cover_timer.timeout.connect(self._on_loading_cover_timeout)

        # QPixmapCache 上限（只升不降），否则一屏都缓存不下，命中率恒为 0。
        _ensure_pixmap_cache_limit()

        # 三个「归主线程所有」的单发定时器（复用，不重复新建）:
        #   _repaint_timer  — 重建后合并一次 可见重绘 + 可见带预取
        #   _prefetch_timer — 滚动 debounce 后增量预取
        #   _dirty_timer    — 解码回填的脏格子攒批刷新
        self._repaint_timer = QTimer(self)
        self._repaint_timer.setSingleShot(True)
        self._repaint_timer.timeout.connect(self._on_repaint_timer)
        self._prefetch_timer = QTimer(self)
        self._prefetch_timer.setSingleShot(True)
        self._prefetch_timer.timeout.connect(self._prefetch_visible_thumbnails)
        self._dirty_timer = QTimer(self)
        self._dirty_timer.setSingleShot(True)
        self._dirty_timer.setInterval(_DIRTY_FLUSH_MS)
        self._dirty_timer.timeout.connect(self._flush_dirty_cells)

        # Worker thread — NOT parented to ``self`` so Qt's parent-child teardown
        # can't race our quit()+wait() (memory: shutdown-lock-leak-must-reboot).
        self._thread = _new_qthread()
        self._worker = GridThumbnailWorker()
        self._worker.moveToThread(self._thread)
        self._worker.decoded.connect(self._on_decoded)
        self._thread.start()

        # Teardown on Qt-side destruction. The closure captures the thread by
        # value so it still works after the Python wrapper for ``self`` is gone.
        _thread_ref = self._thread

        def _cleanup(*_a: object) -> None:
            try:
                _thread_ref.quit()
                _thread_ref.wait(2000)
            except Exception:
                pass

        self._cleanup_fn = _cleanup  # keep a strong ref so GC never reaps it
        self.destroyed.connect(_cleanup)

        self._setup_ui()

    # -- UI scaffolding -------------------------------------------------------

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)

        self._summary = QLabel("0 个编号 / 0 张照片")
        f = self._summary.font()
        f.setPointSize(9)
        self._summary.setFont(f)
        outer.addWidget(self._summary)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._container = QWidget()
        cl = QVBoxLayout(self._container)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(10)
        cl.addStretch(1)  # sections inserted before this stretch
        self._scroll.setWidget(self._container)
        outer.addWidget(self._scroll, 1)

        # polish: wrap in tr() for i18n consistency with sibling loading-state labels (Sonnet 5 multi-agent review)
        # self._loading_cover = QLabel("正在静默准备首屏照片…", self._scroll.viewport())
        self._loading_cover = QLabel(tr("正在静默准备首屏照片…"), self._scroll.viewport())
        self._loading_cover.setObjectName("EmptyState")
        self._loading_cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_cover.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._loading_cover.hide()

        # 滚动 → debounce → 只补预取新进入可见带的格子（新增预取的唯一入口）。
        # 定时器归主线程所有，且 _setup_ui 只在 __init__ 调一次，不会重复连接。
        try:
            self._scroll.verticalScrollBar().valueChanged.connect(
                self._on_scrolled
            )
        except Exception:
            pass

    def _on_scrolled(self, _value: int = 0) -> None:
        self._prefetch_timer.start(_PREFETCH_SCROLL_DEBOUNCE_MS)

    def bind_settings(self, settings) -> None:
        """Sync preview master-size profile from persisted settings."""
        self._settings = settings
        from app.config import preview_profile as pp

        pp.set_preview_master_size(settings.project_tree_preview_master_size)

    def preview_master_size(self) -> int:
        from app.config.preview_profile import current_preview_master_size

        return current_preview_master_size()

    def set_preview_master_size(self, size: int) -> None:
        from app.config import preview_profile as pp
        from app.config.project_tree_layout import clamp_preview_master_size

        size = clamp_preview_master_size(size)
        pp.set_preview_master_size(size)
        if self._settings is not None:
            self._settings.project_tree_preview_master_size = size
        self.refresh_all_thumbnails()

    def refresh_thumbnail(self, path: str) -> None:
        if not path:
            return
        clear_thumbnail_cache(path)
        self._failed_paths.discard(path)
        self._pending_paths.discard(path)
        self._drop_pixmap_cache_for(path)
        for sec_idx, sec in enumerate(self._sections):
            model = sec.model
            for row in range(model.rowCount()):
                p = model.data(model.index(row), UidSectionModel.PATH_ROLE) or ""
                if p == path:
                    self._request_decode(sec_idx, row, path)
                    if sec.list_view is not None:
                        sec.list_view.viewport().update()
                    return

    def refresh_all_thumbnails(self) -> None:
        clear_thumbnail_cache()
        self._failed_paths.clear()
        self._pending_paths.clear()
        self._pixmap_keys.clear()
        QPixmapCache.clear()
        self._schedule_visible_repaint()

    def _drop_pixmap_cache_for(self, path: str) -> None:
        from app.utils.thumbnail_disk_cache import CACHE_SIZE_BUCKETS

        # key 现在含显示框尺寸, 无法只靠 bucket 反推 —— 精确剔除本 widget 实际
        # 插入过的 key（_pixmap_keys 记录）。旧的按 bucket 猜测保留为兜底。
        for key in self._pixmap_keys.pop(path, set()):
            QPixmapCache.remove(key)
        sizes = {self._thumb_size, normalize_thumb_cache_size(self._thumb_size)}
        sizes.update(CACHE_SIZE_BUCKETS)
        for bucket in sizes:
            if bucket is None:
                continue
            QPixmapCache.remove(_cache_key_for(int(bucket), path))

    # -- Public API -----------------------------------------------------------

    def set_groups(
        self,
        groups: list[dict[str, Any]],
        *,
        thumb_size: int | None = None,
    ) -> None:
        """Replace all sections.

        ``groups = [{"uid": str, "items": [{"path","name","seq", ...}, ...]}, ...]``
        — exactly the shape returned by
        :func:`project_service.get_project_results` under the ``"groups"`` key.

        Pass ``thumb_size`` to resize and rebuild in one step (avoids a second
        rebuild from auto-fit after the grid is already populated).
        """
        self._last_groups = merge_groups_by_catalog_key(list(groups or []))
        if any(g.get("items") for g in self._last_groups):
            self._show_loading_cover()
        else:
            self._hide_loading_cover()
        if thumb_size is not None:
            self._thumb_size = clamp_thumb_size(thumb_size)
        self._failed_paths.clear()
        self._rebuild_sections(self._last_groups)

    def set_unified_grid(self, unified: bool) -> None:
        """``True`` = 单网格铺满中栏（项目树成片预览）；``False`` = 按编号分段（汇总页）."""
        unified = bool(unified)
        if unified == self._unified:
            return
        self._unified = unified
        if self._last_groups or unified:
            self._rebuild_sections(self._last_groups)

    def suggested_thumb_size(self) -> int:
        """Thumb size for the current density preset (or default ~4 columns)."""
        if self._density_index is not None:
            return thumb_size_for_density(self._viewport_width(), self._density_index)
        return auto_fit_thumb_for_viewport(self._viewport_width())

    def density_index(self) -> int | None:
        return self._density_index

    def set_density_index(self, index: int) -> None:
        """Apply a grid-density slider index (maps to column presets)."""
        from app.config.project_tree_layout import clamp_density_index

        idx = clamp_density_index(index)
        if idx == self._density_index:
            size = thumb_size_for_density(self._viewport_width(), idx)
            if size != self._thumb_size:
                self.set_thumb_size(size)
            return
        self._density_index = idx
        self.set_thumb_size(thumb_size_for_density(self._viewport_width(), idx))

    def sort_mode(self) -> str:
        return self._sort_mode

    def set_sort_mode(self, mode: str) -> None:
        from app.config.project_tree_layout import normalize_grid_sort

        mode = normalize_grid_sort(mode)
        if mode == self._sort_mode:
            return
        self._sort_mode = mode
        if self._last_groups:
            self._failed_paths.clear()
            self._rebuild_sections(self._last_groups)

    def caption_mode(self) -> str:
        return self._caption_mode

    def set_caption_mode(self, mode: str) -> None:
        from app.config.project_tree_layout import normalize_grid_caption_mode

        mode = normalize_grid_caption_mode(mode)
        if mode == self._caption_mode:
            return
        self._caption_mode = mode
        if self._last_groups:
            self._rebuild_sections(self._last_groups)

    def caption_fg(self) -> QColor:
        from app.config.theme import TOKENS

        return QColor(TOKENS.get("text_soft", "#334155"))

    def caption_bg(self) -> QColor:
        from app.config.theme import TOKENS

        c = QColor(TOKENS.get("panel_inset", "#eef2f6"))
        c.setAlpha(235)
        return c

    def unified_grid(self) -> bool:
        return self._unified

    def set_paths(self, paths: list[str]) -> None:
        """Flat mode: one unnamed section holding every path.

        ``item["name"]`` defaults to ``Path(path).name``; ``seq`` is ``None``
        so the caption falls back to the filename.
        """
        items = [
            {
                "path": str(p),
                "name": (Path(p).name if p else ""),
                "seq": None,
            }
            for p in (paths or [])
        ]
        self.set_groups([{"uid": "", "items": items}])

    def clear(self) -> None:
        """Remove all sections (equivalent to ``set_groups([])``)."""
        self.set_groups([])

    def teardown(self) -> None:
        """Stop the worker thread. Call from the parent view's
        ``on_deactivate`` / ``closeEvent`` (also auto-invoked on ``destroyed``).
        """
        # 主线程持有的定时器 —— 在主线程 stop（Qt: 只能在所属线程 start/stop）。
        for timer in (
            getattr(self, "_repaint_timer", None),
            getattr(self, "_prefetch_timer", None),
            getattr(self, "_dirty_timer", None),
        ):
            try:
                if timer is not None:
                    timer.stop()
            except Exception:
                pass
        try:
            self._thread.quit()
            self._thread.wait(2000)
        except Exception:
            pass
        export_worker = getattr(self, "_batch_export_worker", None)
        if export_worker is not None and export_worker.isRunning():
            export_worker.cancel()
            export_worker.wait(30000)

    # -- Accessors (tests / parent view) --------------------------------------

    def section_count(self) -> int:
        if self._unified:
            return 1 if self._unified_list and self._unified_list.model() else 0
        return len(self._sections)

    def uid_catalog(self) -> list[dict[str, Any]]:
        """Ordered 编号 list (one row per visible abbrev, merged photo count)."""
        groups = sort_result_groups(list(self._last_groups or []), self._sort_mode)
        out: list[dict[str, Any]] = []
        for g in groups:
            uid = str(g.get("uid") or "")
            items = list(g.get("items") or [])
            if not items:
                continue
            rep = uid or str(items[0].get("uid") or "")
            ck = catalog_key_for_uid(rep)
            abbrev = catalog_label_for_uid(rep)
            if ck == "__ungrouped__":
                abbrev = "未分组"
            out.append({
                "uid": rep,
                "catalog_key": ck,
                "abbrev": abbrev,
                "count": len(items),
            })
        return out

    def first_photo_for_uid(self, uid: str) -> Optional[tuple[str, dict[str, Any]]]:
        """First photo path + item dict for *uid* / abbrev (``None`` if missing)."""
        target = str(uid or "")
        if self._unified and self._sections:
            model = self._sections[0].model
            for row in range(model.rowCount()):
                item = model.item_at(row) or {}
                row_uid = effective_item_uid(str(item.get("_uid") or ""), item)
                if not uid_matches_selection(target, row_uid):
                    continue
                path = model.data(model.index(row, 0), UidSectionModel.PATH_ROLE) or ""
                if path:
                    return str(path), dict(item)
            return None
        for sec in self._sections:
            sec_uid = effective_item_uid(sec.model.uid(), {})
            if not uid_matches_selection(target, sec_uid):
                continue
            if sec.model.rowCount() <= 0:
                return None
            item = sec.model.item_at(0) or {}
            path = sec.model.data(sec.model.index(0, 0), UidSectionModel.PATH_ROLE) or ""
            if path:
                return str(path), dict(item)
        return None

    def scroll_to_uid(self, uid: str) -> bool:
        """Scroll grid to *uid* / abbrev and select its first photo."""
        target = str(uid or "")
        if self._unified and self._sections:
            model = self._sections[0].model
            lv = self._sections[0].list_view
            for row in range(model.rowCount()):
                item = model.item_at(row) or {}
                row_uid = effective_item_uid(str(item.get("_uid") or ""), item)
                if not uid_matches_selection(target, row_uid):
                    continue
                ix = model.index(row, 0)
                lv.setCurrentIndex(ix)
                lv.scrollTo(ix, QAbstractItemView.ScrollHint.PositionAtTop)
                return True
            return False
        for sec in self._sections:
            sec_uid = effective_item_uid(sec.model.uid(), {})
            if not uid_matches_selection(target, sec_uid):
                continue
            if sec.model.rowCount() > 0:
                ix = sec.model.index(0, 0)
                sec.list_view.setCurrentIndex(ix)
            self._scroll.ensureWidgetVisible(sec.widget, 0, int(_HEADER_H / 2))
            return True
        return False

    def section(self, idx: int) -> Optional[_Section]:
        if 0 <= idx < len(self._sections):
            return self._sections[idx]
        return None

    def summary_text(self) -> str:
        return self._summary.text()

    def thumb_size(self) -> int:
        return self._thumb_size

    def set_thumb_size(self, size: int) -> None:
        """Resize grid cells; rebuilds from last ``set_groups`` payload."""
        size = clamp_thumb_size(size)
        if size == self._thumb_size:
            return
        self._thumb_size = size
        if self._last_groups:
            self._rebuild_sections(self._last_groups)

    def auto_fit_thumb_size(self) -> int:
        """Pick a thumb size that fills the current viewport."""
        size = auto_fit_thumb_for_viewport(self._viewport_width())
        self.set_thumb_size(size)
        return size

    def _catalog_counts_for_groups(self, groups: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for g in groups or []:
            uid = str(g.get("uid") or "")
            for item in g.get("items") or []:
                ck = catalog_key_for_uid(effective_item_uid(uid, item))
                counts[ck] = counts.get(ck, 0) + 1
        return counts

    def _rebuild_sections(self, groups: list[dict[str, Any]]) -> None:
        groups = sort_result_groups(groups, self._sort_mode)
        self._catalog_counts = self._catalog_counts_for_groups(groups)
        if self._unified:
            self._rebuild_unified(groups)
            self.catalog_changed.emit()
            return
        self._clear_unified()
        self._clear_sections()
        total = 0
        for gi, g in enumerate(groups or []):
            uid = str(g.get("uid") or "")
            items = list(g.get("items") or [])
            total += len(items)
            sec = self._make_section(gi, uid, items)
            self._sections.append(sec)
            self._container_layout().insertWidget(
                self._container_layout().count() - 1, sec.widget
            )
        self._summary.setText(f"{len(self._sections)} 个编号 / {total} 张照片")
        self._reflow_section_heights()
        self._schedule_visible_repaint()
        self.catalog_changed.emit()

    def _schedule_visible_repaint(self) -> None:
        """Ensure visible cells paint (and post decode requests) after rebuild."""
        # §7 旧: QTimer.singleShot(0, self._repaint_visible_cells)
        # §7 旧: QTimer.singleShot(0, self._prefetch_all_thumbnails)
        #   —— singleShot 无去重, 连续 3 次筛选 = 3 份【全表】主线程扫描排队。
        # 现在: 一个复用的单发成员定时器, 重复 start 只重置不叠加。
        self._repaint_timer.start(0)

    def _on_repaint_timer(self) -> None:
        self._repaint_visible_cells()
        self._prefetch_visible_thumbnails()

    # §7 旧实现 —— 主线程遍历「全部分组的全部行」, 每张都 try_cached_image_data
    # (Path.resolve + stat + sha256 + 读盘 + 全量 JPEG 解码), 汇总上千张时秒级冻结;
    # 且没有任何视口裁剪。已由 _prefetch_visible_thumbnails 取代。
    #
    # def _prefetch_all_thumbnails(self) -> None:
    #     """Instant disk/memory hits; queue remaining decodes shortly after."""
    #     ts = self._thumb_size
    #     missing: list[tuple[int, int, str]] = []
    #     for sec_idx, sec in enumerate(self._sections):
    #         model = sec.model
    #         for row in range(model.rowCount()):
    #             path = model.data(model.index(row), UidSectionModel.PATH_ROLE) or ""
    #             if not path or path in self._failed_paths:
    #                 continue
    #             if QPixmapCache.find(_cache_key_for(ts, path)) is not None:
    #                 continue
    #             cached = try_cached_image_data(path, ts)
    #             if cached is not None and not cached.isNull():
    #                 self._store_decoded_pixmap(sec_idx, row, path, cached)
    #                 continue
    #             if path in self._pending_paths:
    #                 continue
    #             missing.append((sec_idx, row, path))
    #     if missing:
    #         QTimer.singleShot(50, lambda m=list(missing): self._prefetch_missing_decodes(m))
    #
    # def _prefetch_missing_decodes(self, items: list[tuple[int, int, str]]) -> None:
    #     for sec_idx, row, path in items:
    #         if path in self._failed_paths or path in self._pending_paths:
    #             continue
    #         if QPixmapCache.find(_cache_key_for(self._thumb_size, path)) is not None:
    #             continue
    #         self._request_decode(sec_idx, row, path)

    # Claude Code 修改 2026-07-15 — codex 回归实测复现: offscreen QPA 插件不支持
    # propagateSizeHints(), _container 的 QVBoxLayout 收不到各 section 的尺寸,
    # 40 个 section 全挤在 y=0 附近, 导致 _visible_row_span 把老远的 section 也
    # 判成可见。每个 section 的 list_view 高度是显式 setFixedHeight 定的(可靠),
    # 这里直接用这些显式高度手算 _container 应有的总高度并 resize+activate,
    # 不依赖会在这个 QPA 插件上失效的自动尺寸传播。
    def _settle_container_geometry(self) -> None:
        try:
            layout = self._container.layout()
            if layout is None or not self._sections:
                return
            total = 0
            for sec in self._sections:
                lv = sec.list_view
                if lv is None:
                    continue
                total += _HEADER_H + 4 + lv.height()  # 4 = wrapper QVBoxLayout spacing
            spacing = layout.spacing()
            if spacing > 0:
                total += spacing * max(0, len(self._sections) - 1)
            margins = layout.contentsMargins()
            total += margins.top() + margins.bottom()
            if self._container.height() < total:
                self._container.resize(self._container.width(), total)
            layout.activate()
        except Exception:
            pass

    # Claude Code 修改 2026-07-14 — BUG1: 加 prefetch 开关, 使调用方能单独取「严格可见视口」跨距, 严格可见行必须无视预算全部请求
    def _visible_row_span(
        self, sec: "_Section", prefetch: bool = True
    ) -> Optional[tuple[int, int]]:
        """Rows of *sec* intersecting the visible band (外层 QScrollArea 视口 ±1 屏).

        每个 section 的 QListView 被 ``_apply_section_height`` 撑成「全部行的总高」,
        滚动的是外层 QScrollArea —— 所以 ``lv.viewport().rect()`` 覆盖该 section 的
        全部行, ``indexAt`` 会把所有行都算成可见。必须以外层视口为基准做映射。

        ``prefetch=True`` 返回视口 ±1 屏的预取带; ``prefetch=False`` 只返回严格
        落在视口内的行 —— 首屏 loading 罩必须保证这些行全部被请求。
        """
        lv = sec.list_view
        model = sec.model
        if lv is None or model is None:
            return None
        n = model.rowCount()
        if n <= 0:
            return None
        try:
            vp = self._scroll.viewport()
            # §7 旧: band = vp.rect().adjusted(0, -vp.height(), 0, vp.height())
            # Claude Code 修改 2026-07-14 — BUG1: prefetch 时才向上下各扩一屏, 严格可见只取视口本身
            if prefetch:
                band = vp.rect().adjusted(0, -vp.height(), 0, vp.height())
            else:
                band = vp.rect()
            lvp = lv.viewport()
            top_left = lvp.mapFrom(vp, band.topLeft())
            r = QRect(top_left, band.size()).intersected(lvp.rect())
        except Exception:
            return None
        if r.isEmpty():
            return None  # 整个 section 在屏外 —— 一行都不碰
        _, cw, ch = _cell_dims(self._thumb_size)
        cols = max(1, lvp.width() // max(1, cw))
        first_line = max(0, r.top() // max(1, ch))
        last_line = max(first_line, r.bottom() // max(1, ch))
        first = first_line * cols
        if first > n - 1:
            return None
        last = min(n - 1, (last_line + 1) * cols - 1)
        return first, max(first, last)

    def _prefetch_visible_thumbnails(self) -> None:
        """预取「可见带」内尚未缓存的缩略图 —— 只投递解码请求给 worker 线程.

        主线程在这里【不做任何磁盘 I/O】: 内存/磁盘缓存的快路径由 worker 的
        ``decode_image_data(use_cache=True)`` 负责（它本来就先查内存缓存再查磁盘
        缓存），把它从 GUI 线程删掉是零功能损失。
        """
        # Claude Code 修改 2026-07-15 — codex 回归实测复现的真根因: set_groups()
        # 之后立刻(singleShot(0))触发的第一轮预取, 此时 QVBoxLayout 还没跑完真正
        # 的布局计算, 各 section 的 list_view 在 _container 里的位置全部挤在
        # y=0 附近的陈旧/默认值(哪怕它们各自已经 setFixedHeight 撑到几千像素高)。
        # 根因进一步实测确认: offscreen QPA 插件明确打印
        # "This plugin does not support propagateSizeHints()" —— 子控件的
        # sizeHint 传不到 _container 这层, layout().activate() 单独调没用
        # (container 自己的尺寸没被撑开, 40 个 section 全挤在同一块窄区域里
        # 重叠, mapTo/mapFrom 算出来的坐标因此把老远的 section 也判成可见)。
        # 每个 section 自己的 list_view 高度是用 setFixedHeight 显式定的(可靠,
        # 不依赖 sizeHint 传播), 这里改成直接用这些显式高度手算 _container 应有
        # 的总高度并 resize 上去, 再 activate 布局——不靠"迟早会跑到"的传播。
        self._settle_container_geometry()
        ts = self._thumb_size
        budget = _PREFETCH_BUDGET
        # Claude Code 修改 2026-07-14 — codex 回归: 严格可见行也要设一个"本轮"上限,
        # 防止小缩略图+大视口时单次调用派发几百个请求; 超额部分留到下一轮 tick,
        # 不是放弃(BUG1 不能复发), 是分批。
        visible_budget = _VISIBLE_PREFETCH_BUDGET
        saw_visible_span = False
        visible_rows_remaining = False
        for sec_idx, sec in enumerate(self._sections):
            # §7 旧: span = self._visible_row_span(sec)  # 只取 ±1 屏预取带
            # Claude Code 修改 2026-07-14 — BUG1: 分别取预取带与严格可见跨距, 前者受预算约束, 后者永远请求
            band = self._visible_row_span(sec)
            if band is None:
                continue
            saw_visible_span = True
            b_first, b_last = band
            strict = self._visible_row_span(sec, prefetch=False)
            s_first, s_last = strict if strict is not None else (None, None)
            model = sec.model
            # §7 旧: 预算耗尽即 ``return`` —— 会连后续 section 一起吞掉, 且首屏可见格可能没请求罩就先撤了
            # Claude Code 修改 2026-07-14 — BUG1: 预算 cutoff 只作用于预取带(非严格可见)行, 严格可见行无条件请求
            for row in range(b_first, b_last + 1):
                in_view = strict is not None and s_first <= row <= s_last
                if not in_view and budget <= 0:
                    continue  # 预取带超预算 —— 跳过本行但不放弃其余严格可见行/后续 section
                if in_view and visible_budget <= 0:
                    # Claude Code 修改 2026-07-14 — 本轮严格可见预算也用完: 不放弃这一格
                    # (否则又是 BUG1), 标记"还有剩的", 留给下一轮 tick 继续请求。
                    visible_rows_remaining = True
                    continue
                path = model.data(model.index(row), UidSectionModel.PATH_ROLE) or ""
                if not path or path in self._failed_paths or path in self._pending_paths:
                    continue
                if QPixmapCache.find(_cache_key_for(ts, path)) is not None:
                    continue
                self._request_decode(sec_idx, row, path)
                if not in_view:
                    budget -= 1  # 只有预取带的请求消耗预算
                else:
                    visible_budget -= 1
        if visible_rows_remaining:
            # Claude Code 修改 2026-07-14 — 排空剩余严格可见行: 短延时后自己再跑一轮,
            # 而不是等下一次滚动/重建事件(那样可能永远不来, 格子会一直空着)。
            QTimer.singleShot(_PREFETCH_CONTINUATION_MS, self._prefetch_visible_thumbnails)
        if (
            saw_visible_span
            and not visible_rows_remaining
            and self._loading_cover_active
            and not self._loading_cover_pending
        ):
            self._hide_loading_cover()

    def _show_loading_cover(self) -> None:
        self._loading_cover_active = True
        self._loading_cover_pending.clear()
        self._loading_cover.setGeometry(self._scroll.viewport().rect())
        self._loading_cover.show()
        self._loading_cover.raise_()
        self._loading_cover_timer.start()

    def _on_loading_cover_timeout(self) -> None:
        # Slow cold decodes must not expose a half-painted first viewport.
        if self._loading_cover_active and self._loading_cover_pending:
            self._loading_cover.raise_()
            self._loading_cover_timer.start()
            return
        self._hide_loading_cover()

    def _hide_loading_cover(self) -> None:
        if not hasattr(self, "_loading_cover"):
            return
        self._loading_cover_timer.stop()
        self._loading_cover_active = False
        self._loading_cover_pending.clear()
        if self._dirty_cells:
            self._dirty_timer.stop()
            self._flush_dirty_cells()
        self._loading_cover.hide()
        self._scroll.viewport().update()

    def _store_decoded_pixmap(
        self,
        section_idx: int,
        row: int,
        path: str,
        image: object,
    ) -> None:
        """Main thread only (``_on_decoded`` 的 queued 槽) —— QPixmap 只能主线程构造."""
        pixmap = make_pixmap(image)  # type: ignore[arg-type]
        if pixmap is None or pixmap.isNull():
            self._failed_paths.add(path)
            return
        # §7 旧: QPixmapCache.insert(_cache_key_for(self._thumb_size, path), pixmap)
        #   —— 存 bucket 原尺寸(最大 720px≈2MB), paint 每帧还要再平滑缩放一次。
        # 现在: 插入前缩到显示尺寸, 单条内存降到 ~1/10, paint 直接 drawPixmap。
        box = _display_box(self._thumb_size)
        if pixmap.width() > box.width() or pixmap.height() > box.height():
            pixmap = pixmap.scaled(
                box,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        key = _cache_key_for(self._thumb_size, path)
        QPixmapCache.insert(key, pixmap)
        self._pixmap_keys.setdefault(path, set()).add(key)
        # §7 旧: sec.model.notify_cell_changed(row) + lv.viewport().update(...)
        #   —— 逐格 dataChanged; 上千格回填时信号本身就是一场风暴。
        # 现在: 攒进 _dirty_cells, 由 ~16ms 的单发定时器合并刷新。
        if 0 <= section_idx < len(self._sections):
            self._dirty_cells.setdefault(section_idx, set()).add(row)
            if not self._dirty_timer.isActive():
                self._dirty_timer.start(_DIRTY_FLUSH_MS)

    def _flush_dirty_cells(self) -> None:
        """合并刷新已解码回填的格子（主线程）。"""
        dirty = self._dirty_cells
        self._dirty_cells = {}
        for section_idx, rows in dirty.items():
            if not (0 <= section_idx < len(self._sections)) or not rows:
                continue
            sec = self._sections[section_idx]
            model = sec.model
            n = model.rowCount()
            valid = [r for r in rows if 0 <= r < n]
            if not valid:
                continue
            lo, hi = min(valid), max(valid)
            top, bottom = model.index(lo), model.index(hi)
            if top.isValid() and bottom.isValid():
                model.dataChanged.emit(top, bottom)
            lv = sec.list_view
            if lv is not None:
                lv.viewport().update()

    def _repaint_visible_cells(self) -> None:
        for sec in self._sections:
            lv = sec.list_view
            if lv is not None:
                lv.viewport().update()

    def _flatten_group_items(self, groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        flat: list[dict[str, Any]] = []
        for g in groups or []:
            uid = str(g.get("uid") or "")
            for item in g.get("items") or []:
                row = dict(item)
                row["_uid"] = effective_item_uid(uid, item)
                flat.append(row)
        return sort_flat_grid_items(flat, self._sort_mode)

    def _rebuild_unified(self, groups: list[dict[str, Any]]) -> None:
        """Single full-width grid — no per-UID gray bands."""
        self._clear_unified()
        self._clear_sections()
        items = self._flatten_group_items(groups)
        uid_count = len([g for g in (groups or []) if g.get("items")])
        self._summary.setText(f"{uid_count} 个编号 / {len(items)} 张照片")
        if not items:
            return
        _, cw, ch = _cell_dims(self._thumb_size)
        cell_size = QSize(cw, ch)
        host = QFrame()
        host.setObjectName("uidUnifiedHost")
        hl = QVBoxLayout(host)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(0)
        model = UidSectionModel(
            "",
            items,
            cell_size,
            parent=self,
            caption_mode=self._caption_mode,
            catalog_counts=self._catalog_counts,
        )
        delegate = _ThumbDelegate(self, self._request_decode, 0, parent=self)
        list_view = QListView()
        list_view.setObjectName("uidUnifiedGrid")
        list_view.setViewMode(QListView.ViewMode.IconMode)
        list_view.setGridSize(cell_size)
        list_view.setUniformItemSizes(True)
        list_view.setResizeMode(QListView.ResizeMode.Adjust)
        list_view.setMovement(QListView.Movement.Static)
        list_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        list_view.setWrapping(True)
        list_view.setSpacing(GRID_CELL_SPACING)
        list_view.setFrameShape(QFrame.Shape.NoFrame)
        list_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        list_view.setItemDelegate(delegate)
        list_view.setModel(model)
        list_view.clicked.connect(lambda ix: self._on_cell_clicked(0, ix))
        self._wire_list_context_menu(list_view, 0)
        hl.addWidget(list_view)
        self._unified_host = host
        self._unified_list = list_view
        self._sections = [_Section(host, QFrame(), QLabel(), QLabel(), list_view, model, delegate)]
        self._container_layout().insertWidget(
            self._container_layout().count() - 1, host
        )
        self._reflow_unified_height()
        self._schedule_visible_repaint()
        self.catalog_changed.emit()

    def _clear_unified(self) -> None:
        if self._unified_host is not None:
            from app.utils.ui import remove_widget_from_layout

            remove_widget_from_layout(self._container_layout(), self._unified_host)
            if self._unified_list is not None:
                self._unified_list.setModel(None)
        self._unified_host = None
        self._unified_list = None

    def _reflow_unified_height(self) -> None:
        if self._unified_list is None or not self._sections:
            return
        model = self._sections[0].model
        _, cw, ch = _cell_dims(self._thumb_size)
        rows = self._rows_for_count(model.rowCount(), cw)
        self._unified_list.setFixedHeight(rows * ch + 8)

    # -- Internal: section construction ---------------------------------------

    def _container_layout(self) -> QVBoxLayout:
        return self._container.layout()  # type: ignore[return-value]

    def _clear_sections(self) -> None:
        from app.utils.ui import remove_widget_from_layout

        for sec in self._sections:
            sec.list_view.setModel(None)
            remove_widget_from_layout(self._container_layout(), sec.widget)
        self._sections.clear()
        # Drop all in-flight requests: any reply that lands now is stale.
        self._pending.clear()
        self._pending_paths.clear()
        # Claude Code 修改 2026-07-14 — BUG2: 被丢弃的在途请求其回复会 stale-drop, 永不 discard; 不同步清空会让罩的 pending 永久非空、再也无法隐藏
        self._loading_cover_pending.clear()
        # 陈旧的脏格子指向已销毁的 section, 一并丢弃。
        self._dirty_cells.clear()
        # NOTE: ``_failed_paths`` is intentionally NOT cleared here — a path
        # that genuinely failed to decode (missing/corrupt file) will fail
        # again next time; re-requesting it on every paint is a busy loop.

    def _make_section(
        self,
        section_idx: int,
        uid: str,
        items: list[dict[str, Any]],
    ) -> _Section:
        abbrev = uid_abbreviation(uid)
        _, cw, ch = _cell_dims(self._thumb_size)
        cell_size = QSize(cw, ch)

        # Header: title left, count right
        header = QFrame()
        header.setObjectName("uidSectionHeader")
        header.setFixedHeight(_HEADER_H)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(4, 0, 4, 0)
        hl.setSpacing(8)
        title = QLabel(f"<b>{abbrev}</b>")
        title.setToolTip("")
        title.setTextFormat(Qt.TextFormat.RichText)
        count = QLabel(f"×{len(items)}")
        count.setStyleSheet("color:#94a3b8;")
        hl.addWidget(title)
        hl.addStretch(1)
        hl.addWidget(count)

        # Model + delegate + view
        model = UidSectionModel(
            uid,
            items,
            cell_size,
            parent=self,
            caption_mode=self._caption_mode,
            catalog_counts=self._catalog_counts,
        )
        delegate = _ThumbDelegate(self, self._request_decode, section_idx, parent=self)
        list_view = QListView()
        list_view.setObjectName("uidSectionList")
        list_view.setViewMode(QListView.ViewMode.IconMode)
        list_view.setGridSize(cell_size)
        list_view.setUniformItemSizes(True)
        list_view.setResizeMode(QListView.ResizeMode.Adjust)
        list_view.setMovement(QListView.Movement.Static)
        list_view.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        list_view.setWrapping(True)
        list_view.setItemDelegate(delegate)
        list_view.setModel(model)
        list_view.setSpacing(GRID_CELL_SPACING)
        list_view.setFrameShape(QFrame.Shape.NoFrame)
        list_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        list_view.clicked.connect(
            lambda ix, si=section_idx: self._on_cell_clicked(si, ix)
        )
        self._wire_list_context_menu(list_view, section_idx)

        wrapper = QFrame()
        wrapper.setObjectName("uidSection")
        wl = QVBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(4)
        wl.addWidget(header)
        wl.addWidget(list_view)
        self._apply_section_height(sec := _Section(
            wrapper, header, title, count, list_view, model, delegate
        ))
        return sec

    def _viewport_width(self) -> int:
        try:
            return max(SPLIT_MIN_GRID, self._scroll.viewport().width() - GRID_VIEWPORT_PAD)
        except Exception:
            return max(200, self.width() - 24)

    def _rows_for_count(self, count: int, cell_w: int) -> int:
        if count <= 0:
            return 1
        cols = max(1, self._viewport_width() // max(1, cell_w))
        return max(1, (count + cols - 1) // cols)

    def _apply_section_height(self, sec: _Section) -> None:
        _, cw, ch = _cell_dims(self._thumb_size)
        rows = self._rows_for_count(sec.model.rowCount(), cw)
        sec.list_view.setFixedHeight(rows * ch + 4)

    def _reflow_section_heights(self) -> None:
        for sec in self._sections:
            self._apply_section_height(sec)

    def resizeEvent(self, event) -> None:  # noqa: D401 - Qt override
        super().resizeEvent(event)
        if hasattr(self, "_loading_cover"):
            self._loading_cover.setGeometry(self._scroll.viewport().rect())
        if self._density_index is not None:
            size = thumb_size_for_density(self._viewport_width(), self._density_index)
            if size != self._thumb_size and self._last_groups:
                self._thumb_size = size
                self._rebuild_sections(self._last_groups)
                return
        if self._unified:
            self._reflow_unified_height()
        else:
            self._reflow_section_heights()

    def showEvent(self, event) -> None:  # noqa: D401 - Qt override
        super().showEvent(event)
        if self._unified:
            self._reflow_unified_height()
        else:
            self._reflow_section_heights()
        self._schedule_visible_repaint()

    def _on_cell_clicked(self, section_idx: int, index: QModelIndex) -> None:
        if not index.isValid() or not (0 <= section_idx < len(self._sections)):
            return
        model = self._sections[section_idx].model
        path = model.data(index, UidSectionModel.PATH_ROLE) or ""
        item = dict(model.item_at(index.row()) or {})
        if model.uid() and not item.get("_uid") and not item.get("uid"):
            item["_uid"] = model.uid()
        elif not item.get("_uid"):
            item["_uid"] = effective_item_uid(model.uid(), item)
        if path:
            self.photo_selected.emit(str(path), item)

    def _wire_list_context_menu(self, list_view: QListView, section_idx: int) -> None:
        list_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        list_view.customContextMenuRequested.connect(
            lambda pos, si=section_idx, lv=list_view: self._on_photo_context_menu(
                si, lv, pos
            )
        )

    def _on_photo_context_menu(
        self,
        section_idx: int,
        list_view: QListView,
        pos: QPoint,
    ) -> None:
        index = list_view.indexAt(pos)
        global_pos = list_view.viewport().mapToGlobal(pos)
        if not index.isValid():
            self._show_grid_background_menu(global_pos)
            return
        if not (0 <= section_idx < len(self._sections)):
            return
        model = self._sections[section_idx].model
        path = str(model.data(index, UidSectionModel.PATH_ROLE) or "")
        if not path:
            return
        item = model.item_at(index.row()) or {}
        selected_entries = self._context_selected_photo_entries(list_view, index)
        selected_paths = [entry_path for entry_path, _entry in selected_entries]
        selected_count = len(selected_paths)
        suffix = f"（{selected_count}）" if selected_count > 1 else ""
        menu = QMenu(self)
        act_preview = menu.addAction(tr("大图预览…"))
        act_preview.triggered.connect(
            lambda: self._open_large_preview(path, item)
        )
        act_reveal = menu.addAction(tr("在文件夹中显示"))
        act_reveal.triggered.connect(lambda: self._reveal_path(path))
        act_copy = menu.addAction(tr("复制所选路径") + suffix)
        act_copy.triggered.connect(lambda: self._copy_paths(selected_paths))
        menu.addSeparator()
        act_refresh = menu.addAction(
            (tr("刷新所选缩略图") + suffix)
            if selected_count > 1
            else tr("刷新此图")
        )
        act_refresh.triggered.connect(lambda: self._refresh_paths(selected_paths))
        act_refresh_all = menu.addAction(tr("刷新全部"))
        act_refresh_all.triggered.connect(self.refresh_all_thumbnails)
        menu.addSeparator()
        act_export = menu.addAction(
            (tr("导出所选为 JPG…") + suffix)
            if selected_count > 1
            else tr("导出为 JPG…")
        )
        act_export.triggered.connect(
            lambda: (
                self._export_photos_jpg(selected_paths)
                if selected_count > 1
                else self._export_photo_jpg(path)
            )
        )
        quality_menu = menu.addMenu(tr("预览清晰度"))
        current = self.preview_master_size()
        labels = {
            512: tr("省空间 (512px)"),
            720: tr("标准 (720px)"),
            1080: tr("高清 (1080px)"),
            1440: tr("超清 (1440px)"),
        }
        for preset in PREVIEW_MASTER_PRESETS:
            label = labels.get(preset, f"{preset}px")
            if preset == current:
                label = f"✓ {label}"
            action = quality_menu.addAction(label)
            action.triggered.connect(
                lambda _=False, p=preset: self.set_preview_master_size(p)
            )
        menu.exec(global_pos)

    def _show_grid_background_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        act_refresh_all = menu.addAction(tr("刷新全部缩略图"))
        act_refresh_all.triggered.connect(self.refresh_all_thumbnails)
        menu.exec(global_pos)

    def _open_large_preview(self, path: str, item: dict) -> None:
        from app.widgets.result_photo_preview_dialog import ResultPhotoPreviewDialog

        title = str(item.get("name") or Path(path).name)
        dlg = ResultPhotoPreviewDialog(path, title=title, parent=self.window())
        dlg.exec()

    def _reveal_path(self, path: str) -> None:
        if not reveal_in_directory(path):
            ui.warn(self, tr("打开失败"), tr("无法在文件夹中显示此文件。"))

    def _copy_path(self, path: str) -> None:
        QApplication.clipboard().setText(path)

    def _copy_paths(self, paths: list[str]) -> None:
        QApplication.clipboard().setText("\n".join(paths))

    def _refresh_paths(self, paths: list[str]) -> None:
        for path in paths:
            self.refresh_thumbnail(path)

    def _context_selected_photo_entries(
        self, list_view: QListView, clicked_index: QModelIndex
    ) -> list[tuple[str, dict]]:
        selection = list_view.selectionModel()
        if selection is None:
            return []
        if not selection.isSelected(clicked_index):
            for section in self._sections:
                section.list_view.clearSelection()
            list_view.setCurrentIndex(clicked_index)
            selection.select(
                clicked_index,
                QItemSelectionModel.SelectionFlag.ClearAndSelect,
            )

        entries: list[tuple[str, dict]] = []
        seen: set[str] = set()
        for section in self._sections:
            selected = sorted(
                section.list_view.selectionModel().selectedIndexes(),
                key=lambda selected_index: selected_index.row(),
            )
            for selected_index in selected:
                selected_path = str(
                    section.model.data(selected_index, UidSectionModel.PATH_ROLE) or ""
                )
                if not selected_path or selected_path in seen:
                    continue
                entries.append(
                    (selected_path, dict(section.model.item_at(selected_index.row()) or {}))
                )
                seen.add(selected_path)
        if not entries:
            selected_path = str(
                list_view.model().data(clicked_index, UidSectionModel.PATH_ROLE) or ""
            )
            if selected_path:
                clicked_item = list_view.model().item_at(clicked_index.row()) or {}
                entries.append((selected_path, dict(clicked_item)))
        return entries

    def _export_photos_jpg(self, paths: list[str]) -> None:
        from app.services.tiff_jpeg_export_service import (
            OverwritePolicy,
            TiffJpegExportSettings,
        )
        from app.workers.tiff_jpeg_export_worker import TiffJpegExportWorker

        sources = [
            str(path) for path in paths
            if str(path).lower().endswith((".tif", ".tiff")) and Path(path).is_file()
        ]
        if not sources:
            ui.warn(self, tr("批量导出 JPG"), tr("所选项目中没有有效的 TIFF 文件。"))
            return
        worker = getattr(self, "_batch_export_worker", None)
        if worker is not None and worker.isRunning():
            ui.info(self, tr("批量导出 JPG"), tr("已有导出任务正在进行。"))
            return
        self._summary.setText(tr("正在并行导出 {count} 张 JPG…").format(count=len(sources)))
        worker = TiffJpegExportWorker(
            sources,
            TiffJpegExportSettings(),
            recursive=False,
            overwrite=OverwritePolicy.RENAME,
            smart=True,
            parent=self,
        )
        self._batch_export_worker = worker
        worker.progress.connect(
            lambda current, total, name: self._summary.setText(
                tr("正在导出 {current}/{total}：{name}").format(
                    current=current, total=total, name=name
                )
            )
        )
        worker.finished.connect(self._on_batch_export_finished)
        worker.failed.connect(self._on_batch_export_failed)
        worker.start()

    def _on_batch_export_finished(self, result) -> None:
        self._batch_export_worker = None
        converted = len(list(getattr(result, "converted", []) or []))
        skipped = len(list(getattr(result, "skipped", []) or []))
        failed = list(getattr(result, "failed", []) or [])
        self._summary.setText(
            tr("批量导出完成：成功 {ok}，跳过 {skip}，失败 {fail}").format(
                ok=converted, skip=skipped, fail=len(failed)
            )
        )
        message = tr("成功 {ok}，跳过 {skip}，失败 {fail}").format(
            ok=converted, skip=skipped, fail=len(failed)
        )
        if failed:
            detail = "\n".join(f"• {Path(path).name}：{reason}" for path, reason in failed[:6])
            ui.warn(self, tr("批量导出完成（有失败项）"), f"{message}\n{detail}")
        else:
            ui.info(self, tr("批量导出完成"), message)

    def _on_batch_export_failed(self, reason: str) -> None:
        self._batch_export_worker = None
        self._summary.setText(tr("批量导出失败"))
        ui.warn(self, tr("批量导出失败"), reason)

    def _export_photo_jpg(self, path: str) -> None:
        from PyQt6.QtWidgets import QMessageBox

        from app.services.tiff_jpeg_export_service import (
            OverwritePolicy,
            TiffJpegExportSettings,
            analyze_tiff,
            convert_tiff_to_jpeg,
            recommend_export_settings,
        )

        dest = Path(path).with_suffix(".jpg")
        policy = OverwritePolicy.OVERWRITE
        if dest.exists():
            btn = ui.question(
                self,
                tr("导出 JPG"),
                tr("已存在 {name}，是否覆盖？").format(name=dest.name),
            )
            if btn != QMessageBox.StandardButton.Yes:
                return
        analysis = analyze_tiff(path)
        settings = (
            recommend_export_settings(analysis)
            if analysis is not None
            else TiffJpegExportSettings()
        )
        try:
            result = convert_tiff_to_jpeg(path, str(dest), settings, overwrite=policy)
        except Exception as exc:
            ui.warn(self, tr("导出失败"), str(exc))
            return
        ui.info(
            self,
            tr("导出完成"),
            tr("已保存：{path}\n{w}×{h} · {kb:.0f} KB").format(
                path=result.output,
                w=result.width,
                h=result.height,
                kb=result.bytes_written / 1024,
            ),
        )

    # -- Async decode plumbing ------------------------------------------------

    def _request_decode(self, section_idx: int, row: int, path: str) -> None:
        """Called from the delegate's paint (GUI thread).

        Posts a queued ``decode`` to the worker and returns immediately. The
        reply lands in :meth:`_on_decoded`. Deduplicates per path and skips
        already-cached / known-failed paths.
        """
        if not path or path in self._failed_paths:
            return
        if path in self._pending_paths:
            return
        ts = self._thumb_size
        if QPixmapCache.find(_cache_key_for(ts, path)) is not None:
            return
        # §7 旧: cached = try_cached_image_data(path, ts)
        # §7 旧: if cached is not None and not cached.isNull():
        # §7 旧:     self._store_decoded_pixmap(section_idx, row, path, cached)
        # §7 旧:     return
        #   —— 这条在 delegate.paint() 的调用栈里同步做 Path.resolve + stat +
        #      sha256 + 读盘 + 全量 JPEG 解码 (~2ms/张, 一屏 30 格 ≈ 60ms 卡在
        #      paint 上), 并在 paint 内递归触发 dataChanged/viewport.update。
        #   —— 删除是零功能损失: worker 的 decode_image_data(use_cache=True)
        #      (image_thumbnail.py) 本来就先查内存缓存、再查磁盘缓存
        #      (read_disk_thumbnail), 这条快路径在 worker 线程上已经具备。
        # paint 现在只允许 QPixmapCache.find + drawPixmap; 未命中就画占位、标
        # pending、把解码投递给 worker。
        self._req_counter += 1
        req_id = self._req_counter
        self._pending[req_id] = (section_idx, row, path)
        self._pending_paths.add(path)
        if self._loading_cover_active:
            self._loading_cover_pending.add(path)
        QMetaObject.invokeMethod(
            self._worker,
            "decode",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(int, req_id),
            Q_ARG(str, path),
            Q_ARG(int, ts),
        )

    @pyqtSlot(object, object)
    def _on_decoded(self, req_id: object, image: object) -> None:
        """Worker replied (main thread via queued connection)."""
        try:
            key = int(req_id)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return
        info = self._pending.pop(key, None)
        if info is None:
            return  # stale (model was reset since this was posted)
        section_idx, row, path = info
        self._pending_paths.discard(path)

        pixmap = make_pixmap(image)  # type: ignore[arg-type]
        if pixmap is None or pixmap.isNull():
            # Negative cache — don't keep asking for a file that won't decode.
            self._failed_paths.add(path)
            self._loading_cover_pending.discard(path)
            if self._loading_cover_active and not self._loading_cover_pending:
                self._hide_loading_cover()
            return
        self._store_decoded_pixmap(section_idx, row, path, image)
        self._loading_cover_pending.discard(path)
        if self._loading_cover_active and not self._loading_cover_pending:
            self._hide_loading_cover()


# ── Module-level helpers ──────────────────────────────────────────────────────

def _new_qthread():
    """Indirection so tests can patch the QThread factory if needed."""
    from PyQt6.QtCore import QThread
    return QThread()
