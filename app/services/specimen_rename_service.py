"""specimen_rename_service.py — UID correction after renameSpecimenCode / applyStorageCorrection.

Oracle: app.js functions renameSpecimenCode (line ~3112), applyStorageCorrection (line ~3001),
migrateSpecimenUidReferences (line ~2960), specimenHasRiskyUidReferences (line ~2947).

Key schema facts:
  - seen_files table has no specimen_uid column; file attribution uses file name only.
  - Risky references live in: grouping (uid FK) and tasks (uid PK).
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from app.utils.naming import derive_uid


def specimen_has_risky_references(db: sqlite3.Connection, uid: str) -> bool:
    """True if the specimen has grouping or tasks records that reference this UID.

    Mirrors specimenHasRiskyUidReferences() in oracle (app.js:2947).
    The oracle checks resultsByUid (photos → grouping table) and specimenTasks
    (tasks table).  seen_files has no specimen_uid column in our schema.
    """
    if not uid:
        return False
    row = db.execute("SELECT 1 FROM grouping WHERE uid=? LIMIT 1", (uid,)).fetchone()
    if row:
        return True
    row = db.execute("SELECT 1 FROM tasks WHERE uid=? LIMIT 1", (uid,)).fetchone()
    if row:
        return True
    return False


def _result_name_for_uid(name: str, old_uid: str, new_uid: str) -> str | None:
    """Return a filename rewritten from old UID to new UID, preserving sequence."""
    path = Path(name)
    parts = path.stem.split("-")
    if len(parts) < 7:
        return None
    try:
        seq = int(parts[4])
    except ValueError:
        return None
    candidate_uid = "-".join(parts[:4] + parts[5:])
    if candidate_uid != old_uid:
        return None
    new_parts = new_uid.split("-")
    new_parts.insert(4, str(seq))
    return "-".join(new_parts) + path.suffix


def _planned_result_path(path: str | None, old_uid: str, new_uid: str) -> str | None:
    if not path:
        return None
    new_name = _result_name_for_uid(Path(path).name, old_uid, new_uid)
    if not new_name:
        return None
    return str(Path(path).with_name(new_name))


def _json_load_object(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _replace_json_values(value, replacements: dict[str, str]):
    """Recursively replace exact string path values in a raw_json blob."""
    if isinstance(value, str):
        return replacements.get(value, value)
    if isinstance(value, list):
        return [_replace_json_values(v, replacements) for v in value]
    if isinstance(value, dict):
        return {k: _replace_json_values(v, replacements) for k, v in value.items()}
    return value


def _rewrite_zip_manifest(zip_path: str, old_uid: str, new_uid: str) -> None:
    """Update manifest.json tiffBasename after a result ZIP is renamed."""
    if not os.path.isfile(zip_path):
        return
    try:
        with zipfile.ZipFile(zip_path, "r") as src:
            if "manifest.json" not in src.namelist():
                return
            manifest = json.loads(src.read("manifest.json").decode("utf-8"))
            if not isinstance(manifest, dict):
                return
            old_base = str(manifest.get("tiffBasename") or "")
            new_base = _result_name_for_uid(old_base, old_uid, new_uid)
            if not new_base:
                return
            manifest["tiffBasename"] = Path(new_base).stem
            fd, tmp = tempfile.mkstemp(
                prefix=Path(zip_path).stem + "-",
                suffix=".zip",
                dir=str(Path(zip_path).parent),
            )
            os.close(fd)
            try:
                with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as dst:
                    for item in src.infolist():
                        if item.filename == "manifest.json":
                            dst.writestr(
                                item,
                                json.dumps(manifest, indent=2, ensure_ascii=False),
                            )
                        else:
                            dst.writestr(item, src.read(item.filename))
                os.replace(tmp, zip_path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError):
        return


def _migrate_grouping_result_files(
    db: sqlite3.Connection, old_uid: str, new_uid: str
) -> None:
    rows = db.execute(
        """
        SELECT group_index, composed_tiff_path, archive_zip,
               retired_tiff_paths, raw_json
        FROM grouping
        WHERE uid = ?
        """,
        (old_uid,),
    ).fetchall()
    if not rows:
        return

    planned: dict[str, str] = {}
    row_updates: list[tuple[int, str | None, str | None, str | None, str | None]] = []

    for row in rows:
        d = dict(row) if not isinstance(row, dict) else row
        replacements: dict[str, str] = {}

        tiff_old = d.get("composed_tiff_path")
        tiff_new = _planned_result_path(tiff_old, old_uid, new_uid)
        if tiff_old and tiff_new:
            replacements[tiff_old] = tiff_new
            if os.path.isfile(tiff_old):
                planned[tiff_old] = tiff_new

        zip_old = d.get("archive_zip")
        zip_new = _planned_result_path(zip_old, old_uid, new_uid)
        if zip_old and zip_new:
            replacements[zip_old] = zip_new
            if os.path.isfile(zip_old):
                planned[zip_old] = zip_new

        # Older rows may have a sibling ZIP on disk but no archive_zip column set.
        sibling_zip_new = None
        if tiff_old:
            sibling_zip_old = str(Path(tiff_old).with_suffix(".zip"))
            sibling_zip_new = _planned_result_path(sibling_zip_old, old_uid, new_uid)
            if (
                sibling_zip_new
                and sibling_zip_old not in planned
                and os.path.isfile(sibling_zip_old)
            ):
                replacements[sibling_zip_old] = sibling_zip_new
                planned[sibling_zip_old] = sibling_zip_new

        retired = []
        try:
            retired = json.loads(d.get("retired_tiff_paths") or "[]")
        except Exception:
            retired = []
        if not isinstance(retired, list):
            retired = []
        new_retired = []
        for item in retired:
            if not isinstance(item, str):
                new_retired.append(item)
                continue
            new_item = _planned_result_path(item, old_uid, new_uid)
            if new_item:
                replacements[item] = new_item
                if os.path.isfile(item):
                    planned[item] = new_item
                new_retired.append(new_item)
            else:
                new_retired.append(item)

        raw_obj = _json_load_object(d.get("raw_json"))
        raw_json = None
        if raw_obj and replacements:
            raw_json = json.dumps(
                _replace_json_values(raw_obj, replacements),
                ensure_ascii=False,
            )
        elif d.get("raw_json") is not None:
            raw_json = d.get("raw_json")

        row_updates.append((
            int(d["group_index"]),
            replacements.get(tiff_old, tiff_old) if tiff_old else tiff_old,
            (
                replacements.get(zip_old, zip_old)
                if zip_old
                else sibling_zip_new
            ),
            json.dumps(new_retired, ensure_ascii=False),
            raw_json,
        ))

    for src, dst in planned.items():
        if src == dst:
            continue
        if os.path.exists(dst):
            raise ValueError(f"目标文件已存在，无法重命名: {dst}")

    renamed_zips: list[str] = []
    for src, dst in planned.items():
        if src == dst:
            continue
        os.replace(src, dst)
        if dst.lower().endswith(".zip"):
            renamed_zips.append(dst)

    for zip_path in renamed_zips:
        _rewrite_zip_manifest(zip_path, old_uid, new_uid)

    for group_index, tiff_path, archive_zip, retired_json, raw_json in row_updates:
        db.execute(
            """
            UPDATE grouping
            SET composed_tiff_path = ?,
                archive_zip = ?,
                retired_tiff_paths = ?,
                raw_json = ?
            WHERE uid = ? AND group_index = ?
            """,
            (tiff_path, archive_zip, retired_json, raw_json, old_uid, group_index),
        )


def migrate_uid_references(db: sqlite3.Connection, old_uid: str, new_uid: str) -> None:
    """Update grouping and tasks rows from old_uid → new_uid.

    Must be called inside a transaction (caller manages the transaction).
    Mirrors migrateSpecimenUidReferences() in oracle (app.js:2960).
    """
    if old_uid == new_uid:
        return
    _migrate_grouping_result_files(db, old_uid, new_uid)
    db.execute("UPDATE grouping SET uid=? WHERE uid=?", (new_uid, old_uid))
    db.execute("UPDATE tasks SET uid=? WHERE uid=?", (new_uid, old_uid))


def rename_specimen_code(db: sqlite3.Connection, uid: str, new_code: str) -> str:
    """Change the specimen id segment (sp.id) and return the new UID.

    Mirrors renameSpecimenCode() in oracle (app.js:3112) extended with the
    server-side UID migration that syncSpecimenUidCorrectionToServer triggers.

    Raises ValueError if:
      - the specimen is not found
      - the resulting new UID already exists (collision)
    """
    row = db.execute(
        "SELECT uid, raw_json FROM specimens WHERE uid=?", (uid,)
    ).fetchone()
    if not row:
        raise ValueError(f"Specimen not found: {uid}")

    raw = json.loads(row["raw_json"] or "{}")
    raw["id"] = new_code
    new_uid = derive_uid(raw)

    if new_uid == uid:
        return uid

    if db.execute("SELECT 1 FROM specimens WHERE uid=?", (new_uid,)).fetchone():
        raise ValueError(f"UID already exists: {new_uid}")

    prev_uids: list = raw.get("previousUniqueIds") or []
    if not isinstance(prev_uids, list):
        prev_uids = []
    if uid not in prev_uids:
        prev_uids.append(uid)
    raw["previousUniqueIds"] = prev_uids

    with db:
        db.execute(
            "UPDATE specimens SET uid=?, id=?, raw_json=? WHERE uid=?",
            (new_uid, new_code, json.dumps(raw, ensure_ascii=False), uid),
        )
        migrate_uid_references(db, uid, new_uid)

    return new_uid


def apply_storage_correction(
    db: sqlite3.Connection, uid: str, new_storage: str
) -> str:
    """Change storage type, recalculate UID, migrate all references.

    Mirrors applyStorageCorrection() in oracle (app.js:3001).

    Raises ValueError if:
      - the specimen is not found
      - the new UID would collide with an existing specimen
    """
    row = db.execute(
        "SELECT uid, raw_json FROM specimens WHERE uid=?", (uid,)
    ).fetchone()
    if not row:
        raise ValueError(f"Specimen not found: {uid}")

    raw = json.loads(row["raw_json"] or "{}")
    old_storage = raw.get("storage", "")

    if old_storage == new_storage:
        return uid

    raw["storage"] = new_storage
    new_uid = derive_uid(raw)

    if new_uid == uid:
        with db:
            db.execute(
                "UPDATE specimens SET storage=?, raw_json=? WHERE uid=?",
                (new_storage, json.dumps(raw, ensure_ascii=False), uid),
            )
        return uid

    if db.execute("SELECT 1 FROM specimens WHERE uid=?", (new_uid,)).fetchone():
        raise ValueError(f"UID would conflict: {new_uid}")

    prev_uids: list = raw.get("previousUniqueIds") or []
    if not isinstance(prev_uids, list):
        prev_uids = []
    if uid not in prev_uids:
        prev_uids.append(uid)
    raw["previousUniqueIds"] = prev_uids

    with db:
        db.execute(
            "UPDATE specimens SET uid=?, storage=?, raw_json=? WHERE uid=?",
            (new_uid, new_storage, json.dumps(raw, ensure_ascii=False), uid),
        )
        migrate_uid_references(db, uid, new_uid)

    return new_uid
