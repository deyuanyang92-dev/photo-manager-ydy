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
    QMetaObject,
    QModelIndex,
    QObject,
    QPoint,
    QRect,
    QSize,
    Qt,
    pyqtSlot,
)
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap, QPixmapCache
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QScrollArea,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from app.utils.image_thumbnail import make_pixmap
from app.workers.thumbnail_worker import GridThumbnailWorker

__all__ = ["UidGroupedGrid", "UidSectionModel", "uid_abbreviation"]


# ── Layout constants ──────────────────────────────────────────────────────────

_THUMB_SIZE = 112                       # thumbnail edge length, px (spec: ~112)
_CELL_W = _THUMB_SIZE + 16              # cell width  (thumb + 8 px side padding)
_CELL_H = _THUMB_SIZE + 30              # cell height (thumb + caption strip)
_HEADER_H = 26                          # section header row height
_PLACEHOLDER_BG = QColor("#2a3548")     # empty cell fill (theme-neutral dark)
_PLACEHOLDER_FG = QColor("#64748b")     # empty cell border / icon stroke
_CAPTION_FG = QColor("#cbd5e1")         # caption text colour
_SELECTED_BG = QColor("#1e3a5f")        # selected cell tint


def uid_abbreviation(uid: str) -> str:
    """Short human label for a UID: ``站点-序号`` (e.g. ``B2-001``).

    UID layout (oracle ``db-utils.js``, ``[province]-[site]-[station]-<speciesId>
    -[storage]-[dateSegment]``). The 3rd segment is the station, the 4th the
    per-station species/specimen id; together they are the natural short handle
    (the same shape the web oracle shows on the workbench card). Falls back to
    the UID tail (or ``未分组`` for empty UIDs) when the shape doesn't match —
    e.g. the flat-mode "ungrouped" bucket or any free-form value.
    """
    if not uid:
        return "未分组"
    parts = uid.split("-")
    if len(parts) >= 4 and parts[2] and parts[3]:
        return f"{parts[2]}-{parts[3]}"
    # Non-standard shape — show the tail so the user still has something.
    return uid if len(uid) <= 24 else ("…" + uid[-23:])


def _cache_key(path: str) -> str:
    """QPixmapCache key scoped to this widget's thumb size (avoids collisions
    with other widgets decoding the same path at a different size)."""
    return f"uidgrid#{_THUMB_SIZE}#{path}"


