"""archive_service.py — JPG archival with pre-delete safety checks.

Safety invariants (hard rules — must never be violated):
  1. Archive/organise never auto-deletes TIFF. Explicit user delete/undo lives
     outside this service.
  2. delete_jpg=True is the product default; deletion happens only after verification.
  3. If delete_jpg=True, JPGs are deleted ONLY after ALL three checks pass:
       a. ZIP file exists and size > 32 bytes.
       b. ZIP integrity check passes.
       c. each archived JPG matches original name/size/SHA-256.
  4. Standard archives store exact JPG bytes in a normal ZIP for speed. Optional
     high-compression mode may use internal JXL lossless-JPEG transcode.

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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Callable, Optional


# ── JXL bridge compatibility knobs ───────────────────────────────────────────

EFFORT_MAP = {
    # Daily organise must be fast: JPG is already compressed, so the standard
    # archive stores exact JPG bytes in a normal ZIP and verifies before delete.
    "standard": {"jxl": None},
    "maximum": {"jxl": 9},
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


def _measure_jpg_for_archive(path: str, *, adaptive: bool = False) -> tuple[int, str, int]:
    """Return size, SHA-256, and the best ZIP method for one JPG."""
    original_size = os.path.getsize(path)
    digest = hashlib.sha256()
    if not adaptive:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return original_size, digest.hexdigest(), zipfile.ZIP_STORED

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


def _reserve_unique_archive_name(original_name: str, used_names: set[str], suffix: str | None = None) -> str:
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
                _extract_zip_safely(zf, verify_dir)
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


def _archive_group_as_jxl_zip(
    jpg_paths: list[str],
    zip_path: str,
    tiff_basename: str,
    *,
    delete_jpg: bool,
    effort: int = 9,
    concurrency: int = 4,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    cancel_callback: Optional[Callable[[], bool]] = None,
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
        plans: list[tuple[str, str, str]] = []
        for jpg_path in jpg_paths:
            _raise_if_cancelled(cancel_callback)
            original_name = os.path.basename(jpg_path)
            archive_name = _reserve_unique_archive_name(original_name, used_names, suffix=".jxl")
            plans.append((jpg_path, original_name, archive_name))

        def _compress_one(plan: tuple[str, str, str]) -> dict:
            jpg_path, original_name, archive_name = plan
            _raise_if_cancelled(cancel_callback)
            original_size = os.path.getsize(jpg_path)
            original_sha256 = _sha256_file(jpg_path)
            jxl_path = os.path.join(temp_dir, archive_name)
            compress_to_jxl(jpg_path, jxl_path, effort=effort)
            _raise_if_cancelled(cancel_callback)
            compressed_size = os.path.getsize(jxl_path)
            return {
                "originalName": original_name,
                "archiveName": archive_name,
                "originalSize": original_size,
                "compressedSize": compressed_size,
                "originalSha256": original_sha256,
                "zipCompression": "jpeg-xl-lossless",
                "jxlPath": jxl_path,
            }

        worker_count = max(1, min(8, int(concurrency), total_files))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            for index, row in enumerate(executor.map(_compress_one, plans), start=1):
                manifest_files.append(row)
                total_original += int(row["originalSize"])
                total_compressed += int(row["compressedSize"])
                if progress_callback is not None:
                    progress_callback(index, total_files, str(row["originalName"]))

        manifest = {
            "version": 3,
            "createdAt": _utc_now_iso(),
            "tiffBasename": tiff_basename,
            "format": "jxl-zip",
            "method": "jpeg-xl-lossless-transcode",
            "jxlEffort": effort,
            "files": [
                {k: v for k, v in f.items() if k != "jxlPath"}
                for f in manifest_files
            ],
            "totalOriginal": total_original,
            "totalCompressed": total_compressed,
        }

        with zipfile.ZipFile(zip_path, "w") as zf:
            _raise_if_cancelled(cancel_callback)
            zf.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2),
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )
            for f in manifest_files:
                _raise_if_cancelled(cancel_callback)
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
            _raise_if_cancelled(cancel_callback)
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
            source_paths=tuple(jpg_paths),
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _is_archive_tool_available(name: str) -> bool:
    try:
        subprocess.run([name, "--version"], capture_output=True, timeout=10)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def has_cjxl() -> bool:
    """Return True if cjxl binary is available."""
    global _cjxl_available
    if _cjxl_available is None:
        _cjxl_available = _is_archive_tool_available("cjxl")
    return _cjxl_available


def has_djxl() -> bool:
    """Return True if djxl binary is available.

    Oracle: archive.js:17-26.
    """
    global _djxl_available
    if _djxl_available is None:
        _djxl_available = _is_archive_tool_available("djxl")
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
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
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
    # Runtime-only provenance for the two-phase delete gate. Do not persist
    # absolute source paths into the user-facing ZIP manifest.
    source_paths: tuple[str, ...] = ()


class ArchiveCancelled(RuntimeError):
    """Raised when the user cancels an in-progress archive before deletion."""


def _raise_if_cancelled(cancel_callback: Optional[Callable[[], bool]] = None) -> None:
    if cancel_callback is not None and bool(cancel_callback()):
        raise ArchiveCancelled("用户取消")


def commit_jpg_deletion_after_archive(
    result: ZipResult,
    jpg_paths: list[str],
    *,
    cancel_callback: Optional[Callable[[], bool]] = None,
) -> ZipResult:
    """Delete loose JPGs only after archive ZIP is verified and DB state is saved.

    Modern two-phase archive: build ZIP first, persist workflow state, then delete
    originals only when every safety gate still passes.
    """
    from dataclasses import replace

    if result.delete_jpg or not jpg_paths:
        return result

    def _source_key(path: str) -> str:
        return os.path.normcase(os.path.realpath(os.path.abspath(path)))

    archived_sources = tuple(getattr(result, "source_paths", ()) or ())
    requested_sources = tuple(jpg_paths)
    if not archived_sources:
        return replace(
            result,
            delete_jpg=False,
            requested_delete_jpg=True,
            deletion_skipped_reason="归档结果缺少源文件清单，已保留 JPG",
        )

    from collections import Counter

    if Counter(map(_source_key, requested_sources)) != Counter(
        map(_source_key, archived_sources)
    ):
        return replace(
            result,
            delete_jpg=False,
            requested_delete_jpg=True,
            deletion_skipped_reason="待删除 JPG 与本次归档源文件不一致，已全部保留",
        )

    manifest = dict(result.manifest or {})
    manifest_files = list(manifest.get("files") or [])
    fmt = str(manifest.get("format") or "")

    if fmt == "jxl-zip":
        zip_check = _verify_jxl_zip_complete(result.zip_path, manifest_files)
    else:
        zip_check = verify_jpg_zip_complete(result.zip_path, manifest_files)

    if not zip_check.ok:
        return replace(
            result,
            delete_jpg=False,
            requested_delete_jpg=True,
            deletion_skipped_reason=zip_check.reason or "删除前校验失败，已保留 JPG",
        )

    _raise_if_cancelled(cancel_callback)
    for path in jpg_paths:
        if os.path.isfile(path):
            os.unlink(path)

    return replace(
        result,
        delete_jpg=True,
        requested_delete_jpg=True,
        deletion_skipped_reason="",
    )


# ── archive_group ─────────────────────────────────────────────────────────────

def _archive_group_direct(
    jpg_paths: list[str],
    tiff_path: str,
    project_dir: str,
    delete_jpg: bool = True,
    method: str = "standard",
    concurrency: int = 4,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    cancel_callback: Optional[Callable[[], bool]] = None,
    output_dir: Optional[str] = None,
) -> ZipResult:
    """Archive original JPGs, verify, maybe delete loose JPGs.

    Safety invariants enforced here:
      - Archive/organise never auto-deletes TIFF.
      - delete_jpg defaults to True, but verification failure always keeps JPGs.
      - Deletion only happens after ZIP integrity + per-file SHA-256 checks pass.
      - Standard mode stores exact JPGs directly for speed.
      - Optional high-compression mode may use internal JXL; restore converts
        back to original JPG bytes.

    Oracle: archive.js:67-190.

    Args:
        jpg_paths:   Absolute paths to source JPGs.
        tiff_path:   Path to the composed TIFF (used for naming; not deleted by archive).
        project_dir: Project root (ZIP placed in results/ subdir or same dir as TIFF).
        delete_jpg:  If True, delete JPGs after all safety checks pass.
        method:      "standard" stores JPGs directly; "maximum" enables JXL
                     high-compression when tools are available; "adaptive"
                     keeps the legacy per-file ZIP deflate decision.
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
    _raise_if_cancelled(cancel_callback)

    method_key = str(method or "standard").strip().lower()
    use_jxl = method_key in {"maximum", "jxl", "jpeg-xl", "high", "high-compression"}
    adaptive_zip = method_key in {"adaptive", "legacy-adaptive", "compat"}

    if use_jxl and has_cjxl() and has_djxl():
        try:
            effort = int(EFFORT_MAP.get("maximum", {"jxl": 9}).get("jxl") or 9)
            return _archive_group_as_jxl_zip(
                jpg_paths,
                zip_path,
                tiff_basename,
                delete_jpg=delete_jpg,
                effort=effort,
                concurrency=concurrency,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
            )
        except ArchiveCancelled:
            raise
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
        _raise_if_cancelled(cancel_callback)
        original_name = os.path.basename(jpg_path)
        archive_name = _unique_archive_name(original_name)
        if adaptive_zip:
            original_size, original_sha256, zip_method = _measure_jpg_for_archive(
                jpg_path,
                adaptive=True,
            )
            mtime_ns = getattr(os.stat(jpg_path), "st_mtime_ns", 0)
        else:
            stat = os.stat(jpg_path)
            original_size = stat.st_size
            original_sha256 = ""
            zip_method = zipfile.ZIP_STORED
            mtime_ns = getattr(stat, "st_mtime_ns", 0)
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
            "_mtimeNs": mtime_ns,
        })
        total_original += original_size
        if adaptive_zip and progress_callback is not None:
            progress_callback(index, total_files, original_name)

    manifest = {
        "version": 2,
        "createdAt": _utc_now_iso(),
        "tiffBasename": tiff_basename,
        "format": "jpg-zip",
        "method": "adaptive-plain-jpg-zip" if adaptive_zip else "fast-plain-jpg-zip",
        "files": [],
        "totalOriginal": total_original,
        "totalCompressed": 0,
    }

    os.makedirs(zip_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w") as zf:
        for index, (jpg_path, f) in enumerate(zip(jpg_paths, manifest_files), start=1):
            _raise_if_cancelled(cancel_callback)
            zip_method = int(f["_zipMethod"])
            if adaptive_zip:
                zf.write(
                    jpg_path,
                    f["archiveName"],
                    compress_type=zip_method,
                    compresslevel=9 if zip_method == zipfile.ZIP_DEFLATED else None,
                )
            else:
                digest = hashlib.sha256()
                written = 0
                with open(jpg_path, "rb") as src:
                    with zf.open(f["archiveName"], "w", force_zip64=True) as dst:
                        for chunk in iter(lambda: src.read(1024 * 1024), b""):
                            _raise_if_cancelled(cancel_callback)
                            digest.update(chunk)
                            written += len(chunk)
                            dst.write(chunk)
                if written != f["originalSize"]:
                    raise RuntimeError("JPG 归档过程中大小发生变化：" + f["originalName"])
                after_stat = os.stat(jpg_path)
                if getattr(after_stat, "st_mtime_ns", 0) != f.get("_mtimeNs"):
                    raise RuntimeError("JPG 归档过程中被修改：" + f["originalName"])
                f["originalSha256"] = digest.hexdigest()
            if progress_callback is not None:
                progress_callback(index, total_files, f["originalName"])

    zip_size = os.path.getsize(zip_path)
    if zip_size < 32:
        raise RuntimeError("ZIP 生成失败或体积异常")

    with zipfile.ZipFile(zip_path, "r") as zf:
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
        _raise_if_cancelled(cancel_callback)
        if progress_callback is not None:
            progress_callback(total_files, total_files, "__verify_zip__")
        zip_check = verify_jpg_zip_complete(zip_path, manifest_files)
        if zip_check.ok:
            actual_delete = True
        else:
            deletion_skipped_reason = zip_check.reason or "删除前校验失败，已保留 JPG"

    if actual_delete:
        _raise_if_cancelled(cancel_callback)
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
        source_paths=tuple(jpg_paths),
    )


