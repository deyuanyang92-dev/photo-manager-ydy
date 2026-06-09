# ============================================================================
# 已退役 (2026-06-07): 孤儿"第二设计"左主面板。App 从未实例化。其列表/平铺
# 切换优点已移植进 app/views/labels_view.py。保留可回退；确认后可删。
# ============================================================================
"""label_list_panel.py — left master pane of the Label Print Studio.

Shows the selected project's specimens as a **label list** (master), in either a
scrolling row list or a tiled thumbnail grid.  Each specimen has a checkbox
(include in the print set).  R-prefix specimens (``has_rna_tissue``) carry a
clickable ``RNA`` marker — clicking it asks the detail pane to preview that
specimen's RNAlater tube label (the "same specimen may go to RNA, show its RNA
label on click" requirement).  The sample/RNAlater bucketing itself is unchanged
(``bucket_specimens`` / ``has_rna_tissue`` in label_core); this widget only
drives selection + which label the detail pane previews.

Signals
-------
selection_changed()          — the checked-for-print set changed.
current_changed(int, str)    — preview target changed: (specimen_index, bucket)
                               where bucket is "sample" | "tissue".
"""
from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.utils.label_core import has_rna_tissue, unique_id


_CSS_ROW = """
QFrame#LabelRow {
    background: #0c2027;
    border: 1px solid rgba(145,182,181,0.10);
    border-radius: 5px;
}
QFrame#LabelRow[current="true"] {
    border: 1.5px solid #29b9ab;
    background: #0f2f38;
}
"""

_CSS_RNA_BADGE = """
QPushButton#RnaBadge {
    background: rgba(74,144,217,0.18);
    border: 1px solid #4a90d9;
    border-radius: 9px;
    color: #9cc6f0;
    padding: 1px 8px;
    font-size: 10px;
}
QPushButton#RnaBadge:hover { background: rgba(74,144,217,0.34); color: #ffffff; }
QPushButton#RnaBadge[active="true"] {
    background: #4a90d9; color: #ffffff; font-weight: bold;
}
"""

_CSS_VIEW_BTN = """
QPushButton#ViewBtn {
    background: #0c2027;
    border: 1px solid rgba(145,182,181,0.18);
    color: #87a2a1;
    padding: 3px 10px;
    font-size: 12px;
}
QPushButton#ViewBtn:checked {
    background: rgba(41,185,171,0.18);
    border-color: #29b9ab;
    color: #29b9ab;
    font-weight: bold;
}
"""

_CSS_TILE = """
QFrame#LabelTile {
    background: #0c2027;
    border: 1px solid rgba(145,182,181,0.14);
    border-radius: 6px;
}
QFrame#LabelTile[current="true"] {
    border: 1.5px solid #29b9ab;
    background: #0f2f38;
}
"""


