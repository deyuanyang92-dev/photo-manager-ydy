"""screenshot_pin.py — pin a captured image as a floating always-on-top window.

Snipaste-style sticky note: frameless, stays on top, drag anywhere to move,
mouse-wheel to scale, Ctrl+C copies, Esc / double-click closes. Handy for
keeping a reference shot visible while working in another view.
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget


class PinWindow(QWidget):
    _open: list["PinWindow"] = []  # keep refs alive

    def __init__(self, pixmap: QPixmap) -> None:
        super().__init__()
        self._pixmap = pixmap
        self._scale = 1.0
        self._drag_from: QPoint | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setToolTip("拖动移动 · 滚轮缩放 · Ctrl+C 复制 · 双击/Esc 关闭")
        self._apply_size()

    def show_at(self, top_left: QPoint) -> None:
        self.move(top_left)
        self.show()
        self.raise_()
        self.activateWindow()
        PinWindow._open.append(self)

    # ── paint ────────────────────────────────────────────────────────────
    def _logical_size(self):
        dpr = self._pixmap.devicePixelRatio() or 1.0
        return (
            int(self._pixmap.width() / dpr * self._scale),
            int(self._pixmap.height() / dpr * self._scale),
        )

    def _apply_size(self) -> None:
        w, h = self._logical_size()
        self.setFixedSize(max(1, w), max(1, h))

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(self.rect(), self._pixmap)
        p.setPen(Qt.GlobalColor.gray)
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.end()

    # ── interaction ──────────────────────────────────────────────────────
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_from = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e) -> None:
        if self._drag_from is not None:
            self.move(e.globalPosition().toPoint() - self._drag_from)

    def mouseReleaseEvent(self, _e) -> None:
        self._drag_from = None

    def mouseDoubleClickEvent(self, _e) -> None:
        self.close()

    def wheelEvent(self, e) -> None:
        step = 1.1 if e.angleDelta().y() > 0 else 1 / 1.1
        self._scale = max(0.1, min(8.0, self._scale * step))
        self._apply_size()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.close()
        elif e.key() == Qt.Key.Key_C and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            QApplication.clipboard().setPixmap(self._pixmap)

    def closeEvent(self, e) -> None:
        if self in PinWindow._open:
            PinWindow._open.remove(self)
        super().closeEvent(e)
