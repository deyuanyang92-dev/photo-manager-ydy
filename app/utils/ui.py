"""ui.py — Screen-aware dialog helpers.

Solves the dual-monitor problem: under WSLg / multi-screen setups the
native OS file-picker appears on the wrong screen, and QMessageBox
parents may be misaligned.  All helpers here:

  1. Walk up to the true top-level QWidget (top_window).
  2. Use QFileDialog.Option.DontUseNativeDialog so Qt owns placement.
  3. Center the dialog on the parent window's screen geometry.

Public API
----------
top_window(w)                        → QWidget
center_on(dialog, parent)            → None

get_existing_directory(parent, caption, start="")     → str | None
get_open_file_name(parent, caption, start="", filter="", **kw)  → str | None
get_open_file_names(parent, caption, start="", filter="", **kw) → list[str]
get_save_file_name(parent, caption, start="", filter="", **kw)  → str | None

warn(parent, title, text, **kw)      → QMessageBox.StandardButton
info(parent, title, text, **kw)      → QMessageBox.StandardButton
question(parent, title, text, **kw)  → QMessageBox.StandardButton
critical(parent, title, text, **kw)  → QMessageBox.StandardButton
"""
from __future__ import annotations

import traceback
from contextlib import contextmanager
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QListView,
    QMessageBox,
    QTreeView,
    QWidget,
)


# ── Internal helpers ──────────────────────────────────────────────────────────

def top_window(w: Optional[QWidget]) -> Optional[QWidget]:
    """Return the top-level QWidget ancestor of *w*, or *w* itself.

    Parameters
    ----------
    w:
        Any widget or None.

    Returns
    -------
    The top-level window, or None if *w* is None.
    """
    if w is None:
        return None
    root = w
    while root.parent() is not None and isinstance(root.parent(), QWidget):
        root = root.parent()  # type: ignore[assignment]
    return root


def center_on(dialog: QDialog, parent: Optional[QWidget]) -> None:
    """Move *dialog* to the centre of the screen that *parent* lives on.

    Safe to call before exec() — the dialog must already have a layout
    so that sizeHint() is meaningful.

    Parameters
    ----------
    dialog:
        The dialog to reposition.
    parent:
        Any widget that identifies the target screen.  If None the
        dialog is left at its current position.
    """
    if parent is None:
        return
    top = top_window(parent)
    if top is None:
        return
    screen = top.screen()
    if screen is None:
        return
    avail = screen.availableGeometry()
    dlg_size = dialog.sizeHint()
    x = avail.x() + (avail.width() - dlg_size.width()) // 2
    y = avail.y() + (avail.height() - dlg_size.height()) // 2
    dialog.move(x, y)


# ── File / directory pickers ──────────────────────────────────────────────────

_NO_NATIVE = QFileDialog.Option.DontUseNativeDialog


def _sort_dialog_by_mtime_desc(dialog: QFileDialog) -> None:
    """Sort a non-native file dialog by Date Modified, newest first."""
    dialog.setViewMode(QFileDialog.ViewMode.Detail)
    for view in dialog.findChildren(QTreeView) + dialog.findChildren(QListView):
        model = view.model()
        if model is None or model.columnCount() < 4:
            continue
        try:
            view.setSortingEnabled(True)
            view.sortByColumn(3, Qt.SortOrder.DescendingOrder)
            model.sort(3, Qt.SortOrder.DescendingOrder)
        except Exception:
            continue
    dialog.resize(max(dialog.width(), 820), max(dialog.height(), 560))


def get_existing_directory(
    parent: Optional[QWidget],
    caption: str,
    start: str = "",
) -> str:
    """Open a directory-picker dialog.

    Returns the selected path (str) or an empty string if cancelled.
    Uses a non-native Qt picker, centered on *parent*'s screen so it is not
    hidden behind non-modal popups (e.g. the grouping-tool dialog).
    """
    top = top_window(parent)
    dlg = QFileDialog(top, caption, start or "")
    dlg.setFileMode(QFileDialog.FileMode.Directory)
    dlg.setOption(_NO_NATIVE, True)
    dlg.setOption(QFileDialog.Option.ShowDirsOnly, True)
    center_on(dlg, top)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return ""
    files = dlg.selectedFiles()
    return files[0] if files else ""


def get_open_file_name(
    parent: Optional[QWidget],
    caption: str,
    start: str = "",
    filter: str = "",  # noqa: A002
    **kw,
) -> str:
    """Open a file-open dialog.

    Returns the selected path (str) or an empty string if cancelled.
    """
    sort_by_mtime = bool(kw.pop("sort_by_mtime", False))
    top = top_window(parent)
    if sort_by_mtime:
        dlg = QFileDialog(top, caption, start or "")
        dlg.setOption(_NO_NATIVE, True)
        dlg.setFileMode(QFileDialog.FileMode.ExistingFile)
        if filter:
            dlg.setNameFilter(filter)
        _sort_dialog_by_mtime_desc(dlg)
        center_on(dlg, top)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return ""
        files = dlg.selectedFiles()
        return files[0] if files else ""
    path, _ = QFileDialog.getOpenFileName(
        top,
        caption,
        start,
        filter,
        options=_NO_NATIVE,
        **kw,
    )
    return path or ""


