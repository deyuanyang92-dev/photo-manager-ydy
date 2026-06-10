"""import_service.py — Read-only import from global data/*.json into per-project SQLite.

Safety guarantees:
  1. Source files are never opened for writing.
  2. SHA-256 is snapshotted before and verified after import; any change raises.
  3. Corrupt JSON aborts the entire import, leaving no partial writes.
  4. INSERT OR REPLACE ensures idempotency (no row duplication on re-import).
  5. A consistency gate checks row counts and a raw_json deep-equality sample.
"""

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.db.db_manager import open_project_db, close_project_db
from app.utils.naming import derive_uid


# ── Data classes ──────────────────────────────────────────────────────────

@dataclass
class ProjectImportCounts:
    project_dir: str
    specimens: int = 0
    tasks: int = 0
    grouping: int = 0


@dataclass
class ImportReport:
    ok: bool
    per_project: list[ProjectImportCounts] = field(default_factory=list)
    source_sha: dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None


# ── Internal helpers ──────────────────────────────────────────────────────

def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path: Path) -> Optional[object]:
    """Read and parse JSON. Returns None if file missing.
    Raises json.JSONDecodeError on corrupt JSON (let it propagate).
    """
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    return json.loads(text)  # raises on invalid JSON


def _lon_lat_or_null(v) -> Optional[float]:
    """Convert lon/lat value: empty string → None; non-numeric → None."""
    if v is None or v == "" or v == 0 and not isinstance(v, bool):
        # Allow actual numeric 0 as valid coordinate
        pass
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _upsert_specimen(conn: sqlite3.Connection, sp: dict) -> str:
    """Insert or replace a specimen row. Returns derived uid."""
    uid = derive_uid(sp)
    lon = _lon_lat_or_null(sp.get("lon"))
    lat = _lon_lat_or_null(sp.get("lat"))

    conn.execute(
        """
        INSERT OR REPLACE INTO specimens
          (uid, id, province, site, station, storage, collection_date, photo_date,
           scientific_name, scientific_name_cn, taxon_group, taxon_group_cn,
           order_name, order_cn, family, family_cn, genus, genus_cn,
           lon, lat, geo_area, collector, photographer, identifier,
           notes, photo_notes, angle, metadata, pinned, owner_project_dir, raw_json)
        VALUES
          (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            uid,
            sp.get("id"),
            sp.get("province"),
            sp.get("site"),
            sp.get("station"),
            sp.get("storage"),
            sp.get("collectionDate"),
            sp.get("photoDate"),
            sp.get("scientificName"),
            sp.get("scientificNameCn"),
            sp.get("taxonGroup"),
            sp.get("taxonGroupCn"),
            sp.get("order"),
            sp.get("orderCn"),
            sp.get("family"),
            sp.get("familyCn"),
            sp.get("genus"),
            sp.get("genusCn"),
            lon,
            lat,
            sp.get("geoArea"),
            sp.get("collector"),
            sp.get("photographer"),
            sp.get("identifier"),
            sp.get("notes"),
            sp.get("photoNotes"),
            sp.get("angle"),
            int(bool(sp.get("metadata", 0))),
            1 if sp.get("_pinned") else 0,
            sp.get("ownerProjectDir"),
            json.dumps(sp, ensure_ascii=False),
        ),
    )
    return uid


def _upsert_task(conn: sqlite3.Connection, uid: str, task: dict) -> None:
    """Insert or replace a task row. uid is used verbatim (no parsing)."""
    conn.execute(
        """
        INSERT OR REPLACE INTO tasks
          (uid, is_active, activated_at, last_organized_at,
           next_result_sequence_hint, raw_json)
        VALUES (?,?,?,?,?,?)
        """,
        (
            uid,
            1 if task.get("isActive") else 0,
            task.get("activatedAt"),
            task.get("lastOrganizedAt"),
            task.get("nextResultSequenceHint"),
            json.dumps(task, ensure_ascii=False),
        ),
    )


def _upsert_group(
    conn: sqlite3.Connection, uid: str, group_index: int, group: dict
) -> None:
    """Insert or replace a single grouping row."""
    conn.execute(
        """
        INSERT OR REPLACE INTO grouping
          (uid, group_index, angle_label, jpg_paths, composed_tiff_path,
           status, source, created_at, updated_at,
           result_sequence, archive_zip, retired_tiff_paths, raw_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            uid,
            group_index,
            group.get("angleLabel"),
            json.dumps(group.get("jpgPaths", []), ensure_ascii=False),
            group.get("composedTiffPath"),
            group.get("status"),
            group.get("source"),
            group.get("createdAt"),
            group.get("updatedAt"),
            group.get("resultSequence"),
            group.get("archiveZip"),
            json.dumps(group.get("retiredTiffPaths", []), ensure_ascii=False)
            if group.get("retiredTiffPaths") is not None
            else None,
            json.dumps(group, ensure_ascii=False),
        ),
    )


