"""Self-update helpers for the Windows portable package.

对标专业软件（VS Code / Cursor / Chrome）的更新工程：

  • 完整性校验：GitHub 提供的大小 + SHA-256（``verify_downloaded_package``）。
  • **真实性校验**：Ed25519 分离签名（``verify_update_signature``）。发布方用私钥对
    整个 zip 签名，随发布上传 ``<zip>.sig``（base64）。应用内嵌公钥
    (``UPDATE_PUBLIC_KEY_B64``)，配置后即强制校验——防止发布账号被盗或包被替换。
    生成/签名工具见 ``scripts/gen_update_keys.py`` 与 ``scripts/sign_release.py``。
  • 发布说明（changelog）：``ReleaseInfo.body`` 取自 GitHub release 正文。
  • 提权应用：安装目录不可写（如 Program Files）时以管理员身份运行替换脚本。
  • 用户资料保留：``PROTECTED_RELATIVE_PATHS`` + PowerShell 备份/恢复；项目数据与
    QSettings 都在安装目录之外，天然不受影响。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import http.client
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile


REPO = "deyuanyang92-dev/photo-manager-ydy"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"
LATEST_RELEASE_PAGE = f"https://github.com/{REPO}/releases/latest"
WINDOWS_ASSET_RE = re.compile(r"SpecimenPhotoWorkbench-.*-win64\.zip$", re.I)
APP_EXE_NAME = "SpecimenPhotoWorkbench.exe"

# ── Update authenticity (Ed25519 detached signature) ─────────────────────────
# base64 of the raw 32-byte Ed25519 *public* key. Empty = signature not enforced
# (keeps unsigned legacy releases working). Once you run
# ``scripts/gen_update_keys.py`` and paste the public key here (and sign each
# release with the matching private key), verification becomes mandatory.
# Can also be overridden at runtime via SPECIMEN_UPDATE_PUBKEY for staged rollout.
# Claude Code 修改 2026-07-14 — 用户裁定(2026-07-14, grill-me): 按通用软件更新
# 做法开启签名强制校验。密钥对由 scripts/gen_update_keys.py 生成于本机
# secrets/(已 gitignore, 不入库); 私钥只在发布者手里, 每次发布用
# scripts/sign_release.py 签。丢了这把私钥, 以后发布只能换新密钥对(旧包仍可用
# 环境变量 SPECIMEN_UPDATE_PUBKEY 覆盖校验, 不影响回滚)。
UPDATE_PUBLIC_KEY_B64 = "ec0g/+K6MMU/XfLstdIGiOIc0uI9XYrtU75xqqUsL74="
_PUBKEY_ENV = "SPECIMEN_UPDATE_PUBKEY"
PACKAGED_ERROR_RE = re.compile(r"Traceback|PermissionError|ModuleNotFoundError|ImportError", re.I)
DEFAULT_NETWORK_ATTEMPTS = 3
DEFAULT_RETRY_DELAY_SECONDS = 1.0
MAX_UPDATE_ZIP_ENTRIES = 30_000
MAX_UPDATE_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
PROTECTED_RELATIVE_PATHS = (
    r"_internal\data\user_projects.json",
    r"_internal\data\user_taxonomy.json",
    r"_internal\data\worms_cache.json",
    r"_internal\data\worms_jobs.json",
    r"_internal\data\worms_taxonomy.json",
    r"data\user_projects.json",
    r"data\user_taxonomy.json",
    r"data\worms_cache.json",
    r"data\worms_jobs.json",
    r"data\worms_taxonomy.json",
)


@dataclass(frozen=True)
class ReleaseInfo:
    tag_name: str
    asset_name: str
    asset_url: str
    asset_size: int
    page_url: str
    asset_digest: str = ""
    signature_url: str = ""   # URL of the <asset>.sig detached Ed25519 signature
    body: str = ""            # release notes / changelog (markdown from GitHub)


@dataclass(frozen=True)
class PreparedUpdate:
    release: ReleaseInfo
    work_dir: Path
    package_dir: Path
    script_path: Path
    requires_elevation: bool = False


class UpdateNetworkError(RuntimeError):
    """User-facing network failure raised after update retries are exhausted."""


def _is_retryable_update_error(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in {408, 409, 425, 429, 500, 502, 503, 504}
    return isinstance(
        exc,
        (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            socket.timeout,
            ssl.SSLError,
            http.client.IncompleteRead,
            http.client.RemoteDisconnected,
        ),
    )


def _sleep_before_retry(attempt_index: int, retry_delay: float) -> None:
    if retry_delay <= 0:
        return
    time.sleep(retry_delay * attempt_index)


def _network_error(action: str, exc: BaseException) -> UpdateNetworkError:
    return UpdateNetworkError(
        f"{action}失败：网络连接被中断，或 GitHub 暂时不可达。\n"
        f"请检查网络/代理后重试；也可以手动打开 {LATEST_RELEASE_PAGE} 下载 Windows 便携包。\n\n"
        f"最后错误：{exc}"
    )


def version_key(version: str) -> tuple[int, ...]:
    """Return a numeric comparison key for tags like ``v0.02``."""
    parts = re.findall(r"\d+", version or "")
    return tuple(int(p) for p in parts) or (0,)


def is_newer_version(candidate: str, current: str) -> bool:
    return version_key(candidate) > version_key(current)


def release_from_api_payload(payload: dict) -> ReleaseInfo:
    assets = payload.get("assets") or []

    def _download_url(asset: dict) -> str:
        return str(asset.get("browser_download_url") or asset.get("url") or "")

    for asset in assets:
        name = str(asset.get("name") or "")
        if not WINDOWS_ASSET_RE.match(name):
            continue
        url = _download_url(asset)
        if not url:
            continue
        sig_name = f"{name}.sig"
        signature_url = ""
        for other in assets:
            if str(other.get("name") or "") == sig_name:
                signature_url = _download_url(other)
                break
        return ReleaseInfo(
            tag_name=str(payload.get("tag_name") or ""),
            asset_name=name,
            asset_url=url,
            asset_size=int(asset.get("size") or 0),
            page_url=str(payload.get("html_url") or ""),
            asset_digest=str(asset.get("digest") or ""),
            signature_url=signature_url,
            body=str(payload.get("body") or ""),
        )
    raise ValueError("latest release does not contain a Windows portable zip")


def fetch_latest_release(
    timeout: int = 20,
    *,
    attempts: int = DEFAULT_NETWORK_ATTEMPTS,
    retry_delay: float = DEFAULT_RETRY_DELAY_SECONDS,
) -> ReleaseInfo:
    req = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "SpecimenPhotoWorkbench-Updater",
        },
    )
    attempts = max(1, int(attempts))
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return release_from_api_payload(data)
        except Exception as exc:  # noqa: BLE001
            if not _is_retryable_update_error(exc) or attempt >= attempts:
                if _is_retryable_update_error(exc):
                    raise _network_error("检查更新", exc) from exc
                raise
            _sleep_before_retry(attempt, retry_delay)
    raise AssertionError("unreachable")


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
    attempts: int = DEFAULT_NETWORK_ATTEMPTS,
    retry_delay: float = DEFAULT_RETRY_DELAY_SECONDS,
) -> None:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "SpecimenPhotoWorkbench-Updater"},
    )
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    attempts = max(1, int(attempts))
    for attempt in range(1, attempts + 1):
        wrote_partial = False
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                with destination.open("wb") as fh:
                    wrote_partial = True
                    while True:
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        fh.write(chunk)
                        done += len(chunk)
                        if progress_cb is not None:
                            progress_cb(done, total)
            return
        except Exception as exc:  # noqa: BLE001
            if wrote_partial and destination.exists():
                destination.unlink()
            if not _is_retryable_update_error(exc) or attempt >= attempts:
                if _is_retryable_update_error(exc):
                    raise _network_error("下载更新包", exc) from exc
                raise
            _sleep_before_retry(attempt, retry_delay)
    raise AssertionError("unreachable")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_downloaded_package(release: ReleaseInfo, zip_path: str | Path) -> None:
    """Validate size and GitHub-provided SHA-256 digest when available."""
    zip_path = Path(zip_path)
    if release.asset_size > 0:
        actual_size = zip_path.stat().st_size
        if actual_size != release.asset_size:
            raise ValueError(
                f"downloaded update size mismatch: expected {release.asset_size}, got {actual_size}"
            )
    digest = str(release.asset_digest or "").strip()
    if digest:
        algo, sep, expected = digest.partition(":")
        if sep and algo.lower() == "sha256" and expected:
            actual = sha256_file(zip_path)
            if actual.lower() != expected.lower():
                raise ValueError("downloaded update SHA-256 mismatch")


def update_public_key_b64() -> str:
    """Effective public key: runtime env overrides the embedded constant."""
    return os.environ.get(_PUBKEY_ENV, "").strip() or UPDATE_PUBLIC_KEY_B64.strip()


def signature_required() -> bool:
    """Whether authenticity verification is enforced (a public key is configured)."""
    return bool(update_public_key_b64())


def fetch_signature(
    url: str,
    *,
    timeout: int = 20,
    attempts: int = DEFAULT_NETWORK_ATTEMPTS,
    retry_delay: float = DEFAULT_RETRY_DELAY_SECONDS,
) -> str:
    """Download the small base64 ``.sig`` text and return it stripped."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "SpecimenPhotoWorkbench-Updater"},
    )
    attempts = max(1, int(attempts))
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8").strip()
        except Exception as exc:  # noqa: BLE001
            if not _is_retryable_update_error(exc) or attempt >= attempts:
                if _is_retryable_update_error(exc):
                    raise _network_error("下载更新签名", exc) from exc
                raise
            _sleep_before_retry(attempt, retry_delay)
    raise AssertionError("unreachable")


