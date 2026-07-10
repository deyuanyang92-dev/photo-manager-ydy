"""project_adopt_service.py — 认领旧/外部文件夹为工作区（零 migrate，不调 enter_workspace）.

Design spec §6: adopt = 只建 ``_data/project.db`` + 登记；首次「进入」才走
``enter_workspace`` 建 incoming/results 并 migrate。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from app.services import project_tree_service as pts
from app.utils.path_utils import normalize_path

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


@dataclass(frozen=True)
class PrescanReport:
    directory: str
    jpg_count: int
    tiff_count: int
    png_count: int
    has_data: bool
    marker_dir_count: int
    incoming_jpg_count: int
    original_files_touched: int = 0

    def summary_lines(self) -> list[str]:
        lines = [
            f"JPG：{self.jpg_count} 张",
            f"TIFF：{self.tiff_count} 张",
        ]
        if self.png_count:
            lines.append(f"PNG：{self.png_count} 张")
        if self.has_data:
            lines.append("已有 _data（将跳过重复建库）")
        else:
            lines.append("将只创建 _data/project.db")
        lines.append(f"原始文件改动：{self.original_files_touched} 个")
        return lines


def is_drive_root_or_system_dir(directory: str) -> bool:
    """Reject drive roots and common system/user dirs (spec §4.4)."""
    try:
        resolved = Path(directory).expanduser().resolve()
    except OSError:
        return True
    if resolved.parent == resolved:
        return True
    home = Path.home().resolve()
    blocked = {
        home,
        home / "Desktop",
        home / "Downloads",
        home / "Documents",
    }
    return resolved in blocked


def prescan_project(directory: str, *, max_files: int = 5000) -> PrescanReport:
    """Read-only scan before adopt — zero writes (spec §6 dry-run)."""
    root = Path(normalize_path(directory))
    jpg = tiff = png = incoming_jpg = 0
    inspected = 0
    if not root.is_dir():
        return PrescanReport(
            directory=str(root),
            jpg_count=0,
            tiff_count=0,
            png_count=0,
            has_data=pts.is_workspace(str(root)),
            marker_dir_count=0,
            incoming_jpg_count=0,
        )
    marker_dirs = sum(
        1 for name in pts._WORKSPACE_MARKER_DIRS if (root / name).is_dir()
    )
    scan_dirs: list[Path] = [root]
    for name in ("incoming-jpg", "新拍JPG", "results"):
        sub = root / name
        if sub.is_dir():
            scan_dirs.append(sub)
    seen_files: set[str] = set()
    for scan_root in scan_dirs:
        try:
            entries = list(scan_root.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_dir():
                    if scan_root == root and entry.name in pts.RESERVED_DIR_NAMES:
                        continue
                    if len(scan_dirs) < 30 and entry not in scan_dirs:
                        scan_dirs.append(entry)
                    continue
            except OSError:
                continue
            try:
                key = str(entry.resolve())
            except OSError:
                key = str(entry)
            if key in seen_files:
                continue
            seen_files.add(key)
            inspected += 1
            if inspected >= max_files:
                break
            ext = entry.suffix.lower()
            if ext in {".jpg", ".jpeg"}:
                jpg += 1
                if entry.parent.name in {"incoming-jpg", "新拍JPG"}:
                    incoming_jpg += 1
            elif ext in {".tif", ".tiff"}:
                tiff += 1
            elif ext == ".png":
                png += 1
        if inspected >= max_files:
            break
    return PrescanReport(
        directory=str(root),
        jpg_count=jpg,
        tiff_count=tiff,
        png_count=png,
        has_data=pts.is_workspace(str(root)),
        marker_dir_count=marker_dirs,
        incoming_jpg_count=incoming_jpg,
    )


AdoptResult = Literal["adopted", "already"]


def adopt_project(
    ctx,
    directory: str,
    *,
    name: Optional[str] = None,
    root: Optional[str] = None,
    projects_json_path: Optional[str] = None,
) -> AdoptResult:
    """Minimal adopt: ``open_project_db(create=True)`` + register — no migrate."""
    resolved = normalize_path(directory)
    if not Path(resolved).is_dir():
        raise FileNotFoundError(f"目录不存在: {resolved}")
    if is_drive_root_or_system_dir(resolved):
        raise ValueError(f"不能认领系统或驱动器根目录: {resolved}")
    if pts.is_workspace(resolved):
        return "already"

    from app.db.db_manager import open_project_db
    from app.services.project_service import (
        default_user_projects_json_path,
        record_recent_workspace,
    )

    open_project_db(resolved, create=True)
    jp = projects_json_path or default_user_projects_json_path()
    record_recent_workspace(jp, resolved, root=root)
    if name:
        import json

        from app.services.project_service import _same_path, list_projects

        projects = list_projects(jp)
        for entry in projects:
            d = entry.get("directory") or entry.get("dir") or ""
            if d and _same_path(d, resolved):
                entry["name"] = name.strip()
                break
        from pathlib import Path as _Path

        _Path(jp).write_text(
            json.dumps({"version": 1, "projects": projects}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    pts.clear_project_tree_cache(root)
    return "adopted"
