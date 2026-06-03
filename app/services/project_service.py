"""project_service.py — Project lifecycle management.

Oracle:
  - server.js:2085-2465 (project management endpoints)
  - project-paths.js (dir resolution, legacy fallback)

Constants mirrored from project-paths.js:
  INCOMING_JPG_DIR = "incoming-jpg"
  LEGACY_INCOMING_JPG_DIR = "新拍JPG"
  RESULTS_DIR = "results"
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Optional

from app.utils.path_utils import default_registry

# ── Directory name constants ───────────────────────────────────────────────────
# Mirrors project-paths.js constants
INCOMING_JPG_DIR = "incoming-jpg"
LEGACY_INCOMING_JPG_DIR = "新拍JPG"
RESULTS_DIR = "results"
DATA_SUBDIR = "_data"


# ── Low-level helpers ──────────────────────────────────────────────────────────

def ensure_project_dirs(project_dir: str) -> dict:
    """Create the standard subdirectory layout under project_dir.

    Creates:  incoming-jpg/  results/  _data/

    Idempotent — safe to call multiple times.
    Returns a dict with resolved paths.

    Oracle: project-paths.js::ensureProjectDirs
    """
    root = Path(project_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)

    incoming = root / INCOMING_JPG_DIR
    results = root / RESULTS_DIR
    data = root / DATA_SUBDIR

    incoming.mkdir(exist_ok=True)
    results.mkdir(exist_ok=True)
    data.mkdir(exist_ok=True)

    return {
        "projectDir": str(root),
        "incomingJpgDir": str(incoming),
        "resultsDir": str(results),
        "dataDir": str(data),
    }


def get_incoming_jpg_dir(project_dir: str) -> str:
    """Return the path to the incoming-JPG directory.

    Prefers modern 'incoming-jpg' subdir; falls back to legacy '新拍JPG'
    if the modern dir is absent but the legacy dir exists.
    If neither exists, returns the modern path (not yet created).

    Oracle: project-paths.js::resolveIncomingJpgDir
    """
    root = Path(project_dir).resolve()
    modern = root / INCOMING_JPG_DIR
    legacy = root / LEGACY_INCOMING_JPG_DIR

    if modern.exists():
        return str(modern)
    if legacy.exists():
        return str(legacy)
    return str(modern)


def get_results_dir(project_dir: str) -> str:
    """Return the path to the results directory.

    Oracle: project-paths.js::resolveResultsDir
    """
    root = Path(project_dir).resolve()
    return str(root / RESULTS_DIR)


# ── Public service API ─────────────────────────────────────────────────────────

def create_project(name: str, directory: str) -> dict:
    """Create a new project at *directory* and return its descriptor dict.

    - Generates a unique ID.
    - Creates the standard subdirectory layout.
    - Does NOT persist to user_projects.json (caller responsibility).

    Oracle: server.js project creation logic.
    """
    resolved = str(Path(directory).resolve())
    dirs = ensure_project_dirs(resolved)

    project_id = str(uuid.uuid4())
    return {
        "id": project_id,
        "name": name,
        "dir": resolved,
        "directory": resolved,
        "incomingJpgDir": dirs["incomingJpgDir"],
        "resultsDir": dirs["resultsDir"],
    }


def open_project(directory: str) -> dict:
    """Open an existing project directory, registering it in the safe-path registry.

    - Calls ensure_project_dirs to create any missing subdirectories.
    - Registers the directory in default_registry so path-safety checks pass.
    - Returns a descriptor dict.

    Oracle: server.js open-project endpoint + registerAllowedDir pattern.
    """
    resolved = str(Path(directory).resolve())
    dirs = ensure_project_dirs(resolved)

    # Register the project root so assert_safe() passes for its children
    default_registry.register_root(resolved)

    return {
        "dir": resolved,
        "directory": resolved,
        "incomingJpgDir": dirs["incomingJpgDir"],
        "resultsDir": dirs["resultsDir"],
        "dataDir": dirs["dataDir"],
    }


def list_projects(user_projects_json_path: str) -> list:
    """Read the user_projects.json file and return the list of project dicts.

    Returns an empty list if the file is missing or contains no projects.
    Does NOT raise on missing file.

    Oracle: server.js::userProjectsRead / GET /api/user-projects
    """
    path = Path(user_projects_json_path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("projects", [])
    except (json.JSONDecodeError, OSError):
        return []


def get_project_summary(project_dir: str) -> dict:
    """Return live statistics for *project_dir*: specimen count, result TIFF count,
    pending JPG count.

    Oracle: server.js GET /api/project/summary (lines 2831-2870).

    Counts:
      specimenCount  — rows in ``_data/project.db`` specimens table (or 0 if absent).
      resultCount    — .tif/.tiff files in ``results/`` and ``results/freeform/``.
      pendingJpgCount— .jpg/.jpeg files in ``incoming-jpg/`` (or legacy ``新拍JPG/``).

    Always returns a dict; never raises.
    """
    import re
    import sqlite3 as _sqlite3

    root = Path(project_dir).resolve()

    # ── specimen count ────────────────────────────────────────────────────────
    specimen_count = 0
    db_path = root / DATA_SUBDIR / "project.db"
    if db_path.exists():
        try:
            conn = _sqlite3.connect(str(db_path))
            try:
                (specimen_count,) = conn.execute("SELECT COUNT(*) FROM specimens").fetchone()
            except Exception:
                specimen_count = 0
            finally:
                conn.close()
        except Exception:
            specimen_count = 0

    # ── result TIFF count ─────────────────────────────────────────────────────
    result_count = 0
    _tif_re = re.compile(r"\.tiff?$", re.IGNORECASE)

    def _count_tifs(directory: Path) -> int:
        if not directory.is_dir():
            return 0
        try:
            return sum(
                1 for n in directory.iterdir()
                if _tif_re.search(n.name) and n.is_file()
            )
        except OSError:
            return 0

    results_root = root / RESULTS_DIR
    result_count = _count_tifs(results_root) + _count_tifs(results_root / "freeform")

    # ── pending JPG count ─────────────────────────────────────────────────────
    pending_jpg_count = 0
    _jpg_re = re.compile(r"\.jpe?g$", re.IGNORECASE)
    incoming_path = Path(get_incoming_jpg_dir(project_dir))
    if incoming_path.is_dir():
        try:
            pending_jpg_count = sum(
                1 for n in incoming_path.iterdir()
                if _jpg_re.search(n.name) and n.is_file()
            )
        except OSError:
            pending_jpg_count = 0

    return {
        "projectDir": str(root),
        "specimenCount": specimen_count,
        "resultCount": result_count,
        "pendingJpgCount": pending_jpg_count,
    }


def get_project_results(project_dir: str) -> dict:
    """Return a grouped listing of result TIF files under *project_dir*.

    Scans ``results/`` and ``results/freeform/`` for .tif/.tiff files,
    parses the 7-segment filename to extract the specimen UID, then
    groups the items by UID.  Files that cannot be parsed go into
    ``ungrouped``.

    Returns a dict matching the shape of server.js ``/api/project/results``::

        {
            "projectDir": str,
            "total": int,
            "groups": [{"uid": str, "items": [item, ...]}, ...],   # sorted by uid
            "ungrouped": [item, ...],
        }

    Each *item* is::

        {"path": str, "name": str, "uid": str | None,
         "seq": int | None, "mtime": str | None}

    Always returns a dict; never raises.

    Oracle: server.js GET /api/project/results (lines 2874-2922).
    """
    import re as _re
    import sqlite3 as _sqlite3

    root = Path(project_dir).resolve()
    results_root = root / RESULTS_DIR
    freeform_dir = results_root / "freeform"

    _tif_re = _re.compile(r"\.tiff?$", _re.IGNORECASE)

    # ── Name-parsing (mirrors parseTiffBasename in server.js) ─────────────────
    # Pattern: region-site-station-speciesId-seq-storage-dateSegment(.tif)
    # seq and storage can be absent (6-segment = uniqueId, 7-segment = resultId).
    _TIFF_7 = _re.compile(
        r"^([^-]+)-([^-]+)-([^-]+)-([^-]+)-(\d+)-([^-]+)-([^-.]+)\.tiff?$",
        _re.IGNORECASE,
    )
    _TIFF_6 = _re.compile(
        r"^([^-]+)-([^-]+)-([^-]+)-([^-]+)-([^-]+)-([^-.]+)\.tiff?$",
        _re.IGNORECASE,
    )

    def _parse(name: str):
        """Return (uid, seq) or (None, None)."""
        m = _TIFF_7.match(name)
        if m:
            province, site, station, species_id, seq_s, storage, date_seg = m.groups()
            uid = f"{province}-{site}-{station}-{species_id}-{storage}-{date_seg}"
            try:
                return uid, int(seq_s)
            except (ValueError, TypeError):
                return uid, None
        m = _TIFF_6.match(name)
        if m:
            province, site, station, species_id, storage, date_seg = m.groups()
            uid = f"{province}-{site}-{station}-{species_id}-{storage}-{date_seg}"
            return uid, None
        return None, None

    def _collect(directory: Path) -> list:
        if not directory.is_dir():
            return []
        items = []
        try:
            for entry in directory.iterdir():
                if not _tif_re.search(entry.name):
                    continue
                try:
                    if not entry.is_file():
                        continue
                except OSError:
                    continue
                uid, seq = _parse(entry.name)
                mtime = None
                try:
                    mtime = entry.stat().st_mtime
                    from datetime import datetime, timezone
                    mtime = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
                except OSError:
                    pass
                items.append({
                    "path": str(entry),
                    "name": entry.name,
                    "uid": uid,
                    "seq": seq,
                    "mtime": mtime,
                })
        except OSError:
            pass
        return items

    all_items = _collect(results_root) + _collect(freeform_dir)

    # Group by UID
    group_map: dict = {}
    ungrouped: list = []
    for item in all_items:
        if item["uid"]:
            group_map.setdefault(item["uid"], []).append(item)
        else:
            ungrouped.append(item)

    # Sort each group by seq, then sort groups by uid
    groups = [
        {
            "uid": uid,
            "items": sorted(items, key=lambda x: (x["seq"] is None, x["seq"] or 0)),
        }
        for uid, items in sorted(group_map.items())
    ]

    return {
        "projectDir": str(root),
        "total": len(all_items),
        "groups": groups,
        "ungrouped": ungrouped,
    }


def default_to_recent_real_project(user_projects_json_path: str) -> Optional[str]:
    """Return the directory of the most recent non-demo project, or None.

    Mirrors web ``defaultToRecentRealProject()``: picks the last project
    in the list that has a ``directory`` field and is not marked ``isDemo``.

    Oracle: app.js:2670 (defaultToRecentRealProject).
    """
    projects = list_projects(user_projects_json_path)
    for proj in reversed(projects):
        directory = proj.get("directory") or proj.get("dir") or ""
        if directory and not proj.get("isDemo", False):
            return directory
    return None
