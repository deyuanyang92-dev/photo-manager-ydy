from __future__ import annotations

import sqlite3

import pytest

import app.services.organize_workflow_service as organize_workflow_service
from app.services.grouping_service import (
    ADHOC_GROUPING_UID,
    Group,
    SpecimenGrouping,
    load_grouping,
    save_grouping,
)
from app.services.organize_workflow_service import (
    compose_batch_queue,
    inspect_organize_group,
    organize_batch_targets,
    plan_archive_worker,
    plan_organize_gate_check,
    prepare_existing_tiff_group,
    resolve_group_jpg_paths,
)


UID = "FJ-XM-B2-DLC001-T95E-20260601"


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
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
        """
    )
    conn.commit()
    return conn


def test_inspect_organize_group_reports_missing_group():
    grouping = SpecimenGrouping(uid=UID, groups=[])

    state = inspect_organize_group(grouping, 0)

    assert state.reason == "missing-group"
    assert state.ready is False


def test_inspect_organize_group_reports_missing_tiff():
    group = Group(group_index=0, jpg_paths=["a.jpg", "b.jpg"])
    grouping = SpecimenGrouping(uid=UID, groups=[group])

    state = inspect_organize_group(grouping, 0)

    assert state.reason == "missing-tiff"
    assert state.group is group


def test_resolve_group_jpg_paths_reports_missing(monkeypatch):
    monkeypatch.setattr(
        "app.services.helicon_service.resolve_existing_image_path",
        lambda p: p if p == "ok.jpg" else None,
    )

    resolved, missing = resolve_group_jpg_paths(["ok.jpg", "gone.jpg"])
    assert resolved == ["ok.jpg"]
    assert missing == ["gone.jpg"]


def test_inspect_organize_group_reports_existing_archive_noop():
    group = Group(
        group_index=0,
        composed_tiff_path="result.tif",
        archive_zip="result.zip",
        status="organized",
    )
    grouping = SpecimenGrouping(uid=UID, groups=[group])

    state = inspect_organize_group(grouping, 0)

    assert state.already_organized is True
    assert state.group is group


def test_inspect_organize_group_reports_tif_only_organized_as_noop():
    group = Group(
        group_index=0,
        composed_tiff_path="external.tif",
        status="organized",
    )
    grouping = SpecimenGrouping(uid=UID, groups=[group])

    state = inspect_organize_group(grouping, 0)

    assert state.already_organized is True


def test_inspect_organize_group_distinguishes_tif_only_from_jpg_archive():
    tif_only = Group(group_index=0, composed_tiff_path="external.tif")
    jpg_group = Group(
        group_index=1,
        composed_tiff_path="composed.tif",
        jpg_paths=["a.jpg"],
    )
    grouping = SpecimenGrouping(uid=UID, groups=[tif_only, jpg_group])

    assert inspect_organize_group(grouping, 0).reason == "ready-tif-only"
    assert inspect_organize_group(grouping, 0).is_tif_only is True
    assert inspect_organize_group(grouping, 1).reason == "ready-jpg-archive"


def test_gate_plan_skips_when_no_project():
    grouping = SpecimenGrouping(uid=UID, groups=[])

    plan = plan_organize_gate_check(
        grouping,
        Group(group_index=0),
        has_project=False,
        allow_single_jpg=False,
        silent_batch=False,
    )

    assert plan.required is False


def test_gate_plan_allows_interactive_single_jpg_with_promptable_gate():
    grouping = SpecimenGrouping(uid=UID, groups=[])

    plan = plan_organize_gate_check(
        grouping,
        Group(group_index=0, composed_tiff_path="external.tif", jpg_paths=["a.jpg"]),
        has_project=True,
        allow_single_jpg=True,
        silent_batch=False,
    )

    assert plan.required is False
    assert plan.skip_gate is True


def test_gate_plan_silent_batch_regular_group_skips_on_gate_error():
    groups = [
        Group(group_index=0, jpg_paths=["a.jpg"]),
        Group(group_index=1, jpg_paths=["b.jpg", "c.jpg"]),
    ]
    grouping = SpecimenGrouping(uid=UID, groups=groups)

    plan = plan_organize_gate_check(
        grouping,
        groups[1],
        has_project=True,
        allow_single_jpg=False,
        silent_batch=True,
    )

    assert plan.required is True
    assert plan.allow_inactive is True
    assert plan.silent_skip_on_error is True
    assert plan.prompt_on_error is False
    assert plan.groups_as_dicts == (
        {"jpgPaths": ["a.jpg"]},
        {"jpgPaths": ["b.jpg", "c.jpg"]},
    )


def test_archive_worker_plan_project_results_and_collision(tmp_path):
    project = tmp_path / "project"
    incoming = project / "incoming-jpg"
    results = project / "results"
    incoming.mkdir(parents=True)
    results.mkdir()
    tiff = incoming / "specimen-1.tif"
    tiff.write_bytes(b"tiff")
    (results / "specimen-1.zip").write_bytes(b"zip")

    plan = plan_archive_worker(
        project_dir=str(project),
        results_subdir="results",
        tiff_path=str(tiff),
        delete_jpg_after_archive=False,
    )

    assert plan.archive_output_dir == str(results)
    assert plan.existing_zip == str(results / "specimen-1.zip")
    assert plan.existing_zip_exists is True
    assert plan.delete_jpg is False


def test_archive_worker_plan_without_project_writes_zip_beside_tiff(tmp_path):
    tiff = tmp_path / "external.tif"
    tiff.write_bytes(b"tiff")

    plan = plan_archive_worker(
        project_dir="",
        results_subdir="results",
        tiff_path=str(tiff),
        delete_jpg_after_archive=True,
    )

    assert plan.archive_output_dir == str(tmp_path)
    assert plan.existing_zip == str(tmp_path / "external.zip")
    assert plan.existing_zip_exists is False
    assert plan.delete_jpg is True


def test_prepare_existing_tiff_group_without_active_keeps_tiff_name_and_uses_adhoc(
    tmp_path,
):
    db = _db()
    project = tmp_path / "project"
    incoming = project / "incoming-jpg"
    incoming.mkdir(parents=True)
    tiff = incoming / "external-name.tif"
    tiff.write_bytes(b"tiff")

    result = prepare_existing_tiff_group(
        db,
        active_uid=None,
        jpg_paths=["a.jpg", "b.jpg"],
        tiff_path=str(tiff),
        project_dir=str(project),
    )

    assert result.uid == ADHOC_GROUPING_UID
    assert result.group_index == 0
    assert result.tiff_path == str(tiff)
    assert result.renamed_tiff is False
    assert tiff.exists()
    group = load_grouping(db, ADHOC_GROUPING_UID).groups[0]
    assert group.jpg_paths == ["a.jpg", "b.jpg"]
    assert group.composed_tiff_path == str(tiff)
    assert group.status == "composed"


def test_prepare_existing_tiff_group_with_active_renames_to_next_uid_result(
    tmp_path,
):
    db = _db()
    project = tmp_path / "project"
    incoming = project / "incoming-jpg"
    results = project / "results"
    incoming.mkdir(parents=True)
    results.mkdir()
    (results / "FJ-XM-B2-DLC001-1-T95E-20260601.tif").write_bytes(b"old")
    tiff = incoming / "helicon-output.tif"
    tiff.write_bytes(b"tiff")

    result = prepare_existing_tiff_group(
        db,
        active_uid=UID,
        jpg_paths=["a.jpg", "b.jpg"],
        tiff_path=str(tiff),
        project_dir=str(project),
    )

    expected = incoming / "FJ-XM-B2-DLC001-2-T95E-20260601.tif"
    assert result.uid == UID
    assert result.group_index == 0
    assert result.tiff_path == str(expected)
    assert result.renamed_tiff is True
    assert expected.exists()
    assert not tiff.exists()
    group = load_grouping(db, UID).groups[0]
    assert group.composed_tiff_path == str(expected)
    assert group.jpg_paths == ["a.jpg", "b.jpg"]


def test_prepare_existing_tiff_group_appends_after_existing_group(tmp_path):
    db = _db()
    project = tmp_path / "project"
    incoming = project / "incoming-jpg"
    incoming.mkdir(parents=True)
    tiff = incoming / "external.tif"
    tiff.write_bytes(b"tiff")
    save_grouping(
        db,
        ADHOC_GROUPING_UID,
        [Group(group_index=0, composed_tiff_path="old.tif")],
        clean_phantoms=False,
    )

    result = prepare_existing_tiff_group(
        db,
        active_uid=None,
        jpg_paths=["new-a.jpg"],
        tiff_path=str(tiff),
        project_dir=str(project),
    )

    assert result.group_index == 1
    assert [g.group_index for g in load_grouping(db, ADHOC_GROUPING_UID).groups] == [0, 1]


def test_prepare_existing_tiff_group_active_missing_tiff_raises(tmp_path):
    db = _db()
    project = tmp_path / "project"
    incoming = project / "incoming-jpg"
    incoming.mkdir(parents=True)

    with pytest.raises(FileNotFoundError):
        prepare_existing_tiff_group(
            db,
            active_uid=UID,
            jpg_paths=["a.jpg"],
            tiff_path=str(incoming / "missing.tif"),
            project_dir=str(project),
        )


def test_prepare_existing_tiff_group_rolls_back_rename_when_save_fails(
    tmp_path,
    monkeypatch,
):
    db = _db()
    project = tmp_path / "project"
    incoming = project / "incoming-jpg"
    incoming.mkdir(parents=True)
    tiff = incoming / "helicon-output.tif"
    tiff.write_bytes(b"tiff")

    def _raise_save(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(organize_workflow_service, "save_grouping", _raise_save)

    with pytest.raises(sqlite3.OperationalError):
        prepare_existing_tiff_group(
            db,
            active_uid=UID,
            jpg_paths=["a.jpg", "b.jpg"],
            tiff_path=str(tiff),
            project_dir=str(project),
        )

    expected_renamed = incoming / "FJ-XM-B2-DLC001-1-T95E-20260601.tif"
    assert tiff.exists()
    assert not expected_renamed.exists()
    assert load_grouping(db, UID).groups == []


def test_compose_batch_queue_returns_uncomposed_groups_and_respects_selection():
    grouping = SpecimenGrouping(
        uid=UID,
        groups=[
            Group(group_index=0, jpg_paths=["a.jpg", "b.jpg"]),
            Group(group_index=1, composed_tiff_path="done.tif"),
            Group(group_index=2, jpg_paths=["c.jpg", "d.jpg"]),
        ],
    )

    assert compose_batch_queue(grouping) == (0, 2)
    assert compose_batch_queue(grouping, selected_group_indexes=[2]) == (2,)
    assert compose_batch_queue(grouping, selected_group_indexes=[1]) == ()


def test_organize_batch_targets_returns_composed_unorganized_groups_only():
    grouping = SpecimenGrouping(
        uid=UID,
        groups=[
            Group(group_index=0, jpg_paths=["a.jpg", "b.jpg"]),
            Group(group_index=1, composed_tiff_path="one.tif", status="composed"),
            Group(group_index=2, composed_tiff_path="two.tif", status="organized"),
            Group(group_index=3, composed_tiff_path="three.tif", status="pending"),
        ],
    )

    assert organize_batch_targets(grouping) == (1, 3)
    assert organize_batch_targets(grouping, selected_group_indexes=[3]) == (3,)
    assert organize_batch_targets(grouping, selected_group_indexes=[0, 2]) == ()
