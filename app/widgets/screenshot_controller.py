"""screenshot_controller.py — reusable screenshot entry point.

One controller owns the capture flow so any view can trigger a screenshot and
receive the result. It computes the preset selection per mode, opens the
:class:`ScreenshotOverlay` editor, and routes the editor's terminal actions to
the four destinations (clipboard / project auto-save / save-as / desktop pin).

Public API
----------
    ctrl = ScreenshotController(main_window, ctx, view_provider, status_cb)
    ctrl.capture_region()       # drag a rectangle
    ctrl.capture_fullscreen()   # whole screen, pre-selected
    ctrl.capture_window()       # the app window, pre-selected
    ctrl.capture_view()         # the current page widget, pre-selected
    ctrl.captured.connect(slot) # QPixmap delivered on any terminal action

The ``captured(QPixmap)`` signal lets a caller (e.g. a workbench "拍屏幕"
button) grab the pixels without caring about clipboard/save plumbing.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import QMimeData, QObject, QPoint, QRect, QUrl, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QWidget

from app.services.screenshot_service import default_screenshot_path
from app.utils import ui
from app.widgets.screenshot_overlay import ScreenshotOverlay
from app.widgets.screenshot_pin import PinWindow


class ScreenshotController(QObject):
    captured = pyqtSignal(QPixmap)

    def __init__(
        self,
        main_window: QWidget,
        ctx: object = None,
        view_provider: Optional[Callable[[], Optional[QWidget]]] = None,
        status_cb: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(main_window)
        self._win = main_window
        self._ctx = ctx
        self._view_provider = view_provider
        self._status_cb = status_cb
        self._overlay: Optional[ScreenshotOverlay] = None

    # ── public capture modes ───────────────────────────────────────────────
    def capture_region(self) -> None:
        # Under WSL the in-app overlay can't grab the Windows desktop (XWayland
        # root is black) — hand off to the Windows-native snipper (Snipaste /
        # screen-clip) so it behaves exactly like every other Windows app.
        if self._delegate_to_windows():
            return
        # screen=None → overlay grabs the monitor under the cursor (works with
        # any number of windows / monitors, regardless of which has focus).
        self._open(None, None)

    def capture_fullscreen(self) -> None:
        if self._delegate_to_windows():
            return
        screen = self._active_screen()
        full = QRect(QPoint(0, 0), screen.geometry().size()) if screen else None
        self._open(full, screen)

    def _delegate_to_windows(self) -> bool:
        """On WSL, launch the Windows-native snip and report it was handled."""
        from app.utils.win_screenshot import is_wsl, launch_windows_snip

        if not is_wsl():
            return False
        if launch_windows_snip():
            self._status("已唤起 Windows 截图工具（Snipaste / 屏幕截图）")
            return True
        return False

    def capture_window(self) -> None:
        win = self._active_window()
        scr = win.screen() if win else None
        self._open(self._screen_local_rect(win, scr), scr)

    def capture_view(self) -> None:
        widget = self._view_provider() if self._view_provider else None
        scr = widget.screen() if widget else None
        self._open(self._screen_local_rect(widget, scr) if widget else None, scr)

    # ── core ────────────────────────────────────────────────────────────────
    def _open(self, preset: Optional[QRect], screen=None) -> None:
        if self._overlay is not None and self._overlay.isVisible():
            return  # one overlay at a time; ignore re-entry while shown
        overlay = ScreenshotOverlay(self._win)
        overlay.actionCopy.connect(self._on_copy)
        overlay.actionSave.connect(self._on_save)
        overlay.actionDone.connect(self._on_done)
        overlay.actionPin.connect(self._on_pin)
        overlay.cancelled.connect(self._on_cancel)
        self._overlay = overlay  # keep alive while shown
        overlay.start(preset, screen)

    def _active_window(self) -> QWidget:
        """The window the user is actually on — active window, else main."""
        return QApplication.activeWindow() or self._win.window()

    def _active_screen(self):
        """Screen under the cursor, falling back to the active window's screen."""
        from PyQt6.QtGui import QCursor, QGuiApplication
        return (QGuiApplication.screenAt(QCursor.pos())
                or self._active_window().screen())

    def _screen_local_rect(self, widget: Optional[QWidget], screen=None) -> Optional[QRect]:
        """Map *widget*'s on-screen rect into overlay-local logical coords.

        *screen* must be the same screen the overlay will cover (the widget's
        own screen) — using the main window's screen mis-placed the preset on
        multi-monitor setups.
        """
        if widget is None:
            return None
        if screen is None:
            screen = widget.screen()
        if screen is None:
            return None
        origin = screen.geometry().topLeft()
        g = widget.frameGeometry() if widget is widget.window() else QRect(
            widget.mapToGlobal(QPoint(0, 0)), widget.size()
        )
        return g.translated(-origin)

    # ── destinations ────────────────────────────────────────────────────────
    def _on_copy(self, pix: QPixmap) -> None:
        self._to_clipboard(pix)
        self._status("截图已复制到剪贴板")
        self.captured.emit(pix)

    def _on_done(self, pix: QPixmap) -> None:
        self._to_clipboard(pix)
        saved = self._auto_save(pix)
        if saved:
            self._status(f"截图已存项目: {saved.name}（已复制剪贴板）")
        else:
            self._status("截图已复制到剪贴板")
        self.captured.emit(pix)

    def _on_save(self, pix: QPixmap) -> None:
        self._to_clipboard(pix)
        saved = self._auto_save(pix)
        start = str(saved) if saved else "截图.png"
        chosen = ui.get_save_file_name(self._win, "保存截图", start, "PNG 图片 (*.png)")
        if chosen:
            if not chosen.lower().endswith(".png"):
                chosen += ".png"
            pix.save(chosen, "PNG")
            self._status(f"截图已存: {Path(chosen).name}（已复制剪贴板）")
        elif saved:
            self._status(f"截图已存项目: {saved.name}（已复制剪贴板）")
        self.captured.emit(pix)

    def _on_pin(self, pix: QPixmap, global_tl: QPoint) -> None:
        win = PinWindow(pix)
        win.show_at(global_tl)
        self._to_clipboard(pix)
        self._status("截图已钉到桌面（已复制剪贴板）")
        self.captured.emit(pix)

    def _on_cancel(self) -> None:
        self._overlay = None

    # ── helpers ───────────────────────────────────────────────────────────
    def _to_clipboard(self, pix: QPixmap) -> None:
        """Put the shot on the clipboard in three formats at once.

        ``setPixmap`` alone only exposes raw image data, which terminal-based
        tools (e.g. pasting into the Claude Code prompt) on Linux can't read —
        they grab a *file path*, not a bitmap. So also write a temp PNG and
        expose its ``file://`` URL plus its path as text. Raw-bitmap consumers,
        file-URL consumers, and plain-text paste all then work.
        """
        md = QMimeData()
        md.setImageData(pix.toImage())
        path = self._temp_png(pix)
        if path is not None:
            md.setUrls([QUrl.fromLocalFile(str(path))])
            md.setText(str(path))
        QApplication.clipboard().setMimeData(md)

    def _temp_png(self, pix: QPixmap) -> Optional[Path]:
        try:
            fd, name = tempfile.mkstemp(prefix="shot-", suffix=".png")
            os.close(fd)
            target = Path(name)
            if pix.save(str(target), "PNG"):
                return target
        except OSError:
            pass
        return None

    def _auto_save(self, pix: QPixmap) -> Optional[Path]:
        project_dir = getattr(self._ctx, "current_project_dir", None) if self._ctx else None
        if not project_dir:
            return None
        # Project volume gone (unmounted drive)? Skip the project auto-save —
        # mkdir would fabricate a ghost tree at the mountpoint. Clipboard /
        # save-as still work.
        from app.services.project_paths import project_root_available
        if not project_root_available(project_dir):
            return None
        target = default_screenshot_path(project_dir, datetime.now())
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if pix.save(str(target), "PNG"):
                return target
        except OSError:
            pass
        return None

    def _status(self, text: str) -> None:
        if self._status_cb:
            self._status_cb(text)
