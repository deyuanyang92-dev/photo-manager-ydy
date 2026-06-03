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
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.services.organize_service import _parse_uid_from_tiff_name


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
) -> dict:
    """Scan project for named TIFFs and pair with preceding JPGs.

    Returns dict with keys:
      ok: bool
      specimens: list of {uid, groups: [{seq, tiffName, tiffPath, jpgPaths, jpgCount}]}
      unassignedJpgs: list of paths with no TIFF pair
      unnamedTiffs: list of {name, path} for TIFFs that don't match naming convention

    Oracle: server.js /api/organize/retroactive/scan handler.
    """
    resolved = str(Path(project_dir).resolve())
    results_dir = os.path.join(resolved, results_subdir)
    incoming_dir = os.path.join(resolved, incoming_subdir)

    # Build selection set if provided
    sel_set: Optional[set[str]] = None
    if selection_paths:
        sel_set = {os.path.abspath(p) for p in selection_paths}

    # List TIFFs in results/
    tiff_files: list[dict] = []
    unnamed_tiffs: list[dict] = []
    if os.path.isdir(results_dir):
        for name in sorted(os.listdir(results_dir)):
            if not re.search(r"\.tiff?$", name, re.IGNORECASE):
                continue
            full = os.path.join(results_dir, name)
            if not os.path.isfile(full):
                continue
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
