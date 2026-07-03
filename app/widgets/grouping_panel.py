"""grouping_panel.py — Specimen grouping editor.

Displays two sections:
  1. **Draft groups** (未合成): editable list — angle label + drag-and-drop
     reorderable JPG list per group.  Groups without a composed TIFF.
  2. **Composed rows** (已合成): read-only summary — composedTiffPath basename,
     📦 Organise button, ↩ Undo-compose button.

Data source: ``grouping_service.load_grouping(db, uid)``

Emits
-----
compose_requested(uid: str, group_index: int)
    User clicked "合成" for a draft group.
organise_requested(uid: str, group_index: int)
    User clicked "📦整理" on a composed row.
undo_compose_requested(uid: str, group_index: int)
    User clicked "↩撤销" on a composed row.
grouping_changed()
    Emitted after any in-memory edit (label rename, JPG removal) so the
    parent view knows a save is needed.
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
import re
from typing import TYPE_CHECKING, Callable, Optional

from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.config import icons
from app.services import grouping_service

if TYPE_CHECKING:
    from app.app_context import AppContext
    from app.services.grouping_service import Group, SpecimenGrouping


# ── Cross-group drag list ─────────────────────────────────────────────────────

_JPG_EXTS = {".jpg", ".jpeg"}
_TIFF_EXTS = {".tif", ".tiff"}
_THUMB_ICON_CACHE_LIMIT = 512
_THUMB_ICON_CACHE: "OrderedDict[tuple[str, int, int], object]" = OrderedDict()
_RELATED_THUMB_CACHE: "OrderedDict[tuple[str, int, int, int], object]" = OrderedDict()
_RELATED_PICKER_NEAR_SECONDS = 30 * 60
_RELATED_PICKER_DEFAULT_CHECK_SECONDS = 3 * 60


def _show_path_in_folder(path: str) -> None:
    """Reveal *path* in the platform file manager."""
    import os
    import subprocess
    import sys

    if not path:
        return
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            return
        try:
            from app.utils.path_utils import wsl_to_windows

            win_path = wsl_to_windows(path) or path
            subprocess.Popen(["explorer.exe", "/select,", win_path])
        except Exception:
            subprocess.Popen(["xdg-open", str(Path(path).parent)])
    except Exception:
        pass


def _archive_jpg_count(zip_path: str | None) -> int | None:
    """Return archived JPG count for both plain-JPG and legacy manifest ZIPs."""
    return grouping_service.archive_jpg_count(zip_path)


def _resolved_archive_zip(group: "Group") -> str | None:
    """Return the registered ZIP or the same-stem ZIP beside the linked TIFF."""
    return grouping_service.resolved_archive_zip(group)


def _result_path_key(path: str | None) -> str:
    return grouping_service.result_path_key(path)


def _clear_group_tiff_link(group: "Group") -> None:
    grouping_service.clear_group_tiff_link(group)


def _deduplicate_tiff_links(groups: list["Group"]) -> bool:
    """Keep one group per TIFF path; clear duplicate associations only."""
    return grouping_service.deduplicate_tiff_links(groups)


def _registered_result_paths(db, *, current_uid: str = "",
                             current_groups: list["Group"] | None = None) -> dict[str, str]:
    """Return result paths already claimed by grouping rows: path-key -> uid."""
    return grouping_service.registered_result_paths(
        db,
        current_uid=current_uid,
        current_groups=current_groups,
    )


def _result_pair_candidates(results_dir: str | Path,
                            used_paths: dict[str, str]) -> list[dict]:
    """Return same-stem TIFF+ZIP pairs from results/ with association state."""
    return grouping_service.result_pair_candidates(results_dir, used_paths)


def _is_composed_group(group: "Group") -> bool:
    """Groups with finished output belong in the composed row workflow."""
    return grouping_service.is_composed_group(group)


def _is_blank_draft_group(group: "Group") -> bool:
    """True for placeholder groups with no user data or result files."""
    return grouping_service.is_blank_draft_group(group)


def _without_blank_draft_groups(groups: list["Group"]) -> list["Group"]:
    return grouping_service.without_blank_draft_groups(groups)


def _uid_core_key(uid: str) -> str:
    """Return the stable survey/site/station key used for fuzzy media matching."""
    return grouping_service.uid_core_key(uid)


def _uid_parts(uid: str) -> list[str]:
    return grouping_service.uid_parts(uid)


def _uid_match_terms(uid: str) -> list[str]:
    return grouping_service.uid_match_terms(uid)


def _uid_matches_name(uid: str, name: str) -> bool:
    return grouping_service.uid_matches_name(uid, name)


def _filename_tokens(path_or_name: str) -> list[str]:
    return grouping_service.filename_tokens(path_or_name)


def _uid_filename_mismatch(uid: str, path_or_name: str) -> bool:
    """True when a media filename visibly belongs to a sibling specimen code."""
    return grouping_service.uid_filename_mismatch(uid, path_or_name)


def _clear_uid_mismatched_result_links(uid: str, groups: list["Group"]) -> bool:
    """Remove result links that visibly belong to another specimen number."""
    return grouping_service.clear_uid_mismatched_result_links(uid, groups)


def _grouping_attribution_ctx(ctx):
    project_dir = getattr(ctx, "current_project_dir", None)
    db = ctx.get_db() if project_dir else None
    if not project_dir or db is None:
        return None
    try:
        from app.services.activation_service import read_activations
        attr = read_activations(project_dir)
    except Exception:
        from app.services.monitor_service import AttributionCtx
        attr = AttributionCtx()
    try:
        attr.explicit_unassigns = grouping_service.get_explicit_unassigns(db)
    except Exception:
        pass
    try:
        import json as _json
        from app.services.monitor_service import _resolved
        rows = db.execute("SELECT uid, jpg_paths FROM grouping").fetchall()
        for row in rows:
            row_uid = row[0] if isinstance(row, (tuple, list)) else row["uid"]
            raw = row[1] if isinstance(row, (tuple, list)) else row["jpg_paths"]
            for path in _json.loads(raw or "[]"):
                if path:
                    attr.path_to_uid[_resolved(path)] = row_uid
    except Exception:
        pass
    return attr


def _related_media_candidates(ctx, uid: str, *,
                              include_jpg: bool = True,
                              include_tiff: bool = True) -> list[dict]:
    """Return JPG/TIFF files that are likely related to *uid*."""
    project_dir = getattr(ctx, "current_project_dir", None)
    db = ctx.get_db() if project_dir else None
    if not project_dir or db is None or not uid:
        return []
    s = getattr(ctx, "settings", None)
    inc = getattr(s, "incoming_subdir", None) if s else None
    res = getattr(s, "results_subdir", None) if s else None
    inc = inc if isinstance(inc, str) and inc else "incoming-jpg"
    res = res if isinstance(res, str) and res else "results"
    try:
        from app.services.monitor_service import scan_project
        scan = scan_project(
            project_dir,
            db,
            incoming_subdir=inc,
            results_subdir=res,
            attr=_grouping_attribution_ctx(ctx),
        )
    except Exception:
        return []

    out: list[dict] = []
    if include_jpg:
        for entry in getattr(scan, "jpg_files", []) or []:
            reason = ""
            if getattr(entry, "attributed_specimen_id", None) == uid:
                reason = "已归属当前编号"
            elif _uid_matches_name(uid, getattr(entry, "name", "")):
                reason = "文件名匹配编号"
            if reason:
                out.append({
                    "kind": "jpg",
                    "path": entry.path,
                    "name": entry.name,
                    "reason": reason,
                    "mtime": entry.mtime,
                })
    if include_tiff:
        for entry in getattr(scan, "tiff_files", []) or []:
            if _uid_matches_name(uid, getattr(entry, "name", "")):
                out.append({
                    "kind": "tiff",
                    "path": entry.path,
                    "name": entry.name,
                    "reason": "文件名匹配编号",
                    "mtime": entry.mtime,
                })
    return sorted(
        out,
        key=lambda c: (
            c.get("kind") != "jpg",
            -float(c.get("mtime") or 0),
            str(c.get("name") or "").casefold(),
        ),
    )


def _mtime_text(ts: float) -> str:
    from app.services.media_discovery_service import mtime_text
    return mtime_text(ts)


def _media_entries_in_dir(root: Path) -> list[dict]:
    """Return JPG/TIF file metadata with one stat call per child."""
    from app.services.media_discovery_service import media_entries_in_dir
    return media_entries_in_dir(root)


def _scan_related_files_in_dir(folder: str | Path, uid: str,
                               *, near_seconds: int = _RELATED_PICKER_NEAR_SECONDS) -> list[dict]:
    """Find matching TIFFs and the JPG time blocks immediately before them."""
    from app.services.media_discovery_service import scan_related_files_in_dir
    return scan_related_files_in_dir(folder, uid, near_seconds=near_seconds)


def _scan_all_media_timeline_in_dir(folder: str | Path, uid: str) -> list[dict]:
    """Return every JPG/TIF in one directory, sorted by modification time."""
    from app.services.media_discovery_service import scan_all_media_timeline_in_dir
    return scan_all_media_timeline_in_dir(folder, uid)


def _scan_jpgs_near_tiff_in_dir(folder: str | Path, tiff_path: str,
                                *, near_seconds: int = _RELATED_PICKER_NEAR_SECONDS) -> list[dict]:
    """Find JPGs in one directory near an already-linked TIFF's timestamp."""
    from app.services.media_discovery_service import scan_jpgs_near_tiff_in_dir
    return scan_jpgs_near_tiff_in_dir(folder, tiff_path, near_seconds=near_seconds)


def _paths_from_mime(event) -> list[str]:
    from app.utils.path_utils import normalize_path

    md = event.mimeData()
    if md is None or not md.hasUrls():
        return []
    paths: list[str] = []
    for url in md.urls():
        if not url.isLocalFile():
            continue
        raw = url.toLocalFile() or url.path()
        if not raw:
            continue
        try:
            p = normalize_path(raw)
        except OSError:
            p = raw
        if Path(p).is_file():
            paths.append(p)
    return paths


def _split_media_paths(paths: list[str]) -> tuple[list[str], list[str]]:
    from app.services.media_discovery_service import split_media_paths
    return split_media_paths(paths)


def _project_incoming_dir(ctx: "AppContext") -> Path | None:
    project_dir = getattr(ctx, "current_project_dir", None)
    if not project_dir:
        return None
    settings = getattr(ctx, "settings", None)
    incoming_subdir = getattr(settings, "incoming_subdir", None) if settings else None
    if not isinstance(incoming_subdir, str) or not incoming_subdir:
        incoming_subdir = "incoming-jpg"
    return Path(project_dir) / incoming_subdir


def _folder_from_picker_selection(selection: str) -> str:
    """Return the directory to scan after the user selects a file or folder."""
    try:
        path = Path(selection)
        if path.is_dir():
            return str(path)
        return str(path.parent)
    except Exception:
        return ""


def _name_matches_any_term(name: str, terms: list[str]) -> bool:
    from app.services.media_discovery_service import name_matches_any_term
    return name_matches_any_term(name, terms)


def _normal_dir(path: str | Path | None) -> Path | None:
    if not path:
        return None
    try:
        p = Path(path).expanduser()
        if p.is_file():
            p = p.parent
        if p.is_dir():
            return p
    except Exception:
        return None
    return None


def _add_dir_shortcut(
    shortcuts: list[tuple[str, str]],
    seen: set[str],
    label: str,
    path: str | Path | None,
) -> None:
    p = _normal_dir(path)
    if p is None:
        return
    key = str(p.resolve()).casefold()
    if key in seen:
        return
    seen.add(key)
    shortcuts.append((label, str(p)))


def _is_broad_scan_root(path: Path) -> bool:
    from app.services.media_discovery_service import is_broad_scan_root
    return is_broad_scan_root(path)


def _find_related_media_dirs(
    roots: list[Path],
    uid: str,
    *,
    max_depth: int = 5,
    max_dirs: int = 1200,
    limit: int = 8,
) -> list[Path]:
    """Find source folders that contain files matching the specimen UID."""
    from app.services.media_discovery_service import find_related_media_dirs
    return find_related_media_dirs(
        roots,
        uid,
        max_depth=max_depth,
        max_dirs=max_dirs,
        limit=limit,
    )


def _media_thumbnail_pixmap(path: str, size: int = 64):
    """Return a bounded thumbnail pixmap for JPG/TIF rows, cached by file stat."""
    try:
        stat = Path(path).stat()
        key = (str(Path(path).resolve()), int(stat.st_mtime_ns), int(stat.st_size), size)
        cached = _RELATED_THUMB_CACHE.get(key)
        if cached is not None:
            _RELATED_THUMB_CACHE.move_to_end(key)
            return cached
    except Exception:
        key = None
    try:
        from app.utils.image_thumbnail import decode_image_thumbnail
        pixmap = decode_image_thumbnail(path, max_size=size * 2)
        if pixmap is None or pixmap.isNull():
            return None
        pixmap = pixmap.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    except Exception:
        return None
    if key is not None:
        _RELATED_THUMB_CACHE[key] = pixmap
        _RELATED_THUMB_CACHE.move_to_end(key)
        while len(_RELATED_THUMB_CACHE) > _THUMB_ICON_CACHE_LIMIT:
            _RELATED_THUMB_CACHE.popitem(last=False)
    return pixmap


