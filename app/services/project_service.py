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
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

from app.utils.path_utils import default_registry, localize_path, normalize_path

logger = logging.getLogger(__name__)

# ── Directory name constants ───────────────────────────────────────────────────
# Mirrors project-paths.js constants
INCOMING_JPG_DIR = "incoming-jpg"
LEGACY_INCOMING_JPG_DIR = "新拍JPG"
RESULTS_DIR = "results"
DATA_SUBDIR = "_data"

LEGACY_ROOT_METADATA_FILES: frozenset[str] = frozenset({
    ".project-specimens.json",
})

LEGACY_INCOMING_METADATA_FILES: frozenset[str] = frozenset({
    ".specimen-log.json",
})

_PROJECT_LIST_CACHE: dict[str, tuple[tuple, list[dict]]] = {}

_PROJECT_PATH_FIELDS = (
    "directory",
    "dir",
    "root",
    "incomingJpgDir",
    "resultsDir",
    "dataDir",
)


def _localize_project_entry(project: dict) -> dict:
    entry = dict(project)
    for key in _PROJECT_PATH_FIELDS:
        value = entry.get(key)
        if isinstance(value, str) and value:
            entry[key] = localize_path(value)
    return entry


def _path_identity(path: str) -> str:
    try:
        return normalize_path(path)
    except (OSError, RuntimeError, ValueError):
        return localize_path(path)


def _same_path(left: str, right: str) -> bool:
    return _path_identity(left) == _path_identity(right)


# ── Low-level helpers ──────────────────────────────────────────────────────────

def ensure_project_dirs(project_dir: str, *, create_root: bool = False) -> dict:
    """Create the standard subdirectory layout under project_dir.

    Creates:  incoming-jpg/  results/  _data/

    Idempotent — safe to call multiple times. Returns a dict with resolved paths.

    ``create_root=False`` (DEFAULT) requires the project ROOT to already exist —
    we only fill in missing *subdirectories* inside it. If the root is gone (the
    drive is unmounted), this raises :class:`ProjectUnavailableError` instead of
    silently re-creating the whole tree on a phantom path (the data-loss bug).

    ``create_root=True`` is for *new* project creation: it may make the leaf
    folder, but only when its parent volume is present.

    Oracle: project-paths.js::ensureProjectDirs
    """
    from app.services.project_paths import (
        require_creatable_parent,
        require_project_root,
    )

    if create_root:
        root = require_creatable_parent(project_dir)
        root.mkdir(parents=True, exist_ok=True)
    else:
        root = require_project_root(project_dir)

    incoming = root / INCOMING_JPG_DIR
    results = root / RESULTS_DIR
    data = root / DATA_SUBDIR

    incoming.mkdir(exist_ok=True)
    results.mkdir(exist_ok=True)
    data.mkdir(exist_ok=True)
    migrate_legacy_metadata(root)

    return {
        "projectDir": str(root),
        "incomingJpgDir": str(incoming),
        "resultsDir": str(results),
        "dataDir": str(data),
    }


def migrate_legacy_metadata(project_dir: str | Path) -> list[str]:
    """Move old web-era dot JSON files into ``_data/legacy``.

    Modern projects keep their state in ``_data/project.db`` and
    ``_data/state.json``. Hidden JSON files in the project root or
    ``incoming-jpg`` are legacy sidecars; keeping them beside user folders makes
    the project look messy in Explorer, and ``incoming-jpg`` should contain only
    photo inputs.
    """
    root = Path(project_dir)
    data = root / DATA_SUBDIR
    legacy = data / "legacy"
    moved: list[str] = []

    def unique_dest(base: Path) -> Path:
        if not base.exists():
            return base
        stem = base.stem
        suffix = base.suffix
        parent = base.parent
        for idx in range(1, 1000):
            candidate = parent / f"{stem}.{idx}{suffix}"
            if not candidate.exists():
                return candidate
        return parent / f"{base.name}.{uuid.uuid4().hex}"

    def move_file(src: Path, dest_name: str) -> None:
        if not src.is_file():
            return
        legacy.mkdir(parents=True, exist_ok=True)
        dest = unique_dest(legacy / dest_name)
        src.replace(dest)
        moved.append(str(dest))

    for name in LEGACY_ROOT_METADATA_FILES:
        move_file(root / name, name)
    incoming = root / INCOMING_JPG_DIR
    for name in LEGACY_INCOMING_METADATA_FILES:
        move_file(incoming / name, f"{INCOMING_JPG_DIR}.{name.lstrip('.')}")

    return moved


