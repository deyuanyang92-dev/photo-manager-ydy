"""win_screenshot.py — delegate screenshots to a Windows-native tool under WSL.

A Qt/X11 app running *inside* WSL cannot grab the Windows desktop: the
XWayland root window is all-black (``QScreen.grabWindow(0)`` returns black —
proven on this machine), so the in-app overlay can only composite the app's
own windows on a grey void. That is strictly worse than the Windows-native
screenshot tools (Snipaste, Win+Shift+S) the user already runs.

So under WSL we don't fight the boundary — we hand off to the Windows side:

  * Snipaste, if installed (``Snipaste.exe snip`` starts a snip immediately), or
  * the built-in Windows screen-clip (``ms-screenclip:`` URI), always present
    on Windows 10/11.

Both capture the *real* Windows desktop and land the result on the Windows
clipboard, exactly like every other screenshot the user takes. This module is
Qt-free and side-effect-free except :func:`launch_windows_snip`.
"""
from __future__ import annotations

import functools
import shutil
import subprocess
from pathlib import Path

_CMD_EXE = "/mnt/c/Windows/System32/cmd.exe"


def is_wsl() -> bool:
    """True when running inside WSL (so the X11 root grab is black)."""
    try:
        txt = Path("/proc/version").read_text(errors="ignore").lower()
    except OSError:
        return False
    return "microsoft" in txt or "wsl" in txt


def _cmd_exe() -> str | None:
    """Path to Windows ``cmd.exe`` reachable from WSL, or None."""
    if Path(_CMD_EXE).exists():
        return _CMD_EXE
    found = shutil.which("cmd.exe")
    return found


@functools.lru_cache(maxsize=1)
def _has_snipaste() -> bool:
    """True if ``Snipaste`` resolves on the Windows PATH (cached)."""
    cmd = _cmd_exe()
    if cmd is None:
        return False
    try:
        out = subprocess.run(
            [cmd, "/c", "where", "Snipaste"],
            capture_output=True, text=True, timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and "Snipaste" in out.stdout


def windows_snipper_cmd() -> list[str] | None:
    """argv that triggers a Windows-native region snip, or None if unavailable.

    Prefers Snipaste (the tool the user already uses); falls back to the
    always-present Windows screen-clip. Launched via ``start`` so the call is
    non-blocking and detached from this process.
    """
    cmd = _cmd_exe()
    if cmd is None:
        return None
    if _has_snipaste():
        # `start "" Snipaste snip` → tells a running/launched Snipaste to snip.
        return [cmd, "/c", "start", "", "Snipaste", "snip"]
    # Windows 10/11 built-in screen clip (Win+Shift+S equivalent).
    return [cmd, "/c", "start", "", "ms-screenclip:"]


def launch_windows_snip() -> bool:
    """Fire the Windows-native snip. Returns True if a launcher was spawned."""
    argv = windows_snipper_cmd()
    if argv is None:
        return False
    try:
        subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True
