"""results_column.py — ② 成果内容 column widget.

Mirrors the web prototype's ``results-column`` area as a **Windows Explorer
file list**: each result file is one compact row — icon LEFT, filename + meta
RIGHT.  A composed TIFF shows its thumbnail; a lossless ZIP shows the standard
zip-folder icon (like Windows).  Files of the same 编号 group under one
"成果 N" header.  The whole area collapses via a single toggle.

  ┌ 成果内容          N 项        缩放 [────]   ▾ ┐
  │  成果 1                                       │
  │    [🖼] a.tif              已合成          ⋮  │
  │    [📦] a.zip              23.4 MB         ⋮  │
  │  成果 2                                       │
  │    [🖼] b.tif              已合成          ⋮  │
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

from PyQt6.QtCore import QEvent, QPoint, Qt, pyqtSignal
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

class _PanImageLabel(QLabel):
    """Image label that drags its parent scroll area for pan navigation."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scroll_area: Optional[QScrollArea] = None
        self._last_pos: Optional[QPoint] = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def set_scroll_area(self, scroll_area: QScrollArea) -> None:
        self._scroll_area = scroll_area

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._last_pos = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._scroll_area is not None and self._last_pos is not None:
            delta = event.pos() - self._last_pos
            hbar = self._scroll_area.horizontalScrollBar()
            vbar = self._scroll_area.verticalScrollBar()
            hbar.setValue(hbar.value() - delta.x())
            vbar.setValue(vbar.value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._last_pos = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _TiffLightboxDialog(QDialog):
    """Fullscreen-ish lightbox for browsing composed TIFF files."""

    def __init__(self, paths: list, initial_index: int = 0,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("图片预览")
        self.resize(900, 700)
        self._paths = paths
        self._index = initial_index
        self._base_pixmap = QPixmap()
        self._fit_to_window = True

        layout = QVBoxLayout(self)

        self._info_label = QLabel()
        layout.addWidget(self._info_label)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label = _PanImageLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setMinimumSize(800, 550)
        self._image_label.setScaledContents(False)
        self._image_label.set_scroll_area(self._scroll)
        self._scroll.setWidget(self._image_label)
        self._scroll.viewport().installEventFilter(self)
        self._image_label.installEventFilter(self)
        layout.addWidget(self._scroll, stretch=1)

        zoom_row = QHBoxLayout()
        zoom_lbl = QLabel("缩放")
        zoom_lbl.setObjectName("MutedSmall")
        zoom_row.addWidget(zoom_lbl)
        self._zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self._zoom_slider.setMinimum(25)
        self._zoom_slider.setMaximum(400)
        self._zoom_slider.setValue(100)
        self._zoom_slider.setToolTip("缩放 TIFF 预览")
        self._zoom_slider.valueChanged.connect(self._on_zoom_changed)
        zoom_row.addWidget(self._zoom_slider, stretch=1)
        self._zoom_value = QLabel("适合窗口")
        self._zoom_value.setObjectName("MutedSmall")
        zoom_row.addWidget(self._zoom_value)
        fit_btn = QPushButton("适合窗口")
        fit_btn.clicked.connect(self._fit_current)
        zoom_row.addWidget(fit_btn)
        layout.addLayout(zoom_row)

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

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.Wheel and obj in (
            self._scroll.viewport(),
            self._image_label,
        ):
            return self._handle_wheel(obj, event)
        return super().eventFilter(obj, event)

    def _handle_wheel(self, obj, event) -> bool:
        delta = event.angleDelta().y()
        if delta == 0:
            return False

        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.AltModifier:
            if delta < 0 and self._index < len(self._paths) - 1:
                self._go_next()
            elif delta > 0 and self._index > 0:
                self._go_prev()
            event.accept()
            return True

        # Mouse wheel zooms the preview; Ctrl+wheel follows the Windows/browser
        # convention and lands on the same path.
        anchor = None
        if hasattr(event, "position"):
            anchor = event.position().toPoint()
            if obj is self._image_label:
                anchor = self._image_label.mapTo(self._scroll.viewport(), anchor)
        self._zoom_by_wheel_delta(delta, anchor)
        event.accept()
        return True

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
                img.thumbnail((2400, 1800))
                tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                img.save(tmp.name)
                pixmap = QPixmap(tmp.name)
                os.unlink(tmp.name)
            except Exception:
                pass

        if not pixmap.isNull():
            self._base_pixmap = pixmap
            self._fit_to_window = True
            self._render_current()
        else:
            self._base_pixmap = QPixmap()
            self._image_label.setText(f"无法预览: {path.name}")

        self._prev_btn.setEnabled(self._index > 0)
        self._next_btn.setEnabled(self._index < len(self._paths) - 1)

    def _render_current(self) -> None:
        if self._base_pixmap.isNull():
            return
        if self._fit_to_window:
            viewport = self._scroll.viewport().size()
            scaled = self._base_pixmap.scaled(
                max(120, viewport.width() - 24),
                max(120, viewport.height() - 24),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            ratio = int((scaled.width() / max(1, self._base_pixmap.width())) * 100)
            self._zoom_slider.blockSignals(True)
            self._zoom_slider.setValue(max(25, min(400, ratio)))
            self._zoom_slider.blockSignals(False)
            self._zoom_value.setText("适合窗口")
        else:
            pct = self._zoom_slider.value()
            scaled = self._base_pixmap.scaled(
                max(1, int(self._base_pixmap.width() * pct / 100)),
                max(1, int(self._base_pixmap.height() * pct / 100)),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._zoom_value.setText(f"{pct}%")
        self._image_label.setText("")
        self._image_label.setPixmap(scaled)
        self._image_label.resize(scaled.size())

    def _on_zoom_changed(self, _value: int) -> None:
        self._fit_to_window = False
        self._render_current()

    def _fit_current(self) -> None:
        self._fit_to_window = True
        self._render_current()

    def _actual_size(self) -> None:
        self._set_zoom_percent(100)

    def _zoom_by_wheel_delta(self, delta: int, anchor: Optional[QPoint] = None) -> None:
        steps = max(1, abs(delta) // 120)
        direction = 1 if delta > 0 else -1
        current = self._zoom_slider.value()
        self._set_zoom_percent(current + direction * steps * 10, anchor)

    def _set_zoom_percent(self, pct: int, anchor: Optional[QPoint] = None) -> None:
        if self._base_pixmap.isNull():
            return
        pct = max(self._zoom_slider.minimum(), min(self._zoom_slider.maximum(), int(pct)))
        viewport = self._scroll.viewport()
        if anchor is None:
            anchor = viewport.rect().center()

        hbar = self._scroll.horizontalScrollBar()
        vbar = self._scroll.verticalScrollBar()
        old_w = max(1, self._image_label.width())
        old_h = max(1, self._image_label.height())
        rel_x = (hbar.value() + anchor.x()) / old_w
        rel_y = (vbar.value() + anchor.y()) / old_h

        self._fit_to_window = False
        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(pct)
        self._zoom_slider.blockSignals(False)
        self._render_current()

        new_w = max(1, self._image_label.width())
        new_h = max(1, self._image_label.height())
        hbar.setValue(int(new_w * rel_x) - anchor.x())
        vbar.setValue(int(new_h * rel_y) - anchor.y())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fit_to_window:
            self._render_current()

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
        key = e.key()
        mods = e.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)

        if ctrl and key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self._set_zoom_percent(self._zoom_slider.value() + 10)
        elif ctrl and key == Qt.Key.Key_Minus:
            self._set_zoom_percent(self._zoom_slider.value() - 10)
        elif ctrl and key in (Qt.Key.Key_0, Qt.Key.Key_F):
            self._fit_current()
        elif ctrl and key == Qt.Key.Key_1:
            self._actual_size()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_PageUp, Qt.Key.Key_Backspace) and self._index > 0:
            self._go_prev()
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_PageDown, Qt.Key.Key_Space) and self._index < len(self._paths) - 1:
            self._go_next()
        elif key == Qt.Key.Key_Home and self._index != 0:
            self._index = 0
            self._load_current()
        elif key == Qt.Key.Key_End and self._index != len(self._paths) - 1:
            self._index = len(self._paths) - 1
            self._load_current()
        elif key == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)


# ── Thumbnail decode (cached at ResultsColumn level) ───────────────────────────

_MIN_THUMB = 32
_MAX_THUMB = 96
_DEFAULT_THUMB = 48
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
    """Shared base: one compact file-list row — icon LEFT, name + meta RIGHT
    (Windows Explorer list style).  Subclasses supply the icon source, the meta
    text and the context-menu actions."""

    _FALLBACK_ICON = "mdi6.file-image-outline"
    _ICON_TINT = "#3a6b75"

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
        lay.setContentsMargins(10, 6, 8, 6)
        lay.setSpacing(10)

        self._icon = QLabel()
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon.setFixedSize(self._thumb_size, self._thumb_size)
        lay.addWidget(self._icon)

        body = self._build_body()
        lay.addWidget(body, stretch=1)

        from app.config.effects import apply_card_shadow
        apply_card_shadow(self, blur=10, y=1, alpha=30)

        path = self._info.get("path", "")
        if self._thumb_provider and path:
            self._base_pm = self._thumb_provider(path)
        self._apply_icon()

    def _build_body(self) -> QWidget:  # override
        return QWidget()

    def _icon_pixmap(self) -> Optional[QPixmap]:
        """Override to force a glyph (e.g. ZIP).  Default = decoded thumbnail."""
        return self._base_pm

    def _apply_icon(self) -> None:
        s = self._thumb_size
        self._icon.setFixedSize(s, s)
        self._icon.setStyleSheet("border:none; border-radius:6px; background:transparent;")
        pm = self._icon_pixmap()
        if pm is not None and not pm.isNull():
            self._icon.setPixmap(pm.scaled(
                s, s,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        else:
            g = min(26, max(12, s - 6))
            self._icon.setPixmap(
                icons.icon(self._FALLBACK_ICON, color=self._ICON_TINT).pixmap(g, g)
            )

    def _make_menu_btn(self, tip: str, handler) -> QPushButton:
        b = QPushButton()
        b.setObjectName("Ghost")
        b.setFixedSize(24, 24)
        b.setToolTip(tip)
        icons.set_button_icon(b, "mdi6.dots-vertical", size=14)
        b.clicked.connect(lambda: handler(b.mapToGlobal(b.rect().bottomLeft())))
        return b

    def set_thumb_size(self, size: int) -> None:
        self._thumb_size = size
        self._apply_icon()


class _TiffCard(_ResultCardBase):
    """One composed-TIFF file row: thumbnail icon + filename + 已合成."""

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
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(2)

        name = self._info.get("name") or Path(self._info.get("path", "")).name
        name_lbl = QLabel(name)
        name_lbl.setObjectName("Mono")
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
        row.addWidget(self._make_menu_btn("成果操作", self._show_menu))
        body_lay.addLayout(row)
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
    """One ZIP-archive file row: Windows-style zip-folder icon + filename + size."""

    _FALLBACK_ICON = "mdi6.folder-zip-outline"
    _ICON_TINT = "#c9981f"  # Windows zip-folder amber

    def __init__(self, info: dict, open_fn=None, restore_fn=None,
                 thumb_size: int = _DEFAULT_THUMB,
                 parent: Optional[QWidget] = None) -> None:
        self._open_fn = open_fn
        self._restore_fn = restore_fn
        super().__init__(info, thumb_provider=None,
                         thumb_size=thumb_size, parent=parent)

    def _icon_pixmap(self) -> Optional[QPixmap]:
        # A ZIP has no preview — always the zip-folder glyph (like Windows).
        return None

    def _build_body(self) -> QWidget:
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(2)

        name = self._info.get("name") or Path(self._info.get("path", "")).name
        name_lbl = QLabel(name)
        name_lbl.setObjectName("Mono")
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
        row.addWidget(self._make_menu_btn("归档操作", self._show_menu))
        body_lay.addLayout(row)
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
    """Muted box shown when a sequence has no files."""
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
    """One result sequence with optional aligned TIFF / ZIP columns."""

    def __init__(self, seq_label: str, tiff_row=None, zip_row=None,
                 *, tile_view: bool = True,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("ResultRow")
        self._rows = [r for r in (tiff_row, zip_row) if r is not None]
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 8)
        v.setSpacing(4)

        hdr = QLabel(seq_label)
        hdr.setObjectName("MutedSmall")
        v.addWidget(hdr)

        if not self._rows:
            v.addWidget(_placeholder("无成果"))
        elif tile_view:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)
            row.addWidget(tiff_row if tiff_row is not None else _placeholder("无 TIFF"), stretch=1)
            row.addWidget(zip_row if zip_row is not None else _placeholder("无 ZIP"), stretch=1)
            v.addLayout(row)
        else:
            for r in self._rows:
                v.addWidget(r)

    def set_thumb_size(self, size: int) -> None:
        for r in self._rows:
            if hasattr(r, "set_thumb_size"):
                r.set_thumb_size(size)


class _SpecimenResultHeader(QFrame):
    """Divider header for all-specimens result view."""

    clicked = pyqtSignal(str)

    def __init__(self, uid: str, row_count: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._uid = uid
        self.setObjectName("ResultGroupHeader")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"打开编号：{uid}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 6, 2, 2)
        lay.setSpacing(8)
        uid_lbl = QLabel(uid)
        uid_lbl.setObjectName("Mono")
        uid_lbl.setToolTip(uid)
        lay.addWidget(uid_lbl, stretch=1)
        count_lbl = QLabel(f"{row_count} 项")
        count_lbl.setObjectName("MutedSmall")
        lay.addWidget(count_lbl)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._uid:
            self.clicked.emit(self._uid)
            event.accept()
            return
        super().mousePressEvent(event)


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
    specimen_requested = pyqtSignal(str)  # all-results group header → select UID

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._thumb_size = _DEFAULT_THUMB
        self._thumb_cache: dict = {}
        self._cards: list = []
        self._collapsed = False
        self._sort_key = "seq"
        self._tile_view = True
        self._results_dir: str = ""
        self._current_tiffs: list[dict] = []
        self._current_zips: list[dict] = []
        self._current_groups: list[dict] = []
        self._display_mode = "single"
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

        self._title = QLabel("成果")
        self._title.setObjectName("WorkspaceTitle")
        hdr.addWidget(self._title)

        self._count = QLabel("0 项")
        self._count.setObjectName("MutedSmall")
        hdr.addWidget(self._count)
        hdr.addStretch()

        self._open_folder_btn = QPushButton("打开文件夹")
        self._open_folder_btn.setObjectName("Outline")
        self._open_folder_btn.setFixedHeight(28)
        self._open_folder_btn.setToolTip("在 Windows 文件资源管理器中打开 results 文件夹")
        icons.set_button_icon(self._open_folder_btn, "mdi6.folder-open-outline",
                              color=icons.TONE_ACCENT, size=14)
        self._open_folder_btn.clicked.connect(self._open_results_folder)
        hdr.addWidget(self._open_folder_btn)

        self._sort_btn = QPushButton("排序方式")
        self._sort_btn.setObjectName("Ghost")
        self._sort_btn.setFixedHeight(28)
        self._sort_btn.setToolTip("按文件名、类型、大小或修改时间排序")
        icons.set_button_icon(self._sort_btn, "mdi6.sort", color=icons.TONE_MUTED, size=14)
        self._sort_btn.clicked.connect(self._show_sort_menu)
        hdr.addWidget(self._sort_btn)

        self._tile_btn = QPushButton("平铺")
        self._tile_btn.setObjectName("Ghost")
        self._tile_btn.setCheckable(True)
        self._tile_btn.setChecked(True)
        self._tile_btn.setFixedHeight(28)
        self._tile_btn.setToolTip("平铺为左右两列：左 TIFF，右 ZIP")
        icons.set_button_icon(self._tile_btn, "mdi6.view-grid-outline",
                              color=icons.TONE_MUTED, size=14)
        self._tile_btn.clicked.connect(self._set_tile_view)
        hdr.addWidget(self._tile_btn)

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
        self._body.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._body.customContextMenuRequested.connect(self._show_body_menu)
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
        self._display_mode = "single"
        self._current_groups = []
        self._clear_rows()
        self._title.setText("成果")

        self._current_tiffs = list(composed_tiffs or [])
        self._current_zips = list(archive_zips or [])
        self._remember_results_dir(composed_tiffs, archive_zips)
        composed_tiffs = list(composed_tiffs or [])
        archive_zips = list(archive_zips or [])
        rows = self._sort_rows(_pair_results(composed_tiffs, archive_zips))
        all_tiff_paths = [
            Path(tinfo["path"]) for _label, tinfo, _zinfo in rows
            if tinfo is not None and tinfo.get("path")
        ]
        for seq_label, tinfo, zinfo in rows:
            tc = None
            zc = None
            if tinfo is not None:
                tc = _TiffCard(
                    tinfo,
                    open_fn=self._open_in_explorer,
                    lightbox_fn=lambda p, _paths=all_tiff_paths: self._open_tiff_lightbox(p, _paths),
                    thumb_provider=self._thumb_provider,
                    thumb_size=self._thumb_size,
                )
                self._cards.append(tc)
            if zinfo is not None:
                zc = _ArchiveCard(
                    zinfo, open_fn=self._open_in_explorer,
                    restore_fn=lambda p: self.restore_requested.emit(p),
                    thumb_size=self._thumb_size,
                )
                self._cards.append(zc)
            self._rows_lay.addWidget(
                _ResultRow(seq_label, tc, zc, tile_view=self._tile_view)
            )

        n = len(rows)
        self._count.setText(f"{n} 项")
        if not n:
            self._show_empty()

    def load_many(self, groups: list[dict]) -> None:
        """Populate results for multiple specimen UIDs, grouped by UID."""
        self._display_mode = "many"
        self._title.setText("全部成果")
        self._current_groups = [
            {
                "uid": str(g.get("uid") or ""),
                "tiffs": list(g.get("tiffs") or []),
                "zips": list(g.get("zips") or []),
            }
            for g in list(groups or [])
        ]
        self._current_tiffs = [
            item for g in self._current_groups for item in g.get("tiffs", [])
        ]
        self._current_zips = [
            item for g in self._current_groups for item in g.get("zips", [])
        ]
        self._clear_rows()
        self._remember_results_dir(self._current_tiffs, self._current_zips)

        total_rows = 0
        visible_groups = 0
        for group in self._current_groups:
            rows = self._sort_rows(_pair_results(group["tiffs"], group["zips"]))
            if not rows:
                continue
            visible_groups += 1
            total_rows += len(rows)
            header = _SpecimenResultHeader(group["uid"], len(rows))
            header.clicked.connect(self.specimen_requested.emit)
            self._rows_lay.addWidget(header)
            all_tiff_paths = [
                Path(tinfo["path"]) for _label, tinfo, _zinfo in rows
                if tinfo is not None and tinfo.get("path")
            ]
            for seq_label, tinfo, zinfo in rows:
                tc = None
                zc = None
                if tinfo is not None:
                    tc = _TiffCard(
                        tinfo,
                        open_fn=self._open_in_explorer,
                        lightbox_fn=lambda p, _paths=all_tiff_paths: self._open_tiff_lightbox(p, _paths),
                        thumb_provider=self._thumb_provider,
                        thumb_size=self._thumb_size,
                    )
                    self._cards.append(tc)
                if zinfo is not None:
                    zc = _ArchiveCard(
                        zinfo, open_fn=self._open_in_explorer,
                        restore_fn=lambda p: self.restore_requested.emit(p),
                        thumb_size=self._thumb_size,
                    )
                    self._cards.append(zc)
                self._rows_lay.addWidget(
                    _ResultRow(seq_label, tc, zc, tile_view=self._tile_view)
                )

        self._count.setText(f"{visible_groups} 编号 / {total_rows} 项")
        if not total_rows:
            self._show_empty("暂无成果：当前项目还没有已整理结果")

    def clear(self) -> None:
        """Reset to empty (暂无成果) state."""
        self._clear_rows()
        self._current_tiffs = []
        self._current_zips = []
        self._current_groups = []
        self._display_mode = "single"
        self._title.setText("成果")
        self._results_dir = ""
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

    def _show_empty(self, text: str = "暂无成果") -> None:
        self._count.setText("0 项")
        empty = QLabel(text)
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

    def _show_sort_menu(self) -> None:
        self._show_sort_menu_at(self._sort_btn.mapToGlobal(self._sort_btn.rect().bottomLeft()))

    def _show_body_menu(self, pos) -> None:
        self._show_sort_menu_at(self._body.viewport().mapToGlobal(pos), include_open=True)

    def _show_sort_menu_at(self, global_pos, *, include_open: bool = False) -> None:
        menu = QMenu(self)
        if include_open:
            open_act = menu.addAction("打开 results 文件夹")
            open_act.setEnabled(bool(self._results_dir))
            open_act.triggered.connect(self._open_results_folder)
            menu.addSeparator()
        for key, label in (
            ("seq", "按顺序"),
            ("name", "按名称"),
            ("type", "按类型"),
            ("size", "按大小"),
            ("mtime", "按修改时间"),
        ):
            act = menu.addAction(("✓ " if self._sort_key == key else "") + label)
            act.triggered.connect(lambda _=False, k=key: self._set_sort_key(k))
        menu.exec(global_pos)

    def _set_sort_key(self, key: str) -> None:
        if key not in {"seq", "name", "type", "size", "mtime"}:
            return
        self._sort_key = key
        self._sort_btn.setText({
            "seq": "顺序",
            "name": "名称",
            "type": "类型",
            "size": "大小",
            "mtime": "修改时间",
        }[key])
        if self._display_mode == "many":
            self.load_many(self._current_groups)
        else:
            self.load_uid("", self._current_tiffs, self._current_zips)

    def _set_tile_view(self, checked: bool) -> None:
        self._tile_view = bool(checked)
        self._tile_btn.setText("平铺" if self._tile_view else "列表")
        if self._display_mode == "many":
            self.load_many(self._current_groups)
        else:
            self.load_uid("", self._current_tiffs, self._current_zips)

    def _sort_rows(self, rows: list) -> list:
        def stat_value(info: dict, attr: str, default=0):
            if not info:
                return default
            try:
                return getattr(Path(str(info.get("path") or "")).stat(), attr)
            except Exception:
                return default

        def file_name(info: dict) -> str:
            if not info:
                return ""
            path = Path(str(info.get("path") or info.get("name") or ""))
            return str(info.get("name") or path.name)

        def seq_value(info: dict):
            if not info:
                return (1, "")
            seq = info.get("seq")
            if seq is None:
                return (1, Path(info.get("path") or info.get("name") or "").stem.lower())
            try:
                return (0, int(seq))
            except Exception:
                return (0, str(seq).lower())

        def key(row):
            _label, tinfo, zinfo = row
            primary = tinfo or zinfo or {}
            name = file_name(primary)
            secondary = file_name(zinfo if primary is tinfo else tinfo)
            name_key = (name.lower(), secondary.lower())
            if self._sort_key == "seq":
                return (seq_value(primary), name_key)
            if self._sort_key == "type":
                return (0 if tinfo else 1, Path(name).suffix.lower(), name.lower())
            if self._sort_key == "size":
                size = int((tinfo or {}).get("size") or stat_value(tinfo, "st_size"))
                size += int((zinfo or {}).get("size") or stat_value(zinfo, "st_size"))
                return (size, name.lower())
            if self._sort_key == "mtime":
                return (max(stat_value(tinfo, "st_mtime"), stat_value(zinfo, "st_mtime")),
                        name.lower())
            return name_key

        return sorted(list(rows or []), key=key)

    def _remember_results_dir(self, composed_tiffs: list, archive_zips: list) -> None:
        for info in list(composed_tiffs or []) + list(archive_zips or []):
            path = str(info.get("path") or "")
            if path:
                self._results_dir = str(Path(path).parent)
                return

    def _open_results_folder(self) -> None:
        if self._results_dir:
            self._open_in_explorer(self._results_dir)

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
            is_dir = os.path.isdir(path)
            if sys.platform == "win32":
                if is_dir:
                    subprocess.Popen(["explorer", os.path.normpath(path)])
                else:
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                # WSL: open Windows Explorer via explorer.exe with wslpath
                win_path = path
                try:
                    from app.utils.path_utils import wsl_to_windows
                    win_path = wsl_to_windows(path) or path
                except Exception:
                    pass
                if is_dir:
                    subprocess.Popen(["explorer.exe", win_path])
                else:
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