def resolve_incoming_jpg_dir(project_dir: str) -> str:
    """Return the path to the incoming-JPG directory.

    Prefers modern 'incoming-jpg' subdir; falls back to legacy '新拍JPG'
    if the modern dir is absent but the legacy dir exists.
    If neither exists, returns the modern path (not yet created).

    Oracle: project-paths.js::resolveIncomingJpgDir
    """
    root = Path(normalize_path(project_dir))
    modern = root / INCOMING_JPG_DIR
    legacy = root / LEGACY_INCOMING_JPG_DIR

    if modern.exists():
        return str(modern)
    if legacy.exists():
        return str(legacy)
    return str(modern)


def resolve_results_dir(project_dir: str) -> str:
    """Return the path to the results directory.

    Oracle: project-paths.js::resolveResultsDir
    """
    root = Path(normalize_path(project_dir))
    return str(root / RESULTS_DIR)


# ── Public service API ─────────────────────────────────────────────────────────

def create_project(name: str, directory: str) -> dict:
    """Create a new project at *directory* and return its descriptor dict.

    - Generates a unique ID.
    - Creates the standard subdirectory layout.
    - Does NOT persist to user_projects.json (caller responsibility).

    Oracle: server.js project creation logic.
    """
    resolved = normalize_path(directory)
    dirs = ensure_project_dirs(resolved, create_root=True)
    # Materialise the db now (create=True) so later background reads can use the
    # strict open_project_db(create=False) path without fabricating anything.
    from app.db.db_manager import open_project_db
    open_project_db(resolved, create=True)

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
    resolved = normalize_path(directory)
    # Entering/claiming a workspace: the folder must already exist (create_root
    # =False). A gone drive raises ProjectUnavailableError rather than rebuilding
    # a ghost. The db is then materialised (create=True) so claiming an existing
    # but un-workspaced folder works, while a missing volume still refuses.
    dirs = ensure_project_dirs(resolved, create_root=False)
    from app.db.db_manager import open_project_db
    db = open_project_db(resolved, create=True)

    # Recover archive-zip pointers for results compressed by an older build /
    # the web prototype (zip on disk but archive_zip never recorded), so they
    # show "已压缩" instead of "尚未压缩". Best-effort — never block opening the
    # workspace on a backfill hiccup.
    try:
        from app.services.grouping_service import backfill_archive_zips
        backfill_archive_zips(db)
    except Exception:
        logger.debug("archive_zip 回填失败（忽略，不阻塞打开工作区）", exc_info=True)

    # Register the project root so assert_safe() passes for its children
    default_registry.register_root(resolved)

    return {
        "dir": resolved,
        "directory": resolved,
        "incomingJpgDir": dirs["incomingJpgDir"],
        "resultsDir": dirs["resultsDir"],
        "dataDir": dirs["dataDir"],
    }


def enter_photo_folder(ctx, directory: str) -> str:
    """Use an existing photo directory without turning it into a project.

    Standalone grouping only needs a local database.  Keep that database beside
    the photos in ``_data/project.db`` but do not scaffold the project-only
    ``incoming-jpg`` and ``results`` directories and do not add the folder to
    the project catalogue/recent-project list.
    """
    from app.db.db_manager import open_project_db
    from app.services.project_paths import require_project_root

    root = require_project_root(directory)
    resolved = str(root.resolve())
    open_project_db(resolved, create=True)
    default_registry.register_root(resolved)
    ctx.current_project_dir = resolved
    ctx.current_project_root = resolved
    return resolved


