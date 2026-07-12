"""project_tree_service.py — 项目文件夹树扫描（新增，计划 glittery-riding-oasis 步骤 3）.

A "project" is a root folder; any subfolder (any depth, any name — 断面/区域/样地/
航次…) can itself be a photo workspace. This module scans that folder tree so the
UI can show it and let the user enter any node as a workspace.

纯逻辑、无 Qt，便于测试。绝不创建目录/数据库——只读扫描。
"""

from __future__ import annotations

import copy
import os
import time
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

_TREE_CACHE_TTL_SECONDS = 2.0
_SCAN_TREE_CACHE: dict[tuple, tuple[float, dict]] = {}
_CANDIDATE_CACHE: dict[tuple, tuple[float, list[dict]]] = {}


def clear_project_tree_cache(root: str | None = None) -> None:
    """Clear cached folder scans.

    ``root=None`` clears everything.  Passing a root invalidates entries for
    that resolved root only; UI actions call this after creating folders or
    changing project state so the next refresh is immediate and correct.
    """
    if root is None:
        _SCAN_TREE_CACHE.clear()
        _CANDIDATE_CACHE.clear()
        return
    try:
        resolved = str(Path(root).resolve())
    except OSError:
        resolved = str(root)
    for cache in (_SCAN_TREE_CACHE, _CANDIDATE_CACHE):
        for key in list(cache):
            if key and key[0] == resolved:
                cache.pop(key, None)


def _root_fingerprint(root: Path) -> tuple:
    try:
        st = root.stat()
        root_sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        root_sig = None
    try:
        visible_dir_count = sum(
            1
            for entry in os.scandir(root)
            if not entry.name.startswith(".")
            and entry.name not in RESERVED_DIR_NAMES
            and entry.is_dir()
        )
    except OSError:
        visible_dir_count = -1
    return (root_sig, visible_dir_count)


def _cache_key(root: str, max_depth: int) -> tuple:
    try:
        resolved = str(Path(root).resolve())
    except OSError:
        resolved = str(root)
    return (resolved, int(max_depth), _root_fingerprint(Path(resolved)))


def _cache_get(cache: dict, key: tuple):
    hit = cache.get(key)
    if not hit:
        return None
    ts, value = hit
    if time.monotonic() - ts > _TREE_CACHE_TTL_SECONDS:
        cache.pop(key, None)
        return None
    return copy.deepcopy(value)


def _cache_put(cache: dict, key: tuple, value) -> None:
    cache[key] = (time.monotonic(), copy.deepcopy(value))


def is_region(dir_path: str) -> bool:
    """True 当这个目录是**项目容器(调查区域)**, 不是拍照工作区。

    场景(用户 2026-07-12): 「江苏盐城2026」是项目, 底下「日出海湾」「月亮湾」才是
      拍照的地方。项目根也有 _data/project.db(存共享设置供子点继承), 光看
      project.db 会把项目根误判成工作区 -> 照片堆到项目根上。
    标记文件 _data/region.json 由 project_scaffold_service 建项目时写入。
    纯路径判断: 项目树扫描不开数据库(开了会持有文件锁, Windows 上文件夹就删不掉了)。
    (Fable 5, 2026-07-12)
    """
    return (Path(dir_path) / "_data" / "region.json").exists()


def is_workspace(dir_path: str) -> bool:
    """True if *dir_path* already has its own ``_data/project.db`` (已认领的工作区).

    §7 旧: 只看 project.db 存不存在。现在多一条: 打了 region.json 标记的**项目容器**
    不算工作区(见 is_region) —— 老项目没有该标记, 行为完全不变。
    """
    if is_region(dir_path):
        return False
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


def scan_tree(root: str, max_depth: int = 6, *, use_cache: bool = True) -> dict:
    """Return a nested node dict for the folder tree under *root*.

    Node shape::

        {"name": str, "path": str, "has_data": bool, "children": [node, ...]}

    - Reserved workspace-internal dirs (RESERVED_DIR_NAMES) and dotfiles are skipped.
    - ``has_data`` marks folders that already are workspaces (have project.db) —
      i.e. folders to adopt as-is, zero restructuring.
    - Never creates anything; unreadable dirs degrade to no children.
    """
    root_path = Path(root)
    key = _cache_key(root, max_depth)
    if use_cache:
        cached = _cache_get(_SCAN_TREE_CACHE, key)
        if cached is not None:
            return cached

    def build_workspace_tree_node(p: Path, depth: int) -> dict:
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
                children.append(build_workspace_tree_node(Path(entry.path), depth + 1))
        return {
            "name": p.name,
            "path": str(p),
            "has_data": is_workspace(str(p)),
            "children": children,
        }

    tree = build_workspace_tree_node(root_path, 0)
    if use_cache:
        _cache_put(_SCAN_TREE_CACHE, key, tree)
    return tree


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


def discover_workspace_candidates(
    root_dir: str,
    max_depth: int = 2,
    *,
    use_cache: bool = True,
) -> list[dict]:
    """Return existing and legacy workspace-like folders under *root_dir*.

    This is intentionally broader than :func:`discover_workspaces`: it is used
    for adoption/import lists, where missing a real old project is worse than
    showing a folder that can later be entered and materialised.
    """
    root_path = Path(root_dir)
    key = _cache_key(root_dir, max_depth)
    if use_cache:
        cached = _cache_get(_CANDIDATE_CACHE, key)
        if cached is not None:
            return cached
    out: list[dict] = []

    def collect_workspace_candidate_dirs(p: Path, depth: int) -> None:
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
                    collect_workspace_candidate_dirs(Path(entry.path), depth + 1)
            except OSError:
                continue

    collect_workspace_candidate_dirs(root_path, 0)
    if use_cache:
        _cache_put(_CANDIDATE_CACHE, key, out)
    return out
