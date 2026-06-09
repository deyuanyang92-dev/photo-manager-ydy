"""monitor_service.py — File monitor and JPG attribution logic.

Pure-function core (attribute_jpg, scan_project) + DB persistence for
firstSeenAt.  Mirrors monitor-service.js attribution algorithm exactly.

Oracle: monitor-service.js lines 56, 101–116, 292–401, 354–366.

§3.5 Attribution priority:
  P0: explicitUnassigns  → None  (black-list, overrides everything)
  P1: grouping pathToUid → uid   (explicit group membership)
  P2: manual-assign      → uid   (assignToUid map)
  P3: activation window  → uid   (last activation whose eventAt ≤ firstSeenAt)

CRITICAL: comparison uses firstSeenAt (system first-seen time), NOT file
mtime.  Old files (mtime earlier than activation) must still be attributed
correctly once they arrive in the watched directory.
Oracle comment: monitor-service.js:107-109.
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.utils.path_utils import normalize_path, is_wsl_runtime


# Path.resolve() hits the filesystem (an lstat per path component + symlink
# walk). scan_project runs every 2 s and resolves every JPG twice, so the same
# paths are re-resolved hundreds of times a second — the dominant scan cost.
# Symlink topology is static within a session, so memoise by raw path string.
_RESOLVE_CACHE: dict[str, str] = {}


def _resolved(path: str) -> str:
    """Cached ``str(Path(path).resolve())``."""
    r = _RESOLVE_CACHE.get(path)
    if r is None:
        r = str(Path(path).resolve())
        _RESOLVE_CACHE[path] = r
    return r


# ── Public data classes ───────────────────────────────────────────────────────

@dataclass
class FileEntry:
    """Single file entry, mirrors the JS fileStatEntry shape."""
    name: str
    path: str
    kind: str          # "jpg" | "tiff" | "zip" | "other"
    size: int
    mtime: str         # ISO-8601
    first_seen_at: Optional[str] = None
    attributed_specimen_id: Optional[str] = None
    composed_tiff: Optional[str] = None
    naming_ok: Optional[bool] = None
    basename: Optional[str] = None
    has_zip: Optional[bool] = None
    detail: str = ""
    is_grouped: bool = False


@dataclass
class ScanResult:
    project_dir: str
    jpg_files: list[FileEntry] = field(default_factory=list)
    tiff_files: list[FileEntry] = field(default_factory=list)
    zip_files: list[FileEntry] = field(default_factory=list)
    other_files: list[FileEntry] = field(default_factory=list)
    pending_count: int = 0
    archived_jpg_count: int = 0
    processed_tiff_count: int = 0
    incoming_jpg_dir: str = ""
    results_dir: str = ""


@dataclass
class AttributionCtx:
    """Pre-built attribution context; passed to attribute_jpg.

    Fields mirror the return value of buildAttribution() in monitor-service.js.
    """
    # P0: explicit unassign blacklist (resolved absolute paths)
    explicit_unassigns: set = field(default_factory=set)
    # P1: grouping-confirmed path → uid
    path_to_uid: dict = field(default_factory=dict)
    # P2: manual-assign path → uid
    assign_to_uid: dict = field(default_factory=dict)
    # P3: sorted activation events [{specimenUniqueId, eventAt}]
    activations: list = field(default_factory=list)


# ── Pure attribution function ─────────────────────────────────────────────────

def attribute_jpg(
    entry: FileEntry,
    attr: AttributionCtx,
) -> Optional[str]:
    """Return the attributed specimen UID for *entry*, or None.

    4-level priority — mirrors monitor-service.js:101-116 exactly.

    P0 – explicitUnassigns: if the resolved path is in the blacklist, return
         None immediately (no further lookup).
    P1 – grouping pathToUid: if the path appears in a confirmed group, return
         that group's uid.
    P2 – manual-assign assignToUid: explicit one-shot override.
    P3 – activation time window: walk sorted activations; keep last one whose
         eventAt ≤ entry.first_seen_at.

    IMPORTANT: uses first_seen_at NOT mtime.  The Oracle comment at
    monitor-service.js:107-109 explains: old photos (mtime < activation) would
    never be attributed if we compared against mtime.
    """
    rp = _resolved(entry.path)

    # P0: blacklist → None
    if rp in attr.explicit_unassigns:
        return None

    # P1: grouping membership
    if rp in attr.path_to_uid:
        return attr.path_to_uid[rp]

    # P2: manual-assign
    if rp in attr.assign_to_uid:
        return attr.assign_to_uid[rp]

    # P3: activation time window
    # Use firstSeenAt; fall back to mtime if firstSeenAt not yet recorded.
    # Oracle: monitor-service.js:110 — `const arrival = entry.firstSeenAt || entry.mtime`
    arrival = entry.first_seen_at or entry.mtime
    if not arrival:
        return None

    chosen = None
    for activation in attr.activations:
        event_at = activation.get("eventAt", "")
        if event_at <= arrival:
            chosen = activation.get("specimenUniqueId")
        else:
            break  # activations are sorted ascending; can stop early
    return chosen


# ── DB helpers for firstSeenAt persistence ────────────────────────────────────

def ensure_seen_files_table(db: sqlite3.Connection) -> None:
    """Create seen_files table if absent (matches schema.sql)."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_files (
            name TEXT PRIMARY KEY,
            first_seen_at TEXT
        )
        """
    )
    db.commit()


def get_first_seen_at(db: sqlite3.Connection, name: str) -> Optional[str]:
    """Return persisted firstSeenAt for *name*, or None."""
    row = db.execute(
        "SELECT first_seen_at FROM seen_files WHERE name = ?", (name,)
    ).fetchone()
    return row[0] if row else None


def set_first_seen_at(db: sqlite3.Connection, name: str, ts: str) -> None:
    """Insert firstSeenAt for *name* (INSERT OR IGNORE — never overwrite)."""
    db.execute(
        "INSERT OR IGNORE INTO seen_files (name, first_seen_at) VALUES (?, ?)",
        (name, ts),
    )
    db.commit()


# ── Scan helpers ──────────────────────────────────────────────────────────────

def _iso_mtime(full_path: str) -> str:
    """Return ISO-8601 mtime for a path."""
    st = os.stat(full_path)
    return datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()


def _file_entry(full_path: str, name: str, kind: str, detail: str = "") -> FileEntry:
    """Build a FileEntry from a real file."""
    st = os.stat(full_path)
    return FileEntry(
        name=name,
        path=full_path,
        kind=kind,
        size=st.st_size,
        mtime=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
        detail=detail,
    )


def _list_jpgs(
    jpg_dir: str,
    archived_set: set,
    detail_prefix: str,
    include_hidden: bool = False,
) -> list[FileEntry]:
    """List JPG files in *jpg_dir*, skipping archived names.

    Mirrors monitor-service.js:185-203.
    """
    result = []
    if not os.path.isdir(jpg_dir):
        return result
    for name in os.listdir(jpg_dir):
        if not include_hidden and name.startswith("."):
            continue
        if not re.search(r"\.jpe?g$", name, re.IGNORECASE):
            continue
        if name in archived_set:
            continue
        full = os.path.join(jpg_dir, name)
        try:
            if os.path.isfile(full):
                e = _file_entry(full, name, "jpg", detail_prefix + " · 未关联原片")
                result.append(e)
        except OSError:
            pass
    return result


def _list_tiffs(
    tiff_dir: str,
    processed_set: set,
    detail_prefix: str,
    skip_processed: bool = True,
    skip_if_zip: bool = True,
    include_hidden: bool = False,
) -> list[FileEntry]:
    """List TIFF files in *tiff_dir*.

    Mirrors monitor-service.js:205-243.
    """
    result = []
    if not os.path.isdir(tiff_dir):
        return result
    for name in os.listdir(tiff_dir):
        if not include_hidden and name.startswith("."):
            continue
        if not re.search(r"\.tiff?$", name, re.IGNORECASE):
            continue
        base = Path(name).stem
        if skip_processed and base in processed_set:
            continue
        if skip_if_zip:
            zip_path = os.path.join(tiff_dir, base + ".zip")
            if os.path.isfile(zip_path):
                continue
        full = os.path.join(tiff_dir, name)
        try:
            if os.path.isfile(full):
                e = _file_entry(full, name, "tiff", detail_prefix + " · TIFF")
                e.basename = base
                # Check co-located zip
                e.has_zip = os.path.isfile(os.path.join(tiff_dir, base + ".zip"))
                result.append(e)
        except OSError:
            pass
    return result


# ── scan_project — pure function (IO via OS + db) ─────────────────────────────

def scan_project(
    project_dir: str,
    db: sqlite3.Connection,
    *,
    incoming_subdir: str = "incoming-jpg",
    results_subdir: str = "results",
    attr: Optional[AttributionCtx] = None,
    archived_names: Optional[set] = None,
    processed_basenames: Optional[set] = None,
) -> ScanResult:
    """Scan *project_dir* and return a ScanResult.

    - Lists JPGs in incoming-jpg/, TIFFs in results/.
    - Assigns / updates firstSeenAt in the DB (seen_files table).
      Oracle: monitor-service.js:354-366 — first time a JPG is seen,
      record now; never overwrite existing record.
    - Runs attribution for each JPG using *attr* (if provided).

    NOTE: This function performs real IO (os.listdir, stat, DB writes).
    It is "pure" in the sense that its observable output (ScanResult) is
    deterministic given the same filesystem state; side-effects are only
    DB updates to seen_files.
    """
    resolved = str(Path(normalize_path(project_dir)).resolve())
    if not os.path.isdir(resolved):
        raise FileNotFoundError(f"项目目录不存在: {resolved}")

    incoming_dir = os.path.join(resolved, incoming_subdir)
    results_dir_path = os.path.join(resolved, results_subdir)

    archived = archived_names or set()
    processed = processed_basenames or set()

    # Ensure table exists
    ensure_seen_files_table(db)

    # List JPGs
    jpg_files = _list_jpgs(incoming_dir, archived, incoming_subdir + "/")
    # List TIFFs — Oracle: both filters OFF for results/ so all TIFFs visible
    tiff_files = _list_tiffs(
        results_dir_path, processed, results_subdir + "/",
        skip_processed=False, skip_if_zip=False,
    )

    # ── firstSeenAt: persist on first sight, never overwrite ─────────────────
    # Oracle: monitor-service.js:356-366
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    for f in jpg_files:
        existing = get_first_seen_at(db, f.name)
        if existing is None:
            set_first_seen_at(db, f.name, now_iso)
            f.first_seen_at = now_iso
        else:
            f.first_seen_at = existing

    # ── Attribution ───────────────────────────────────────────────────────────
    if attr is not None:
        for f in jpg_files:
            f.attributed_specimen_id = attribute_jpg(f, attr)

    # ── is_grouped: mark JPGs that appear in grouping table ──────────────────
    # Query all jpg_paths from rows where uid IS NOT NULL; parse JSON lists.
    grouped_paths: set[str] = set()
    try:
        rows = db.execute(
            "SELECT jpg_paths FROM grouping WHERE uid IS NOT NULL"
        ).fetchall()
        for row in rows:
            raw = row[0] if isinstance(row, (tuple, list)) else row["jpg_paths"]
            if raw:
                import json as _json
                for p in _json.loads(raw):
                    if p:
                        grouped_paths.add(_resolved(p))
    except Exception:
        pass  # table may not exist; degrade gracefully

    for f in jpg_files:
        f.is_grouped = _resolved(f.path) in grouped_paths

    # Sort by mtime desc (mirrors monitor-service.js:368-370)
    jpg_files.sort(key=lambda f: f.mtime, reverse=True)
    tiff_files.sort(key=lambda f: f.mtime, reverse=True)

    result = ScanResult(
        project_dir=resolved,
        jpg_files=jpg_files,
        tiff_files=tiff_files,
        incoming_jpg_dir=incoming_dir,
        results_dir=results_dir_path,
        pending_count=len(jpg_files) + len(tiff_files),
        archived_jpg_count=len(archived),
        processed_tiff_count=len(processed),
    )
    return result