class _MediaLocationPickerDialog(QDialog):
    """File/folder browser for directly selecting JPG/TIF or a scan folder."""

    def __init__(
        self,
        title: str,
        start: str = "",
        *,
        priority_terms: list[str] | None = None,
        file_exts: set[str] | None = None,
        shortcuts: list[tuple[str, str]] | None = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._priority_terms = [t for t in (priority_terms or []) if str(t or "").strip()]
        self._file_exts = {e.lower() for e in (file_exts or (_JPG_EXTS | _TIFF_EXTS))}
        self._shortcuts = self._normal_shortcuts(shortcuts or [])
        self._thumb_queue: list[tuple[QLabel, str, str]] = []
        self._selected_paths: list[str] = []
        self._selected_folder = ""
        self._current_dir = self._normal_start_dir(start)

        self.setWindowTitle(title)
        self.setMinimumSize(1040, 640)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("Section")
        root.addWidget(title_lbl)

        path_row = QHBoxLayout()
        self._path_edit = QLineEdit(str(self._current_dir))
        self._path_edit.returnPressed.connect(self._go_to_path_text)
        path_row.addWidget(self._path_edit, stretch=1)
        go_btn = QPushButton("转到")
        go_btn.setObjectName("Ghost")
        go_btn.clicked.connect(self._go_to_path_text)
        path_row.addWidget(go_btn)
        up_btn = QPushButton("上一级")
        up_btn.setObjectName("Ghost")
        up_btn.clicked.connect(self._go_up)
        path_row.addWidget(up_btn)
        use_current = QPushButton("筛选当前目录")
        use_current.setObjectName("Ghost")
        use_current.clicked.connect(self._accept_current_dir)
        path_row.addWidget(use_current)
        root.addLayout(path_row)

        if self._shortcuts:
            shortcut_row = QHBoxLayout()
            shortcut_lbl = QLabel("常用位置")
            shortcut_lbl.setObjectName("Muted")
            shortcut_row.addWidget(shortcut_lbl)
            for label, path in self._shortcuts[:8]:
                btn = QPushButton(label)
                btn.setObjectName("Ghost")
                btn.setToolTip(path)
                btn.clicked.connect(lambda _checked=False, p=path: self._go_to_dir(p))
                shortcut_row.addWidget(btn)
            shortcut_row.addStretch()
            root.addLayout(shortcut_row)

        hint = QLabel("双击文件夹进入；多选 JPG/TIF 后确定会直接加入；进入目标目录后点“筛选当前目录”再按编号筛选。")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["缩略图", "名称", "类型", "修改时间", "大小"])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(74)
        header = self._table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(0, 76)
        self._table.setColumnWidth(1, 520)
        self._table.setColumnWidth(2, 90)
        self._table.setColumnWidth(3, 180)
        self._table.setColumnWidth(4, 90)
        self._table.itemDoubleClicked.connect(self._on_item_activated)
        root.addWidget(self._table, stretch=1)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setText("确定")
        self._buttons.accepted.connect(self._accept_selection)
        self._buttons.rejected.connect(self.reject)
        root.addWidget(self._buttons)

        self._populate()
        QTimer.singleShot(0, self._load_next_thumbnail_batch)

    def selected_folder(self) -> str:
        return self._selected_folder

    def selected_paths(self) -> list[str]:
        return list(self._selected_paths)

    @staticmethod
    def _normal_start_dir(start: str) -> Path:
        try:
            path = Path(start).expanduser() if start else Path.cwd()
            if path.is_file():
                path = path.parent
            if path.is_dir():
                return path
        except Exception:
            pass
        return Path.cwd()

    @staticmethod
    def _normal_shortcuts(shortcuts: list[tuple[str, str]]) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for label, path in shortcuts:
            p = _normal_dir(path)
            if p is None:
                continue
            key = str(p.resolve()).casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append((str(label or p.name or p), str(p)))
        return out

    def _entry_info(self, row: int) -> dict | None:
        item = self._table.item(row, 1)
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return data if isinstance(data, dict) else None

    def _current_info(self) -> dict | None:
        row = self._table.currentRow()
        if row < 0:
            return None
        return self._entry_info(row)

    def _kind_for_file(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in _TIFF_EXTS:
            return "TIF"
        if suffix in _JPG_EXTS:
            return "JPG"
        return suffix.lstrip(".").upper()

    def _thumbnail_label(self, path: Path, *, is_dir: bool) -> QLabel:
        label = QLabel()
        label.setFixedSize(68, 68)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setObjectName("RelatedThumb")
        label.setToolTip(str(path))
        if is_dir:
            label.setText("DIR")
            label.setProperty("hasThumbnail", False)
            return label
        kind = self._kind_for_file(path)
        label.setText(kind)
        label.setProperty("hasThumbnail", False)
        self._thumb_queue.append((label, str(path), kind))
        return label

    def _apply_thumbnail(self, label: QLabel, path: str, kind: str) -> None:
        pixmap = _media_thumbnail_pixmap(path, 64)
        if pixmap is not None and not pixmap.isNull():
            label.setPixmap(pixmap)
            label.setProperty("hasThumbnail", True)
        else:
            label.setText(kind or "IMG")
            label.setProperty("hasThumbnail", False)

    def _load_next_thumbnail_batch(self) -> None:
        processed = 0
        while self._thumb_queue and processed < 4:
            label, path, kind = self._thumb_queue.pop(0)
            self._apply_thumbnail(label, path, kind)
            processed += 1
        if self._thumb_queue:
            QTimer.singleShot(10, self._load_next_thumbnail_batch)

    def _load_all_thumbnails_now(self) -> None:
        while self._thumb_queue:
            label, path, kind = self._thumb_queue.pop(0)
            self._apply_thumbnail(label, path, kind)

    def _selected_infos(self) -> list[dict]:
        rows = sorted({index.row() for index in self._table.selectionModel().selectedRows()})
        if not rows:
            current = self._current_info()
            return [current] if current else []
        infos: list[dict] = []
        for row in rows:
            info = self._entry_info(row)
            if info:
                infos.append(info)
        return infos

    def _populate(self) -> None:
        self._table.setSortingEnabled(False)
        self._thumb_queue.clear()
        self._path_edit.setText(str(self._current_dir))
        entries: list[tuple[bool, bool, float, Path]] = []
        try:
            children = list(self._current_dir.iterdir())
        except OSError:
            children = []
        for child in children:
            is_dir = child.is_dir()
            if not is_dir and child.suffix.lower() not in self._file_exts:
                continue
            try:
                mtime = child.stat().st_mtime
            except OSError:
                mtime = 0
            related = _name_matches_any_term(child.name, self._priority_terms)
            entries.append((is_dir, related, mtime, child))
        entries.sort(
            key=lambda item: (
                not item[0],
                not item[1],
                -float(item[2] or 0),
                item[3].name.casefold(),
            )
        )

        self._table.setRowCount(len(entries))
        for row, (is_dir, _related, mtime, path) in enumerate(entries):
            self._table.setCellWidget(row, 0, self._thumbnail_label(path, is_dir=is_dir))
            name_item = QTableWidgetItem(path.name)
            name_item.setToolTip(str(path))
            name_item.setData(
                Qt.ItemDataRole.UserRole,
                {"path": str(path), "is_dir": is_dir},
            )
            self._table.setItem(row, 1, name_item)

            kind = "文件夹" if is_dir else self._kind_for_file(path)
            self._table.setItem(row, 2, QTableWidgetItem(kind))
            self._table.setItem(row, 3, QTableWidgetItem(_mtime_text(mtime)))

            size = ""
            if not is_dir:
                try:
                    size = f"{path.stat().st_size / 1024 / 1024:.1f} MB"
                except OSError:
                    size = ""
            self._table.setItem(row, 4, QTableWidgetItem(size))
            self._table.setRowHeight(row, 74)

        self._table.clearSelection()
        self._table.setCurrentCell(-1, -1)
        self._table.setSortingEnabled(True)
        self._table.sortItems(3, Qt.SortOrder.DescendingOrder)

    def _go_up(self) -> None:
        parent = self._current_dir.parent
        if parent != self._current_dir and parent.is_dir():
            self._current_dir = parent
            self._populate()

    def _go_to_dir(self, path: str) -> None:
        target = _normal_dir(path)
        if target is None:
            return
        self._current_dir = target
        self._populate()

    def _go_to_path_text(self) -> None:
        self._go_to_dir(self._path_edit.text().strip())

    def _accept_current_dir(self) -> None:
        self._selected_paths = []
        self._selected_folder = str(self._current_dir)
        self.accept()

    def _on_item_activated(self, item: QTableWidgetItem) -> None:
        info = self._entry_info(item.row())
        if not info:
            return
        path = Path(str(info.get("path") or ""))
        if info.get("is_dir") and path.is_dir():
            self._current_dir = path
            self._populate()
            return
        self._selected_paths = [str(path)]
        self._selected_folder = ""
        self.accept()

    def _accept_selection(self) -> None:
        infos = self._selected_infos()
        if not infos:
            self._selected_paths = []
            self._selected_folder = str(self._current_dir)
            self.accept()
            return

        file_paths = [
            str(Path(str(info.get("path") or "")))
            for info in infos
            if not info.get("is_dir")
        ]
        if file_paths:
            self._selected_paths = file_paths
            self._selected_folder = ""
            self.accept()
            return

        path = Path(str(infos[0].get("path") or ""))
        self._selected_paths = []
        self._selected_folder = str(path)
        self.accept()


def _pick_media_paths_or_folder(
    parent: Optional[QWidget],
    caption: str,
    *,
    start: str = "",
    priority_terms: list[str] | None = None,
    file_exts: set[str] | None = None,
    shortcuts: list[tuple[str, str]] | None = None,
) -> tuple[list[str], str]:
    dlg = _MediaLocationPickerDialog(
        caption,
        start=start,
        priority_terms=priority_terms,
        file_exts=file_exts,
        shortcuts=shortcuts,
        parent=parent,
    )
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return [], ""
    return dlg.selected_paths(), dlg.selected_folder()


def _pick_media_location_folder(
    parent: Optional[QWidget],
    caption: str,
    *,
    start: str = "",
    priority_terms: list[str] | None = None,
    file_exts: set[str] | None = None,
    shortcuts: list[tuple[str, str]] | None = None,
) -> str:
    _paths, folder = _pick_media_paths_or_folder(
        parent,
        caption,
        start=start,
        priority_terms=priority_terms,
        file_exts=file_exts,
        shortcuts=shortcuts,
    )
    return folder


def _resolve_path_for_group(p: str) -> Optional[str]:
    """Normalize a path for grouping; return None if file missing."""
    from app.services.helicon_service import resolve_existing_image_path
    return resolve_existing_image_path(p)


class _CrossGroupList(QListWidget):
    """QListWidget that supports cross-group JPG drag-drop.

    When a drop arrives from a *different* list, the dragged item is removed
    from the source list and the parent GroupingPanel._persist_grouping_after_editor_change() is
    called to persist the change.

    Within the same list, items reorder normally (InternalMove behaviour is
    preserved by letting Qt handle it via the base dropEvent).
    """

    def __init__(self, panel: "GroupingPanel", group_index: int,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._panel = panel
        self._group_index = group_index
        self.setDragDropMode(QListWidget.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if isinstance(event.source(), _CrossGroupList):
            event.acceptProposedAction()
            return
        jpgs, tiffs = _split_media_paths(_paths_from_mime(event))
        if jpgs or tiffs:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.dragEnterEvent(event)

    def dropEvent(self, event) -> None:
        if not isinstance(event.source(), _CrossGroupList):
            jpgs, tiffs = _split_media_paths(_paths_from_mime(event))
            if jpgs or tiffs:
                if len(tiffs) > 1:
                    from PyQt6.QtWidgets import QMessageBox
                    QMessageBox.warning(
                        self, "拖入文件", "一次只能拖入 1 个 TIFF。"
                    )
                    event.ignore()
                    return
                self._panel.drop_external_files(
                    self._group_index,
                    jpgs,
                    tiffs[0] if tiffs else None,
                )
                event.acceptProposedAction()
                return

        src = event.source()
        if src is self:
            # Same-list reorder — delegate to Qt's default implementation.
            super().dropEvent(event)
            self._panel._persist_grouping_after_editor_change()
            return

        if not isinstance(src, _CrossGroupList):
            event.ignore()
            return

        # Cross-group move: identify the item being dragged.
        item = src.currentItem()
        if item is None:
            event.ignore()
            return

        jpg_path = item.data(Qt.ItemDataRole.UserRole)
        if not jpg_path:
            event.ignore()
            return

        # Remove from source list widget and source group model.
        src.takeItem(src.row(item))

        # Add to this list widget.
        new_item = QListWidgetItem(item.text())
        new_item.setData(Qt.ItemDataRole.UserRole, jpg_path)
        new_item.setToolTip(jpg_path)
        self.addItem(new_item)

        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()

        # Persist via panel (updates the in-memory model and saves to DB).
        self._panel._move_jpg_between_groups(
            src_group_index=src._group_index,
            dst_group_index=self._group_index,
            jpg_path=jpg_path,
        )


# ── Composed row ──────────────────────────────────────────────────────────────

class _ComposedRow(QFrame):
    """A single row in the "已合成" section."""

    organise_clicked = pyqtSignal(int)   # group_index
    link_jpg_clicked = pyqtSignal(int)   # group_index
    undo_clicked = pyqtSignal(int)       # group_index
    selected_changed = pyqtSignal(int, bool)  # group_index, checked
    register_zip_clicked = pyqtSignal(int)  # group_index
    tiff_naming_check_requested = pyqtSignal(str)  # tiff_path
    tiff_delete_requested = pyqtSignal(int)  # group_index

    def __init__(
        self,
        group: "Group",
        parent: Optional[QWidget] = None,
        *,
        selected: bool = False,
        display_number: Optional[int] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._group = group
        self._selected = selected
        self._display_number = display_number or (group.group_index + 1)
        self._tiff_path = self._group.composed_tiff_path or ""
        self._setup_ui()

    def _setup_ui(self) -> None:
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(10)

        sel = QCheckBox()
        sel.setToolTip("勾选后，顶部合成/整理只处理勾选组；未勾选任何组时处理全部。")
        sel.setChecked(self._selected)
        sel.toggled.connect(
            lambda checked: self.selected_changed.emit(self._group.group_index, checked)
        )
        lay.addWidget(sel)

        # Composed-state chip + angle label
        chip = QLabel(self._group.angle_label or f"组{self._display_number}")
        chip.setObjectName("ChipTiff")
        lay.addWidget(chip)

        # TIFF basename
        tiff_path = self._tiff_path
        tiff_name = Path(tiff_path).name if tiff_path else "(无 TIFF)"
        tiff_lbl = QLabel(tiff_name)
        tiff_lbl.setObjectName("Mono")
        tiff_lbl.setToolTip(tiff_path)
        tiff_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if tiff_path:
            tiff_lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            tiff_lbl.customContextMenuRequested.connect(
                lambda pos, label=tiff_lbl: self._show_tiff_menu(
                    label.mapToGlobal(pos)
                )
            )
        lay.addWidget(tiff_lbl)

        # JPG / archive state badge.  A composed row can have only a TIFF when
        # the user imported a finished TIFF but has not linked the original JPGs.
        jpg_count = len(self._group.jpg_paths)
        archive_zip = _resolved_archive_zip(self._group)
        archived_count = _archive_jpg_count(archive_zip)
        if archive_zip:
            count_text = (
                f"已归档 {archived_count} JPG"
                if archived_count is not None
                else "已归档 ZIP"
            )
        elif jpg_count:
            count_text = f"{jpg_count} JPG 待整理"
        elif tiff_path:
            count_text = "仅TIF 待整理"
        else:
            count_text = "未关联 JPG"
        count_lbl = QLabel(count_text)
        count_lbl.setObjectName("MutedSmall")
        count_lbl.setToolTip(
            "此组已关联原始 JPG，可整理归档。"
            if jpg_count
            else (
                "已注册 ZIP 归档。"
                if archive_zip
                else "此组只有 TIF，可先整理登记到编号；需要 ZIP 时再关联 JPG。"
            )
        )
        lay.addWidget(count_lbl)

        # Main action button
        needs_jpg_link = bool(tiff_path) and not jpg_count and not archive_zip
        org_btn = QPushButton(
            "已整理" if archive_zip else ("整理TIF" if needs_jpg_link else "整理")
        )
        org_btn.setObjectName("Primary")
        org_btn.setFixedHeight(28)
        icons.set_button_icon(
            org_btn,
            "mdi6.file-image-outline" if needs_jpg_link else "mdi6.folder-zip-outline",
            color=icons.TONE_ON_ACCENT,
            size=14,
        )
        org_btn.setEnabled((bool(jpg_count) and not bool(archive_zip)) or needs_jpg_link)
        org_btn.setToolTip(
            "仅整理登记 TIFF 到当前编号；不生成 ZIP"
            if needs_jpg_link
            else (
                "归档 JPG → ZIP，按设置删除 JPG"
                if jpg_count and not archive_zip
                else (
                    "本组已有 ZIP 归档。"
                    if archive_zip
                    else "无法整理：此组未关联 JPG 原片。"
                )
            )
        )
        org_btn.clicked.connect(lambda: self.organise_clicked.emit(self._group.group_index))
        lay.addWidget(org_btn)

        if needs_jpg_link:
            link_btn = QPushButton("关联JPG")
            link_btn.setObjectName("Ghost")
            link_btn.setFixedHeight(28)
            icons.set_button_icon(
                link_btn, "mdi6.image-plus-outline", color=icons.TONE_MUTED, size=15
            )
            link_btn.setToolTip("按此 TIFF 时间，从原片目录选择并关联 JPG")
            link_btn.clicked.connect(
                lambda: self.link_jpg_clicked.emit(self._group.group_index)
            )
            lay.addWidget(link_btn)

        zip_btn = QPushButton("换ZIP" if archive_zip else "注册ZIP")
        zip_btn.setObjectName("Ghost")
        zip_btn.setFixedHeight(28)
        icons.set_button_icon(zip_btn, "mdi6.folder-zip-outline",
                              color=icons.TONE_MUTED, size=15)
        zip_btn.setToolTip(
            "更换已注册 ZIP 归档（不重新压缩）"
            if archive_zip
            else "注册已有 ZIP 归档（不重新压缩）"
        )
        zip_btn.clicked.connect(
            lambda: self.register_zip_clicked.emit(self._group.group_index)
        )
        lay.addWidget(zip_btn)

        # Undo button
        undo_btn = QPushButton()
        undo_btn.setObjectName("Ghost")
        undo_btn.setFixedSize(30, 28)
        icons.set_button_icon(undo_btn, "mdi6.undo-variant", color=icons.TONE_MUTED, size=15)
        undo_btn.setToolTip("撤销合成：确认后删除 TIFF，并把 JPG 放回自由池")
        undo_btn.clicked.connect(lambda: self.undo_clicked.emit(self._group.group_index))
        lay.addWidget(undo_btn)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        if self._tiff_path:
            self._show_tiff_menu(event.globalPos())
            event.accept()
            return
        super().contextMenuEvent(event)

    def _show_tiff_menu(self, global_pos) -> None:
        path = self._tiff_path
        if not path:
            return
        menu = QMenu(self)
        check_action = menu.addAction("检查 TIF 命名格式")
        copy_action = menu.addAction("复制路径")
        show_action = menu.addAction("在文件夹中显示")
        menu.addSeparator()
        delete_action = menu.addAction("删除 TIF")

        chosen = menu.exec(global_pos)
        if chosen == check_action:
            self.tiff_naming_check_requested.emit(path)
        elif chosen == copy_action:
            QApplication.clipboard().setText(path)
        elif chosen == show_action:
            _show_path_in_folder(path)
        elif chosen == delete_action:
            self.tiff_delete_requested.emit(self._group.group_index)


# ── Draft group ───────────────────────────────────────────────────────────────

class _ThumbAreaResizeHandle(QFrame):
    """Drag vertically to resize the thumbnail grid inside a group card."""

    def __init__(
        self,
        target: QListWidget,
        *,
        min_h: int = 88,
        max_h: int = 320,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._target = target
        self._min_h = min_h
        self._max_h = max_h
        self._drag_y = 0
        self._start_h = 0
        self.setObjectName("ThumbResizeGrip")
        self.setFixedHeight(8)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setToolTip("上下拖动，调整缩略图区域高度")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_y = int(event.globalPosition().y())
            self._start_h = self._target.height()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() & Qt.MouseButton.LeftButton:
            delta = int(event.globalPosition().y()) - self._drag_y
            h = max(self._min_h, min(self._max_h, self._start_h + delta))
            self._target.setFixedHeight(h)
            event.accept()
        else:
            super().mouseMoveEvent(event)


class _DraftGroupRow(QFrame):
    """Editable draft group card (angle label + drag-reorderable JPG list)."""

    compose_clicked = pyqtSignal(int)         # group_index
    organise_clicked = pyqtSignal(int)        # group_index — 已关联 TIF 时整理
    label_changed = pyqtSignal(int, str)      # group_index, new_label
    jpg_removed = pyqtSignal(int, str)        # group_index, jpg_path (kept for compat)
    add_selected_to_group = pyqtSignal(int)   # group_index
    jpg_remove_requested = pyqtSignal(int, str)  # group_index, jpg_path
    clear_group_requested = pyqtSignal(int)   # group_index  #cursor
    delete_group_requested = pyqtSignal(int)  # group_index  #cursor
    import_tiff_requested = pyqtSignal(int)   # group_index  #cursor groupingImportTiff
    add_photos_requested = pyqtSignal(int)    # group_index — 从文件夹选图加入
    output_name_changed = pyqtSignal(int, str)  # group_index, 用户编辑的输出命名
    selected_changed = pyqtSignal(int, bool)  # group_index, checked
    tiff_naming_check_requested = pyqtSignal(str)  # tiff_path
    tiff_delete_requested = pyqtSignal(int)  # group_index

    def __init__(self, group: "Group", parent: Optional[QWidget] = None,
                 panel: Optional["GroupingPanel"] = None,
                 selected: bool = False,
                 display_number: Optional[int] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self._group = group
        self._panel = panel
        self._selected = selected
        self._display_number = display_number or (group.group_index + 1)
        self._setup_ui()

    # 横向胶片条：每组一个固定宽度的窄竖卡片，卡内 = 角度名 / JPG缩略图网格 /
    # 张数+输出名 / [合成] / 小动作按钮。多组并排横向滚动，一眼看完所有角度组。
    CARD_W = 234

    def _setup_ui(self) -> None:
        self.setFixedWidth(self.CARD_W)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Row1: 组chip + 角度label
        top = QHBoxLayout()
        top.setSpacing(6)
        sel = QCheckBox()
        sel.setToolTip("勾选后，顶部合成/整理只处理勾选组；未勾选任何组时处理全部。")
        sel.setChecked(self._selected)
        sel.toggled.connect(
            lambda checked: self.selected_changed.emit(self._group.group_index, checked)
        )
        top.addWidget(sel)
        chip = QLabel(f"组{self._display_number}")
        chip.setObjectName(f"GroupChip{(self._display_number - 1) % 4}")
        self._group_number_chip = chip
        top.addWidget(chip)
        self._label_edit = QLineEdit(self._group.angle_label or "")
        self._label_edit.setPlaceholderText("角度")
        self._label_edit.setFixedHeight(26)
        self._label_edit.textEdited.connect(
            lambda t: self.label_changed.emit(self._group.group_index, t)
        )
        top.addWidget(self._label_edit, 1)
        root.addLayout(top)

        # JPG 缩略图网格（IconMode，支持拖拽排序 + 组间拖动）
        self._jpg_list = _CrossGroupList(
            panel=self._panel,
            group_index=self._group.group_index,
            parent=self,
        )
        # Dashed drop-zone look when empty, hairline card when filled (theme QSS).
        has_media = bool(self._group.jpg_paths or self._group.composed_tiff_path)
        self._jpg_list.setObjectName(
            "GroupDropZoneEmpty" if not has_media else "GroupDropZone"
        )
        self._jpg_list.setViewMode(QListWidget.ViewMode.IconMode)
        self._jpg_list.setIconSize(QSize(50, 50))
        self._jpg_list.setGridSize(QSize(58, 60))
        self._jpg_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._jpg_list.setMovement(QListWidget.Movement.Snap)
        self._jpg_list.setWrapping(True)
        self._jpg_list.setSpacing(3)
        self._jpg_list.setUniformItemSizes(True)
        self._jpg_list.setFixedHeight(124)  # 2 行 50px 缩略图；不 stretch —— 否则把下方按钮挤出视口
        self._jpg_list.setToolTip(
            "从监控区或文件夹拖入 JPG/TIF；组内可排序；右键移除；可组间拖动"
        )
        for p in self._group.jpg_paths:
            item = QListWidgetItem(self._thumb_icon(p), "")
            item.setData(Qt.ItemDataRole.UserRole, p)
            item.setData(Qt.ItemDataRole.UserRole + 1, "jpg")
            item.setToolTip(Path(p).name)
            self._jpg_list.addItem(item)
        tiff_path = self._group.composed_tiff_path or ""
        if tiff_path:
            tiff_item = QListWidgetItem(self._tiff_icon(), Path(tiff_path).name)
            tiff_item.setData(Qt.ItemDataRole.UserRole, tiff_path)
            tiff_item.setData(Qt.ItemDataRole.UserRole + 1, "tiff")
            tiff_item.setToolTip(tiff_path)
            tiff_item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._jpg_list.addItem(tiff_item)
        if not self._group.jpg_paths and not tiff_path:
            empty = QListWidgetItem("空组\n+ / 拖入\nJPG+TIF")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            empty.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._jpg_list.addItem(empty)
        self._jpg_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._jpg_list.customContextMenuRequested.connect(self._on_jpg_context_menu)
        root.addWidget(self._jpg_list)
        root.addWidget(_ThumbAreaResizeHandle(self._jpg_list, parent=self))

        if tiff_path:
            tiff_name_lbl = QLabel(Path(tiff_path).name)
            tiff_name_lbl.setObjectName("Mono")
            tiff_name_lbl.setWordWrap(True)
            tiff_name_lbl.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            tiff_name_lbl.setToolTip(tiff_path)
            tiff_name_lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            tiff_name_lbl.customContextMenuRequested.connect(
                lambda pos, label=tiff_name_lbl: self._show_tiff_menu(
                    tiff_path, label.mapToGlobal(pos)
                )
            )
            root.addWidget(tiff_name_lbl)

        # 张数 + 输出名（分两行，避免 TIF 命名被挤没）
        meta = QHBoxLayout()
        meta.setSpacing(4)
        if tiff_path and not self._group.jpg_paths:
            count_text = "TIF 已关联"
        elif tiff_path:
            count_text = f"{len(self._group.jpg_paths)} JPG · TIF"
        else:
            count_text = f"{len(self._group.jpg_paths)} 张"
        count_lbl = QLabel(count_text)
        count_lbl.setObjectName("MutedSmall")
        meta.addWidget(count_lbl)
        meta.addStretch()
        root.addLayout(meta)

        out_row = QHBoxLayout()
        out_row.setSpacing(4)
        out_lbl = QLabel("输出")
        out_lbl.setObjectName("MutedSmall")
        out_row.addWidget(out_lbl)
        self._output_edit = QLineEdit(self._effective_output_name())
        self._output_edit.setPlaceholderText("自动")
        self._output_edit.setFixedHeight(24)
        self._output_edit.setToolTip(
            "本组输出文件名（不含路径与扩展名）。\n"
            "留空 = 自动：有编号→编号-序号；临时分组→组序(1/2…)；导入TIF→原名。"
        )
        self._output_edit.textEdited.connect(
            lambda t: self.output_name_changed.emit(self._group.group_index, t)
        )
        out_row.addWidget(self._output_edit, 1)
        root.addLayout(out_row)

        if tiff_path:
            action_btn = QPushButton("整理")
            action_btn.setObjectName("Primary")
            action_btn.setFixedHeight(28)
            icons.set_button_icon(action_btn, "mdi6.folder-zip-outline",
                                  color=icons.TONE_ON_ACCENT, size=12)
            action_btn.setToolTip(
                f"已关联 TIFF：{Path(tiff_path).name}\n"
                "打包 JPG → 与 TIF 同名的 ZIP"
            )
            action_btn.clicked.connect(
                lambda: self.organise_clicked.emit(self._group.group_index)
            )
        else:
            action_btn = QPushButton("合成")
            action_btn.setObjectName("Primary")
            action_btn.setFixedHeight(28)
            icons.set_button_icon(action_btn, "mdi6.layers-triple-outline",
                                  color=icons.TONE_ON_ACCENT, size=12)
            action_btn.setToolTip("调用 Helicon Focus 合成该组 JPG")
            action_btn.clicked.connect(
                lambda: self.compose_clicked.emit(self._group.group_index)
            )
        root.addWidget(action_btn)

        # 小动作行：添加照片 / 加入所选 / 导入TIF / 清空 / 删组
        actions = QHBoxLayout()
        actions.setSpacing(4)
        add_photos_btn = QPushButton()
        add_photos_btn.setObjectName("Ghost")
        add_photos_btn.setFixedSize(24, 24)
        icons.set_button_icon(add_photos_btn, "mdi6.plus",
                              color=icons.TONE_MUTED, size=14)
        add_photos_btn.setToolTip("从文件夹选择 JPG/TIF 加入此组（TIF → 输出/ZIP 名）")
        add_photos_btn.clicked.connect(
            lambda: self.add_photos_requested.emit(self._group.group_index)
        )
        actions.addWidget(add_photos_btn)
        add_sel_btn = QPushButton("← 加入所选")
        add_sel_btn.setObjectName("Ghost")
        add_sel_btn.setFixedHeight(24)
        add_sel_btn.setToolTip(
            "将监控区选中的 JPG/TIF 加入此分组（TIF 基础名 → 输出/ZIP 名）"
        )
        add_sel_btn.clicked.connect(
            lambda: self.add_selected_to_group.emit(self._group.group_index)
        )
        actions.addWidget(add_sel_btn, 1)
        import_tiff_btn = QPushButton()
        import_tiff_btn.setObjectName("Ghost")
        import_tiff_btn.setFixedSize(24, 24)
        icons.set_button_icon(import_tiff_btn, "mdi6.file-import-outline",
                              color=icons.TONE_MUTED, size=12)
        import_tiff_btn.setToolTip("导入已有 TIFF 关联到本组（跳过 Helicon 直接整理）")
        import_tiff_btn.clicked.connect(
            lambda: self.import_tiff_requested.emit(self._group.group_index)
        )
        actions.addWidget(import_tiff_btn)
        clear_btn = QPushButton()
        clear_btn.setObjectName("Ghost")
        clear_btn.setFixedSize(24, 24)
        icons.set_button_icon(clear_btn, "mdi6.eraser", color=icons.TONE_MUTED, size=12)
        clear_btn.setToolTip("清空此组所有 JPG（不删除文件）")
        clear_btn.clicked.connect(lambda: self.clear_group_requested.emit(self._group.group_index))
        actions.addWidget(clear_btn)
        del_btn = QPushButton()
        del_btn.setObjectName("Ghost")
        del_btn.setFixedSize(24, 24)
        icons.set_button_icon(del_btn, "mdi6.delete-outline", color=icons.TONE_DANGER, size=12)
        del_btn.setToolTip("删除此分组（仅删记录，不删文件）")
        del_btn.clicked.connect(lambda: self.delete_group_requested.emit(self._group.group_index))
        actions.addWidget(del_btn)
        root.addLayout(actions)

    def _thumb_icon(self, path: str):
        """生成 JPG 缩略图 QIcon（QImageReader 按比例缩放解码，快且不爆内存）。
        失败/非图片 → 通用图片图标占位。"""
        from PyQt6.QtGui import QImageReader, QIcon, QPixmap
        try:
            stat = Path(path).stat()
            key = (str(Path(path).resolve()), int(stat.st_mtime_ns), int(stat.st_size))
            cached = _THUMB_ICON_CACHE.get(key)
            if cached is not None:
                _THUMB_ICON_CACHE.move_to_end(key)
                return cached
        except Exception:
            key = None
        try:
            r = QImageReader(path)
            r.setAutoTransform(True)
            sz = r.size()
            if sz.isValid() and sz.width() > 0 and sz.height() > 0:
                sz.scale(58, 58, Qt.AspectRatioMode.KeepAspectRatio)
                r.setScaledSize(sz)
            img = r.read()
            if not img.isNull():
                icon = QIcon(QPixmap.fromImage(img))
                if key is not None:
                    _THUMB_ICON_CACHE[key] = icon
                    _THUMB_ICON_CACHE.move_to_end(key)
                    while len(_THUMB_ICON_CACHE) > _THUMB_ICON_CACHE_LIMIT:
                        _THUMB_ICON_CACHE.popitem(last=False)
                return icon
        except Exception:
            pass
        return icons.icon("mdi6.image-outline")

    def _tiff_icon(self):
        """TIFF 关联项占位图标（组内显示绿色 TIF 标记）。"""
        return icons.icon("mdi6.file-image-outline", color="#36c98f")

    def _effective_output_name(self) -> str:
        """当前应显示的输出名：用户覆盖 > 已合成TIF名 > 临时分组默认组序 > 空。"""
        g = self._group
        if g.output_name:
            return g.output_name
        if g.composed_tiff_path:
            return Path(g.composed_tiff_path).stem
        # 临时分组(无编号)：默认显示 组序(1/2/…)，让用户看到不填就用这个。
        from app.services.grouping_service import ADHOC_GROUPING_UID
        panel_uid = getattr(self._panel, "_uid", None) if self._panel else None
        if panel_uid == ADHOC_GROUPING_UID:
            return str(g.group_index + 1)
        return ""


    def _on_jpg_context_menu(self, pos) -> None:
        """Right-click context menu on a media item."""
        item = self._jpg_list.itemAt(pos)
        if not item:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        kind = str(item.data(Qt.ItemDataRole.UserRole + 1) or "jpg")
        if kind == "tiff":
            self._show_tiff_menu(path, self._jpg_list.mapToGlobal(pos))
            return
        menu = QMenu(self)
        action = menu.addAction("移除此 JPG")
        chosen = menu.exec(self._jpg_list.mapToGlobal(pos))
        if chosen == action:
            self.jpg_remove_requested.emit(self._group.group_index, path)

    def _show_tiff_menu(self, path: str, global_pos) -> None:
        if not path:
            return
        menu = QMenu(self)
        check_action = menu.addAction("检查 TIF 命名格式")
        copy_action = menu.addAction("复制路径")
        show_action = menu.addAction("在文件夹中显示")
        menu.addSeparator()
        delete_action = menu.addAction("删除 TIF")

        chosen = menu.exec(global_pos)
        if chosen == check_action:
            self.tiff_naming_check_requested.emit(path)
        elif chosen == copy_action:
            QApplication.clipboard().setText(path)
        elif chosen == show_action:
            _show_path_in_folder(path)
        elif chosen == delete_action:
            self.tiff_delete_requested.emit(self._group.group_index)


# ── Grouping panel ────────────────────────────────────────────────────────────

class _SuppDropButton(QPushButton):
    """Drop-aware button for 补处理 (拖入所选 JPG + TIFF 补处理).

    Click → caller consumes the monitor selection. OS drag-drop of files →
    ``files_dropped`` carries the dropped local paths directly. Always enabled
    once a project is open — independent of the active-specimen gate.
    """

    files_dropped = pyqtSignal(list)  # list[str] of local file paths

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        md = event.mimeData()
        if md is not None and md.hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        md = event.mimeData()
        if md is not None and md.hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
        from app.utils.path_utils import normalize_path

        md = event.mimeData()
        if md is None or not md.hasUrls():
            event.ignore()
            return
        paths = [
            normalize_path(u.toLocalFile())
            for u in md.urls()
            if u.isLocalFile() and u.toLocalFile()
        ]
        paths = [p for p in paths if Path(p).is_file()]
        event.acceptProposedAction()
        if paths:
            self.files_dropped.emit(paths)


class _AutoGroupDropZone(QFrame):
    """Staging area: drag JPG/TIF here, then click 自动分组整理."""

    files_dropped = pyqtSignal(list)

    def __init__(self, panel: "GroupingPanel", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._panel = panel
        self.setAcceptDrops(True)
        self.setMinimumHeight(140)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(8)
        self._hint = QLabel(
            "拖入 JPG / TIF 到此处，然后点「自动分组整理」\n"
            "WSL 下 Windows 拖入常无效，请用下方「添加文件 / 文件夹」\n"
            "也可直接点「自动分组整理」选择来源"
        )
        self._hint.setObjectName("Muted")
        self._hint.setWordWrap(True)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._hint)
        self._count_lbl = QLabel("")
        self._count_lbl.setObjectName("Mono")
        self._count_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._count_lbl)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._add_files_btn = QPushButton("添加文件…")
        self._add_files_btn.setObjectName("Outline")
        self._add_files_btn.setFixedHeight(26)
        self._add_files_btn.clicked.connect(self._pick_files)
        btn_row.addWidget(self._add_files_btn)
        self._add_folder_btn = QPushButton("添加文件夹…")
        self._add_folder_btn.setObjectName("Outline")
        self._add_folder_btn.setFixedHeight(26)
        self._add_folder_btn.clicked.connect(self._pick_folder)
        btn_row.addWidget(self._add_folder_btn)
        self._clear_btn = QPushButton("清空暂存")
        self._clear_btn.setObjectName("Ghost")
        self._clear_btn.setFixedHeight(26)
        self._clear_btn.clicked.connect(panel.clear_auto_group_staging)
        self._clear_btn.hide()
        btn_row.addWidget(self._clear_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

    def set_staged_count(self, count: int, paths: list[str] | None = None) -> None:
        if count > 0:
            names = ", ".join(Path(p).name for p in (paths or [])[:8])
            if count > 8:
                names += f" …等 {count} 个"
            self._count_lbl.setText(f"已暂存 {count} 个文件：{names}")
            self._clear_btn.show()
        else:
            self._count_lbl.setText("")
            self._clear_btn.hide()

    def _picker_start_dir(self) -> str:
        project_dir = getattr(self._panel.ctx, "current_project_dir", None)
        if project_dir and Path(project_dir).is_dir():
            return str(project_dir)
        return str(Path.home())

    def _pick_files(self) -> None:
        from app.utils import ui

        paths = ui.get_open_file_names(
            self.window(),
            "选择 JPG / TIF 文件",
            start=self._picker_start_dir(),
            filter="图片 (*.jpg *.jpeg *.tif *.tiff);;所有文件 (*.*)",
            sort_by_mtime=True,
        )
        if paths:
            self.files_dropped.emit(paths)

    def _pick_folder(self) -> None:
        from app.utils import ui

        folder = ui.get_existing_directory(
            self.window(),
            "选择含 JPG / TIF 的文件夹",
            start=self._picker_start_dir(),
        )
        if not folder:
            return
        paths = [
            str(p) for p in Path(folder).iterdir()
            if p.is_file() and p.suffix.lower() in (_JPG_EXTS | _TIFF_EXTS)
        ]
        if paths:
            self.files_dropped.emit(paths)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if _paths_from_mime(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        self.dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = _paths_from_mime(event)
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self.files_dropped.emit(paths)


class GroupingPanel(QWidget):
    """Full grouping editor: draft groups above, composed rows below.

    Signals
    -------
    compose_requested(uid, group_index)
    organise_requested(uid, group_index)
    undo_compose_requested(uid, group_index)
    grouping_changed()
    """

    compose_requested = pyqtSignal(str, int)
    organise_requested = pyqtSignal(str, int)
    undo_compose_requested = pyqtSignal(str, int)
    grouping_changed = pyqtSignal()
    # Bulk-action signals (capture-main-actions row)
    compose_all_requested = pyqtSignal(str)    # uid — compose all pending groups
    compose_and_organise_all_requested = pyqtSignal(str)  # uid — 合成全部 + 逐组整理
    organise_all_requested = pyqtSignal(str)   # uid — organise all composed groups
    # Add-to-group / free-compose / retroactive signals
    add_selection_to_group_requested = pyqtSignal(int)  # group_index
    free_compose_requested = pyqtSignal()
    retroactive_requested = pyqtSignal()
    auto_group_organize_requested = pyqtSignal()
    tiff_naming_check_requested = pyqtSignal()
    tiff_naming_check_path_requested = pyqtSignal(str)
    helicon_params_requested = pyqtSignal()
    import_tiff_requested = pyqtSignal(str, int)  # uid, group_index  #cursor groupingImportTiff
    archive_zip_registered = pyqtSignal(str, int)  # uid, group_index
    # 补处理 (supplementary archival) — independent of the active-specimen gate.
    supp_process_requested = pyqtSignal()       # click → consume monitor selection
    supp_files_dropped = pyqtSignal(list)       # OS drop → list[str] of local paths

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._uid: Optional[str] = None
        self._grouping: Optional["SpecimenGrouping"] = None
        self._render_signature: tuple | None = None
        self._selected_group_indexes: set[int] = set()
        self._auto_group_staged_paths: list[str] = []
        self._auto_group_preview_result: Optional[dict] = None
        self._setup_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        section = QFrame()
        section.setObjectName("WorkbenchSection")
        outer.addWidget(section)
        from app.config.effects import apply_card_shadow
        apply_card_shadow(section)

        root = QVBoxLayout(section)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(14)

        # ── capture-main-actions row (web parity: ⚡合成/🗜整理/合成+整理/⋯更多) ──
        # Hidden when no specimen active (mirrors app.js:7374-7378 early return)
        self._toolbar_widget = QWidget()
        main_actions = QHBoxLayout(self._toolbar_widget)
        main_actions.setContentsMargins(0, 0, 0, 0)
        main_actions.setSpacing(8)

        self._select_all_btn = QPushButton("全选组")
        self._select_all_btn.setObjectName("Ghost")
        self._select_all_btn.setFixedHeight(30)
        self._select_all_btn.setToolTip("勾选所有分组；顶部合成/整理只处理勾选组")
        self._select_all_btn.clicked.connect(self.select_all_groups)
        main_actions.addWidget(self._select_all_btn)

        self._clear_selection_btn = QPushButton("清除选择")
        self._clear_selection_btn.setObjectName("Ghost")
        self._clear_selection_btn.setFixedHeight(30)
        self._clear_selection_btn.setToolTip("清除分组勾选；未勾选任何组时顶部操作处理全部")
        self._clear_selection_btn.clicked.connect(self.clear_group_selection)
        main_actions.addWidget(self._clear_selection_btn)

        compose_btn = QPushButton("合成")
        compose_btn.setObjectName("Primary")
        compose_btn.setFixedHeight(30)
        icons.set_button_icon(compose_btn, "mdi6.layers-triple-outline",
                              color=icons.TONE_ON_ACCENT, size=14)
        compose_btn.setToolTip("对所有待合成组调用 Helicon Focus")
        compose_btn.clicked.connect(self._request_compose_all_groups)
        main_actions.addWidget(compose_btn)

        org_btn = QPushButton("整理")
        org_btn.setObjectName("Outline")
        org_btn.setFixedHeight(30)
        icons.set_button_icon(org_btn, "mdi6.folder-zip-outline",
                              color=icons.TONE_ACCENT, size=14)
        org_btn.setToolTip("整理所有已合成组（归档 JPG）")
        org_btn.clicked.connect(self._request_organise_all_groups)
        main_actions.addWidget(org_btn)

        compose_org_btn = QPushButton("合成+整理")
        compose_org_btn.setObjectName("Primary")
        compose_org_btn.setFixedHeight(30)
        icons.set_button_icon(compose_org_btn, "mdi6.archive-check-outline",
                              color=icons.TONE_ON_ACCENT, size=14)
        compose_org_btn.setToolTip("合成后立即整理归档（一条龙）")
        compose_org_btn.clicked.connect(self._request_compose_and_organise_all_groups)
        main_actions.addWidget(compose_org_btn)

        self._auto_group_btn = QPushButton("自动分组整理")
        self._auto_group_btn.setObjectName("Outline")
        self._auto_group_btn.setFixedHeight(30)
        icons.set_button_icon(
            self._auto_group_btn,
            "mdi6.auto-fix",
            color=icons.TONE_ACCENT,
            size=14,
        )
        self._auto_group_btn.setToolTip(
            "第一步：扫描并预览分组（不打包）；"
            "核对无误后再点同一按钮执行整理归档"
        )
        self._auto_group_btn.clicked.connect(
            self.auto_group_organize_requested.emit
        )
        main_actions.addWidget(self._auto_group_btn)

        self._tiff_naming_check_btn = QPushButton("检查 TIF")
        self._tiff_naming_check_btn.setObjectName("Outline")
        self._tiff_naming_check_btn.setFixedHeight(30)
        icons.set_button_icon(
            self._tiff_naming_check_btn,
            "mdi6.file-search-outline",
            color=icons.TONE_ACCENT,
            size=14,
        )
        self._tiff_naming_check_btn.setToolTip(
            "检查勾选/当前 TIF 命名是否符合项目规则；"
            "当前无 TIF 时可选择目录批量检查；可导出 CSV"
        )
        self._tiff_naming_check_btn.clicked.connect(
            self.tiff_naming_check_requested.emit
        )
        main_actions.addWidget(self._tiff_naming_check_btn)

        self._related_first_btn = QPushButton("相关优先")
        self._related_first_btn.setObjectName("Outline")
        self._related_first_btn.setCheckable(True)
        self._related_first_btn.setFixedHeight(30)
        icons.set_button_icon(
            self._related_first_btn,
            "mdi6.sort-variant",
            color=icons.TONE_ACCENT,
            size=14,
        )
        self._related_first_btn.setToolTip(
            "用于每组「+」选图：开启后，当前编号匹配的 TIF 及其时间附近 JPG 优先显示；"
            "关闭时按普通修改时间排序"
        )
        self._related_first_btn.toggled.connect(self._on_related_first_toggled)
        main_actions.addWidget(self._related_first_btn)

        self._related_filter_btn = QPushButton("筛相关")
        self._related_filter_btn.setObjectName("Outline")
        self._related_filter_btn.setCheckable(True)
        self._related_filter_btn.setFixedHeight(30)
        icons.set_button_icon(
            self._related_filter_btn,
            "mdi6.filter-outline",
            color=icons.TONE_ACCENT,
            size=14,
        )
        self._related_filter_btn.setToolTip(
            "用于每组「+」选图：先选目录，再打开相关文件列表；"
            "只列当前编号匹配 TIF 及其时间附近 JPG/TIF"
        )
        self._related_filter_btn.toggled.connect(self._on_related_filter_toggled)
        main_actions.addWidget(self._related_filter_btn)

        more_btn = QPushButton("⋯ 更多 ▾")
        more_btn.setObjectName("Ghost")
        more_btn.setFixedHeight(30)
        more_btn.setToolTip("更多操作")
        more_btn.setMenu(self._build_more_menu())
        main_actions.addWidget(more_btn)

        main_actions.addStretch()

        self._add_btn = QPushButton("新组")
        self._add_btn.setObjectName("Outline")
        self._add_btn.setFixedHeight(30)
        icons.set_button_icon(self._add_btn, "mdi6.plus", color=icons.TONE_ACCENT, size=14)
        self._add_btn.clicked.connect(self._add_group)
        self._add_btn.hide()
        main_actions.addWidget(self._add_btn)

        self._import_pending_btn = QPushButton("导入待整理")
        self._import_pending_btn.setObjectName("Outline")
        self._import_pending_btn.setFixedHeight(30)
        icons.set_button_icon(
            self._import_pending_btn,
            "mdi6.image-plus-outline",
            color=icons.TONE_ACCENT,
            size=14,
        )
        self._import_pending_btn.setToolTip("选择 JPG，新建一个待整理分组")
        self._import_pending_btn.clicked.connect(self._on_import_pending_group)
        self._import_pending_btn.hide()
        main_actions.addWidget(self._import_pending_btn)

        self._link_result_pair_btn = QPushButton("关联成品")
        self._link_result_pair_btn.setObjectName("Outline")
        self._link_result_pair_btn.setFixedHeight(30)
        icons.set_button_icon(
            self._link_result_pair_btn,
            "mdi6.link-variant",
            color=icons.TONE_ACCENT,
            size=14,
        )
        self._link_result_pair_btn.setToolTip("选择已有 TIF + ZIP，登记到当前编号")
        self._link_result_pair_btn.clicked.connect(self._on_link_result_pair)
        self._link_result_pair_btn.hide()
        main_actions.addWidget(self._link_result_pair_btn)

        self._target_label = QLabel("—")
        self._target_label.setObjectName("Mono")
        self._target_label.setToolTip("当前目标标本编号")
        self._target_label.hide()
        root.addWidget(self._toolbar_widget)
        self._toolbar_widget.hide()

        # ── ▸ 分组工具 collapsible header (popup hides whole row — redundant) ──
        self._group_header_widget = QWidget()
        group_toggle_row = QHBoxLayout(self._group_header_widget)
        group_toggle_row.setContentsMargins(0, 0, 0, 0)
        group_toggle_row.setSpacing(6)
        self._group_toggle_btn = QPushButton("▸ 分组工具")
        self._group_toggle_btn.setObjectName("Ghost")
        self._group_toggle_btn.setFixedHeight(26)
        self._group_toggle_btn.setCheckable(True)
        self._group_toggle_btn.setChecked(True)
        self._group_toggle_btn.clicked.connect(self._set_group_editor_expanded)
        group_toggle_row.addWidget(self._group_toggle_btn)
        self._supp_btn = _SuppDropButton("拖入所选 JPG + TIFF 补处理")
        self._supp_btn.setObjectName("Ghost")
        self._supp_btn.setFixedHeight(26)
        self._supp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._supp_btn.setToolTip(
            "在监控区勾选 JPG 原片 + TIFF 成片后点击，或直接把文件拖到此处。\n"
            "无需激活标本——标本身份从 TIFF 文件名识别。"
        )
        self._supp_btn.clicked.connect(self.supp_process_requested.emit)
        self._supp_btn.files_dropped.connect(self.supp_files_dropped.emit)
        self._supp_btn.hide()
        group_toggle_row.addStretch()

        self._uid_label = QLabel("未选择标本")
        self._uid_label.setObjectName("Mono")
        self._uid_label.hide()

        root.addWidget(self._group_header_widget)

        self._header_divider = QFrame()
        self._header_divider.setObjectName("Divider")
        self._header_divider.setFixedHeight(1)
        root.addWidget(self._header_divider)

        # Collapsible body
        self._group_body = QWidget()
        body_lay = QVBoxLayout(self._group_body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(8)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._content = QWidget()
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.setContentsMargins(0, 4, 0, 4)
        self._content_lay.setSpacing(8)
        self._content_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._content)
        body_lay.addWidget(scroll, stretch=1)

        self._auto_group_drop = _AutoGroupDropZone(self)
        self._auto_group_drop.files_dropped.connect(self._on_auto_group_files_added)
        body_lay.addWidget(self._auto_group_drop)

        self._auto_group_preview_host = QFrame()
        self._auto_group_preview_host.setObjectName("Panel")
        preview_lay = QVBoxLayout(self._auto_group_preview_host)
        preview_lay.setContentsMargins(12, 10, 12, 10)
        preview_lay.setSpacing(6)
        self._auto_group_preview_title = QLabel("自动分组预览（尚未整理）")
        self._auto_group_preview_title.setObjectName("Section")
        preview_lay.addWidget(self._auto_group_preview_title)
        self._auto_group_preview_body = QLabel("")
        self._auto_group_preview_body.setObjectName("Muted")
        self._auto_group_preview_body.setWordWrap(True)
        preview_lay.addWidget(self._auto_group_preview_body)
        preview_btn_row = QHBoxLayout()
        preview_btn_row.addStretch()
        self._clear_preview_btn = QPushButton("清除预览")
        self._clear_preview_btn.setObjectName("Ghost")
        self._clear_preview_btn.setFixedHeight(26)
        self._clear_preview_btn.clicked.connect(self.clear_auto_group_preview)
        preview_btn_row.addWidget(self._clear_preview_btn)
        preview_btn_row.addStretch()
        preview_lay.addLayout(preview_btn_row)
        self._auto_group_preview_host.hide()
        body_lay.addWidget(self._auto_group_preview_host)

        root.addWidget(self._group_body, stretch=1)

    # ── Public API ────────────────────────────────────────────────────────────

    def load_grouping(self, uid: str, grouping: "SpecimenGrouping") -> None:
        """Display all groups for *uid*."""
        self._uid = uid
        self._grouping = grouping
        mismatches_removed = _clear_uid_mismatched_result_links(uid, self._grouping.groups)
        if mismatches_removed:
            self._grouping.groups = _without_blank_draft_groups(self._grouping.groups)
        duplicates_removed = _deduplicate_tiff_links(self._grouping.groups)
        self._renumber_default_angle_labels()
        if mismatches_removed or duplicates_removed:
            self._persist_grouping()
        valid = {g.group_index for g in grouping.groups}
        self._selected_group_indexes &= valid
        short = uid[:30] + ("…" if len(uid) > 30 else "")
        self._uid_label.setText(short)
        self._target_label.setText(short)
        self._toolbar_widget.show()
        self._add_btn.show()
        self._import_pending_btn.show()
        self._link_result_pair_btn.show()
        signature = self._grouping_render_signature(uid, grouping)
        if signature == self._render_signature:
            self._refresh_auto_group_drop_visibility()
            return
        self._render_signature = signature
        self._rebuild()

    def clear(self) -> None:
        self._uid = None
        self._grouping = None
        self._render_signature = None
        self._selected_group_indexes.clear()
        self._uid_label.setText("— 未选择标本 —")
        self._target_label.setText("—")
        self._toolbar_widget.hide()
        self._add_btn.hide()
        self._import_pending_btn.hide()
        self._link_result_pair_btn.hide()
        self._clear_content()
        self._refresh_auto_group_drop_visibility()

    def staged_auto_group_paths(self) -> list[str]:
        return list(self._auto_group_staged_paths)

    def has_auto_group_preview(self) -> bool:
        return self._auto_group_preview_result is not None

    def auto_group_preview_result(self) -> Optional[dict]:
        return self._auto_group_preview_result

    def _on_auto_group_files_added(self, paths: list[str]) -> None:
        self.clear_auto_group_preview()
        self.add_auto_group_staged(paths)

    def add_auto_group_staged(self, paths: list[str]) -> None:
        from app.utils.path_utils import normalize_path

        seen = set(self._auto_group_staged_paths)
        for raw in paths:
            ext = Path(raw).suffix.lower()
            if ext not in _JPG_EXTS | _TIFF_EXTS:
                continue
            try:
                resolved = normalize_path(raw)
            except OSError:
                resolved = str(raw)
            if resolved in seen or not Path(resolved).is_file():
                continue
            seen.add(resolved)
            self._auto_group_staged_paths.append(resolved)
        self._refresh_auto_group_drop_visibility()

    def show_auto_group_preview(self, result: dict) -> None:
        self._auto_group_preview_result = result
        lines: list[str] = []
        total_groups = 0
        for sp in result.get("specimens", []):
            uid = sp.get("uid", "")
            for g in sp.get("groups", []):
                total_groups += 1
                jpg_names = ", ".join(Path(p).name for p in g.get("jpgPaths", [])[:6])
                if len(g.get("jpgPaths", [])) > 6:
                    jpg_names += " …"
                lines.append(
                    f"• {uid} / 成果 #{g.get('seq')} / {g.get('tiffName', '')}\n"
                    f"  ← {g.get('jpgCount', 0)} 张：{jpg_names or '（无原片）'}"
                )
        unnamed = result.get("unnamedTiffs") or []
        if unnamed:
            lines.append(f"⚠ {len(unnamed)} 个 TIF 无法识别编号（预览中未纳入整理）")
        unassigned = result.get("unassignedJpgs") or []
        if unassigned:
            lines.append(f"⚠ {len(unassigned)} 张 JPG 未配到 TIF")
        if not lines:
            lines.append("（没有识别到可整理的分组）")
        self._auto_group_preview_body.setText("\n\n".join(lines))
        self._auto_group_preview_title.setText(
            f"自动分组预览（尚未整理）— 共 {total_groups} 组"
        )
        self._auto_group_preview_host.show()
        self._sync_auto_group_action_button()

    def clear_auto_group_preview(self) -> None:
        if self._auto_group_preview_result is None:
            return
        self._auto_group_preview_result = None
        self._auto_group_preview_body.clear()
        self._auto_group_preview_host.hide()
        self._sync_auto_group_action_button()

    def _sync_auto_group_action_button(self) -> None:
        if self._auto_group_preview_result is not None:
            self._auto_group_btn.setText("执行整理归档")
        else:
            self._auto_group_btn.setText("自动分组整理")

    def clear_auto_group_staging(self) -> None:
        if not self._auto_group_staged_paths:
            return
        self._auto_group_staged_paths.clear()
        self.clear_auto_group_preview()
        self._refresh_auto_group_drop_visibility()

    def _refresh_auto_group_drop_visibility(self) -> None:
        has_groups = bool(self._grouping and self._grouping.groups)
        count = len(self._auto_group_staged_paths)
        has_preview = self._auto_group_preview_result is not None
        show = (not has_groups) or count > 0 or has_preview
        self._auto_group_drop.setVisible(show)
        self._auto_group_drop.set_staged_count(count, self._auto_group_staged_paths)
        if has_preview:
            self._auto_group_preview_host.show()

    def selected_group_indexes(self) -> list[int]:
        """Return checked group indexes. Empty means bulk actions should use all."""
        return sorted(self._selected_group_indexes)

    def select_all_groups(self) -> None:
        """Check every current group."""
        if not self._grouping:
            return
        self._selected_group_indexes = {g.group_index for g in self._grouping.groups}
        self._rebuild()

    def clear_group_selection(self) -> None:
        """Clear all group checks; bulk actions fall back to all groups."""
        if not self._selected_group_indexes:
            return
        self._selected_group_indexes.clear()
        self._rebuild()

    def add_jpgs_to_group(self, group_index: int, jpg_paths: list[str]) -> None:
        """Add *jpg_paths* to the group at *group_index* (mutual exclusion).

        Removes paths from all other groups first, then appends (no duplicates).
        Mirrors web groupingAddSelectedToGroup() app.js:5258–5271.
        """
        if not self._grouping:
            return
        # P1: remove paths from all other groups (mutual exclusion)
        for g in self._grouping.groups:
            if g.group_index != group_index:
                g.jpg_paths = [p for p in g.jpg_paths if p not in jpg_paths]
        # P2: add to target group (no duplicates)
        target = next((g for g in self._grouping.groups if g.group_index == group_index), None)
        if target is None:
            return
        for p in jpg_paths:
            if p not in target.jpg_paths:
                target.jpg_paths.append(p)
        self._rebuild()
        self.grouping_changed.emit()

    def drop_external_files(
        self,
        group_index: int,
        jpg_paths: list[str],
        tiff_path: Optional[str] = None,
    ) -> None:
        """监控区/文件夹拖入：JPG 进组；单个 TIF → 关联成片，output_name = TIF 基础名。"""
        if not self._grouping:
            return
        target = next(
            (g for g in self._grouping.groups if g.group_index == group_index),
            None,
        )
        if target is None:
            return

        if jpg_paths:
            incoming_dir = _project_incoming_dir(self.ctx)
            if incoming_dir is not None:
                from app.services.photo_import_service import import_jpgs_to_incoming
                result = import_jpgs_to_incoming(list(jpg_paths), incoming_dir)
                if result.errors:
                    from PyQt6.QtWidgets import QMessageBox
                    QMessageBox.warning(
                        self, "拖入文件部分失败", "\n".join(result.errors[:5])
                    )
                jpg_paths = result.imported_paths
            jpg_paths = [r for p in jpg_paths if (r := _resolve_path_for_group(p))]
        if tiff_path:
            tiff_resolved = _resolve_path_for_group(tiff_path)
            if not tiff_resolved:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "拖入文件", f"找不到 TIFF：\n{tiff_path}")
                return
            tiff_path = tiff_resolved

        tiff_ok = True
        if tiff_path:
            tiff_ok = self._associate_tiff_with_group(target, tiff_path)

        if jpg_paths:
            self.add_jpgs_to_group(group_index, jpg_paths)
        elif tiff_ok and tiff_path:
            self._rebuild()
            self._persist_grouping_after_editor_change()

        if tiff_ok and tiff_path and self._uid:
            self.import_tiff_requested.emit(self._uid, group_index)

    def _result_file_matches_current_uid(self, path: str, title: str) -> bool:
        if not self._uid or not _uid_filename_mismatch(self._uid, path):
            return True
        QMessageBox.warning(
            self,
            title,
            "文件名编号和当前标本不一致，已拒绝关联。\n\n"
            f"当前编号：{_uid_core_key(self._uid)}\n"
            f"选择文件：{Path(path).name}",
        )
        return False

    def _associate_tiff_with_group(self, target: "Group", tiff_path: str) -> bool:
        """Bind TIFF to *target*; output_name = TIF stem (ZIP 整理同名)."""
        if not self._result_file_matches_current_uid(tiff_path, "关联 TIFF"):
            return False
        new_key = _result_path_key(tiff_path)
        if self._grouping and new_key:
            owner = next(
                (
                    g for g in self._grouping.groups
                    if g is not target
                    and _result_path_key(getattr(g, "composed_tiff_path", None)) == new_key
                ),
                None,
            )
            if owner is not None:
                QMessageBox.warning(
                    self,
                    "TIFF 已关联",
                    f"这个 TIFF 已经在 {owner.angle_label or f'组{owner.group_index + 1}'} 中。\n"
                    "同一个 TIFF 不能重复关联到多个角度。",
                )
                return False
        if target.composed_tiff_path and target.composed_tiff_path != tiff_path:
            reply = QMessageBox.question(
                self,
                "替换 TIFF？",
                f"本组已有 TIFF：{Path(target.composed_tiff_path).name}\n\n"
                f"是否替换为：{Path(tiff_path).name}？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return False

        from datetime import datetime, timezone
        target.composed_tiff_path = tiff_path
        target.output_name = Path(tiff_path).stem
        target.status = "composed"
        target.source = target.source or "external-tif"
        target.updated_at = datetime.now(tz=timezone.utc).isoformat()
        return True

    def remove_jpg_from_group(self, group_index: int, jpg_path: str) -> None:
        """Remove *jpg_path* from the specified group.

        Mirrors web groupingRemoveFile() app.js:5274–5280.
        """
        if not self._grouping:
            return
        for g in self._grouping.groups:
            if g.group_index == group_index:
                g.jpg_paths = [p for p in g.jpg_paths if p != jpg_path]
                break
        self._rebuild()
        self.grouping_changed.emit()

    def clear_group(self, group_index: int) -> None:
        """Clear all JPGs from *group_index* (does not delete files).

        Mirrors web groupingClearGroup() app.js:5291–5297.
        """
        if not self._grouping:
            return
        for g in self._grouping.groups:
            if g.group_index == group_index:
                g.jpg_paths = []
                break
        self._rebuild()
        self.grouping_changed.emit()

    def delete_group(self, group_index: int) -> None:
        """Delete the group entirely (in-memory only, no file deletion).

        Mirrors web groupingDeleteGroup() app.js:5283–5289.
        If a TIFF is associated, only the grouping record is removed; the
        image file remains on disk. Use undo-compose for deliberate TIFF
        deletion.
        """
        if not self._grouping:
            return
        target = next((g for g in self._grouping.groups if g.group_index == group_index), None)
        if target is None:
            return
        self._grouping.groups = [g for g in self._grouping.groups if g.group_index != group_index]
        self._renumber_default_angle_labels()
        self._selected_group_indexes.discard(group_index)
        self._rebuild()
        self.grouping_changed.emit()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _move_jpg_between_groups(
        self, src_group_index: int, dst_group_index: int, jpg_path: str
    ) -> None:
        """Move *jpg_path* from *src_group_index* to *dst_group_index* in the
        in-memory model, then persist and emit grouping_changed.

        Called by _CrossGroupList.dropEvent on a cross-group drop.
        """
        if not self._grouping:
            return
        for g in self._grouping.groups:
            if g.group_index == src_group_index:
                g.jpg_paths = [p for p in g.jpg_paths if p != jpg_path]
            elif g.group_index == dst_group_index:
                if jpg_path not in g.jpg_paths:
                    g.jpg_paths.append(jpg_path)
        self._persist_grouping_after_editor_change()

    def _persist_grouping_after_editor_change(self) -> None:
        """Persist the current in-memory grouping to DB and emit grouping_changed."""
        if not self._grouping or not self._uid:
            return
        if _clear_uid_mismatched_result_links(self._uid, self._grouping.groups):
            self._grouping.groups = _without_blank_draft_groups(self._grouping.groups)
        _deduplicate_tiff_links(self._grouping.groups)
        self._persist_grouping()
        self.grouping_changed.emit()

    def _persist_grouping(self) -> None:
        if not self._grouping or not self._uid:
            return
        db = self.ctx.get_db()
        if db is not None:
            grouping_service.save_grouping(
                db, self._uid, self._grouping.groups, clean_phantoms=False
            )

    def _build_more_menu(self):
        """Build the ⋯ 更多 dropdown menu."""
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        free_act = menu.addAction("无号合成（选中 JPG → incoming-jpg/）")
        free_act.triggered.connect(self.free_compose_requested.emit)
        retro_act = menu.addAction("存量整理…")
        retro_act.triggered.connect(self.retroactive_requested.emit)
        menu.addSeparator()
        helicon_act = menu.addAction("Helicon 合成参数")
        helicon_act.triggered.connect(self.helicon_params_requested.emit)
        return menu

    def _default_group_label_prefix(self) -> str:
        """Default group label reflects whether this is a specimen or ad-hoc job."""
        from app.services.grouping_service import ADHOC_GROUPING_UID

        return "结果" if self._uid == ADHOC_GROUPING_UID else "角度"

    def _renumber_default_angle_labels(self) -> None:
        """只重排系统默认标签；用户自定义角度名保持不变。"""
        if not self._grouping:
            return
        prefix = self._default_group_label_prefix()
        for display_number, group in enumerate(self._grouping.groups, start=1):
            label = (group.angle_label or "").strip()
            if re.fullmatch(r"(角度|结果)\d+", label):
                group.angle_label = f"{prefix}{display_number}"

    def _grouping_render_signature(self, uid: str, grouping: "SpecimenGrouping") -> tuple:
        """Small immutable snapshot of fields that affect rendered group cards."""
        return (
            uid,
            tuple(
                (
                    g.group_index,
                    g.angle_label or "",
                    tuple(g.jpg_paths),
                    g.composed_tiff_path or "",
                    getattr(g, "archive_zip", None) or "",
                    getattr(g, "status", None) or "",
                    getattr(g, "output_name", None) or "",
                )
                for g in grouping.groups
            ),
            tuple(sorted(self._selected_group_indexes)),
        )

    def _rebuild(self) -> None:
        self._clear_content()
        if not self._grouping or not self._uid:
            self._refresh_auto_group_drop_visibility()
            return

        groups = self._grouping.groups
        draft = [
            g for g in groups
            if not _is_composed_group(g)
        ]
        composed = [
            g for g in groups
            if _is_composed_group(g)
        ]
        display_numbers = {id(g): n for n, g in enumerate(groups, start=1)}

        if draft:
            # 横向胶片条：草稿卡片并排，横向滚动；多角度组也不挤。
            hscroll = QScrollArea()
            hscroll.setObjectName("GroupStrip")
            hscroll.setWidgetResizable(True)
            hscroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            hscroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            hscroll.setFixedHeight(286)  # 一张卡片(≈266)+横向滚动条，下方按钮不被裁
            strip = QWidget()
            strip_lay = QHBoxLayout(strip)
            strip_lay.setContentsMargins(0, 0, 0, 0)
            strip_lay.setSpacing(10)
            strip_lay.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            for g in draft:
                row = _DraftGroupRow(
                    g,
                    strip,
                    panel=self,
                    selected=g.group_index in self._selected_group_indexes,
                    display_number=display_numbers[id(g)],
                )
                row.compose_clicked.connect(self._request_compose_group)
                row.organise_clicked.connect(self._request_organise_group)
                row.label_changed.connect(self._rename_group_label)
                row.add_selected_to_group.connect(self._request_add_current_selection_to_group)
                row.jpg_remove_requested.connect(self._remove_jpg_from_draft_group)
                row.clear_group_requested.connect(self._clear_draft_group)      # #cursor
                row.delete_group_requested.connect(self._delete_draft_group)    # #cursor
                row.import_tiff_requested.connect(self._import_existing_tiff_into_group)      # #cursor
                row.add_photos_requested.connect(self._on_add_photos_from_picker)
                row.output_name_changed.connect(self._rename_group_output_stem)
                row.selected_changed.connect(self._track_group_selection_state)
                row.tiff_naming_check_requested.connect(
                    self.tiff_naming_check_path_requested.emit
                )
                row.tiff_delete_requested.connect(self._request_delete_group_tiff)
                strip_lay.addWidget(row)
            hscroll.setWidget(strip)
            self._content_lay.addWidget(hscroll)

        if composed:
            sep = QFrame()
            sep.setObjectName("Divider")
            sep.setFixedHeight(1)
            self._content_lay.addWidget(sep)
            sec_lbl2 = QLabel("已合成")
            sec_lbl2.setObjectName("Section")
            self._content_lay.addWidget(sec_lbl2)
            for g in composed:
                row2 = _ComposedRow(
                    g,
                    self,
                    selected=g.group_index in self._selected_group_indexes,
                    display_number=display_numbers[id(g)],
                )
                row2.organise_clicked.connect(self._request_organise_group)
                row2.link_jpg_clicked.connect(self._link_original_jpgs_to_composed_group)
                row2.undo_clicked.connect(self._request_undo_group_compose)
                row2.selected_changed.connect(self._track_group_selection_state)
                row2.register_zip_clicked.connect(self._register_existing_archive_zip)
                row2.tiff_naming_check_requested.connect(
                    self.tiff_naming_check_path_requested.emit
                )
                row2.tiff_delete_requested.connect(self._request_delete_group_tiff)
                self._content_lay.addWidget(row2)

        if not groups:
            no_lbl = QLabel("此标本暂无分组 — 点「+ 新组」创建")
            no_lbl.setObjectName("Muted")
            self._content_lay.addWidget(no_lbl)

        self._refresh_auto_group_drop_visibility()

    def _clear_content(self) -> None:
        while self._content_lay.count():
            item = self._content_lay.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

    def _add_group(self) -> None:
        """新增一个空草稿组；编号标本用角度N，无编号任务用结果N。"""
        if self._append_group() is None:
            return
        self._render_signature = None
        self._rebuild()
        self.grouping_changed.emit()

    def _append_group(self, *, jpg_paths: Optional[list[str]] = None) -> Optional[int]:
        """Append a draft group and return its group index."""
        if not self._grouping or not self._uid:
            return None
        from app.services.grouping_service import Group

        new_index = max((g.group_index for g in self._grouping.groups), default=-1) + 1
        display_number = len(self._grouping.groups) + 1
        prefix = self._default_group_label_prefix()
        self._grouping.groups.append(
            Group(
                group_index=new_index,
                angle_label=f"{prefix}{display_number}",
                jpg_paths=list(jpg_paths or []),
            )
        )
        return new_index

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _set_group_editor_expanded(self, checked: bool) -> None:
        """Show/hide the group editor body; update toggle button label."""
        self._group_body.setVisible(checked)
        self._group_toggle_btn.setText("▾ 分组工具" if checked else "▸ 分组工具")

    def _request_compose_all_groups(self) -> None:
        """[⚡合成] 批量:发单信号,由 workbench 驱动顺序队列(异步合成需串行,
        不能在面板里紧循环 emit——会同时启动多个 HeliconWorker 互相覆盖)。"""
        if not self._uid:
            return
        self.compose_all_requested.emit(self._uid)

    def _request_organise_all_groups(self) -> None:
        """[🗜整理] 批量:发单信号,workbench 逐组同步整理已合成组。"""
        if not self._uid:
            return
        self.organise_all_requested.emit(self._uid)

    def _request_compose_and_organise_all_groups(self) -> None:
        """[合成+整理] 批量:发单信号,workbench 顺序队列——每组合成完成(异步回调)
        后再同步整理该组,然后下一组。旧紧循环 emit 在合成完成前就读 composed →
        刚合成的组整理空跑,故移除。"""
        if not self._uid:
            return
        self.compose_and_organise_all_requested.emit(self._uid)

    def _request_compose_group(self, group_index: int) -> None:
        if self._uid:
            self.compose_requested.emit(self._uid, group_index)

    def _request_organise_group(self, group_index: int) -> None:
        if self._uid:
            self.organise_requested.emit(self._uid, group_index)

    def _request_undo_group_compose(self, group_index: int) -> None:
        if self._uid:
            self.undo_compose_requested.emit(self._uid, group_index)

    def _request_delete_group_tiff(self, group_index: int) -> None:
        self._request_undo_group_compose(group_index)

    def _rename_group_label(self, group_index: int, new_label: str) -> None:
        if not self._grouping:
            return
        for g in self._grouping.groups:
            if g.group_index == group_index:
                g.angle_label = new_label
                break
        self.grouping_changed.emit()

    def _rename_group_output_stem(self, group_index: int, name: str) -> None:
        """用户编辑某组「输出 TIF」命名 → 写入 group.output_name（空=回到自动派生）。"""
        if not self._grouping:
            return
        for g in self._grouping.groups:
            if g.group_index == group_index:
                g.output_name = name.strip() or None
                break
        self.grouping_changed.emit()

    def _track_group_selection_state(self, group_index: int, checked: bool) -> None:
        if checked:
            self._selected_group_indexes.add(group_index)
        else:
            self._selected_group_indexes.discard(group_index)

    def _request_add_current_selection_to_group(self, group_index: int) -> None:
        """Request workbench view to resolve monitor selection and add to group."""
        self.add_selection_to_group_requested.emit(group_index)

    def _remove_jpg_from_draft_group(self, group_index: int, jpg_path: str) -> None:
        """Handle right-click remove from _DraftGroupRow."""
        self.remove_jpg_from_group(group_index, jpg_path)

    def _link_original_jpgs_to_composed_group(self, group_index: int) -> None:
        """Link original JPGs to an existing TIFF-only composed row."""
        if not self._grouping:
            return
        target = next(
            (g for g in self._grouping.groups if g.group_index == group_index),
            None,
        )
        if target is None or not target.composed_tiff_path:
            return
        start = ""
        try:
            start = str(Path(target.composed_tiff_path).parent)
        except Exception:
            start = ""
        jpgs = self._pick_jpgs_for_existing_tiff(target, start=start)
        if not jpgs:
            return
        self.add_jpgs_to_group(group_index, jpgs)

    def _clear_draft_group(self, group_index: int) -> None:  # #cursor
        """Handle clear-group button from _DraftGroupRow."""
        self.clear_group(group_index)

    def _delete_draft_group(self, group_index: int) -> None:  # #cursor
        """Handle delete-group button from _DraftGroupRow."""
        self.delete_group(group_index)

    def _sync_toggle_button_state(self, button: QPushButton, base_text: str) -> None:
        checked = button.isChecked()
        button.setText(f"{base_text}:开" if checked else base_text)
        button.setObjectName("Primary" if checked else "Outline")
        button.style().unpolish(button)
        button.style().polish(button)

    def _on_related_first_toggled(self, checked: bool) -> None:
        self._sync_toggle_button_state(self._related_first_btn, "相关优先")

    def _on_related_filter_toggled(self, checked: bool) -> None:
        if checked and not self._related_first_btn.isChecked():
            self._related_first_btn.setChecked(True)
        self._sync_toggle_button_state(self._related_filter_btn, "筛相关")

    def _group_by_index(self, group_index: int) -> Optional["Group"]:
        if not self._grouping:
            return None
        return next(
            (g for g in self._grouping.groups if g.group_index == group_index),
            None,
        )

    def _start_dir_for_group_picker(self, group_index: int, fallback: str = "") -> str:
        group = self._group_by_index(group_index)
        if group is None:
            return fallback
        media_paths = list(getattr(group, "jpg_paths", None) or [])
        tiff_path = getattr(group, "composed_tiff_path", None)
        if tiff_path:
            media_paths.insert(0, tiff_path)
        for path in media_paths:
            try:
                parent = Path(path).parent
            except Exception:
                continue
            if parent.is_dir():
                return str(parent)
        return fallback

    def _media_location_shortcuts(
        self,
        *,
        start: str = "",
        uid: str = "",
    ) -> list[tuple[str, str]]:
        shortcuts: list[tuple[str, str]] = []
        seen: set[str] = set()
        project_dir = _normal_dir(getattr(self.ctx, "current_project_dir", None))
        settings = getattr(self.ctx, "settings", None)
        incoming_subdir = getattr(settings, "incoming_subdir", None) if settings else None
        results_subdir = getattr(settings, "results_subdir", None) if settings else None
        incoming_subdir = incoming_subdir if isinstance(incoming_subdir, str) and incoming_subdir else "incoming-jpg"
        results_subdir = results_subdir if isinstance(results_subdir, str) and results_subdir else "results"

        start_dir = _normal_dir(start)
        sibling_dsc = project_dir.parent / "dsc" if project_dir is not None else None
        _add_dir_shortcut(shortcuts, seen, "当前起点", start_dir)
        _add_dir_shortcut(shortcuts, seen, "相机原图", sibling_dsc)
        if project_dir is not None:
            _add_dir_shortcut(shortcuts, seen, "incoming-jpg", project_dir / incoming_subdir)
            _add_dir_shortcut(shortcuts, seen, "results", project_dir / results_subdir)
            _add_dir_shortcut(shortcuts, seen, "工作目录", project_dir)
        return shortcuts

    def _preferred_media_location_start(self, start: str, shortcuts: list[tuple[str, str]]) -> str:
        start_dir = _normal_dir(start)
        if start_dir is not None and not _is_broad_scan_root(start_dir):
            return str(start_dir)
        for _label, path in shortcuts:
            p = _normal_dir(path)
            if p is not None:
                return str(p)
        return start

    def _on_add_photos_from_picker(self, group_index: int) -> None:
        """「+」从文件夹多选 JPG/TIF 加入指定组。"""
        if not self._grouping:
            return

        start = ""
        project_dir = getattr(self.ctx, "current_project_dir", None)
        if project_dir:
            pd = Path(project_dir)
            candidates: list[Path] = []
            try:
                from app.services.project_service import resolve_incoming_jpg_dir
                candidates.append(Path(resolve_incoming_jpg_dir(str(pd))))
            except Exception:
                pass
            s = getattr(self.ctx, "settings", None)
            inc = getattr(s, "incoming_subdir", None) if s else None
            if isinstance(inc, str) and inc:
                candidates.append(pd / inc)
            candidates.append(pd / "incoming-jpg")
            candidates.append(pd / "新拍JPG")
            candidates.append(pd)
            for candidate in candidates:
                if candidate.is_dir():
                    start = str(candidate)
                    break

        from app.utils.ui import get_open_file_names
        related_first = bool(getattr(self, "_related_first_btn", None)
                             and self._related_first_btn.isChecked())
        filter_related = bool(
            self._uid
            and getattr(self, "_related_filter_btn", None)
            and self._related_filter_btn.isChecked()
        )
        if filter_related:
            related_uid = str(self._uid or "").strip()
            paths = self._pick_related_files_from_dir(
                uid=related_uid,
                start=self._start_dir_for_group_picker(group_index, start),
            )
            if paths is None:
                return
            if paths:
                self._add_selected_media_paths_to_group(group_index, paths)
                return
            filter_related = False

        priority_paths = []
        priority_terms = []
        filter_terms = []
        if related_first or filter_related:
            priority_terms = [self._uid] if self._uid else []
        if filter_related:
            filter_terms = [self._uid] if self._uid else []
        caption = "选择 JPG / TIFF 加入分组"
        if filter_related:
            caption = f"{caption}（筛相关：{self._uid or '当前编号'}）"
        elif related_first:
            caption = f"{caption}（相关优先：{self._uid or '当前编号'}）"
        paths = get_open_file_names(
            self,
            caption,
            start=start,
            filter=(
                "JPG 与 TIFF (*.jpg *.jpeg *.JPG *.JPEG *.tif *.tiff *.TIF *.TIFF);;"
                "JPG (*.jpg *.jpeg *.JPG *.JPEG);;"
                "TIFF (*.tif *.tiff *.TIF *.TIFF)"
            ),
            # The proxy-backed mtime sorter is useful for related-file review,
            # but expensive on mounted/network folders. Keep the common "+"
            # path lightweight; users can enable 相关优先 when they need it.
            sort_by_mtime=bool(related_first or filter_related),
            priority_paths=priority_paths,
            priority_terms=priority_terms,
            filter_terms=filter_terms,
        )
        if not paths:
            return

        self._add_selected_media_paths_to_group(group_index, paths)

    def _add_selected_media_paths_to_group(self, group_index: int, paths: list[str]) -> None:
        """Add selected JPG/TIFF paths from any picker to a group."""
        if not paths:
            return

        jpgs, tiffs = _split_media_paths(paths)
        jpgs = [r for p in jpgs if (r := _resolve_path_for_group(p))]
        tiff_path = None
        if tiffs:
            tiff_resolved = _resolve_path_for_group(tiffs[0])
            if not tiff_resolved:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "添加照片", f"找不到 TIFF：\n{tiffs[0]}")
                return
            tiff_path = tiff_resolved
        if not jpgs and not tiff_path:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, "添加照片", "未识别到 JPG 或 TIFF 文件，请检查扩展名。"
            )
            return
        if len(tiffs) > 1:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "添加照片", "一次只能关联 1 个 TIFF。")
            return

        self.drop_external_files(group_index, jpgs, tiff_path)

        if jpgs and self._uid:
            project_dir = getattr(self.ctx, "current_project_dir", None)
            if project_dir:
                try:
                    from app.services.activation_service import manual_assign
                    manual_assign(project_dir, self._uid, jpgs)
                except Exception:
                    pass

    def _pick_related_files_from_dir(
        self,
        *,
        uid: str | None = None,
        start: str = "",
    ) -> list[str] | None:
        """Pick current-UID related files after selecting a visible JPG/TIF."""
        target_uid = str(uid or self._uid or "").strip()
        display_key = _uid_core_key(target_uid)
        if not target_uid:
            QMessageBox.warning(self, "筛相关", "当前没有激活编号，无法筛选相关文件。")
            return None

        shortcuts = self._media_location_shortcuts(start=start, uid=target_uid)
        picker_start = self._preferred_media_location_start(start, shortcuts)
        selected_paths, folder = _pick_media_paths_or_folder(
            self,
            f"选择 {display_key} 相关 JPG/TIF 所在位置",
            start=picker_start,
            priority_terms=[],
            file_exts=_JPG_EXTS | _TIFF_EXTS,
            shortcuts=shortcuts,
        )
        if selected_paths:
            return selected_paths
        if not folder:
            return None
        if not Path(folder).is_dir():
            QMessageBox.warning(self, "筛相关", f"无法读取所选位置：\n{folder}")
            return []
        return self._select_related_files_from_folder(
            folder,
            target_uid,
            display_key,
            show_empty_message=True,
        )

    def _select_related_files_from_folder(
        self,
        folder: str,
        target_uid: str,
        display_key: str,
        *,
        show_empty_message: bool,
    ) -> list[str] | None:
        candidates = _scan_related_files_in_dir(folder, target_uid)
        if not candidates:
            if show_empty_message:
                QMessageBox.information(
                    self,
                    "筛相关",
                    f"此目录没有找到 {display_key} 匹配的 TIF，或没有时间附近的 JPG。",
                )
            return []
        dlg = _RelatedFilesPickerDialog(
            display_key,
            folder,
            candidates,
            all_candidates_loader=lambda: _scan_all_media_timeline_in_dir(folder, target_uid),
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return dlg.selected_paths()

    def _pick_jpgs_for_existing_tiff(self, group: "Group", *, start: str = "") -> list[str] | None:
        """Pick JPG originals near an already-linked TIFF's timestamp."""
        tiff_path = getattr(group, "composed_tiff_path", None) or ""
        if not tiff_path:
            return None

        display_key = _uid_core_key(self._uid or Path(tiff_path).stem)
        shortcuts = self._media_location_shortcuts(start=start, uid=display_key)
        picker_start = self._preferred_media_location_start(start, shortcuts)
        selected_paths, folder = _pick_media_paths_or_folder(
            self,
            f"选择 {Path(tiff_path).name} 对应 JPG 所在位置",
            start=picker_start,
            priority_terms=[],
            file_exts=_JPG_EXTS,
            shortcuts=shortcuts,
        )
        if selected_paths:
            jpgs, _tiffs = _split_media_paths(selected_paths)
            return jpgs
        if not folder:
            return None
        if not Path(folder).is_dir():
            QMessageBox.warning(self, "关联JPG", f"无法读取所选位置：\n{folder}")
            return []
        candidates = _scan_jpgs_near_tiff_in_dir(folder, tiff_path)
        if not candidates:
            QMessageBox.information(
                self,
                "关联JPG",
                "此目录没有找到与该 TIF 修改时间接近的 JPG。",
            )
            return []
        dlg = _RelatedFilesPickerDialog(
            self._uid or "",
            folder,
            candidates,
            title=f"{Path(tiff_path).name} 附近 JPG",
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        jpgs, _tiffs = _split_media_paths(dlg.selected_paths())
        return jpgs

    def _on_import_pending_group(self) -> None:
        """Toolbar action: select JPGs and create one pending draft group."""
        if not self._grouping:
            return

        start = ""
        project_dir = getattr(self.ctx, "current_project_dir", None)
        if project_dir:
            pd = Path(project_dir)
            s = getattr(self.ctx, "settings", None)
            inc = getattr(s, "incoming_subdir", None) if s else None
            inc = inc if isinstance(inc, str) and inc else "incoming-jpg"
            candidate = pd / inc
            if candidate.is_dir():
                start = str(candidate)
            elif pd.is_dir():
                start = str(pd)

        from app.utils.ui import get_open_file_names
        paths = get_open_file_names(
            self,
            "导入 JPG 到待整理分组",
            start=start,
            filter="JPG (*.jpg *.jpeg *.JPG *.JPEG)",
            sort_by_mtime=True,
        )
        if not paths:
            return

        jpgs, _tiffs = _split_media_paths(paths)
        if not jpgs:
            QMessageBox.warning(self, "导入待整理", "请选择 JPG 文件。")
            return

        incoming_dir = _project_incoming_dir(self.ctx)
        if incoming_dir is not None:
            from app.services.photo_import_service import import_jpgs_to_incoming
            result = import_jpgs_to_incoming(list(jpgs), incoming_dir)
            if result.errors:
                QMessageBox.warning(
                    self, "导入待整理部分失败", "\n".join(result.errors[:5])
                )
            jpgs = [
                r for p in result.imported_paths
                if (r := _resolve_path_for_group(p))
            ]
        else:
            jpgs = [r for p in jpgs if (r := _resolve_path_for_group(p))]
        if not jpgs:
            QMessageBox.warning(self, "导入待整理", "没有成功导入 JPG，未创建空分组。")
            return

        group_index = self._append_group(jpg_paths=jpgs)
        if group_index is None:
            return
        self._render_signature = None
        self._rebuild()
        self.grouping_changed.emit()

    def _on_link_result_pair(self) -> None:
        """Register an existing TIFF+ZIP pair as a finished result for this UID."""
        if not self._uid or not self._grouping:
            return

        project_dir = getattr(self.ctx, "current_project_dir", None)
        if not project_dir:
            QMessageBox.warning(self, "关联成品", "当前没有打开项目。")
            return

        s = getattr(self.ctx, "settings", None)
        res = getattr(s, "results_subdir", None) if s else None
        res = res if isinstance(res, str) and res else "results"
        results_dir = Path(project_dir) / res
        db = None
        try:
            db = self.ctx.get_db(project_dir)
        except Exception:
            db = None
        used = _registered_result_paths(
            db,
            current_uid=self._uid,
            current_groups=list(self._grouping.groups),
        )
        candidates = [
            c for c in _result_pair_candidates(results_dir, used)
            if not _uid_filename_mismatch(self._uid, str(c.get("tiff") or ""))
            and not _uid_filename_mismatch(self._uid, str(c.get("zip") or ""))
        ]
        dlg = _ResultPairPickerDialog(candidates, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        selected = dlg.selected_pair()
        if not selected:
            return
        tiff_path = _resolve_path_for_group(selected["tiff"])
        zip_path = str(Path(selected["zip"]))
        if not tiff_path or not Path(zip_path).is_file():
            QMessageBox.warning(self, "关联成品", "TIF 或 ZIP 文件不存在。")
            return
        if (
            not self._result_file_matches_current_uid(tiff_path, "关联成品")
            or not self._result_file_matches_current_uid(zip_path, "关联成品")
        ):
            return
        owner = used.get(_result_path_key(tiff_path)) or used.get(_result_path_key(zip_path))
        if owner:
            QMessageBox.warning(
                self,
                "关联成品",
                f"这组成品已经关联到编号：{owner}\n如需改绑，请先在成果区使用“关联到右侧编号”。",
            )
            return

        group_index = self._append_group()
        if group_index is None:
            return
        target = next(
            (g for g in self._grouping.groups if g.group_index == group_index),
            None,
        )
        if target is None:
            return

        from datetime import datetime, timezone
        target.composed_tiff_path = tiff_path
        target.output_name = Path(tiff_path).stem
        target.archive_zip = zip_path
        target.status = "organized"
        target.source = "existing-result-pair"
        target.updated_at = datetime.now(tz=timezone.utc).isoformat()
        self._render_signature = None
        self._rebuild()
        self.grouping_changed.emit()
        self.archive_zip_registered.emit(self._uid, group_index)

    def _import_existing_tiff_into_group(self, group_index: int) -> None:  # #cursor groupingImportTiff
        """Open TIFF-import dialog and update the group composedTiffPath."""
        if not self._uid or not self._grouping:
            return
        target = next(
            (g for g in self._grouping.groups if g.group_index == group_index), None
        )
        if target is None:
            return

        # Collect TIFF candidates from the project's results/ and incoming-jpg/
        tiff_candidates: list[str] = []
        try:
            import os
            project_dir = getattr(self.ctx, "current_project_dir", None)
            if project_dir:
                # 用项目配置的 incoming/results 子目录（含遗留 新拍JPG），不写死。
                s = getattr(self.ctx, "settings", None)
                inc = getattr(s, "incoming_subdir", None)
                res = getattr(s, "results_subdir", None)
                inc = inc if isinstance(inc, str) and inc else "incoming-jpg"
                res = res if isinstance(res, str) and res else "results"
                subs = [res, inc]
                if not os.path.isdir(os.path.join(project_dir, inc)) and \
                   os.path.isdir(os.path.join(project_dir, "新拍JPG")):
                    subs.append("新拍JPG")
                for sub in subs:
                    d = os.path.join(project_dir, sub)
                    if os.path.isdir(d):
                        for f in sorted(os.listdir(d)):
                            if f.lower().endswith((".tif", ".tiff")):
                                tiff_candidates.append(os.path.join(d, f))
        except Exception:
            pass

        # Show picker dialog
        dlg = _TiffImportDialog(
            group_index=group_index,
            tiff_candidates=tiff_candidates,
            existing_tiff=target.composed_tiff_path or "",
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        tiff_path = dlg.selected_path()
        if not tiff_path:
            return

        if not self._associate_tiff_with_group(target, tiff_path):
            return

        self._rebuild()
        self.grouping_changed.emit()
        # Propagate to workbench view as well
        if self._uid:
            self.import_tiff_requested.emit(self._uid, group_index)

    def _register_existing_archive_zip(self, group_index: int) -> None:
        """Associate an existing ZIP archive with a composed group."""
        if not self._uid or not self._grouping:
            return
        target = next(
            (g for g in self._grouping.groups if g.group_index == group_index), None
        )
        if target is None:
            return

        from app.utils.ui import get_open_file_name
        zip_path = get_open_file_name(
            self,
            "选择 ZIP 归档",
            filter="ZIP 归档 (*.zip *.ZIP)",
        )
        if not zip_path:
            return
        if not zip_path.lower().endswith(".zip"):
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "注册 ZIP", "请选择 .zip 文件。")
            return
        if not self._result_file_matches_current_uid(zip_path, "注册 ZIP"):
            return

        if target.archive_zip and target.archive_zip != zip_path:
            from PyQt6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self,
                "替换 ZIP？",
                f"本组已有 ZIP：{Path(target.archive_zip).name}\n\n"
                f"是否替换为：{Path(zip_path).name}？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        from datetime import datetime, timezone
        target.archive_zip = zip_path
        target.status = "organized"
        target.updated_at = datetime.now(tz=timezone.utc).isoformat()
        self._rebuild()
        self.grouping_changed.emit()
        self.archive_zip_registered.emit(self._uid, group_index)


class _ResultPairPickerDialog(QDialog):
    """Pick one unclaimed TIFF+ZIP result pair from results/."""

    def __init__(self, candidates: list[dict],
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._all_candidates = list(candidates or [])
        self._selected: dict | None = None
        self.setWindowTitle("关联成品")
        self.setMinimumSize(760, 460)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        title = QLabel("选择未关联的 TIF + ZIP 成品")
        title.setObjectName("Section")
        root.addWidget(title)

        hint = QLabel("已被其它编号登记的成品默认隐藏，避免重复关联。")
        hint.setObjectName("MutedSmall")
        root.addWidget(hint)

        self._show_associated = QCheckBox("显示已关联成品（只读灰显）")
        self._show_associated.toggled.connect(self._populate)
        root.addWidget(self._show_associated)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.currentItemChanged.connect(lambda *_: self._sync_buttons())
        self._list.itemDoubleClicked.connect(lambda _item: self._accept_selected())
        root.addWidget(self._list, stretch=1)

        self._empty = QLabel("")
        self._empty.setObjectName("Muted")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        self._empty.hide()
        root.addWidget(self._empty)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self._accept_selected)
        self._buttons.rejected.connect(self.reject)
        root.addWidget(self._buttons)

        self._populate()

    def selected_pair(self) -> dict | None:
        return dict(self._selected) if self._selected else None

    def _populate(self) -> None:
        self._list.clear()
        show_associated = self._show_associated.isChecked()
        visible = [
            c for c in self._all_candidates
            if show_associated or not c.get("associated")
        ]
        for candidate in visible:
            self._add_candidate(candidate)
        if not self._all_candidates:
            text = "results/ 中没有找到同名的 TIF + ZIP 成品。"
        elif not visible:
            text = "所有 TIF + ZIP 成品都已经关联到编号。勾选上方选项可查看。"
        else:
            text = ""
        self._empty.setText(text)
        self._empty.setVisible(bool(text))
        self._list.setVisible(bool(visible))
        self._sync_buttons()

    def _add_candidate(self, candidate: dict) -> None:
        owner = str(candidate.get("associated_uid") or "")
        tiff_name = Path(str(candidate.get("tiff") or "")).name
        zip_name = Path(str(candidate.get("zip") or "")).name
        status = f"已关联：{owner}" if owner else "未关联"
        item = QListWidgetItem(f"{status}\n{tiff_name}\n{zip_name}")
        item.setData(Qt.ItemDataRole.UserRole, candidate)
        if owner:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            item.setForeground(Qt.GlobalColor.gray)
            item.setToolTip(f"已关联到 {owner}，不能在这里重复登记")
        else:
            item.setToolTip("双击或选择后确定，关联到当前编号")
        self._list.addItem(item)

    def _current_candidate(self) -> dict | None:
        item = self._list.currentItem()
        if item is None:
            return None
        candidate = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(candidate, dict) or candidate.get("associated"):
            return None
        return candidate

    def _sync_buttons(self) -> None:
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setEnabled(self._current_candidate() is not None)

    def _accept_selected(self) -> None:
        candidate = self._current_candidate()
        if candidate is None:
            return
        self._selected = dict(candidate)
        self.accept()


class _RelatedFilesPickerDialog(QDialog):
    """App-owned picker for current UID related JPG/TIFF files."""

    def __init__(
        self,
        uid: str,
        folder: str,
        candidates: list[dict],
        *,
        all_candidates: list[dict] | None = None,
        all_candidates_loader: Callable[[], list[dict]] | None = None,
        title: str | None = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._block_candidates = list(candidates or [])
        self._all_candidates = list(all_candidates or [])
        self._all_candidates_loader = all_candidates_loader
        self._candidates = list(self._block_candidates)
        self._preserve_checked_paths: set[str] | None = None
        self._thumb_queue: list[tuple[QLabel, str, str]] = []
        display_title = title or f"{uid} 相关文件"
        self.setWindowTitle(display_title)
        self.setMinimumSize(860, 540)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        title_lbl = QLabel(display_title)
        title_lbl.setObjectName("Section")
        root.addWidget(title_lbl)

        hint = QLabel(
            f"{folder}\n"
            "已按修改时间排序；按“先拍 JPG，再合成 TIF”的时间块识别。"
            " 选择某个 TIF 时，会勾选它前面到上一个 TIF 之间的 JPG。"
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        btn_row = QHBoxLayout()
        select_all = QPushButton("全选")
        select_all.setObjectName("Ghost")
        select_all.clicked.connect(lambda: self._set_all_checked(True))
        btn_row.addWidget(select_all)
        clear = QPushButton("清空")
        clear.setObjectName("Ghost")
        clear.clicked.connect(lambda: self._set_all_checked(False))
        btn_row.addWidget(clear)
        if self._all_candidates or self._all_candidates_loader is not None:
            block_btn = QPushButton("时间块")
            block_btn.setObjectName("Ghost")
            block_btn.clicked.connect(lambda: self._replace_candidates(self._block_candidates))
            btn_row.addWidget(block_btn)
            all_btn = QPushButton("全部时间线")
            all_btn.setObjectName("Ghost")
            all_btn.clicked.connect(self._show_all_timeline)
            btn_row.addWidget(all_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(["", "缩略图", "文件名", "类型", "修改时间", "距TIF"])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(74)
        header = self._table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(1, 78)
        self._table.setColumnWidth(2, 470)
        self._table.setColumnWidth(3, 80)
        self._table.setColumnWidth(4, 170)
        self._table.setColumnWidth(5, 90)
        root.addWidget(self._table, stretch=1)

        self._syncing_checks = False
        self._populate()
        self._table.itemChanged.connect(self._on_check_changed)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        root.addWidget(self._buttons)

    def selected_paths(self) -> list[str]:
        paths: list[str] = []
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                data = item.data(Qt.ItemDataRole.UserRole)
                path = data.get("path") if isinstance(data, dict) else data
                if path:
                    paths.append(str(path))
        return paths

    def _candidate_for_row(self, row: int) -> dict:
        check_item = self._table.item(row, 0)
        if check_item is not None:
            data = check_item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, dict):
                return data
        if 0 <= row < len(self._candidates):
            return self._candidates[row]
        return {}

    def _replace_candidates(self, candidates: list[dict]) -> None:
        self._preserve_checked_paths = set(self.selected_paths())
        self._candidates = list(candidates or [])
        self._populate()

    def _show_all_timeline(self) -> None:
        if not self._all_candidates and self._all_candidates_loader is not None:
            self._all_candidates = list(self._all_candidates_loader() or [])
        self._replace_candidates(self._all_candidates)

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self._syncing_checks = True
        try:
            for row in range(self._table.rowCount()):
                item = self._table.item(row, 0)
                if item is not None:
                    item.setCheckState(state)
        finally:
            self._syncing_checks = False

    def _default_anchor_path(self) -> str:
        for item in self._candidates:
            if item.get("anchor"):
                path = str(item.get("path") or "")
                if path:
                    return path
        for item in self._candidates:
            if str(item.get("kind") or "").upper() == "TIF":
                path = str(item.get("path") or "")
                if path:
                    return path
        return ""

    def _checked_for_anchor(self, item: dict, anchor_path: str) -> bool:
        if not anchor_path:
            return str(item.get("kind") or "").upper() != "TIF"
        path = str(item.get("path") or "")
        if path == anchor_path:
            return True
        if "default_related" in item:
            return (
                bool(item.get("default_related"))
                and str(item.get("nearest_anchor") or "") == anchor_path
            )
        return (
            str(item.get("kind") or "").upper() != "TIF"
            and str(item.get("nearest_anchor") or "") == anchor_path
            and float(item.get("nearest_seconds") or 0)
            <= _RELATED_PICKER_DEFAULT_CHECK_SECONDS
        )

    def _select_anchor_group(self, anchor_path: str) -> None:
        if not anchor_path:
            return
        self._syncing_checks = True
        try:
            for row in range(self._table.rowCount()):
                candidate = self._candidate_for_row(row)
                item = self._table.item(row, 0)
                if item is None:
                    continue
                checked = self._checked_for_anchor(candidate, anchor_path)
                item.setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
        finally:
            self._syncing_checks = False

    def _on_check_changed(self, item: QTableWidgetItem) -> None:
        if self._syncing_checks or item.column() != 0:
            return
        if item.checkState() != Qt.CheckState.Checked:
            return
        row = item.row()
        if row < 0:
            return
        candidate = self._candidate_for_row(row)
        if str(candidate.get("kind") or "").upper() != "TIF":
            return
        self._select_anchor_group(str(candidate.get("path") or ""))

    def _thumbnail_label(self, item: dict) -> QLabel:
        path = str(item.get("path") or "")
        kind = str(item.get("kind") or "").upper()
        label = QLabel()
        label.setFixedSize(68, 68)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setObjectName("RelatedThumb")
        label.setToolTip(path)
        label.setText(kind or "IMG")
        label.setProperty("hasThumbnail", False)
        if path:
            self._thumb_queue.append((label, path, kind))
        return label

    def _apply_thumbnail(self, label: QLabel, path: str, kind: str) -> None:
        pixmap = _media_thumbnail_pixmap(path, 64)
        if pixmap is not None and not pixmap.isNull():
            label.setPixmap(pixmap)
            label.setProperty("hasThumbnail", True)
        else:
            label.setText(kind or "IMG")
            label.setProperty("hasThumbnail", False)

    def _load_next_thumbnail_batch(self) -> None:
        processed = 0
        while self._thumb_queue and processed < 2:
            label, path, kind = self._thumb_queue.pop(0)
            self._apply_thumbnail(label, path, kind)
            processed += 1
        if self._thumb_queue:
            QTimer.singleShot(10, self._load_next_thumbnail_batch)

    def _load_all_thumbnails_now(self) -> None:
        while self._thumb_queue:
            label, path, kind = self._thumb_queue.pop(0)
            self._apply_thumbnail(label, path, kind)

    def _distance_text(self, item: dict) -> tuple[str, str]:
        if item.get("anchor"):
            return "TIF", "匹配编号的 TIF"
        seconds = int(item.get("nearest_seconds") or 0)
        direction = str(item.get("relative_to_tif") or "")
        prefix = "前" if direction == "before" else "后" if direction == "after" else ""
        text = f"{prefix}{seconds // 60}:{seconds % 60:02d}"
        anchor_name = str(item.get("nearest_anchor_name") or "")
        tooltip = f"距离 {anchor_name} {seconds // 60}分{seconds % 60}秒" if anchor_name else ""
        return text, tooltip

    def _apply_anchor_row_style(self, row: int, candidate: dict) -> None:
        if not candidate.get("anchor"):
            return
        bg = QColor("#edfdf7")
        for col in range(self._table.columnCount()):
            cell = self._table.item(row, col)
            if cell is not None:
                cell.setBackground(bg)

    def _populate(self) -> None:
        self._syncing_checks = True
        preserve = self._preserve_checked_paths
        self._preserve_checked_paths = None
        self._thumb_queue.clear()
        try:
            self._table.setSortingEnabled(False)
            self._table.setRowCount(len(self._candidates))
            default_anchor = self._default_anchor_path()
            default_anchor_row = -1
            for row, item in enumerate(self._candidates):
                path = str(item.get("path") or "")
                if path == default_anchor:
                    default_anchor_row = row
                check = QTableWidgetItem("")
                check.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                checked = (
                    path in preserve
                    if preserve is not None else self._checked_for_anchor(item, default_anchor)
                )
                check.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
                check.setData(Qt.ItemDataRole.UserRole, dict(item))
                self._table.setItem(row, 0, check)
                self._table.setCellWidget(row, 1, self._thumbnail_label(item))
                self._table.setRowHeight(row, 74)

                name = QTableWidgetItem(str(item.get("name") or ""))
                name.setToolTip(path)
                self._table.setItem(row, 2, name)

                kind = QTableWidgetItem(str(item.get("kind") or ""))
                self._table.setItem(row, 3, kind)

                mtime = QTableWidgetItem(_mtime_text(float(item.get("mtime") or 0)))
                self._table.setItem(row, 4, mtime)

                distance, tooltip = self._distance_text(item)
                distance_item = QTableWidgetItem(distance)
                if tooltip:
                    distance_item.setToolTip(tooltip)
                self._table.setItem(row, 5, distance_item)
                self._apply_anchor_row_style(row, item)
        finally:
            self._syncing_checks = False
        self._table.horizontalHeader().setSortIndicator(4, Qt.SortOrder.AscendingOrder)
        self._table.setSortingEnabled(True)
        if default_anchor_row >= 0:
            default_anchor_row = self._row_for_path(default_anchor)
            self._table.setCurrentCell(default_anchor_row, 2)
            anchor_item = self._table.item(default_anchor_row, 2)
            if anchor_item is not None:
                self._table.scrollToItem(
                    anchor_item,
                    QAbstractItemView.ScrollHint.PositionAtCenter,
                )
        QTimer.singleShot(0, self._load_next_thumbnail_batch)

    def _row_for_path(self, path: str) -> int:
        for row in range(self._table.rowCount()):
            candidate = self._candidate_for_row(row)
            if str(candidate.get("path") or "") == path:
                return row
        return -1


# ── TIFF Import Dialog ────────────────────────────────────────────────────────

class _TiffImportDialog(QDialog):
    """Dialog to pick an existing TIFF file and associate it to a group.

    Mirrors web renderTiffImportModal() app.js:6124.
    Shows:
      1. A scrollable list of TIF/TIFF files found in results/ and incoming-jpg/
      2. A text field to paste an arbitrary absolute path
    """

    def __init__(
        self,
        group_index: int,
        tiff_candidates: list[str],
        existing_tiff: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._selected: str = ""
        self.setWindowTitle(f"导入 TIF → 组 {group_index}")
        self.setMinimumWidth(520)
        self.setMinimumHeight(380)
        self._setup_ui(group_index, tiff_candidates, existing_tiff)

    def _setup_ui(
        self,
        group_index: int,
        candidates: list[str],
        existing_tiff: str,
    ) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16,16)
        root.setSpacing(10)

        note = QLabel(
            "选择一张已有 TIF（如在 Helicon 手动合成的）挂到本组，"
            "随后点「整理」把对应 JPG 打包归档，不重跑 Helicon。"
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        root.addWidget(note)

        sec = QLabel("检测到的 TIF 文件")
        sec.setObjectName("Section")
        root.addWidget(sec)

        self._list = QListWidget()
        self._list.setMaximumHeight(200)
        for path in candidates:
            item = QListWidgetItem(Path(path).name)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self._list.addItem(item)
        if not candidates:
            placeholder = QListWidgetItem("（项目目录暂无 TIF 文件）")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(placeholder)
        self._list.itemDoubleClicked.connect(self._on_list_double_clicked)
        root.addWidget(self._list)

        paste_row = QHBoxLayout()
        paste_lbl = QLabel("或粘贴绝对路径：")
        paste_lbl.setObjectName("Muted")
        paste_row.addWidget(paste_lbl)
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("粘贴 TIF 文件的完整路径")
        if existing_tiff:
            self._path_edit.setText(existing_tiff)
        paste_row.addWidget(self._path_edit, stretch=1)
        root.addLayout(paste_row)

        # Browse button
        browse_row = QHBoxLayout()
        browse_btn = QPushButton("浏览…")
        browse_btn.setObjectName("Ghost")
        browse_btn.setFixedHeight(28)
        browse_btn.clicked.connect(self._browse_for_tiff_file)
        browse_row.addWidget(browse_btn)
        browse_row.addStretch()
        root.addLayout(browse_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_selected_tiff)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _on_list_double_clicked(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self._path_edit.setText(path)

    def _browse_for_tiff_file(self) -> None:
        from app.utils.ui import get_open_file_name
        path = get_open_file_name(
            self, "选择 TIF 文件", filter="TIFF 文件 (*.tif *.tiff *.TIF *.TIFF)"
        )
        if path:
            self._path_edit.setText(path)

    def _accept_selected_tiff(self) -> None:
        # Prefer path_edit; fall back to list selection
        path = self._path_edit.text().strip()
        if not path:
            item = self._list.currentItem()
            if item:
                path = item.data(Qt.ItemDataRole.UserRole) or ""
        import re
        if path and not re.search(r"\.(tif|tiff)$", path, re.IGNORECASE):
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "格式错误", "请选择 .tif / .tiff 文件。")
            return
        self._selected = path
        self.accept()

    def selected_path(self) -> str:
        return self._selected
