#!/usr/bin/env python3
"""Convert legacy manifest+.jxl archives into plain JPG ZIP archives.

The conversion is conservative:
  - decode every .jxl with djxl,
  - verify decoded JPG size and SHA-256 against manifest.json,
  - write a replacement ZIP containing only JPG/JPEG entries,
  - verify the replacement ZIP entries again,
  - keep the original ZIP as <name>.legacy-jxl.zip unless --no-backup is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


JPG_EXTS = {".jpg", ".jpeg"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_legacy_jxl_zip(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            return "manifest.json" in names and any(
                name.lower().endswith(".jxl") for name in names
            )
    except (OSError, zipfile.BadZipFile):
        return False


def discover(paths: list[Path]) -> list[Path]:
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            for candidate in path.rglob("*.zip"):
                if candidate.name.endswith(".legacy-jxl.zip"):
                    continue
                if is_legacy_jxl_zip(candidate):
                    found.append(candidate)
        elif path.is_file() and path.suffix.lower() == ".zip":
            if path.name.endswith(".legacy-jxl.zip"):
                continue
            if is_legacy_jxl_zip(path):
                found.append(path)
    return sorted(dict.fromkeys(found))


def unique_name(name: str, used: set[str]) -> str:
    candidate = name
    stem = Path(name).stem
    suffix = Path(name).suffix
    index = 2
    while candidate in used:
        candidate = f"{stem}-{index}{suffix}"
        index += 1
    used.add(candidate)
    return candidate


def read_manifest(zf: zipfile.ZipFile) -> dict:
    try:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"manifest.json 读取失败: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise RuntimeError("manifest.json 格式不正确")
    return manifest


def convert_one(zip_path: Path, *, dry_run: bool, backup: bool) -> tuple[int, int]:
    with zipfile.ZipFile(zip_path, "r") as zf:
        manifest = read_manifest(zf)
        entries = list(manifest["files"])

    if not entries:
        raise RuntimeError("manifest.json 没有 files")

    decoded: list[tuple[Path, str]] = []
    used_names: set[str] = set()
    total_size = 0

    with tempfile.TemporaryDirectory(prefix="jxl-to-jpg-") as td:
        temp_dir = Path(td)
        extract_dir = temp_dir / "in"
        out_dir = temp_dir / "out"
        extract_dir.mkdir()
        out_dir.mkdir()

        with zipfile.ZipFile(zip_path, "r") as zf:
            for entry in entries:
                archive_name = str(entry.get("archiveName") or "")
                original_name = str(entry.get("originalName") or archive_name)
                expected_size = entry.get("originalSize")
                expected_sha = str(entry.get("originalSha256") or "")
                if not archive_name:
                    raise RuntimeError("manifest entry 缺少 archiveName")
                if not original_name.lower().endswith((".jpg", ".jpeg")):
                    original_name = Path(original_name).with_suffix(".JPG").name

                extracted = extract_dir / Path(archive_name).name
                with zf.open(archive_name, "r") as src, extracted.open("wb") as dst:
                    shutil.copyfileobj(src, dst)

                output_name = unique_name(Path(original_name).name, used_names)
                decoded_path = out_dir / output_name

                if archive_name.lower().endswith(".jxl"):
                    subprocess.run(
                        ["djxl", str(extracted), str(decoded_path)],
                        check=True,
                        capture_output=True,
                        timeout=180,
                    )
                else:
                    shutil.copy2(extracted, decoded_path)

                actual_size = decoded_path.stat().st_size
                if expected_size is not None and int(expected_size) != actual_size:
                    raise RuntimeError(
                        f"{output_name} 大小不一致: {actual_size} != {expected_size}"
                    )
                if expected_sha:
                    actual_sha = sha256_file(decoded_path)
                    if actual_sha != expected_sha:
                        raise RuntimeError(f"{output_name} SHA-256 不一致")
                decoded.append((decoded_path, output_name))
                total_size += actual_size

        if dry_run:
            return len(decoded), total_size

        tmp_zip = zip_path.with_name(zip_path.name + ".tmp-jpgzip")
        backup_zip = zip_path.with_suffix(".legacy-jxl.zip")
        try:
            with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
                for decoded_path, output_name in decoded:
                    zf.write(decoded_path, output_name)

            with zipfile.ZipFile(tmp_zip, "r") as zf:
                bad = zf.testzip()
                if bad:
                    raise RuntimeError(f"新 ZIP 内部文件损坏: {bad}")
                names = zf.namelist()
                if len(names) != len(decoded) or any(Path(n).suffix.lower() not in JPG_EXTS for n in names):
                    raise RuntimeError("新 ZIP 内容不是纯 JPG")
                for decoded_path, output_name in decoded:
                    info = zf.getinfo(output_name)
                    if info.file_size != decoded_path.stat().st_size:
                        raise RuntimeError(f"新 ZIP 内 {output_name} 大小不一致")

            if backup:
                if backup_zip.exists():
                    raise RuntimeError(f"备份文件已存在，拒绝覆盖: {backup_zip}")
                os.replace(zip_path, backup_zip)
                os.replace(tmp_zip, zip_path)
            else:
                os.replace(tmp_zip, zip_path)
        finally:
            if tmp_zip.exists():
                tmp_zip.unlink()

    return len(decoded), total_size


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", help="ZIP 文件或目录")
    parser.add_argument("--dry-run", action="store_true", help="只检查，不写文件")
    parser.add_argument("--no-backup", action="store_true", help="不保留 .legacy-jxl.zip 备份")
    args = parser.parse_args(argv)

    targets = discover([Path(p) for p in args.paths])
    if not targets:
        print("未找到 legacy JXL ZIP。")
        return 0

    print(f"找到 {len(targets)} 个 legacy JXL ZIP。")
    failed = 0
    for target in targets:
        try:
            count, size = convert_one(
                target,
                dry_run=args.dry_run,
                backup=not args.no_backup,
            )
            mode = "检查通过" if args.dry_run else "转换完成"
            print(f"{mode}: {target} ({count} JPG, {size} bytes)")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"失败: {target}: {exc}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
