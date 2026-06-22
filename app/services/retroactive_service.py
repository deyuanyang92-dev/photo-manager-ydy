"""retroactive_service.py — Retroactive organize: scan results/ + incoming-jpg/.

Oracle: server.js POST /api/organize/retroactive/scan (lines ~3840-3920 area)
        and POST /api/organize/retroactive/apply.

Algorithm (matches server.js):
  1. List all TIFF files in results/ with valid 7-part naming.
  2. For each TIFF, collect JPGs from incoming-jpg/ whose mtime is EARLIER
     than the TIFF's mtime (time-window pairing: "JPGs shot before this TIF").
  3. Return specimens → groups structure with jpgCount.

apply:
  For each confirmed group, call archive_service.archive_group.
"""
from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.services.organize_service import _parse_uid_from_tiff_name


@dataclass
class FileResult:
    """Per-file result from a batch retroactive archive operation."""
    name: str
    ok: bool
    size_bytes: int
    error: str


def scan_folder_auto_groups(
    folder: str,
    *,
    recursive: bool = False,
    fallback_uid: Optional[str] = None,
) -> dict:
    """Split a legacy mixed JPG/TIFF folder into batches using TIFF boundaries.

    Files are ordered by filesystem modification time (nanosecond precision),
    then by name for deterministic handling of equal timestamps.  Every TIFF
    closes the JPG batch immediately before it.  A TIFF with a standard result
    name supplies the specimen UID and result sequence; unnamed TIFFs and JPGs
    without a following named TIFF are returned for manual review.

    This is intentionally read-only.  The returned structure matches
    ``scan_project_retroactive`` so the existing preview/archive UI can be
    reused without adding a second organizing pipeline.
    """
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"扫描目录不存在: {folder}")

    iterator = root.rglob("*") if recursive else root.iterdir()
    events: list[tuple[int, str, Path]] = []
    for path in iterator:
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".tif", ".tiff"}:
            continue
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            continue
        events.append((mtime_ns, path.name.casefold(), path))
    events.sort(key=lambda item: (item[0], item[1], str(item[2])))

    pending_jpgs: list[str] = []
    unnamed_tiffs: list[dict] = []
    groups_by_uid: dict[str, list[dict]] = {}
    for _mtime_ns, _name_key, path in events:
        if path.suffix.lower() in {".jpg", ".jpeg"}:
            pending_jpgs.append(str(path))
            continue

        parsed_uid = _parse_uid_from_tiff_name(path.name)
        uid = parsed_uid or (fallback_uid or "").strip() or None
        parts = path.stem.split("-")
        try:
            seq = int(parts[4]) if parsed_uid else None
        except (IndexError, ValueError):
            seq = None
        if uid and seq is None and fallback_uid:
            seq = len(groups_by_uid.get(uid, [])) + 1
        if not uid or seq is None:
            unnamed_tiffs.append({
                "name": path.name,
                "path": str(path),
                "jpgPaths": list(pending_jpgs),
            })
            pending_jpgs.clear()
            continue

        jpg_paths = list(pending_jpgs)
        pending_jpgs.clear()
        groups_by_uid.setdefault(uid, []).append({
            "seq": seq,
            "tiffName": path.name,
            "tiffPath": str(path),
            "tiffNameValid": parsed_uid is not None,
            "jpgPaths": jpg_paths,
            "jpgCount": len(jpg_paths),
        })

    specimens = [
        {"uid": uid, "groups": groups}
        for uid, groups in groups_by_uid.items()
    ]
    return {
        "ok": True,
        "specimens": specimens,
        "unassignedJpgs": pending_jpgs,
        "unnamedTiffs": unnamed_tiffs,
        "scanFolder": str(root),
        "autoGroup": True,
    }


def register_auto_group(
    db: sqlite3.Connection,
    uid: str,
    detected: dict,
    *,
    archive_zip: Optional[str] = None,
) -> int:
    """Persist one confirmed time-detected batch as a normal grouping row.

    Re-registering the same TIFF updates its row instead of creating a
    duplicate, making rescans safe after a partially completed batch.
    Returns the persisted ``group_index``.
    """
    from app.services.grouping_service import Group, load_grouping, save_grouping

    grouping = load_grouping(db, uid)
    tiff_path = str(detected.get("tiffPath") or "")
    target = next(
        (g for g in grouping.groups if g.composed_tiff_path == tiff_path),
        None,
    )
    now = datetime.now(tz=timezone.utc).isoformat()
    if target is None:
        group_index = max(
            (g.group_index for g in grouping.groups), default=-1
        ) + 1
        target = Group(
            group_index=group_index,
            angle_label=f"自动分组{group_index + 1}",
            created_at=now,
        )
        grouping.groups.append(target)

    target.jpg_paths = list(detected.get("jpgPaths") or [])
    target.composed_tiff_path = tiff_path or None
    target.result_sequence = detected.get("seq")
    target.archive_zip = archive_zip
    target.status = "organized" if archive_zip else "composed"
    target.source = "auto-time-group"
    target.updated_at = now
    save_grouping(db, uid, grouping.groups, clean_phantoms=False)
    return target.group_index


def _iso_mtime(p: str) -> str:
    st = os.stat(p)
    return datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()


