"""Self-update helpers for the Windows portable package."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import sys
import tempfile
import time
import urllib.request
import zipfile


REPO = "deyuanyang92-dev/photo-manager-ydy"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"
WINDOWS_ASSET_RE = re.compile(r"SpecimenPhotoWorkbench-.*-win64\.zip$", re.I)
APP_EXE_NAME = "SpecimenPhotoWorkbench.exe"


@dataclass(frozen=True)
class ReleaseInfo:
    tag_name: str
    asset_name: str
    asset_url: str
    asset_size: int
    page_url: str


@dataclass(frozen=True)
class PreparedUpdate:
    release: ReleaseInfo
    work_dir: Path
    package_dir: Path
    script_path: Path


def version_key(version: str) -> tuple[int, ...]:
    """Return a numeric comparison key for tags like ``v0.02``."""
    parts = re.findall(r"\d+", version or "")
    return tuple(int(p) for p in parts) or (0,)


def is_newer_version(candidate: str, current: str) -> bool:
    return version_key(candidate) > version_key(current)


def release_from_api_payload(payload: dict) -> ReleaseInfo:
    assets = payload.get("assets") or []
    for asset in assets:
        name = str(asset.get("name") or "")
        if not WINDOWS_ASSET_RE.match(name):
            continue
        url = str(asset.get("browser_download_url") or asset.get("url") or "")
        if not url:
            continue
        return ReleaseInfo(
            tag_name=str(payload.get("tag_name") or ""),
            asset_name=name,
            asset_url=url,
            asset_size=int(asset.get("size") or 0),
            page_url=str(payload.get("html_url") or ""),
        )
    raise ValueError("latest release does not contain a Windows portable zip")


def fetch_latest_release(timeout: int = 20) -> ReleaseInfo:
    req = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "SpecimenPhotoWorkbench-Updater",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return release_from_api_payload(data)


def can_self_update(executable: str | None = None, platform: str | None = None) -> bool:
    """Only the Windows PyInstaller app may replace itself."""
    platform = platform or sys.platform
    executable = executable or sys.executable
    if platform != "win32":
        return False
    exe = PureWindowsPath(executable)
    if exe.name.lower() != APP_EXE_NAME.lower():
        return False
    return bool(getattr(sys, "frozen", False))


def install_dir_for_executable(executable: str | None = None) -> Path:
    return Path(executable or sys.executable).resolve().parent


def download_file(
    url: str,
    destination: str | Path,
    *,
    progress_cb=None,
    timeout: int = 30,
) -> None:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "SpecimenPhotoWorkbench-Updater"},
    )
    destination = Path(destination)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with destination.open("wb") as fh:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if progress_cb is not None:
                    progress_cb(done, total)


def extract_update_zip(zip_path: str | Path, package_dir: str | Path) -> Path:
    package_dir = Path(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(package_dir)
    exe = package_dir / APP_EXE_NAME
    if exe.is_file():
        return package_dir
    matches = list(package_dir.rglob(APP_EXE_NAME))
    if not matches:
        raise ValueError(f"{APP_EXE_NAME} not found in update package")
    return matches[0].parent


def ps_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def make_update_script(
    *,
    install_dir: str | Path,
    package_dir: str | Path,
    exe_path: str | Path,
    backup_dir: str | Path,
    pid: int,
) -> str:
    """PowerShell script that runs after the app exits, then restarts it."""
    install = ps_quote(install_dir)
    package = ps_quote(package_dir)
    exe = ps_quote(exe_path)
    backup = ps_quote(backup_dir)
    return f"""$ErrorActionPreference = 'Stop'
$installDir = {install}
$packageDir = {package}
$exePath = {exe}
$backupDir = {backup}
$pidToWait = {int(pid)}

Start-Sleep -Seconds 1
if ($pidToWait -gt 0) {{
    try {{ Wait-Process -Id $pidToWait -Timeout 90 -ErrorAction SilentlyContinue }} catch {{ }}
}}

New-Item -ItemType Directory -Path (Split-Path -Parent $backupDir) -Force | Out-Null
if (Test-Path -LiteralPath $backupDir) {{
    Remove-Item -LiteralPath $backupDir -Recurse -Force
}}
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null

Get-ChildItem -LiteralPath $installDir -Force |
    Copy-Item -Destination $backupDir -Recurse -Force

Copy-Item -Path (Join-Path $packageDir '*') -Destination $installDir -Recurse -Force
Start-Process -FilePath $exePath -WorkingDirectory $installDir
"""


def prepare_update(
    release: ReleaseInfo,
    *,
    executable: str | None = None,
    progress_cb=None,
) -> PreparedUpdate:
    install_dir = install_dir_for_executable(executable)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    work_dir = Path(tempfile.gettempdir()) / f"specimen-photo-workbench-update-{stamp}"
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    zip_path = work_dir / release.asset_name
    download_file(release.asset_url, zip_path, progress_cb=progress_cb)
    package_dir = extract_update_zip(zip_path, work_dir / "package")
    backup_dir = Path(tempfile.gettempdir()) / f"specimen-photo-workbench-backup-{stamp}"
    script_path = work_dir / "apply-update.ps1"
    script_path.write_text(
        make_update_script(
            install_dir=install_dir,
            package_dir=package_dir,
            exe_path=install_dir / APP_EXE_NAME,
            backup_dir=backup_dir,
            pid=os.getpid(),
        ),
        encoding="utf-8",
    )
    return PreparedUpdate(
        release=release,
        work_dir=work_dir,
        package_dir=package_dir,
        script_path=script_path,
    )


def launch_update_script(script_path: str | Path) -> None:
    import subprocess

    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
        ],
        cwd=str(Path(script_path).parent),
        close_fds=True,
    )