def list_projects(user_projects_json_path: str) -> list:
    """Read the user_projects.json file and return the list of project dicts.

    Returns an empty list if the file is missing or contains no projects.
    Does NOT raise on missing file.

    Oracle: server.js::userProjectsRead / GET /api/user-projects
    """
    path = Path(localize_path(user_projects_json_path))
    if not path.exists():
        return []
    key = str(path.resolve())
    sig = _file_signature(path)
    hit = _PROJECT_LIST_CACHE.get(key)
    if hit and hit[0] == sig:
        return [dict(p) for p in hit[1]]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        projects = data.get("projects", [])
        if not isinstance(projects, list):
            projects = []
        projects = [_localize_project_entry(p) for p in projects if isinstance(p, dict)]
        _PROJECT_LIST_CACHE[key] = (sig, [dict(p) for p in projects])
        return projects
    except (json.JSONDecodeError, OSError):
        return []


def clear_project_list_cache(path: str | None = None) -> None:
    if path is None:
        _PROJECT_LIST_CACHE.clear()
        return
    try:
        key = str(Path(localize_path(path)).resolve())
    except OSError:
        key = str(path)
    _PROJECT_LIST_CACHE.pop(key, None)


def _file_signature(path: Path) -> tuple:
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return (0, 0)


def default_user_projects_json_path() -> str:
    """Return the app-local ``data/user_projects.json`` path (the writable
    recent-workspaces list shared by 项目树 and 项目总览)."""
    repo_root = Path(__file__).resolve().parents[2]
    return str(repo_root / "data" / "user_projects.json")


def resolve_user_projects_json() -> str:
    """Return the ``user_projects.json`` path to READ.

    Priority:
      1. app-local ``data/user_projects.json`` (writable app data)
      2. the web-prototype ``data/user_projects.json`` (real working data)
    Falls back to (1) even if it doesn't exist yet.
    """
    app_local = Path(default_user_projects_json_path())
    if app_local.exists():
        return str(app_local)
    web_proto = (
        app_local.parents[2]
        / "photo-platform-ydy" / "prototype-photo-gui" / "data" / "user_projects.json"
    )
    if web_proto.exists():
        return str(web_proto)
    return str(app_local)


def load_user_projects(path: str | Path | None = None) -> list[dict]:
    """Load the recent-projects list from user_projects.json.  [] on any error."""
    return list_projects(str(path or resolve_user_projects_json()))


def save_user_projects(projects: list[dict]) -> None:
    """Persist the project list to the app-local user_projects.json."""
    out = Path(default_user_projects_json_path())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"version": 1, "projects": projects}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _workspace_display_name(resolved: str, root: Optional[str]) -> str:
    """Human-readable name for a workspace in the recent list.

    With a survey root, show the path relative to that root prefixed by the
    root's own name (``雷州岛 / 断面a``) so identically-named 断面a folders in
    different regions never collide. Without a root, fall back to the folder
    name.
    """
    if not root:
        return Path(resolved).name
    rootp = Path(normalize_path(root))
    try:
        rel = Path(resolved).relative_to(rootp)
    except ValueError:
        return Path(resolved).name
    return " / ".join([rootp.name, *rel.parts])


