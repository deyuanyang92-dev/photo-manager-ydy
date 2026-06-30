"""grouping_service.py — Specimen grouping persistence and manipulation.

Mirrors server.js:3384–3392 (groupingRead/Write), db-utils grouping CRUD,
and server.js:3252-3330 grouping-tool save logic.

Key invariant: when saving groups, filter out "phantom" jpgPaths — paths
that no longer exist on disk.  Oracle: server.js grouping-tool save handler
(phantom jpg cleanup before write).

explicitUnassigns: a set of absolute paths the user has manually unassigned
from ALL specimens.  These paths take priority P0 in attribution.
Oracle: monitor-service.js:56.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# 临时分组(ad-hoc)的占位编号：没有激活标本编号时，“新组”暂存到这里。
# 它不是任何标本编号；保存/选择真实编号后可显式迁入目标编号。
# 输出文件名默认按组序(1.tif/2.tif)派生，而非 编号-序号。
ADHOC_GROUPING_UID = "~未命名"

_JPG_EXTS = {".jpg", ".jpeg"}
_TIFF_EXTS = {".tif", ".tiff"}
_ZIP_EXTS = {".zip"}
_SQLITE_LOCK_RETRY_DELAYS = (0.08, 0.16, 0.32, 0.64, 1.0)


def _is_sqlite_lock_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return isinstance(exc, sqlite3.OperationalError) and (
        "database is locked" in msg
        or "database table is locked" in msg
        or "database is busy" in msg
    )


def _rollback_quietly(db: sqlite3.Connection) -> None:
    try:
        db.rollback()
    except Exception:
        pass


def _run_with_sqlite_lock_retries(db: sqlite3.Connection, fn) -> None:
    """Run a short write transaction, retrying transient SQLite writer locks."""
    for attempt, delay in enumerate((*_SQLITE_LOCK_RETRY_DELAYS, None)):
        try:
            fn()
            return
        except sqlite3.OperationalError as exc:
            if delay is None or not _is_sqlite_lock_error(exc):
                raise
            _rollback_quietly(db)
            time.sleep(delay)


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class Group:
    """Single angle/batch group for a specimen.

    Mirrors the group object in grouping_confirmations.json.
    """
    group_index: int
    angle_label: str = ""
    jpg_paths: list[str] = field(default_factory=list)
    composed_tiff_path: Optional[str] = None
    status: Optional[str] = None      # "pending" | "composed" | "organized"
    source: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    result_sequence: Optional[int] = None
    archive_zip: Optional[str] = None
    retired_tiff_paths: list[str] = field(default_factory=list)
    # 用户可编辑的「输出命名」覆盖值（分组工具每组下方那个框）。空=自动派生
    # （合成→编号-组序；导入→TIF原名）。存进 raw_json，不动表结构。
    output_name: Optional[str] = None
    raw_json: Optional[str] = None    # full JSON blob fallback

    def to_dict(self) -> dict:
        return {
            "groupIndex": self.group_index,
            "angleLabel": self.angle_label,
            "jpgPaths": self.jpg_paths,
            "composedTiffPath": self.composed_tiff_path,
            "status": self.status,
            "source": self.source,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "resultSequence": self.result_sequence,
            "archiveZip": self.archive_zip,
            "retiredTiffPaths": self.retired_tiff_paths,
            "outputName": self.output_name,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Group":
        return cls(
            group_index=int(d.get("groupIndex", 0)),
            angle_label=d.get("angleLabel", ""),
            jpg_paths=list(d.get("jpgPaths") or []),
            composed_tiff_path=d.get("composedTiffPath"),
            status=d.get("status"),
            source=d.get("source"),
            created_at=d.get("createdAt"),
            updated_at=d.get("updatedAt"),
            result_sequence=d.get("resultSequence"),
            archive_zip=d.get("archiveZip"),
            retired_tiff_paths=list(d.get("retiredTiffPaths") or []),
            output_name=d.get("outputName"),
            raw_json=json.dumps(d, ensure_ascii=False),
        )


@dataclass
class SpecimenGrouping:
    """All groups for one specimen in one project."""
    uid: str
    groups: list[Group] = field(default_factory=list)


# ── DB helpers ────────────────────────────────────────────────────────────────

def _ensure_grouping_table(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS grouping (
            uid TEXT, group_index INTEGER,
            angle_label TEXT, jpg_paths TEXT, composed_tiff_path TEXT,
            status TEXT, source TEXT, created_at TEXT, updated_at TEXT,
            result_sequence INTEGER, archive_zip TEXT, retired_tiff_paths TEXT,
            raw_json TEXT,
            PRIMARY KEY (uid, group_index)
        )
    """)
    db.commit()