def verify_update_signature(
    zip_path: str | Path,
    signature_b64: str,
    *,
    public_key_b64: str | None = None,
) -> bool:
    """Verify an Ed25519 detached signature over the raw zip bytes.

    Returns True on success. Returns False when no public key is configured
    (signature not enforced). Raises ValueError on a bad/mismatched signature.
    """
    key_b64 = public_key_b64 if public_key_b64 is not None else update_public_key_b64()
    if not key_b64:
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            "cryptography 库不可用，无法验证更新签名。请安装 cryptography 后重试。"
        ) from exc
    try:
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(key_b64))
        signature = base64.b64decode(str(signature_b64).strip())
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"更新签名/公钥编码无效：{exc}") from exc
    data = Path(zip_path).read_bytes()
    try:
        pub.verify(signature, data)
    except InvalidSignature as exc:
        raise ValueError("更新包签名验证失败：可能被篡改或来源不可信。") from exc
    return True


def install_requires_elevation(install_dir: str | Path | None = None) -> bool:
    """True when the install directory is not writable by the current user."""
    target = Path(install_dir) if install_dir else install_dir_for_executable()
    probe = target / ".spw-update-write-test"
    try:
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return False
    except Exception:  # noqa: BLE001
        return True


def _zip_target(package_dir: Path, info: zipfile.ZipInfo) -> Path:
    raw = str(info.filename or "").replace("\\", "/")
    if not raw:
        raise ValueError("update package contains an empty path")
    rel = PurePosixPath(raw)
    if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        raise ValueError(f"unsafe path in update package: {info.filename}")
    target = (package_dir / Path(*rel.parts)).resolve()
    root = package_dir.resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"unsafe path in update package: {info.filename}")
    return target


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    return ((info.external_attr >> 16) & 0o170000) == 0o120000