def scan_project_retroactive(
    project_dir: str,
    db: sqlite3.Connection,
    *,
    incoming_subdir: str = "incoming-jpg",
    results_subdir: str = "results",
    selection_paths: Optional[list[str]] = None,
    subdir: Optional[str] = None,
) -> dict:
    """Scan project for named TIFFs and pair with preceding JPGs.

    If *subdir* is given, only TIFFs inside results/<subdir>/ are scanned;
    otherwise the entire results/ tree (including subdirectories) is covered.

    Returns dict with keys:
      ok: bool
      specimens: list of {uid, groups: [{seq, tiffName, tiffPath, jpgPaths, jpgCount}]}
      unassignedJpgs: list of paths with no TIFF pair
      unnamedTiffs: list of {name, path} for TIFFs that don't match naming convention

    Oracle: server.js /api/organize/retroactive/scan handler.
    """
    resolved = str(Path(project_dir).resolve())
    if subdir is not None:
        results_dir = os.path.join(resolved, results_subdir, subdir)
    else:
        results_dir = os.path.join(resolved, results_subdir)
    incoming_dir = os.path.join(resolved, incoming_subdir)

    # Build selection set if provided
    sel_set: Optional[set[str]] = None
    if selection_paths:
        sel_set = {os.path.abspath(p) for p in selection_paths}

    # List TIFFs in results/ (recursive when subdir=None, flat otherwise)
    tiff_files: list[dict] = []
    unnamed_tiffs: list[dict] = []
    if os.path.isdir(results_dir):
        # Collect all TIFF paths: flat list when a specific subdir was given,
        # recursive walk when scanning the whole results/ tree.
        all_tiff_paths: list[str] = []
        if subdir is not None:
            for name in sorted(os.listdir(results_dir)):
                full = os.path.join(results_dir, name)
                if os.path.isfile(full) and re.search(r"\.tiff?$", name, re.IGNORECASE):
                    all_tiff_paths.append(full)
        else:
            for dirpath, _dirs, filenames in os.walk(results_dir):
                for name in sorted(filenames):
                    if re.search(r"\.tiff?$", name, re.IGNORECASE):
                        all_tiff_paths.append(os.path.join(dirpath, name))

        for full in all_tiff_paths:
            name = os.path.basename(full)
            if sel_set and os.path.abspath(full) not in sel_set:
                continue
            uid = _parse_uid_from_tiff_name(name)
            if not uid:
                unnamed_tiffs.append({"name": name, "path": full})
                continue
            stem = Path(name).stem
            parts = stem.split("-")
            try:
                seq = int(parts[4])
            except (IndexError, ValueError):
                unnamed_tiffs.append({"name": name, "path": full})
                continue
            tiff_files.append({
                "uid": uid, "seq": seq, "name": name, "path": full,
                "mtime": _iso_mtime(full),
            })

    if not tiff_files:
        return {
            "ok": True,
            "specimens": [],
            "unassignedJpgs": [],
            "unnamedTiffs": unnamed_tiffs,
        }

    # Sort TIFFs by mtime ascending (needed for time-window algorithm)
    tiff_files.sort(key=lambda t: t["mtime"])

    # List JPGs in incoming-jpg/
    jpg_files: list[dict] = []
    if os.path.isdir(incoming_dir):
        for name in os.listdir(incoming_dir):
            if not re.search(r"\.jpe?g$", name, re.IGNORECASE):
                continue
            full = os.path.join(incoming_dir, name)
            if not os.path.isfile(full):
                continue
            if sel_set and os.path.abspath(full) not in sel_set:
                continue
            jpg_files.append({"name": name, "path": full, "mtime": _iso_mtime(full)})

    jpg_files.sort(key=lambda j: j["mtime"])

    # Time-window pairing: each JPG → first TIFF with mtime > jpg mtime
    jpg_to_tiff: dict[str, int] = {}  # jpg_path → tiff index
    for jpg in jpg_files:
        for ti, tiff in enumerate(tiff_files):
            if tiff["mtime"] > jpg["mtime"]:
                jpg_to_tiff[jpg["path"]] = ti
                break  # first TIFF after this JPG

    # Group by TIFF index
    tiff_groups: dict[int, list[str]] = {i: [] for i in range(len(tiff_files))}
    for jpg_path, tiff_idx in jpg_to_tiff.items():
        tiff_groups[tiff_idx].append(jpg_path)

    unassigned_jpgs = [j["path"] for j in jpg_files if j["path"] not in jpg_to_tiff]

    # Build specimens structure
    uid_to_groups: dict[str, list[dict]] = {}
    for ti, tiff in enumerate(tiff_files):
        uid = tiff["uid"]
        if uid not in uid_to_groups:
            uid_to_groups[uid] = []
        jpg_paths = tiff_groups[ti]
        uid_to_groups[uid].append({
            "seq": tiff["seq"],
            "tiffName": tiff["name"],
            "tiffPath": tiff["path"],
            "jpgPaths": jpg_paths,
            "jpgCount": len(jpg_paths),
        })

    specimens = [
        {"uid": uid, "groups": sorted(groups, key=lambda g: g["seq"])}
        for uid, groups in uid_to_groups.items()
    ]

    return {
        "ok": True,
        "specimens": specimens,
        "unassignedJpgs": unassigned_jpgs,
        "unnamedTiffs": unnamed_tiffs,
    }