def archive_group(
    jpg_paths: list[str],
    tiff_path: str,
    project_dir: str,
    delete_jpg: bool = True,
    method: str = "standard",
    concurrency: int = 4,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    cancel_callback: Optional[Callable[[], bool]] = None,
    output_dir: Optional[str] = None,
) -> ZipResult:
    """Build and verify an archive before atomically publishing the final ZIP.

    A same-name archive is never opened for writing. The new archive is built
    in a sibling staging directory and replaces the destination only after all
    verification gates pass, so cancellation or failure preserves an existing
    valid ZIP.
    """
    tiff_basename = Path(tiff_path).stem
    zip_dir = Path(output_dir) if output_dir else Path(tiff_path).parent
    zip_dir.mkdir(parents=True, exist_ok=True)
    final_zip_path = zip_dir / f"{tiff_basename}.zip"
    staging_dir = Path(tempfile.mkdtemp(prefix=".archive-staging-", dir=zip_dir))

    try:
        staged_result = _archive_group_direct(
            jpg_paths,
            tiff_path,
            project_dir,
            delete_jpg=False,
            method=method,
            concurrency=concurrency,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
            output_dir=str(staging_dir),
        )
        _raise_if_cancelled(cancel_callback)

        staged_zip_path = Path(staged_result.zip_path)
        if not staged_zip_path.is_file():
            raise RuntimeError("归档暂存文件不存在")
        manifest_files = list((staged_result.manifest or {}).get("files") or [])
        if str((staged_result.manifest or {}).get("format") or "") == "jxl-zip":
            staged_check = _verify_jxl_zip_complete(
                str(staged_zip_path),
                manifest_files,
            )
        else:
            staged_check = verify_jpg_zip_complete(
                str(staged_zip_path),
                manifest_files,
            )
        if not staged_check.ok:
            raise RuntimeError(staged_check.reason or "归档暂存文件校验失败")
        os.replace(staged_zip_path, final_zip_path)

        published_result = replace(
            staged_result,
            zip_path=str(final_zip_path),
            zip_size=final_zip_path.stat().st_size,
            delete_jpg=False,
            requested_delete_jpg=bool(delete_jpg),
            deletion_skipped_reason="",
        )
        if delete_jpg:
            return commit_jpg_deletion_after_archive(
                published_result,
                jpg_paths,
            )
        return published_result
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(tz=timezone.utc).isoformat()


