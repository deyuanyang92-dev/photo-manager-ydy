"""collab_project_bind.py — Team project pick-list for photo/TIF sync binding.

Pure helpers used by the collaboration centre dialog.  Keeps peer/project
listing rules in one place so CollabView and tests stay thin.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional


@dataclass(frozen=True)
class TeamProjectBindOption:
    """One logical project a teammate is working on."""

    project_id: str
    name: str
    code: str
    peer_names: tuple[str, ...] = ()
    same_name: bool = False
    already_synced: bool = False


def _project_display(project: Optional[str]) -> str:
    raw = str(project or "").strip()
    if not raw:
        return ""
    normalised = raw.replace("\\", "/").rstrip("/")
    name = Path(normalised).name
    return name or raw


def _normalize_label(name: str) -> str:
    from app.services.collab_service import CollabService
    return CollabService._normalize_project_label(name)


def _group_matches(service: Any, peer: Any) -> bool:
    code = str(getattr(service, "group_code", "") or "")
    return bool(code) and str(getattr(peer, "group_code", "") or "") == code


def list_bind_options(service: Any, peers: Iterable[Any] | None = None) -> list[TeamProjectBindOption]:
    """Return teammate projects the user can bind to (excludes already-synced)."""
    if service is None:
        return []
    peer_list = list(peers or [])
    if peer_list:
        candidates = [p for p in peer_list if _group_matches(service, p)]
    else:
        try:
            candidates = list(service.peers())
            candidates = [p for p in candidates if _group_matches(service, p)]
        except Exception:
            candidates = []

    local_id = str(getattr(service, "project_id", "") or "")
    local_label = _normalize_label(
        str(getattr(service, "project_name", "") or "")
        or str(getattr(service, "_project_dir", "") or "")
    )

    from app.services.project_identity_service import project_sync_code
    from app.services.collab_share_registry import iter_peer_shared_projects

    def _valid_project_id(pid: str) -> bool:
        return len(pid) == 32 and all(c in "0123456789abcdef" for c in pid.lower())

    by_id: dict[str, TeamProjectBindOption] = {}
    for peer in candidates:
        host = str(getattr(peer, "hostname", "") or getattr(peer, "ip", "") or "队友")
        for pid, display in iter_peer_shared_projects(peer):
            if not _valid_project_id(pid):
                continue
            same_name = bool(local_label and _normalize_label(display) == local_label)
            already = bool(local_id and pid == local_id)
            if already:
                continue
            entry = by_id.get(pid)
            if entry is None:
                by_id[pid] = TeamProjectBindOption(
                    project_id=pid,
                    name=display,
                    code=project_sync_code(pid, project_name=display),
                    peer_names=(host,),
                    same_name=same_name,
                )
            else:
                names = tuple(sorted(set(entry.peer_names + (host,))))
                by_id[pid] = TeamProjectBindOption(
                    project_id=entry.project_id,
                    name=entry.name,
                    code=entry.code,
                    peer_names=names,
                    same_name=entry.same_name or same_name,
                )
    return sorted(by_id.values(), key=lambda o: (not o.same_name, o.name))


def filter_bind_options(
    options: Iterable[TeamProjectBindOption],
    query: str,
) -> list[TeamProjectBindOption]:
    q = str(query or "").strip().casefold()
    if not q:
        return list(options)
    return [
        o for o in options
        if q in o.name.casefold()
        or any(q in n.casefold() for n in o.peer_names)
    ]


def same_name_options(service: Any, peers: Iterable[Any] | None = None) -> list[TeamProjectBindOption]:
    return [o for o in list_bind_options(service, peers) if o.same_name]


def describe_project_sync_state(service: Any, peers: Iterable[Any] | None = None) -> str:
    """One-line status for the collaboration centre project row."""
    from app.services.collab_share_registry import load_shared_dirs

    if service is None:
        return "共享项目：协作未启动"
    if not getattr(service, "group_code", ""):
        return "共享项目：请先设置团队永久码"

    settings_qs = None
    try:
        from PyQt6.QtCore import QSettings
        from app.config.settings import _APP, _ORG
        settings_qs = QSettings(_ORG, _APP)
    except Exception:
        pass
    shared_dirs = getattr(service, "_shared_project_dirs", None) or set()
    if not shared_dirs and settings_qs is not None:
        shared_dirs = load_shared_dirs(settings_qs)
    share_count = len(shared_dirs)
    if share_count:
        names = [Path(d).name for d in sorted(shared_dirs)[:3]]
        label = "、".join(names)
        extra = share_count - len(names)
        if extra > 0:
            label += f" 等{share_count}个"
        share_line = f"本机共享 {share_count} 个项目（{label}）"
    elif not str(getattr(service, "project_id", "") or ""):
        return "共享项目：请先打开项目并勾选要共享的项目"
    else:
        share_line = "本机共享：当前项目"

    if not str(getattr(service, "project_id", "") or ""):
        return f"共享项目：{share_line} · 打开项目后可与队友配对"

    local_id = str(getattr(service, "project_id", "") or "")
    local_name = _project_display(
        getattr(service, "project_name", "")
        or getattr(service, "_project_dir", "")
    ) or "当前项目"
    peer_list = list(peers or [])
    synced = [
        p for p in peer_list
        if _group_matches(service, p)
        and str(getattr(p, "project_id", "") or "") == local_id
    ]
    if synced:
        hosts = ", ".join(
            sorted({str(getattr(p, "hostname", "") or p.ip) for p in synced})[:3]
        )
        extra = len(synced) - 3
        if extra > 0:
            hosts += f" 等{len(synced)}台"
        return f"共享项目：{share_line} · 「{local_name}」已与 {hosts} 同步"

    if same_name_options(service, peer_list):
        return f"共享项目：{share_line} · 「{local_name}」可同名配对"

    options = list_bind_options(service, peer_list)
    if options:
        return f"共享项目：{share_line} · 可选 {len(options)} 个队友项目"

    return f"共享项目：{share_line} · 「{local_name}」尚未与队友配对"
