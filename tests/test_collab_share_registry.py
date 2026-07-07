"""Tests for local project share registry."""
from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from PyQt6.QtCore import QSettings

from app.config.settings import _APP, _ORG
from app.services.collab_share_registry import (
    build_shared_projects_payload,
    apply_project_sync_code_to_directory,
    list_local_share_candidates,
    load_shared_dirs,
    project_id_for_directory,
    read_project_id_for_directory,
    save_shared_dirs,
)
from app.services.project_identity_service import (
    ensure_project_identity,
    project_sync_code,
    read_project_identity,
)
from app.db.db_manager import close_project_db, open_project_db, open_project_db_private


def _make_workspace(tmp_path: Path, name: str) -> str:
    ws = tmp_path / name
    (ws / "_data").mkdir(parents=True)
    db = open_project_db(str(ws), create=True)
    try:
        ensure_project_identity(db, project_name=name)
    finally:
        db.close()
    return str(ws.resolve())


def _make_workspace_without_identity(tmp_path: Path, name: str) -> str:
    ws = tmp_path / name
    (ws / "_data").mkdir(parents=True)
    db = open_project_db(str(ws), create=True)
    close_project_db(str(ws))
    return str(ws.resolve())


def test_save_and_load_shared_dirs():
    qs = QSettings(_ORG, _APP)
    key = "collab/shared_project_dirs"
    old = qs.value(key, "", type=str)
    try:
        save_shared_dirs(qs, [r"N:\proj_a", r"N:\proj_b"])
        loaded = load_shared_dirs(qs)
        assert len(loaded) == 2
    finally:
        qs.setValue(key, old)


def test_list_local_share_candidates(tmp_path, monkeypatch):
    a = _make_workspace(tmp_path, "proj_a")
    b = _make_workspace(tmp_path, "proj_b")
    monkeypatch.setattr(
        "app.services.project_service.load_user_projects",
        lambda: [
            {"name": "proj_a", "directory": a},
            {"name": "proj_b", "directory": b},
        ],
    )
    names = {p.name for p in list_local_share_candidates()}
    assert names == {"proj_a", "proj_b"}


def test_list_local_share_candidates_is_read_only_for_project_identity(tmp_path, monkeypatch):
    a = _make_workspace_without_identity(tmp_path, "proj_unstamped")
    monkeypatch.setattr(
        "app.services.project_service.load_user_projects",
        lambda: [{"name": "proj_unstamped", "directory": a}],
    )

    candidates = list_local_share_candidates()

    assert len(candidates) == 1
    assert candidates[0].project_id == ""
    assert read_project_id_for_directory(a) == ""
    db = open_project_db_private(str(a))
    try:
        assert read_project_identity(db) == ""
    finally:
        db.close()


def test_bad_workspace_db_does_not_break_share_candidate_listing(tmp_path, monkeypatch):
    bad = tmp_path / "bad_project"
    (bad / "_data").mkdir(parents=True)
    (bad / "_data" / "project.db").write_text("not sqlite", encoding="utf-8")
    bad_path = str(bad.resolve())
    monkeypatch.setattr(
        "app.services.project_service.load_user_projects",
        lambda: [{"name": "bad_project", "directory": bad_path}],
    )

    candidates = list_local_share_candidates()

    assert len(candidates) == 1
    assert candidates[0].name == "bad_project"
    assert candidates[0].project_id == ""
    assert read_project_id_for_directory(bad_path) == ""
    assert project_id_for_directory(bad_path) == ""


def test_build_shared_projects_payload_creates_identity_only_for_selected_project(tmp_path):
    a = _make_workspace_without_identity(tmp_path, "proj_selected")

    payload = build_shared_projects_payload([a])

    assert len(payload) == 1
    assert payload[0]["projectName"] == "proj_selected"
    assert len(payload[0]["projectId"]) == 32
    assert read_project_id_for_directory(a) == payload[0]["projectId"]


def test_apply_project_sync_code_to_directory_binds_selected_workspace(tmp_path):
    a = _make_workspace_without_identity(tmp_path, "proj_join")
    code = project_sync_code("a" * 32, project_name="remote_project")

    applied = apply_project_sync_code_to_directory(a, code)

    assert applied == "a" * 32
    assert read_project_id_for_directory(a) == "a" * 32


def test_build_shared_projects_payload(tmp_path):
    a = _make_workspace(tmp_path, "proj_a")
    payload = build_shared_projects_payload([a])
    assert len(payload) == 1
    assert payload[0]["projectName"] == "proj_a"
    assert len(payload[0]["projectId"]) == 32


def test_node_info_includes_shared_projects(tmp_path):
    from app.services.collab_service import CollabService

    ws = _make_workspace(tmp_path, "proj_share")
    svc = CollabService()
    svc._running = True
    svc._project_dir = ws
    svc._project_id = build_shared_projects_payload([ws])[0]["projectId"]
    svc.set_shared_project_dirs({ws})
    info = svc._node_info()
    assert len(info["sharedProjects"]) == 1
    assert info["sharedProjects"][0]["projectName"] == "proj_share"
