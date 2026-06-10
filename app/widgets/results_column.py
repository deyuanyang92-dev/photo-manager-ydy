"""results_column.py — ② 成果内容 column widget.

Mirrors the web prototype's ``results-column`` area, redesigned per the user's
request into **paired rows**: each result sequence is one row showing its
composed TIFF (left) and its lossless ZIP archive (right) side-by-side, so the
two products of the same 编号 read as associated.  Each card carries a real,
zoomable preview display-box; the whole area collapses via a single toggle.

  ┌ 成果内容          N 项        缩放 [────]   ▾ ┐
  │  成果 1                                       │
  │    [ TIFF 展示框 ]   |   [ ZIP 展示框 ]       │
  │  成果 2                                       │
  │    [ TIFF 展示框 ]   |   [ 尚未压缩 ]         │
  └───────────────────────────────────────────────┘

Public API
----------
load_uid(uid, composed_tiffs, archive_zips)
    Populate the paired rows for the given specimen UID.
    ``composed_tiffs``: list of dicts with keys ``path``, ``name``, optional ``seq``.
    ``archive_zips``:   list of dicts with keys ``path``, ``name``, ``size``, optional ``seq``.
    Items are paired by ``seq`` (falling back to matching filename stem).
clear()
    Reset to empty state (暂无成果 placeholder).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from app.config import icons


# ── TIFF Lightbox Dialog ───────────────────────────────────────────────────────

class _TiffLightboxDialog(QDialog):
    """Fullscreen-ish lightbox for browsing composed TIFF files."""

    def __init__(self, paths: list, initial_index: int = 0,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("图片预览")
        self.resize(900, 700)
        self._paths = paths
        self._index = initial_index

        layout = QVBoxLayout(self)

        self._info_label = QLabel()
        layout.addWidget(self._info_label)

        self._image_label = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self._image_label.setMinimumSize(800, 550)
        self._image_label.setScaledContents(False)
        layout.addWidget(self._image_label)

        nav_row = QHBoxLayout()

        self._prev_btn = QPushButton("◀ 上一张")
        self._prev_btn.clicked.connect(self._go_prev)
        nav_row.addWidget(self._prev_btn)

        self._next_btn = QPushButton("下一张 ▶")
        self._next_btn.clicked.connect(self._go_next)
        nav_row.addWidget(self._next_btn)

        open_btn = QPushButton("在文件管理器中显示")
        open_btn.clicked.connect(self._open_explorer)
        nav_row.addWidget(open_btn)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        nav_row.addWidget(close_btn)

        layout.addLayout(nav_row)

        self._load_current()

    def _load_current(self) -> None:
        path = self._paths[self._index]
        self._info_label.setText(
            f"{path.name}  ({self._index + 1} / {len(self._paths)})"
        )

        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            try:
                from PIL import Image
                import tempfile
                img = Image.open(str(path))
                img.thumbnail((1600, 1200))
                tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                img.save(tmp.name)
                pixmap = QPixmap(tmp.name)
                os.unlink(tmp.name)
            except Exception:
                pass

        if not pixmap.isNull():
            scaled = pixmap.scaled(
                self._image_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._image_label.setPixmap(scaled)
        else:
            self._image_label.setText(f"无法预览: {path.name}")

        self._prev_btn.setEnabled(self._index > 0)
        self._next_btn.setEnabled(self._index < len(self._paths) - 1)

    def _go_prev(self) -> None:
        self._index -= 1
        self._load_current()

    def _go_next(self) -> None:
        self._index += 1
        self._load_current()

    def _open_explorer(self) -> None:
        import subprocess
        import sys
        path = self._paths[self._index]
        if sys.platform == "win32":
            subprocess.run(["explorer", "/select,", str(path)])
        else:
            subprocess.run(["xdg-open", str(path.parent)])

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Left and self._index > 0:
            self._go_prev()
        elif e.key() == Qt.Key.Key_Right and self._index < len(self._paths) - 1:
            self._go_next()
        elif e.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)


# ── Thumbnail decode (cached at ResultsColumn level) ───────────────────────────

_MIN_THUMB = 72
_MAX_THUMB = 280
_DEFAULT_THUMB = 112
_BASE_THUMB = 280  # base decode size; zoom scales DOWN from this cached pixmap


def _decode_thumb(path: str, max_size: int = _BASE_THUMB) -> Optional[QPixmap]:
    """Decode *path* to a QPixmap downscaled to ``max_size`` (KeepAspectRatio).

    Returns None for empty / missing / undecodable paths — callers fall back to
    an icon placeholder.  TIFF that Qt can't read natively goes through a
    PIL → temp-PNG path (same as the lightbox).  Never raises.
    """
    if not path:
        return None
    try:
        if not os.path.exists(path):
            return None
    except Exception:
        return None
    pm = QPixmap(path)
    if pm.isNull():
        try:
            from PIL import Image
            import tempfile
            img = Image.open(path)
            img.thumbnail((max_size, max_size))
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            img.save(tmp.name)
            pm = QPixmap(tmp.name)
            os.unlink(tmp.name)
        except Exception:
            return None
    if pm.isNull():
        return None
    return pm.scaled(
        max_size, max_size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


# ── Individual result cards ────────────────────────────────────────────────────

class _ResultCardBase(QFrame):
    """Shared base: a square preview display-box (zoomable) + a body column."""

    _FALLBACK_ICON = "mdi6.file-image-outline"
    _TILE_BG = (
        "background: qradialgradient(cx:0.46, cy:0.42, radius:0.62,"
        " fx:0.46, fy:0.42, stop:0 rgba(66,212,160,0.40),"
        " stop:0.4 rgba(54,201,143,0.10), stop:1 #091b20);"
    )

    def __init__(self, info: dict, thumb_provider=None,
                 thumb_size: int = _DEFAULT_THUMB,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._info = info
        self._thumb_size = thumb_size
        self._thumb_provider = thumb_provider
        self._base_pm: Optional[QPixmap] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._preview = QLabel()
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._preview)

        body = self._build_body()
        lay.addWidget(body, stretch=1)

        from app.config.effects import apply_card_shadow
        apply_card_shadow(self, blur=14, y=2, alpha=45)

        path = self._info.get("path", "")
        if self._thumb_provider and path:
            self._base_pm = self._thumb_provider(path)
        self._apply_thumb()

    def _build_body(self) -> QWidget:  # override
        return QWidget()

    def _apply_thumb(self) -> None:
        s = self._thumb_size
        self._preview.setFixedSize(s, s)
        if self._base_pm is not None and not self._base_pm.isNull():
            self._preview.setStyleSheet(
                "border:none; border-top-left-radius:12px;"
                " border-bottom-left-radius:12px; background:#06141a;"
            )
            self._preview.setPixmap(self._base_pm.scaled(
                s, s,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        else:
            self._preview.setStyleSheet(
                "border:none; border-top-left-radius:12px;"
                " border-bottom-left-radius:12px;" + self._TILE_BG
            )
            g = min(28, max(14, s // 2))
            self._preview.setPixmap(
                icons.icon(self._FALLBACK_ICON, color="#1f4148").pixmap(g, g)
            )
        self.setMinimumHeight(s)

    def set_thumb_size(self, size: int) -> None:
        self._thumb_size = size
        self._apply_thumb()


class _TiffCard(_ResultCardBase):
    """A single composed-TIFF result card with a real, zoomable preview box."""

    _FALLBACK_ICON = "mdi6.file-image-outline"

    def __init__(self, info: dict, open_fn=None, lightbox_fn=None,
                 thumb_provider=None, thumb_size: int = _DEFAULT_THUMB,
                 parent: Optional[QWidget] = None) -> None:
        self._open_fn = open_fn
        self._lightbox_fn = lightbox_fn
        super().__init__(info, thumb_provider=thumb_provider,
                         thumb_size=thumb_size, parent=parent)

    def _build_body(self) -> QWidget:
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(10, 8, 8, 8)
        body_lay.setSpacing(4)

        name = self._info.get("name") or Path(self._info.get("path", "")).name
        name_lbl = QLabel(name)
        name_lbl.setObjectName("Mono")
        name_lbl.setWordWrap(True)
        name_lbl.setToolTip(self._info.get("path", name))
        body_lay.addWidget(name_lbl)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        state_chip = QLabel("已合成")
        state_chip.setObjectName("ChipComposed")
        state_chip.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        row.addWidget(state_chip)
        row.addStretch()
        menu_btn = QPushButton()
        menu_btn.setObjectName("Ghost")
        menu_btn.setFixedSize(26, 26)
        menu_btn.setToolTip("成果操作")
        icons.set_button_icon(menu_btn, "mdi6.dots-vertical", size=14)
        menu_btn.clicked.connect(
            lambda: self._show_menu(menu_btn.mapToGlobal(menu_btn.rect().bottomLeft()))
        )
        row.addWidget(menu_btn)
        body_lay.addLayout(row)
        body_lay.addStretch()
        return body

    def mouseDoubleClickEvent(self, event) -> None:
        if self._lightbox_fn:
            path = self._info.get("path", "")
            if path:
                self._lightbox_fn(Path(path))
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        self._show_menu(event.globalPos())

    def _show_menu(self, global_pos) -> None:
        path = self._info.get("path", "")
        menu = QMenu(self)
        preview_action = menu.addAction("打开预览")
        preview_action.setEnabled(bool(self._lightbox_fn and path))
        preview_action.triggered.connect(lambda: self._lightbox_fn(Path(path)))
        open_action = menu.addAction("在文件夹中显示")
        open_action.setEnabled(bool(self._open_fn and path))
        open_action.triggered.connect(lambda: self._open_fn(path))
        menu.exec(global_pos)


class _ArchiveCard(_ResultCardBase):
    """A single ZIP-archive result card (no decodable image → glyph tile)."""

    _FALLBACK_ICON = "mdi6.folder-zip-outline"
    _TILE_BG = (
        "background: qradialgradient(cx:0.46, cy:0.42, radius:0.62,"
        " fx:0.46, fy:0.42, stop:0 rgba(74,144,217,0.42),"
        " stop:0.4 rgba(74,144,217,0.10), stop:1 #091b20);"
    )

    def __init__(self, info: dict, open_fn=None, restore_fn=None,
                 thumb_size: int = _DEFAULT_THUMB,
                 parent: Optional[QWidget] = None) -> None:
        self._open_fn = open_fn
        self._restore_fn = restore_fn
        super().__init__(info, thumb_provider=None,
                         thumb_size=thumb_size, parent=parent)

    def _build_body(self) -> QWidget:
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(10, 8, 8, 8)
        body_lay.setSpacing(4)

        name = self._info.get("name") or Path(self._info.get("path", "")).name
        name_lbl = QLabel(name)
        name_lbl.setObjectName("Mono")
        name_lbl.setWordWrap(True)
        name_lbl.setToolTip(self._info.get("path", name))
        body_lay.addWidget(name_lbl)

        size_bytes = self._info.get("size", 0)
        size_str = _fmt_size(size_bytes) if size_bytes else "已归档"
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        state_lbl = QLabel(size_str)
        state_lbl.setObjectName("MutedSmall")
        row.addWidget(state_lbl)
        row.addStretch()
        menu_btn = QPushButton()
        menu_btn.setObjectName("Ghost")
        menu_btn.setFixedSize(26, 26)
        menu_btn.setToolTip("归档操作")
        icons.set_button_icon(menu_btn, "mdi6.dots-vertical", size=14)
        menu_btn.clicked.connect(
            lambda: self._show_menu(menu_btn.mapToGlobal(menu_btn.rect().bottomLeft()))
        )
        row.addWidget(menu_btn)
        body_lay.addLayout(row)
        body_lay.addStretch()
        return body

    def contextMenuEvent(self, event) -> None:
        self._show_menu(event.globalPos())

    def _show_menu(self, global_pos) -> None:
        path = self._info.get("path", "")
        menu = QMenu(self)
        restore_action = menu.addAction("还原原片")
        restore_action.setEnabled(bool(self._restore_fn and path))
        restore_action.triggered.connect(lambda: self._restore_fn(path))
        open_action = menu.addAction("在文件夹中显示")
        open_action.setEnabled(bool(self._open_fn and path))
        open_action.triggered.connect(lambda: self._open_fn(path))
        menu.exec(global_pos)


def _placeholder(text: str) -> QWidget:
    """Muted box shown when a row is missing its TIFF or its ZIP side."""
    f = QFrame()
    f.setObjectName("Card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(10, 10, 10, 10)
    lbl = QLabel(text)
    lbl.setObjectName("Muted")
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setWordWrap(True)
    lay.addWidget(lbl)
    return f


class _ResultRow(QFrame):
    """One result sequence: header label + [TIFF | ZIP] paired side-by-side."""

    def __init__(self, seq_label: str, tiff_card: Optional[QWidget],
                 zip_card: Optional[QWidget],
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("ResultRow")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 6)
        v.setSpacing(6)

        hdr = QLabel(seq_label)
        hdr.setObjectName("MutedSmall")
        v.addWidget(hdr)

        pair = QHBoxLayout()
        pair.setContentsMargins(0, 0, 0, 0)
        pair.setSpacing(12)
        pair.addWidget(tiff_card if tiff_card is not None else _placeholder("无对应成片"), 1)
        pair.addWidget(zip_card if zip_card is not None else _placeholder("尚未压缩"), 1)
        v.addLayout(pair)


def _pair_results(composed_tiffs: list, archive_zips: list) -> list:
    """Pair TIFF/ZIP infos by ``seq`` (fallback: filename stem), preserving the
    TIFF input order.  Returns a list of ``(seq_label, tiff_or_None, zip_or_None)``.
    """
    def keyfor(info: dict, fallback):
        s = info.get("seq")
        if s is not None:
            return ("seq", s)
        stem = Path(info.get("path") or info.get("name") or "").stem
        if stem:
            return ("stem", stem)
        return fallback

    key_order: list = []
    tiff_by: dict = {}
    zip_by: dict = {}

    for i, t in enumerate(composed_tiffs):
        k = keyfor(t, ("t", i))
        if k not in tiff_by:
            tiff_by[k] = t
            if k not in key_order:
                key_order.append(k)
    for i, z in enumerate(archive_zips):
        k = keyfor(z, ("z", i))
        if k not in zip_by:
            zip_by[k] = z
        if k not in key_order:
            key_order.append(k)

    out = []
    for k in key_order:
        label = f"成果 {k[1]}" if k[0] == "seq" else "成果"
        out.append((label, tiff_by.get(k), zip_by.get(k)))
    return out


# ── ResultsColumn ──────────────────────────────────────────────────────────────

class ResultsColumn(QWidget):
    """② 成果内容 column: collapsible, zoomable, paired TIFF↔ZIP rows."""

    restore_requested = pyqtSignal(str)  # ZIP 绝对路径 → 还原原片 JPG

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._thumb_size = _DEFAULT_THUMB
        self._thumb_cache: dict = {}
        self._cards: list = []
        self._collapsed = False
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
        root.setSpacing(12)

        # ── Header row: collapse + title + count + zoom ──
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        hdr.setSpacing(10)

        self._collapse_btn = QPushButton("▾")
        self._collapse_btn.setObjectName("Ghost")
        self._collapse_btn.setFixedSize(24, 24)
        self._collapse_btn.setCheckable(True)
        self._collapse_btn.setToolTip("收起 / 展开成果区")
        self._collapse_btn.clicked.connect(
            lambda: self._set_collapsed(not self._collapsed)
        )
        hdr.addWidget(self._collapse_btn)

        title = QLabel("成果")
        title.setObjectName("WorkspaceTitle")
        hdr.addWidget(title)

        self._count = QLabel("0 项")
        self._count.setObjectName("MutedSmall")
        hdr.addWidget(self._count)
        hdr.addStretch()

        zoom_lbl = QLabel("缩放")
        zoom_lbl.setObjectName("MutedSmall")
        hdr.addWidget(zoom_lbl)
        self._zoom = QSlider(Qt.Orientation.Horizontal)
        self._zoom.setMinimum(_MIN_THUMB)
        self._zoom.setMaximum(_MAX_THUMB)
        self._zoom.setValue(self._thumb_size)
        self._zoom.setFixedWidth(120)
        self._zoom.setToolTip("调整结果展示框大小")
        self._zoom.valueChanged.connect(self._set_zoom)
        hdr.addWidget(self._zoom)
        root.addLayout(hdr)

        divider = QFrame()
        divider.setObjectName("Divider")
        divider.setFixedHeight(1)
        root.addWidget(divider)

        # ── Body: scrollable paired rows ──
        self._body = QScrollArea()
        self._body.setWidgetResizable(True)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._rows_container = QWidget()
        self._rows_lay = QVBoxLayout(self._rows_container)
        self._rows_lay.setContentsMargins(0, 2, 0, 2)
        self._rows_lay.setSpacing(10)
        self._rows_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._body.setWidget(self._rows_container)
        root.addWidget(self._body, stretch=1)

        self._show_empty()

    # ── Public API ────────────────────────────────────────────────────────────

    def load_uid(self, uid: str,
                 composed_tiffs: list,
                 archive_zips: list) -> None:
        """Populate the paired rows for the given specimen UID."""
        self._clear_rows()

        all_tiff_paths = [Path(i["path"]) for i in composed_tiffs if i.get("path")]
        rows = _pair_results(composed_tiffs, archive_zips)
        for seq_label, tinfo, zinfo in rows:
            tcard = None
            if tinfo is not None:
                tcard = _TiffCard(
                    tinfo,
                    open_fn=self._open_in_explorer,
                    lightbox_fn=lambda p, _paths=all_tiff_paths: self._open_tiff_lightbox(p, _paths),
                    thumb_provider=self._thumb_provider,
                    thumb_size=self._thumb_size,
                )
                self._cards.append(tcard)
            zcard = None
            if zinfo is not None:
                zcard = _ArchiveCard(
                    zinfo, open_fn=self._open_in_explorer,
                    restore_fn=lambda p: self.restore_requested.emit(p),
                    thumb_size=self._thumb_size,
                )
                self._cards.append(zcard)
            self._rows_lay.addWidget(_ResultRow(seq_label, tcard, zcard))

        n = len(rows)
        self._count.setText(f"{n} 项")
        if not n:
            self._show_empty()

    def clear(self) -> None:
        """Reset to empty (暂无成果) state."""
        self._clear_rows()
        self._show_empty()

    # ── Internals ───────────────────────────────────────────────────────────────

    def _thumb_provider(self, path: str) -> Optional[QPixmap]:
        """Return a cached base pixmap for *path* (None if undecodable)."""
        if path in self._thumb_cache:
            return self._thumb_cache[path]
        pm = _decode_thumb(path, _BASE_THUMB)
        self._thumb_cache[path] = pm
        return pm

    def _clear_rows(self) -> None:
        while self._rows_lay.count():
            it = self._rows_lay.takeAt(0)
            if it and it.widget():
                it.widget().deleteLater()
        self._cards = []

    def _show_empty(self) -> None:
        self._count.setText("0 项")
        empty = QLabel("暂无成果")
        empty.setObjectName("Muted")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setWordWrap(True)
        self._rows_lay.addWidget(empty)

    def _set_zoom(self, size: int) -> None:
        """Resize every result display-box (the 缩放 control)."""
        self._thumb_size = size
        if self._zoom.value() != size:
            self._zoom.blockSignals(True)
            self._zoom.setValue(size)
            self._zoom.blockSignals(False)
        for c in self._cards:
            c.set_thumb_size(size)

    def _set_collapsed(self, collapsed: bool) -> None:
        """Collapse / expand the whole results area (single toggle)."""
        self._collapsed = collapsed
        self._body.setVisible(not collapsed)
        self._collapse_btn.setText("▸" if collapsed else "▾")
        self._collapse_btn.setChecked(collapsed)

    def _open_tiff_lightbox(self, clicked_path: Path, all_paths: list) -> None:
        """Open the TIFF lightbox dialog starting at *clicked_path*."""
        try:
            idx = all_paths.index(clicked_path)
        except ValueError:
            idx = 0
        dlg = _TiffLightboxDialog(all_paths, initial_index=idx, parent=self)
        dlg.exec()

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