def _ensure_explicit_unassigns_table(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS explicit_unassigns (
            path TEXT PRIMARY KEY,
            created_at TEXT
        )
    """)
    db.commit()


# ── Load grouping ─────────────────────────────────────────────────────────────

def load_grouping(db: sqlite3.Connection, uid: str) -> SpecimenGrouping:
    """Load all groups for *uid* from the DB.

    Returns SpecimenGrouping with empty groups if uid not found.
    """
    _ensure_grouping_table(db)
    rows = db.execute(
        "SELECT * FROM grouping WHERE uid = ? ORDER BY group_index",
        (uid,),
    ).fetchall()

    groups = []
    for row in rows:
        raw = dict(row) if not isinstance(row, dict) else row
        # Decode JSON columns
        jpg_paths = json.loads(raw.get("jpg_paths") or "[]")
        retired = json.loads(raw.get("retired_tiff_paths") or "[]")
        # output_name 没有独立列 → 从 raw_json 取（向后兼容旧行无此字段）。
        output_name = None
        try:
            rj = raw.get("raw_json")
            if rj:
                output_name = (json.loads(rj) or {}).get("outputName")
        except Exception:
            output_name = None
        g = Group(
            group_index=raw.get("group_index", 0),
            angle_label=raw.get("angle_label", ""),
            jpg_paths=jpg_paths,
            composed_tiff_path=raw.get("composed_tiff_path"),
            status=raw.get("status"),
            source=raw.get("source"),
            created_at=raw.get("created_at"),
            updated_at=raw.get("updated_at"),
            result_sequence=raw.get("result_sequence"),
            archive_zip=raw.get("archive_zip"),
            retired_tiff_paths=retired,
            output_name=output_name,
            raw_json=raw.get("raw_json"),
        )
        groups.append(g)

    return SpecimenGrouping(uid=uid, groups=groups)


def archive_jpg_count(zip_path: str | None) -> int | None:
    """Return archived JPG count for both plain-JPG and legacy manifest ZIPs."""
    if not zip_path:
        return None
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            jpg_count = sum(
                1 for name in zf.namelist()
                if Path(name).suffix.lower() in _JPG_EXTS
            )
            if jpg_count:
                return jpg_count
            with zf.open("manifest.json") as fh:
                manifest = json.loads(fh.read().decode("utf-8"))
    except Exception:
        return None
    files = manifest.get("files") if isinstance(manifest, dict) else None
    return len(files) if isinstance(files, list) else None


def resolved_archive_zip(group: Group) -> str | None:
    """Return the registered ZIP or the same-stem ZIP beside the linked TIFF."""
    explicit = getattr(group, "archive_zip", None)
    if explicit:
        return explicit
    tiff_path = getattr(group, "composed_tiff_path", None)
    if not tiff_path:
        return None
    candidate = Path(tiff_path).with_suffix(".zip")
    return str(candidate) if candidate.is_file() else None


def result_path_key(path: str | None) -> str:
    """Return a stable comparison key for a filesystem result path."""
    if not path:
        return ""
    try:
        return str(Path(path).resolve()).casefold()
    except OSError:
        return str(path).casefold()


def clear_group_tiff_link(group: Group) -> None:
    """Clear TIFF/ZIP result state from a group while preserving JPG membership."""
    old_stem = Path(getattr(group, "composed_tiff_path", "") or "").stem
    group.composed_tiff_path = None
    group.archive_zip = None
    group.retired_tiff_paths = []
    if not list(getattr(group, "jpg_paths", None) or []):
        group.status = None
        group.source = None
    elif getattr(group, "status", None) in {"composed", "organized"}:
        group.status = "pending"
        if getattr(group, "source", None) in {"external-tif", "existing-result-pair"}:
            group.source = None
    if old_stem and getattr(group, "output_name", None) == old_stem:
        group.output_name = None


def deduplicate_tiff_links(groups: list[Group]) -> bool:
    """Keep one group per TIFF path; clear duplicate associations only."""
    seen: set[str] = set()
    changed = False
    for group in groups:
        key = result_path_key(getattr(group, "composed_tiff_path", None))
        if not key:
            continue
        if key in seen:
            clear_group_tiff_link(group)
            changed = True
            continue
        seen.add(key)
    return changed


def registered_result_paths(
    db: sqlite3.Connection | None,
    *,
    current_uid: str = "",
    current_groups: list[Group] | None = None,
) -> dict[str, str]:
    """Return result paths already claimed by grouping rows: path-key -> uid."""
    used: dict[str, str] = {}
    if db is not None:
        try:
            _ensure_grouping_table(db)
            rows = db.execute(
                "SELECT uid, composed_tiff_path, archive_zip FROM grouping"
            ).fetchall()
            for row in rows:
                uid = row["uid"] if hasattr(row, "keys") else row[0]
                for value in (
                    row["composed_tiff_path"] if hasattr(row, "keys") else row[1],
                    row["archive_zip"] if hasattr(row, "keys") else row[2],
                ):
                    key = result_path_key(value)
                    if key:
                        used[key] = str(uid or "")
        except Exception:
            pass
    for group in current_groups or []:
        for value in (
            getattr(group, "composed_tiff_path", None),
            getattr(group, "archive_zip", None),
        ):
            key = result_path_key(value)
            if key:
                used[key] = current_uid
    return used


def result_pair_candidates(results_dir: str | Path, used_paths: dict[str, str]) -> list[dict]:
    """Return same-stem TIFF+ZIP pairs from results/ with association state."""
    root = Path(results_dir)
    if not root.is_dir():
        return []
    by_stem: dict[str, dict[str, Path]] = {}
    for child in sorted(root.iterdir(), key=lambda p: p.name.casefold()):
        if not child.is_file():
            continue
        suffix = child.suffix.lower()
        if suffix not in (_TIFF_EXTS | _ZIP_EXTS):
            continue
        bucket = by_stem.setdefault(child.stem, {})
        if suffix in _TIFF_EXTS:
            bucket["tiff"] = child
        elif suffix in _ZIP_EXTS:
            bucket["zip"] = child

    out: list[dict] = []
    for stem, pair in by_stem.items():
        tiff = pair.get("tiff")
        zip_path = pair.get("zip")
        if not tiff or not zip_path:
            continue
        tiff_owner = used_paths.get(result_path_key(str(tiff)), "")
        zip_owner = used_paths.get(result_path_key(str(zip_path)), "")
        owner = tiff_owner or zip_owner
        try:
            mtime = max(tiff.stat().st_mtime, zip_path.stat().st_mtime)
        except OSError:
            mtime = 0
        out.append({
            "stem": stem,
            "tiff": str(tiff),
            "zip": str(zip_path),
            "associated_uid": owner,
            "associated": bool(owner),
            "mtime": mtime,
        })
    return sorted(out, key=lambda c: (-float(c.get("mtime") or 0), c["stem"].casefold()))


def is_composed_group(group: Group) -> bool:
    """Groups with finished output belong in the composed row workflow."""
    return bool(
        getattr(group, "composed_tiff_path", None)
        or getattr(group, "archive_zip", None)
        or getattr(group, "status", None) in {"composed", "organized"}
    )


def uid_core_key(uid: str) -> str:
    """Return the stable survey/site/station key used for fuzzy media matching."""
    parts = [p for p in re.split(r"[-_\s]+", str(uid or "").strip()) if p]
    if len(parts) >= 3:
        return "-".join(parts[:3]).upper()
    return str(uid or "").strip().upper()


def uid_parts(uid: str) -> list[str]:
    return [p.upper() for p in re.split(r"[-_\s]+", str(uid or "").strip()) if p]


def uid_match_terms(uid: str) -> list[str]:
    full = str(uid or "").strip().upper()
    core = uid_core_key(full)
    terms = []
    for value in (core, full):
        if value and value not in terms:
            terms.append(value)
    return terms


def uid_matches_name(uid: str, name: str) -> bool:
    haystack = str(name or "").casefold()
    terms = [t.casefold() for t in uid_match_terms(uid)]
    if not terms or not haystack:
        return False
    for term in terms:
        if term in haystack:
            return True
        tokens = [p for p in re.split(r"[-_\s]+", term) if p]
        if tokens and all(token in haystack for token in tokens):
            return True
    return False


def filename_tokens(path_or_name: str) -> list[str]:
    name = Path(str(path_or_name or "")).name.upper()
    return [p for p in re.split(r"[^A-Z0-9]+", name) if p]


def uid_filename_mismatch(uid: str, path_or_name: str) -> bool:
    """True when a media filename visibly belongs to a sibling specimen code."""
    parts = uid_parts(uid)
    if len(parts) < 3 or str(uid or "").strip() == ADHOC_GROUPING_UID:
        return False
    name = Path(str(path_or_name or "")).name
    if not name or uid_matches_name(uid, name):
        return False

    tokens = filename_tokens(name)
    if not tokens:
        return False

    # Strong signal: same survey/site prefix appears, but the specimen code differs.
    if parts[0] in tokens and parts[1] in tokens:
        return True

    # Secondary signal: same specimen-code family, e.g. BZC002 while current is BZC003.
    current_code = parts[2]
    family = re.match(r"([A-Z]+)", current_code)
    if not family:
        return False
    prefix = family.group(1)
    if not prefix:
        return False
    code_tokens = [
        t for t in tokens
        if re.fullmatch(r"[A-Z]{1,12}\d{2,}[A-Z0-9]*", t)
    ]
    return any(t != current_code and t.startswith(prefix) for t in code_tokens)


def clear_uid_mismatched_result_links(uid: str, groups: list[Group]) -> bool:
    """Remove result links that visibly belong to another specimen number."""
    changed = False
    for group in groups:
        tiff_path = getattr(group, "composed_tiff_path", None)
        if tiff_path and uid_filename_mismatch(uid, tiff_path):
            if (
                not list(getattr(group, "jpg_paths", None) or [])
                and not getattr(group, "archive_zip", None)
            ):
                continue
            clear_group_tiff_link(group)
            changed = True
            continue

        zip_path = getattr(group, "archive_zip", None)
        if zip_path and uid_filename_mismatch(uid, zip_path):
            group.archive_zip = None
            if getattr(group, "status", None) == "organized":
                group.status = "composed" if getattr(group, "composed_tiff_path", None) else None
            changed = True
    return changed


def is_blank_draft_group(group: Group) -> bool:
    """True for placeholder groups with no user data or result files."""
    return not any((
        list(getattr(group, "jpg_paths", None) or []),
        getattr(group, "composed_tiff_path", None),
        getattr(group, "archive_zip", None),
        getattr(group, "status", None),
        getattr(group, "source", None),
        getattr(group, "output_name", None),
    ))


def without_blank_draft_groups(groups: list[Group]) -> list[Group]:
    """Return *groups* without empty placeholder draft rows."""
    return [g for g in list(groups or []) if not is_blank_draft_group(g)]


def merge_adhoc_groups_for_uid(
    db: sqlite3.Connection,
    uid: str,
    groups: list[Group],
) -> list[Group]:
    """Merge temporary ad-hoc groups into a real specimen UID.

    Existing groups for *uid* keep their current indexes. Incoming ad-hoc groups
    are appended after the highest existing index so no persisted group is
    overwritten.
    """
    target_uid = str(uid or "").strip()
    if not target_uid:
        return []

    incoming = without_blank_draft_groups(groups)
    if not incoming:
        return []

    existing = load_grouping(db, target_uid)
    if not existing.groups:
        return incoming

    next_index = max(g.group_index for g in existing.groups) + 1
    claimed_groups: list[Group] = []
    for offset, group in enumerate(incoming):
        group.group_index = next_index + offset
        claimed_groups.append(group)
    return existing.groups + claimed_groups


def delete_grouping(db: sqlite3.Connection, uid: str) -> None:
    """Delete all grouping rows for *uid*."""
    _ensure_grouping_table(db)
    with db:
        db.execute("DELETE FROM grouping WHERE uid = ?", (uid,))


# ── Save grouping ─────────────────────────────────────────────────────────────

def _clean_jpg_paths(jpg_paths: list[str]) -> list[str]:
    """Remove phantom paths (not on disk).

    Oracle: server.js grouping-tool save — jpgPaths are filtered to only
    those that actually exist on disk before writing back.
    """
    return [p for p in jpg_paths if os.path.isfile(p)]


def save_grouping(
    db: sqlite3.Connection,
    uid: str,
    groups: list[Group],
    *,
    clean_phantoms: bool = True,
) -> None:
    """Persist *groups* for *uid* to the DB.

    If *clean_phantoms* is True (default), phantom jpg paths that no longer
    exist on disk are removed before writing.

    Oracle: server.js:3387-3391 groupingWrite + phantom cleanup.
    """
    def _save_once() -> None:
        _ensure_grouping_table(db)
        now = _iso_now()

        with db:
            # Delete existing groups for this uid then re-insert
            db.execute("DELETE FROM grouping WHERE uid = ?", (uid,))
            for g in groups:
                jpg_paths = _clean_jpg_paths(g.jpg_paths) if clean_phantoms else g.jpg_paths
                g.jpg_paths = jpg_paths
                # raw_json 总是从 to_dict 重建（合并旧 raw_json 的未知字段），保证 output_name
                # 等当前字段写入；否则旧 raw_json 会盖掉新改的 output_name。
                merged: dict = {}
                if g.raw_json:
                    try:
                        parsed = json.loads(g.raw_json)
                        if isinstance(parsed, dict):
                            merged = parsed
                    except Exception:
                        merged = {}
                merged.update(g.to_dict())
                merged["jpgPaths"] = jpg_paths
                raw_json_str = json.dumps(merged, ensure_ascii=False)
                db.execute(
                    """
                    INSERT OR REPLACE INTO grouping
                      (uid, group_index, angle_label, jpg_paths, composed_tiff_path,
                       status, source, created_at, updated_at,
                       result_sequence, archive_zip, retired_tiff_paths, raw_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        uid,
                        g.group_index,
                        g.angle_label,
                        json.dumps(jpg_paths, ensure_ascii=False),
                        g.composed_tiff_path,
                        g.status,
                        g.source,
                        g.created_at or now,
                        g.updated_at or now,
                        g.result_sequence,
                        g.archive_zip,
                        json.dumps(g.retired_tiff_paths or [], ensure_ascii=False),
                        raw_json_str,
                    ),
                )

    _run_with_sqlite_lock_retries(db, _save_once)


