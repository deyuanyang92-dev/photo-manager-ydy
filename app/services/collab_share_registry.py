"""Which local projects this machine advertises to teammates.

Persisted in QSettings as ``collab/shared_project_dirs`` (JSON list of paths).
Each workspace with a ``project.db`` gets a stable ``projectId`` for LAN sync.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

_SETTINGS_KEY = "collab/shared_project_dirs"


@dataclass(frozen=True)
class LocalShareProject:
    directory: str
    name: str
    project_id: str


def _normalise_dir(path: str) -> str:
    return str(Path(path).resolve())


def load_shared_dirs(settings_qs: Any) -> set[str]:
    raw = settings_qs.value(_SETTINGS_KEY, "", type=str)
    if not raw:
        return set()
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return set()
    if not isinstance(data, list):
        return set()
    out: set[str] = set()
    for item in data:
        text = str(item or "").strip()
        if text:
            try:
                out.add(_normalise_dir(text))
            except OSError:
                out.add(text)
    return out


def save_shared_dirs(settings_qs: Any, directories: Iterable[str]) -> None:
    normalised: list[str] = []
    seen: set[str] = set()
    for item in directories:
        text = str(item or "").strip()
        if not text:
            continue
        try:
            key = _normalise_dir(text)
        except OSError:
            key = text
        if key in seen:
            continue
        seen.add(key)
        normalised.append(key)
    settings_qs.setValue(_SETTINGS_KEY, json.dumps(normalised, ensure_ascii=False))


def project_id_for_directory(directory: str) -> str:
    """Return stable project ID for a workspace directory, or '' if unavailable."""
    from app.db.db_manager import open_project_db_private
    from app.services.project_identity_service import ensure_project_identity
    from app.services.project_paths import ProjectUnavailableError
    from app.services.project_tree_service import is_workspace

    path = Path(directory)
    try:
        if not is_workspace(str(path)):
            return ""
        db = open_project_db_private(path)
    except (OSError, sqlite3.Error, ProjectUnavailableError):
        return ""
    try:
        from app.db.db_manager import ensure_schema

        ensure_schema(db)
        return ensure_project_identity(db, project_name=path.name)
    except (OSError, sqlite3.Error, ProjectUnavailableError):
        return ""
    finally:
        db.close()


def read_project_id_for_directory(directory: str) -> str:
    """Return existing project ID without creating one."""
    from app.db.db_manager import open_project_db_private
    from app.services.project_identity_service import read_project_identity
    from app.services.project_paths import ProjectUnavailableError
    from app.services.project_tree_service import is_workspace

    path = Path(directory)
    try:
        if not is_workspace(str(path)):
            return ""
        db = open_project_db_private(path)
    except (OSError, sqlite3.Error, ProjectUnavailableError):
        return ""
    try:
        return read_project_identity(db)
    except sqlite3.OperationalError:
        return ""
    finally:
        db.close()


def list_local_share_candidates(
    *,
    extra_directories: Optional[Iterable[str]] = None,
) -> list[LocalShareProject]:
    """Workspaces from recent list (+ optional extras) that can be shared.

    Listing is intentionally read-only.  A project identity is created only
    when the user actually shares/copies a project code for that workspace.
    """
    from app.services.project_service import load_user_projects
    from app.services.project_tree_service import is_workspace

    projects = load_user_projects()
    seen: set[str] = set()
    dirs: list[str] = []
    display_names: dict[str, str] = {}
    for entry in projects:
        directory = str(entry.get("directory") or "").strip()
        if directory:
            dirs.append(directory)
            try:
                resolved = _normalise_dir(directory)
            except OSError:
                resolved = directory
            display_names[resolved] = (
                str(entry.get("name") or Path(resolved).name or resolved)
                .split("/")[-1]
                .strip()
                or Path(resolved).name
                or resolved
            )
    if extra_directories:
        dirs.extend(str(d or "").strip() for d in extra_directories)

    out: list[LocalShareProject] = []
    for directory in dirs:
        if not directory:
            continue
        try:
            resolved = _normalise_dir(directory)
        except OSError:
            resolved = directory
        if resolved in seen:
            continue
        try:
            if not is_workspace(resolved):
                continue
        except OSError:
            continue
        pid = read_project_id_for_directory(resolved)
        seen.add(resolved)
        display = display_names.get(resolved) or Path(resolved).name or resolved
        out.append(LocalShareProject(resolved, display, pid))
    out.sort(key=lambda p: p.name.casefold())
    return out


def build_shared_projects_payload(directories: Iterable[str]) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
    seen: set[str] = set()
    for directory in directories:
        try:
            resolved = _normalise_dir(directory)
        except OSError:
            continue
        pid = project_id_for_directory(resolved)
        if not pid or pid in seen:
            continue
        seen.add(pid)
        payload.append({
            "projectId": pid,
            "projectName": Path(resolved).name,
        })
    payload.sort(key=lambda item: item["projectName"].casefold())
    return payload


def apply_project_sync_code_to_directory(directory: str, code: str) -> str:
    """Bind an arbitrary local workspace directory to a teammate project code."""
    from app.db.db_manager import open_project_db_private
    from app.services.project_identity_service import (
        parse_project_sync_code,
        read_project_identity,
        set_project_identity,
    )
    from app.services.project_tree_service import is_workspace

    resolved = _normalise_dir(directory)
    if not is_workspace(resolved):
        raise ValueError("请选择已经创建的拍照工作区。")
    parsed = parse_project_sync_code(code)
    db = open_project_db_private(resolved)
    try:
        previous = read_project_identity(db)
        return set_project_identity(
            db,
            parsed["projectId"],
            project_name=parsed.get("projectName", "") or Path(resolved).name,
            previous_project_id=previous,
        )
    finally:
        db.close()


def reset_project_identity_for_directory(directory: str) -> str:
    """Break an existing project-code pairing by assigning a fresh local ID."""
    from app.db.db_manager import open_project_db_private
    from app.services.project_identity_service import (
        read_project_identity,
        reset_project_identity,
    )
    from app.services.project_tree_service import is_workspace

    resolved = _normalise_dir(directory)
    if not is_workspace(resolved):
        raise ValueError("请选择已经创建的拍照工作区。")
    db = open_project_db_private(resolved)
    try:
        previous = read_project_identity(db)
        return reset_project_identity(
            db,
            project_name=Path(resolved).name,
            previous_project_id=previous,
        )
    finally:
        db.close()


def default_shared_dirs(
    settings_qs: Any,
    *,
    current_directory: Optional[str] = None,
) -> set[str]:
    """Saved selection, or the current workspace when nothing saved yet."""
    saved = load_shared_dirs(settings_qs)
    if saved:
        return saved
    if current_directory:
        try:
            return {_normalise_dir(current_directory)}
        except OSError:
            return {current_directory}
    return set()


def iter_peer_shared_projects(peer: Any) -> list[tuple[str, str]]:
    """(project_id, project_name) entries advertised by one peer."""
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(pid: str, name: str) -> None:
        pid = str(pid or "").strip()
        if len(pid) != 32 or pid in seen:
            return
        seen.add(pid)
        entries.append((pid, str(name or "").strip() or pid[:8]))

    shared = getattr(peer, "shared_projects", None) or []
    if isinstance(shared, list):
        for item in shared:
            if isinstance(item, dict):
                add(item.get("projectId", ""), item.get("projectName", ""))
    if not entries:
        add(getattr(peer, "project_id", ""), getattr(peer, "project_name", ""))
    return entries
