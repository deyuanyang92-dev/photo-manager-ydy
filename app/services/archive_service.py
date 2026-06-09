"""archive_service.py — JPG→JXL→ZIP archival with pre-delete safety checks.

Oracle: archive.js:28-61, 150-168; compress.js:32-45.

Safety invariants (hard rules — must never be violated):
  1. TIFF is NEVER deleted.
  2. delete_jpg=False (default) → no JPGs are deleted.
  3. If delete_jpg=True, JPGs are deleted ONLY after ALL four checks pass:
       a. cjxl available (else compression skips to sharp fallback).
       b. ZIP file exists and size > 32 bytes.
       c. verify_manifest_complete passes (count + names + sizes).
       d. verify_jxl_recoverable passes (djxl re-decodes each JXL, size > 0).
     If djxl is unavailable → check (d) fails → JPGs are NOT deleted.
  4. cjxl flags: --distance 0 -e <effort>
     NO --modular / --quality / -j flags.  (Oracle: compress.js:34-39)

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── Effort mapping (mirrors compress.js EFFORT_MAP) ──────────────────────────

EFFORT_MAP = {
    "standard": {"jxl": 7},
    "maximum":  {"jxl": 9},
}


# ── cjxl / djxl availability cache ───────────────────────────────────────────

_cjxl_available: Optional[bool] = None
_djxl_available: Optional[bool] = None


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


# ── JXL compression ───────────────────────────────────────────────────────────

def compress_to_jxl(
    input_path: str,
    output_path: str,
    effort: int = 9,
) -> None:
    """Compress *input_path* to JXL at *output_path* using cjxl.

    Flags: --distance 0 -e <effort>  (Oracle: compress.js:35-39)
    --distance 0 → lossless transcode for JPEG input (bit-exact recoverable).
    NO --modular, --quality, or -j flags.

    Raises RuntimeError if cjxl is not available (caller decides fallback).
    Raises subprocess.CalledProcessError on cjxl failure.
    """
    if not has_cjxl():
        raise RuntimeError("cjxl not available")
    subprocess.run(
        ["cjxl", input_path, output_path, "--distance", "0", "-e", str(effort)],
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
        except Exception as e:
            msg = str(e)
            return CheckResult(ok=False, reason="JXL 恢复校验失败：" + f["archiveName"] + " · " + msg)
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
    delete_jpg: bool = False,
    method: str = "maximum",
) -> ZipResult:
    """Compress JPGs to JXL, write manifest, zip, verify, maybe delete JPGs.

    Safety invariants enforced here:
      - TIFF is NEVER deleted (not even accepted as input for deletion).
      - delete_jpg defaults to False.
      - Deletion only happens after BOTH manifest + jxl_recoverable checks pass.
      - If djxl is unavailable, jxl_recoverable check fails → no deletion.

    Oracle: archive.js:67-190.

    Args:
        jpg_paths:   Absolute paths to source JPGs.
        tiff_path:   Path to the composed TIFF (used for naming; never deleted).
        project_dir: Project root (ZIP placed in results/ subdir or same dir as TIFF).
        delete_jpg:  If True, delete JPGs after all safety checks pass.
        method:      Compression effort level: "standard" | "maximum".

    Returns:
        ZipResult dataclass.
    """
    if not jpg_paths:
        raise ValueError("未指定 JPG 原片")
    for p in jpg_paths:
        if not os.path.isfile(p):
            raise FileNotFoundError(f"文件不存在: {p}")

    tiff_basename = Path(tiff_path).stem
    zip_dir = str(Path(tiff_path).parent)
    zip_path = os.path.join(zip_dir, tiff_basename + ".zip")

    effort_map = EFFORT_MAP.get(method, EFFORT_MAP["maximum"])
    effort = effort_map["jxl"]

    temp_dir = tempfile.mkdtemp(prefix="specimen-archive-")
    manifest_files: list[dict] = []
    total_original = 0
    total_compressed = 0

    try:
        # ── Phase 1: compress each JPG to JXL ────────────────────────────────
        for jpg_path in jpg_paths:
            original_name = os.path.basename(jpg_path)
            jxl_name = Path(original_name).stem + ".jxl"
            jxl_path = os.path.join(temp_dir, jxl_name)

            original_size = os.path.getsize(jpg_path)

            try:
                compress_to_jxl(jpg_path, jxl_path, effort=effort)
            except RuntimeError:
                # cjxl not available — skip JXL, store original JPG in archive
                shutil.copy2(jpg_path, os.path.join(temp_dir, original_name))
                jxl_path = os.path.join(temp_dir, original_name)
                jxl_name = original_name

            if not os.path.isfile(jxl_path):
                raise RuntimeError(f"压缩后文件不存在: {jxl_path}")

            compressed_size = os.path.getsize(jxl_path)
            total_original += original_size
            total_compressed += compressed_size

            manifest_files.append({
                "originalName": original_name,
                "archiveName": jxl_name,
                "jxlPath": jxl_path,
                "originalSize": original_size,
                "compressedSize": compressed_size,
            })

        # ── Phase 2: write manifest.json ──────────────────────────────────────
        manifest = {
            "version": 1,
            "createdAt": _iso_now(),
            "tiffBasename": tiff_basename,
            "format": "jxl-lossless",
            "method": method,
            "files": [
                {
                    "originalName": f["originalName"],
                    "archiveName": f["archiveName"],
                    "originalSize": f["originalSize"],
                    "compressedSize": f["compressedSize"],
                }
                for f in manifest_files
            ],
            "totalOriginal": total_original,
            "totalCompressed": total_compressed,
        }
        manifest_path = os.path.join(temp_dir, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, ensure_ascii=False)

        # ── Phase 3: write ZIP ────────────────────────────────────────────────
        os.makedirs(zip_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            zf.write(manifest_path, "manifest.json")
            for f in manifest_files:
                zf.write(f["jxlPath"], f["archiveName"])

        zip_size = os.path.getsize(zip_path)
        if zip_size < 32:
            raise RuntimeError("ZIP 生成失败或体积异常")

        # ── Phase 4: testzip (verify ZIP integrity) ───────────────────────────
        with zipfile.ZipFile(zip_path, "r") as zf:
            bad = zf.testzip()
            if bad:
                raise RuntimeError(f"ZIP 内部文件损坏: {bad}")

        saved_percent = (
            round((1 - total_compressed / total_original) * 100)
            if total_original > 0
            else 0
        )

        # ── Phase 5: safety checks before deleting JPGs ───────────────────────
        # Oracle: archive.js:150-168
        actual_delete = False
        deletion_skipped_reason = ""

        if delete_jpg:
            manifest_check = verify_manifest_complete(manifest, manifest_files)
            if manifest_check.ok:
                recover_check = verify_jxl_recoverable(manifest_files, temp_dir)
            else:
                recover_check = manifest_check

            if recover_check.ok:
                actual_delete = True
            else:
                deletion_skipped_reason = recover_check.reason or "删除前校验失败，已保留 JPG"

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

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


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

    Reverses archive_group: extract each JXL and re-decode it with djxl back to
    the original JPG (bit-exact — JXL stores JPEGs losslessly). Entries stored as
    raw JPG (the cjxl-unavailable fallback) are copied out directly.

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

        # ── Build entries: (archiveName, originalName, originalSize|None) ──────
        manifest_path = os.path.join(temp_dir, "manifest.json")
        entries: list[tuple[str, str, Optional[int]]] = []
        if os.path.isfile(manifest_path):
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
            for f in manifest.get("files", []):
                entries.append((
                    f["archiveName"],
                    f.get("originalName") or f["archiveName"],
                    f.get("originalSize"),
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
                entries.append((name, original, None))

        # ── djxl gate: any .jxl but no djxl → abort, write nothing ────────────
        needs_djxl = any(a.lower().endswith(".jxl") for a, _, _ in entries)
        if needs_djxl and not has_djxl():
            return RestoreResult(
                output_dir=output_dir,
                ok=False,
                reason="缺少 djxl 解码工具，无法还原 JXL，未输出任何文件",
                failures=["缺少 djxl 解码工具"],
            )

        for archive_name, original_name, original_size in entries:
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
                else:
                    shutil.copy2(src, dst)
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
