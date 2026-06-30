"""Utilities for importing external photo files into a project."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shutil


JPG_EXTS = {".jpg", ".jpeg"}
TIFF_EXTS = {".tif", ".tiff"}


@dataclass
class PhotoImportResult:
    imported_paths: list[str] = field(default_factory=list)
    skipped_paths: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    imported_jpg_paths: list[str] = field(default_factory=list)
    imported_tiff_paths: list[str] = field(default_factory=list)


def is_jpg_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in JPG_EXTS


def is_tiff_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in TIFF_EXTS


def is_media_path(path: str | Path) -> bool:
    suffix = Path(path).suffix.lower()
    return suffix in JPG_EXTS or suffix in TIFF_EXTS


def unique_destination_path(target_dir: str | Path, source_path: str | Path) -> Path:
    """Return a non-overwriting destination path for *source_path*."""
    incoming = Path(target_dir)
    source = Path(source_path)
    candidate = incoming / source.name
    if not candidate.exists():
        return candidate

    stem = source.stem
    suffix = source.suffix
    index = 2
    while True:
        candidate = incoming / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def import_jpgs_to_incoming(
    source_paths: list[str],
    incoming_dir: str | Path,
) -> PhotoImportResult:
    """Copy JPG/JPEG files into *incoming_dir* without deleting originals.

    Existing names are never overwritten. Files already inside the incoming
    directory are returned as imported paths so callers can continue with one
    normalized list.
    """
    incoming = Path(incoming_dir)
    incoming.mkdir(parents=True, exist_ok=True)
    result = PhotoImportResult()

    seen: set[Path] = set()
    for raw in source_paths:
        try:
            source = Path(raw).expanduser()
            if not source.is_file() or not is_jpg_path(source):
                result.skipped_paths.append(str(raw))
                continue
            source_resolved = source.resolve()
            if source_resolved in seen:
                continue
            seen.add(source_resolved)

            incoming_resolved = incoming.resolve()
            if source_resolved.parent == incoming_resolved:
                imported = str(source_resolved)
                result.imported_paths.append(imported)
                result.imported_jpg_paths.append(imported)
                continue

            destination = unique_destination_path(incoming, source_resolved)
            shutil.copy2(source_resolved, destination)
            imported = str(destination.resolve())
            result.imported_paths.append(imported)
            result.imported_jpg_paths.append(imported)
        except OSError as exc:
            result.errors.append(f"{Path(raw).name}: {exc}")

    return result


def import_media_to_project(
    source_paths: list[str],
    incoming_dir: str | Path,
    results_dir: str | Path,
) -> PhotoImportResult:
    """Copy JPGs to incoming and TIFFs to results without deleting originals."""
    incoming = Path(incoming_dir)
    results = Path(results_dir)
    incoming.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)
    result = PhotoImportResult()

    seen: set[Path] = set()
    for raw in source_paths:
        try:
            source = Path(raw).expanduser()
            if not source.is_file() or not is_media_path(source):
                result.skipped_paths.append(str(raw))
                continue
            source_resolved = source.resolve()
            if source_resolved in seen:
                continue
            seen.add(source_resolved)

            if is_jpg_path(source_resolved):
                target_dir = incoming
                bucket = result.imported_jpg_paths
            else:
                target_dir = results
                bucket = result.imported_tiff_paths

            target_resolved = target_dir.resolve()
            if source_resolved.parent == target_resolved:
                imported = str(source_resolved)
            else:
                destination = unique_destination_path(target_dir, source_resolved)
                shutil.copy2(source_resolved, destination)
                imported = str(destination.resolve())
            result.imported_paths.append(imported)
            bucket.append(imported)
        except OSError as exc:
            result.errors.append(f"{Path(raw).name}: {exc}")

    return result
