"""screenshot_overlay.py — fullscreen capture + annotation editor.

One widget drives every capture mode. The controller freezes the screen and
opens this overlay, optionally with a *preset* selection rectangle:

  * region     → preset=None, the user drags the rectangle out
  * fullscreen → preset = whole screen
  * window     → preset = the app window's rect (screen-local)
  * view       → preset = the current page widget's rect (screen-local)

After a selection exists the overlay enters *edit* mode: a floating
:class:`ScreenshotToolbar` appears and the user can layer annotations
(rect / arrow / pen / text / highlight / mosaic) directly on the frozen
pixels. Terminal toolbar actions render the composited result and emit it:

  actionCopy / actionSave / actionDone(QPixmap),  actionPin(QPixmap, QPoint global),
  cancelled()

The same rendered pixmap feeds clipboard, project auto-save, save-as and pin,
so on-screen == saved == pinned, byte-for-byte.
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPixmap,
)
from PyQt6.QtWidgets import QApplication, QLineEdit, QWidget

from app.widgets.screenshot_annotations import (
    DRAG_TOOLS,
    Annotation,
    Tool,
    paint_annotation,
)
from app.widgets.screenshot_toolbar import ScreenshotToolbar


def _pixmap_is_blank(pm: QPixmap) -> bool:
    """True if *pm* is null or every sampled pixel is pure black.

    ``QScreen.grabWindow(0)`` (root-window grab) returns an all-black pixmap
    under WSLg / XWayland — the X11 root holds no desktop pixels there — so a
    blank result signals that we must fall back to compositing app windows.
    """
    if pm.isNull():
        return True
    img = pm.toImage()
    w, h = img.width(), img.height()
    if w == 0 or h == 0:
        return True
    step_x = max(1, w // 32)
    step_y = max(1, h // 32)
    for x in range(0, w, step_x):
        for y in range(0, h, step_y):
            c = img.pixelColor(x, y)
            if c.red() or c.green() or c.blue():
                return False
    return True


def _composite_top_levels(screen, exclude: QWidget | None) -> QPixmap:
    """Compose every visible top-level widget onto a screen-sized canvas via
    ``QWidget.grab()`` — the capture path that works under WSLg/XWayland where
    the root grab is black. Non-app desktop areas stay a neutral dark grey.
    """
    geo = screen.geometry()
    dpr = screen.devicePixelRatio()
    canvas = QPixmap(int(geo.width() * dpr), int(geo.height() * dpr))
    canvas.setDevicePixelRatio(dpr)
    canvas.fill(QColor(28, 28, 30))
    p = QPainter(canvas)
    for w in QApplication.topLevelWidgets():
        if w is exclude or not w.isVisible() or w.width() <= 0 or w.height() <= 0:
            continue
        grab = w.grab()
        if grab.isNull():
            continue
        p.drawPixmap(w.mapToGlobal(QPoint(0, 0)) - geo.topLeft(), grab)
    p.end()
    return canvas


def _screen_has_app_window(screen, exclude: QWidget | None) -> bool:
    """True if any visible top-level app window intersects *screen*.

    Used only on the WSLg blank-grab path: when the screen we are about to
    composite holds no app window, the result would be an empty grey frame.
    """
    geo = screen.geometry()
    for w in QApplication.topLevelWidgets():
        if w is exclude or not w.isVisible() or w.width() <= 0 or w.height() <= 0:
            continue
        rect = QRect(w.mapToGlobal(QPoint(0, 0)), w.size())
        if rect.intersects(geo):
            return True
    return False


class ScreenshotOverlay(QWidget):
    actionCopy = pyqtSignal(QPixmap)
    actionSave = pyqtSignal(QPixmap)
    actionDone = pyqtSignal(QPixmap)
    actionPin = pyqtSignal(QPixmap, QPoint)
    cancelled = pyqtSignal()

    def __init__(self, anchor: QWidget | None = None) -> None:
        super().__init__()
        self._anchor = anchor
        self._frozen: QPixmap = QPixmap()
        self._preset: QRect | None = None

        self._sel: QRect | None = None        # finalized selection (logical)
        self._select_origin: QPoint | None = None  # selection-phase rubber band
        self._live_end: QPoint | None = None
        self._mouse_pos: QPoint | None = None

        self._annotations: list[Annotation] = []
        self._draft: Annotation | None = None  # annotation under construction
        self._tool: Tool | None = None
        self._color = QColor("#FF4040")
        self._width = 3
        self._text_edit: QLineEdit | None = None

        self._toolbar: ScreenshotToolbar | None = None

        # NOTE: no BypassWindowManagerHint — an override-redirect fullscreen
        # window renders black on some X11 compositors (Mutter/GNOME on certain
        # GPUs) and fails to stack above panels/docks. Let the WM manage it and
        # rely on showFullScreen() for true full-screen coverage.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        # The overlay can be launched from modal dialogs such as 经纬度导入.
        # Make it the active app-modal window so it can receive the drag input.
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)

    # ── lifecycle ─────────────────────────────────────────────────────────
    def start(self, preset_rect: QRect | None = None, screen=None) -> None:
        """Freeze *screen*; show fullscreen over it. *preset_rect* is in
        overlay-local logical coords (None → user selects a region).

        When *screen* is None (region capture), the screen under the cursor is
        used — so with multiple windows / monitors the overlay lands where the
        user is looking, not on the main window's screen.
        """
        if screen is None:
            from PyQt6.QtGui import QCursor
            screen = QGuiApplication.screenAt(QCursor.pos())
        if screen is None:
            screen = (self._anchor.screen() if self._anchor else None) or \
                QGuiApplication.primaryScreen()
        if screen is None:
            self.close()
            return
        self._frozen = self._grab_root(screen)
        if _pixmap_is_blank(self._frozen):
            # WSLg/XWayland: root grab is black — we can only grab app windows.
            # If the chosen screen (e.g. cursor monitor on an Alt+A global
            # hotkey) holds NO app window, capturing it yields a blank grey
            # frame. Redirect to the screen where the app actually lives so the
            # shot shows the app instead of nothing.
            if not _screen_has_app_window(screen, exclude=self):
                app_screen = self._app_screen()
                if app_screen is not None:
                    screen = app_screen
            self._frozen = _composite_top_levels(screen, exclude=self)
        self.setGeometry(screen.geometry())
        self._preset = preset_rect
        if preset_rect is not None:
            self._sel = preset_rect.intersected(self.rect())
            self._enter_edit_mode()
        self.showFullScreen()
        self.raise_()
        self.activateWindow()

    def _grab_root(self, screen) -> QPixmap:
        """Native full-desktop grab — overridable seam (tested by faking a
        blank result to exercise the WSLg composite fallback)."""
        return screen.grabWindow(0)

    def _app_screen(self):
        """Screen the launching app window lives on (WSLg redirect target)."""
        anchor = self._anchor
        if anchor is None:
            return None
        return anchor.window().screen()

    def closeEvent(self, e) -> None:
        """Return OS focus to the launching window on close.

        The overlay is a frameless, app-modal, stays-on-top fullscreen window.
        When it closes, most WMs auto-refocus the previous window — but WSLg /
        XWayland does NOT, leaving the app window inactive: Qt still processes
        events, yet the OS routes no clicks to it, so it *looks* frozen until
        the user alt-tabs. Re-activating the anchor here fixes every exit path
        (deliver / pin / Esc / right-click cancel) in one place.
        """
        anchor = self._anchor
        if anchor is not None:
            win = anchor.window()
            win.raise_()
            win.activateWindow()
        super().closeEvent(e)

    # ── painting ──────────────────────────────────────────────────────────
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        if not self._frozen.isNull():
            p.drawPixmap(self.rect(), self._frozen)
        p.fillRect(self.rect(), QColor(0, 0, 0, 120))

        sel = self._live_selection()
        if sel is None or sel.isNull():
            self._paint_hint(p)
            p.end()
            return

        # Un-dim the selection.
        if not self._frozen.isNull():
            dpr = self._frozen.devicePixelRatio()
            src = QRect(
                int(sel.x() * dpr), int(sel.y() * dpr),
                int(sel.width() * dpr), int(sel.height() * dpr),
            )
            p.drawPixmap(sel, self._frozen, src)

        # Annotations live only inside the selection.
        p.save()
        p.setClipRect(sel)
        for ann in self._annotations:
            paint_annotation(p, ann, self._frozen)
        if self._draft is not None:
            paint_annotation(p, self._draft, self._frozen)
        p.restore()

        pen = p.pen()
        pen.setColor(QColor(10, 132, 255))
        pen.setWidth(2)
        p.setPen(pen)
        p.drawRect(sel)
        self._paint_selection_badge(p, sel)
        if self._sel is None and self._mouse_pos is not None:
            self._paint_loupe(p, self._mouse_pos, sel)
        p.end()

    def _paint_hint(self, p: QPainter) -> None:
        p.setPen(QColor(235, 235, 235))
        p.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignCenter,
            "拖动选择截图区域 · Enter 完成 · Esc 取消",
        )

    def _paint_selection_badge(self, p: QPainter, sel: QRect) -> None:
        text = f"{sel.width()} x {sel.height()}"
        fm = p.fontMetrics()
        box = QRect(sel.x(), sel.y() - 28, fm.horizontalAdvance(text) + 18, 22)
        if box.top() < 4:
            box.moveTop(sel.y() + 6)
        box.moveLeft(max(4, min(box.left(), self.width() - box.width() - 4)))
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(18, 18, 18, 220))
        p.drawRoundedRect(box, 5, 5)
        p.setPen(QColor(245, 245, 245))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        p.restore()

    def _paint_loupe(self, p: QPainter, pos: QPoint, sel: QRect) -> None:
        if self._frozen.isNull() or not self.rect().contains(pos):
            return
        size = 122
        margin = 14
        target = QRect(pos.x() + margin, pos.y() + margin, size, size)
        if target.right() > self.width() - 4:
            target.moveRight(pos.x() - margin)
        if target.bottom() > self.height() - 4:
            target.moveBottom(pos.y() - margin)
        target = target.intersected(self.rect().adjusted(4, 4, -4, -4))
        if target.width() < 64 or target.height() < 64:
            return

        dpr = self._frozen.devicePixelRatio()
        src_size = 22
        src = QRect(
            int((pos.x() - src_size // 2) * dpr),
            int((pos.y() - src_size // 2) * dpr),
            int(src_size * dpr),
            int(src_size * dpr),
        )
        src = src.intersected(self._frozen.rect())
        img = self._frozen.toImage()
        px = img.pixelColor(
            max(0, min(img.width() - 1, int(pos.x() * dpr))),
            max(0, min(img.height() - 1, int(pos.y() * dpr))),
        )

        p.save()
        p.setPen(QColor(255, 255, 255, 230))
        p.setBrush(QColor(20, 20, 20, 235))
        p.drawRoundedRect(target, 7, 7)
        preview = target.adjusted(6, 6, -6, -28)
        p.drawPixmap(preview, self._frozen, src)
        cx, cy = preview.center().x(), preview.center().y()
        p.setPen(QColor(0, 132, 255, 230))
        p.drawLine(cx, preview.top(), cx, preview.bottom())
        p.drawLine(preview.left(), cy, preview.right(), cy)
        p.setFont(QFont("monospace", 8))
        p.setPen(QColor(245, 245, 245))
        p.drawText(
            target.adjusted(7, target.height() - 22, -7, -4),
            Qt.AlignmentFlag.AlignVCenter,
            f"{sel.width()}x{sel.height()}  {px.name().upper()}",
        )
        p.restore()

    # ── selection / drawing ────────────────────────────────────────────────
    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.RightButton:
            self._cancel()
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self._sel is None:
            self._select_origin = e.pos()
            return
        self._start_annotation(e.pos())

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        self._mouse_pos = e.pos()
        if self._select_origin is not None:
            self._live_end = e.pos()
            self.update()
        elif self._draft is not None:
            if self._draft.tool is Tool.PEN:
                self._draft.points.append(e.pos())
            else:
                self._draft.points[-1] = e.pos()
            self.update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self._select_origin is not None:
            end = e.pos()
            rect = QRect(self._select_origin, end).normalized()
            self._select_origin = None
            if rect.width() < 4 or rect.height() < 4:
                self._cancel()
                return
            self._sel = rect
            self._live_end = None
            self._enter_edit_mode()
            self.update()
        elif self._draft is not None:
            self._commit_draft()

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self._sel is not None:
            self._deliver(self.actionDone)
            return
        super().mouseDoubleClickEvent(e)

    @staticmethod
    def _is_cmd(e: QKeyEvent) -> bool:
        """True if Ctrl (Linux/Windows) or Meta is held.

        Mac keyboards used over a remote-desktop link to this Linux app send
        Cmd as Meta/Super rather than Ctrl, so accept either — keeps copy/undo
        working for native Ctrl and for Mac-Cmd-over-remote alike.
        """
        mods = e.modifiers()
        return bool(
            mods & Qt.KeyboardModifier.ControlModifier
            or mods & Qt.KeyboardModifier.MetaModifier
        )

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self._cancel()
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._sel is not None:
            self._deliver(self.actionDone)
        elif e.key() == Qt.Key.Key_Z and self._is_cmd(e):
            self._undo()
        elif e.key() == Qt.Key.Key_C and self._is_cmd(e) and self._sel is not None:
            self._deliver(self.actionCopy)
        elif e.key() == Qt.Key.Key_S and self._is_cmd(e) and self._sel is not None:
            self._deliver(self.actionSave)
        elif e.key() == Qt.Key.Key_P and self._sel is not None:
            self._deliver_pin()
        else:
            super().keyPressEvent(e)

    # ── annotation helpers ─────────────────────────────────────────────────
    def _start_annotation(self, pos: QPoint) -> None:
        if self._tool is None or self._sel is None or not self._sel.contains(pos):
            return
        if self._tool is Tool.TEXT:
            self._begin_text(pos)
            return
        self._draft = Annotation(
            tool=self._tool, points=[pos, pos],
            color=QColor(self._color), width=self._width,
        )
        if self._tool is Tool.PEN:
            self._draft.points = [pos]

    def _commit_draft(self) -> None:
        d = self._draft
        self._draft = None
        if d is None:
            return
        if d.tool is Tool.PEN and len(d.points) >= 2:
            self._annotations.append(d)
        elif d.tool in DRAG_TOOLS and not d.bounds_rect().isNull():
            r = d.bounds_rect()
            if r.width() >= 3 and r.height() >= 3:
                self._annotations.append(d)
        self.update()

    def _begin_text(self, pos: QPoint) -> None:
        self._commit_text()
        edit = QLineEdit(self)
        edit.setStyleSheet(
            f"background:rgba(0,0,0,140);color:{self._color.name()};"
            f"border:1px dashed {self._color.name()};font-size:{12 + self._width}px;"
        )
        edit.move(pos)
        edit.resize(180, 26 + self._width)
        edit.setProperty("anchor", pos)
        edit.returnPressed.connect(self._commit_text)
        edit.editingFinished.connect(self._commit_text)
        edit.show()
        edit.setFocus()
        self._text_edit = edit

    def _commit_text(self) -> None:
        edit = self._text_edit
        if edit is None:
            return
        self._text_edit = None
        text = edit.text().strip()
        anchor: QPoint = edit.property("anchor")
        edit.deleteLater()
        if text and anchor is not None:
            self._annotations.append(
                Annotation(
                    tool=Tool.TEXT, points=[anchor], text=text,
                    color=QColor(self._color), font_pt=12 + self._width,
                )
            )
            self.update()

    def _undo(self) -> None:
        if self._annotations:
            self._annotations.pop()
            self.update()

    # ── edit mode / toolbar ─────────────────────────────────────────────────
    def _enter_edit_mode(self) -> None:
        if self._toolbar is not None:
            return
        tb = ScreenshotToolbar(self)
        tb.toolChanged.connect(self._set_tool)
        tb.colorChanged.connect(self._set_color)
        tb.widthChanged.connect(self._set_width)
        tb.undoRequested.connect(self._undo)
        tb.copyRequested.connect(lambda: self._deliver(self.actionCopy))
        tb.saveRequested.connect(lambda: self._deliver(self.actionSave))
        tb.pinRequested.connect(self._deliver_pin)
        tb.doneRequested.connect(lambda: self._deliver(self.actionDone))
        tb.cancelRequested.connect(self._cancel)
        tb.adjustSize()
        self._toolbar = tb
        self._place_toolbar()
        tb.show()

    def _place_toolbar(self) -> None:
        if self._toolbar is None or self._sel is None:
            return
        tb = self._toolbar
        w, h = tb.width(), tb.height()
        x = min(self._sel.right() - w, self.width() - w - 4)
        x = max(4, x)
        y = self._sel.bottom() + 8
        if y + h > self.height() - 4:
            y = max(4, self._sel.top() - h - 8)
        tb.move(x, y)

    def _set_tool(self, tool) -> None:
        self._commit_text()
        self._tool = tool
        self.setCursor(
            Qt.CursorShape.ArrowCursor if tool is None else Qt.CursorShape.CrossCursor
        )

    def _set_color(self, color: QColor) -> None:
        self._color = color

    def _set_width(self, width: int) -> None:
        self._width = width

    # ── delivery ────────────────────────────────────────────────────────────
    def render_result(self) -> QPixmap:
        """Composite the selection region + annotations into a new QPixmap."""
        if self._sel is None or self._frozen.isNull():
            return QPixmap()
        sel = self._sel
        dpr = self._frozen.devicePixelRatio()
        phys = QRect(
            int(sel.x() * dpr), int(sel.y() * dpr),
            int(sel.width() * dpr), int(sel.height() * dpr),
        )
        out = self._frozen.copy(phys)
        p = QPainter(out)
        p.scale(dpr, dpr)
        p.translate(-sel.topLeft())
        for ann in self._annotations:
            paint_annotation(p, ann, self._frozen)
        p.end()
        out.setDevicePixelRatio(dpr)
        return out

    def _deliver(self, signal) -> None:
        self._commit_text()
        pix = self.render_result()
        self.close()
        if not pix.isNull():
            signal.emit(pix)

    def _deliver_pin(self) -> None:
        self._commit_text()
        pix = self.render_result()
        global_tl = self.mapToGlobal(self._sel.topLeft()) if self._sel else QPoint(0, 0)
        self.close()
        if not pix.isNull():
            self.actionPin.emit(pix, global_tl)

    def _cancel(self) -> None:
        self.close()
        self.cancelled.emit()

    # ── helpers ─────────────────────────────────────────────────────────────
    def _live_selection(self) -> QRect | None:
        if self._sel is not None:
            return self._sel
        if self._select_origin is not None:
            end = self._live_end or self._select_origin
            return QRect(self._select_origin, end).normalized()
        return None