# ── restore_archive — one-click recover original JPGs from a ZIP ───────────────

def _safe_archive_member_path(root: str | Path, member_name: str) -> Path:
    """Resolve one ZIP member under *root* and reject traversal/absolute paths."""
    normalized = str(member_name or "").replace("\\", "/")
    member = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or member.is_absolute()
        or any(part in {"", ".", ".."} for part in member.parts)
        or (member.parts and ":" in member.parts[0])
    ):
        raise ValueError(f"ZIP 包含不安全路径: {member_name}")

    root_input = Path(root)
    root_path = root_input.resolve()
    target_input = root_input.joinpath(*member.parts)
    target = target_input.resolve()
    try:
        target.relative_to(root_path)
    except ValueError as exc:
        raise ValueError(f"ZIP 包含越界路径: {member_name}") from exc
    return target_input


def _extract_zip_safely(zf: zipfile.ZipFile, temp_dir: str) -> list[str]:
    """Extract regular ZIP members under a temporary root without Zip Slip."""
    names: list[str] = []
    seen_targets: set[str] = set()
    for info in zf.infolist():
        target = _safe_archive_member_path(temp_dir, info.filename)
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target_key = os.path.normcase(str(target))
        if target_key in seen_targets:
            raise ValueError(f"ZIP 包含重复路径: {info.filename}")
        seen_targets.add(target_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info, "r") as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
        names.append(info.filename)
    return names


