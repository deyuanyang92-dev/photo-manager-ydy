from __future__ import annotations

import json
import sqlite3
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from app.services.capture_workflow_service import (
    assign_jpg_to_active_specimen,
    delete_specimen,
    finalize_supplementary_archive,
    finalize_archived_group,
    link_result_pair_to_clean_uid,
    move_result_file,
    plan_archive_target,
    persist_grouping_claim,
    prepare_grouping_claim,
    register_tif_only_group,
    result_infos_from_grouping,
    unassign_jpg,
)
from app.services.grouping_service import (
    ADHOC_GROUPING_UID,
    Group,
    get_explicit_unassigns,
    load_grouping,
    save_grouping,
)
from app.utils.path_utils import windows_to_wsl


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE specimens (
            uid TEXT PRIMARY KEY,
            raw_json TEXT
        );
        CREATE TABLE tasks (
            uid TEXT PRIMARY KEY,
            is_active INTEGER DEFAULT 0,
            activated_at TEXT,
            last_organized_at TEXT,
            next_result_sequence_hint INTEGER,
            raw_json TEXT
        );
        CREATE TABLE grouping (
            uid TEXT, group_index INTEGER,
            angle_label TEXT, jpg_paths TEXT, composed_tiff_path TEXT,
            status TEXT, source TEXT, created_at TEXT, updated_at TEXT,
            result_sequence INTEGER, archive_zip TEXT,
            retired_tiff_paths TEXT, raw_json TEXT,
            PRIMARY KEY (uid, group_index)
        );
        CREATE TABLE photo_assignments (
            assignment_id TEXT PRIMARY KEY,
            photo_id TEXT NOT NULL,
            specimen_uid TEXT,
            is_current INTEGER DEFAULT 1
        );
        CREATE TABLE explicit_unassigns (
            path TEXT PRIMARY KEY,
            created_at TEXT
        );
        """
    )
    conn.commit()
    return conn


def test_delete_specimen_removes_local_references_without_disk_rules():
    db = _db()
    uid = "FJ-XM-A01-DLC001-T95E-20260601"
    db.execute("INSERT INTO specimens (uid) VALUES (?)", (uid,))
    db.execute("INSERT INTO tasks (uid, is_active) VALUES (?, 1)", (uid,))
    db.execute("INSERT INTO grouping (uid, group_index) VALUES (?, 0)", (uid,))
    db.execute(
        "INSERT INTO photo_assignments (assignment_id, photo_id, specimen_uid) VALUES ('a1', 'p1', ?)",
        (uid,),
    )
    db.commit()

    result = delete_specimen(db, uid)

    assert result.deleted is True
    assert result.unassigned_photo_rows == 1
    assert not db.execute("SELECT 1 FROM specimens WHERE uid=?", (uid,)).fetchone()
    assert not db.execute("SELECT 1 FROM tasks WHERE uid=?", (uid,)).fetchone()
    assert not db.execute("SELECT 1 FROM grouping WHERE uid=?", (uid,)).fetchone()
    row = db.execute("SELECT specimen_uid FROM photo_assignments WHERE assignment_id='a1'").fetchone()
    assert row["specimen_uid"] is None


def test_grouping_claim_moves_adhoc_groups_after_existing_target_groups():
    db = _db()
    uid = "FJ-XM-A01-DLC001-T95E-20260601"
    save_grouping(db, uid, [Group(group_index=0, angle_label="old")], clean_phantoms=False)
    save_grouping(
        db,
        ADHOC_GROUPING_UID,
        [Group(group_index=0, angle_label="new", jpg_paths=["incoming-jpg/img001.jpg"]), Group(group_index=1)],
        clean_phantoms=False,
    )
    adhoc_groups = load_grouping(db, ADHOC_GROUPING_UID).groups

    plan = prepare_grouping_claim(db, uid, ADHOC_GROUPING_UID, adhoc_groups)
    persist_grouping_claim(db, plan)

    assert plan.claimed is True
    grouping = load_grouping(db, uid)
    assert [(g.group_index, g.angle_label) for g in grouping.groups] == [
        (0, "old"),
        (1, "new"),
    ]
    assert grouping.groups[1].jpg_paths == ["incoming-jpg/img001.jpg"]
    assert load_grouping(db, ADHOC_GROUPING_UID).groups == []


def test_assign_jpg_to_active_specimen_records_event_and_removes_unassign(tmp_path):
    db = _db()
    project_dir = tmp_path / "project"
    incoming = project_dir / "incoming-jpg"
    incoming.mkdir(parents=True)
    jpg = incoming / "img001.jpg"
    jpg.write_bytes(b"jpg")
    uid = "FJ-XM-A01-DLC001-T95E-20260601"
    db.execute("INSERT INTO tasks (uid, is_active) VALUES (?, 1)", (uid,))
    db.commit()
    assert unassign_jpg(db, str(jpg)) is True
    assert str(jpg.resolve()) in get_explicit_unassigns(db)

    result = assign_jpg_to_active_specimen(str(project_dir), db, str(jpg))

    assert result.active_uid == uid
    assert result.assigned_count == 1
    assert str(jpg.resolve()) not in get_explicit_unassigns(db)
    state = json.loads((project_dir / "_data" / "state.json").read_text(encoding="utf-8"))
    assert state["events"][-1]["source"] == "manual-assign"
    assert state["events"][-1]["specimenUniqueId"] == uid


def test_assign_jpg_without_active_specimen_is_noop(tmp_path):
    db = _db()
    project_dir = tmp_path / "project"
    jpg = project_dir / "img001.jpg"
    jpg.parent.mkdir(parents=True)
    jpg.write_bytes(b"jpg")

    result = assign_jpg_to_active_specimen(str(project_dir), db, str(jpg))

    assert result.active_uid is None
    assert not (project_dir / "_data" / "state.json").exists()


def test_result_infos_from_grouping_infers_zip_next_to_tiff(tmp_path):
    uid = "FJ-XM-A01-DLC001-T95E-20260601"
    tiff = tmp_path / "result-1.tif"
    zip_path = tmp_path / "result-1.zip"
    tiff.write_bytes(b"tiff")
    zip_path.write_bytes(b"zip-data")
    grouping = load_grouping(_db(), uid)
    grouping.groups = [
        Group(
            group_index=2,
            composed_tiff_path=str(tiff),
            result_sequence=7,
        )
    ]

    tiffs, zips = result_infos_from_grouping(grouping)

    assert tiffs == [{
        "path": str(tiff),
        "name": "result-1.tif",
        "seq": 7,
        "owner_uid": uid,
        "group_index": 2,
        "registered": True,
    }]
    assert zips == [{
        "path": str(zip_path),
        "name": "result-1.zip",
        "size": len(b"zip-data"),
        "seq": 7,
        "owner_uid": uid,
        "group_index": 2,
        "registered": False,
    }]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path localization")
def test_result_infos_from_grouping_localizes_wsl_result_paths(tmp_path):
    uid = "FJ-XM-A01-DLC001-T95E-20260601"
    tiff = tmp_path / "result-wsl.tif"
    zip_path = tmp_path / "result-wsl.zip"
    tiff.write_bytes(b"tiff")
    zip_path.write_bytes(b"zip-data")
    wsl_tiff = windows_to_wsl(str(tiff))
    assert wsl_tiff
    grouping = load_grouping(_db(), uid)
    grouping.groups = [
        Group(
            group_index=2,
            composed_tiff_path=wsl_tiff,
            result_sequence=7,
        )
    ]

    tiffs, zips = result_infos_from_grouping(grouping)

    assert tiffs[0]["path"] == str(tiff)
    assert tiffs[0]["name"] == tiff.name
    assert zips[0]["path"] == str(zip_path)
    assert zips[0]["name"] == zip_path.name


def test_move_result_file_avoids_overwriting_existing_result(tmp_path):
    src = tmp_path / "incoming" / "result.tif"
    out = tmp_path / "results"
    src.parent.mkdir()
    out.mkdir()
    src.write_bytes(b"new")
    (out / "result.tif").write_bytes(b"existing")

    moved = move_result_file(str(src), out)

    assert moved == str(out / "result_1.tif")
    assert (out / "result.tif").read_bytes() == b"existing"
    assert (out / "result_1.tif").read_bytes() == b"new"
    assert not src.exists()


def test_move_result_file_refuses_to_replace_directory(tmp_path):
    src = tmp_path / "incoming" / "result.tif"
    out = tmp_path / "results"
    src.parent.mkdir()
    (out / "result.tif").mkdir(parents=True)
    src.write_bytes(b"new")

    with pytest.raises(IsADirectoryError):
        move_result_file(str(src), out, replace_existing=True)

    assert src.exists()


def test_register_tif_only_group_moves_to_results_and_persists(tmp_path):
    db = _db()
    uid = "FJ-XM-A01-DLC001-T95E-20260601"
    project = tmp_path / "project"
    incoming = project / "incoming-jpg"
    incoming.mkdir(parents=True)
    tiff = incoming / "external.tif"
    tiff.write_bytes(b"not-a-real-tiff")
    grouping = load_grouping(db, uid)
    grouping.groups = [Group(group_index=0, composed_tiff_path=str(tiff))]

    result = register_tif_only_group(
        db,
        uid,
        grouping,
        grouping.groups[0],
        project_dir=str(project),
        results_subdir="results",
    )

    moved_tiff = project / "results" / "external.tif"
    assert result.tiff_path == str(moved_tiff)
    assert moved_tiff.exists()
    saved = load_grouping(db, uid).groups[0]
    assert saved.status == "organized"
    assert saved.composed_tiff_path == str(moved_tiff)
    assert saved.archive_zip is None


def test_finalize_archived_group_moves_tiff_zip_and_persists(tmp_path):
    db = _db()
    uid = "FJ-XM-A01-DLC001-T95E-20260601"
    project = tmp_path / "project"
    incoming = project / "incoming-jpg"
    results = project / "results"
    incoming.mkdir(parents=True)
    results.mkdir()
    tiff = incoming / "specimen-1.tif"
    zip_path = incoming / "temporary.zip"
    tiff.write_bytes(b"not-a-real-tiff")
    zip_path.write_bytes(b"zip")
    grouping = load_grouping(db, uid)
    grouping.groups = [
        Group(
            group_index=0,
            jpg_paths=[str(incoming / "a.jpg")],
            composed_tiff_path=str(tiff),
        )
    ]

    target = plan_archive_target(str(project), "results", str(tiff))
    result = finalize_archived_group(
        db,
        uid,
        grouping,
        grouping.groups[0],
        SimpleNamespace(zip_path=str(zip_path)),
        archive_output_dir=target.output_dir,
        project_dir=str(project),
    )

    moved_tiff = results / "specimen-1.tif"
    moved_zip = results / "specimen-1.zip"
    assert result.tiff_path == str(moved_tiff)
    assert result.archive_zip == str(moved_zip)
    assert moved_tiff.exists()
    assert moved_zip.exists()
    saved = load_grouping(db, uid).groups[0]
    assert saved.status == "organized"
    assert saved.composed_tiff_path == str(moved_tiff)
    assert saved.archive_zip == str(moved_zip)


def test_finalize_archived_group_requires_worker_zip(tmp_path):
    db = _db()
    uid = "FJ-XM-A01-DLC001-T95E-20260601"
    project = tmp_path / "project"
    incoming = project / "incoming-jpg"
    incoming.mkdir(parents=True)
    tiff = incoming / "specimen-1.tif"
    tiff.write_bytes(b"not-a-real-tiff")
    grouping = load_grouping(db, uid)
    grouping.groups = [
        Group(group_index=0, composed_tiff_path=str(tiff), jpg_paths=["a.jpg"])
    ]

    target = plan_archive_target(str(project), "results", str(tiff))
    with pytest.raises(FileNotFoundError):
        finalize_archived_group(
            db,
            uid,
            grouping,
            grouping.groups[0],
            SimpleNamespace(zip_path=str(incoming / "missing.zip")),
            archive_output_dir=target.output_dir,
            project_dir=str(project),
        )

    assert load_grouping(db, uid).groups == []
    assert tiff.exists()


def test_link_result_pair_to_clean_uid_removes_old_owner_and_registers_target(tmp_path):
    db = _db()
    old_uid = "FJ-XM-A01-DLC001-T95E-20260601"
    target_uid = "FJ-XM-A02-DLC002-T95E-20260601"
    tiff = tmp_path / "linked.tif"
    zip_path = tmp_path / "linked.zip"
    tiff.write_bytes(b"tiff")
    zip_path.write_bytes(b"zip")
    save_grouping(
        db,
        old_uid,
        [
            Group(
                group_index=0,
                composed_tiff_path=str(tiff),
                archive_zip=str(zip_path),
                status="organized",
            )
        ],
        clean_phantoms=False,
    )

    linked = link_result_pair_to_clean_uid(db, target_uid, str(tiff), str(zip_path))

    assert linked.uid == target_uid
    assert linked.removed_from == [old_uid]
    assert load_grouping(db, old_uid).groups == []
    target = load_grouping(db, target_uid).groups
    assert len(target) == 1
    assert target[0].composed_tiff_path == str(tiff.resolve())
    assert target[0].archive_zip == str(zip_path.resolve())
    assert target[0].source == "linked-existing-result"


def test_finalize_supplementary_archive_replaces_existing_outputs(tmp_path):
    project = tmp_path / "project"
    incoming = project / "incoming-jpg"
    results = project / "results"
    incoming.mkdir(parents=True)
    results.mkdir()
    tiff = incoming / "bundle.tif"
    zip_path = incoming / "bundle.zip"
    tiff.write_bytes(b"new-tiff")
    zip_path.write_bytes(b"new-zip")
    (results / "bundle.tif").write_bytes(b"old-tiff")
    (results / "bundle.zip").write_bytes(b"old-zip")

    finalized = finalize_supplementary_archive(
        SimpleNamespace(zip_path=str(zip_path)),
        SimpleNamespace(tiff_path=str(tiff), uid="U1"),
        project_dir=str(project),
        results_subdir="results",
    )

    assert finalized.uid == "U1"
    assert finalized.tiff_path == str(results / "bundle.tif")
    assert finalized.zip_path == str(results / "bundle.zip")
    assert (results / "bundle.tif").read_bytes() == b"new-tiff"
    assert (results / "bundle.zip").read_bytes() == b"new-zip"


def test_finalize_supplementary_archive_requires_worker_zip(tmp_path):
    project = tmp_path / "project"
    incoming = project / "incoming-jpg"
    incoming.mkdir(parents=True)
    tiff = incoming / "bundle.tif"
    tiff.write_bytes(b"new-tiff")

    with pytest.raises(FileNotFoundError):
        finalize_supplementary_archive(
            SimpleNamespace(zip_path=str(incoming / "missing.zip")),
            SimpleNamespace(tiff_path=str(tiff), uid="U1"),
            project_dir=str(project),
            results_subdir="results",
        )

    assert tiff.exists()
