"""path_utils.py — Cross-platform path utilities for WSL / Windows / Linux.

Oracle: prototype-photo-gui/path-utils.js + wslpath.js + server.js:62-151

Public interface (exact signatures — other agents depend on these):
  def normalize_path(p: str) -> str
  def is_wsl_runtime() -> bool
  def wsl_to_windows(p: str) -> str          # /mnt/n/foo → N:\\foo (None if not /mnt/X/)
  def windows_to_wsl(p: str) -> str          # N:\\foo → /mnt/n/foo (None if not drive path)
  def repair_doubled_mount(p: str) -> str    # /mnt/n/mnt/n/x → /mnt/n/x
  class SafePathRegistry:
      register_root(d: str) -> None
      assert_safe(path: str, label: str = "") -> None   # PermissionError if not in whitelist
  default_registry = SafePathRegistry()
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Optional


# ── Runtime detection ─────────────────────────────────────────────────────────

def is_wsl_runtime() -> bool:
    """Return True when running inside WSL (Linux on Windows).

    Mirrors path-utils.js::isWslRuntime:
      - must be linux platform
      - check WSL_DISTRO_NAME env var first (fast path)
      - fall back to /proc/version content for 'microsoft'
    """
    if sys.platform != "linux":
        return False
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        with open("/proc/version", "r", encoding="utf-8", errors="replace") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


# ── WSL ↔ Windows path conversion ─────────────────────────────────────────────

def wsl_to_windows(p: str) -> Optional[str]:
    """Convert /mnt/n/foo/bar → N:\\foo\\bar.

    Returns None when the input is not a /mnt/<letter>/... path.
    Mirrors path-utils.js::wslToWindows.
    """
    if not p:
        return None
    m = re.match(r"^/mnt/([a-zA-Z])(?:/(.*))?$", p)
    if not m:
        return None
    drive = m.group(1).upper()
    rest = m.group(2) or ""
    return drive + ":\\" + rest.replace("/", "\\")


def windows_to_wsl(p: str) -> Optional[str]:
    """Convert N:\\foo\\bar or N:/foo/bar → /mnt/n/foo/bar.

    Returns None when the input doesn't look like a Windows drive path.
    Mirrors path-utils.js::windowsToWsl.
    """
    if not p:
        return None
    # Full path with separator: N:\foo\bar or N:/foo/bar
    m = re.match(r"^([a-zA-Z]):[/\\](.*)$", p)
    if m:
        drive = m.group(1).lower()
        rest = m.group(2).replace("\\", "/")
        # Normalise repeated/trailing slashes
        parts = [part for part in rest.split("/") if part]
        return "/mnt/" + drive + ("/" + "/".join(parts) if parts else "")
    # Bare drive with no path: "C:"
    m2 = re.match(r"^([a-zA-Z]):$", p)
    if m2:
        return "/mnt/" + m2.group(1).lower()
    return None


# ── Double-mount prefix repair ─────────────────────────────────────────────────

def repair_doubled_mount(p: str) -> str:
    """Remove duplicate /mnt/<letter>/mnt/<letter>/ prefixes.

    Handles:
      /mnt/n/mnt/n/x → /mnt/n/x        (WSL double)
      N:\\mnt\\n\\...  → N:\\...          (Windows equivalent)

    Mirrors path-utils.js::repairDoubledMountPrefix.
    """
    if not p:
        return p
    # WSL double: /mnt/X/mnt/X/... (same letter, case-insensitive)
    m = re.match(r"^/mnt/([a-zA-Z])/mnt/\1(?:/|$)", p, re.IGNORECASE)
    if m:
        letter = m.group(1).lower()
        prefix = "/mnt/" + letter
        # The matched portion length = len("/mnt/X/mnt/X") + optional trailing slash
        suffix = p[len(m.group(0)):]
        return prefix + ("/" + suffix if suffix else "/")

    # Windows double: N:\mnt\n\... (same letter, case-insensitive)
    m2 = re.match(r"^([a-zA-Z]):[/\\]mnt[/\\]([a-zA-Z])(?:[/\\]|$)", p, re.IGNORECASE)
    if m2 and m2.group(1).upper() == m2.group(2).upper():
        drive = m2.group(1).upper()
        suffix = p[len(m2.group(0)):]
        return drive + ":\\" + suffix.replace("/", "\\")

    return p


# ── normalize_path ─────────────────────────────────────────────────────────────

def normalize_path(p: str) -> str:
    """Repair doubled mount prefixes, convert Windows→POSIX on WSL, resolve.

    Mirrors wslpath.js::normalizePath + path-utils.js double-mount fix.
    """
    p = repair_doubled_mount(str(p or "").strip())
    if is_wsl_runtime():
        converted = windows_to_wsl(p)
        if converted is not None:
            return str(Path(converted).resolve())
    return str(Path(p).resolve())


# ── SafePathRegistry ───────────────────────────────────────────────────────────

class SafePathRegistry:
    """Whitelist-based path safety guard.

    Mirrors server.js::ALLOWED_DIRS + assertSafePath (lines 62-101).

    Uses os.path.relpath semantics — NOT startswith — to prevent
    prefix-spoofing attacks (e.g. /tmp/proj vs /tmp/proj_evil).
    The JS Oracle uses path.relative(root, resolved) and checks that
    rel === '' or (!rel.startsWith('..') && !path.isAbsolute(rel)).
    """

    def __init__(self) -> None:
        self._roots: list[str] = []

    def register_root(self, d: str) -> None:
        """Add an allowed root directory. Idempotent (duplicates ignored).

        Resolves to absolute path before storing.
        Mirrors server.js::registerAllowedDir.
        """
        resolved = str(Path(d).resolve())
        if resolved not in self._roots:
            self._roots.append(resolved)

    def assert_safe(self, path: str, label: str = "") -> None:
        """Raise PermissionError if path is not inside any registered root.

        Empty path also raises PermissionError.
        The check uses os.path.relpath and verifies rel does NOT start with '..'.
        This correctly blocks /tmp/proj_evil when /tmp/proj is the root.
        """
        lbl = label or "path"
        if not path:
            raise PermissionError(f"{lbl} 必须是非空字符串")

        resolved = str(Path(path).resolve())

        for root in self._roots:
            rel = os.path.relpath(resolved, root)
            # rel == '.' → same dir; not starting with '..' → child path
            if rel == "." or not rel.startswith(".."):
                return  # inside this root — OK

        roots_str = ", ".join(self._roots) if self._roots else "(none)"
        raise PermissionError(
            f"{lbl} 越界: {path} (白名单: {roots_str})"
        )


# ── Module-level default registry ─────────────────────────────────────────────

default_registry = SafePathRegistry()