def _validate_zip_manifest(infos: list[zipfile.ZipInfo]) -> None:
    if len(infos) > MAX_UPDATE_ZIP_ENTRIES:
        raise ValueError("update package contains too many files")
    total = 0
    for info in infos:
        total += int(info.file_size or 0)
        if total > MAX_UPDATE_UNCOMPRESSED_BYTES:
            raise ValueError("update package is too large after extraction")


def extract_update_zip(zip_path: str | Path, package_dir: str | Path) -> Path:
    package_dir = Path(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        infos = zf.infolist()
        _validate_zip_manifest(infos)
        for info in infos:
            if _is_zip_symlink(info):
                raise ValueError(f"update package contains unsupported symlink: {info.filename}")
            target = _zip_target(package_dir, info)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    exe = package_dir / APP_EXE_NAME
    if exe.is_file():
        return validate_package_dir(package_dir)
    matches = list(package_dir.rglob(APP_EXE_NAME))
    if not matches:
        raise ValueError(f"{APP_EXE_NAME} not found in update package")
    return validate_package_dir(matches[0].parent)


def validate_package_dir(package_dir: str | Path) -> Path:
    """Require the expected PyInstaller onedir shape before applying."""
    package_dir = Path(package_dir)
    exe = package_dir / APP_EXE_NAME
    if not exe.is_file():
        raise ValueError(f"{APP_EXE_NAME} not found in update package")
    internal = package_dir / "_internal"
    if not internal.is_dir():
        raise ValueError("update package is missing PyInstaller _internal directory")
    return package_dir


def run_packaged_smoke(exe_path: str | Path, *, timeout: int = 30) -> None:
    """Start an extracted packaged app in offscreen smoke mode."""
    exe_path = Path(exe_path)
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["SPECIMEN_WORKBENCH_ALLOW_MULTI"] = "1"
    try:
        completed = subprocess.run(
            [str(exe_path), "--smoke"],
            cwd=str(exe_path.parent),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"packaged smoke test timed out after {timeout} seconds") from exc
    combined = f"{completed.stdout}\n{completed.stderr}"
    if completed.returncode != 0 or PACKAGED_ERROR_RE.search(combined):
        detail = "\n".join(line for line in combined.splitlines() if line.strip())[-1200:]
        raise RuntimeError(
            f"packaged smoke test failed with exit code {completed.returncode}: {detail}"
        )


def ps_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def make_update_script(
    *,
    install_dir: str | Path,
    package_dir: str | Path,
    exe_path: str | Path,
    backup_dir: str | Path,
    protected_dir: str | Path,
    pid: int,
    protected_relative_paths: tuple[str, ...] = PROTECTED_RELATIVE_PATHS,
) -> str:
    """PowerShell script that runs after the app exits, then restarts it."""
    install = ps_quote(install_dir)
    package = ps_quote(package_dir)
    exe = ps_quote(exe_path)
    backup = ps_quote(backup_dir)
    protected = ps_quote(protected_dir)
    protected_items = "@(" + ", ".join(ps_quote(p) for p in protected_relative_paths) + ")"
    return rf"""$ErrorActionPreference = 'Stop'
$installDir = {install}
$packageDir = {package}
$exePath = {exe}
$backupDir = {backup}
$protectedDir = {protected}
$pidToWait = {int(pid)}
$protectedRelPaths = {protected_items}
$logPath = Join-Path (Split-Path -Parent $backupDir) 'apply-update.log'

function Write-UpdateLog([string]$Message) {{
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -LiteralPath $logPath -Value "$stamp $Message"
}}

function Copy-Tree([string]$Source, [string]$Destination) {{
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    foreach ($item in Get-ChildItem -LiteralPath $Source -Force) {{
        Copy-Item -LiteralPath $item.FullName -Destination $Destination -Recurse -Force
    }}
}}

function Clear-InstallDir {{
    foreach ($item in Get-ChildItem -LiteralPath $installDir -Force) {{
        Remove-Item -LiteralPath $item.FullName -Recurse -Force
    }}
}}

function Save-ProtectedFiles {{
    if (Test-Path -LiteralPath $protectedDir) {{
        Remove-Item -LiteralPath $protectedDir -Recurse -Force
    }}
    foreach ($rel in $protectedRelPaths) {{
        $src = Join-Path $installDir $rel
        if (Test-Path -LiteralPath $src) {{
            $dst = Join-Path $protectedDir $rel
            New-Item -ItemType Directory -Path (Split-Path -Parent $dst) -Force | Out-Null
            Copy-Item -LiteralPath $src -Destination $dst -Force
        }}
    }}
}}

function Restore-ProtectedFiles {{
    foreach ($rel in $protectedRelPaths) {{
        $src = Join-Path $protectedDir $rel
        if (Test-Path -LiteralPath $src) {{
            $dst = Join-Path $installDir $rel
            New-Item -ItemType Directory -Path (Split-Path -Parent $dst) -Force | Out-Null
            Copy-Item -LiteralPath $src -Destination $dst -Force
        }}
    }}
}}

function Invoke-Smoke([string]$Path) {{
    $oldQt = $env:QT_QPA_PLATFORM
    $oldMulti = $env:SPECIMEN_WORKBENCH_ALLOW_MULTI
    try {{
        $env:QT_QPA_PLATFORM = 'offscreen'
        $env:SPECIMEN_WORKBENCH_ALLOW_MULTI = '1'
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $Path
        $psi.Arguments = '--smoke'
        $psi.WorkingDirectory = Split-Path -Parent $Path
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true
        $proc = New-Object System.Diagnostics.Process
        $proc.StartInfo = $psi
        [void]$proc.Start()
        if (-not $proc.WaitForExit(30000)) {{
            try {{ $proc.Kill($true) }} catch {{ $proc.Kill() }}
            throw 'installed smoke test timed out after 30 seconds'
        }}
        $stdout = $proc.StandardOutput.ReadToEnd()
        $stderr = $proc.StandardError.ReadToEnd()
        $combined = "$stdout`n$stderr"
        if ($proc.ExitCode -ne 0 -or $combined -match 'Traceback|PermissionError|ModuleNotFoundError|ImportError') {{
            Write-UpdateLog $combined
            throw "installed smoke test failed with exit code $($proc.ExitCode)"
        }}
    }} finally {{
        if ($null -eq $oldQt) {{ Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue }} else {{ $env:QT_QPA_PLATFORM = $oldQt }}
        if ($null -eq $oldMulti) {{ Remove-Item Env:\SPECIMEN_WORKBENCH_ALLOW_MULTI -ErrorAction SilentlyContinue }} else {{ $env:SPECIMEN_WORKBENCH_ALLOW_MULTI = $oldMulti }}
    }}
}}

Start-Sleep -Seconds 1
if ($pidToWait -gt 0) {{
    try {{ Wait-Process -Id $pidToWait -Timeout 90 -ErrorAction SilentlyContinue }} catch {{ }}
}}

try {{
    Write-UpdateLog 'Starting update apply'
    New-Item -ItemType Directory -Path (Split-Path -Parent $backupDir) -Force | Out-Null
    if (Test-Path -LiteralPath $backupDir) {{
        Remove-Item -LiteralPath $backupDir -Recurse -Force
    }}
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null

    Copy-Tree -Source $installDir -Destination $backupDir
    Save-ProtectedFiles

    Clear-InstallDir
    Copy-Tree -Source $packageDir -Destination $installDir
    Restore-ProtectedFiles
    Invoke-Smoke -Path $exePath
    Write-UpdateLog 'Update apply succeeded'
    Start-Process -FilePath $exePath -WorkingDirectory $installDir
}} catch {{
    Write-UpdateLog "Update failed: $($_.Exception.Message)"
    if (Test-Path -LiteralPath $backupDir) {{
        try {{
            Clear-InstallDir
            Copy-Tree -Source $backupDir -Destination $installDir
            Restore-ProtectedFiles
            Write-UpdateLog 'Rollback succeeded'
            Start-Process -FilePath $exePath -WorkingDirectory $installDir
        }} catch {{
            Write-UpdateLog "Rollback failed: $($_.Exception.Message)"
        }}
    }}
    throw
}}
"""


def prepare_update(
    release: ReleaseInfo,
    *,
    executable: str | None = None,
    progress_cb=None,
    smoke: bool | None = None,
) -> PreparedUpdate:
    install_dir = install_dir_for_executable(executable)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    work_dir = Path(tempfile.gettempdir()) / f"specimen-photo-workbench-update-{stamp}"
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    zip_path = work_dir / release.asset_name
    download_file(release.asset_url, zip_path, progress_cb=progress_cb)
    verify_downloaded_package(release, zip_path)
    # Authenticity: if a public key is configured, the release MUST carry a valid
    # Ed25519 signature. Refuse to apply otherwise (prevents forged packages).
    if signature_required():
        if not release.signature_url:
            raise ValueError(
                "更新已启用签名校验，但该版本缺少 .sig 签名文件，拒绝安装。"
            )
        signature_b64 = fetch_signature(release.signature_url)
        verify_update_signature(zip_path, signature_b64)
    package_dir = extract_update_zip(zip_path, work_dir / "package")
    if smoke is None:
        smoke = can_self_update(executable=executable)
    if smoke:
        run_packaged_smoke(package_dir / APP_EXE_NAME)
    backup_dir = Path(tempfile.gettempdir()) / f"specimen-photo-workbench-backup-{stamp}"
    protected_dir = work_dir / "protected"
    script_path = work_dir / "apply-update.ps1"
    script_path.write_text(
        make_update_script(
            install_dir=install_dir,
            package_dir=package_dir,
            exe_path=install_dir / APP_EXE_NAME,
            backup_dir=backup_dir,
            protected_dir=protected_dir,
            pid=os.getpid(),
        ),
        encoding="utf-8",
    )
    return PreparedUpdate(
        release=release,
        work_dir=work_dir,
        package_dir=package_dir,
        script_path=script_path,
        requires_elevation=install_requires_elevation(install_dir),
    )


def launch_update_script(script_path: str | Path, *, elevate: bool = False) -> None:
    import subprocess

    script_path = Path(script_path)
    if elevate:
        # Relaunch the apply script elevated (UAC prompt). Needed when the install
        # dir is under Program Files or otherwise not user-writable.
        inner = (
            "Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList "
            "@('-NoProfile','-ExecutionPolicy','Bypass','-File',"
            f"'{ps_quote(script_path)[1:-1]}')"
        )
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                inner,
            ],
            cwd=str(script_path.parent),
            close_fds=True,
        )
        return
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
        ],
        cwd=str(script_path.parent),
        close_fds=True,
    )