def record_recent_workspace(
    user_projects_json_path: str, path: str, root: Optional[str] = None
) -> list:
    """Append *path* to the recent-workspaces list (``user_projects.json``).

    De-dupes by resolved directory so a workspace entered repeatedly — whether
    from the folder tree or the flat overview — appears exactly once. Existing
    entries are preserved. This is what makes 项目树 and 项目总览 share a single
    source of truth: entering any tree node surfaces it in the overview table.

    Returns the full project list after the update.
    """
    resolved = normalize_path(path)
    projects = list_projects(user_projects_json_path)
    if not any(_same_path(p.get("directory") or p.get("dir") or "", resolved) for p in projects):
        entry = {
            "id": str(uuid.uuid4()),
            "name": _workspace_display_name(resolved, root),
            "directory": resolved,
            "dir": resolved,
        }
        if root:
            entry["root"] = normalize_path(root)
        projects.append(entry)
        out = Path(localize_path(user_projects_json_path))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps({"version": 1, "projects": projects}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        clear_project_list_cache(user_projects_json_path)
    return projects


def save_project_descriptor(
    user_projects_json_path: str,
    project: dict,
    root: Optional[str] = None,
    existing_projects: Optional[list[dict]] = None,
) -> list:
    """Upsert a full project descriptor into ``user_projects.json``.

    ``record_recent_workspace`` is intentionally path-first for tree nodes. New
    project creation already has richer metadata (name, projectCode, location,
    collector, year, dateRange); this helper keeps those fields instead of
    reducing the project to only a recent path.
    """
    raw_dir = project.get("directory") or project.get("dir") or ""
    if not raw_dir:
        return list_projects(user_projects_json_path)

    resolved = normalize_path(raw_dir)
    entry = dict(project)
    has_explicit_id = bool(entry.get("id"))
    entry["directory"] = resolved
    entry["dir"] = resolved
    entry.setdefault("name", _workspace_display_name(resolved, root))
    if root:
        entry["root"] = normalize_path(root)

    projects = (
        [_localize_project_entry(p) for p in existing_projects]
        if existing_projects is not None
        else list_projects(user_projects_json_path)
    )
    sync_entry: dict | None = None
    sync_fill_empty_only = True
    for idx, existing in enumerate(projects):
        existing_dir = existing.get("directory") or existing.get("dir") or ""
        if existing_dir and _same_path(existing_dir, resolved):
            merged = dict(existing)
            if not has_explicit_id:
                entry.pop("id", None)
            merged.update({k: v for k, v in entry.items() if v is not None})
            merged["directory"] = resolved
            merged["dir"] = resolved
            projects[idx] = merged
            sync_entry = merged
            sync_fill_empty_only = True
            break
    else:
        entry.setdefault("id", str(uuid.uuid4()))
        projects.append(entry)
        sync_entry = entry
        sync_fill_empty_only = False

    out = Path(localize_path(user_projects_json_path))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"version": 1, "projects": projects}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    clear_project_list_cache(user_projects_json_path)
    if sync_entry is not None:
        sync_project_descriptor_to_settings(
            sync_entry,
            fill_empty_only=sync_fill_empty_only,
        )
    return projects


def sync_project_descriptor_to_settings(
    project: dict, *, fill_empty_only: bool = False
) -> None:
    """Persist creation-dialog metadata into the workspace project settings.

    ``user_projects.json`` is only the app's recent-project index.  Project
    identity belongs in the workspace database too, otherwise the settings drawer
    opens with empty project fields right after creation.
    """
    raw_dir = project.get("directory") or project.get("dir") or ""
    if not raw_dir:
        return

    from app.db.db_manager import open_project_db
    from app.services import project_settings_service as pss

    resolved = normalize_path(raw_dir)
    if not Path(resolved).is_dir():
        return
    db = open_project_db(resolved, create=True)
    meta = pss.load_setting(db, "project_meta", pss.DEFAULT_PROJECT_META)
    field_map = {
        "project_code": project.get("projectCode") or project.get("project_code"),
        "name": project.get("name"),
        "year": project.get("year"),
        "date_range": project.get("dateRange") or project.get("date_range"),
        "location": project.get("location"),
        "photo_location": project.get("photoLocation") or project.get("photo_location"),
    }
    for key, value in field_map.items():
        text = str(value or "").strip()
        if text and (not fill_empty_only or not str(meta.get(key) or "").strip()):
            meta[key] = text
    pss.save_setting(db, "project_meta", meta)

    collector = str(project.get("collector") or "").strip()
    if collector:
        personnel = pss.load_setting(db, "personnel", pss.DEFAULT_PERSONNEL)
        if not fill_empty_only or not str(personnel.get("collector") or "").strip():
            personnel["collector"] = collector
        pss.save_setting(db, "personnel", personnel)


