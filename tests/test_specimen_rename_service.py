"""Tests for specimen_rename_service.py.

Oracle functions: renameSpecimenCode (app.js:3112),
                  applyStorageCorrection (app.js:3001),
                  migrateSpecimenUidReferences (app.js:2960),
                  specimenHasRiskyUidReferences (app.js:2947).
"""
from __future__ import annotations

import json
import sqlite3
import zipfile

import pytest

from app.services.specimen_rename_service import (
    apply_storage_correction,
    migrate_uid_references,
    rename_specimen_code,
    specimen_has_risky_references,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS specimens (
            uid TEXT PRIMARY KEY,
            id TEXT,
            province TEXT, site TEXT, station TEXT,
            storage TEXT, collection_date TEXT, photo_date TEXT,
            scientific_name TEXT, scientific_name_cn TEXT,
            taxon_group TEXT, taxon_group_cn TEXT,
            order_name TEXT, order_cn TEXT,
            family TEXT, family_cn TEXT, genus TEXT, genus_cn TEXT,
            lon REAL, lat REAL, geo_area TEXT,
            collector TEXT, photographer TEXT, identifier TEXT,
            notes TEXT, photo_notes TEXT, angle TEXT,
            metadata INTEGER DEFAULT 0, pinned INTEGER DEFAULT 0,
            owner_project_dir TEXT,
            raw_json TEXT
        );
        CREATE TABLE IF NOT EXISTS tasks (
            uid TEXT PRIMARY KEY,
            is_active INTEGER DEFAULT 0,
            activated_at TEXT, last_organized_at TEXT,
            next_result_sequence_hint INTEGER,
            raw_json TEXT
        );
        CREATE TABLE IF NOT EXISTS grouping (
            uid TEXT, group_index INTEGER,
            angle_label TEXT, jpg_paths TEXT, composed_tiff_path TEXT,
            status TEXT, source TEXT, created_at TEXT, updated_at TEXT,
            result_sequence INTEGER, archive_zip TEXT, retired_tiff_paths TEXT,
            raw_json TEXT,
            PRIMARY KEY (uid, group_index)
        );
        CREATE TABLE IF NOT EXISTS seen_files (
            name TEXT PRIMARY KEY,
            first_seen_at TEXT
        );
    """)
    return db


def _insert_specimen(db: sqlite3.Connection, uid: str, raw: dict | None = None) -> None:
    if raw is None:
        raw = {}
    raw.setdefault("uid", uid)
    db.execute(
        "INSERT INTO specimens (uid, id, province, site, station, storage, "
        "collection_date, photo_date, raw_json) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            uid,
            raw.get("id", "DLC001"),
            raw.get("province", "FJ"),
            raw.get("site", "XM"),
            raw.get("station", "B2"),
            raw.get("storage", "T95E"),
            raw.get("collectionDate", "20260601"),
            raw.get("photoDate", ""),
            json.dumps(raw, ensure_ascii=False),
        ),
    )
    db.commit()


# ── specimen_has_risky_references ─────────────────────────────────────────────

def test_has_risky_no_refs():
    db = _make_db()
    _insert_specimen(db, "FJ-XM-B2-DLC001-T95E-20260601")
    assert specimen_has_risky_references(db, "FJ-XM-B2-DLC001-T95E-20260601") is False


def test_has_risky_with_grouping():
    db = _make_db()
    uid = "FJ-XM-B2-DLC001-T95E-20260601"
    _insert_specimen(db, uid)
    db.execute(
        "INSERT INTO grouping (uid, group_index) VALUES (?, 0)",
        (uid,),
    )
    db.commit()
    assert specimen_has_risky_references(db, uid) is True


def test_has_risky_with_tasks():
    db = _make_db()
    uid = "FJ-XM-B2-DLC001-T95E-20260601"
    _insert_specimen(db, uid)
    db.execute("INSERT INTO tasks (uid, is_active) VALUES (?, 1)", (uid,))
    db.commit()
    assert specimen_has_risky_references(db, uid) is True


def test_has_risky_false_for_unknown_uid():
    db = _make_db()
    assert specimen_has_risky_references(db, "NONEXISTENT-UID") is False


# ── migrate_uid_references ────────────────────────────────────────────────────

def test_migrate_uid_references_updates_all_tables():
    db = _make_db()
    old = "FJ-XM-B2-DLC001-T95E-20260601"
    new = "FJ-XM-B2-DLC002-T95E-20260601"
    _insert_specimen(db, old)
    db.execute("INSERT INTO grouping (uid, group_index) VALUES (?, 0)", (old,))
    db.execute("INSERT INTO tasks (uid) VALUES (?)", (old,))
    db.commit()
    with db:
        migrate_uid_references(db, old, new)
    assert db.execute("SELECT 1 FROM grouping WHERE uid=?", (new,)).fetchone()
    assert db.execute("SELECT 1 FROM tasks WHERE uid=?", (new,)).fetchone()
    assert not db.execute("SELECT 1 FROM grouping WHERE uid=?", (old,)).fetchone()
    assert not db.execute("SELECT 1 FROM tasks WHERE uid=?", (old,)).fetchone()


def test_migrate_uid_same_is_noop():
    db = _make_db()
    uid = "FJ-XM-B2-DLC001-T95E-20260601"
    _insert_specimen(db, uid)
    db.execute("INSERT INTO tasks (uid) VALUES (?)", (uid,))
    db.commit()
    with db:
        migrate_uid_references(db, uid, uid)
    assert db.execute("SELECT 1 FROM tasks WHERE uid=?", (uid,)).fetchone()


# ── rename_specimen_code ──────────────────────────────────────────────────────

def test_rename_specimen_code_updates_uid():
    db = _make_db()
    raw = {
        "id": "DLC001", "province": "FJ", "site": "XM", "station": "B2",
        "storage": "T95E", "collectionDate": "20260601",
    }
    old_uid = "FJ-XM-B2-DLC001-T95E-20260601"
    _insert_specimen(db, old_uid, raw)
    new_uid = rename_specimen_code(db, old_uid, "DLC002")
    assert new_uid == "FJ-XM-B2-DLC002-T95E-20260601"
    assert db.execute("SELECT 1 FROM specimens WHERE uid=?", (new_uid,)).fetchone()
    assert not db.execute("SELECT 1 FROM specimens WHERE uid=?", (old_uid,)).fetchone()


def test_rename_specimen_code_migrates_grouping():
    db = _make_db()
    raw = {
        "id": "DLC001", "province": "FJ", "site": "XM", "station": "B2",
        "storage": "T95E", "collectionDate": "20260601",
    }
    old_uid = "FJ-XM-B2-DLC001-T95E-20260601"
    _insert_specimen(db, old_uid, raw)
    db.execute("INSERT INTO grouping (uid, group_index) VALUES (?, 0)", (old_uid,))
    db.commit()
    new_uid = rename_specimen_code(db, old_uid, "DLC002")
    assert db.execute("SELECT 1 FROM grouping WHERE uid=?", (new_uid,)).fetchone()
    assert not db.execute("SELECT 1 FROM grouping WHERE uid=?", (old_uid,)).fetchone()


def test_rename_specimen_code_migrates_tasks():
    db = _make_db()
    raw = {
        "id": "DLC001", "province": "FJ", "site": "XM", "station": "B2",
        "storage": "T95E", "collectionDate": "20260601",
    }
    old_uid = "FJ-XM-B2-DLC001-T95E-20260601"
    _insert_specimen(db, old_uid, raw)
    db.execute("INSERT INTO tasks (uid) VALUES (?)", (old_uid,))
    db.commit()
    new_uid = rename_specimen_code(db, old_uid, "DLC002")
    assert db.execute("SELECT 1 FROM tasks WHERE uid=?", (new_uid,)).fetchone()
    assert not db.execute("SELECT 1 FROM tasks WHERE uid=?", (old_uid,)).fetchone()


def test_rename_specimen_code_renames_result_tiff_and_archive_zip(tmp_path):
    db = _make_db()
    raw = {
        "id": "DLC001", "province": "FJ", "site": "XM", "station": "B2",
        "storage": "T95E", "collectionDate": "20260601",
    }
    old_uid = "FJ-XM-B2-DLC001-T95E-20260601"
    _insert_specimen(db, old_uid, raw)

    tiff = tmp_path / "FJ-XM-B2-DLC001-1-T95E-20260601.tif"
    zip_path = tmp_path / "FJ-XM-B2-DLC001-1-T95E-20260601.zip"
    tiff.write_bytes(b"TIFF")
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({
            "tiffBasename": "FJ-XM-B2-DLC001-1-T95E-20260601",
            "files": [],
        }))

    db.execute(
        """
        INSERT INTO grouping
          (uid, group_index, composed_tiff_path, archive_zip, raw_json)
        VALUES (?, 1, ?, ?, ?)
        """,
        (
            old_uid,
            str(tiff),
            str(zip_path),
            json.dumps({
                "composedTiffPath": str(tiff),
                "archiveZip": str(zip_path),
            }, ensure_ascii=False),
        ),
    )
    db.commit()

    new_uid = rename_specimen_code(db, old_uid, "DLC002")
    new_tiff = tmp_path / "FJ-XM-B2-DLC002-1-T95E-20260601.tif"
    new_zip = tmp_path / "FJ-XM-B2-DLC002-1-T95E-20260601.zip"

    assert new_uid == "FJ-XM-B2-DLC002-T95E-20260601"
    assert new_tiff.is_file()
    assert new_zip.is_file()
    assert not tiff.exists()
    assert not zip_path.exists()

    row = db.execute(
        "SELECT composed_tiff_path, archive_zip, raw_json FROM grouping WHERE uid=?",
        (new_uid,),
    ).fetchone()
    assert row["composed_tiff_path"] == str(new_tiff)
    assert row["archive_zip"] == str(new_zip)
    saved = json.loads(row["raw_json"])
    assert saved["composedTiffPath"] == str(new_tiff)
    assert saved["archiveZip"] == str(new_zip)

    with zipfile.ZipFile(new_zip, "r") as zf:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    assert manifest["tiffBasename"] == "FJ-XM-B2-DLC002-1-T95E-20260601"


def test_rename_specimen_code_refuses_result_file_collision(tmp_path):
    db = _make_db()
    raw = {
        "id": "DLC001", "province": "FJ", "site": "XM", "station": "B2",
        "storage": "T95E", "collectionDate": "20260601",
    }
    old_uid = "FJ-XM-B2-DLC001-T95E-20260601"
    _insert_specimen(db, old_uid, raw)

    old_tiff = tmp_path / "FJ-XM-B2-DLC001-1-T95E-20260601.tif"
    new_tiff = tmp_path / "FJ-XM-B2-DLC002-1-T95E-20260601.tif"
    old_tiff.write_bytes(b"old")
    new_tiff.write_bytes(b"occupied")
    db.execute(
        "INSERT INTO grouping (uid, group_index, composed_tiff_path) VALUES (?, 1, ?)",
        (old_uid, str(old_tiff)),
    )
    db.commit()

    with pytest.raises(ValueError, match="目标文件已存在"):
        rename_specimen_code(db, old_uid, "DLC002")

    assert old_tiff.read_bytes() == b"old"
    assert new_tiff.read_bytes() == b"occupied"
    assert db.execute("SELECT 1 FROM specimens WHERE uid=?", (old_uid,)).fetchone()


def test_rename_specimen_code_backfills_renamed_sibling_zip(tmp_path):
    db = _make_db()
    raw = {
        "id": "DLC001", "province": "FJ", "site": "XM", "station": "B2",
        "storage": "T95E", "collectionDate": "20260601",
    }
    old_uid = "FJ-XM-B2-DLC001-T95E-20260601"
    _insert_specimen(db, old_uid, raw)
    old_tiff = tmp_path / "FJ-XM-B2-DLC001-1-T95E-20260601.tif"
    old_zip = tmp_path / "FJ-XM-B2-DLC001-1-T95E-20260601.zip"
    old_tiff.write_bytes(b"tiff")
    with zipfile.ZipFile(old_zip, "w") as zf:
        zf.writestr("manifest.json", json.dumps({
            "tiffBasename": "FJ-XM-B2-DLC001-1-T95E-20260601",
        }))
    db.execute(
        "INSERT INTO grouping (uid, group_index, composed_tiff_path, archive_zip) VALUES (?, 1, ?, NULL)",
        (old_uid, str(old_tiff)),
    )
    db.commit()

    new_uid = rename_specimen_code(db, old_uid, "DLC002")
    new_zip = tmp_path / "FJ-XM-B2-DLC002-1-T95E-20260601.zip"

    row = db.execute(
        "SELECT archive_zip FROM grouping WHERE uid=? AND group_index=1",
        (new_uid,),
    ).fetchone()
    assert row["archive_zip"] == str(new_zip)
    assert new_zip.is_file()


def test_rename_specimen_code_migrates_seen_files_noop():
    """seen_files has no specimen_uid column — migration is a no-op for that table."""
    db = _make_db()
    raw = {
        "id": "DLC001", "province": "FJ", "site": "XM", "station": "B2",
        "storage": "T95E", "collectionDate": "20260601",
    }
    old_uid = "FJ-XM-B2-DLC001-T95E-20260601"
    _insert_specimen(db, old_uid, raw)
    db.execute("INSERT INTO seen_files (name, first_seen_at) VALUES (?, ?)",
               ("FJ-XM-B2-DLC001-T95E-20260601-001.jpg", "2026-06-01"))
    db.commit()
    new_uid = rename_specimen_code(db, old_uid, "DLC002")
    assert new_uid == "FJ-XM-B2-DLC002-T95E-20260601"
    row = db.execute("SELECT name FROM seen_files").fetchone()
    assert row["name"] == "FJ-XM-B2-DLC001-T95E-20260601-001.jpg"


def test_rename_collision_raises_ValueError():
    db = _make_db()
    raw1 = {
        "id": "DLC001", "province": "FJ", "site": "XM", "station": "B2",
        "storage": "T95E", "collectionDate": "20260601",
    }
    raw2 = {
        "id": "DLC002", "province": "FJ", "site": "XM", "station": "B2",
        "storage": "T95E", "collectionDate": "20260601",
    }
    _insert_specimen(db, "FJ-XM-B2-DLC001-T95E-20260601", raw1)
    _insert_specimen(db, "FJ-XM-B2-DLC002-T95E-20260601", raw2)
    with pytest.raises(ValueError, match="already exists"):
        rename_specimen_code(db, "FJ-XM-B2-DLC001-T95E-20260601", "DLC002")


def test_rename_not_found_raises_ValueError():
    db = _make_db()
    with pytest.raises(ValueError, match="not found"):
        rename_specimen_code(db, "NONEXISTENT", "DLC002")


def test_rename_records_previous_uid_in_raw_json():
    db = _make_db()
    old_uid = "FJ-XM-B2-DLC001-T95E-20260601"
    raw = {
        "id": "DLC001", "province": "FJ", "site": "XM", "station": "B2",
        "storage": "T95E", "collectionDate": "20260601",
    }
    _insert_specimen(db, old_uid, raw)
    new_uid = rename_specimen_code(db, old_uid, "DLC002")
    row = db.execute("SELECT raw_json FROM specimens WHERE uid=?", (new_uid,)).fetchone()
    assert row is not None
    saved = json.loads(row["raw_json"])
    assert old_uid in saved.get("previousUniqueIds", [])


def test_rename_no_change_returns_same_uid():
    db = _make_db()
    old_uid = "FJ-XM-B2-DLC001-T95E-20260601"
    raw = {
        "id": "DLC001", "province": "FJ", "site": "XM", "station": "B2",
        "storage": "T95E", "collectionDate": "20260601",
    }
    _insert_specimen(db, old_uid, raw)
    result = rename_specimen_code(db, old_uid, "DLC001")
    assert result == old_uid


# ── apply_storage_correction ──────────────────────────────────────────────────

def test_apply_storage_correction_updates_uid():
    db = _make_db()
    old_uid = "FJ-XM-B2-DLC001-T95E-20260601"
    raw = {
        "id": "DLC001", "province": "FJ", "site": "XM", "station": "B2",
        "storage": "T95E", "collectionDate": "20260601",
    }
    _insert_specimen(db, old_uid, raw)
    new_uid = apply_storage_correction(db, old_uid, "D95E")
    assert new_uid == "FJ-XM-B2-DLC001-D95E-20260601"
    assert db.execute("SELECT 1 FROM specimens WHERE uid=?", (new_uid,)).fetchone()
    assert not db.execute("SELECT 1 FROM specimens WHERE uid=?", (old_uid,)).fetchone()


def test_apply_storage_correction_records_previous_uid():
    db = _make_db()
    old_uid = "FJ-XM-B2-DLC001-T95E-20260601"
    raw = {
        "id": "DLC001", "province": "FJ", "site": "XM", "station": "B2",
        "storage": "T95E", "collectionDate": "20260601",
    }
    _insert_specimen(db, old_uid, raw)
    new_uid = apply_storage_correction(db, old_uid, "D95E")
    row = db.execute("SELECT raw_json FROM specimens WHERE uid=?", (new_uid,)).fetchone()
    saved = json.loads(row["raw_json"])
    assert old_uid in saved.get("previousUniqueIds", [])


def test_apply_storage_correction_same_storage_returns_uid():
    db = _make_db()
    old_uid = "FJ-XM-B2-DLC001-T95E-20260601"
    raw = {
        "id": "DLC001", "province": "FJ", "site": "XM", "station": "B2",
        "storage": "T95E", "collectionDate": "20260601",
    }
    _insert_specimen(db, old_uid, raw)
    result = apply_storage_correction(db, old_uid, "T95E")
    assert result == old_uid


def test_apply_storage_correction_collision_raises():
    db = _make_db()
    raw1 = {
        "id": "DLC001", "province": "FJ", "site": "XM", "station": "B2",
        "storage": "T95E", "collectionDate": "20260601",
    }
    raw2 = {
        "id": "DLC001", "province": "FJ", "site": "XM", "station": "B2",
        "storage": "D95E", "collectionDate": "20260601",
    }
    _insert_specimen(db, "FJ-XM-B2-DLC001-T95E-20260601", raw1)
    _insert_specimen(db, "FJ-XM-B2-DLC001-D95E-20260601", raw2)
    with pytest.raises(ValueError, match="conflict"):
        apply_storage_correction(db, "FJ-XM-B2-DLC001-T95E-20260601", "D95E")


def test_apply_storage_correction_migrates_tasks():
    db = _make_db()
    old_uid = "FJ-XM-B2-DLC001-T95E-20260601"
    raw = {
        "id": "DLC001", "province": "FJ", "site": "XM", "station": "B2",
        "storage": "T95E", "collectionDate": "20260601",
    }
    _insert_specimen(db, old_uid, raw)
    db.execute("INSERT INTO tasks (uid) VALUES (?)", (old_uid,))
    db.commit()
    new_uid = apply_storage_correction(db, old_uid, "D95E")
    assert db.execute("SELECT 1 FROM tasks WHERE uid=?", (new_uid,)).fetchone()
    assert not db.execute("SELECT 1 FROM tasks WHERE uid=?", (old_uid,)).fetchone()


def test_apply_storage_correction_not_found_raises():
    db = _make_db()
    with pytest.raises(ValueError, match="not found"):
        apply_storage_correction(db, "NONEXISTENT", "D95E")
