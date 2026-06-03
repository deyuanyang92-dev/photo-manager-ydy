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
get_save_file_name(parent, caption, start="", filter="", **kw)  → str | None

warn(parent, title, text, **kw)      → QMessageBox.StandardButton
info(parent, title, text, **kw)      → QMessageBox.StandardButton
question(parent, title, text, **kw)  → QMessageBox.StandardButton
critical(parent, title, text, **kw)  → QMessageBox.StandardButton
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import QDialog, QFileDialog, QMessageBox, QWidget


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


def get_existing_directory(
    parent: Optional[QWidget],
    caption: str,
    start: str = "",
) -> str:
    """Open a directory-picker dialog.

    Returns the selected path (str) or an empty string if cancelled.
    Always uses the Qt-native (non-OS-native) picker so Qt controls
    which screen the dialog appears on.
    """
    top = top_window(parent)
    path = QFileDialog.getExistingDirectory(
        top,
        caption,
        start,
        _NO_NATIVE,
    )
    return path or ""


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
    top = top_window(parent)
    path, _ = QFileDialog.getOpenFileName(
        top,
        caption,
        start,
        filter,
        options=_NO_NATIVE,
        **kw,
    )
    return path or ""


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

def warn(
    parent: Optional[QWidget],
    title: str,
    text: str,
    **kw,
) -> "QMessageBox.StandardButton":
    """Show a warning message box parented on the top-level window."""
    return QMessageBox.warning(top_window(parent), title, text, **kw)


def info(
    parent: Optional[QWidget],
    title: str,
    text: str,
    **kw,
) -> "QMessageBox.StandardButton":
    """Show an information message box parented on the top-level window."""
    return QMessageBox.information(top_window(parent), title, text, **kw)


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
    return QMessageBox.question(top_window(parent), title, text, buttons, default, **kw)


def critical(
    parent: Optional[QWidget],
    title: str,
    text: str,
    **kw,
) -> "QMessageBox.StandardButton":
    """Show a critical error message box parented on the top-level window."""
    return QMessageBox.critical(top_window(parent), title, text, **kw)
