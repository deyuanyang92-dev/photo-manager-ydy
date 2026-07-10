"""Media/path helpers for the specimen grouping editor."""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Optional
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt

from app.services import grouping_service

if TYPE_CHECKING:
    from app.app_context import AppContext
    from app.services.grouping_service import Group


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

def _resolve_path_for_group(p: str) -> Optional[str]:
    """Normalize a path for grouping; return None if file missing."""
    # NOTE: explicit Optional here is intentional because callers branch on
    # `None` to skip unresolved media entries.
    from app.services.helicon_service import resolve_existing_image_path
    return resolve_existing_image_path(p)