# ── Archive-zip backfill ──────────────────────────────────────────────────────

def backfill_archive_zips(db: sqlite3.Connection) -> int:
    """Recover ``grouping.archive_zip`` for results compressed by an older build.

    archive_service writes the bundle as ``<tiff-stem>.zip`` next to the composed
    TIFF (archive_service.py:227-228). A project.db whose ``archive_zip`` was
    never recorded — e.g. one created by the web prototype (db-utils.js, 5-column
    grouping) or migrated from an older schema — therefore shows "尚未压缩" even
    though the zip is right there on disk.

    For each group whose ``composed_tiff_path`` exists on disk but whose
    ``archive_zip`` is empty, this records the sibling ``.zip`` (size >= 32 B —
    the same validity gate as archive_service.py:301). It NEVER recompresses,
    deletes, or fabricates a zip; it only points the DB at an archive that
    already exists. Returns the number of rows backfilled. Idempotent.
    """
    _ensure_grouping_table(db)
    rows = db.execute(
        "SELECT uid, group_index, composed_tiff_path, archive_zip FROM grouping"
    ).fetchall()
    updated = 0
    for row in rows:
        d = dict(row) if not isinstance(row, dict) else row
        if d.get("archive_zip"):
            continue  # already recorded — never override
        tiff = d.get("composed_tiff_path")
        if not tiff or not os.path.isfile(tiff):
            continue  # no real TIFF → never fabricate an archive pointer
        candidate = str(Path(tiff).with_suffix(".zip"))
        try:
            if not (os.path.isfile(candidate) and os.path.getsize(candidate) >= 32):
                continue
        except OSError:
            continue
        db.execute(
            "UPDATE grouping SET archive_zip = ? WHERE uid = ? AND group_index = ?",
            (candidate, d["uid"], d["group_index"]),
        )
        updated += 1
    if updated:
        db.commit()
    return updated


