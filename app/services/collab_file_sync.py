"""LAN file sync helpers for collaboration projects.

The collaboration task store is the fast UID index; this module handles the
slower media layer.  It syncs by project-relative paths and hashes so callers
can safely skip identical files and refuse silent overwrites.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Optional

from app.services.collab_types import get_httpx
from app.services.project_paths import require_project_root


MEDIA_EXTS = {".jpg", ".jpeg", ".tif", ".tiff", ".zip"}
DEFAULT_MAX_WORKERS = 4


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _kind_for_path(path: str | Path) -> str:
    ext = Path(path).suffix.lower()
    if ext in {".jpg", ".jpeg"}:
        return "jpg"
    if ext in {".tif", ".tiff"}:
        return "tiff"
    if ext == ".zip":
        return "zip"
    return "file"


def project_relative_path(project_dir: str | Path, file_path: str | Path) -> str:
    root = require_project_root(str(project_dir)).resolve()
    resolved = Path(file_path).resolve()
    try:
        rel = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"file is outside project root: {file_path}") from exc
    return rel.as_posix()


def resolve_project_relative(project_dir: str | Path, relative_path: str) -> Path:
    """Resolve a project-relative POSIX path and reject traversal/absolute input."""
    root = require_project_root(str(project_dir)).resolve()
    rel = PurePosixPath(str(relative_path or "").replace("\\", "/"))
    if not str(rel) or rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        raise ValueError(f"invalid project-relative path: {relative_path!r}")
    target = (root / Path(*rel.parts)).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes project root: {relative_path!r}") from exc
    return target


@dataclass(frozen=True)
class FileManifestEntry:
    uid: str
    relative_path: str
    kind: str
    size_bytes: int
    mtime: float
    sha256: str
    device_id: str = ""
    source: str = "scan"

    def to_dict(self) -> dict:
        return {
            "uid": self.uid,
            "relativePath": self.relative_path,
            "kind": self.kind,
            "sizeBytes": self.size_bytes,
            "mtime": self.mtime,
            "sha256": self.sha256,
            "deviceId": self.device_id,
            "source": self.source,
        }

    @staticmethod
    def from_dict(data: dict) -> "FileManifestEntry":
        return FileManifestEntry(
            uid=str(data.get("uid") or ""),
            relative_path=str(data.get("relativePath") or data.get("relative_path") or ""),
            kind=str(data.get("kind") or "file"),
            size_bytes=int(data.get("sizeBytes") or data.get("size_bytes") or 0),
            mtime=float(data.get("mtime") or 0),
            sha256=str(data.get("sha256") or ""),
            device_id=str(data.get("deviceId") or ""),
            source=str(data.get("source") or "remote"),
        )


@dataclass
class SyncSummary:
    planned: int = 0
    downloaded: int = 0
    skipped: int = 0
    conflicts: int = 0
    failed: int = 0
    incompatible_peers: int = 0
    bytes_downloaded: int = 0
    peers: int = 0
    conflict_paths: list[str] = field(default_factory=list)
    failed_paths: list[str] = field(default_factory=list)

    def merge(self, other: "SyncSummary") -> None:
        self.planned += other.planned
        self.downloaded += other.downloaded
        self.skipped += other.skipped
        self.conflicts += other.conflicts
        self.failed += other.failed
        self.incompatible_peers += other.incompatible_peers
        self.bytes_downloaded += other.bytes_downloaded
        self.peers += other.peers
        self.conflict_paths.extend(other.conflict_paths)
        self.failed_paths.extend(other.failed_paths)


def _file_entry(
    project_dir: str | Path,
    file_path: str | Path,
    *,
    uid: str = "",
    device_id: str = "",
    source: str = "scan",
    known_sha: str | None = None,
) -> Optional[FileManifestEntry]:
    path = Path(file_path)
    if not path.is_file() or path.suffix.lower() not in MEDIA_EXTS:
        return None
    try:
        rel = project_relative_path(project_dir, path)
        st = path.stat()
        sha = known_sha if known_sha else sha256_file(path)
    except (OSError, ValueError):
        return None
    return FileManifestEntry(
        uid=uid,
        relative_path=rel,
        kind=_kind_for_path(path),
        size_bytes=int(st.st_size),
        mtime=float(st.st_mtime),
        sha256=sha or "",
        device_id=device_id,
        source=source,
    )


def _add_entry(
    entries: dict[str, FileManifestEntry],
    entry: Optional[FileManifestEntry],
    *,
    wanted_uids: set[str] | None,
) -> None:
    if entry is None:
        return
    if wanted_uids is not None and entry.uid not in wanted_uids:
        return
    existing = entries.get(entry.relative_path)
    if existing is None:
        entries[entry.relative_path] = entry
        return
    if not existing.uid and entry.uid:
        entries[entry.relative_path] = entry


def _load_known_uids(db) -> list[str]:
    if db is None:
        return []
    try:
        rows = db.execute("SELECT uid FROM specimens ORDER BY length(uid) DESC").fetchall()
        return [str(row["uid"] if hasattr(row, "keys") else row[0]) for row in rows]
    except Exception:
        return []


def _infer_uid_from_name(name: str, known_uids: Iterable[str]) -> str:
    stem = Path(name).stem
    for uid in known_uids:
        if stem == uid or stem.startswith(uid + "-") or stem.startswith(uid + "_"):
            return uid
    return ""


def _iter_grouping_paths(db) -> Iterable[tuple[str, str, str]]:
    if db is None:
        return []
    rows = []
    try:
        rows = db.execute(
            "SELECT uid, jpg_paths, composed_tiff_path, archive_zip FROM grouping"
        ).fetchall()
    except Exception:
        return []
    for row in rows:
        uid_raw = row["uid"] if hasattr(row, "keys") else row[0]
        uid = str(uid_raw or "")
        jpg_raw = row["jpg_paths"] if hasattr(row, "keys") else row[1]
        tiff_path = row["composed_tiff_path"] if hasattr(row, "keys") else row[2]
        zip_path = row["archive_zip"] if hasattr(row, "keys") else row[3]
        try:
            jpgs = json.loads(jpg_raw or "[]")
        except Exception:
            jpgs = []
        for path in jpgs:
            if path:
                yield uid, str(path), "grouping-jpg"
        if tiff_path:
            yield uid, str(tiff_path), "grouping-tiff"
        if zip_path:
            yield uid, str(zip_path), "grouping-zip"


def _iter_photo_file_rows(db, project_dir: str | Path) -> Iterable[tuple[str, Path, str | None]]:
    if db is None:
        return []
    rows = []
    try:
        rows = db.execute(
            """
            SELECT f.relative_path, f.sha256, a.specimen_uid
              FROM photo_files f
              JOIN photos p ON p.photo_id = f.photo_id
              LEFT JOIN photo_assignments a ON a.assignment_id = p.current_assignment_id
             WHERE COALESCE(f.exists_on_disk, 1) = 1
            """
        ).fetchall()
    except Exception:
        return []
    root = require_project_root(str(project_dir)).resolve()
    for row in rows:
        rel_raw = row["relative_path"] if hasattr(row, "keys") else row[0]
        rel = str(rel_raw or "")
        sha = row["sha256"] if hasattr(row, "keys") else row[1]
        uid_raw = row["specimen_uid"] if hasattr(row, "keys") else row[2]
        uid = str(uid_raw or "")
        try:
            path = resolve_project_relative(root, rel)
        except ValueError:
            continue
        yield uid, path, str(sha or "") or None


def build_project_manifest(
    project_dir: str,
    *,
    db=None,
    uids: Optional[Iterable[str]] = None,
    device_id: str = "",
) -> list[FileManifestEntry]:
    """Return media files known to this project, optionally scoped to UIDs."""
    root = require_project_root(project_dir).resolve()
    wanted_uids = {str(u) for u in uids if str(u).strip()} if uids is not None else None
    entries: dict[str, FileManifestEntry] = {}

    for uid, path, known_sha in _iter_photo_file_rows(db, root):
        _add_entry(
            entries,
            _file_entry(root, path, uid=uid, device_id=device_id, source="photo_files", known_sha=known_sha),
            wanted_uids=wanted_uids,
        )

    for uid, raw_path, source in _iter_grouping_paths(db):
        path = Path(raw_path)
        if not path.is_absolute():
            path = root / raw_path
        _add_entry(
            entries,
            _file_entry(root, path, uid=uid, device_id=device_id, source=source),
            wanted_uids=wanted_uids,
        )

    known_uids = _load_known_uids(db)
    for subdir in ("incoming-jpg", "新拍JPG", "results"):
        base = root / subdir
        if not base.is_dir():
            continue
        for dirpath, _dirs, filenames in os.walk(base):
            for name in filenames:
                if Path(name).suffix.lower() not in MEDIA_EXTS:
                    continue
                uid = _infer_uid_from_name(name, known_uids)
                _add_entry(
                    entries,
                    _file_entry(root, Path(dirpath) / name, uid=uid, device_id=device_id, source="scan"),
                    wanted_uids=wanted_uids,
                )

    return sorted(entries.values(), key=lambda e: (e.uid, e.kind, e.relative_path))


def manifest_payload(
    project_dir: str,
    *,
    db=None,
    uids: Optional[Iterable[str]] = None,
    device_id: str = "",
    project_id: str = "",
) -> dict:
    files = build_project_manifest(project_dir, db=db, uids=uids, device_id=device_id)
    return {
        "projectDirName": Path(project_dir).name,
        "projectId": project_id,
        "deviceId": device_id,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "files": [entry.to_dict() for entry in files],
    }


def _same_file(local_path: Path, remote: FileManifestEntry) -> bool:
    try:
        if not local_path.is_file():
            return False
        if local_path.stat().st_size != remote.size_bytes:
            return False
        if remote.sha256:
            return sha256_file(local_path) == remote.sha256
        return False
    except OSError:
        return False


def _backup_existing(project_dir: str, local_path: Path, relative_path: str) -> None:
    root = require_project_root(project_dir).resolve()
    backup_root = root / "_data" / "sync-conflicts" / _now_stamp()
    dest = backup_root / Path(*PurePosixPath(relative_path).parts)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest = dest.with_name(f"{dest.stem}-{uuid.uuid4().hex[:8]}{dest.suffix}")
    shutil.move(str(local_path), str(dest))


def _download_one(
    *,
    project_dir: str,
    peer_base_url: str,
    group_code: str,
    project_id: str,
    remote: FileManifestEntry,
    mode: str,
) -> tuple[str, int, str]:
    """Download/skip one file.  Returns (status, bytes, relative_path)."""
    local_path = resolve_project_relative(project_dir, remote.relative_path)
    if local_path.exists():
        if _same_file(local_path, remote):
            return "skipped", 0, remote.relative_path
        if mode == "missing":
            return "skipped", 0, remote.relative_path
        if mode != "overwrite":
            return "conflict", 0, remote.relative_path
        _backup_existing(project_dir, local_path, remote.relative_path)

    local_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = local_path.with_name(f".{local_path.name}.{uuid.uuid4().hex}.part")
    try:
        httpx = get_httpx()

        timeout = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)
        with httpx.stream(
            "GET",
            f"{peer_base_url}/api/collab/files/download",
            params={
                "path": remote.relative_path,
                "groupCode": group_code,
                "projectId": project_id,
            },
            timeout=timeout,
            trust_env=False,
        ) as resp:
            resp.raise_for_status()
            with open(part_path, "wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                    if chunk:
                        fh.write(chunk)

        if remote.sha256 and sha256_file(part_path) != remote.sha256:
            try:
                part_path.unlink()
            except OSError:
                pass
            return "failed", 0, remote.relative_path
        os.replace(part_path, local_path)
        try:
            os.utime(local_path, (time.time(), remote.mtime or time.time()))
        except OSError:
            pass
        return "downloaded", int(local_path.stat().st_size), remote.relative_path
    except Exception:
        try:
            part_path.unlink()
        except OSError:
            pass
        return "failed", 0, remote.relative_path


def fetch_peer_manifest(
    peer_base_url: str,
    group_code: str,
    project_id: str,
    uids: Optional[Iterable[str]] = None,
) -> list[FileManifestEntry]:
    try:
        httpx = get_httpx()
        params = {"groupCode": group_code, "projectId": project_id}
        uid_list = [str(u) for u in (uids or []) if str(u).strip()]
        if uid_list:
            params["uids"] = ",".join(uid_list)
        resp = httpx.get(
            f"{peer_base_url}/api/collab/files/manifest",
            params=params,
            timeout=30.0,
            trust_env=False,
        )
        resp.raise_for_status()
        payload = resp.json()
        files = payload.get("files") if isinstance(payload, dict) else payload
        if not isinstance(files, list):
            return []
        return [
            entry for entry in (FileManifestEntry.from_dict(item) for item in files)
            if entry.relative_path and entry.kind in {"jpg", "tiff", "zip", "file"}
        ]
    except Exception:
        return []


def sync_from_peer(
    *,
    project_dir: str,
    peer_base_url: str,
    group_code: str,
    project_id: str,
    uids: Optional[Iterable[str]] = None,
    mode: str = "smart",
    max_workers: int = DEFAULT_MAX_WORKERS,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> SyncSummary:
    """Pull missing/different media from one peer.

    mode:
        ``smart``      download missing, skip identical, mark different as conflict.
        ``missing``    only download absent files.
        ``overwrite``  download missing/different, backing up replaced files.
    """
    if mode not in {"smart", "missing", "overwrite"}:
        mode = "smart"
    if not project_id:
        return SyncSummary(failed=1)
    remote_files = fetch_peer_manifest(peer_base_url, group_code, project_id, uids=uids)
    summary = SyncSummary(planned=len(remote_files), peers=1 if remote_files else 0)
    if not remote_files:
        return summary

    done = 0
    workers = max(1, min(int(max_workers or DEFAULT_MAX_WORKERS), 8))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                _download_one,
                project_dir=project_dir,
                peer_base_url=peer_base_url,
                group_code=group_code,
                project_id=project_id,
                remote=entry,
                mode=mode,
            )
            for entry in remote_files
        ]
        for future in as_completed(futures):
            status, byte_count, rel = future.result()
            done += 1
            if status == "downloaded":
                summary.downloaded += 1
                summary.bytes_downloaded += byte_count
            elif status == "skipped":
                summary.skipped += 1
            elif status == "conflict":
                summary.conflicts += 1
                summary.conflict_paths.append(rel)
            else:
                summary.failed += 1
                summary.failed_paths.append(rel)
            if progress_cb is not None:
                progress_cb(done, len(remote_files), rel)
    return summary


def sync_from_peers(
    *,
    project_dir: str,
    peers: Iterable,
    group_code: str,
    project_id: str,
    uids: Optional[Iterable[str]] = None,
    mode: str = "smart",
    max_workers: int = DEFAULT_MAX_WORKERS,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> SyncSummary:
    summary = SyncSummary()
    for peer in peers:
        peer_group = getattr(peer, "group_code", "")
        if not group_code or peer_group != group_code:
            continue
        peer_project = str(getattr(peer, "project_id", "") or "")
        if not project_id or peer_project != project_id:
            summary.incompatible_peers += 1
            continue
        peer_summary = sync_from_peer(
            project_dir=project_dir,
            peer_base_url=getattr(peer, "base_url"),
            group_code=group_code,
            project_id=project_id,
            uids=uids,
            mode=mode,
            max_workers=max_workers,
            progress_cb=progress_cb,
        )
        summary.merge(peer_summary)
    return summary
