from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

import app.services.compose_workflow_service as compose_workflow_service
from app.services.compose_workflow_service import (
    SelectedComposeTarget,
    assign_selected_jpgs_to_uid,
    create_implicit_group,
    detect_external_tiff_candidate,
    free_compose_output_name,
    pending_tiff_paths,
    persist_composed_group,
    resolve_compose_output_name,
    resolve_external_tiff_jpg_source,
    resolve_implicit_compose_target,
    unoccupied_jpg_paths,
)
from app.services.grouping_service import (
    ADHOC_GROUPING_UID,
    Group,
    add_explicit_unassign,
    get_explicit_unassigns,
    load_grouping,
    save_grouping,
)


UID = "FJ-XM-B2-DLC001-T95E-20260601"
OTHER_UID = "GXFCG-BLW-BZC003-R-20260618"


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
        CREATE TABLE explicit_unassigns (
            path TEXT PRIMARY KEY,
            created_at TEXT
        );
        """
    )
    conn.commit()
    return conn


def test_unoccupied_jpg_paths_filters_existing_group_members(tmp_path):
    db = _db()
    used = tmp_path / "used.jpg"
    free = tmp_path / "free.jpg"
    for path in (used, free):
        path.write_bytes(b"jpg")
    save_grouping(
        db,
        UID,
        [Group(group_index=0, jpg_paths=[str(used)], status="pending")],
        clean_phantoms=False,
    )

    result = unoccupied_jpg_paths(db, UID, [str(used), str(free)])

    assert result == [str(free)]


def test_free_compose_output_name_sanitizes_and_avoids_conflicts(tmp_path):
    incoming = tmp_path / "incoming-jpg"
    incoming.mkdir()

    assert free_compose_output_name(str(incoming), "my:output") == "my_output.tif"

    (incoming / "my_output.tif").write_bytes(b"tiff")
    fallback = free_compose_output_name(str(incoming), "my:output")
    assert fallback == "自由合成-1.tif"


def test_create_implicit_group_appends_pending_group_with_output_name(tmp_path):
    db = _db()
    first = tmp_path / "1.jpg"
    second = tmp_path / "2.jpg"
    for path in (first, second):
        path.write_bytes(b"jpg")
    save_grouping(
        db,
        UID,
        [Group(group_index=0, angle_label="old")],
        clean_phantoms=False,
    )

    result = create_implicit_group(
        db,
        UID,
        [str(first), str(second)],
        output_name="手填输出名",
    )

    assert result.group_index == 1
    grouping = load_grouping(db, UID)
    assert grouping.groups[1].jpg_paths == [str(first), str(second)]
    assert grouping.groups[1].status == "pending"
    assert grouping.groups[1].output_name == "手填输出名"


def test_create_implicit_group_refuses_less_than_two_available_jpgs(tmp_path):
    db = _db()
    one = tmp_path / "1.jpg"
    one.write_bytes(b"jpg")

    result = create_implicit_group(db, UID, [str(one)])

    assert result.group_index is None
    assert result.reason == "not-enough-unoccupied-jpgs"
    assert load_grouping(db, UID).groups == []


def test_resolve_compose_output_name_adhoc_defaults_to_group_sequence(tmp_path):
    db = _db()

    name, seq = resolve_compose_output_name(
        db,
        ADHOC_GROUPING_UID,
        Group(group_index=1),
        str(tmp_path / "results"),
        str(tmp_path / "incoming-jpg"),
    )

    assert (name, seq) == ("2.tif", 2)


def test_resolve_compose_output_name_output_override_wins(tmp_path):
    db = _db()

    name, seq = resolve_compose_output_name(
        db,
        ADHOC_GROUPING_UID,
        Group(group_index=0, output_name="我的X.tiff"),
        str(tmp_path / "results"),
        str(tmp_path / "incoming-jpg"),
    )

    assert (name, seq) == ("我的X.tif", 1)


def test_resolve_compose_output_name_real_uid_advances_after_existing_result(tmp_path):
    db = _db()
    results = tmp_path / "results"
    incoming = tmp_path / "incoming-jpg"
    results.mkdir()
    incoming.mkdir()
    (results / "FJ-XM-B2-DLC001-1-T95E-20260601.tif").write_bytes(b"tiff")

    name, seq = resolve_compose_output_name(
        db,
        UID,
        Group(group_index=0),
        str(results),
        str(incoming),
    )

    assert name == "FJ-XM-B2-DLC001-2-T95E-20260601.tif"
    assert seq == 2


def test_persist_composed_group_updates_group_and_sequence_hint(tmp_path):
    db = _db()
    tiff = tmp_path / "result.tif"
    tiff.write_bytes(b"tiff")
    grouping = load_grouping(db, UID)
    grouping.groups = [
        Group(group_index=0, jpg_paths=["old-a.jpg", "old-b.jpg"], status="pending")
    ]

    result = persist_composed_group(
        db,
        UID,
        grouping,
        0,
        tiff_path=str(tiff),
        result_sequence=3,
        jpg_paths=["new-a.jpg", "new-b.jpg"],
    )

    assert result.uid == UID
    assert result.group_index == 0
    saved = load_grouping(db, UID).groups[0]
    assert saved.composed_tiff_path == str(tiff)
    assert saved.status == "composed"
    assert saved.result_sequence == 3
    assert saved.jpg_paths == ["new-a.jpg", "new-b.jpg"]
    assert saved.updated_at
    row = db.execute(
        "SELECT next_result_sequence_hint FROM tasks WHERE uid=?",
        (UID,),
    ).fetchone()
    assert row[0] == 4


def test_persist_composed_group_preserves_groups_added_while_compose_runs(tmp_path):
    db = _db()
    tiff = tmp_path / "result.tif"
    tiff.write_bytes(b"tiff")
    save_grouping(
        db,
        UID,
        [Group(group_index=0, jpg_paths=["a.jpg", "b.jpg"], status="pending")],
        clean_phantoms=False,
    )
    stale_grouping = load_grouping(db, UID)
    save_grouping(
        db,
        UID,
        [
            Group(group_index=0, jpg_paths=["a.jpg", "b.jpg"], status="pending"),
            Group(group_index=1, jpg_paths=["c.jpg", "d.jpg"], status="pending"),
        ],
        clean_phantoms=False,
    )

    result = persist_composed_group(
        db,
        UID,
        stale_grouping,
        0,
        tiff_path=str(tiff),
        result_sequence=1,
    )

    saved = {group.group_index: group for group in load_grouping(db, UID).groups}
    assert saved[0].status == "composed"
    assert saved[0].composed_tiff_path == str(tiff)
    assert saved[1].status == "pending"
    assert saved[1].jpg_paths == ["c.jpg", "d.jpg"]
    assert len(result.grouping.groups) == 2


def test_persist_composed_group_raises_when_output_missing(tmp_path):
    db = _db()
    grouping = load_grouping(db, UID)
    grouping.groups = [Group(group_index=0, status="pending")]

    try:
        persist_composed_group(
            db,
            UID,
            grouping,
            0,
            tiff_path=str(tmp_path / "missing.tif"),
            result_sequence=1,
        )
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected FileNotFoundError")

    assert load_grouping(db, UID).groups == []


def test_persist_composed_group_raises_when_group_missing(tmp_path):
    db = _db()
    tiff = tmp_path / "result.tif"
    tiff.write_bytes(b"tiff")
    grouping = load_grouping(db, UID)

    try:
        persist_composed_group(
            db,
            UID,
            grouping,
            3,
            tiff_path=str(tiff),
            result_sequence=1,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_persist_composed_group_rolls_back_memory_when_save_fails(
    tmp_path,
    monkeypatch,
):
    db = _db()
    tiff = tmp_path / "result.tif"
    tiff.write_bytes(b"tiff")
    group = Group(
        group_index=0,
        jpg_paths=["old-a.jpg"],
        composed_tiff_path="old.tif",
        status="pending",
        result_sequence=1,
    )
    group.updated_at = "old-time"
    grouping = load_grouping(db, UID)
    grouping.groups = [group]

    def _raise_save(*_args, **_kwargs):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(compose_workflow_service, "save_grouping", _raise_save)

    with pytest.raises(sqlite3.OperationalError):
        persist_composed_group(
            db,
            UID,
            grouping,
            0,
            tiff_path=str(tiff),
            result_sequence=3,
            jpg_paths=["new-a.jpg"],
        )

    assert group.jpg_paths == ["old-a.jpg"]
    assert group.composed_tiff_path == "old.tif"
    assert group.status == "pending"
    assert group.result_sequence == 1
    assert group.updated_at == "old-time"
    assert load_grouping(db, UID).groups == []


def test_assign_selected_jpgs_to_uid_writes_assignment_and_removes_old_grouping(tmp_path):
    db = _db()
    project_dir = tmp_path / "project"
    incoming = project_dir / "incoming-jpg"
    incoming.mkdir(parents=True)
    jpg = incoming / "selected.jpg"
    jpg.write_bytes(b"jpg")
    save_grouping(
        db,
        OTHER_UID,
        [Group(group_index=0, jpg_paths=[str(jpg)], status="pending")],
        clean_phantoms=False,
    )
    add_explicit_unassign(db, str(jpg))

    count = assign_selected_jpgs_to_uid(str(project_dir), db, UID, [str(jpg)])

    assert count == 1
    assert load_grouping(db, OTHER_UID).groups[0].jpg_paths == []
    assert str(jpg.resolve()) not in get_explicit_unassigns(db)
    state = json.loads((project_dir / "_data" / "state.json").read_text(encoding="utf-8"))
    assert state["events"][-1]["source"] == "manual-assign"
    assert state["events"][-1]["specimenUniqueId"] == UID
    assert state["events"][-1]["jpgPaths"] == [str(jpg.resolve())]


def test_resolve_target_selected_jpgs_use_active_uid_without_prompt():
    prompts = []

    target = resolve_implicit_compose_target(
        ["a.jpg", "b.jpg"],
        UID,
        auto_archive_enabled=False,
        selected_owner_uids=[OTHER_UID],
        prompt_target=lambda count, default_uid: prompts.append((count, default_uid)),
    )

    assert target == SelectedComposeTarget(uid=UID, assign_to_uid=True)
    assert prompts == []


def test_resolve_target_selected_jpgs_without_active_prompts_with_single_owner_hint():
    prompts = []

    def _prompt(count: int, default_uid: str):
        prompts.append((count, default_uid))
        return SelectedComposeTarget(uid=default_uid, assign_to_uid=True)

    target = resolve_implicit_compose_target(
        ["a.jpg", "b.jpg"],
        None,
        auto_archive_enabled=True,
        selected_owner_uids=[OTHER_UID],
        prompt_target=_prompt,
    )

    assert target == SelectedComposeTarget(uid=OTHER_UID, assign_to_uid=True)
    assert prompts == [(2, OTHER_UID)]


def test_resolve_target_no_selection_requires_active_uid_and_auto_archive():
    prompt = lambda _count, _default_uid: SelectedComposeTarget(uid="unexpected")

    assert resolve_implicit_compose_target(
        [],
        UID,
        auto_archive_enabled=True,
        selected_owner_uids=[],
        prompt_target=prompt,
    ) == SelectedComposeTarget(uid=UID, assign_to_uid=False)

    assert resolve_implicit_compose_target(
        [],
        UID,
        auto_archive_enabled=False,
        selected_owner_uids=[],
        prompt_target=prompt,
    ) is None

    assert resolve_implicit_compose_target(
        [],
        None,
        auto_archive_enabled=True,
        selected_owner_uids=[],
        prompt_target=prompt,
    ) is None


def test_pending_tiff_paths_ignores_entries_that_already_have_zip(tmp_path):
    pending = tmp_path / "pending.tif"
    done = tmp_path / "done.tif"
    pending.write_bytes(b"tiff")
    done.write_bytes(b"tiff")
    scan_result = SimpleNamespace(
        tiff_files=[
            SimpleNamespace(path=str(pending), has_zip=False),
            SimpleNamespace(path=str(done), has_zip=True),
        ],
    )

    assert pending_tiff_paths(scan_result) == {str(pending.resolve())}


def test_detect_external_tiff_candidate_disabled_returns_current_paths(tmp_path):
    current = [str(tmp_path / "new.tif")]

    candidate = detect_external_tiff_candidate(
        enabled=False,
        current_tiff_paths=current,
        known_tiff_paths=[],
        busy=False,
    )

    assert candidate.reason == "disabled"
    assert candidate.should_seed_known_tiffs is True
    assert candidate.current_tiff_paths == (str((tmp_path / "new.tif").resolve()),)


def test_detect_external_tiff_candidate_picks_first_unknown_tiff(tmp_path):
    known = tmp_path / "known.tif"
    first = tmp_path / "a-new.tif"
    second = tmp_path / "b-new.tif"

    candidate = detect_external_tiff_candidate(
        enabled=True,
        current_tiff_paths=[str(second), str(known), str(first)],
        known_tiff_paths=[str(known)],
        busy=False,
    )

    assert candidate.needs_jpg_source is True
    assert candidate.new_tiff_paths == (
        str(first.resolve()),
        str(second.resolve()),
    )
    assert candidate.target_tiff == str(first.resolve())


def test_detect_external_tiff_candidate_busy_defers_source_selection(tmp_path):
    candidate = detect_external_tiff_candidate(
        enabled=True,
        current_tiff_paths=[str(tmp_path / "new.tif")],
        known_tiff_paths=[],
        busy=True,
    )

    assert candidate.reason == "busy"
    assert candidate.needs_jpg_source is False


def test_resolve_external_tiff_jpg_source_prefers_active_uid_paths():
    source = resolve_external_tiff_jpg_source(
        active_uid=UID,
        active_uid_jpg_paths=["active-a.jpg"],
        selected_jpg_paths=["selected-a.jpg"],
    )

    assert source.ready is True
    assert source.source == "active_uid"
    assert source.jpg_paths == ("active-a.jpg",)


def test_resolve_external_tiff_jpg_source_without_active_requires_selection():
    assert resolve_external_tiff_jpg_source(
        active_uid=None,
        selected_jpg_paths=[],
    ).reason == "no-selected-jpgs"

    source = resolve_external_tiff_jpg_source(
        active_uid=None,
        selected_jpg_paths=["selected-a.jpg"],
    )

    assert source.ready is True
    assert source.source == "selection"
    assert source.jpg_paths == ("selected-a.jpg",)
