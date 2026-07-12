"""project_card.py — 项目树卡片视图单卡 (spec §4.2).

性能/线程约定（2026-07-12 重做）:
  * 主线程**零磁盘 I/O**：封面的「路径选取(SQLite + 递归扫目录) + 解码」整体
    丢进一条**常驻工作线程**(`_CoverLoadWorker` QObject + moveToThread，范式抄
    `app/workers/thumbnail_worker.py::GridThumbnailWorker`)。
  * 工作线程只 emit ``QImage``（线程安全）；``QPixmap`` 只在主线程用
    ``make_pixmap`` 构造。
  * 封面路径按**目录 stat 签名**做 memo 缓存，避免重复扫盘；
    ``invalidate_cover_path_cache()`` 供 ``refresh_cover`` 精确失效。
  * 只给**视口内可见**的卡片派发任务；滚动/resize/show 用一个归本 widget 所有
    的单次 ``QTimer(0ms)`` 合并触发（定时器复用，不重建；只在主线程 start/stop）。
  * 退出只 ``quit() + wait(2000)`` 一次。

§7: 被替换的旧逻辑（每卡一条 ``CoverThumbnailWorker`` QThread、主线程
``pick_project_cover_path``、逐个 ``wait(500)``）以 ``#`` 注释保留在文末归档区。
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import (
    Q_ARG,
    QMetaObject,
    QObject,
    QPoint,
    QRect,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.config import icons
from app.services.cover_pick_service import pick_project_cover_path
from app.utils.image_thumbnail import decode_image_data, make_pixmap
# §7 旧: 每张卡起一条一次性 QThread —— N 卡 = N 个 OS 线程同时解 TIFF。
# from app.workers.thumbnail_worker import CoverThumbnailWorker


# ── 封面路径 memo 缓存（按目录 stat 签名失效） ─────────────────────────────────
#
# ``pick_project_cover_path`` 会开 SQLite + 递归扫 results/incoming-jpg，代价高。
# 目录内容变化必然改变下列路径之一的 mtime/size，因此用它们的 stat 组成签名：
# 签名不变 ⇒ 直接复用上次结果（连 stat 之外的 I/O 都不做）。
_COVER_SIG_PATHS: tuple[str, ...] = (
    "",  # 项目根
    "results",
    "results/freeform",
    "incoming-jpg",
    "新拍JPG",
    "_data/project.db",  # 手动封面写在 project_meta 里
)

_COVER_PATH_CACHE: dict[str, tuple[tuple, Optional[str]]] = {}
_COVER_PATH_LOCK = threading.Lock()


def _cover_dir_signature(directory: str) -> tuple:
    root = Path(directory)
    parts: list[tuple[str, int, int]] = []
    for rel in _COVER_SIG_PATHS:
        target = root / rel if rel else root
        try:
            st = target.stat()
            parts.append((rel, int(st.st_mtime_ns), int(st.st_size)))
        except OSError:
            parts.append((rel, -1, -1))
    return tuple(parts)


def resolve_cover_path(directory: str) -> Optional[str]:
    """Cover path for *directory*, memoized on the directory's stat signature.

    Runs on the cover worker thread — never call from the GUI thread.
    """
    if not directory:
        return None
    sig = _cover_dir_signature(directory)
    with _COVER_PATH_LOCK:
        hit = _COVER_PATH_CACHE.get(directory)
        if hit is not None and hit[0] == sig:
            return hit[1]
    try:
        path = pick_project_cover_path(directory)
    except Exception:
        path = None
    with _COVER_PATH_LOCK:
        _COVER_PATH_CACHE[directory] = (sig, path)
    return path


def invalidate_cover_path_cache(directory: Optional[str] = None) -> None:
    """Drop the memoized cover path for *directory* (or the whole cache)."""
    with _COVER_PATH_LOCK:
        if directory is None:
            _COVER_PATH_CACHE.clear()
        else:
            _COVER_PATH_CACHE.pop(str(directory), None)


class _CoverLoadWorker(QObject):
    """Long-lived: lives on a worker ``QThread`` (grid creates + moveToThread).

    One task = 「选路径 + 解码」，两步都在工作线程里做，主线程零磁盘 I/O。
    Emits ``QImage | None`` only — 绝不在这里构造 ``QPixmap``（GUI 资源类，
    非主线程构造是未定义行为）。
    """

    loaded = pyqtSignal(object, object)  # (request_id: int, QImage | None)

    @pyqtSlot(int, str, int)
    def load(self, request_id: int, directory: str, max_size: int) -> None:
        path = resolve_cover_path(directory)
        image = decode_image_data(path, max_size) if path else None
        self.loaded.emit(request_id, image)


class ProjectCard(QFrame):
    """One project / candidate card — cover, stats, primary actions."""

    enter_clicked = pyqtSignal(str)
    adopt_clicked = pyqtSignal(str)
    selection_toggled = pyqtSignal(str, bool)  # directory, selected
    set_cover_requested = pyqtSignal(str)
    clear_cover_requested = pyqtSignal(str)
    open_in_tree_requested = pyqtSignal(str)

    def __init__(
        self,
        directory: str,
        *,
        name: Optional[str] = None,
        specimen_count: Optional[int] = None,
        pending_count: Optional[int] = None,
        available: bool = True,
        is_candidate: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._directory = directory
        self._is_candidate = is_candidate
        self._selected = False
        self.setObjectName("ProjectCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(220)
        self.setMaximumWidth(300)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        cover_row = QHBoxLayout()
        self._cover = QLabel()
        self._cover.setObjectName("ProjectCardCover")
        self._cover.setFixedSize(220, 132)
        self._cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cover.setScaledContents(True)
        cover_row.addWidget(self._cover, 1, Qt.AlignmentFlag.AlignCenter)
        self._badge = QLabel("")
        self._badge.setObjectName("ProjectCardBadge")
        self._badge.hide()
        cover_row.addWidget(self._badge, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(cover_row)

        title_row = QHBoxLayout()
        self._dot = QLabel("●")
        self._dot.setObjectName("ProjectCardDot")
        title_row.addWidget(self._dot)
        display = name or Path(directory).name or directory
        self._title = QLabel(display)
        self._title.setObjectName("ProjectCardTitle")
        self._title.setWordWrap(True)
        title_row.addWidget(self._title, 1)
        self._check_mark = QLabel("")
        self._check_mark.setObjectName("ProjectCardCheck")
        self._check_mark.setFixedWidth(18)
        self._check_mark.hide()
        title_row.addWidget(self._check_mark)
        root.addLayout(title_row)

        self._stats = QLabel("")
        self._stats.setObjectName("ProjectCardStats")
        self._stats.setWordWrap(True)
        root.addWidget(self._stats)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._btn_enter = QPushButton("进入工作区")
        self._btn_enter.setObjectName("Primary")
        self._btn_enter.setFixedHeight(32)
        self._btn_enter.clicked.connect(lambda: self.enter_clicked.emit(self._directory))
        btn_row.addWidget(self._btn_enter)
        self._btn_adopt = QPushButton("认领")
        self._btn_adopt.setObjectName("Outline")
        self._btn_adopt.setFixedHeight(32)
        self._btn_adopt.clicked.connect(lambda: self.adopt_clicked.emit(self._directory))
        self._btn_adopt.hide()
        btn_row.addWidget(self._btn_adopt)
        root.addLayout(btn_row)

        self.set_available(available)
        self.set_candidate(is_candidate)
        self.set_counts(specimen_count, pending_count)
        self._set_placeholder_cover(display)
        self._apply_selected_style()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: D401
        if event.button() == Qt.MouseButton.LeftButton:
            self.enter_clicked.emit(self._directory)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def directory(self) -> str:
        return self._directory

    def is_selected(self) -> bool:
        return self._selected

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self._apply_selected_style()

    def _apply_selected_style(self) -> None:
        self.setProperty("selected", "true" if self._selected else "false")
        if self._selected:
            self._check_mark.setText("✓")
            self._check_mark.show()
        else:
            self._check_mark.hide()
            self._check_mark.setText("")
        # force QSS re-eval
        self.style().unpolish(self)
        self.style().polish(self)

    def set_cover_pixmap(self, pm: Optional[QPixmap]) -> None:
        if pm is not None and not pm.isNull():
            self._cover.setPixmap(
                pm.scaled(
                    self._cover.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            self._set_placeholder_cover(self._title.text())

    def _set_placeholder_cover(self, label: str) -> None:
        letter = (label.strip()[:1] or "?").upper()
        pm = QPixmap(self._cover.size())
        pm.fill(Qt.GlobalColor.transparent)
        from PyQt6.QtGui import QColor, QPainter

        pm.fill(QColor("#1e293b"))
        p = QPainter(pm)
        p.setPen(QColor("#94a3b8"))
        f = p.font()
        f.setPointSize(28)
        f.setBold(True)
        p.setFont(f)
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, letter)
        p.end()
        self._cover.setPixmap(pm)

    def set_available(self, available: bool) -> None:
        self._dot.setStyleSheet(
            f"color: {'#22c55e' if available else '#94a3b8'}; font-size: 10px;"
        )
        self.setEnabled(True)
        self._btn_enter.setEnabled(available or self._is_candidate)
        if not available:
            self._title.setToolTip("磁盘未连接或路径不可用")

    def set_candidate(self, is_candidate: bool) -> None:
        self._is_candidate = is_candidate
        if is_candidate:
            self._btn_enter.setText("预览")
            self._btn_adopt.show()
        else:
            self._btn_enter.setText("进入工作区")
            self._btn_adopt.hide()

    def set_counts(
        self,
        specimen_count: Optional[int],
        pending_count: Optional[int] = None,
    ) -> None:
        parts: list[str] = []
        if specimen_count is not None:
            parts.append(f"{specimen_count} 标本")
        if pending_count is not None and pending_count > 0:
            parts.append(f"待处理 {pending_count}")
            self._badge.setText(str(pending_count))
            self._badge.show()
        else:
            self._badge.hide()
        self._stats.setText(" · ".join(parts) if parts else "—")

    def _show_context_menu(self, pos) -> None:
        menu = QMenu(self)
        act_enter = menu.addAction("进入工作区")
        act_enter.triggered.connect(lambda: self.enter_clicked.emit(self._directory))
        if self._is_candidate:
            act_adopt = menu.addAction("认领此文件夹")
            act_adopt.triggered.connect(lambda: self.adopt_clicked.emit(self._directory))
        menu.addSeparator()
        act_cover = menu.addAction("设置封面…")
        act_cover.triggered.connect(lambda: self.set_cover_requested.emit(self._directory))
        act_clear = menu.addAction("恢复自动封面")
        act_clear.triggered.connect(lambda: self.clear_cover_requested.emit(self._directory))
        menu.addSeparator()
        act_tree = menu.addAction("在树视图中打开")
        act_tree.triggered.connect(lambda: self.open_in_tree_requested.emit(self._directory))
        menu.exec(self.mapToGlobal(pos))


class ProjectCardGrid(QFrame):
    """Flow grid of :class:`ProjectCard` — always shows full registered set."""

    enter_requested = pyqtSignal(str)
    adopt_requested = pyqtSignal(str)
    card_selected = pyqtSignal(str)
    selection_changed = pyqtSignal(list)  # list[str] directories
    set_cover_requested = pyqtSignal(str)
    clear_cover_requested = pyqtSignal(str)
    open_in_tree_requested = pyqtSignal(str)
    summarize_requested = pyqtSignal(list)  # list[str] directories

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ProjectCardGrid")
        self._cards: dict[str, ProjectCard] = {}
        self._selected: set[str] = set()
        self._filter_text = ""
        self._entry_meta: dict[str, str] = {}  # directory -> searchable haystack
        # §7 旧: self._cover_workers: list[CoverThumbnailWorker] = []
        self._cover_req = 0
        # 常驻封面线程（懒起，见 _ensure_cover_worker）
        self._cover_thread: Optional[QThread] = None
        self._cover_worker: Optional[_CoverLoadWorker] = None
        # destroyed 只在这里连一次, 线程存进 holder —— 否则「teardown → 再进页面 →
        # 又起线程」每轮都会 destroyed.connect 一个新闭包: N 次进出 = N 个残留连接,
        # 各自捏着一条已死线程的引用。闭包只捕获 holder 不捕获 self: destroyed 触发时
        # Python 包装对象可能已经不在了。
        self._cover_thread_holder: list[QThread] = []
        _holder = self._cover_thread_holder

        def _cleanup_threads(*_a: object) -> None:
            for th in list(_holder):
                try:
                    th.quit()
                    th.wait(2000)
                except Exception:  # pragma: no cover - 防御性
                    pass
            _holder.clear()

        self._cover_cleanup_fn = _cleanup_threads  # 强引用，防 GC
        self.destroyed.connect(_cleanup_threads)
        self._cover_pending: dict[int, str] = {}  # request_id -> directory
        self._cover_done: set[str] = set()  # 已派发（含在途）的目录
        self._cover_eligible: set[str] = set()  # available 的目录才派发
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 4, 8, 0)
        toolbar.setSpacing(8)
        self._hint = QLabel("Ctrl+点击多选 · 双击进入 · 右键设封面 · Ctrl+A 全选")
        self._hint.setObjectName("MutedSmall")
        toolbar.addWidget(self._hint)
        toolbar.addStretch(1)
        self._sel_lbl = QLabel("")
        self._sel_lbl.setObjectName("MutedSmall")
        toolbar.addWidget(self._sel_lbl)
        self._btn_select_all = QPushButton("全选")
        self._btn_select_all.setObjectName("Outline")
        self._btn_select_all.setFixedHeight(28)
        self._btn_select_all.clicked.connect(self.select_all)
        toolbar.addWidget(self._btn_select_all)
        self._btn_clear = QPushButton("清除")
        self._btn_clear.setObjectName("Ghost")
        self._btn_clear.setFixedHeight(28)
        self._btn_clear.clicked.connect(self.clear_selection)
        toolbar.addWidget(self._btn_clear)
        self._btn_summarize = QPushButton("查看汇总")
        self._btn_summarize.setObjectName("Primary")
        self._btn_summarize.setFixedHeight(28)
        self._btn_summarize.setEnabled(False)
        self._btn_summarize.setToolTip("在树视图中打开选中项目的数据汇总")
        self._btn_summarize.clicked.connect(self._emit_summarize)
        toolbar.addWidget(self._btn_summarize)
        outer.addLayout(toolbar)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._host = QWidget()
        self._grid = QGridLayout(self._host)
        self._grid.setContentsMargins(12, 8, 12, 12)
        self._grid.setHorizontalSpacing(16)
        self._grid.setVerticalSpacing(16)
        self._scroll.setWidget(self._host)
        outer.addWidget(self._scroll, 1)

        # 单次定时器（归本 widget 所有 ⇒ 主线程 start/stop）。滚动 / resize /
        # show / 过滤 都只是 start() 它，多次触发自动合并成一次派发。
        self._cover_timer = QTimer(self)
        self._cover_timer.setSingleShot(True)
        self._cover_timer.setInterval(0)
        self._cover_timer.timeout.connect(self._dispatch_visible_covers)
        self._scroll.verticalScrollBar().valueChanged.connect(
            self._schedule_cover_dispatch
        )

    # ── 封面：可见性派发 + 常驻线程 ───────────────────────────────────────────

    def _schedule_cover_dispatch(self, *_a: object) -> None:
        """合并触发一次「可见卡片派发」。只在主线程调用（Qt: 定时器所属线程）。"""
        timer = getattr(self, "_cover_timer", None)
        if timer is not None:
            timer.start()

    def showEvent(self, event) -> None:  # noqa: D401 - Qt override
        super().showEvent(event)
        self._schedule_cover_dispatch()

    def resizeEvent(self, event) -> None:  # noqa: D401 - Qt override
        super().resizeEvent(event)
        self._schedule_cover_dispatch()

    def _ensure_cover_worker(self) -> Optional[_CoverLoadWorker]:
        """懒起唯一一条常驻解码线程（不 parent 到 self：避免父子销毁抢跑 quit/wait）。"""
        if self._cover_thread is not None:
            return self._cover_worker
        try:
            thread = QThread()
            worker = _CoverLoadWorker()
            worker.moveToThread(thread)
            worker.loaded.connect(self._on_cover_loaded)  # 自动 → QueuedConnection
            thread.start()
        except Exception:  # pragma: no cover - 无法起线程时降级为「不解码」
            return None
        self._cover_thread = thread
        self._cover_worker = worker
        # §7 旧: 每次起线程都 self.destroyed.connect(_cleanup) —— teardown 后重进页面
        #        会不断叠加连接。现在 destroyed 在 __init__ 只连一次, 这里只登记线程。
        self._cover_thread_holder.append(thread)
        return worker

    def _viewport_visible_directories(self) -> list[str]:
        """视口矩形 ∩ 卡片 geometry —— 只返回真的看得见的卡片目录。"""
        scroll = getattr(self, "_scroll", None)
        if scroll is None or not self._cards:
            return []
        viewport = scroll.viewport()
        vis = QRect(QPoint(0, 0), viewport.size())
        if vis.isEmpty():
            return []
        out: list[str] = []
        for directory, card in self._cards.items():
            if card.isHidden():
                continue
            try:
                top_left = card.mapTo(viewport, QPoint(0, 0))
            except RuntimeError:  # pragma: no cover - 卡片已销毁
                continue
            if vis.intersects(QRect(top_left, card.size())):
                out.append(directory)
        return out

    def _dispatch_visible_covers(self) -> None:
        """给「可见 且 未派发过」的卡片投递封面任务（主线程只做几何计算）。"""
        todo = [
            d
            for d in self._viewport_visible_directories()
            if d not in self._cover_done and d in self._cover_eligible
        ]
        if not todo:
            return
        worker = self._ensure_cover_worker()
        if worker is None:
            return
        from app.config.project_tree_layout import COVER_CARD_DECODE_MAX

        for directory in todo:
            self._cover_req += 1
            req_id = self._cover_req
            self._cover_pending[req_id] = directory
            self._cover_done.add(directory)
            QMetaObject.invokeMethod(
                worker,
                "load",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(int, req_id),
                Q_ARG(str, directory),
                Q_ARG(int, int(COVER_CARD_DECODE_MAX)),
            )

    @pyqtSlot(object, object)
    def _on_cover_loaded(self, req_id: object, image: object) -> None:
        """Worker 回包（主线程，queued）。QPixmap 只在这里构造。"""
        try:
            key = int(req_id)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return
        directory = self._cover_pending.pop(key, None)
        if directory is None:
            return  # stale：set_entries 已重建
        card = self._cards.get(directory)
        if card is None:
            return
        try:
            card.set_cover_pixmap(make_pixmap(image))  # type: ignore[arg-type]
        except RuntimeError:  # pragma: no cover - 卡片已销毁
            pass

    def selected_directories(self) -> list[str]:
        return [d for d in self._cards if d in self._selected and not self._cards[d].isHidden()]

    def select_all(self) -> None:
        self._selected = {
            d for d, card in self._cards.items() if not card.isHidden()
        }
        for d, card in self._cards.items():
            card.set_selected(d in self._selected)
        self._emit_selection()

    def clear_selection(self) -> None:
        self._selected.clear()
        for card in self._cards.values():
            card.set_selected(False)
        self._emit_selection()

    def set_selected_directories(self, directories: list[str]) -> None:
        wanted = {str(d) for d in directories}
        self._selected = {d for d in wanted if d in self._cards}
        for d, card in self._cards.items():
            card.set_selected(d in self._selected)
        self._emit_selection()

    def set_filter_text(self, text: str) -> None:
        self._filter_text = (text or "").strip().lower()
        self._apply_filter()

    def _apply_filter(self) -> None:
        q = self._filter_text
        for d, card in self._cards.items():
            hay = self._entry_meta.get(d, "")
            visible = (not q) or (q in hay)
            card.setHidden(not visible)
            if not visible and d in self._selected:
                self._selected.discard(d)
                card.set_selected(False)
        self._emit_selection()
        # 过滤会改变可见集合 → 新露出的卡片需要补派发封面。
        self._schedule_cover_dispatch()

    def refresh_cover(self, directory: str) -> None:
        # §7 旧: card = self._cards.get(directory)
        # §7 旧: if card is not None:
        # §7 旧:     self._queue_cover(card)   # ← 主线程 pick_project_cover_path
        if directory not in self._cards:
            return
        invalidate_cover_path_cache(directory)  # 手动封面/自动封面已变
        self._cover_done.discard(directory)
        # 作废该目录的在途请求，否则旧回包会先到、盖掉新封面
        for req_id, d in list(self._cover_pending.items()):
            if d == directory:
                self._cover_pending.pop(req_id, None)
        self._schedule_cover_dispatch()

    def set_entries(
        self,
        entries: list[dict],
        *,
        stats_loader: Optional[Callable[[str], dict]] = None,
    ) -> None:
        prev = set(self._selected)
        # §7 旧: self._cancel_cover_workers()   # 逐个 wait(500) 阻塞主线程
        # 新: 常驻线程留着复用，只作废在途请求（回包按 req_id 判定为 stale 丢弃）。
        self._cover_pending.clear()
        self._cover_done.clear()
        self._cover_eligible.clear()
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cards.clear()
        self._entry_meta.clear()
        from app.config.project_tree_layout import CARD_GRID_COLUMNS

        cols = CARD_GRID_COLUMNS
        for idx, entry in enumerate(entries):
            directory = str(entry.get("directory") or entry.get("dir") or "")
            if not directory:
                continue
            name = str(entry.get("name") or Path(directory).name)
            try:
                available = Path(directory).expanduser().exists()
            except OSError:
                available = False
            is_candidate = bool(entry.get("is_candidate"))
            stats = stats_loader(directory) if stats_loader and available else {}
            card = ProjectCard(
                directory,
                name=name,
                specimen_count=stats.get("specimenCount"),
                pending_count=stats.get("pendingJpgCount"),
                available=available,
                is_candidate=is_candidate,
            )
            card.enter_clicked.connect(self.enter_requested.emit)
            card.adopt_clicked.connect(self.adopt_requested.emit)
            card.set_cover_requested.connect(self.set_cover_requested.emit)
            card.clear_cover_requested.connect(self.clear_cover_requested.emit)
            card.open_in_tree_requested.connect(self.open_in_tree_requested.emit)
            self._entry_meta[directory] = f"{name} {directory}".lower()

            def _on_press(ev, d=directory, c=card):
                mods = ev.modifiers()
                multi = bool(
                    mods & (
                        Qt.KeyboardModifier.ControlModifier
                        | Qt.KeyboardModifier.MetaModifier
                        | Qt.KeyboardModifier.ShiftModifier
                    )
                )
                if multi:
                    if d in self._selected:
                        self._selected.discard(d)
                        c.set_selected(False)
                    else:
                        self._selected.add(d)
                        c.set_selected(True)
                else:
                    # 单选：点一下选中自己（保留多选时用 Ctrl）
                    self._selected = {d}
                    for other_d, other in self._cards.items():
                        other.set_selected(other_d == d)
                self.card_selected.emit(d)
                self._emit_selection()
                QFrame.mousePressEvent(c, ev)

            card.mousePressEvent = _on_press
            self._cards[directory] = card
            if directory in prev:
                card.set_selected(True)
                self._selected.add(directory)
            self._grid.addWidget(card, idx // cols, idx % cols)
            # §7 旧: if available: self._queue_cover(card)
            #   —— 不管可不可见一律派发 + 主线程扫盘。新: 只登记 eligible，
            #      实际派发交给 _dispatch_visible_covers（仅视口内）。
            if available:
                self._cover_eligible.add(directory)
        # drop stale selection
        self._selected = {d for d in self._selected if d in self._cards}
        if not entries:
            empty = QLabel("还没有登记的项目。\n请扫描磁盘或添加工作区。")
            empty.setObjectName("EmptyState")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._grid.addWidget(empty, 0, 0, 1, cols)
        self._apply_filter()  # 内部已 _schedule_cover_dispatch()
        self._emit_selection()

    def _emit_selection(self) -> None:
        dirs = self.selected_directories()
        n = len(dirs)
        self._sel_lbl.setText(f"已选 {n}" if n else "")
        self._btn_summarize.setEnabled(n >= 1)
        self.selection_changed.emit(dirs)

    def _emit_summarize(self) -> None:
        dirs = self.selected_directories()
        if dirs:
            self.summarize_requested.emit(dirs)

    def teardown(self) -> None:
        """离页 / 退出：停定时器 + 只 quit()+wait(2000) 一次。幂等。"""
        # §7 旧: self._cancel_cover_workers()   # 逐个 worker.wait(500)
        timer = getattr(self, "_cover_timer", None)
        if timer is not None:
            try:
                timer.stop()  # 主线程持有 ⇒ 主线程 stop
            except Exception:
                pass
        # 在途请求作废；这些目录未拿到封面，下次进页面重新派发。
        for directory in self._cover_pending.values():
            self._cover_done.discard(directory)
        self._cover_pending.clear()

        thread = self._cover_thread
        if thread is None:
            return
        self._cover_thread = None
        self._cover_worker = None
        try:
            thread.quit()
            thread.wait(2000)
        except Exception:  # pragma: no cover - 防御性
            pass
        # 从 holder 摘掉, 否则「进页面 → teardown」反复几次会攒一堆已停线程,
        # 窗口销毁时 _cleanup_threads 还要挨个 quit/wait 它们。
        try:
            self._cover_thread_holder.remove(thread)
        except ValueError:  # pragma: no cover - 已被 _cleanup_threads 清空
            pass


# =============================================================================
# 【§7 归档】旧封面加载实现 —— 注释保留，勿动（新实现见上）。
# 问题：(1) _queue_cover 在主线程调 pick_project_cover_path（开 SQLite + 递归扫
# 目录），N 卡 = N 次主线程磁盘 I/O；(2) 每卡一条 CoverThumbnailWorker(QThread)，
# 无上限，N 卡 = N 个 OS 线程同时解 TIFF；(3) _cancel_cover_workers 逐个
# wait(500) 阻塞主线程；(4) 不论卡片可不可见一律派发。
# =============================================================================
#
# def _queue_cover(self, card: ProjectCard) -> None:
#     cover_path = pick_project_cover_path(card.directory())   # ← 主线程磁盘 I/O
#     if not cover_path:
#         card.set_cover_pixmap(None)
#         return
#     self._cover_req += 1
#     req_id = (card.directory(), self._cover_req)
#     from app.config.project_tree_layout import COVER_CARD_DECODE_MAX
#
#     worker = CoverThumbnailWorker(req_id, cover_path, max_size=COVER_CARD_DECODE_MAX, parent=self)
#     worker.decoded.connect(self._on_cover_decoded)
#     worker.finished.connect(worker.deleteLater)
#     self._cover_workers.append(worker)
#     worker.start()
#
# def _on_cover_decoded(self, req_id: object, image: object) -> None:
#     if not isinstance(req_id, tuple) or len(req_id) != 2:
#         return
#     directory, _ = req_id
#     card = self._cards.get(str(directory))
#     if card is None:
#         return
#     card.set_cover_pixmap(make_pixmap(image))  # type: ignore[arg-type]
#
# def _cancel_cover_workers(self) -> None:
#     for worker in self._cover_workers:
#         try:
#             if worker.isRunning():
#                 worker.wait(500)          # ← 逐个阻塞主线程
#         except Exception:
#             pass
#     self._cover_workers.clear()
#
# def teardown(self) -> None:
#     self._cancel_cover_workers()
