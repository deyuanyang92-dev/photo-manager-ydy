"""project_paths.py — single availability gate for project storage.

The data-loss class this prevents: when a project's directory is on a volume
that is not currently present (unmounted USB/external drive, disconnected
network share, deleted folder), the old code would ``mkdir(parents=True)`` the
whole path and create an empty SQLite db at the *mountpoint* on the local disk.
That fabricated an empty "ghost" project (so the real data looked lost) AND, if
the user then wrote to it, the writes landed on the local shadow and were
hidden the moment the real volume was remounted — silent data loss.

The rule enforced here is simple and absolute:

    **Never create a project's ROOT directory implicitly.**

A project root that does not already exist means its volume is gone. Reads must
refuse (raise :class:`ProjectUnavailableError`) rather than fabricate. Only a
*deliberate* new-project creation may create a leaf folder, and even then its
parent volume must already be present.

Qt-free so it can be unit-tested headless and reused by every storage layer
(``db_manager``, ``project_service``, ``project_settings_service``).
"""
from __future__ import annotations

from pathlib import Path


class ProjectUnavailableError(OSError):
    """A project's directory/volume is not present — refuse to fabricate it.

    Subclasses :class:`OSError` so existing ``except (OSError, sqlite3.Error)``
    handlers (e.g. ``AppContext.get_db``) degrade gracefully to "no project"
    instead of crashing.
    """


def project_root_available(project_dir: str | None) -> bool:
    """True if *project_dir* already exists on disk (its volume is mounted)."""
    if not project_dir:
        return False
    try:
        return Path(project_dir).resolve().is_dir()
    except OSError:
        return False


def require_project_root(project_dir: str | None) -> Path:
    """Return the resolved root, or raise if it does not already exist.

    Use before any read/open of an existing project. Never creates anything.
    """
    if not project_dir:
        raise ProjectUnavailableError("未指定项目目录")
    root = Path(project_dir).resolve()
    if not root.is_dir():
        raise ProjectUnavailableError(
            f"项目目录不可用（盘未挂载 / 路径丢失）：{project_dir}"
        )
    return root


def require_creatable_parent(project_dir: str) -> Path:
    """Return the resolved root for a *new* project whose parent must exist.

    Creating a brand-new project may make the leaf folder, but only when the
    parent volume is present — otherwise we'd fabricate on a phantom path again.
    """
    root = Path(project_dir).resolve()
    if root.is_dir():
        return root
    parent = root.parent
    if not parent.is_dir():
        raise ProjectUnavailableError(
            f"无法在此创建项目，上级目录不存在（盘未挂载？）：{parent}"
        )
    return root
