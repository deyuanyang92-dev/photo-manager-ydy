"""Utilities for importing external photo files into a project."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil


JPG_EXTS = {".jpg", ".jpeg"}
TIFF_EXTS = {".tif", ".tiff"}


@dataclass
class ImportedMediaRecord:
    source_path: str
    imported_path: str
    kind: str


@dataclass
class PhotoImportResult:
    imported_paths: list[str] = field(default_factory=list)
    skipped_paths: list[str] = field(default_factory=list)
    skipped_duplicate_paths: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    imported_jpg_paths: list[str] = field(default_factory=list)
    imported_tiff_paths: list[str] = field(default_factory=list)
    imported_records: list[ImportedMediaRecord] = field(default_factory=list)


@dataclass
class PendingClearResult:
    returned_paths: list[str] = field(default_factory=list)
    stashed_paths: list[str] = field(default_factory=list)
    skipped_paths: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def changed_count(self) -> int:
        return len(self.returned_paths) + len(self.stashed_paths)


def is_jpg_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in JPG_EXTS


def is_tiff_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in TIFF_EXTS


def is_media_path(path: str | Path) -> bool:
    suffix = Path(path).suffix.lower()
    return suffix in JPG_EXTS or suffix in TIFF_EXTS


def next_available_destination_path(target_dir: str | Path, source_path: str | Path) -> Path:
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


def _same_file_content(left: Path, right: Path) -> bool:
    try:
        if left.samefile(right):
            return True
    except OSError:
        pass
    try:
        left_stat = left.stat()
        right_stat = right.stat()
        if left_stat.st_size != right_stat.st_size:
            return False
        with left.open("rb") as lf, right.open("rb") as rf:
            while True:
                left_chunk = lf.read(1024 * 1024)
                right_chunk = rf.read(1024 * 1024)
                if left_chunk != right_chunk:
                    return False
                if not left_chunk:
                    return True
    except OSError:
        return False


def _same_name_same_content_file(target_dir: Path, source_path: Path) -> Path | None:
    candidate = target_dir / source_path.name
    if not candidate.is_file():
        return None
    return candidate if _same_file_content(source_path, candidate) else None


def _import_log_path(project_dir: str | Path) -> Path:
    return Path(project_dir) / "_data" / "imported_media.json"


def _load_import_log(project_dir: str | Path) -> dict[str, dict]:
    path = _import_log_path(project_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(data, list):
        return {}
    records: dict[str, dict] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        imported = str(item.get("imported_path") or "")
        if imported:
            records[imported] = dict(item)
    return records


def _save_import_log(project_dir: str | Path, records: dict[str, dict]) -> None:
    path = _import_log_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = sorted(records.values(), key=lambda item: str(item.get("recorded_at") or ""))
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def record_imported_media(
    project_dir: str | Path,
    records: list[ImportedMediaRecord],
) -> None:
    """Persist enough import history to safely undo pending imports later."""
    if not records:
        return
    saved = _load_import_log(project_dir)
    now = datetime.now(timezone.utc).isoformat()
    for record in records:
        try:
            imported = str(Path(record.imported_path).resolve())
        except OSError:
            imported = str(record.imported_path)
        try:
            source = str(Path(record.source_path).resolve())
        except OSError:
            source = str(record.source_path)
        saved[imported] = {
            "source_path": source,
            "imported_path": imported,
            "kind": record.kind,
            "recorded_at": now,
        }
    _save_import_log(project_dir, saved)


def _same_physical_file(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return False


def _stash_pending_file(project_dir: str | Path, path: Path) -> Path:
    stash = Path(project_dir) / "_data" / "cleared-pending"
    stash.mkdir(parents=True, exist_ok=True)
    destination = next_available_destination_path(stash, path)
    shutil.move(str(path), str(destination))
    return destination.resolve()


def clear_pending_imports(
    project_dir: str | Path,
    pending_paths: list[str],
) -> PendingClearResult:
    """Clear pending imported files without deleting source originals.

    If a file has import history, move it back to its source path when possible.
    If the source still exists, move the pending copy aside instead of deleting
    it. Files without history are also moved to ``_data/cleared-pending`` so
    clearing the queue never silently destroys user data.
    """
    result = PendingClearResult()
    records = _load_import_log(project_dir)
    changed_imported_paths: set[str] = set()

    for raw in pending_paths:
        try:
            path = Path(raw).resolve()
        except OSError:
            result.skipped_paths.append(str(raw))
            continue
        if not path.is_file():
            result.skipped_paths.append(str(raw))
            continue

        imported_key = str(path)
        record = records.get(imported_key)
        try:
            if not record:
                moved = _stash_pending_file(project_dir, path)
                result.stashed_paths.append(str(moved))
                continue

            source_raw = str(record.get("source_path") or "").strip()
            if not source_raw:
                moved = _stash_pending_file(project_dir, path)
                result.stashed_paths.append(str(moved))
                changed_imported_paths.add(imported_key)
                continue
            source = Path(source_raw)

            if source.exists():
                if _same_physical_file(path, source):
                    moved = _stash_pending_file(project_dir, path)
                    result.stashed_paths.append(str(moved))
                elif _same_file_content(path, source):
                    moved = _stash_pending_file(project_dir, path)
                    result.stashed_paths.append(str(moved))
                else:
                    destination = next_available_destination_path(source.parent, path)
                    shutil.move(str(path), str(destination))
                    result.returned_paths.append(str(destination.resolve()))
                changed_imported_paths.add(imported_key)
                continue

            if source.parent.is_dir():
                shutil.move(str(path), str(source))
                result.returned_paths.append(str(source.resolve()))
                changed_imported_paths.add(imported_key)
                continue

            moved = _stash_pending_file(project_dir, path)
            result.stashed_paths.append(str(moved))
            changed_imported_paths.add(imported_key)
        except OSError as exc:
            result.errors.append(f"{path.name}: {exc}")

    if changed_imported_paths:
        for key in changed_imported_paths:
            records.pop(key, None)
        _save_import_log(project_dir, records)

    return result


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

            destination = next_available_destination_path(incoming, source_resolved)
            shutil.copy2(source_resolved, destination)
            imported = str(destination.resolve())
            result.imported_paths.append(imported)
            result.imported_jpg_paths.append(imported)
            result.imported_records.append(
                ImportedMediaRecord(str(source_resolved), imported, "jpg")
            )
        except OSError as exc:
            result.errors.append(f"{Path(raw).name}: {exc}")

    return result


def import_media_to_project(
    source_paths: list[str],
    incoming_dir: str | Path,
    results_dir: str | Path | None = None,
) -> PhotoImportResult:
    """Copy JPG/TIFF files into *incoming_dir* without deleting originals."""
    incoming = Path(incoming_dir)
    incoming.mkdir(parents=True, exist_ok=True)
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
                target_dir = incoming
                bucket = result.imported_tiff_paths

            target_resolved = target_dir.resolve()
            if source_resolved.parent == target_resolved:
                result.skipped_duplicate_paths.append(str(source_resolved))
                continue

            duplicate = _same_name_same_content_file(target_dir, source_resolved)
            if duplicate is not None:
                result.skipped_duplicate_paths.append(str(duplicate.resolve()))
                continue

            destination = next_available_destination_path(target_dir, source_resolved)
            shutil.copy2(source_resolved, destination)
            imported = str(destination.resolve())
            result.imported_paths.append(imported)
            bucket.append(imported)
            result.imported_records.append(
                ImportedMediaRecord(
                    str(source_resolved),
                    imported,
                    "jpg" if bucket is result.imported_jpg_paths else "tiff",
                )
            )
        except OSError as exc:
            result.errors.append(f"{Path(raw).name}: {exc}")

    return result