class LabelListPanel(QWidget):
    """Master list of label rows / tiles with per-specimen include + RNA marker."""

    selection_changed = pyqtSignal()
    current_changed = pyqtSignal(int, str)  # (specimen_index, bucket)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            "background: #08161b; color: #eef3ef;"
            + _CSS_ROW + _CSS_RNA_BADGE + _CSS_VIEW_BTN + _CSS_TILE
        )
        self._specimens: list[dict] = []
        self._checked: set[int] = set()
        self._current_idx: int = -1
        self._current_bucket: str = "sample"
        self._view_mode: str = "list"
        self._thumb_provider: Optional[Callable[[int, str], Optional[QPixmap]]] = None

        # Row widgets, indexed by specimen index, for fast current-highlight.
        self._row_frames: dict[int, QFrame] = {}
        self._row_checks: dict[int, QCheckBox] = {}
        self._rna_badges: dict[int, QPushButton] = {}
        self._tile_frames: list[tuple[QFrame, int, str]] = []

        self._setup_ui()

    # ── UI ─────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 10, 12)
        root.setSpacing(8)

        title = QLabel("标签列表")
        title.setStyleSheet("color:#eef3ef; font-size:14px; font-weight:bold;")
        root.addWidget(title)

        # Toolbar: search + view toggle
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索编号 / 名称…")
        self._search.setStyleSheet(
            "QLineEdit { background:#0f2127; border:1px solid rgba(145,182,181,0.18);"
            " border-radius:4px; color:#eef3ef; padding:3px 8px; }"
        )
        self._search.textChanged.connect(self._apply_filter)
        bar.addWidget(self._search, stretch=1)

        self._view_group = QButtonGroup(self)
        self._view_group.setExclusive(True)
        self._btn_list = QPushButton("≣ 列表")
        self._btn_grid = QPushButton("▦ 平铺")
        for b, mode in ((self._btn_list, "list"), (self._btn_grid, "grid")):
            b.setObjectName("ViewBtn")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, m=mode: self.set_view_mode(m))
            self._view_group.addButton(b)
            bar.addWidget(b)
        self._btn_list.setChecked(True)
        root.addLayout(bar)

        # Stacked: list scroll / grid scroll
        self._stack = QStackedWidget()
        root.addWidget(self._stack, stretch=1)

        # List page
        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(5)
        self._list_layout.addStretch(1)
        list_scroll = QScrollArea()
        list_scroll.setWidgetResizable(True)
        list_scroll.setFrameShape(QFrame.Shape.NoFrame)
        list_scroll.setWidget(self._list_container)
        self._stack.addWidget(list_scroll)

        # Grid page
        self._grid_container = QWidget()
        self._grid_layout = QGridLayout(self._grid_container)
        self._grid_layout.setContentsMargins(2, 2, 2, 2)
        self._grid_layout.setHorizontalSpacing(8)
        self._grid_layout.setVerticalSpacing(8)
        grid_scroll = QScrollArea()
        grid_scroll.setWidgetResizable(True)
        grid_scroll.setFrameShape(QFrame.Shape.NoFrame)
        grid_scroll.setWidget(self._grid_container)
        self._stack.addWidget(grid_scroll)

        self._empty = QLabel("未加载标本。请在配置/项目中选择一个项目。")
        self._empty.setStyleSheet("color:#5f7d7a; font-size:12px;")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._empty)
        self._empty.hide()

    # ── Public API ───────────────────────────────────────────────────────────

    def set_specimens(self, specimens: list[dict]) -> None:
        self._specimens = list(specimens or [])
        self._checked = set(range(len(self._specimens)))  # default: all included
        self._current_idx = 0 if self._specimens else -1
        self._current_bucket = "sample"
        self._rebuild_rows()
        self._empty.setVisible(not self._specimens)
        self.selection_changed.emit()
        if self._current_idx >= 0:
            self.current_changed.emit(self._current_idx, self._current_bucket)

    def selected_indices(self) -> list[int]:
        """Indices checked for printing (sorted)."""
        return sorted(i for i in self._checked if 0 <= i < len(self._specimens))

    def current(self) -> tuple[int, str]:
        return self._current_idx, self._current_bucket

    def set_thumbnail_provider(
        self, fn: Optional[Callable[[int, str], Optional[QPixmap]]]
    ) -> None:
        self._thumb_provider = fn

    def select_only_uid(self, uid: str) -> bool:
        """Check exactly one specimen by its uniqueId; make it current."""
        target = -1
        for i, sp in enumerate(self._specimens):
            if unique_id(sp) == uid:
                target = i
                break
        if target < 0:
            return False
        self._checked = {target}
        self._current_idx = target
        self._current_bucket = "sample"
        self._sync_checks()
        self._highlight_current()
        self.selection_changed.emit()
        self.current_changed.emit(target, "sample")
        return True

    # Selection helpers (mirror old 全选/仅RNA/仅样品/清空)
    def select_all(self) -> None:
        self._checked = set(range(len(self._specimens)))
        self._sync_checks()
        self.selection_changed.emit()

    def select_rna_only(self) -> None:
        self._checked = {
            i for i, sp in enumerate(self._specimens) if has_rna_tissue(sp)
        }
        self._sync_checks()
        self.selection_changed.emit()

    def select_sample_only(self) -> None:
        self._checked = {
            i for i, sp in enumerate(self._specimens) if not has_rna_tissue(sp)
        }
        self._sync_checks()
        self.selection_changed.emit()

    def clear_selection(self) -> None:
        self._checked = set()
        self._sync_checks()
        self.selection_changed.emit()

    def set_view_mode(self, mode: str) -> None:
        self._view_mode = "grid" if mode == "grid" else "list"
        self._btn_list.setChecked(self._view_mode == "list")
        self._btn_grid.setChecked(self._view_mode == "grid")
        self._stack.setCurrentIndex(1 if self._view_mode == "grid" else 0)
        if self._view_mode == "grid":
            self._rebuild_grid()

    def refresh(self) -> None:
        """Rebuild whichever view is active (e.g. after template change so grid
        thumbnails re-render)."""
        if self._view_mode == "grid":
            self._rebuild_grid()

    # ── Row construction ─────────────────────────────────────────────────────

    def _rebuild_rows(self) -> None:
        # Clear
        for frame in self._row_frames.values():
            frame.setParent(None)
        self._row_frames.clear()
        self._row_checks.clear()
        self._rna_badges.clear()

        insert_at = self._list_layout.count() - 1  # before the trailing stretch
        for idx, sp in enumerate(self._specimens):
            frame = self._make_row(idx, sp)
            self._list_layout.insertWidget(insert_at, frame)
            insert_at += 1
        self._highlight_current()

    def _make_row(self, idx: int, sp: dict) -> QFrame:
        frame = QFrame()
        frame.setObjectName("LabelRow")
        frame.setProperty("current", False)
        row = QHBoxLayout(frame)
        row.setContentsMargins(8, 5, 8, 5)
        row.setSpacing(8)

        chk = QCheckBox()
        chk.setChecked(idx in self._checked)
        chk.stateChanged.connect(lambda _s, i=idx: self._on_check(i, chk.isChecked()))
        row.addWidget(chk)
        self._row_checks[idx] = chk

        uid = unique_id(sp) or f"#{idx}"
        cn = sp.get("species") or sp.get("scientificNameCn") or ""
        text = uid + (f"   {cn}" if cn else "")
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#cfe0db; font-size:12px;")
        lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        # Click the row body → preview this specimen's SAMPLE label.
        lbl.mousePressEvent = lambda _e, i=idx: self._set_current(i, "sample")  # type: ignore[assignment]
        row.addWidget(lbl, stretch=1)

        if has_rna_tissue(sp):
            badge = QPushButton("RNA")
            badge.setObjectName("RnaBadge")
            badge.setProperty("active", False)
            badge.setCursor(Qt.CursorShape.PointingHandCursor)
            badge.setToolTip("该标本取了 RNA（RNAlater 保存）。点击预览其组织管标签。")
            badge.clicked.connect(lambda _=False, i=idx: self._set_current(i, "tissue"))
            row.addWidget(badge)
            self._rna_badges[idx] = badge

        self._row_frames[idx] = frame
        return frame

    # ── Grid construction ────────────────────────────────────────────────────

    def _rebuild_grid(self) -> None:
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self._tile_frames.clear()

        cols = 3
        slot = 0
        for idx in self.selected_indices():
            sp = self._specimens[idx]
            buckets = ["sample"] + (["tissue"] if has_rna_tissue(sp) else [])
            for bucket in buckets:
                tile = self._make_tile(idx, bucket, sp)
                self._grid_layout.addWidget(tile, slot // cols, slot % cols)
                slot += 1
        if slot == 0:
            empty = QLabel("没有勾选标本。")
            empty.setStyleSheet("color:#5f7d7a; font-size:12px;")
            self._grid_layout.addWidget(empty, 0, 0)

    def _make_tile(self, idx: int, bucket: str, sp: dict) -> QFrame:
        tile = QFrame()
        tile.setObjectName("LabelTile")
        tile.setProperty("current", idx == self._current_idx and bucket == self._current_bucket)
        tile.setFixedSize(150, 120)
        tile.setCursor(Qt.CursorShape.PointingHandCursor)
        v = QVBoxLayout(tile)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)

        img = QLabel()
        img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        pm = self._thumb_provider(idx, bucket) if self._thumb_provider else None
        if pm is not None and not pm.isNull():
            img.setPixmap(pm)
        else:
            img.setText("—")
            img.setStyleSheet("color:#5f7d7a;")
        v.addWidget(img, stretch=1)

        cap = QLabel((unique_id(sp) or f"#{idx}") + ("  · RNA" if bucket == "tissue" else ""))
        cap.setStyleSheet(
            "color:%s; font-size:10px;" % ("#9cc6f0" if bucket == "tissue" else "#87a2a1")
        )
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(cap)

        tile.mousePressEvent = lambda _e, i=idx, b=bucket: self._set_current(i, b)  # type: ignore[assignment]
        self._tile_frames.append((tile, idx, bucket))
        return tile

    # ── State sync ───────────────────────────────────────────────────────────

    def _on_check(self, idx: int, checked: bool) -> None:
        if checked:
            self._checked.add(idx)
        else:
            self._checked.discard(idx)
        self.selection_changed.emit()
        if self._view_mode == "grid":
            self._rebuild_grid()

    def _sync_checks(self) -> None:
        for idx, chk in self._row_checks.items():
            blocked = chk.blockSignals(True)
            chk.setChecked(idx in self._checked)
            chk.blockSignals(blocked)
        if self._view_mode == "grid":
            self._rebuild_grid()

    def set_current(self, idx: int, bucket: str) -> None:
        """Highlight (idx, bucket) WITHOUT re-emitting current_changed — used by
        the detail pane's bucket toggle to keep the list in sync without a loop."""
        if not (0 <= idx < len(self._specimens)):
            return
        if bucket == "tissue" and not has_rna_tissue(self._specimens[idx]):
            bucket = "sample"
        self._current_idx = idx
        self._current_bucket = bucket
        self._highlight_current()

    def _set_current(self, idx: int, bucket: str) -> None:
        if not (0 <= idx < len(self._specimens)):
            return
        if bucket == "tissue" and not has_rna_tissue(self._specimens[idx]):
            bucket = "sample"
        self._current_idx = idx
        self._current_bucket = bucket
        self._highlight_current()
        self.current_changed.emit(idx, bucket)

    def _highlight_current(self) -> None:
        for idx, frame in self._row_frames.items():
            cur = idx == self._current_idx
            frame.setProperty("current", cur)
            frame.style().unpolish(frame)
            frame.style().polish(frame)
        for idx, badge in self._rna_badges.items():
            active = idx == self._current_idx and self._current_bucket == "tissue"
            badge.setProperty("active", active)
            badge.style().unpolish(badge)
            badge.style().polish(badge)
        for tile, idx, bucket in self._tile_frames:
            cur = idx == self._current_idx and bucket == self._current_bucket
            tile.setProperty("current", cur)
            tile.style().unpolish(tile)
            tile.style().polish(tile)

    # ── Filter ───────────────────────────────────────────────────────────────

    def _apply_filter(self, text: str) -> None:
        q = (text or "").strip().lower()
        for idx, frame in self._row_frames.items():
            sp = self._specimens[idx]
            hay = (unique_id(sp) + " " + str(sp.get("species") or "")).lower()
            frame.setVisible(q in hay)
