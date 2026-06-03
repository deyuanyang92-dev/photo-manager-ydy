"""results_column.py — ② 成果内容 column widget.

Mirrors the web prototype's ``results-column`` area (workspace-body--three-col
column 2), which displays:

  ┌ 成果内容 ──────────────────────────────────────┐
  │                                                 │
  │  ▸ Helicon 输出成片（合成后结果）   N 项        │
  │    [ result-gallery: TIFF 卡片 ]               │
  │                                                 │
  │  ▸ 无损压缩归档（压缩后结果）      N 项        │
  │    [ result-gallery: ZIP 卡片 ]                │
  │                                                 │
  └─────────────────────────────────────────────────┘

Public API
----------
load_uid(uid, composed_tiffs, archive_zips)
    Populate both galleries for the given specimen UID.
    ``composed_tiffs``: list of dicts with keys ``path``, ``name``.
    ``archive_zips``:   list of dicts with keys ``path``, ``name``, ``size``.
clear()
    Reset to empty state (暂无结果 placeholders).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.config import icons


# ── Individual result cards ────────────────────────────────────────────────────

class _TiffCard(QFrame):
    """A single composed-TIFF result card."""

    def __init__(self, info: dict, open_fn=None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._info = info
        self._open_fn = open_fn
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setFixedHeight(64)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Preview tile
        preview = QWidget()
        preview.setFixedWidth(56)
        preview.setStyleSheet(
            "border:none;"
            "border-top-left-radius:12px; border-bottom-left-radius:12px;"
            "background: qradialgradient(cx:0.46, cy:0.42, radius:0.62,"
            " fx:0.46, fy:0.42, stop:0 rgba(66,212,160,0.48),"
            " stop:0.4 rgba(54,201,143,0.10), stop:1 #091b20);"
        )
        pv_lay = QVBoxLayout(preview)
        pv_lay.setContentsMargins(4, 4, 4, 4)
        pv_lay.setSpacing(0)
        chip = QLabel("TIFF")
        chip.setObjectName("ChipTiff")
        chip.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        pv_lay.addWidget(chip, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        pv_lay.addStretch()
        glyph = QLabel()
        glyph.setPixmap(icons.icon("mdi6.file-image-outline", color="#1f4148").pixmap(18, 18))
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pv_lay.addWidget(glyph, alignment=Qt.AlignmentFlag.AlignHCenter)
        pv_lay.addStretch()
        lay.addWidget(preview)

        # Body
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(10, 8, 8, 8)
        body_lay.setSpacing(4)

        name = self._info.get("name") or Path(self._info.get("path", "")).name
        name_lbl = QLabel(name)
        name_lbl.setObjectName("Mono")
        name_lbl.setToolTip(self._info.get("path", name))
        body_lay.addWidget(name_lbl)

        state_chip = QLabel("已合成")
        state_chip.setObjectName("ChipComposed")
        state_chip.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        body_lay.addWidget(state_chip)
        lay.addWidget(body, stretch=1)

        # Open-in-explorer button
        if self._open_fn:
            open_btn = QPushButton("📂")
            open_btn.setObjectName("Ghost")
            open_btn.setFixedSize(26, 26)
            open_btn.setToolTip("在文件夹中显示")
            p = self._info.get("path", "")
            open_btn.clicked.connect(lambda _, _p=p: self._open_fn(_p))
            lay.addWidget(open_btn)

        from app.config.effects import apply_card_shadow
        apply_card_shadow(self, blur=14, y=2, alpha=45)


class _ArchiveCard(QFrame):
    """A single ZIP archive result card."""

    def __init__(self, info: dict, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._info = info
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setFixedHeight(64)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Preview tile (amber gradient for archive)
        preview = QWidget()
        preview.setFixedWidth(56)
        preview.setStyleSheet(
            "border:none;"
            "border-top-left-radius:12px; border-bottom-left-radius:12px;"
            "background: qradialgradient(cx:0.46, cy:0.42, radius:0.62,"
            " fx:0.46, fy:0.42, stop:0 rgba(74,144,217,0.45),"
            " stop:0.4 rgba(74,144,217,0.10), stop:1 #091b20);"
        )
        pv_lay = QVBoxLayout(preview)
        pv_lay.setContentsMargins(4, 4, 4, 4)
        pv_lay.setSpacing(0)
        chip = QLabel("ZIP")
        chip.setObjectName("ChipArchived")
        chip.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        pv_lay.addWidget(chip, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        pv_lay.addStretch()
        glyph = QLabel()
        glyph.setPixmap(icons.icon("mdi6.folder-zip-outline", color="#1a3040").pixmap(18, 18))
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pv_lay.addWidget(glyph, alignment=Qt.AlignmentFlag.AlignHCenter)
        pv_lay.addStretch()
        lay.addWidget(preview)

        # Body
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(10, 8, 8, 8)
        body_lay.setSpacing(4)

        name = self._info.get("name") or Path(self._info.get("path", "")).name
        name_lbl = QLabel(name)
        name_lbl.setObjectName("Mono")
        name_lbl.setToolTip(self._info.get("path", name))
        body_lay.addWidget(name_lbl)

        size_bytes = self._info.get("size", 0)
        size_str = _fmt_size(size_bytes) if size_bytes else "已归档"
        state_lbl = QLabel(size_str)
        state_lbl.setObjectName("MutedSmall")
        body_lay.addWidget(state_lbl)
        lay.addWidget(body, stretch=1)

        from app.config.effects import apply_card_shadow
        apply_card_shadow(self, blur=14, y=2, alpha=45)


# ── Gallery section ────────────────────────────────────────────────────────────

class _Gallery(QWidget):
    """A collapsible gallery section: header + count badge + card list."""

    def __init__(self, title: str, empty_text: str,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._title = title
        self._empty_text = empty_text
        self._cards: list[QWidget] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        # ── Section header ──
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        hdr.setSpacing(8)

        title_lbl = QLabel(self._title)
        title_lbl.setObjectName("CardTitle")
        hdr.addWidget(title_lbl)

        self._count_badge = QLabel("0 项")
        self._count_badge.setObjectName("MutedSmall")
        hdr.addWidget(self._count_badge)
        hdr.addStretch()
        lay.addLayout(hdr)

        # ── Divider ──
        divider = QFrame()
        divider.setObjectName("Divider")
        divider.setFixedHeight(1)
        lay.addWidget(divider)

        # ── Card list (scrollable) ──
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFixedHeight(200)

        self._card_container = QWidget()
        self._card_layout = QVBoxLayout(self._card_container)
        self._card_layout.setContentsMargins(0, 2, 0, 2)
        self._card_layout.setSpacing(6)
        self._card_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._scroll.setWidget(self._card_container)
        lay.addWidget(self._scroll)

        # ── Empty state ──
        self._empty_lbl = QLabel(self._empty_text)
        self._empty_lbl.setObjectName("Muted")
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setWordWrap(True)
        self._empty_lbl.hide()
        lay.addWidget(self._empty_lbl)

        self._show_empty()

    def set_cards(self, cards: list[QWidget]) -> None:
        """Replace all cards in the gallery."""
        # Clear existing
        while self._card_layout.count():
            item = self._card_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self._cards = cards
        self._count_badge.setText(f"{len(cards)} 项")
        if cards:
            self._scroll.show()
            self._empty_lbl.hide()
            for c in cards:
                self._card_layout.addWidget(c)
        else:
            self._show_empty()

    def clear(self) -> None:
        self.set_cards([])

    def _show_empty(self) -> None:
        self._scroll.hide()
        self._empty_lbl.show()
        self._count_badge.setText("0 项")


# ── ResultsColumn ──────────────────────────────────────────────────────────────

class ResultsColumn(QWidget):
    """② 成果内容 column: Helicon 输出成片 + 无损压缩归档 两组 gallery。

    This is the centre-right column of the three-column workbench body,
    placed between the capture-workbench panel and the right-panel.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        card = QFrame()
        card.setObjectName("WorkbenchSection")
        outer.addWidget(card)
        from app.config.effects import apply_card_shadow
        apply_card_shadow(card)

        root = QVBoxLayout(card)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(16)

        # Column title
        col_title = QLabel("成果内容")
        col_title.setObjectName("WorkspaceTitle")
        root.addWidget(col_title)

        divider = QFrame()
        divider.setObjectName("Divider")
        divider.setFixedHeight(1)
        root.addWidget(divider)

        # Scrollable container for both galleries
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(0, 0, 0, 0)
        inner_lay.setSpacing(20)

        # ── Gallery 1: Helicon 输出成片（合成后结果） ──
        self._tiff_gallery = _Gallery(
            title="Helicon 输出成片",
            empty_text="暂无合成结果",
        )
        inner_lay.addWidget(self._tiff_gallery)

        # ── Gallery 2: 无损压缩归档（压缩后结果） ──
        self._zip_gallery = _Gallery(
            title="无损压缩归档",
            empty_text="暂无归档结果",
        )
        inner_lay.addWidget(self._zip_gallery)
        inner_lay.addStretch()

        scroll.setWidget(inner)
        root.addWidget(scroll, stretch=1)

    # ── Public API ────────────────────────────────────────────────────────────

    def load_uid(self, uid: str,
                 composed_tiffs: list[dict],
                 archive_zips: list[dict]) -> None:
        """Populate both galleries for the given specimen UID.

        Parameters
        ----------
        uid:
            The specimen UID (currently unused by the widget but kept for
            future slot-wiring).
        composed_tiffs:
            List of dicts with at least ``path`` and optionally ``name``.
        archive_zips:
            List of dicts with at least ``path`` and optionally ``name``, ``size``.
        """
        tiff_cards = [_TiffCard(info, open_fn=self._open_in_explorer)
                      for info in composed_tiffs]
        self._tiff_gallery.set_cards(tiff_cards)

        zip_cards = [_ArchiveCard(info) for info in archive_zips]
        self._zip_gallery.set_cards(zip_cards)

    def clear(self) -> None:
        """Reset to empty (暂无结果) state."""
        self._tiff_gallery.clear()
        self._zip_gallery.clear()

    def _open_in_explorer(self, path: str) -> None:
        """Open the folder containing *path* in the system file explorer.

        Oracle: app.js openInExplorer() / electron shell.showItemInFolder().
        """
        import subprocess
        import sys
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                # WSL: open Windows Explorer via explorer.exe with wslpath
                win_path = path
                try:
                    from app.utils.path_utils import wsl_to_windows
                    win_path = wsl_to_windows(path) or path
                except Exception:
                    pass
                subprocess.Popen(["explorer.exe", "/select,", win_path])
        except Exception:
            pass


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_size(n: int) -> str:
    """Format byte count as human-readable string."""
    if n >= 1024 ** 3:
        return f"{n / 1024**3:.1f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024**2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"
