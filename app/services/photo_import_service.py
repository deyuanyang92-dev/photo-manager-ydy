"""Utilities for importing external photo files into a project."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shutil


JPG_EXTS = {".jpg", ".jpeg"}


@dataclass
class PhotoImportResult:
    imported_paths: list[str] = field(default_factory=list)
    skipped_paths: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def is_jpg_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in JPG_EXTS


def unique_destination_path(incoming_dir: str | Path, source_path: str | Path) -> Path:
    """Return a non-overwriting destination path for *source_path*."""
    incoming = Path(incoming_dir)
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
                result.imported_paths.append(str(source_resolved))
                continue

            destination = unique_destination_path(incoming, source_resolved)
            shutil.copy2(source_resolved, destination)
            result.imported_paths.append(str(destination.resolve()))
        except OSError as exc:
            result.errors.append(f"{Path(raw).name}: {exc}")

    return result
