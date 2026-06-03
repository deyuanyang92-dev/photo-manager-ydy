"""helicon_service.py — Helicon Focus CLI wrapper.

Pure-function core (build_helicon_args) + detection (detect_helicon) +
QProcess invocation stub.

Oracle: helicon.js lines 127–194 (buildHeliconArgs), 68–124 (hasHelicon),
        248–309 (stackSingle: WSL path translation, retry, timeout).

FLAG ALIGNMENT (critical — these are the real Helicon CLI flags):
  -silent          (no space / equals; bare flag)
  -save:<out>      (colon separator, NOT -o)
  -mp:<method>     (NOT -m:)
  -rp:<radius>     (NOT -r:)
  -sp:<smoothing>  (NOT -s:)
  -j:<quality>
  -tif:<compression>
  -sort:<order>
  -dmap
  -ba: / -va: / -ha: / -ra: / -ma: / -im: / -dmf:

Path translation: On WSL, inputFolder / inputList / outputFile are converted
from /mnt/<x>/... to Windows paths before being passed to Helicon.exe.
Oracle: helicon.js:248–251.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from app.utils.path_utils import wsl_to_windows, windows_to_wsl, is_wsl_runtime


# ── Known install locations (mirrors helicon.js KNOWN_INSTALL_DIRS) ──────────

HELICON_EXE_NAMES = ["HeliconFocus.exe", "Helicon Focus.exe"]

KNOWN_INSTALL_DIRS = [
    r"I:\Helicon Focus 8",
    r"C:\Program Files\Helicon Software\Helicon Focus",
    r"C:\Program Files (x86)\Helicon Software\Helicon Focus",
]


# ── Pure argument builder ─────────────────────────────────────────────────────

def build_helicon_args(
    jpg_paths: list[str],
    output: str,
    method: Optional[str] = None,
    radius: Optional[str] = None,
    smoothing: Optional[str] = None,
    quality: Optional[int] = None,
    *,
    input_list_path: Optional[str] = None,
    tiff_compression: Optional[str] = None,
    sort_order: Optional[str] = None,
    save_depth_map: bool = False,
    silent: bool = True,
    brightness: Optional[str] = None,
    v_shift: Optional[str] = None,
    h_shift: Optional[str] = None,
    rotation: Optional[str] = None,
    magnification: Optional[str] = None,
    interpolation: Optional[str] = None,
    depth_map_feathering: Optional[str] = None,
) -> list[str]:
    """Build the Helicon Focus CLI argument list.

    Mirrors helicon.js:buildHeliconArgs exactly.

    Input handling:
      - If *input_list_path* is provided → -i <list_path> (list file).
      - Else if *jpg_paths* is non-empty → first element as inputFolder
        (Helicon accepts a folder or a list file via -i).
      - Paths are WSL-translated via wsl_to_windows when running under WSL.

    Output path is also WSL-translated.

    Returns a list[str] suitable for subprocess / QProcess.
    """
    # WSL path translation — Oracle: helicon.js:248-251
    def _win(p: str) -> str:
        if is_wsl_runtime() and p:
            return wsl_to_windows(p) or p
        return p

    args: list[str] = []

    # -silent flag (Oracle: helicon.js:130-132)
    if silent:
        args.append("-silent")

    # Input: list file (-i) or folder
    if input_list_path:
        args.extend(["-i", _win(input_list_path)])
    elif jpg_paths:
        # Pass first jpg_path as the input folder (or single file treated as folder)
        args.append(_win(jpg_paths[0]))

    # Output: -save:<out>  (Oracle: helicon.js:139)
    if output:
        args.append("-save:" + _win(output))

    # Method: -mp:<value>  (Oracle: helicon.js:142-144)
    if method is not None and method != "":
        args.append("-mp:" + str(method))

    # Radius: -rp:<value>  (Oracle: helicon.js:145-148)
    if radius is not None and radius != "":
        args.append("-rp:" + str(radius))

    # Smoothing: -sp:<value>  (Oracle: helicon.js:149-152)
    if smoothing is not None and smoothing != "":
        args.append("-sp:" + str(smoothing))

    # TIFF compression: -tif:<value>  (Oracle: helicon.js:153-155)
    if tiff_compression:
        args.append("-tif:" + str(tiff_compression))

    # JPEG quality: -j:<value>  (Oracle: helicon.js:156-158)
    if quality is not None:
        args.append("-j:" + str(quality))

    # Sort order: -sort:<value>
    if sort_order:
        args.append("-sort:" + str(sort_order))

    # Depth map: -dmap
    if save_depth_map:
        args.append("-dmap")

    # Alignment params
    if brightness is not None:
        args.append("-ba:" + str(brightness))
    if v_shift is not None:
        args.append("-va:" + str(v_shift))
    if h_shift is not None:
        args.append("-ha:" + str(h_shift))
    if rotation is not None:
        args.append("-ra:" + str(rotation))
    if magnification is not None:
        args.append("-ma:" + str(magnification))
    if interpolation is not None:
        args.append("-im:" + str(interpolation))
    if depth_map_feathering is not None:
        args.append("-dmf:" + str(depth_map_feathering))

    return args


# ── Installation detection ────────────────────────────────────────────────────

_helicon_exe_cache: Optional[str | bool] = None  # None=unchecked, str=found, False=not found


def _resolve_helicon_exe(path_input: str) -> Optional[str]:
    """Try to resolve *path_input* to an existing Helicon .exe.

    Mirrors helicon.js:resolveHeliconExe.
    Accepts: a direct .exe path, or an install directory.
    Under WSL, translates Windows paths first.
    """
    if not path_input or not isinstance(path_input, str):
        return None
    trimmed = path_input.strip()
    if not trimmed:
        return None

    # On WSL, convert Windows paths to WSL paths first
    candidate = trimmed
    if is_wsl_runtime() and re.match(r"^[A-Za-z]:[/\\]", trimmed):
        candidate = windows_to_wsl(trimmed) or trimmed

    if os.path.isfile(candidate) and candidate.lower().endswith(".exe"):
        return candidate
    if os.path.isdir(candidate):
        for exe_name in HELICON_EXE_NAMES:
            full = os.path.join(candidate, exe_name)
            if os.path.isfile(full):
                return full
    return None


def detect_helicon() -> Optional[str]:
    """Detect Helicon Focus executable path.

    Three-level detection (mirrors helicon.js:hasHelicon):
      1. HELICON_FOCUS_PATH env var (exe or install dir).
      2. HELICON_FOCUS_DIR  env var (install dir).
      3. Known install dirs.

    Returns path to .exe or None if not found.
    Cache result for session lifetime.

    NOTE: Registry query (helicon.js level-3) is omitted in the Python
    implementation; it requires Windows registry access which is not
    available inside WSL without cmd.exe subprocess.  The known-dirs
    fallback covers the same common cases.
    """
    global _helicon_exe_cache
    if _helicon_exe_cache is not None:
        return _helicon_exe_cache if _helicon_exe_cache else None

    # Level 1: HELICON_FOCUS_PATH
    env_path = os.environ.get("HELICON_FOCUS_PATH", "")
    found = _resolve_helicon_exe(env_path)
    if found:
        _helicon_exe_cache = found
        return found

    # Level 2: HELICON_FOCUS_DIR
    env_dir = os.environ.get("HELICON_FOCUS_DIR", "")
    found = _resolve_helicon_exe(env_dir)
    if found:
        _helicon_exe_cache = found
        return found

    # Level 3: known install directories
    for known_dir in KNOWN_INSTALL_DIRS:
        found = _resolve_helicon_exe(known_dir)
        if found:
            _helicon_exe_cache = found
            return found

    _helicon_exe_cache = False
    return None


def reset_helicon_cache() -> None:
    """Reset cached detection result (for testing)."""
    global _helicon_exe_cache
    _helicon_exe_cache = None


# ── QProcess / subprocess invocation ─────────────────────────────────────────
# NOTE: The QProcess real-call path requires a running Qt application and
# cannot be exercised in headless CI.  It is marked accordingly.
# build_helicon_args and detect_helicon are fully unit-testable.

def stack_single_subprocess(
    jpg_paths: list[str],
    output_file: str,
    method: Optional[str] = None,
    radius: Optional[str] = None,
    smoothing: Optional[str] = None,
    quality: Optional[int] = None,
    input_list_path: Optional[str] = None,
    tiff_compression: Optional[str] = None,
    timeout_seconds: int = 600,
    max_attempts: int = 2,
) -> dict:
    """Invoke Helicon Focus via subprocess (NOT QProcess).

    Mirrors helicon.js:stackSingle retry and output-file validation:
      - Max 2 attempts (Oracle: helicon.js:268).
      - 10-minute timeout per attempt (Oracle: helicon.js:272-273).
      - After success, verify output file exists (Oracle: helicon.js:276-280).
      - On timeout → raise immediately, no retry (Oracle: helicon.js:285-287).

    Returns dict with ok, output_file, duration_ms.

    NOTE: Requires True machine with Helicon installed.  CI skips this.
    """
    exe = detect_helicon()
    if not exe:
        raise RuntimeError("Helicon Focus 未安装或未检测到")

    args = build_helicon_args(
        jpg_paths=jpg_paths,
        output=output_file,
        method=method,
        radius=radius,
        smoothing=smoothing,
        quality=quality,
        input_list_path=input_list_path,
        tiff_compression=tiff_compression,
    )

    import time
    last_err = None
    start = time.monotonic()

    for attempt in range(1, max_attempts + 1):
        try:
            proc = subprocess.run(
                [exe] + args,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)

            if not os.path.isfile(output_file):
                raise RuntimeError(
                    "Helicon 执行完成但输出文件未生成。可能需要 Pro/Premium 授权。"
                    + (("\n" + proc.stderr) if proc.stderr else "")
                )

            return {
                "ok": True,
                "output_file": output_file,
                "duration_ms": elapsed_ms,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        except subprocess.TimeoutExpired:
            raise RuntimeError("Helicon 进程超时（10 分钟）")
        except RuntimeError:
            raise
        except Exception as e:
            last_err = e
            if attempt < max_attempts:
                import time as _t
                _t.sleep(0.8)
                continue

    detail = str(last_err) if last_err else "未知错误"
    raise RuntimeError(f"Helicon 调用失败：{detail}")