def _safe_restore_destination(output_dir: str | Path, original_name: str) -> Path:
    return _safe_archive_member_path(output_dir, original_name)


def _restore_entry_atomically(
    src: str | Path,
    dst: str | Path,
    *,
    is_jxl: bool,
    original_size: Optional[int],
    original_sha256: Optional[str],
) -> None:
    """Decode/copy and verify one JPG before atomically replacing its target."""
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = dst_path.suffix or ".jpg"
    fd, staged_name = tempfile.mkstemp(
        prefix=".restore-staging-",
        suffix=suffix,
        dir=dst_path.parent,
    )
    os.close(fd)
    staged_path = Path(staged_name)
    try:
        if is_jxl:
            subprocess.run(
                ["djxl", str(src), str(staged_path)],
                check=True,
                capture_output=True,
                timeout=120,
            )
        else:
            shutil.copy2(src, staged_path)

        if not staged_path.is_file() or staged_path.stat().st_size <= 0:
            raise RuntimeError("还原输出异常")
        if original_size is not None and staged_path.stat().st_size != original_size:
            raise RuntimeError(
                f"大小与清单不一致（{staged_path.stat().st_size}≠{original_size}）"
            )
        if original_sha256 and _sha256_file(str(staged_path)) != original_sha256:
            raise RuntimeError("SHA-256 与清单不一致")
        os.replace(staged_path, dst_path)
    finally:
        try:
            staged_path.unlink(missing_ok=True)
        except OSError:
            pass


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
            names = _extract_zip_safely(zf, temp_dir)

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
            try:
                src = _safe_archive_member_path(temp_dir, archive_name)
                dst = _safe_restore_destination(output_dir, original_name)
            except ValueError as exc:
                failures.append(str(exc))
                continue

            if not src.is_file():
                failures.append(f"{archive_name}：归档内缺失")
                continue
            if dst.is_file() and not overwrite:
                skipped.append(str(dst))
                continue

            try:
                _restore_entry_atomically(
                    src,
                    dst,
                    is_jxl=archive_name.lower().endswith(".jxl"),
                    original_size=original_size,
                    original_sha256=original_sha256,
                )
                restored.append(str(dst))
            except Exception as e:
                failures.append(f"{original_name}：{e}")

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


