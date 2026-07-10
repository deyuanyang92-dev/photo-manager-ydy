"""project_card.py — 项目树卡片视图单卡 (spec §4.2)."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
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
from app.utils.image_thumbnail import make_pixmap
from app.workers.thumbnail_worker import CoverThumbnailWorker


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
        self._cover_workers: list[CoverThumbnailWorker] = []
        self._cover_req = 0
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

    def refresh_cover(self, directory: str) -> None:
        card = self._cards.get(directory)
        if card is not None:
            self._queue_cover(card)

    def set_entries(
        self,
        entries: list[dict],
        *,
        stats_loader: Optional[Callable[[str], dict]] = None,
    ) -> None:
        prev = set(self._selected)
        self._cancel_cover_workers()
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
            if available:
                self._queue_cover(card)
        # drop stale selection
        self._selected = {d for d in self._selected if d in self._cards}
        if not entries:
            empty = QLabel("还没有登记的项目。\n请扫描磁盘或添加工作区。")
            empty.setObjectName("EmptyState")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._grid.addWidget(empty, 0, 0, 1, cols)
        self._apply_filter()
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

    def _queue_cover(self, card: ProjectCard) -> None:
        cover_path = pick_project_cover_path(card.directory())
        if not cover_path:
            card.set_cover_pixmap(None)
            return
        self._cover_req += 1
        req_id = (card.directory(), self._cover_req)
        from app.config.project_tree_layout import COVER_CARD_DECODE_MAX

        worker = CoverThumbnailWorker(req_id, cover_path, max_size=COVER_CARD_DECODE_MAX, parent=self)
        worker.decoded.connect(self._on_cover_decoded)
        worker.finished.connect(worker.deleteLater)
        self._cover_workers.append(worker)
        worker.start()

    def _on_cover_decoded(self, req_id: object, image: object) -> None:
        if not isinstance(req_id, tuple) or len(req_id) != 2:
            return
        directory, _ = req_id
        card = self._cards.get(str(directory))
        if card is None:
            return
        card.set_cover_pixmap(make_pixmap(image))  # type: ignore[arg-type]

    def _cancel_cover_workers(self) -> None:
        for worker in self._cover_workers:
            try:
                if worker.isRunning():
                    worker.wait(500)
            except Exception:
                pass
        self._cover_workers.clear()

    def teardown(self) -> None:
        self._cancel_cover_workers()
