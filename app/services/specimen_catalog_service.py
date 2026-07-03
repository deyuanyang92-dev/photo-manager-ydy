"""Global specimen UID lookup across known workspaces.

The per-workspace ``specimens.uid PRIMARY KEY`` only protects one
``_data/project.db``. A specimen UID is a museum-style catalogue number, so
creation and correction must also check other known workspaces before saving.
"""
from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import quote

from app.services.project_service import (
    default_user_projects_json_path,
    list_projects,
)
from app.services.project_tree_service import discover_workspaces
from app.utils.naming import normalize_uid


@dataclass(frozen=True)
class SpecimenUidHit:
    uid: str
    project_dir: str
    owner_project_dir: str
    scientific_name: str = ""

    @property
    def display_project(self) -> str:
        return Path(self.owner_project_dir or self.project_dir).name


def _candidate_projects_json_paths() -> list[Path]:
    local = Path(default_user_projects_json_path())
    repo_root = Path(__file__).resolve().parents[2]
    web = repo_root.parent / "photo-platform-ydy" / "prototype-photo-gui" / "data" / "user_projects.json"
    out: list[Path] = []
    for path in (local, web):
        if path not in out:
            out.append(path)
    return out


def _project_dir_from_record(record: dict) -> str:
    return str(record.get("_resolved_test_dir") or record.get("directory") or record.get("dir") or "")


def known_workspace_dirs(
    *,
    current_project_dir: Optional[str] = None,
    current_project_root: Optional[str] = None,
    extra_dirs: Optional[Iterable[str]] = None,
) -> list[str]:
    """Return known workspace dirs, de-duplicated by resolved path."""
    ordered: list[str] = []

    def add_unique_workspace_dir(path: Optional[str]) -> None:
        if not path:
            return
        try:
            resolved = str(Path(path).resolve())
        except OSError:
            resolved = str(path)
        if resolved not in ordered:
            ordered.append(resolved)

    add_unique_workspace_dir(current_project_dir)
    if extra_dirs:
        for path in extra_dirs:
            add_unique_workspace_dir(path)

    if current_project_root:
        try:
            for entry in discover_workspaces(current_project_root):
                add_unique_workspace_dir(entry.get("path"))
        except OSError:
            pass

    for json_path in _candidate_projects_json_paths():
        for project in list_projects(str(json_path)):
            add_unique_workspace_dir(_project_dir_from_record(project))

    return ordered


def _lookup_uid_in_project(project_dir: str, uid: str) -> Optional[SpecimenUidHit]:
    """Read one workspace DB for UID existence without running migrations."""
    try:
        resolved = str(Path(project_dir).resolve())
        db_path = Path(resolved) / "_data" / "project.db"
        if not db_path.exists():
            return None
        uri_path = quote(str(db_path), safe="/:\\")
        db = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            cols = {
                str(r["name"] if isinstance(r, sqlite3.Row) else r[1])
                for r in db.execute("PRAGMA table_info(specimens)").fetchall()
            }
            if "uid" not in cols:
                return None
            owner_expr = (
                "COALESCE(owner_project_dir, '')"
                if "owner_project_dir" in cols else "''"
            )
            sci_expr = (
                "COALESCE(scientific_name, '')"
                if "scientific_name" in cols else "''"
            )
            row = db.execute(
                f"""
                SELECT uid, {owner_expr} AS owner_project_dir,
                       {sci_expr} AS scientific_name
                FROM specimens
                WHERE uid = ?
                """,
                (uid,),
            ).fetchone()
        finally:
            db.close()
    except (sqlite3.Error, OSError):
        return None
    if not row:
        return None
    return SpecimenUidHit(
        uid=row["uid"],
        project_dir=resolved,
        owner_project_dir=row["owner_project_dir"] or resolved,
        scientific_name=row["scientific_name"] or "",
    )


def find_uid(
    uid: str,
    *,
    current_project_dir: Optional[str] = None,
    current_project_root: Optional[str] = None,
    extra_dirs: Optional[Iterable[str]] = None,
) -> list[SpecimenUidHit]:
    """Find every known workspace that already contains *uid*."""
    normalized = normalize_uid(uid)
    if not normalized:
        return []
    hits: list[SpecimenUidHit] = []
    for project_dir in known_workspace_dirs(
        current_project_dir=current_project_dir,
        current_project_root=current_project_root,
        extra_dirs=extra_dirs,
    ):
        hit = _lookup_uid_in_project(project_dir, normalized)
        if hit is not None:
            hits.append(hit)
    return hits


def conflicting_uid_hits(
    uid: str,
    *,
    current_project_dir: Optional[str] = None,
    current_project_root: Optional[str] = None,
    allowed_current_uid: Optional[str] = None,
    extra_dirs: Optional[Iterable[str]] = None,
) -> list[SpecimenUidHit]:
    """Return hits that should block saving *uid*.

    ``allowed_current_uid`` is the UID already loaded in the current workspace;
    re-saving that exact specimen is allowed, but any other hit is a collision.
    """
    normalized = normalize_uid(uid)
    allowed = normalize_uid(allowed_current_uid or "")
    current_resolved = ""
    if current_project_dir:
        try:
            current_resolved = str(Path(current_project_dir).resolve())
        except OSError:
            current_resolved = str(current_project_dir)

    conflicts: list[SpecimenUidHit] = []
    for hit in find_uid(
        normalized,
        current_project_dir=current_project_dir,
        current_project_root=current_project_root,
        extra_dirs=extra_dirs,
    ):
        if (
            allowed
            and normalized == allowed
            and current_resolved
            and hit.project_dir == current_resolved
        ):
            continue
        conflicts.append(hit)
    return conflicts


def format_uid_conflict(uid: str, hits: list[SpecimenUidHit]) -> str:
    if not hits:
        return ""
    shown = "、".join(hit.display_project for hit in hits[:3])
    more = " 等" if len(hits) > 3 else ""
    return f"编号 {normalize_uid(uid)} 已存在于项目「{shown}{more}」，不能重复使用。"
