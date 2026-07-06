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
from pathlib import Path

from app.utils import path_utils


def local_path(path: str) -> str:
    """Return *path* in the syntax usable by this Python runtime."""
    return path_utils.localize_path(path)


def open_directory(path: str) -> bool:
    """Open *path* as a directory in the user's file manager.

    Returns whether a launcher process was started.  The caller is responsible
    for checking that the directory exists and for showing any user-facing
    warning.
    """
    localized = local_path(path)
    if not localized:
        return False
    try:
        if path_utils.is_wsl_runtime():
            win_path = path_utils.wsl_to_windows(localized)
            if win_path:
                subprocess.Popen(["explorer.exe", win_path])
                return True
        if sys.platform == "win32":
            subprocess.Popen(["explorer", os.path.normpath(localized)])
            return True
        if sys.platform == "darwin":
            subprocess.Popen(["open", localized])
            return True
        subprocess.Popen(["xdg-open", localized])
        return True
    except (OSError, subprocess.SubprocessError):
        return False


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
