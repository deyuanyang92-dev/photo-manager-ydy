"""Photo asset catalog for workspace databases."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relative_path_from_project(project_dir: str, file_path: str) -> str:
    return os.path.relpath(str(Path(file_path).resolve()), str(Path(project_dir).resolve())).replace(os.sep, "/")


def _relative_path_from_root(root: Path, file_path: str) -> str:
    return os.path.relpath(file_path, str(root)).replace(os.sep, "/")


def _sqlite_row_value(row, key: str, index: int):
    try:
        return row[key]
    except Exception:
        return row[index]


def _batched_values(values: list, size: int = 900) -> Iterable[list]:
    for i in range(0, len(values), size):
        yield values[i:i + size]


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_workspace_id(db) -> Optional[str]:
    try:
        row = db.execute("SELECT workspace_id FROM workspace_meta LIMIT 1").fetchone()
        return row["workspace_id"] if row else None
    except Exception:
        return None


def _read_image_metadata(path: str) -> dict:
    from app.utils.tiff_exif_read import read_tiff_exif_metadata

    meta = read_tiff_exif_metadata(path)
    meta.setdefault("raw_exif_json", "{}")
    return meta


def read_image_exif_metadata(path: str) -> dict:
    """Read width/height and common camera EXIF fields from an image file."""
    return _read_image_metadata(path)


def upsert_photo_file(
    db,
    project_dir: str,
    file_path: str,
    *,
    storage_role: str = "incoming",
    photo_kind: str = "original",
    specimen_uid: Optional[str] = None,
    assignment_source: str = "scan",
    first_seen_at: Optional[str] = None,
    compute_hash: bool = True,
    read_metadata: bool = True,
) -> dict:
    """Register a photo file and immutable first-seen metadata.

    The current implementation keeps one logical ``photos`` row per file path.
    Duplicate hashes are reported later by QC instead of being silently merged.
    """
    resolved = str(Path(file_path).resolve())
    st = os.stat(resolved)
    rel = _relative_path_from_project(project_dir, resolved)
    ts = _utc_now_iso()
    first_seen = first_seen_at or ts
    existing_file = db.execute(
        "SELECT * FROM photo_files WHERE relative_path=?",
        (rel,),
    ).fetchone()
    sha = None
    if compute_hash and (existing_file is None or existing_file["size_bytes"] != st.st_size):
        sha = _sha256_file(resolved)
    elif existing_file is not None:
        sha = existing_file["sha256"]

    meta = _read_image_metadata(resolved) if read_metadata else {"raw_exif_json": "{}"}
    width = meta.get("image_width")
    height = meta.get("image_height")
    mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()

    if existing_file is None:
        photo_id = str(uuid.uuid4())
        file_id = str(uuid.uuid4())
        metadata_id = str(uuid.uuid4())
        with db:
            db.execute(
                """
                INSERT INTO photos (
                  photo_id, workspace_id, content_hash, original_filename,
                  first_seen_at, capture_datetime, photo_kind, lifecycle_status,
                  primary_file_id, metadata_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    photo_id,
                    _read_workspace_id(db),
                    sha,
                    Path(resolved).name,
                    first_seen,
                    meta.get("exif_datetime"),
                    photo_kind,
                    "incoming" if not specimen_uid else "assigned",
                    file_id,
                    metadata_id,
                    ts,
                    ts,
                ),
            )
            db.execute(
                """
                INSERT INTO photo_files (
                  file_id, photo_id, relative_path, storage_role, size_bytes,
                  mtime, sha256, width, height, exists_on_disk, first_seen_at,
                  last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    file_id,
                    photo_id,
                    rel,
                    storage_role,
                    st.st_size,
                    mtime,
                    sha,
                    width,
                    height,
                    first_seen,
                    ts,
                ),
            )
            db.execute(
                """
                INSERT INTO photo_metadata (
                  metadata_id, photo_id, camera_make, camera_model, lens_model,
                  exposure_time, f_number, iso, focal_length, exif_datetime,
                  gps_lat, gps_lon, image_width, image_height, raw_exif_json,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    metadata_id,
                    photo_id,
                    meta.get("camera_make"),
                    meta.get("camera_model"),
                    meta.get("lens_model"),
                    meta.get("exposure_time"),
                    meta.get("f_number"),
                    meta.get("iso"),
                    meta.get("focal_length"),
                    meta.get("exif_datetime"),
                    meta.get("gps_lat"),
                    meta.get("gps_lon"),
                    width,
                    height,
                    meta.get("raw_exif_json", "{}"),
                    ts,
                    ts,
                ),
            )
    else:
        photo_id = existing_file["photo_id"]
        file_id = existing_file["file_id"]
        metadata_id = db.execute(
            "SELECT metadata_id FROM photos WHERE photo_id=?", (photo_id,)
        ).fetchone()["metadata_id"]
        with db:
            db.execute(
                """
                UPDATE photo_files
                   SET size_bytes=?, mtime=?, sha256=COALESCE(?, sha256),
                       width=COALESCE(?, width), height=COALESCE(?, height),
                       exists_on_disk=1, last_seen_at=?
                 WHERE file_id=?
                """,
                (st.st_size, mtime, sha, width, height, ts, file_id),
            )
            db.execute(
                "UPDATE photos SET updated_at=?, content_hash=COALESCE(?, content_hash) WHERE photo_id=?",
                (ts, sha, photo_id),
            )

    if specimen_uid:
        assign_photo_to_specimen(
            db,
            photo_id,
            specimen_uid,
            assignment_source=assignment_source,
        )

    return dict(
        db.execute(
            """
            SELECT p.*, f.file_id, f.relative_path, f.sha256, f.width, f.height
              FROM photos p
              JOIN photo_files f ON f.photo_id = p.photo_id
             WHERE p.photo_id=?
            """,
            (photo_id,),
        ).fetchone()
    )


