"""Photo asset catalog for workspace databases."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rel(project_dir: str, file_path: str) -> str:
    return os.path.relpath(str(Path(file_path).resolve()), str(Path(project_dir).resolve())).replace(os.sep, "/")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _workspace_id(db) -> Optional[str]:
    try:
        row = db.execute("SELECT workspace_id FROM workspace_meta LIMIT 1").fetchone()
        return row["workspace_id"] if row else None
    except Exception:
        return None


def _image_metadata(path: str) -> dict:
    meta: dict = {}
    try:
        from PIL import ExifTags, Image

        with Image.open(path) as img:
            meta["image_width"] = img.width
            meta["image_height"] = img.height
            raw_exif = {}
            try:
                exif = img.getexif()
            except Exception:
                exif = None
            if exif:
                tags = ExifTags.TAGS
                for key, value in exif.items():
                    name = tags.get(key, str(key))
                    try:
                        json.dumps(value)
                        raw_exif[name] = value
                    except TypeError:
                        raw_exif[name] = str(value)
                meta["camera_make"] = raw_exif.get("Make")
                meta["camera_model"] = raw_exif.get("Model")
                meta["lens_model"] = raw_exif.get("LensModel")
                meta["exposure_time"] = str(raw_exif.get("ExposureTime") or "")
                meta["f_number"] = str(raw_exif.get("FNumber") or "")
                meta["iso"] = str(raw_exif.get("ISOSpeedRatings") or raw_exif.get("PhotographicSensitivity") or "")
                meta["focal_length"] = str(raw_exif.get("FocalLength") or "")
                meta["exif_datetime"] = (
                    raw_exif.get("DateTimeOriginal")
                    or raw_exif.get("DateTimeDigitized")
                    or raw_exif.get("DateTime")
                )
                meta["raw_exif_json"] = json.dumps(raw_exif, ensure_ascii=False)
            else:
                meta["raw_exif_json"] = "{}"
    except Exception:
        meta.setdefault("raw_exif_json", "{}")
    return meta


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
) -> dict:
    """Register a photo file and immutable first-seen metadata.

    The current implementation keeps one logical ``photos`` row per file path.
    Duplicate hashes are reported later by QC instead of being silently merged.
    """
    resolved = str(Path(file_path).resolve())
    st = os.stat(resolved)
    rel = _rel(project_dir, resolved)
    ts = _now()
    first_seen = first_seen_at or ts
    existing_file = db.execute(
        "SELECT * FROM photo_files WHERE relative_path=?",
        (rel,),
    ).fetchone()
    sha = None
    if compute_hash and (existing_file is None or existing_file["size_bytes"] != st.st_size):
        sha = _sha256(resolved)
    elif existing_file is not None:
        sha = existing_file["sha256"]

    meta = _image_metadata(resolved)
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
                    _workspace_id(db),
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
        assign_photo(
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


def assign_photo(
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
    ts = _now()
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


def mark_missing_files(db, project_dir: str) -> int:
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


def list_unassigned(db) -> list[dict]:
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
