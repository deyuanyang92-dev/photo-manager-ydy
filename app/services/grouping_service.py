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
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


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
            raw_json=raw.get("raw_json"),
        )
        groups.append(g)

    return SpecimenGrouping(uid=uid, groups=groups)


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
    _ensure_grouping_table(db)

    now = _iso_now()

    with db:
        # Delete existing groups for this uid then re-insert
        db.execute("DELETE FROM grouping WHERE uid = ?", (uid,))
        for g in groups:
            jpg_paths = _clean_jpg_paths(g.jpg_paths) if clean_phantoms else g.jpg_paths
            g.jpg_paths = jpg_paths
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
                    g.raw_json or json.dumps(g.to_dict(), ensure_ascii=False),
                ),
            )


# ── explicitUnassigns ─────────────────────────────────────────────────────────

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