def upsert_photo_files_lightweight(
    db,
    project_dir: str,
    files: list[tuple[str, Optional[str], Optional[str]]],
    *,
    storage_role: str = "incoming",
    photo_kind: str = "original",
    assignment_source: str = "scan",
) -> int:
    """Fast path for monitor scans.

    The monitor only needs the catalog to know a file exists and when it was
    seen.  Reading EXIF or hashing every JPG on every scan makes camera folders
    and network/WSL drives feel stuck, so this path records stat metadata only
    and avoids duplicate assignment history when the current UID is unchanged.
    """
    if not files:
        return 0

    root = Path(project_dir).resolve()
    prepared: list[tuple[str, str, os.stat_result, Optional[str], Optional[str]]] = []
    seen_rels: set[str] = set()
    for file_path, specimen_uid, first_seen_at in files:
        try:
            resolved = str(Path(file_path).resolve())
            st = os.stat(resolved)
            rel = _relative_path_from_root(root, resolved)
        except OSError:
            continue
        if rel in seen_rels:
            continue
        seen_rels.add(rel)
        prepared.append((resolved, rel, st, specimen_uid, first_seen_at))
    if not prepared:
        return 0

    existing_by_rel = {}
    rels = list(dict.fromkeys(rel for _resolved, rel, _st, _uid, _first in prepared))
    for chunk in _batched_values(rels):
        placeholders = ",".join("?" for _ in chunk)
        rows = db.execute(
            f"SELECT * FROM photo_files WHERE relative_path IN ({placeholders})",
            chunk,
        ).fetchall()
        for row in rows:
            existing_by_rel[_sqlite_row_value(row, "relative_path", 2)] = row

    ts = _utc_now_iso()
    workspace_id = _read_workspace_id(db)
    desired_assignments: list[tuple[str, str]] = []
    photo_ids_by_rel: dict[str, str] = {}

    with db:
        for resolved, rel, st, specimen_uid, first_seen_at in prepared:
            existing_file = existing_by_rel.get(rel)
            mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
            first_seen = first_seen_at or ts

            if existing_file is None:
                photo_id = str(uuid.uuid4())
                file_id = str(uuid.uuid4())
                metadata_id = str(uuid.uuid4())
                db.execute(
                    """
                    INSERT INTO photos (
                      photo_id, workspace_id, content_hash, original_filename,
                      first_seen_at, capture_datetime, photo_kind, lifecycle_status,
                      primary_file_id, metadata_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        photo_id,
                        workspace_id,
                        None,
                        Path(resolved).name,
                        first_seen,
                        None,
                        photo_kind,
                        "incoming",
                        file_id,
                        metadata_id,
                        ts,
                        ts,
                    ),
                )
                db.execute(
                    """
                    INSERT INTO photo_files (
                      file_id, photo_id, relative_path, storage_role, size_bytes,
                      mtime, sha256, width, height, exists_on_disk, first_seen_at,
                      last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        file_id,
                        photo_id,
                        rel,
                        storage_role,
                        st.st_size,
                        mtime,
                        None,
                        None,
                        None,
                        first_seen,
                        ts,
                    ),
                )
                db.execute(
                    """
                    INSERT INTO photo_metadata (
                      metadata_id, photo_id, raw_exif_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (metadata_id, photo_id, "{}", ts, ts),
                )
            else:
                photo_id = _sqlite_row_value(existing_file, "photo_id", 1)
                file_id = _sqlite_row_value(existing_file, "file_id", 0)
                db.execute(
                    """
                    UPDATE photo_files
                       SET size_bytes=?, mtime=?, exists_on_disk=1, last_seen_at=?
                     WHERE file_id=?
                    """,
                    (st.st_size, mtime, ts, file_id),
                )
                db.execute(
                    "UPDATE photos SET updated_at=? WHERE photo_id=?",
                    (ts, photo_id),
                )

            photo_ids_by_rel[rel] = photo_id
            if specimen_uid:
                desired_assignments.append((photo_id, specimen_uid))

        if desired_assignments:
            unique_photo_ids = list(dict.fromkeys(photo_id for photo_id, _uid in desired_assignments))
            current_by_photo: dict[str, Optional[str]] = {}
            for chunk in _batched_values(unique_photo_ids):
                placeholders = ",".join("?" for _ in chunk)
                rows = db.execute(
                    f"""
                    SELECT p.photo_id, a.specimen_uid
                      FROM photos p
                      LEFT JOIN photo_assignments a
                        ON a.assignment_id = p.current_assignment_id
                     WHERE p.photo_id IN ({placeholders})
                    """,
                    chunk,
                ).fetchall()
                for row in rows:
                    current_by_photo[_sqlite_row_value(row, "photo_id", 0)] = _sqlite_row_value(row, "specimen_uid", 1)

            for photo_id, specimen_uid in desired_assignments:
                if current_by_photo.get(photo_id) == specimen_uid:
                    continue
                aid = str(uuid.uuid4())
                db.execute(
                    "UPDATE photo_assignments SET is_current=0, revoked_at=? WHERE photo_id=? AND is_current=1",
                    (ts, photo_id),
                )
                db.execute(
                    """
                    INSERT INTO photo_assignments (
                      assignment_id, photo_id, specimen_uid, collection_event_id,
                      assigned_by, assigned_at, assignment_source, confidence, is_current
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        aid,
                        photo_id,
                        specimen_uid,
                        None,
                        "",
                        ts,
                        assignment_source,
                        None,
                    ),
                )
                db.execute(
                    """
                    UPDATE photos
                       SET current_assignment_id=?, lifecycle_status='assigned', updated_at=?
                     WHERE photo_id=?
                    """,
                    (aid, ts, photo_id),
                )
                current_by_photo[photo_id] = specimen_uid

    return len(photo_ids_by_rel)


