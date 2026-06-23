#!/usr/bin/env python3
"""Extract JPGs from archive ZIPs, verify them, then remove ZIP/JXL metadata.

Use this for returning a photo folder to a plain-file state:
  - extract every JPG/JPEG from normal ZIP archives in the folder,
  - refuse to overwrite a different existing JPG,
  - verify extracted files match ZIP entry size and SHA-256,
  - delete the archive ZIPs and same-stem .legacy-jxl.zip backups,
  - delete matching loose .jxl files and manifest.json.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import zipfile
from pathlib import Path


JPG_EXTS = {".jpg", ".jpeg"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def archive_zips(folder: Path) -> list[Path]:
    return sorted(
        p for p in folder.glob("*.zip")
        if p.is_file() and not p.name.endswith(".legacy-jxl.zip")
    )


def extract_and_verify(zip_path: Path, *, dry_run: bool) -> list[Path]:
    extracted: list[Path] = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        bad = zf.testzip()
        if bad:
            raise RuntimeError(f"{zip_path.name}: ZIP 内部损坏: {bad}")
        names = [
            n for n in zf.namelist()
            if not n.endswith("/") and Path(n).suffix.lower() in JPG_EXTS
        ]
        if not names:
            return []

        for name in names:
            data = zf.read(name)
            target = zip_path.parent / Path(name).name
            info = zf.getinfo(name)
            if len(data) != info.file_size:
                raise RuntimeError(f"{zip_path.name}: {name} 读取大小不一致")
            data_sha = sha256_bytes(data)

            if target.exists():
                if target.stat().st_size != info.file_size or sha256_file(target) != data_sha:
                    raise RuntimeError(f"{target.name} 已存在但内容不同，拒绝覆盖")
            elif not dry_run:
                target.write_bytes(data)

            if not dry_run:
                if target.stat().st_size != info.file_size or sha256_file(target) != data_sha:
                    raise RuntimeError(f"{target.name} 解压后校验失败")
            extracted.append(target)
    return extracted


def cleanup(folder: Path, zips: list[Path], jpgs: list[Path], *, dry_run: bool) -> list[Path]:
    to_delete: list[Path] = []
    for zip_path in zips:
        to_delete.append(zip_path)
        legacy = zip_path.with_suffix(".legacy-jxl.zip")
        if legacy.exists():
            to_delete.append(legacy)
    for jpg in jpgs:
        jxl = jpg.with_suffix(".jxl")
        if jxl.exists():
            to_delete.append(jxl)
    manifest = folder / "manifest.json"
    if manifest.exists():
        to_delete.append(manifest)

    unique = sorted(dict.fromkeys(to_delete))
    if not dry_run:
        for path in unique:
            if path.exists():
                path.unlink()
    return unique


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", help="照片目录")
    parser.add_argument("--dry-run", action="store_true", help="只检查，不写入/删除")
    args = parser.parse_args(argv)

    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"不是目录: {folder}", file=sys.stderr)
        return 2

    zips = archive_zips(folder)
    if not zips:
        print("没有需要处理的普通 ZIP。")
        return 0

    all_jpgs: list[Path] = []
    for zip_path in zips:
        jpgs = extract_and_verify(zip_path, dry_run=args.dry_run)
        print(f"{'检查' if args.dry_run else '解压'}: {zip_path.name} -> {len(jpgs)} JPG")
        all_jpgs.extend(jpgs)

    deleted = cleanup(folder, zips, all_jpgs, dry_run=args.dry_run)
    action = "将删除" if args.dry_run else "已删除"
    for path in deleted:
        print(f"{action}: {path.name}")

    print(
        f"{'检查通过' if args.dry_run else '完成'}: "
        f"{len(all_jpgs)} JPG, {len(deleted)} 个归档/中间文件"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
