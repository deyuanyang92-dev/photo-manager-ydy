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
from pathlib import Path
import re
from typing import Optional

from PyQt6.QtCore import QSortFilterProxyModel, Qt
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

from app.utils import diagnostics


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
_ANCHOR_SCAN_LIMIT = 3000
_ANCHOR_NEAR_SECONDS = 30 * 60


def _file_priority_key(path: str) -> str:
    try:
        return str(Path(path).resolve()).casefold()
    except OSError:
        return str(path or "").casefold()


def _priority_terms_match(name: str, terms: list[str]) -> bool:
    haystack = str(name or "").casefold()
    for term in terms:
        term = str(term or "").strip().casefold()
        if not term:
            continue
        if term in haystack:
            return True
        tokens = [p for p in re.split(r"[-_\s]+", term) if p]
        if tokens and all(token in haystack for token in tokens):
            return True
    return False


class _PriorityFileSortProxy(QSortFilterProxyModel):
    """Keep likely-related files at the top of QFileDialog results."""

    def __init__(
        self,
        priority_paths: list[str],
        priority_terms: list[str] | None = None,
        filter_terms: list[str] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._priority = {
            _file_priority_key(path): index
            for index, path in enumerate(priority_paths)
            if path
        }
        self._priority_terms = [str(t) for t in (priority_terms or []) if str(t or "").strip()]
        self._filter_terms = [str(t) for t in (filter_terms or []) if str(t or "").strip()]
        self._anchor_mtimes_by_dir: dict[str, list[int]] = {}

    def _source_path(self, index) -> str:
        model = self.sourceModel()
        if model is None:
            return ""
        try:
            return str(model.filePath(index))
        except Exception:
            return ""

    def _source_mtime(self, index) -> int:
        model = self.sourceModel()
        if model is None:
            return 0
        try:
            return int(model.fileInfo(index).lastModified().toSecsSinceEpoch())
        except Exception:
            return 0

    def _anchor_mtimes(self, path: str) -> list[int]:
        directory = str(Path(path).parent)
        cached = self._anchor_mtimes_by_dir.get(directory)
        if cached is not None:
            return cached
        anchors: list[int] = []
        terms = list(dict.fromkeys(self._priority_terms + self._filter_terms))
        if terms:
            try:
                for index, child in enumerate(Path(directory).iterdir()):
                    if index >= _ANCHOR_SCAN_LIMIT:
                        break
                    if not child.is_file():
                        continue
                    if child.suffix.lower() not in {".tif", ".tiff"}:
                        continue
                    if _priority_terms_match(child.name, terms):
                        anchors.append(int(child.stat().st_mtime))
            except OSError:
                anchors = []
        self._anchor_mtimes_by_dir[directory] = anchors
        return anchors

    def _near_anchor_seconds(self, path: str, mtime: int) -> int | None:
        anchors = self._anchor_mtimes(path)
        if not anchors or not mtime:
            return None
        return min(abs(mtime - anchor) for anchor in anchors)

    def _is_related_file(self, path: str, mtime: int) -> bool:
        if self._priority.get(_file_priority_key(path)) is not None:
            return True
        if self._filter_terms and _priority_terms_match(Path(path).name, self._filter_terms):
            return True
        near_anchor = self._near_anchor_seconds(path, mtime)
        return near_anchor is not None and near_anchor <= _ANCHOR_NEAR_SECONDS

    def _rank_tuple(self, index) -> tuple[int, int]:
        path = self._source_path(index)
        exact_rank = self._priority.get(_file_priority_key(path))
        if exact_rank is not None:
            return 0, exact_rank
        if self._priority_terms and _priority_terms_match(Path(path).name, self._priority_terms):
            return 1, 0
        mtime = self._source_mtime(index)
        near_anchor = self._near_anchor_seconds(path, mtime)
        if near_anchor is not None:
            return 2, near_anchor
        return 10**9, 0

    def lessThan(self, left, right) -> bool:  # noqa: N802 - Qt override
        left_rank = self._rank_tuple(left)
        right_rank = self._rank_tuple(right)
        if left_rank != right_rank:
            return left_rank < right_rank

        left_mtime = self._source_mtime(left)
        right_mtime = self._source_mtime(right)
        if left_mtime != right_mtime:
            return left_mtime > right_mtime

        return str(left.data() or "").casefold() < str(right.data() or "").casefold()

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:  # noqa: N802 - Qt override
        if not self._filter_terms:
            return True
        model = self.sourceModel()
        if model is None:
            return True
        index = model.index(source_row, 0, source_parent)
        try:
            info = model.fileInfo(index)
            if info.isDir():
                return True
            return self._is_related_file(info.absoluteFilePath(), int(info.lastModified().toSecsSinceEpoch()))
        except Exception:
            return True


def _sort_dialog_by_mtime_desc(
    dialog: QFileDialog,
    priority_paths: list[str] | None = None,
    priority_terms: list[str] | None = None,
    filter_terms: list[str] | None = None,
) -> None:
    """Sort a non-native file dialog, optionally pinning related files first."""
    dialog.setViewMode(QFileDialog.ViewMode.Detail)
    priority_paths = [p for p in (priority_paths or []) if p]
    priority_terms = [t for t in (priority_terms or []) if str(t or "").strip()]
    filter_terms = [t for t in (filter_terms or []) if str(t or "").strip()]
    if priority_paths or priority_terms or filter_terms:
        proxy = _PriorityFileSortProxy(
            priority_paths,
            priority_terms,
            filter_terms,
            dialog,
        )
        dialog.setProxyModel(proxy)
        setattr(dialog, "_priority_sort_proxy", proxy)

    for view in dialog.findChildren(QTreeView) + dialog.findChildren(QListView):
        model = view.model()
        if model is None or model.columnCount() < 4:
            continue
        try:
            view.setSortingEnabled(True)
            if priority_paths or priority_terms or filter_terms:
                view.sortByColumn(0, Qt.SortOrder.AscendingOrder)
                model.sort(0, Qt.SortOrder.AscendingOrder)
            else:
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
    if files:
        return files[0]
    current = dlg.directory()
    return current.absolutePath() if current else ""


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
    priority_paths = list(kw.pop("priority_paths", []) or [])
    priority_terms = list(kw.pop("priority_terms", []) or [])
    filter_terms = list(kw.pop("filter_terms", []) or [])
    top = top_window(parent)
    if sort_by_mtime or priority_paths or priority_terms or filter_terms:
        dlg = QFileDialog(top, caption, start or "")
        dlg.setOption(_NO_NATIVE, True)
        dlg.setFileMode(QFileDialog.FileMode.ExistingFile)
        if filter:
            dlg.setNameFilter(filter)
        _sort_dialog_by_mtime_desc(
            dlg,
            priority_paths=priority_paths,
            priority_terms=priority_terms,
            filter_terms=filter_terms,
        )
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
    priority_paths = list(kw.pop("priority_paths", []) or [])
    priority_terms = list(kw.pop("priority_terms", []) or [])
    filter_terms = list(kw.pop("filter_terms", []) or [])
    top = top_window(parent)
    if sort_by_mtime or priority_paths or priority_terms or filter_terms:
        dlg = QFileDialog(top, caption, start or "")
        dlg.setOption(_NO_NATIVE, True)
        dlg.setFileMode(QFileDialog.FileMode.ExistingFiles)
        if filter:
            dlg.setNameFilter(filter)
        _sort_dialog_by_mtime_desc(
            dlg,
            priority_paths=priority_paths,
            priority_terms=priority_terms,
            filter_terms=filter_terms,
        )
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
    copy_button = None
    if detailed_text:
        copy_payload = diagnostics.format_diagnostic(
            title,
            text,
            detail=detailed_text,
            context={"informative_text": informative_text},
        )
        copy_button = box.addButton("复制详情", QMessageBox.ButtonRole.ActionRole)
        copy_button.clicked.connect(
            lambda _checked=False, payload=copy_payload: QApplication.clipboard().setText(payload)
        )
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