# ── Per-section list model ────────────────────────────────────────────────────

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
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._uid = uid
        self._abbrev = uid_abbreviation(uid)
        self._items: list[dict[str, Any]] = list(items)

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
        if role == Qt.ItemDataRole.DisplayRole:
            seq = item.get("seq")
            if seq is not None:
                return f"#{seq}"
            return item.get("name", "")
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._uid
        if role == Qt.ItemDataRole.SizeHintRole:
            return QSize(_CELL_W, _CELL_H)
        if role == self.PATH_ROLE:
            return str(item.get("path", "") or "")
        if role == self.NAME_ROLE:
            return str(item.get("name", "") or "")
        if role == self.SEQ_ROLE:
            seq = item.get("seq")
            return int(seq) if isinstance(seq, (int, float)) and seq == seq else seq
        if role == self.UID_ROLE:
            return self._uid
        if role == self.ABBREV_ROLE:
            return self._abbrev
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
        request_fn: Callable[[int, int, str], None],
        section_idx: int,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._request = request_fn
        self._section_idx = section_idx

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: ARG002
        return QSize(_CELL_W, _CELL_H)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        painter.save()
        rect = option.rect

        # Background / selection
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, _SELECTED_BG)

        path = index.data(UidSectionModel.PATH_ROLE) or ""

        # Thumbnail square (centred horizontally in the cell)
        thumb_rect = QRect(
            rect.left() + (rect.width() - _THUMB_SIZE) // 2,
            rect.top() + 6,
            _THUMB_SIZE,
            _THUMB_SIZE,
        )

        pixmap: Optional[QPixmap] = None
        if path:
            pixmap = QPixmapCache.find(_cache_key(path))

        if pixmap is not None and not pixmap.isNull():
            scaled = pixmap.scaled(
                _THUMB_SIZE,
                _THUMB_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = thumb_rect.left() + (_THUMB_SIZE - scaled.width()) // 2
            y = thumb_rect.top() + (_THUMB_SIZE - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        else:
            # Placeholder box
            painter.fillRect(thumb_rect, _PLACEHOLDER_BG)
            painter.setPen(_PLACEHOLDER_FG)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(thumb_rect.adjusted(0, 0, -1, -1))
            # Post async decode (returns immediately, never blocks paint)
            if path and self._section_idx >= 0:
                self._request(self._section_idx, index.row(), path)

        # Caption strip (#seq or filename) under the thumbnail
        caption = index.data(Qt.ItemDataRole.DisplayRole) or ""
        caption_rect = QRect(
            rect.left() + 2,
            rect.bottom() - 22,
            rect.width() - 4,
            18,
        )
        painter.setPen(_CAPTION_FG)
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        painter.drawText(
            caption_rect,
            Qt.AlignmentFlag.AlignCenter,
            str(caption),
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

    THUMB_SIZE = _THUMB_SIZE

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._sections: list[_Section] = []
        self._req_counter: int = 0
        # request_id -> (section_idx, row, path). Cleared on every set_groups;
        # a decoded() whose id is missing here is stale and dropped.
        self._pending: dict[int, tuple[int, int, str]] = {}
        # Paths with an in-flight request (avoids duplicate decode posts).
        self._pending_paths: set[str] = set()
        # Paths whose decode returned None (negative cache; no busy re-request).
        self._failed_paths: set[str] = set()

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
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        self._summary = QLabel("0 个编号 / 0 张照片")
        f = self._summary.font()
        f.setPointSize(9)
        self._summary.setFont(f)
        outer.addWidget(self._summary)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
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

    # -- Public API -----------------------------------------------------------

    def set_groups(self, groups: list[dict[str, Any]]) -> None:
        """Replace all sections.

        ``groups = [{"uid": str, "items": [{"path","name","seq", ...}, ...]}, ...]``
        — exactly the shape returned by
        :func:`project_service.get_project_results` under the ``"groups"`` key.
        """
        self._clear_sections()
        total = 0
        for gi, g in enumerate(groups or []):
            uid = str(g.get("uid") or "")
            items = list(g.get("items") or [])
            total += len(items)
            sec = self._make_section(gi, uid, items)
            self._sections.append(sec)
            # Insert before the trailing stretch.
            self._container_layout().insertWidget(
                self._container_layout().count() - 1, sec.widget
            )
        self._summary.setText(f"{len(self._sections)} 个编号 / {total} 张照片")

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
        try:
            self._thread.quit()
            self._thread.wait(2000)
        except Exception:
            pass

    # -- Accessors (tests / parent view) --------------------------------------

    def section_count(self) -> int:
        return len(self._sections)

    def section(self, idx: int) -> Optional[_Section]:
        if 0 <= idx < len(self._sections):
            return self._sections[idx]
        return None

    def summary_text(self) -> str:
        return self._summary.text()

    def thumb_size(self) -> int:
        return _THUMB_SIZE

    # -- Internal: section construction ---------------------------------------

    def _container_layout(self) -> QVBoxLayout:
        return self._container.layout()  # type: ignore[return-value]

    def _clear_sections(self) -> None:
        for sec in self._sections:
            self._container_layout().removeWidget(sec.widget)
            sec.list_view.setModel(None)
            sec.widget.setParent(None)
            sec.widget.deleteLater()
        self._sections.clear()
        # Drop all in-flight requests: any reply that lands now is stale.
        self._pending.clear()
        self._pending_paths.clear()
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

        # Header: title left, count right
        header = QFrame()
        header.setObjectName("uidSectionHeader")
        header.setFixedHeight(_HEADER_H)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(4, 0, 4, 0)
        hl.setSpacing(8)
        title = QLabel(f"<b>{abbrev}</b>")
        title.setToolTip(uid or "未分组")
        title.setTextFormat(Qt.TextFormat.RichText)
        count = QLabel(f"×{len(items)}")
        count.setStyleSheet("color:#94a3b8;")
        hl.addWidget(title)
        hl.addStretch(1)
        hl.addWidget(count)

        # Model + delegate + view
        model = UidSectionModel(uid, items, parent=self)
        delegate = _ThumbDelegate(self._request_decode, section_idx, parent=self)
        list_view = QListView()
        list_view.setObjectName("uidSectionList")
        list_view.setViewMode(QListView.ViewMode.IconMode)
        list_view.setGridSize(QSize(_CELL_W, _CELL_H))
        list_view.setUniformItemSizes(True)
        list_view.setResizeMode(QListView.ResizeMode.Adjust)
        list_view.setMovement(QListView.Movement.Static)
        list_view.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        list_view.setWrapping(True)
        list_view.setUniformItemSizes(True)
        list_view.setItemDelegate(delegate)
        list_view.setModel(model)
        # Show ~2 rows by default; the scroll area grows the section anyway.
        list_view.setMinimumHeight(_CELL_H * 2 + 16)
        list_view.setUniformItemSizes(True)

        wrapper = QFrame()
        wrapper.setObjectName("uidSection")
        wl = QVBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(4)
        wl.addWidget(header)
        wl.addWidget(list_view, 1)

        return _Section(wrapper, header, title, count, list_view, model, delegate)

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
        if QPixmapCache.find(_cache_key(path)) is not None:
            return  # another section already cached it
        self._req_counter += 1
        req_id = self._req_counter
        self._pending[req_id] = (section_idx, row, path)
        self._pending_paths.add(path)
        QMetaObject.invokeMethod(
            self._worker,
            "decode",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(int, req_id),
            Q_ARG(str, path),
            Q_ARG(int, _THUMB_SIZE),
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
            return
        QPixmapCache.insert(_cache_key(path), pixmap)
        if 0 <= section_idx < len(self._sections):
            self._sections[section_idx].model.notify_cell_changed(row)


# ── Module-level helpers ──────────────────────────────────────────────────────

def _new_qthread():
    """Indirection so tests can patch the QThread factory if needed."""
    from PyQt6.QtCore import QThread
    return QThread()