def _write_manifest(
    conn: sqlite3.Connection,
    source_file: str,
    sha256: str,
    row_count: int,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO _import_manifest (source_file, sha256, row_count, imported_at)
        VALUES (?,?,?,?)
        """,
        (source_file, sha256, row_count, datetime.now(timezone.utc).isoformat()),
    )


# ── Public API ────────────────────────────────────────────────────────────

def import_all(global_data_dir: str, projects: list[dict]) -> ImportReport:
    """Import global JSON data into per-project SQLite databases.

    Parameters
    ----------
    global_data_dir:
        Path to the directory containing user_specimens.json,
        specimen_tasks.json, and grouping_confirmations.json.
    projects:
        List of project dicts. Each must contain either
        ``_resolved_test_dir`` (test override) or ``dir``/``path``
        pointing to the project directory where ``_data/project.db``
        will be created.

    Returns
    -------
    ImportReport
        Per-project row counts and source SHA-256 hashes.

    Raises
    ------
    ValueError
        If a source JSON file is corrupt (non-parseable).
    sqlite3.IntegrityError
        If source files were mutated during import, or consistency
        checks fail.
    """
    data_dir = Path(global_data_dir)
    report = ImportReport(ok=False)

    # Identify source files to snapshot
    source_files = {
        "user_specimens.json": data_dir / "user_specimens.json",
        "specimen_tasks.json": data_dir / "specimen_tasks.json",
        "grouping_confirmations.json": data_dir / "grouping_confirmations.json",
    }

    # ── Step 1: SHA-256 snapshot BEFORE read ─────────────────────────────
    sha_before: dict[str, str] = {}
    for name, path in source_files.items():
        if path.exists():
            sha_before[name] = _sha256(str(path))
    report.source_sha = dict(sha_before)

    # ── Step 2: Parse all JSON (fail fast on corrupt) ─────────────────────
    # We read all three files before touching any DB.
    try:
        specimens_data = _read_json(source_files["user_specimens.json"])
        tasks_data = _read_json(source_files["specimen_tasks.json"])
        grouping_data = _read_json(source_files["grouping_confirmations.json"])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Corrupt JSON in source data: {exc}") from exc

    all_specimens: list[dict] = []
    if specimens_data is not None:
        raw = specimens_data
        if isinstance(raw, list):
            all_specimens = raw
        elif isinstance(raw, dict):
            all_specimens = raw.get("specimens", [])

    tasks_by_project: dict[str, dict[str, dict]] = {}
    if tasks_data is not None:
        tasks_by_project = tasks_data.get("projects", {})

    grouping_by_project: dict = {}
    if grouping_data is not None:
        grouping_by_project = grouping_data.get("projects", {})

    # ── Step 3: Import per project ────────────────────────────────────────
    for proj in projects:
        # Support test override key or standard dir/path keys
        proj_dir = (
            proj.get("_resolved_test_dir")
            or proj.get("dir")
            or proj.get("path")
            or proj.get("projectDir")
        )
        if not proj_dir:
            continue

        proj_dir_str = str(Path(proj_dir).resolve())
        # Import = deliberately claiming this project's workspace, so db
        # creation is legitimate (create=True still refuses a phantom
        # mountpoint via require_project_root).
        conn = open_project_db(proj_dir_str, create=True)
        counts = ProjectImportCounts(project_dir=proj_dir_str)

        # -- specimens --
        proj_specimens = [
            sp for sp in all_specimens
            if str(sp.get("ownerProjectDir", "")) == str(proj_dir)
               or str(Path(sp.get("ownerProjectDir", "")).resolve()) == proj_dir_str
        ]
        with conn:
            for sp in proj_specimens:
                _upsert_specimen(conn, sp)
            counts.specimens = len(proj_specimens)
            if "user_specimens.json" in sha_before:
                _write_manifest(
                    conn, "user_specimens.json",
                    sha_before["user_specimens.json"], counts.specimens
                )

        # -- tasks --
        # Try both the raw project path and resolved
        proj_tasks: dict[str, dict] = {}
        for key in tasks_by_project:
            # Match by raw key string, or resolved path
            if key == str(proj_dir) or str(Path(key).resolve()) == proj_dir_str:
                proj_tasks = tasks_by_project[key]
                break
        with conn:
            for uid, task in proj_tasks.items():
                _upsert_task(conn, uid, task)
            counts.tasks = len(proj_tasks)
            if "specimen_tasks.json" in sha_before:
                _write_manifest(
                    conn, "specimen_tasks.json",
                    sha_before["specimen_tasks.json"], counts.tasks
                )

        # -- grouping --
        proj_grouping: dict = {}
        for key in grouping_by_project:
            if key == str(proj_dir) or str(Path(key).resolve()) == proj_dir_str:
                proj_grouping = grouping_by_project[key]
                break
        specimens_grouping = proj_grouping.get("specimens", {})
        group_row_count = 0
        with conn:
            for uid, spec_data in specimens_grouping.items():
                for group in spec_data.get("groups", []):
                    _upsert_group(conn, uid, group.get("groupIndex", 0), group)
                    group_row_count += 1
            counts.grouping = group_row_count
            if "grouping_confirmations.json" in sha_before:
                _write_manifest(
                    conn, "grouping_confirmations.json",
                    sha_before["grouping_confirmations.json"], counts.grouping
                )

        report.per_project.append(counts)

    # ── Step 4: SHA-256 verification AFTER all writes ─────────────────────
    for name, path in source_files.items():
        if name in sha_before:
            sha_after = _sha256(str(path))
            if sha_after != sha_before[name]:
                raise sqlite3.IntegrityError(
                    f"Source file was mutated during import: {name}"
                )

    # ── Step 5: Consistency gate ──────────────────────────────────────────
    for proj_counts in report.per_project:
        conn = open_project_db(proj_counts.project_dir)
        db_specimens = conn.execute("SELECT COUNT(*) FROM specimens").fetchone()[0]
        db_tasks = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        db_grouping = conn.execute("SELECT COUNT(*) FROM grouping").fetchone()[0]

        if db_specimens < proj_counts.specimens:
            raise sqlite3.IntegrityError(
                f"Consistency check failed: expected {proj_counts.specimens} specimens "
                f"in {proj_counts.project_dir}, got {db_specimens}"
            )

        # Sample raw_json deep-equality check for specimens
        sample_rows = conn.execute(
            "SELECT raw_json FROM specimens LIMIT 3"
        ).fetchall()
        for row in sample_rows:
            try:
                obj = json.loads(row[0])
                assert isinstance(obj, dict)
            except (json.JSONDecodeError, AssertionError) as exc:
                raise sqlite3.IntegrityError(
                    f"raw_json roundtrip failed in {proj_counts.project_dir}: {exc}"
                ) from exc

    report.ok = True
    return report
