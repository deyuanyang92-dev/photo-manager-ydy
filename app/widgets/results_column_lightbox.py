"""TIFF lightbox preview dialog used by ResultsColumn."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
)

_PREVIEW_MAX_SIZE = None


def _decode_preview_pixmap(
    path: str,
    max_size: Optional[int] = _PREVIEW_MAX_SIZE,
) -> Optional[QPixmap]:
    """Decode a preview pixmap using the shared robust image backends."""
    from app.utils.image_thumbnail import decode_image_pixmap, decode_image_thumbnail

    if max_size is None:
        return decode_image_pixmap(path, use_cache=False)
    return decode_image_thumbnail(path, max_size=max_size, use_cache=False)


def _facade_decode_preview_pixmap(path: str) -> Optional[QPixmap]:
    facade = sys.modules.get("app.widgets.results_column")
    if facade is not None:
        func = getattr(facade, "_decode_preview_pixmap", None)
        if callable(func) and func is not _decode_preview_pixmap:
            return func(path)
    return _decode_preview_pixmap(path)

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

        pixmap = _facade_decode_preview_pixmap(str(path))
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

