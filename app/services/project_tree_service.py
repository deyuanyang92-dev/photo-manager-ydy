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
})


def is_workspace(dir_path: str) -> bool:
    """True if *dir_path* already has its own ``_data/project.db`` (已认领的工作区)."""
    return (Path(dir_path) / "_data" / "project.db").exists()


def scan_tree(root: str, max_depth: int = 4) -> dict:
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
