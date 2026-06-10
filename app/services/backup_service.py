"""backup_service.py — silent local snapshots of project metadata.

The per-project storage model (user's explicit choice) keeps each workspace's
``_data/project.db`` inside the project folder — often on a removable or
external drive, as the ONLY copy. This module is the safety net behind that
model: it snapshots the tiny metadata db (tens of KB; never the photos) into
the app user-data dir on the always-present local disk, keeping the newest N.

A dead drive, an accidental delete, or a botched sync then costs nothing —
restore = copy the newest snapshot back into ``<project>/_data/project.db``.

Triggers are silent and zero-effort for the user: app close + after bulk
imports (coords / collection records). Qt-free; tested headless.

Snapshots use the SQLite backup API (``Connection.backup``) so a WAL db with
an open writer still copies consistently — a plain file copy could capture a
torn state.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

_KEEP_DEFAULT = 10


def user_backup_root() -> Path:
    """Stable, always-local directory holding all snapshots."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", str(Path.home())))
        return base / "SpecimenPhotoWorkbench" / "backups"
    return Path.home() / ".specimen_workbench" / "backups"


def _project_slot(project_dir: str) -> Path:
    """Per-project snapshot dir: ``<name>-<sha1(path)[:8]>`` — readable AND
    collision-free for same-named projects on different disks."""
    resolved = str(Path(project_dir).resolve())
    digest = hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:8]
    name = Path(resolved).name or "project"
    return user_backup_root() / f"{name}-{digest}"


def _tag(now_tag: Optional[str]) -> str:
    return now_tag or datetime.now().strftime("%Y%m%d-%H%M%S")


def snapshot_project(project_dir: str, keep: int = _KEEP_DEFAULT,
                     now_tag: Optional[str] = None) -> Optional[Path]:
    """Snapshot ``<project>/_data/project.db`` → backup slot; prune to *keep*.

    Returns the snapshot path, or None when the project is offline / has no
    db / any step fails — NEVER raises and NEVER creates anything inside the
    project itself (a gone volume must not be touched).
    """
    src = Path(project_dir) / "_data" / "project.db"
    try:
        if not src.is_file():
            return None
        slot = _project_slot(project_dir)
        slot.mkdir(parents=True, exist_ok=True)
        target = slot / f"project-{_tag(now_tag)}.db"
        src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        try:
            dst_conn = sqlite3.connect(str(target))
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()
        _prune(slot, "project-*.db", keep)
        return target
    except (OSError, sqlite3.Error):
        return None


def snapshot_projects_json(json_path: str, keep: int = _KEEP_DEFAULT,
                           now_tag: Optional[str] = None) -> Optional[Path]:
    """Snapshot the recent-projects pointer list (user_projects.json)."""
    src = Path(json_path)
    try:
        if not src.is_file():
            return None
        slot = user_backup_root() / "user_projects"
        slot.mkdir(parents=True, exist_ok=True)
        target = slot / f"user_projects-{_tag(now_tag)}.json"
        shutil.copyfile(src, target)
        _prune(slot, "user_projects-*.json", keep)
        return target
    except OSError:
        return None


def list_snapshots(project_dir: str) -> list[Path]:
    """Existing snapshots for *project_dir*, oldest → newest."""
    slot = _project_slot(project_dir)
    if not slot.is_dir():
        return []
    return sorted(slot.glob("project-*.db"))


def _prune(slot: Path, pattern: str, keep: int) -> None:
    files = sorted(slot.glob(pattern))
    for old in files[:-keep] if keep > 0 else files:
        try:
            old.unlink()
        except OSError:
            pass