def assign_photo_to_specimen(
    db,
    photo_id: str,
    specimen_uid: str,
    *,
    collection_event_id: Optional[str] = None,
    assigned_by: str = "",
    assignment_source: str = "manual",
    confidence: Optional[float] = None,
) -> dict:
    """Assign a photo to a specimen, preserving assignment history."""
    ts = _utc_now_iso()
    aid = str(uuid.uuid4())
    with db:
        db.execute(
            "UPDATE photo_assignments SET is_current=0, revoked_at=? WHERE photo_id=? AND is_current=1",
            (ts, photo_id),
        )
        db.execute(
            """
            INSERT INTO photo_assignments (
              assignment_id, photo_id, specimen_uid, collection_event_id,
              assigned_by, assigned_at, assignment_source, confidence, is_current
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                aid,
                photo_id,
                specimen_uid,
                collection_event_id,
                assigned_by,
                ts,
                assignment_source,
                confidence,
            ),
        )
        db.execute(
            """
            UPDATE photos
               SET current_assignment_id=?, lifecycle_status='assigned', updated_at=?
             WHERE photo_id=?
            """,
            (aid, ts, photo_id),
        )
    return dict(db.execute("SELECT * FROM photo_assignments WHERE assignment_id=?", (aid,)).fetchone())


def mark_catalog_files_missing_on_disk(db, project_dir: str) -> int:
    """Mark catalogued files missing when their relative path no longer exists."""
    root = Path(project_dir).resolve()
    rows = db.execute("SELECT file_id, relative_path FROM photo_files WHERE exists_on_disk=1").fetchall()
    missing = 0
    with db:
        for row in rows:
            if not (root / row["relative_path"]).exists():
                db.execute("UPDATE photo_files SET exists_on_disk=0 WHERE file_id=?", (row["file_id"],))
                missing += 1
    return missing


def list_unassigned_photos(db) -> list[dict]:
    return [
        dict(r)
        for r in db.execute(
            """
            SELECT p.*, f.relative_path
              FROM photos p
              JOIN photo_files f ON f.photo_id = p.photo_id
             WHERE p.current_assignment_id IS NULL
             ORDER BY p.first_seen_at
            """
        ).fetchall()
    ]