# ── explicitUnassigns ─────────────────────────────────────────────────────────

def remove_jpg_from_all_groups(db: sqlite3.Connection, jpg_path: str) -> None:
    """Remove *jpg_path* from every grouping row (resolved path comparison)."""
    _ensure_grouping_table(db)

    def _resolved(path: str) -> str:
        try:
            return str(Path(path).resolve())
        except OSError:
            return str(path)

    target = _resolved(jpg_path)
    uids = db.execute("SELECT DISTINCT uid FROM grouping").fetchall()
    for (uid,) in uids:
        sg = load_grouping(db, uid)
        changed = False
        for g in sg.groups:
            kept = [p for p in g.jpg_paths if _resolved(p) != target]
            if len(kept) != len(g.jpg_paths):
                g.jpg_paths = kept
                changed = True
        if changed:
            save_grouping(db, uid, sg.groups, clean_phantoms=False)


def add_explicit_unassign(db: sqlite3.Connection, path: str) -> None:
    """Mark *path* as explicitly unassigned (P0 blacklist).

    Oracle: monitor-service.js:56 — explicitUnassigns takes priority over
    all other attribution sources.
    """
    _ensure_explicit_unassigns_table(db)
    resolved = str(Path(path).resolve())
    db.execute(
        "INSERT OR IGNORE INTO explicit_unassigns (path, created_at) VALUES (?, ?)",
        (resolved, _iso_now()),
    )
    db.commit()


def remove_explicit_unassign(db: sqlite3.Connection, path: str) -> None:
    """Remove *path* from the explicit-unassign blacklist."""
    _ensure_explicit_unassigns_table(db)
    resolved = str(Path(path).resolve())
    db.execute("DELETE FROM explicit_unassigns WHERE path = ?", (resolved,))
    db.commit()


def get_explicit_unassigns(db: sqlite3.Connection) -> set:
    """Return the full set of explicitly unassigned paths (resolved)."""
    _ensure_explicit_unassigns_table(db)
    rows = db.execute("SELECT path FROM explicit_unassigns").fetchall()
    return {row[0] for row in rows}


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(tz=timezone.utc).isoformat()
