"""specimen_rename_service.py — UID correction after renameSpecimenCode / applyStorageCorrection.

Oracle: app.js functions renameSpecimenCode (line ~3112), applyStorageCorrection (line ~3001),
migrateSpecimenUidReferences (line ~2960), specimenHasRiskyUidReferences (line ~2947).

Key schema facts:
  - seen_files table has no specimen_uid column; file attribution uses file name only.
  - Risky references live in: grouping (uid FK) and tasks (uid PK).
"""
from __future__ import annotations

import json
import sqlite3

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


def migrate_uid_references(db: sqlite3.Connection, old_uid: str, new_uid: str) -> None:
    """Update grouping and tasks rows from old_uid → new_uid.

    Must be called inside a transaction (caller manages the transaction).
    Mirrors migrateSpecimenUidReferences() in oracle (app.js:2960).
    """
    if old_uid == new_uid:
        return
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
