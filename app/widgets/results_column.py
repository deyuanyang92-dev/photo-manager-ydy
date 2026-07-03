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
from collections import deque
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QEvent, QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
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
        self._preview_pixmap = QPixmap()
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def set_scroll_area(self, scroll_area: QScrollArea) -> None:
        self._scroll_area = scroll_area

    def set_preview_pixmap(self, pixmap: QPixmap, logical_width: int, logical_height: int) -> None:
        self._preview_pixmap = QPixmap(pixmap)
        self.setText("")
        self.resize(max(1, int(logical_width)), max(1, int(logical_height)))
        self.update()

    def clear_preview_pixmap(self) -> None:
        self._preview_pixmap = QPixmap()
        self.update()

    def pixmap(self) -> Optional[QPixmap]:
        if self._preview_pixmap.isNull():
            return None
        return QPixmap(self._preview_pixmap)

    def paintEvent(self, event) -> None:
        if self._preview_pixmap.isNull():
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(self.rect(), self._preview_pixmap)

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
        self._preview_sharpen_downscale = True
        self._scaled_cache_key = None
        self._scaled_cache = QPixmap()

        layout = QVBoxLayout(self)

        self._info_label = QLabel()
        layout.addWidget(self._info_label)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label = _PanImageLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setMinimumSize(1, 1)
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
        actual_btn = QPushButton("100%")
        actual_btn.setToolTip("按原始像素显示 (Ctrl+1)")
        actual_btn.clicked.connect(self._actual_size)
        zoom_row.addWidget(actual_btn)
        self._sharpen_btn = QPushButton("锐化")
        self._sharpen_btn.setCheckable(True)
        self._sharpen_btn.setChecked(True)
        self._sharpen_btn.setToolTip("缩小时增强预览清晰度")
        self._sharpen_btn.toggled.connect(self._set_preview_sharpen)
        zoom_row.addWidget(self._sharpen_btn)
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

        pixmap = _decode_preview_pixmap(str(path))
        if pixmap is not None and not pixmap.isNull():
            self._base_pixmap = pixmap
            self._scaled_cache_key = None
            self._scaled_cache = QPixmap()
            self._update_info_label()
            self._fit_to_window = True
            self._render_current()
        else:
            self._base_pixmap = QPixmap()
            self._scaled_cache_key = None
            self._scaled_cache = QPixmap()
            self._image_label.clear_preview_pixmap()
            self._image_label.setText(f"无法预览: {path.name}")

        self._prev_btn.setEnabled(self._index > 0)
        self._next_btn.setEnabled(self._index < len(self._paths) - 1)

    def _render_current(self) -> None:
        if self._base_pixmap.isNull():
            return
        src_w = max(1, self._base_pixmap.width())
        src_h = max(1, self._base_pixmap.height())
        dpr = self._preview_device_pixel_ratio()
        if self._fit_to_window:
            viewport = self._scroll.viewport().size()
            max_w = max(120, int((viewport.width() - 24) * dpr))
            max_h = max(120, int((viewport.height() - 24) * dpr))
            scale = min(max_w / src_w, max_h / src_h)
            scale = max(0.01, scale)
            target_w = max(1, int(src_w * scale))
            target_h = max(1, int(src_h * scale))
            ratio = int(round(scale * 100))
            self._zoom_slider.blockSignals(True)
            self._zoom_slider.setValue(max(25, min(400, ratio)))
            self._zoom_slider.blockSignals(False)
            self._zoom_value.setText(f"适合窗口 {ratio}%")
        else:
            pct = self._zoom_slider.value()
            target_w = max(1, int(src_w * pct / 100))
            target_h = max(1, int(src_h * pct / 100))
            self._zoom_value.setText(f"{pct}%")
        scaled = self._scaled_preview_pixmap(target_w, target_h, dpr)
        logical_w = max(1, int(round(target_w / dpr)))
        logical_h = max(1, int(round(target_h / dpr)))
        self._image_label.set_preview_pixmap(scaled, logical_w, logical_h)

    def _preview_device_pixel_ratio(self) -> float:
        ratio = self._scroll.viewport().devicePixelRatioF()
        if ratio <= 0:
            screen = self.screen()
            ratio = screen.devicePixelRatio() if screen is not None else 1.0
        return max(1.0, float(ratio))

    def _scaled_preview_pixmap(self, width: int, height: int, dpr: float) -> QPixmap:
        width = max(1, int(width))
        height = max(1, int(height))
        sharpen = (
            self._preview_sharpen_downscale
            and width < self._base_pixmap.width()
            and height < self._base_pixmap.height()
        )
        key = (width, height, round(float(dpr), 3), sharpen)
        if self._scaled_cache_key == key and not self._scaled_cache.isNull():
            return QPixmap(self._scaled_cache)
        if width == self._base_pixmap.width() and height == self._base_pixmap.height():
            scaled = QPixmap(self._base_pixmap)
        else:
            scaled = self._base_pixmap.scaled(
                width,
                height,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        if sharpen:
            scaled = self._sharpen_preview_pixmap(scaled)
        self._scaled_cache_key = key
        self._scaled_cache = QPixmap(scaled)
        return scaled

    def _sharpen_preview_pixmap(self, pixmap: QPixmap) -> QPixmap:
        try:
            from PIL import Image, ImageFilter
        except Exception:
            return pixmap
        image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        width = image.width()
        height = image.height()
        if width <= 1 or height <= 1:
            return pixmap
        ptr = image.bits()
        ptr.setsize(image.sizeInBytes())
        pil = Image.frombuffer(
            "RGBA",
            (width, height),
            bytes(ptr),
            "raw",
            "RGBA",
            image.bytesPerLine(),
            1,
        )
        pil = pil.filter(ImageFilter.UnsharpMask(radius=0.8, percent=110, threshold=2))
        raw = pil.tobytes("raw", "RGBA")
        qimage = QImage(raw, width, height, width * 4, QImage.Format.Format_RGBA8888)
        return QPixmap.fromImage(qimage.copy())

    def _set_preview_sharpen(self, enabled: bool) -> None:
        self._preview_sharpen_downscale = bool(enabled)
        self._scaled_cache_key = None
        self._scaled_cache = QPixmap()
        self._render_current()

    def _update_info_label(self) -> None:
        path = self._paths[self._index]
        if self._base_pixmap.isNull():
            size_text = ""
        else:
            size_text = f" · 原图 {self._base_pixmap.width()}×{self._base_pixmap.height()} px"
        self._info_label.setText(
            f"{path.name}  ({self._index + 1} / {len(self._paths)}){size_text}"
        )

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

_DEFAULT_THUMB = 48
_LARGE_THUMB_MIN_SIZE = 128
_BASE_THUMB = 280  # base decode size; zoom scales DOWN from this cached pixmap
_PREVIEW_MAX_SIZE = None
_MIN_PAIRED_COLUMNS_WIDTH = 640
_LARGE_THUMB_CARD_EXTRA_HEIGHT = 52


def _decode_thumb(path: str, max_size: int = _BASE_THUMB) -> Optional[QPixmap]:
    """Decode *path* to a QPixmap downscaled to ``max_size`` (KeepAspectRatio).

    Returns None for empty / missing / undecodable paths.  Callers fall back to
    an icon placeholder; TIFF decoding uses the shared multi-backend helper.
    """
    from app.utils.image_thumbnail import decode_image_thumbnail

    return decode_image_thumbnail(path, max_size)


def _decode_preview_pixmap(
    path: str,
    max_size: Optional[int] = _PREVIEW_MAX_SIZE,
) -> Optional[QPixmap]:
    """Decode a preview pixmap using the shared robust image backends."""
    from app.utils.image_thumbnail import decode_image_pixmap, decode_image_thumbnail

    if max_size is None:
        return decode_image_pixmap(path, use_cache=False)
    return decode_image_thumbnail(path, max_size=max_size, use_cache=False)


# ── Individual result cards ────────────────────────────────────────────────────

class _ResultCardBase(QFrame):
    """Shared base: one compact file-list row — icon LEFT, name + meta RIGHT
    (Windows Explorer list style).  Subclasses supply the icon source, the meta
    text and the context-menu actions."""

    _FALLBACK_ICON = "mdi6.file-image-outline"
    _ICON_TINT = "#3a6b75"

    def __init__(self, info: dict, thumb_provider=None,
                 select_fn=None, selected: bool = False,
                 thumb_size: int = _DEFAULT_THUMB,
                 result_view_mode: str = "list",
                 defer_thumbnail: bool = False,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("ResultFile")
        self.setMinimumWidth(0)
        self._result_view_mode = (
            "large_thumbnail"
            if result_view_mode == "large_thumbnail"
            else "list"
        )
        self.setProperty("resultViewMode", self._result_view_mode)
        self._info = info
        self._thumb_size = thumb_size
        self._thumb_provider = thumb_provider
        self._defer_thumbnail = defer_thumbnail
        self._thumbnail_loaded = False
        self._select_fn = select_fn
        self._selected = selected
        self._base_pm: Optional[QPixmap] = None
        self._setup_ui()
        self.set_selected(selected)

    def _setup_ui(self) -> None:
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if self._result_view_mode == "large_thumbnail":
            self._setup_large_thumbnail_ui()
        else:
            self._setup_list_ui()

        path = self._info.get("path", "")
        if self._thumb_provider and path and not self._defer_thumbnail:
            self.load_thumbnail_now()
        self._apply_icon()

    def _create_select_badge(self) -> QLabel:
        badge = QLabel("")
        badge.setObjectName("ResultSelectBadge")
        badge.setFixedSize(18, 18)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setToolTip("单击选择/取消选择")
        return badge

    def _create_icon_label(self) -> QLabel:
        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setFixedSize(self._display_thumb_size(), self._display_thumb_size())
        return icon_label

    def _setup_list_ui(self) -> None:
        lay = QHBoxLayout(self)
        lay.setContentsMargins(7, 4, 7, 4)
        lay.setSpacing(7)

        self._select_badge = self._create_select_badge()
        lay.addWidget(self._select_badge)

        self._icon = self._create_icon_label()
        lay.addWidget(self._icon)

        body = self._build_body()
        body.setMinimumWidth(0)
        body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay.addWidget(body, stretch=1)

    def _setup_large_thumbnail_ui(self) -> None:
        self.setMinimumHeight(self._large_thumbnail_min_height())
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(5)

        media_row = QHBoxLayout()
        media_row.setContentsMargins(0, 0, 0, 0)
        media_row.setSpacing(8)
        self._select_badge = self._create_select_badge()
        media_row.addWidget(
            self._select_badge,
            alignment=Qt.AlignmentFlag.AlignTop,
        )
        media_row.addStretch()
        self._icon = self._create_icon_label()
        media_row.addWidget(self._icon, alignment=Qt.AlignmentFlag.AlignCenter)
        media_row.addStretch()
        media_row.addSpacing(self._select_badge.width())
        lay.addLayout(media_row)

        body = self._build_body()
        body.setMinimumWidth(0)
        body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay.addWidget(body)

    def _display_thumb_size(self) -> int:
        if self._result_view_mode == "large_thumbnail":
            return max(_LARGE_THUMB_MIN_SIZE, self._thumb_size)
        return self._thumb_size

    def _large_thumbnail_min_height(self) -> int:
        return self._display_thumb_size() + _LARGE_THUMB_CARD_EXTRA_HEIGHT

    def load_thumbnail_now(self) -> None:
        if self._thumbnail_loaded:
            return
        self._thumbnail_loaded = True
        path = self._info.get("path", "")
        if self._thumb_provider and path:
            self._base_pm = self._thumb_provider(path)
        self._apply_icon()

    def _build_body(self) -> QWidget:  # override
        return QWidget()

    def _registry_line(self, kind: str, counterpart_path: str = "") -> str:
        uid = str(self._info.get("owner_uid") or self._info.get("uid") or "")
        group_index = self._info.get("group_index")
        registered = bool(self._info.get("registered", bool(uid)))
        if uid:
            where = f"已入库: {uid}"
            if group_index is not None:
                where += f" / 组 {int(group_index) + 1}"
        elif registered:
            where = "已入库"
        else:
            where = "未入库"
        pair_label = "ZIP" if kind == "tiff" else "TIF"
        pair = f"已配 {pair_label}" if counterpart_path else f"缺 {pair_label}"
        return f"{where} · {pair}"

    def _show_registry_info(self, kind: str, counterpart_path: str = "") -> None:
        from PyQt6.QtWidgets import QMessageBox

        uid = str(self._info.get("owner_uid") or self._info.get("uid") or "")
        group_index = self._info.get("group_index")
        lines = [
            f"类型: {'TIFF 成果' if kind == 'tiff' else 'ZIP 归档'}",
            f"文件: {self._info.get('path') or ''}",
            f"入库: {'是' if self._info.get('registered', bool(uid)) else '否'}",
            f"关联编号: {uid or '未关联'}",
        ]
        if group_index is not None:
            lines.append(f"分组: {int(group_index) + 1}")
        if counterpart_path:
            lines.append(f"配对文件: {counterpart_path}")
        else:
            lines.append("配对文件: 未找到")
        QMessageBox.information(self, "入库/关联信息", "\n".join(lines))

    def _icon_pixmap(self) -> Optional[QPixmap]:
        """Override to force a glyph (e.g. ZIP).  Default = decoded thumbnail."""
        return self._base_pm

    def _apply_icon(self) -> None:
        s = self._display_thumb_size()
        self._icon.setFixedSize(s, s)
        self._icon.setStyleSheet("border:none; background:transparent;")
        pm = self._icon_pixmap()
        if pm is not None and not pm.isNull():
            self._icon.setProperty("hasThumbnail", True)
            self._icon.setPixmap(pm.scaled(
                s, s,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        else:
            self._icon.setProperty("hasThumbnail", False)
            glyph_cap = 76 if self._result_view_mode == "large_thumbnail" else 26
            g = min(glyph_cap, max(12, s - 6))
            self._icon.setPixmap(
                icons.icon(self._FALLBACK_ICON, color=self._ICON_TINT).pixmap(g, g)
            )
        self._icon.style().unpolish(self._icon)
        self._icon.style().polish(self._icon)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            path = self._info.get("path", "")
            if self._select_fn and path:
                self._select_fn(path, self)
                event.accept()
                return
        super().mousePressEvent(event)

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self.setProperty("resultSelected", "true" if self._selected else "false")
        self._select_badge.setText("✓" if self._selected else "")
        self._select_badge.setProperty("selected", "true" if self._selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self._select_badge.style().unpolish(self._select_badge)
        self._select_badge.style().polish(self._select_badge)

    def set_thumb_size(self, size: int) -> None:
        self._thumb_size = size
        if self._result_view_mode == "large_thumbnail":
            self.setMinimumHeight(self._large_thumbnail_min_height())
        self._apply_icon()


class _TiffCard(_ResultCardBase):
    """One composed-TIFF file row: thumbnail icon + filename + 已合成."""

    _FALLBACK_ICON = "mdi6.file-image-outline"

    def __init__(self, info: dict, open_fn=None, lightbox_fn=None,
                 link_fn=None, paired_zip: str = "",
                 naming_check_fn=None, delete_fn=None,
                 select_fn=None, selected: bool = False,
                 thumb_provider=None, thumb_size: int = _DEFAULT_THUMB,
                 result_view_mode: str = "list",
                 defer_thumbnail: bool = False,
                 parent: Optional[QWidget] = None) -> None:
        self._open_fn = open_fn
        self._lightbox_fn = lightbox_fn
        self._link_fn = link_fn
        self._paired_zip = paired_zip
        self._naming_check_fn = naming_check_fn
        self._delete_fn = delete_fn
        super().__init__(info, thumb_provider=thumb_provider,
                         select_fn=select_fn, selected=selected,
                         thumb_size=thumb_size,
                         result_view_mode=result_view_mode,
                         defer_thumbnail=defer_thumbnail,
                         parent=parent)

    def _build_body(self) -> QWidget:
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(2)

        name = (
            self._info.get("display_name")
            or self._info.get("name")
            or Path(self._info.get("path", "")).name
        )
        name_lbl = QLabel(name)
        name_lbl.setObjectName("Mono")
        name_lbl.setMinimumWidth(0)
        name_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        if self._result_view_mode == "large_thumbnail":
            name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name_lbl.setWordWrap(True)
            name_lbl.setMaximumHeight(42)
        full_name = self._info.get("name") or Path(self._info.get("path", "")).name
        name_lbl.setToolTip(self._info.get("path", full_name))
        body_lay.addWidget(name_lbl)

        registry_lbl = QLabel(f"TIF · {self._registry_line('tiff', self._paired_zip)}")
        registry_lbl.setObjectName("ResultMeta")
        registry_lbl.setMinimumWidth(0)
        registry_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        if self._result_view_mode == "large_thumbnail":
            registry_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        registry_lbl.setToolTip(self._registry_line("tiff", self._paired_zip))
        body_lay.addWidget(registry_lbl)
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
        check_action = menu.addAction("检查 TIF 命名格式")
        check_action.setEnabled(bool(self._naming_check_fn and path))
        check_action.triggered.connect(lambda: self._naming_check_fn(path))
        open_action = menu.addAction("在文件夹中显示")
        open_action.setEnabled(bool(self._open_fn and path))
        open_action.triggered.connect(lambda: self._open_fn(path))
        copy_action = menu.addAction("复制路径")
        copy_action.setEnabled(bool(path))
        copy_action.triggered.connect(lambda: QApplication.clipboard().setText(path))
        info_action = menu.addAction("查看入库/关联信息")
        info_action.triggered.connect(
            lambda: self._show_registry_info("tiff", self._paired_zip)
        )
        menu.addSeparator()
        link_action = menu.addAction("关联到右侧编号")
        link_action.setEnabled(bool(self._link_fn and path))
        link_action.triggered.connect(lambda: self._link_fn(path, self._paired_zip))
        menu.addSeparator()
        delete_action = menu.addAction("删除 TIF")
        delete_action.setEnabled(bool(self._delete_fn and path))
        delete_action.triggered.connect(lambda: self._delete_fn(path))
        menu.exec(global_pos)


class _ArchiveCard(_ResultCardBase):
    """One ZIP-archive file row: Windows-style zip-folder icon + filename + size."""

    _FALLBACK_ICON = "mdi6.folder-zip-outline"
    _ICON_TINT = "#c9981f"  # Windows zip-folder amber

    def __init__(self, info: dict, open_fn=None, restore_fn=None,
                 link_fn=None, paired_tiff: str = "",
                 select_fn=None, selected: bool = False,
                 thumb_size: int = _DEFAULT_THUMB,
                 result_view_mode: str = "list",
                 parent: Optional[QWidget] = None) -> None:
        self._open_fn = open_fn
        self._restore_fn = restore_fn
        self._link_fn = link_fn
        self._paired_tiff = paired_tiff
        super().__init__(info, thumb_provider=None,
                         select_fn=select_fn, selected=selected,
                         thumb_size=thumb_size,
                         result_view_mode=result_view_mode,
                         parent=parent)

    def _icon_pixmap(self) -> Optional[QPixmap]:
        # A ZIP has no preview — always the zip-folder glyph (like Windows).
        return None

    def _build_body(self) -> QWidget:
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(2)

        name = (
            self._info.get("display_name")
            or self._info.get("name")
            or Path(self._info.get("path", "")).name
        )
        name_lbl = QLabel(name)
        name_lbl.setObjectName("Mono")
        name_lbl.setMinimumWidth(0)
        name_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        if self._result_view_mode == "large_thumbnail":
            name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name_lbl.setWordWrap(True)
            name_lbl.setMaximumHeight(42)
        full_name = self._info.get("name") or Path(self._info.get("path", "")).name
        name_lbl.setToolTip(self._info.get("path", full_name))
        body_lay.addWidget(name_lbl)

        size_bytes = self._info.get("size", 0)
        size_str = _fmt_size(size_bytes) if size_bytes else "已归档"
        registry_lbl = QLabel(
            f"ZIP · {size_str} · {self._registry_line('zip', self._paired_tiff)}"
        )
        registry_lbl.setObjectName("ResultMeta")
        registry_lbl.setMinimumWidth(0)
        registry_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        if self._result_view_mode == "large_thumbnail":
            registry_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        registry_lbl.setToolTip(self._registry_line("zip", self._paired_tiff))
        body_lay.addWidget(registry_lbl)
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
        info_action = menu.addAction("查看入库/关联信息")
        info_action.triggered.connect(
            lambda: self._show_registry_info("zip", self._paired_tiff)
        )
        menu.addSeparator()
        link_action = menu.addAction("关联到右侧编号")
        link_action.setEnabled(bool(self._link_fn and path))
        link_action.triggered.connect(lambda: self._link_fn(self._paired_tiff, path))
        menu.exec(global_pos)


def _placeholder(text: str) -> QWidget:
    """Muted box shown when a sequence has no files."""
    f = QFrame()
    f.setObjectName("ResultPlaceholder")
    f.setMinimumWidth(0)
    lay = QVBoxLayout(f)
    lay.setContentsMargins(8, 6, 8, 6)
    lbl = QLabel(text)
    lbl.setObjectName("Muted")
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setWordWrap(True)
    lay.addWidget(lbl)
    return f


class _ResultPairIndicator(QFrame):
    """Visual indicator for a visible TIFF/ZIP pair in two-column mode."""

    def __init__(
        self,
        tiff_path: str = "",
        zip_path: str = "",
        *,
        has_pair: bool = False,
        selected: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ResultPairIndicator")
        self.setFixedWidth(24)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._pair_paths = (
            [str(tiff_path or ""), str(zip_path or "")]
            if has_pair else []
        )
        self._has_pair = bool(has_pair)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._left_line = QFrame()
        self._left_line.setObjectName("ResultPairLine")
        self._left_line.setFixedHeight(1)
        lay.addWidget(self._left_line, stretch=1)

        self._arrow = QLabel("→" if has_pair else "")
        self._arrow.setObjectName("ResultPairArrow")
        self._arrow.setFixedSize(16, 18)
        self._arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._arrow.setToolTip("同一成果：左侧 TIF 对应右侧 ZIP" if has_pair else "")
        lay.addWidget(self._arrow)

        self._right_line = QFrame()
        self._right_line.setObjectName("ResultPairLine")
        self._right_line.setFixedHeight(1)
        lay.addWidget(self._right_line, stretch=1)

        self._apply_state(selected)

    def visible_pair_paths(self) -> list[str]:
        return list(self._pair_paths)

    def set_selected(self, selected: bool) -> None:
        self._apply_state(selected)

    def _apply_state(self, selected: bool) -> None:
        for widget in (self, self._arrow, self._left_line, self._right_line):
            widget.setProperty("hasPair", "true" if self._has_pair else "false")
            widget.setProperty("selected", "true" if selected else "false")
            widget.style().unpolish(widget)
            widget.style().polish(widget)


class _ResultRow(QFrame):
    """One result sequence with optional aligned TIFF / ZIP columns."""

    def __init__(self, seq_label: str, tiff_row=None, zip_row=None,
                 *, show_paired_columns: bool = True,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("ResultRow")
        self.setProperty("pairedColumns", "true" if show_paired_columns else "false")
        self.setMinimumWidth(0)
        self._rows = [r for r in (tiff_row, zip_row) if r is not None]
        self._pair_indicator = None
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        seq_badge = QLabel(_compact_result_sequence_label(seq_label))
        seq_badge.setObjectName("ResultSeqBadge")
        seq_badge.setFixedWidth(34)
        seq_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        seq_badge.setToolTip(seq_label)
        row.addWidget(seq_badge)

        if not self._rows:
            row.addWidget(_placeholder("无成果"), stretch=1)
        elif show_paired_columns:
            row.addWidget(
                tiff_row if tiff_row is not None else _placeholder("无 TIFF"),
                stretch=1,
            )
            has_pair = tiff_row is not None and zip_row is not None
            tiff_path = tiff_row._info.get("path", "") if has_pair else ""
            zip_path = zip_row._info.get("path", "") if has_pair else ""
            selected = bool(
                has_pair
                and getattr(tiff_row, "_selected", False)
                and getattr(zip_row, "_selected", False)
            )
            self._pair_indicator = _ResultPairIndicator(
                tiff_path, zip_path, has_pair=has_pair, selected=selected
            )
            row.addWidget(self._pair_indicator)
            row.addWidget(
                zip_row if zip_row is not None else _placeholder("无 ZIP"),
                stretch=1,
            )
        else:
            stack = QVBoxLayout()
            stack.setContentsMargins(0, 0, 0, 0)
            stack.setSpacing(4)
            for r in self._rows:
                stack.addWidget(r)
            row.addLayout(stack, stretch=1)

    def set_thumb_size(self, size: int) -> None:
        for r in self._rows:
            if hasattr(r, "set_thumb_size"):
                r.set_thumb_size(size)


def _compact_result_sequence_label(seq_label: str) -> str:
    text = str(seq_label or "").strip()
    prefix = "成果 "
    if text.startswith(prefix) and text[len(prefix):].strip():
        return text[len(prefix):].strip()
    return text or "·"


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


def _unique_id_from_result_name(name: str, seq=None) -> str:
    """Return the specimen UID portion of a result filename when it matches."""
    stem = Path(name).stem
    parts = stem.split("-")
    if len(parts) < 7:
        return stem
    if seq is not None:
        seq_text = str(seq)
        for idx in range(3, len(parts)):
            if parts[idx] == seq_text:
                return "-".join(parts[:idx] + parts[idx + 1:])
    try:
        int(parts[4])
    except ValueError:
        return stem
    return "-".join(parts[:4] + parts[5:])


# ── ResultsColumn ──────────────────────────────────────────────────────────────

class ResultsColumn(QWidget):
    """② 成果内容 column: collapsible, zoomable, paired TIFF↔ZIP rows."""

    restore_requested = pyqtSignal(str)  # ZIP 绝对路径 → 还原原片 JPG
    specimen_requested = pyqtSignal(str)  # all-results group header → select UID
    show_all_requested = pyqtSignal()
    current_requested = pyqtSignal()
    link_result_requested = pyqtSignal(str, str)  # tiff_path, zip_path
    tiff_naming_check_requested = pyqtSignal(str)  # tiff_path
    tiff_delete_requested = pyqtSignal(str)  # tiff_path

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._thumb_size = _DEFAULT_THUMB
        self._result_view_mode = "list"
        self._thumb_cache: dict = {}
        self._thumb_queue: deque[_ResultCardBase] = deque()
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setInterval(20)
        self._thumb_timer.timeout.connect(self._load_next_thumbnail_batch)
        self._layout_refresh_timer = QTimer(self)
        self._layout_refresh_timer.setSingleShot(True)
        self._layout_refresh_timer.setInterval(60)
        self._layout_refresh_timer.timeout.connect(self._refresh_layout_if_needed)
        self._cards: list = []
        self._collapsed = False
        self._sort_key = "seq"
        self._paired_columns_enabled = True
        self._paired_selection_enabled = False
        self._results_dir: str = ""
        self._current_tiffs: list[dict] = []
        self._current_zips: list[dict] = []
        self._current_groups: list[dict] = []
        self._selected_result_paths: set[str] = set()
        self._display_mode = "single"
        self._filename_mode = "full"
        self._rendered_paired_columns = True
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
        hdr.setSpacing(8)

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

        self._current_mode_btn = QPushButton("当前")
        self._current_mode_btn.setObjectName("Ghost")
        self._current_mode_btn.setCheckable(True)
        self._current_mode_btn.setChecked(True)
        self._current_mode_btn.setFixedSize(44, 28)
        self._current_mode_btn.setToolTip("只显示当前选中编号的 TIFF 和 ZIP")
        self._current_mode_btn.clicked.connect(lambda: self.current_requested.emit())
        hdr.addWidget(self._current_mode_btn)

        self._all_mode_btn = QPushButton("全部")
        self._all_mode_btn.setObjectName("Ghost")
        self._all_mode_btn.setCheckable(True)
        self._all_mode_btn.setFixedSize(44, 28)
        self._all_mode_btn.setToolTip("按编号分组显示当前项目全部 TIFF 和 ZIP")
        self._all_mode_btn.clicked.connect(lambda: self.show_all_requested.emit())
        hdr.addWidget(self._all_mode_btn)
        hdr.addStretch()

        self._paired_selection_btn = QPushButton("联选")
        self._paired_selection_btn.setObjectName("Ghost")
        self._paired_selection_btn.setCheckable(True)
        self._paired_selection_btn.setChecked(False)
        self._paired_selection_btn.setFixedSize(56, 28)
        self._paired_selection_btn.clicked.connect(self._set_paired_selection_enabled)
        hdr.addWidget(self._paired_selection_btn)
        self._update_paired_selection_button_state()

        self._options_btn = QPushButton("")
        self._options_btn.setObjectName("Ghost")
        self._options_btn.setFixedSize(28, 28)
        self._options_btn.setToolTip("结果显示与排序选项")
        icons.set_button_icon(self._options_btn, "mdi6.dots-vertical",
                              color=icons.TONE_MUTED, size=14)
        self._options_btn.clicked.connect(self._show_results_options_menu)
        hdr.addWidget(self._options_btn)

        root.addLayout(hdr)
        self._update_options_button_tooltip()

        divider = QFrame()
        divider.setObjectName("Divider")
        divider.setFixedHeight(1)
        root.addWidget(divider)

        # ── Body: scrollable paired rows ──
        self._body = QScrollArea()
        self._body.setObjectName("ResultsScroll")
        self._body.setWidgetResizable(True)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._body.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._body.customContextMenuRequested.connect(self._show_body_menu)
        self._body.viewport().installEventFilter(self)
        self._body.verticalScrollBar().setSingleStep(36)
        self._rows_container = QWidget()
        self._rows_lay = QVBoxLayout(self._rows_container)
        self._rows_lay.setContentsMargins(0, 2, 0, 2)
        self._rows_lay.setSpacing(6)
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
        self._update_mode_button_state()
        self._current_groups = []
        self._clear_rows()
        self._title.setText("成果")

        self._current_tiffs = list(composed_tiffs or [])
        self._current_zips = list(archive_zips or [])
        self._prune_selected_result_paths()
        self._remember_results_dir(composed_tiffs, archive_zips)
        composed_tiffs = list(composed_tiffs or [])
        archive_zips = list(archive_zips or [])
        rows = self._sort_rows(_pair_results(composed_tiffs, archive_zips))
        show_paired_columns = self._should_show_paired_columns()
        self._rendered_paired_columns = show_paired_columns
        all_tiff_paths = [
            Path(tinfo["path"]) for _label, tinfo, _zinfo in rows
            if tinfo is not None and tinfo.get("path")
        ]
        for seq_label, tinfo, zinfo in rows:
            tc = None
            zc = None
            if tinfo is not None:
                tc = _TiffCard(
                    self._display_info(tinfo),
                    open_fn=self._open_in_explorer,
                    lightbox_fn=lambda p, _paths=all_tiff_paths: self._open_tiff_lightbox(p, _paths),
                    link_fn=self._emit_link_result,
                    paired_zip=(zinfo or {}).get("path", "") if zinfo else "",
                    naming_check_fn=self._emit_tiff_naming_check,
                    delete_fn=self._emit_tiff_delete,
                    select_fn=self._toggle_result_selection,
                    selected=self._is_result_selected(tinfo.get("path", "")),
                    thumb_provider=self._thumb_provider,
                    thumb_size=self._thumb_size,
                    result_view_mode=self._result_view_mode,
                    defer_thumbnail=True,
                )
                self._cards.append(tc)
                self._queue_thumbnail(tc)
            if zinfo is not None:
                zc = _ArchiveCard(
                    self._display_info(zinfo), open_fn=self._open_in_explorer,
                    restore_fn=lambda p: self.restore_requested.emit(p),
                    link_fn=self._emit_link_result,
                    paired_tiff=(tinfo or {}).get("path", "") if tinfo else "",
                    select_fn=self._toggle_result_selection,
                    selected=self._is_result_selected(zinfo.get("path", "")),
                    thumb_size=self._thumb_size,
                    result_view_mode=self._result_view_mode,
                )
                self._cards.append(zc)
            self._rows_lay.addWidget(
                _ResultRow(
                    seq_label, tc, zc,
                    show_paired_columns=show_paired_columns,
                )
            )

        n = len(rows)
        self._count.setText(f"{n} 项")
        self._update_options_button_tooltip()
        if not n:
            self._show_empty()

    def load_many(self, groups: list[dict]) -> None:
        """Populate results for multiple specimen UIDs, grouped by UID."""
        self._display_mode = "many"
        self._update_mode_button_state()
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
        self._prune_selected_result_paths()
        self._clear_rows()
        self._remember_results_dir(self._current_tiffs, self._current_zips)

        total_rows = 0
        visible_groups = 0
        show_paired_columns = self._should_show_paired_columns()
        self._rendered_paired_columns = show_paired_columns
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
                        self._display_info(tinfo),
                        open_fn=self._open_in_explorer,
                        lightbox_fn=lambda p, _paths=all_tiff_paths: self._open_tiff_lightbox(p, _paths),
                        link_fn=self._emit_link_result,
                        paired_zip=(zinfo or {}).get("path", "") if zinfo else "",
                        naming_check_fn=self._emit_tiff_naming_check,
                        delete_fn=self._emit_tiff_delete,
                        select_fn=self._toggle_result_selection,
                        selected=self._is_result_selected(tinfo.get("path", "")),
                        thumb_provider=self._thumb_provider,
                        thumb_size=self._thumb_size,
                        result_view_mode=self._result_view_mode,
                        defer_thumbnail=True,
                    )
                    self._cards.append(tc)
                    self._queue_thumbnail(tc)
                if zinfo is not None:
                    zc = _ArchiveCard(
                        self._display_info(zinfo), open_fn=self._open_in_explorer,
                        restore_fn=lambda p: self.restore_requested.emit(p),
                        link_fn=self._emit_link_result,
                        paired_tiff=(tinfo or {}).get("path", "") if tinfo else "",
                        select_fn=self._toggle_result_selection,
                        selected=self._is_result_selected(zinfo.get("path", "")),
                        thumb_size=self._thumb_size,
                        result_view_mode=self._result_view_mode,
                    )
                    self._cards.append(zc)
                self._rows_lay.addWidget(
                    _ResultRow(
                        seq_label, tc, zc,
                        show_paired_columns=show_paired_columns,
                    )
                )

        self._count.setText(f"{visible_groups} 编号 / {total_rows} 项")
        self._update_options_button_tooltip()
        if not total_rows:
            self._show_empty("暂无成果：当前项目还没有已整理结果")

    def clear(self) -> None:
        """Reset to empty (暂无成果) state."""
        self._clear_rows()
        self._current_tiffs = []
        self._current_zips = []
        self._current_groups = []
        self._selected_result_paths.clear()
        self._display_mode = "single"
        self._update_mode_button_state()
        self._title.setText("成果")
        self._results_dir = ""
        self._show_empty()

    def _emit_link_result(self, tiff_path: str, zip_path: str) -> None:
        self.link_result_requested.emit(tiff_path or "", zip_path or "")

    def _emit_tiff_naming_check(self, tiff_path: str) -> None:
        self.tiff_naming_check_requested.emit(tiff_path or "")

    def _emit_tiff_delete(self, tiff_path: str) -> None:
        self.tiff_delete_requested.emit(tiff_path or "")

    def selected_result_paths(self) -> list[str]:
        return sorted(self._selected_result_paths)

    def _result_key(self, path: str) -> str:
        if not path:
            return ""
        try:
            return str(Path(path).resolve())
        except OSError:
            return str(path)

    def _is_result_selected(self, path: str) -> bool:
        key = self._result_key(path)
        return bool(key and key in self._selected_result_paths)

    def _toggle_result_selection(self, path: str, card=None) -> None:
        key = self._result_key(path)
        if not key:
            return

        keys = {key}
        if self._paired_selection_enabled:
            tiff_key, zip_key = self._visible_result_pair_for(key)
            if tiff_key and zip_key:
                keys = {tiff_key, zip_key}

        selected = not all(k in self._selected_result_paths for k in keys)
        if selected:
            self._selected_result_paths.update(keys)
        else:
            self._selected_result_paths.difference_update(keys)
        for c in self._cards:
            c_key = self._result_key(c._info.get("path", ""))
            if c_key in keys and hasattr(c, "set_selected"):
                c.set_selected(c_key in self._selected_result_paths)
        self._update_visible_pair_indicator_state()

    def _prune_selected_result_paths(self) -> None:
        valid = self._visible_result_keys()
        self._selected_result_paths &= valid
        self._update_visible_pair_indicator_state()

    def _visible_result_keys(self) -> set[str]:
        return {
            self._result_key(info.get("path", ""))
            for info in self._current_tiffs + self._current_zips
            if info.get("path")
        }

    def _visible_result_pair_for(self, path: str) -> tuple[str, str]:
        target_key = self._result_key(path)
        if not target_key:
            return "", ""
        groups = (
            self._current_groups
            if self._current_groups
            else [{"tiffs": self._current_tiffs, "zips": self._current_zips}]
        )
        for group in groups:
            for _label, tinfo, zinfo in _pair_results(
                list(group.get("tiffs") or []),
                list(group.get("zips") or []),
            ):
                if not tinfo or not zinfo:
                    continue
                tiff_key = self._result_key(tinfo.get("path", ""))
                zip_key = self._result_key(zinfo.get("path", ""))
                if tiff_key and zip_key and target_key in {tiff_key, zip_key}:
                    return tiff_key, zip_key
        return "", ""

    def _update_visible_pair_indicator_state(self) -> None:
        for indicator in self._rows_container.findChildren(_ResultPairIndicator):
            keys = [self._result_key(p) for p in indicator.visible_pair_paths()]
            keys = [k for k in keys if k]
            indicator.set_selected(bool(keys) and all(
                k in self._selected_result_paths for k in keys
            ))

    def _set_paired_selection_enabled(self, checked: bool) -> None:
        self._paired_selection_enabled = bool(checked)
        self._update_paired_selection_button_state()
        self._update_options_button_tooltip()

    def _update_paired_selection_button_state(self) -> None:
        if not hasattr(self, "_paired_selection_btn"):
            return
        self._paired_selection_btn.blockSignals(True)
        self._paired_selection_btn.setChecked(self._paired_selection_enabled)
        self._paired_selection_btn.blockSignals(False)
        self._paired_selection_btn.setObjectName(
            "Outline" if self._paired_selection_enabled else "Ghost"
        )
        icon_name = (
            "mdi6.checkbox-marked-outline"
            if self._paired_selection_enabled
            else "mdi6.checkbox-blank-outline"
        )
        color = (
            icons.TONE_ACCENT
            if self._paired_selection_enabled
            else icons.TONE_MUTED
        )
        icons.set_button_icon(
            self._paired_selection_btn, icon_name, color=color, size=14
        )
        state = "开" if self._paired_selection_enabled else "关"
        self._paired_selection_btn.setToolTip(
            f"联选：{state}；开启后单击 TIF/ZIP 会同时选择当前可见配对"
        )
        self._paired_selection_btn.style().unpolish(self._paired_selection_btn)
        self._paired_selection_btn.style().polish(self._paired_selection_btn)

    # ── Internals ───────────────────────────────────────────────────────────────

    def _thumb_provider(self, path: str) -> Optional[QPixmap]:
        """Return a cached base pixmap for *path* (None if undecodable)."""
        if path in self._thumb_cache:
            return self._thumb_cache[path]
        pm = _decode_thumb(path, _BASE_THUMB)
        self._thumb_cache[path] = pm
        return pm

    def _queue_thumbnail(self, card: "_ResultCardBase") -> None:
        self._thumb_queue.append(card)
        if not self._thumb_timer.isActive():
            self._thumb_timer.start()

    def _load_next_thumbnail_batch(self) -> None:
        loaded = 0
        while self._thumb_queue and loaded < 2:
            card = self._thumb_queue.popleft()
            try:
                if card.parent() is not None:
                    card.load_thumbnail_now()
                    loaded += 1
            except RuntimeError:
                continue
        if not self._thumb_queue:
            self._thumb_timer.stop()

    def _clear_rows(self) -> None:
        self._thumb_queue.clear()
        self._thumb_timer.stop()
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

    def _display_info(self, info: dict) -> dict:
        out = dict(info)
        raw_name = out.get("name") or Path(out.get("path", "")).name
        if self._filename_mode == "uid":
            out["display_name"] = _unique_id_from_result_name(
                str(raw_name), out.get("seq")
            )
        else:
            out.pop("display_name", None)
        return out

    def _refresh_current_results(self) -> None:
        if self._display_mode == "many":
            self.load_many(self._current_groups)
        else:
            self.load_uid("", self._current_tiffs, self._current_zips)

    def _should_show_paired_columns(self) -> bool:
        if not self._paired_columns_enabled:
            return False
        width = 0
        if hasattr(self, "_body"):
            width = self._body.viewport().width()
        if width <= 0:
            width = self.width()
        if width <= 0 or not self.isVisible():
            return True
        return width >= _MIN_PAIRED_COLUMNS_WIDTH

    def _schedule_layout_refresh(self) -> None:
        if hasattr(self, "_layout_refresh_timer"):
            self._layout_refresh_timer.start()

    def _refresh_layout_if_needed(self) -> None:
        if not (self._current_tiffs or self._current_zips or self._current_groups):
            return
        show_paired_columns = self._should_show_paired_columns()
        if show_paired_columns != self._rendered_paired_columns:
            self._refresh_current_results()

    def eventFilter(self, obj, event) -> bool:
        if hasattr(self, "_body") and obj is self._body.viewport():
            if event.type() == QEvent.Type.Resize:
                self._schedule_layout_refresh()
        return super().eventFilter(obj, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._schedule_layout_refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._schedule_layout_refresh()

    def _show_results_options_menu(self) -> None:
        self._show_results_options_menu_at(
            self._options_btn.mapToGlobal(self._options_btn.rect().bottomLeft())
        )

    def _show_body_menu(self, pos) -> None:
        self._show_results_options_menu_at(
            self._body.viewport().mapToGlobal(pos),
            include_open=True,
        )

    def _show_results_options_menu_at(self, global_pos, *,
                                      include_open: bool = False) -> None:
        menu = QMenu(self)
        if include_open or self._results_dir:
            open_act = menu.addAction("打开 results 文件夹")
            open_act.setEnabled(bool(self._results_dir))
            open_act.triggered.connect(self._open_results_folder)
            menu.addSeparator()

        view_menu = menu.addMenu("显示方式")
        for key, label in (
            ("list", "列表"),
            ("large_thumbnail", "大缩略图"),
        ):
            act = view_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._result_view_mode == key)
            act.triggered.connect(
                lambda _=False, k=key: self._set_result_view_mode(k)
            )

        name_menu = menu.addMenu("显示名称")
        for key, label in (
            ("full", "完整文件名"),
            ("uid", "唯一编号"),
        ):
            act = name_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._filename_mode == key)
            act.triggered.connect(lambda _=False, k=key: self._set_filename_mode(k))

        sort_menu = menu.addMenu("排序")
        for key, label in (
            ("seq", "顺序"),
            ("name", "名称"),
            ("type", "类型"),
            ("size", "大小"),
            ("mtime", "修改时间"),
        ):
            act = sort_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._sort_key == key)
            act.triggered.connect(lambda _=False, k=key: self._set_sort_key(k))

        thumb_menu = menu.addMenu("缩略图大小")
        for size, label in (
            (48, "小"),
            (72, "中"),
            (112, "大"),
            (144, "最大"),
        ):
            act = thumb_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._thumb_size == size)
            act.triggered.connect(lambda _=False, s=size: self._set_zoom(s))

        menu.addSeparator()
        columns_act = menu.addAction("双栏对照")
        columns_act.setCheckable(True)
        columns_act.setChecked(self._paired_columns_enabled)
        columns_act.triggered.connect(self._set_paired_columns_enabled)

        paired_selection_act = menu.addAction("联选 TIF/ZIP")
        paired_selection_act.setCheckable(True)
        paired_selection_act.setChecked(self._paired_selection_enabled)
        paired_selection_act.triggered.connect(self._set_paired_selection_enabled)

        menu.exec(global_pos)

    def _set_filename_mode(self, mode: str) -> None:
        if mode not in {"full", "uid"} or mode == self._filename_mode:
            return
        self._filename_mode = mode
        self._update_options_button_tooltip()
        self._refresh_current_results()

    def _set_result_view_mode(self, mode: str) -> None:
        if mode not in {"list", "large_thumbnail"} or mode == self._result_view_mode:
            return
        self._result_view_mode = mode
        self._update_options_button_tooltip()
        self._refresh_current_results()

    def _set_zoom(self, size: int) -> None:
        """Resize every result thumbnail."""
        self._thumb_size = size
        for c in self._cards:
            c.set_thumb_size(size)
        self._update_options_button_tooltip()

    def _set_collapsed(self, collapsed: bool) -> None:
        """Collapse / expand the whole results area (single toggle)."""
        self._collapsed = collapsed
        self._body.setVisible(not collapsed)
        self._collapse_btn.setText("▸" if collapsed else "▾")
        self._collapse_btn.setChecked(collapsed)

    def _update_mode_button_state(self) -> None:
        is_many = self._display_mode == "many"
        for btn, checked in (
            (self._current_mode_btn, not is_many),
            (self._all_mode_btn, is_many),
        ):
            btn.blockSignals(True)
            btn.setChecked(checked)
            btn.setObjectName("Outline" if checked else "Ghost")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.blockSignals(False)

    def _set_sort_key(self, key: str) -> None:
        if key not in {"seq", "name", "type", "size", "mtime"}:
            return
        self._sort_key = key
        self._update_options_button_tooltip()
        if self._display_mode == "many":
            self.load_many(self._current_groups)
        else:
            self.load_uid("", self._current_tiffs, self._current_zips)

    def _set_paired_columns_enabled(self, checked: bool) -> None:
        self._paired_columns_enabled = bool(checked)
        self._update_options_button_tooltip()
        if self._display_mode == "many":
            self.load_many(self._current_groups)
        else:
            self.load_uid("", self._current_tiffs, self._current_zips)

    def _update_options_button_tooltip(self) -> None:
        if not hasattr(self, "_options_btn"):
            return
        view = "大缩略图" if self._result_view_mode == "large_thumbnail" else "列表"
        filename = "唯一编号" if self._filename_mode == "uid" else "完整文件名"
        sort_label = {
            "seq": "顺序",
            "name": "名称",
            "type": "类型",
            "size": "大小",
            "mtime": "修改时间",
        }.get(self._sort_key, "顺序")
        columns = (
            "双栏对照"
            if self._paired_columns_enabled and self._rendered_paired_columns
            else "单列"
        )
        if self._paired_columns_enabled and not self._rendered_paired_columns:
            columns = "单列(宽度不足)"
        paired = "联选开" if self._paired_selection_enabled else "联选关"
        self._options_btn.setToolTip(
            f"结果选项：{view} / {filename} / {sort_label} / {self._thumb_size}px / {columns} / {paired}"
        )

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

        def sort_key_for_result_pair(row):
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

        return sorted(list(rows or []), key=sort_key_for_result_pair)

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
