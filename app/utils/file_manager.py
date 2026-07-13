"""Open local files or directories in the platform file manager.

The app often runs inside WSL while the user's desktop and Explorer are on
Windows.  In that case `/mnt/<drive>/...` must be handed to Explorer as a
Windows path, while stored Windows paths must first be localized for existence
checks inside WSL.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.utils import path_utils


@dataclass(frozen=True)
class OpenDirectoryResult:
    """Outcome of handing a directory to the platform file manager."""

    opened: bool
    local_path: str
    error: str = ""


def local_path(path: str) -> str:
    """Return *path* in the syntax usable by this Python runtime."""
    return path_utils.localize_path(path)


def open_directory_detailed(path: str) -> OpenDirectoryResult:
    """Open *path* and retain the real local path and launcher error."""
    localized = local_path(path)
    if not localized:
        return OpenDirectoryResult(False, "", "路径为空或无法转换为本机路径")
    normalized = os.path.normpath(localized)
    try:
        if path_utils.is_wsl_runtime():
            win_path = path_utils.wsl_to_windows(localized)
            if win_path:
                subprocess.Popen(["explorer.exe", win_path])
                return OpenDirectoryResult(True, win_path)
            return OpenDirectoryResult(False, localized, "无法把 WSL 路径转换为 Windows 路径")
        if sys.platform == "win32":
            # 用户场景（2026-07-13）：右键「打开文件夹」必须真正交给资源管理器；
            # 失败时要把实际本机路径和系统错误告诉用户，不能只表现为“按键没反应”。
            try:
                os.startfile(normalized)  # type: ignore[attr-defined]
            except (AttributeError, OSError) as start_error:
                try:
                    subprocess.Popen(["explorer.exe", normalized])
                except (OSError, subprocess.SubprocessError) as explorer_error:
                    return OpenDirectoryResult(
                        False,
                        normalized,
                        f"Windows 打开失败：{start_error}；资源管理器备用方式也失败：{explorer_error}",
                    )
            return OpenDirectoryResult(True, normalized)
        if sys.platform == "darwin":
            subprocess.Popen(["open", localized])
            return OpenDirectoryResult(True, normalized)
        subprocess.Popen(["xdg-open", localized])
        return OpenDirectoryResult(True, normalized)
    except (OSError, subprocess.SubprocessError) as exc:
        return OpenDirectoryResult(False, normalized, str(exc))


def open_directory(path: str) -> bool:
    """Open *path* as a directory in the user's file manager."""
    return open_directory_detailed(path).opened


def reveal_in_directory(path: str) -> bool:
    """Show *path* in its containing directory, selecting it when possible."""
    localized = local_path(path)
    if not localized:
        return False
    try:
        is_dir = Path(localized).is_dir()
        if path_utils.is_wsl_runtime():
            win_path = path_utils.wsl_to_windows(localized)
            if win_path:
                argv = (
                    ["explorer.exe", win_path]
                    if is_dir
                    else ["explorer.exe", "/select,", win_path]
                )
                subprocess.Popen(argv)
                return True
        if sys.platform == "win32":
            norm = os.path.normpath(localized)
            argv = ["explorer", norm] if is_dir else ["explorer", "/select,", norm]
            subprocess.Popen(argv)
            return True
        target = localized if is_dir else str(Path(localized).parent)
        if sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
        return True
    except (OSError, subprocess.SubprocessError):
        return False
