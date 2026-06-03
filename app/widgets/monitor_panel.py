"""monitor_panel.py — Incoming-JPG and results-TIFF thumbnail grid.

Shows the contents of ``incoming-jpg/`` and ``results/`` for the current
project.  Each file card carries:
  - A small "thumbnail" placeholder (real thumbnail loading is a future task)
  - A colour-coded attribution badge (raw/stacked/compressed/jpg/archived)
  - Buttons: Activate attribution, Deactivate, Manual-assign

Data source: ``monitor_service.scan_project()``.
Emits signals so the parent view (WorkbenchView) can coordinate state.

Layout: scrollable QGridLayout-based grid inside a QScrollArea.

Badge colour mapping (mirrors web styles.css monitoring area):
  raw       → teal  (#29b9ab) — unattributed JPG
  attributed → green (#4caf50) — JPG attributed to a specimen
  composed  → blue  (#4a90d9) — JPG is bound to a composed TIFF
  archived  → muted (#87a2a1) — JPG archived in ZIP
  tiff      → amber (#e6b04a) — TIFF result file
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from app.app_context import AppContext
    from app.services.monitor_service import ScanResult


# ── Badge constants ───────────────────────────────────────────────────────────
_BADGE: dict[str, str] = {
    "raw":        ("未归属",   "background:#29b9ab; color:#08161b;"),
    "attributed": ("已归属",   "background:#4caf50; color:#fff;"),
    "composed":   ("已合成",   "background:#4a90d9; color:#fff;"),
    "archived":   ("已归档",   "background:#87a2a1; color:#08161b;"),
    "tiff":       ("TIFF",     "background:#e6b04a; color:#08161b;"),
}
_COMMON_BADGE = (
    "border-radius:3px; font-size:10px; padding:1px 5px; font-weight:600;"
)


def _badge_label(state: str) -> QLabel:
    text, color = _BADGE.get(state, ("?", "background:#555;"))
    lbl = QLabel(text)
    lbl.setStyleSheet(color + _COMMON_BADGE)
    lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return lbl


# ── Single file card ──────────────────────────────────────────────────────────

class _FileCard(QFrame):
    """A compact card representing a single file in the monitor grid."""

    activate_requested = pyqtSignal(str)      # path
    deactivate_requested = pyqtSignal(str)    # path
    assign_requested = pyqtSignal(str)        # path

    def __init__(self, entry, parent: Optional[QWidget] = None) -> None:
        """
        Parameters
        ----------
        entry:
            A ``FileEntry`` (from monitor_service) or any duck-typed object
            with ``.name``, ``.path``, ``.kind``, ``.attributed_specimen_id``.
        """
        super().__init__(parent)
        self.setObjectName("Card")
        self._entry = entry
        self._setup_ui()

    def _setup_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        # Thumbnail placeholder
        thumb = QLabel("🖼")
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setFixedSize(80, 60)
        thumb.setStyleSheet(
            "background:#0b2025; border:1px solid rgba(145,182,181,0.13);"
            " border-radius:4px; font-size:20px;"
        )
        lay.addWidget(thumb, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Filename (truncated)
        name = self._entry.name if hasattr(self._entry, "name") else Path(getattr(self._entry, "path", "")).name
        name_lbl = QLabel(name)
        name_lbl.setObjectName("Mono")
        name_lbl.setWordWrap(False)
        name_lbl.setMaximumWidth(120)
        name_lbl.setToolTip(getattr(self._entry, "path", name))
        lay.addWidget(name_lbl)

        # Badge row
        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(0, 0, 0, 0)
        kind = getattr(self._entry, "kind", "jpg")
        uid = getattr(self._entry, "attributed_specimen_id", None)
        composed = getattr(self._entry, "composed_tiff", None)

        if kind == "tiff":
            state = "tiff"
        elif composed:
            state = "composed"
        elif uid:
            state = "attributed"
        else:
            state = "raw"

        badge_row.addWidget(_badge_label(state))
        badge_row.addStretch()
        lay.addLayout(badge_row)

        # Attribution label
        if uid:
            uid_lbl = QLabel(uid[:20] + ("…" if len(uid) > 20 else ""))
            uid_lbl.setObjectName("Muted")
            uid_lbl.setToolTip(uid)
            lay.addWidget(uid_lbl)

        # Action buttons (JPG only)
        if kind == "jpg":
            btn_row = QHBoxLayout()
            btn_row.setContentsMargins(0, 2, 0, 0)
            btn_row.setSpacing(4)

            act_btn = QPushButton("归属")
            act_btn.setFixedHeight(22)
            act_btn.setObjectName("Primary")
            act_btn.setToolTip("手动归属到当前激活标本")
            act_btn.clicked.connect(lambda: self.assign_requested.emit(getattr(self._entry, "path", "")))
            btn_row.addWidget(act_btn)

            deact_btn = QPushButton("解除")
            deact_btn.setFixedHeight(22)
            deact_btn.setToolTip("解除此 JPG 的归属")
            deact_btn.clicked.connect(lambda: self.deactivate_requested.emit(getattr(self._entry, "path", "")))
            btn_row.addWidget(deact_btn)

            lay.addLayout(btn_row)

        self.setFixedWidth(138)


# ── Monitor panel ─────────────────────────────────────────────────────────────

class MonitorPanel(QWidget):
    """Incoming-JPG + results-TIFF thumbnail grid.

    Signals
    -------
    assign_requested(path: str)
        User requested manual attribution for the given JPG path.
    unassign_requested(path: str)
        User requested to remove attribution for the given JPG path.
    refresh_requested()
        User clicked the refresh button.
    """

    assign_requested = pyqtSignal(str)
    unassign_requested = pyqtSignal(str)
    refresh_requested = pyqtSignal()

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._scan_result: Optional["ScanResult"] = None
        self._setup_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Toolbar row
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 6, 8, 6)
        title = QLabel("目录监控")
        title.setObjectName("Section")
        toolbar.addWidget(title)
        toolbar.addStretch()

        self._stat_label = QLabel("无项目")
        self._stat_label.setObjectName("Muted")
        toolbar.addWidget(self._stat_label)

        refresh_btn = QPushButton("刷新")
        refresh_btn.setFixedHeight(26)
        refresh_btn.clicked.connect(self._on_refresh)
        toolbar.addWidget(refresh_btn)
        root.addLayout(toolbar)

        # Divider
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: rgba(145,182,181,0.13);")
        root.addWidget(line)

        # Scroll area with grid
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._grid_widget = QWidget()
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setContentsMargins(8, 8, 8, 8)
        self._grid.setSpacing(8)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        scroll.setWidget(self._grid_widget)
        root.addWidget(scroll)

        # Empty-state label (shown when no files)
        self._empty_label = QLabel("暂无文件 — 等待相机 JPG 写入 incoming-jpg/")
        self._empty_label.setObjectName("Muted")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.hide()
        root.addWidget(self._empty_label)

    # ── Public API ────────────────────────────────────────────────────────────

    def load_scan(self, scan_result: "ScanResult") -> None:
        """Populate the grid from a completed scan result."""
        self._scan_result = scan_result
        self._rebuild_grid()

    def clear(self) -> None:
        """Remove all cards and show empty state."""
        self._clear_grid()
        self._stat_label.setText("无项目")
        self._empty_label.show()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _rebuild_grid(self) -> None:
        self._clear_grid()
        if not self._scan_result:
            self._empty_label.show()
            return

        all_files = (
            list(self._scan_result.jpg_files)
            + list(self._scan_result.tiff_files)
        )

        if not all_files:
            self._empty_label.show()
            jpg_c = 0
            tiff_c = 0
        else:
            self._empty_label.hide()
            jpg_c = len(self._scan_result.jpg_files)
            tiff_c = len(self._scan_result.tiff_files)

            cols = max(1, self._grid_widget.width() // 150) or 5
            for idx, entry in enumerate(all_files):
                card = _FileCard(entry, self)
                card.assign_requested.connect(self.assign_requested)
                card.deactivate_requested.connect(self.unassign_requested)
                self._grid.addWidget(card, idx // cols, idx % cols)

        self._stat_label.setText(f"JPG {jpg_c}  TIFF {tiff_c}")

    def _clear_grid(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_refresh(self) -> None:
        self.refresh_requested.emit()
