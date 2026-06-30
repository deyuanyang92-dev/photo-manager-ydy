"""archive_service.py — JPG archival with pre-delete safety checks.

Safety invariants (hard rules — must never be violated):
  1. TIFF is NEVER deleted.
  2. delete_jpg=True is the product default; deletion happens only after verification.
  3. If delete_jpg=True, JPGs are deleted ONLY after ALL three checks pass:
       a. ZIP file exists and size > 32 bytes.
       b. ZIP integrity check passes.
       c. each archived JPG matches original name/size/SHA-256.
  4. When cjxl/djxl are available, new archives use internal JXL lossless-JPEG
     transcode for higher compression. The software restores/presents JPGs.
     If the JXL bridge is unavailable or fails verification, fallback is exact
     plain-JPG ZIP.

ZipResult fields mirror archive.js archiveJpgGroup return value.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


# ── JXL bridge compatibility knobs ───────────────────────────────────────────

EFFORT_MAP = {
    "standard": {"jxl": 7},
    "maximum":  {"jxl": 9},
}


# ── cjxl / djxl availability cache ───────────────────────────────────────────

_cjxl_available: Optional[bool] = None
_djxl_available: Optional[bool] = None


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_jpg_probe(path: str) -> tuple[int, str, int]:
    """Return size, SHA-256, and the best ZIP method for one JPG."""
    original_size = os.path.getsize(path)
    digest = hashlib.sha256()
    compressor = zlib.compressobj(level=9, wbits=-15)
    compressed_size = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
            compressed_size += len(compressor.compress(chunk))
    compressed_size += len(compressor.flush())
    method = (
        zipfile.ZIP_DEFLATED
        if compressed_size < original_size
        else zipfile.ZIP_STORED
    )
    return original_size, digest.hexdigest(), method


def _unique_name(original_name: str, used_names: set[str], suffix: str | None = None) -> str:
    base = Path(original_name)
    stem = base.stem
    ext = suffix if suffix is not None else base.suffix
    candidate = stem + ext
    index = 2
    while candidate in used_names:
        candidate = f"{stem}-{index}{ext}"
        index += 1
    used_names.add(candidate)
    return candidate


def _verify_jxl_zip_complete(zip_path: str, manifest_files: list[dict]) -> CheckResult:
    """Verify a JXL archive ZIP can restore every original JPG exactly."""
    if not os.path.isfile(zip_path) or os.path.getsize(zip_path) <= 32:
        return CheckResult(ok=False, reason="ZIP 生成失败或体积异常")
    verify_dir = tempfile.mkdtemp(prefix="specimen-verify-jxl-")
    try:
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                bad = zf.testzip()
                if bad:
                    return CheckResult(ok=False, reason=f"ZIP 内部文件损坏: {bad}")
                zf.extractall(verify_dir)
        except (OSError, zipfile.BadZipFile) as exc:
            return CheckResult(ok=False, reason=f"ZIP 校验失败：{exc}")

        manifest_path = os.path.join(verify_dir, "manifest.json")
        if not os.path.isfile(manifest_path):
            return CheckResult(ok=False, reason="ZIP 清单缺失")
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)

        check_files: list[dict] = []
        for f in manifest_files:
            row = dict(f)
            row["jxlPath"] = os.path.join(verify_dir, row["archiveName"])
            check_files.append(row)
        manifest_check = verify_manifest_complete(manifest, check_files)
        if not manifest_check.ok:
            return manifest_check
        return verify_jxl_recoverable(check_files, verify_dir)
    finally:
        shutil.rmtree(verify_dir, ignore_errors=True)


def _archive_group_jxl(
    jpg_paths: list[str],
    zip_path: str,
    tiff_basename: str,
    *,
    delete_jpg: bool,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> ZipResult:
    """Archive JPGs as internal JXL, with software restore back to JPG."""
    os.makedirs(str(Path(zip_path).parent), exist_ok=True)
    temp_dir = tempfile.mkdtemp(prefix="specimen-jxl-")
    manifest_files: list[dict] = []
    total_original = 0
    total_compressed = 0
    used_names: set[str] = set()
    total_files = len(jpg_paths)
    try:
        for index, jpg_path in enumerate(jpg_paths, start=1):
            original_name = os.path.basename(jpg_path)
            archive_name = _unique_name(original_name, used_names, suffix=".jxl")
            original_size = os.path.getsize(jpg_path)
            original_sha256 = _sha256_file(jpg_path)
            jxl_path = os.path.join(temp_dir, archive_name)
            compress_to_jxl(jpg_path, jxl_path, effort=9)
            compressed_size = os.path.getsize(jxl_path)
            manifest_files.append({
                "originalName": original_name,
                "archiveName": archive_name,
                "originalSize": original_size,
                "compressedSize": compressed_size,
                "originalSha256": original_sha256,
                "zipCompression": "jpeg-xl-lossless",
                "jxlPath": jxl_path,
            })
            total_original += original_size
            total_compressed += compressed_size
            if progress_callback is not None:
                progress_callback(index, total_files, original_name)

        manifest = {
            "version": 3,
            "createdAt": _iso_now(),
            "tiffBasename": tiff_basename,
            "format": "jxl-zip",
            "method": "jpeg-xl-lossless-transcode",
            "files": [
                {k: v for k, v in f.items() if k != "jxlPath"}
                for f in manifest_files
            ],
            "totalOriginal": total_original,
            "totalCompressed": total_compressed,
        }

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2),
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )
            for f in manifest_files:
                zf.write(
                    f["jxlPath"],
                    f["archiveName"],
                    compress_type=zipfile.ZIP_STORED,
                )

        zip_size = os.path.getsize(zip_path)
        if zip_size < 32:
            raise RuntimeError("ZIP 生成失败或体积异常")

        zip_check = _verify_jxl_zip_complete(zip_path, manifest_files)
        if not zip_check.ok:
            raise RuntimeError(zip_check.reason or "JXL 归档校验失败")

        actual_delete = bool(delete_jpg)
        deletion_skipped_reason = ""
        if actual_delete:
            for p in jpg_paths:
                os.unlink(p)

        saved_percent = (
            max(0, round((1 - total_compressed / total_original) * 100))
            if total_original > 0
            else 0
        )
        return ZipResult(
            zip_path=zip_path,
            zip_size=zip_size,
            file_count=len(jpg_paths),
            total_original=total_original,
            total_compressed=total_compressed,
            saved_percent=saved_percent,
            delete_jpg=actual_delete,
            requested_delete_jpg=bool(delete_jpg),
            deletion_skipped_reason=deletion_skipped_reason,
            manifest=manifest,
            ok=True,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _check_tool(name: str) -> bool:
    try:
        subprocess.run([name, "--version"], capture_output=True, timeout=10)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def has_cjxl() -> bool:
    """Return True if cjxl binary is available."""
    global _cjxl_available
    if _cjxl_available is None:
        _cjxl_available = _check_tool("cjxl")
    return _cjxl_available


def has_djxl() -> bool:
    """Return True if djxl binary is available.

    Oracle: archive.js:17-26.
    """
    global _djxl_available
    if _djxl_available is None:
        _djxl_available = _check_tool("djxl")
    return _djxl_available


def reset_tool_cache() -> None:
    """Reset availability caches (for testing)."""
    global _cjxl_available, _djxl_available
    _cjxl_available = None
    _djxl_available = None


# ── JXL bridge helpers ────────────────────────────────────────────────────────

def compress_to_jxl(
    input_path: str,
    output_path: str,
    effort: int = 9,
) -> None:
    """Compress *input_path* to JXL at *output_path* using cjxl.

    Flags: --lossless_jpeg=1 -e <effort>.
    For JPEG input, this preserves the original JPEG bitstream so djxl can
    restore the original .jpg bytes exactly.
    NO --modular, --quality, or -j flags.

    Raises RuntimeError if cjxl is not available (caller decides fallback).
    Raises subprocess.CalledProcessError on cjxl failure.
    """
    if not has_cjxl():
        raise RuntimeError("cjxl not available")
    subprocess.run(
        [
            "cjxl",
            input_path,
            output_path,
            "--lossless_jpeg=1",
            "-e",
            str(effort),
        ],
        check=True,
        capture_output=True,
    )


# ── Safety checks ─────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    ok: bool
    reason: str = ""


def verify_manifest_complete(
    manifest: dict,
    manifest_files: list[dict],
) -> CheckResult:
    """Verify the manifest covers all files with matching names/sizes.

    Oracle: archive.js:49-61.
    """
    if not manifest or not isinstance(manifest.get("files"), list):
        return CheckResult(ok=False, reason="ZIP 清单缺失")
    if len(manifest["files"]) != len(manifest_files):
        return CheckResult(ok=False, reason="ZIP 清单数量不一致")
    for f in manifest_files:
        row = next(
            (x for x in manifest["files"] if x.get("archiveName") == f["archiveName"]),
            None,
        )
        if not row:
            return CheckResult(ok=False, reason="ZIP 清单缺少：" + f["archiveName"])
        if row.get("originalName") != f["originalName"]:
            return CheckResult(ok=False, reason="ZIP 清单原文件名不一致：" + f["originalName"])
        if row.get("originalSize") != f["originalSize"] or row.get("compressedSize") != f["compressedSize"]:
            return CheckResult(ok=False, reason="ZIP 清单大小不一致：" + f["originalName"])
        if row.get("originalSha256") != f.get("originalSha256"):
            return CheckResult(ok=False, reason="ZIP 清单哈希不一致：" + f["originalName"])
    return CheckResult(ok=True)


def verify_jxl_recoverable(
    manifest_files: list[dict],
    temp_dir: str,
) -> CheckResult:
    """Verify each JXL can be decoded back by djxl (size > 0).

    Oracle: archive.js:28-47.

    If djxl is not available → return failure immediately (do NOT delete JPGs).
    """
    if not has_djxl():
        return CheckResult(ok=False, reason="缺少 djxl，不能做 JXL 可恢复校验，已保留 JPG")

    for f in manifest_files:
        jxl_path = f.get("jxlPath", "")
        if not os.path.isfile(jxl_path):
            return CheckResult(ok=False, reason="JXL 文件缺失：" + f["archiveName"])
        expected_hash = f.get("originalSha256")
        # cjxl-unavailable fallback stores the original JPEG byte-for-byte.
        if not str(f.get("archiveName", "")).lower().endswith(".jxl"):
            if expected_hash and _sha256_file(jxl_path) != expected_hash:
                return CheckResult(
                    ok=False,
                    reason="归档内 JPEG 与原始文件不一致：" + f["originalName"],
                )
            continue
        restored_path = os.path.join(temp_dir, "restore-" + f["originalName"])
        try:
            subprocess.run(
                ["djxl", jxl_path, restored_path],
                check=True,
                capture_output=True,
                timeout=120,
            )
            if not os.path.isfile(restored_path) or os.path.getsize(restored_path) <= 0:
                return CheckResult(ok=False, reason="JXL 恢复输出异常：" + f["archiveName"])
            if expected_hash and _sha256_file(restored_path) != expected_hash:
                return CheckResult(
                    ok=False,
                    reason="JXL 恢复文件与原始 JPEG 不一致：" + f["originalName"],
                )
        except Exception as e:
            msg = str(e)
            return CheckResult(ok=False, reason="JXL 恢复校验失败：" + f["archiveName"] + " · " + msg)
    return CheckResult(ok=True)


def verify_jpg_zip_complete(
    zip_path: str,
    manifest_files: list[dict],
) -> CheckResult:
    """Verify a user-facing JPG ZIP before loose JPG deletion.

    The archive intentionally contains only JPG files, so normal Windows/macOS
    extraction produces the original photos directly.  The in-memory
    ``manifest_files`` list is used only for the deletion safety check.
    """
    if not os.path.isfile(zip_path) or os.path.getsize(zip_path) <= 32:
        return CheckResult(ok=False, reason="ZIP 生成失败或体积异常")

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            bad = zf.testzip()
            if bad:
                return CheckResult(ok=False, reason=f"ZIP 内部文件损坏: {bad}")
            names = zf.namelist()
            if len(names) != len(manifest_files):
                return CheckResult(ok=False, reason="ZIP 文件数量不一致")

            for f in manifest_files:
                archive_name = f["archiveName"]
                if archive_name not in names:
                    return CheckResult(ok=False, reason="ZIP 缺少：" + archive_name)
                info = zf.getinfo(archive_name)
                if info.file_size != f["originalSize"]:
                    return CheckResult(ok=False, reason="ZIP 内 JPG 大小不一致：" + archive_name)

                digest = hashlib.sha256()
                with zf.open(archive_name, "r") as fh:
                    for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != f["originalSha256"]:
                    return CheckResult(ok=False, reason="ZIP 内 JPG 哈希不一致：" + archive_name)
    except (OSError, zipfile.BadZipFile) as exc:
        return CheckResult(ok=False, reason=f"ZIP 校验失败：{exc}")

    return CheckResult(ok=True)


# ── ZipResult ─────────────────────────────────────────────────────────────────

@dataclass
class ZipResult:
    zip_path: str
    zip_size: int
    file_count: int
    total_original: int
    total_compressed: int
    saved_percent: int
    delete_jpg: bool           # whether JPGs were actually deleted
    requested_delete_jpg: bool
    deletion_skipped_reason: str
    manifest: dict
    ok: bool = True


# ── archive_group ─────────────────────────────────────────────────────────────

def archive_group(
    jpg_paths: list[str],
    tiff_path: str,
    project_dir: str,
    delete_jpg: bool = True,
    method: str = "standard",
    concurrency: int = 4,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    output_dir: Optional[str] = None,
) -> ZipResult:
    """Archive original JPGs, verify, maybe delete loose JPGs.

    Safety invariants enforced here:
      - TIFF is NEVER deleted (not even accepted as input for deletion).
      - delete_jpg defaults to True, but verification failure always keeps JPGs.
      - Deletion only happens after ZIP integrity + per-file SHA-256 checks pass.
      - With cjxl/djxl, ZIP may contain internal JXL to improve compression.
        Software restore/presentation always converts back to original JPG.
      - Without working JXL tools, fallback ZIP stores exact JPGs directly.

    Oracle: archive.js:67-190.

    Args:
        jpg_paths:   Absolute paths to source JPGs.
        tiff_path:   Path to the composed TIFF (used for naming; never deleted).
        project_dir: Project root (ZIP placed in results/ subdir or same dir as TIFF).
        delete_jpg:  If True, delete JPGs after all safety checks pass.
        method:      Legacy compatibility argument; ignored for new plain-JPG ZIPs.
        output_dir:  Optional explicit ZIP directory.  With an open project,
                     callers pass project results/ so archives never land in
                     the source photo folder.

    Returns:
        ZipResult dataclass.
    """
    if not jpg_paths:
        raise ValueError("未指定 JPG 原片")
    for p in jpg_paths:
        if not os.path.isfile(p):
            raise FileNotFoundError(f"文件不存在: {p}")

    tiff_basename = Path(tiff_path).stem
    zip_dir = str(Path(output_dir) if output_dir else Path(tiff_path).parent)
    zip_path = os.path.join(zip_dir, tiff_basename + ".zip")

    if has_cjxl() and has_djxl():
        try:
            return _archive_group_jxl(
                jpg_paths,
                zip_path,
                tiff_basename,
                delete_jpg=delete_jpg,
                progress_callback=progress_callback,
            )
        except Exception:
            # If the high-compression transcode fails on a malformed/unsupported
            # JPG, keep the workflow usable and fall back to exact plain JPG ZIP.
            pass

    manifest_files: list[dict] = []
    total_original = 0
    total_compressed = 0

    used_names: set[str] = set()

    def _unique_archive_name(original_name: str) -> str:
        candidate = original_name
        stem = Path(original_name).stem
        suffix = Path(original_name).suffix
        index = 2
        while candidate in used_names:
            candidate = f"{stem}-{index}{suffix}"
            index += 1
        used_names.add(candidate)
        return candidate

    total_files = len(jpg_paths)
    for index, jpg_path in enumerate(jpg_paths, start=1):
        original_name = os.path.basename(jpg_path)
        archive_name = _unique_archive_name(original_name)
        original_size, original_sha256, zip_method = _archive_jpg_probe(jpg_path)
        manifest_files.append({
            "originalName": original_name,
            "archiveName": archive_name,
            "originalSize": original_size,
            "compressedSize": original_size,
            "originalSha256": original_sha256,
            "zipCompression": (
                "deflate9" if zip_method == zipfile.ZIP_DEFLATED else "store"
            ),
            "_zipMethod": zip_method,
        })
        total_original += original_size
        if progress_callback is not None:
            progress_callback(index, total_files, original_name)

    manifest = {
        "version": 2,
        "createdAt": _iso_now(),
        "tiffBasename": tiff_basename,
        "format": "jpg-zip",
        "method": "adaptive-plain-jpg-zip",
        "files": [],
        "totalOriginal": total_original,
        "totalCompressed": 0,
    }

    os.makedirs(zip_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w") as zf:
        for jpg_path, f in zip(jpg_paths, manifest_files):
            zip_method = int(f["_zipMethod"])
            zf.write(
                jpg_path,
                f["archiveName"],
                compress_type=zip_method,
                compresslevel=9 if zip_method == zipfile.ZIP_DEFLATED else None,
            )

    zip_size = os.path.getsize(zip_path)
    if zip_size < 32:
        raise RuntimeError("ZIP 生成失败或体积异常")

    with zipfile.ZipFile(zip_path, "r") as zf:
        bad = zf.testzip()
        if bad:
            raise RuntimeError(f"ZIP 内部文件损坏: {bad}")
        total_compressed = 0
        for f in manifest_files:
            compressed_size = zf.getinfo(f["archiveName"]).compress_size
            f["compressedSize"] = compressed_size
            total_compressed += compressed_size

    manifest["totalCompressed"] = total_compressed
    manifest["files"] = [
        {k: v for k, v in f.items() if not k.startswith("_")}
        for f in manifest_files
    ]

    saved_percent = (
        max(0, round((1 - total_compressed / total_original) * 100))
        if total_original > 0
        else 0
    )

    actual_delete = False
    deletion_skipped_reason = ""

    if delete_jpg:
        zip_check = verify_jpg_zip_complete(zip_path, manifest_files)
        if zip_check.ok:
            actual_delete = True
        else:
            deletion_skipped_reason = zip_check.reason or "删除前校验失败，已保留 JPG"

    if actual_delete:
        for p in jpg_paths:
            os.unlink(p)

    return ZipResult(
        zip_path=zip_path,
        zip_size=zip_size,
        file_count=len(jpg_paths),
        total_original=total_original,
        total_compressed=total_compressed,
        saved_percent=saved_percent,
        delete_jpg=actual_delete,
        requested_delete_jpg=bool(delete_jpg),
        deletion_skipped_reason=deletion_skipped_reason,
        manifest=manifest,
        ok=True,
    )


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(tz=timezone.utc).isoformat()


# ── restore_archive — one-click recover original JPGs from a ZIP ───────────────

@dataclass
class RestoreResult:
    output_dir: str
    restored: list[str] = field(default_factory=list)  # 还原成功的 JPG 绝对路径
    skipped: list[str] = field(default_factory=list)    # 已存在被跳过(overwrite=False)
    failures: list[str] = field(default_factory=list)   # 文件名 + 原因
    count: int = 0
    ok: bool = True
    reason: str = ""


def restore_archive(
    zip_path: str,
    output_dir: str,
    overwrite: bool = False,
) -> RestoreResult:
    """Recover the original JPGs from an archive ZIP (read-only, additive).

    Archives may contain raw JPG entries or internal JXL entries. JXL entries
    are decoded with djxl back to the original JPG.

    Safety: this only READS the ZIP and WRITES new JPGs under output_dir. It never
    deletes anything. If any .jxl entry is present but djxl is unavailable, it
    aborts before writing anything (no half-products).

    Args:
        zip_path:   Archive ZIP produced by archive_group.
        output_dir: Where to write recovered JPGs (created if absent).
        overwrite:  If False, existing target files are skipped, not overwritten.

    Returns:
        RestoreResult.
    """
    if not os.path.isfile(zip_path):
        raise FileNotFoundError(f"归档文件不存在: {zip_path}")
    os.makedirs(output_dir, exist_ok=True)

    temp_dir = tempfile.mkdtemp(prefix="specimen-restore-")
    restored: list[str] = []
    skipped: list[str] = []
    failures: list[str] = []

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            zf.extractall(temp_dir)

        # ── Build entries: (archiveName, originalName, originalSize, sha256) ───
        manifest_path = os.path.join(temp_dir, "manifest.json")
        entries: list[tuple[str, str, Optional[int], Optional[str]]] = []
        if os.path.isfile(manifest_path):
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
            for f in manifest.get("files", []):
                entries.append((
                    f["archiveName"],
                    f.get("originalName") or f["archiveName"],
                    f.get("originalSize"),
                    f.get("originalSha256"),
                ))
        else:
            # manifest 缺失 → 退化:按 ZIP 内文件名还原(.jxl → stem.jpg)
            for name in names:
                if name == "manifest.json":
                    continue
                if name.lower().endswith(".jxl"):
                    original = Path(name).stem + ".jpg"
                else:
                    original = name
                entries.append((name, original, None, None))

        # ── djxl gate: any .jxl but no djxl → abort, write nothing ────────────
        needs_djxl = any(a.lower().endswith(".jxl") for a, _, _, _ in entries)
        if needs_djxl and not has_djxl():
            return RestoreResult(
                output_dir=output_dir,
                ok=False,
                reason="缺少 djxl 解码工具，无法还原 JXL，未输出任何文件",
                failures=["缺少 djxl 解码工具"],
            )

        for archive_name, original_name, original_size, original_sha256 in entries:
            src = os.path.join(temp_dir, archive_name)
            dst = os.path.join(output_dir, original_name)

            if not os.path.isfile(src):
                failures.append(f"{archive_name}：归档内缺失")
                continue
            if os.path.isfile(dst) and not overwrite:
                skipped.append(dst)
                continue

            try:
                if archive_name.lower().endswith(".jxl"):
                    subprocess.run(
                        ["djxl", src, dst],
                        check=True,
                        capture_output=True,
                        timeout=120,
                    )
                    if not os.path.isfile(dst) or os.path.getsize(dst) <= 0:
                        failures.append(f"{original_name}：还原输出异常")
                        if os.path.isfile(dst):
                            os.unlink(dst)
                        continue
                    if original_size is not None and os.path.getsize(dst) != original_size:
                        failures.append(
                            f"{original_name}：大小与清单不一致"
                            f"（{os.path.getsize(dst)}≠{original_size}）"
                        )
                        os.unlink(dst)
                        continue
                    if original_sha256 and _sha256_file(dst) != original_sha256:
                        failures.append(f"{original_name}：SHA-256 与清单不一致")
                        os.unlink(dst)
                        continue
                else:
                    shutil.copy2(src, dst)
                    if original_sha256 and _sha256_file(dst) != original_sha256:
                        failures.append(f"{original_name}：SHA-256 与清单不一致")
                        os.unlink(dst)
                        continue
                restored.append(dst)
            except Exception as e:
                failures.append(f"{original_name}：{e}")
                if os.path.isfile(dst):
                    try:
                        os.unlink(dst)
                    except OSError:
                        pass

        return RestoreResult(
            output_dir=output_dir,
            restored=restored,
            skipped=skipped,
            failures=failures,
            count=len(restored),
            ok=(len(failures) == 0),
        )

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