def get_open_file_names(
    parent: Optional[QWidget],
    caption: str,
    start: str = "",
    filter: str = "",  # noqa: A002
    **kw,
) -> list[str]:
    """Open a multi-select file-open dialog.

    Returns selected paths, or an empty list if cancelled.
    """
    sort_by_mtime = bool(kw.pop("sort_by_mtime", False))
    top = top_window(parent)
    if sort_by_mtime:
        dlg = QFileDialog(top, caption, start or "")
        dlg.setOption(_NO_NATIVE, True)
        dlg.setFileMode(QFileDialog.FileMode.ExistingFiles)
        if filter:
            dlg.setNameFilter(filter)
        _sort_dialog_by_mtime_desc(dlg)
        center_on(dlg, top)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return []
        return [p for p in dlg.selectedFiles() if p]
    paths, _ = QFileDialog.getOpenFileNames(
        top,
        caption,
        start,
        filter,
        options=_NO_NATIVE,
        **kw,
    )
    return [p for p in paths if p]


def get_save_file_name(
    parent: Optional[QWidget],
    caption: str,
    start: str = "",
    filter: str = "",  # noqa: A002
    **kw,
) -> str:
    """Open a save-file dialog.

    Returns the selected path (str) or an empty string if cancelled.
    """
    top = top_window(parent)
    path, _ = QFileDialog.getSaveFileName(
        top,
        caption,
        start,
        filter,
        options=_NO_NATIVE,
        **kw,
    )
    return path or ""


# ── Message boxes ─────────────────────────────────────────────────────────────

def _message_box(
    parent: Optional[QWidget],
    icon: "QMessageBox.Icon",
    title: str,
    text: str,
    *,
    informative_text: str = "",
    detailed_text: str = "",
    buttons: "QMessageBox.StandardButton" = QMessageBox.StandardButton.Ok,
    default: Optional["QMessageBox.StandardButton"] = None,
) -> "QMessageBox.StandardButton":
    """Show a screen-aware message box with optional copyable diagnostics."""
    box = QMessageBox(top_window(parent))
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    if informative_text:
        box.setInformativeText(informative_text)
    if detailed_text:
        box.setDetailedText(detailed_text)
    box.setStandardButtons(buttons)
    if default is not None:
        box.setDefaultButton(default)
    box.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse
        | Qt.TextInteractionFlag.TextSelectableByKeyboard
    )
    center_on(box, parent)
    return box.exec()


def warn(
    parent: Optional[QWidget],
    title: str,
    text: str,
    informative_text: str = "",
    detailed_text: str = "",
    **kw,
) -> "QMessageBox.StandardButton":
    """Show a warning message box parented on the top-level window."""
    if kw:
        return QMessageBox.warning(top_window(parent), title, text, **kw)
    return _message_box(
        parent,
        QMessageBox.Icon.Warning,
        title,
        text,
        informative_text=informative_text,
        detailed_text=detailed_text,
    )


def info(
    parent: Optional[QWidget],
    title: str,
    text: str,
    informative_text: str = "",
    detailed_text: str = "",
    **kw,
) -> "QMessageBox.StandardButton":
    """Show an information message box parented on the top-level window."""
    if kw:
        return QMessageBox.information(top_window(parent), title, text, **kw)
    return _message_box(
        parent,
        QMessageBox.Icon.Information,
        title,
        text,
        informative_text=informative_text,
        detailed_text=detailed_text,
    )


def question(
    parent: Optional[QWidget],
    title: str,
    text: str,
    buttons: "QMessageBox.StandardButton" = (
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    ),
    default: "QMessageBox.StandardButton" = QMessageBox.StandardButton.No,
    **kw,
) -> "QMessageBox.StandardButton":
    """Show a yes/no question dialog parented on the top-level window."""
    if kw:
        return QMessageBox.question(top_window(parent), title, text, buttons, default, **kw)
    return _message_box(
        parent,
        QMessageBox.Icon.Question,
        title,
        text,
        buttons=buttons,
        default=default,
    )


def critical(
    parent: Optional[QWidget],
    title: str,
    text: str,
    informative_text: str = "",
    detailed_text: str = "",
    **kw,
) -> "QMessageBox.StandardButton":
    """Show a critical error message box parented on the top-level window."""
    if kw:
        return QMessageBox.critical(top_window(parent), title, text, **kw)
    return _message_box(
        parent,
        QMessageBox.Icon.Critical,
        title,
        text,
        informative_text=informative_text,
        detailed_text=detailed_text,
    )


def exception(
    parent: Optional[QWidget],
    title: str,
    exc: BaseException,
    *,
    text: str = "",
    hint: str = "",
) -> "QMessageBox.StandardButton":
    """Show an exception with a concise user message and expandable traceback."""
    message = text or str(exc) or exc.__class__.__name__
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return critical(parent, title, message, informative_text=hint, detailed_text=detail)


def show_status(parent: Optional[QWidget], text: str, timeout_ms: int = 4000) -> None:
    """Show a short status-bar message when the parent window has a status bar."""
    root = top_window(parent)
    status = getattr(root, "statusBar", None)
    if callable(status):
        try:
            status().showMessage(text, timeout_ms)
        except Exception:  # noqa: BLE001
            pass


@contextmanager
def busy_cursor():
    """Temporarily show the busy cursor around a short synchronous operation."""
    app = QApplication.instance() or QGuiApplication.instance()
    if app is None:
        yield
        return
    app.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        yield
    finally:
        app.restoreOverrideCursor()