def backfill_project_settings_from_recent(
    user_projects_json_path: str, project_dir: str
) -> None:
    """Backfill empty workspace project settings from the recent-project record."""
    resolved = normalize_path(project_dir)
    for project in list_projects(user_projects_json_path):
        existing_dir = project.get("directory") or project.get("dir") or ""
        if existing_dir and _same_path(existing_dir, resolved):
            sync_project_descriptor_to_settings(project, fill_empty_only=True)
            return


def enter_workspace(
    ctx,
    path: str,
    root: Optional[str] = None,
    *,
    projects_json_path: Optional[str] = None,
) -> str:
    """Single, unified workspace-entry path used by EVERY caller.

    Guarantees a consistent post-condition regardless of where the user entered
    from (folder tree, flat overview, recent list):

      1. Standard subdir layout exists (``open_project``).
      2. ``ctx.current_project_dir`` = the workspace.
      3. ``ctx.current_project_root`` = *root* (or the workspace itself when no
         root is given) — so settings inheritance is ALWAYS bounded and never
         silently walks to the filesystem root.

    When *projects_json_path* is supplied, the workspace is also recorded into
    the recent list (see :func:`record_recent_workspace`).

    Returns the resolved workspace path.
    """
    resolved = normalize_path(path)
    # Unavailability (drive unmounted / path gone) MUST surface — do not activate
    # a dead path, or the workbench reads a ghost. Other minor errors are
    # tolerated as before.
    import sqlite3
    from app.db.db_manager import is_database_locked
    from app.services.project_paths import ProjectUnavailableError
    try:
        open_project(resolved)
    except ProjectUnavailableError:
        raise
    except sqlite3.Error as exc:
        if is_database_locked(exc):
            raise
        raise
    ctx.current_project_dir = resolved
    resolved_root = normalize_path(root) if root else resolved
    ctx.current_project_root = resolved_root
    if root:
        if resolved_root != resolved:
            from app.services.project_catalog_service import register_workspace
            register_workspace(
                resolved_root,
                resolved,
                role="workspace",
                name=_workspace_display_name(resolved, root),
            )
    if projects_json_path:
        try:
            record_recent_workspace(projects_json_path, resolved, root)
            backfill_project_settings_from_recent(projects_json_path, resolved)
        except Exception:
            pass
    return resolved


def seed_region_settings(
    region_dir: str,
    *,
    province: str = "",
    site: str = "",
    collector: str = "",
    photographer: str = "",
    identifier: str = "",
    meta: Optional[dict] = None,
) -> str:
    """Scaffold a 调查区域 root: create its anchor ``_data/project.db`` and store
    region-level settings (地区/样地/人员) once, so every 断面 workspace beneath
    it inherits them via ``project_settings_service.get_effective``.

    This is the "set it once at the region, never re-type per 断面" path. Only
    non-empty values are written, so calling it again to amend is safe.
    Returns the resolved region directory.
    """
    from app.db.db_manager import open_project_db
    from app.services import project_settings_service as pss

    resolved = normalize_path(region_dir)
    ensure_project_dirs(resolved, create_root=True)
    db = open_project_db(resolved, create=True)

    code_labels = pss.load_setting(db, "code_labels", pss.DEFAULT_CODE_LABELS)
    if province:
        code_labels["province"] = province
    if site:
        code_labels["site"] = site
    pss.save_setting(db, "code_labels", code_labels)

    personnel = pss.load_setting(db, "personnel", pss.DEFAULT_PERSONNEL)
    if collector:
        personnel["collector"] = collector
    if photographer:
        personnel["photographer"] = photographer
    if identifier:
        personnel["identifier"] = identifier
    pss.save_setting(db, "personnel", personnel)

    if meta:
        pm = pss.load_setting(db, "project_meta", pss.DEFAULT_PROJECT_META)
        pm.update({k: v for k, v in meta.items() if v})
        pss.save_setting(db, "project_meta", pm)

    return resolved


