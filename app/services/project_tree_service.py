"""project_tree_service.py — 项目文件夹树扫描（新增，计划 glittery-riding-oasis 步骤 3）.

A "project" is a root folder; any subfolder (any depth, any name — 断面/区域/样地/
航次…) can itself be a photo workspace. This module scans that folder tree so the
UI can show it and let the user enter any node as a workspace.

纯逻辑、无 Qt，便于测试。绝不创建目录/数据库——只读扫描。
"""

from __future__ import annotations

import os
from pathlib import Path

# 这些子目录是工作区内部结构，不当作"断面/子项目"节点展示。
RESERVED_DIR_NAMES: frozenset[str] = frozenset({
    "_data", "incoming-jpg", "新拍JPG", "results", "freeform", "archive",
    "_retired-tiff",  # 撤销合成/重合成退役的 TIF（工作区内部，不当树节点）
})

_WORKSPACE_MARKER_FILES: frozenset[str] = frozenset({
    ".project-specimens.json",
})

_WORKSPACE_MARKER_DIRS: frozenset[str] = frozenset({
    "_data",
    "incoming-jpg",
    "新拍JPG",
    "results",
})


def is_workspace(dir_path: str) -> bool:
    """True if *dir_path* already has its own ``_data/project.db`` (已认领的工作区)."""
    return (Path(dir_path) / "_data" / "project.db").exists()


def is_workspace_candidate(dir_path: str) -> bool:
    """True when a folder looks like an existing or legacy photo workspace.

    Older test/work directories may predate the current project registry and may
    not be present in ``user_projects.json``. Treat folders with a real
    ``project.db`` as workspaces, and folders with the old photo-workbench shape
    as importable candidates.
    """
    p = Path(dir_path)
    if is_workspace(str(p)):
        return True
    if any((p / name).exists() for name in _WORKSPACE_MARKER_FILES):
        return True
    marker_dirs = sum(1 for name in _WORKSPACE_MARKER_DIRS if (p / name).is_dir())
    if marker_dirs >= 2:
        return True
    incoming = p / "incoming-jpg"
    if incoming.is_dir():
        try:
            for child in incoming.iterdir():
                if child.is_file() and child.suffix.lower() in {".jpg", ".jpeg"}:
                    return True
        except OSError:
            pass
    return False


def scan_tree(root: str, max_depth: int = 6) -> dict:
    """Return a nested node dict for the folder tree under *root*.

    Node shape::

        {"name": str, "path": str, "has_data": bool, "children": [node, ...]}

    - Reserved workspace-internal dirs (RESERVED_DIR_NAMES) and dotfiles are skipped.
    - ``has_data`` marks folders that already are workspaces (have project.db) —
      i.e. folders to adopt as-is, zero restructuring.
    - Never creates anything; unreadable dirs degrade to no children.
    """
    root_path = Path(root)

    def _node(p: Path, depth: int) -> dict:
        children: list[dict] = []
        if depth < max_depth:
            try:
                entries = sorted(os.scandir(p), key=lambda e: e.name)
            except OSError:
                entries = []
            for entry in entries:
                name = entry.name
                if name.startswith(".") or name in RESERVED_DIR_NAMES:
                    continue
                try:
                    if not entry.is_dir():
                        continue
                except OSError:
                    continue
                children.append(_node(Path(entry.path), depth + 1))
        return {
            "name": p.name,
            "path": str(p),
            "has_data": is_workspace(str(p)),
            "children": children,
        }

    return _node(root_path, 0)


def flatten_workspaces(node: dict) -> list[str]:
    """Collect ``node["path"]`` for every node (root included) with ``has_data``.

    Pre-order (root first, then children in their existing order). Pure over the
    dict ``scan_tree`` returns — touches no filesystem.
    """
    out: list[str] = []
    if node.get("has_data"):
        out.append(node["path"])
    for child in node.get("children", []):
        out.extend(flatten_workspaces(child))
    return out


def discover_workspaces(root_dir: str, max_depth: int = 6) -> list[dict]:
    """Scan *root_dir* and return one dict per adopted workspace.

    Each entry::

        {"path": <abs path str>, "rel": <relpath to root>, "name": <断面 label>}

    ``rel`` is the workspace dir relative to the survey root (the 断面 label).
    When the root itself is a workspace, ``relpath`` returns ``"."`` → ``name``
    falls back to the basename. Order follows :func:`flatten_workspaces`.
    """
    tree = scan_tree(root_dir, max_depth)
    out: list[dict] = []
    for path in flatten_workspaces(tree):
        rel = os.path.relpath(path, root_dir)
        name = rel if rel != "." else os.path.basename(path)
        out.append({"path": path, "rel": rel, "name": name})
    return out


def discover_workspace_candidates(root_dir: str, max_depth: int = 2) -> list[dict]:
    """Return existing and legacy workspace-like folders under *root_dir*.

    This is intentionally broader than :func:`discover_workspaces`: it is used
    for adoption/import lists, where missing a real old project is worse than
    showing a folder that can later be entered and materialised.
    """
    root_path = Path(root_dir)
    out: list[dict] = []

    def _walk(p: Path, depth: int) -> None:
        if depth > max_depth:
            return
        if is_workspace_candidate(str(p)):
            try:
                rel = os.path.relpath(str(p), str(root_path))
            except ValueError:
                rel = p.name
            out.append({
                "path": str(p),
                "rel": rel,
                "name": rel if rel != "." else p.name,
                "has_data": is_workspace(str(p)),
                "is_candidate": True,
            })
            return
        if depth == max_depth:
            return
        try:
            entries = sorted(os.scandir(p), key=lambda e: e.name)
        except OSError:
            entries = []
        for entry in entries:
            name = entry.name
            if name.startswith(".") or name in RESERVED_DIR_NAMES:
                continue
            try:
                if entry.is_dir():
                    _walk(Path(entry.path), depth + 1)
            except OSError:
                continue

    _walk(root_path, 0)
    return out