def restore_archive_to_original_paths(
    zip_path: str,
    original_paths: list[str],
    overwrite: bool = False,
) -> RestoreResult:
    """Restore archived JPGs back to their original paths.

    This is the rollback path for "undo organise": it reads the verified ZIP,
    writes JPGs back to the paths still recorded on the group, and deletes
    nothing. Existing files are skipped unless overwrite=True.
    """
    if not os.path.isfile(zip_path):
        raise FileNotFoundError(f"归档文件不存在: {zip_path}")
    targets = [str(p) for p in original_paths if str(p or "").strip()]
    if not targets:
        return RestoreResult(
            output_dir="",
            ok=False,
            reason="没有可恢复的 JPG 原路径",
            failures=["没有可恢复的 JPG 原路径"],
        )

    temp_dir = tempfile.mkdtemp(prefix="specimen-restore-original-")
    restored: list[str] = []
    skipped: list[str] = []
    failures: list[str] = []
    output_dir = str(Path(targets[0]).parent) if targets else ""

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = [
                name
                for name in _extract_zip_safely(zf, temp_dir)
                if name != "manifest.json"
            ]

        manifest_path = os.path.join(temp_dir, "manifest.json")
        entries: list[tuple[str, Optional[int], Optional[str]]] = []
        if os.path.isfile(manifest_path):
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
            entries = [
                (
                    f["archiveName"],
                    f.get("originalSize"),
                    f.get("originalSha256"),
                )
                for f in manifest.get("files", [])
            ]
        else:
            entries = [(name, None, None) for name in names]

        if len(entries) != len(targets):
            return RestoreResult(
                output_dir=output_dir,
                restored=restored,
                skipped=skipped,
                failures=[
                    f"ZIP 内 JPG 数量与分组记录不一致（{len(entries)}≠{len(targets)}）"
                ],
                count=0,
                ok=False,
                reason="ZIP 内 JPG 数量与分组记录不一致",
            )

        needs_djxl = any(name.lower().endswith(".jxl") for name, _, _ in entries)
        if needs_djxl and not has_djxl():
            return RestoreResult(
                output_dir=output_dir,
                ok=False,
                reason="缺少 djxl 解码工具，无法还原 JXL，未输出任何文件",
                failures=["缺少 djxl 解码工具"],
            )

        for (archive_name, original_size, original_sha256), dst in zip(entries, targets):
            try:
                src = _safe_archive_member_path(temp_dir, archive_name)
            except ValueError as exc:
                failures.append(str(exc))
                continue
            if not src.is_file():
                failures.append(f"{archive_name}：归档内缺失")
                continue
            if os.path.isfile(dst) and not overwrite:
                # 场景(用户 2026-07-12 问"还原中途死机会不会丢数据"):
                #   还原的顺序是 写回 JPG -> 写库(撤归档登记) -> 删 ZIP。若在**写完
                #   JPG、还没写库**时断电/崩溃, 重跑还原时这些 JPG 已经躺在原位了 ——
                #   旧逻辑一律记成"跳过", 于是 count=0 -> 上层判定「未能还原」->
                #   归档登记和 ZIP 永远留着、组永远退不回, 用户彻底卡住。
                # 理由(Fable 5, 2026-07-12): 目标文件已存在且 **SHA-256 与归档清单
                #   一致** = 这张本来就是从这个归档还原出来的, 算「已还原」(restored),
                #   重跑就能把剩下的步骤走完(幂等可续跑)。内容**不一致**才算跳过 ——
                #   那是用户自己的新文件, 绝不能乱覆盖。
                # §7 旧: skipped.append(dst); continue
                if original_sha256 and _sha256_file(dst) == original_sha256:
                    restored.append(dst)
                else:
                    skipped.append(dst)
                continue

            os.makedirs(str(Path(dst).parent), exist_ok=True)
            try:
                _restore_entry_atomically(
                    src,
                    dst,
                    is_jxl=archive_name.lower().endswith(".jxl"),
                    original_size=original_size,
                    original_sha256=original_sha256,
                )
                restored.append(dst)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{Path(dst).name}：{exc}")

        complete = len(restored) + len(skipped) == len(targets)
        return RestoreResult(
            output_dir=output_dir,
            restored=restored,
            skipped=skipped,
            failures=failures,
            count=len(restored),
            ok=complete and not failures,
            reason="" if complete and not failures else "部分 JPG 未能恢复",
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
