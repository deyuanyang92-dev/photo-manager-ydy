"""Tests for global specimen UID lookup across workspaces."""
from pathlib import Path

import pytest

from app.db import db_manager
from app.services import specimen_catalog_service as catalog


@pytest.fixture(autouse=True)
def _clean_db_cache():
    db_manager.close_all()
    yield
    db_manager.close_all()


def _workspace(root: Path, name: str):
    path = root / name
    path.mkdir(parents=True)
    db = db_manager.open_project_db(str(path), create=True)
    return path, db


def test_find_uid_scans_project_root_workspaces(tmp_path):
    root = tmp_path / "survey"
    root.mkdir()
    ws_a, db_a = _workspace(root, "断面A")
    ws_b, _db_b = _workspace(root, "断面B")
    uid = "FJ-XM-B2-DLC001-T95E-20260601"
    db_a.execute(
        "INSERT INTO specimens (uid, scientific_name, owner_project_dir) VALUES (?, ?, ?)",
        (uid, "Marphysa sp.", str(ws_a)),
    )
    db_a.commit()

    hits = catalog.find_uid(
        uid,
        current_project_dir=str(ws_b),
        current_project_root=str(root),
    )

    assert len(hits) == 1
    assert hits[0].project_dir == str(ws_a.resolve())
    assert hits[0].display_project == "断面A"


def test_conflicting_uid_hits_allows_same_current_specimen(tmp_path):
    root = tmp_path / "survey"
    root.mkdir()
    ws, db = _workspace(root, "断面A")
    uid = "FJ-XM-B2-DLC001-T95E-20260601"
    db.execute(
        "INSERT INTO specimens (uid, owner_project_dir) VALUES (?, ?)",
        (uid, str(ws)),
    )
    db.commit()

    hits = catalog.conflicting_uid_hits(
        uid,
        current_project_dir=str(ws),
        current_project_root=str(root),
        allowed_current_uid=uid,
    )

    assert hits == []
