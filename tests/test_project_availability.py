"""test_project_availability.py — RED-LINE: never fabricate a project on a
missing volume.

The bug being locked out: a project dir on an unmounted drive must NOT be
recreated as an empty ghost (with an empty project.db) when the app merely
*reads* it. Reads must refuse; data on the (absent) real volume stays the only
copy. See app/services/project_paths.py for the rationale.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.db import db_manager
from app.services import project_service
from app.services.project_paths import (
    ProjectUnavailableError,
    project_root_available,
    require_project_root,
)


@pytest.fixture(autouse=True)
def _clean_db_cache():
    db_manager.close_all()
    yield
    db_manager.close_all()


def _missing(tmp_path):
    """A project dir whose volume/root does NOT exist (simulates unmounted)."""
    return str(tmp_path / "media" / "GONE-UUID" / "proj" / "11")


# ── gate primitives ─────────────────────────────────────────────────────────

def test_root_available_false_for_missing(tmp_path):
    assert project_root_available(_missing(tmp_path)) is False
    assert project_root_available(None) is False
    assert project_root_available(str(tmp_path)) is True


def test_require_root_raises_on_missing(tmp_path):
    with pytest.raises(ProjectUnavailableError):
        require_project_root(_missing(tmp_path))


# ── db layer: reads must never fabricate ─────────────────────────────────────

def test_open_db_read_refuses_and_creates_nothing(tmp_path):
    missing = _missing(tmp_path)
    with pytest.raises(ProjectUnavailableError):
        db_manager.open_project_db(missing)  # create=False default
    # The smoking gun the old code left: a fabricated tree + empty db.
    from pathlib import Path
    assert not Path(missing).exists(), "read fabricated the project tree!"


def test_get_db_read_refuses_existing_dir_without_db(tmp_path):
    """An existing folder that is NOT a workspace (no project.db) must not be
    silently turned into one by a read."""
    p = tmp_path / "emptyfolder"
    p.mkdir()
    with pytest.raises(ProjectUnavailableError):
        db_manager.open_project_db(str(p))
    assert not (p / "_data" / "project.db").exists()


# ── create path still works (deliberate) ─────────────────────────────────────

def test_create_then_open_roundtrips(tmp_path):
    proj = tmp_path / "newproj"
    project_service.create_project("新项目", str(proj))
    # create_project must materialize a usable db
    db = db_manager.open_project_db(str(proj))  # now read-opens fine
    assert isinstance(db, sqlite3.Connection)
    assert (proj / "_data" / "project.db").exists()


def test_create_refuses_when_parent_volume_missing(tmp_path):
    missing = _missing(tmp_path)
    with pytest.raises(ProjectUnavailableError):
        project_service.create_project("x", missing)