# Memoised summaries, keyed by resolved project dir. Each entry is
# ``(signature, result)``; the signature is a cheap stat() fingerprint of the
# inputs (db + wal mtime/size, plus the mtimes of the scanned dirs). A cache hit
# skips the expensive sqlite COUNT + iterdir over potentially thousands of JPGs —
# the overview re-scans every project on EVERY tab activation, so this turns a
# multi-hundred-ms freeze into a handful of stat() calls. Any real change (new
# JPG/TIFF, specimen insert → WAL grows) shifts the signature and forces a
# recompute, so a cache hit never returns stale counts.
_SUMMARY_CACHE: dict[str, tuple] = {}


def _summary_signature(db_path: Path, results_root: Path, incoming_path: Path) -> tuple:
    def _file_stat_signature(p: Path):
        # db/wal files: mtime + size. WAL grows on every insert, so an inserted
        # specimen always shifts this even before checkpoint.
        try:
            s = p.stat()
            return (s.st_mtime_ns, s.st_size)
        except OSError:
            return None

    def _directory_signature(p: Path):
        # Scanned dirs: mtime + entry COUNT. Count is read via os.scandir (one
        # readdir, NO per-entry stat) and is what makes the cache correct —
        # directory mtime resolution can be too coarse to catch a file added in
        # the same second, but the entry count changes immediately on add/remove.
        try:
            s = p.stat()
        except OSError:
            return None
        try:
            n = sum(1 for _ in os.scandir(p))
        except OSError:
            n = -1
        return (s.st_mtime_ns, n)

    wal = db_path.with_name(db_path.name + "-wal")
    return (
        _file_stat_signature(db_path), _file_stat_signature(wal),
        _directory_signature(results_root), _directory_signature(results_root / "freeform"),
        _directory_signature(incoming_path),
    )


def get_project_summary(project_dir: str) -> dict:
    """Return live statistics for *project_dir*: specimen count, result TIFF count,
    pending JPG count.

    Oracle: server.js GET /api/project/summary (lines 2831-2870).

    Counts:
      specimenCount  — rows in ``_data/project.db`` specimens table (or 0 if absent).
      resultCount    — .tif/.tiff files in ``results/`` and ``results/freeform/``.
      pendingJpgCount— .jpg/.jpeg files in ``incoming-jpg/`` (or legacy ``新拍JPG/``).

    Result is memoised by a stat() signature (see ``_SUMMARY_CACHE``) so repeat
    calls for an unchanged project are near-free. Always returns a dict; never
    raises.
    """
    import re
    import sqlite3 as _sqlite3

    root = Path(normalize_path(project_dir))
    db_path = root / DATA_SUBDIR / "project.db"
    results_root = root / RESULTS_DIR
    incoming_path = Path(resolve_incoming_jpg_dir(project_dir))

    sig = _summary_signature(db_path, results_root, incoming_path)
    cached = _SUMMARY_CACHE.get(str(root))
    if cached is not None and cached[0] == sig:
        return dict(cached[1])   # copy → callers can't mutate the cached dict

    # ── specimen count ────────────────────────────────────────────────────────
    specimen_count = 0
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

    result_count = _count_tifs(results_root) + _count_tifs(results_root / "freeform")

    # ── pending JPG count ─────────────────────────────────────────────────────
    pending_jpg_count = 0
    _jpg_re = re.compile(r"\.jpe?g$", re.IGNORECASE)
    if incoming_path.is_dir():
        try:
            pending_jpg_count = sum(
                1 for n in incoming_path.iterdir()
                if _jpg_re.search(n.name) and n.is_file()
            )
        except OSError:
            pending_jpg_count = 0

    result = {
        "projectDir": str(root),
        "specimenCount": specimen_count,
        "resultCount": result_count,
        "pendingJpgCount": pending_jpg_count,
    }
    _SUMMARY_CACHE[str(root)] = (sig, result)
    return dict(result)


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

    root = Path(normalize_path(project_dir))
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

    def parse_result_tiff_name(name: str):
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

    def collect_result_tiff_items(directory: Path) -> list:
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
                uid, seq = parse_result_tiff_name(entry.name)
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

    all_items = collect_result_tiff_items(results_root) + collect_result_tiff_items(freeform_dir)

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
