from pathlib import Path

import pytest
from PIL import Image

from app.db import db_manager


@pytest.fixture(autouse=True)
def close_dbs():
    db_manager.close_all()
    yield
    db_manager.close_all()


def _project(tmp_path):
    project = tmp_path / "proj"
    incoming = project / "incoming-jpg"
    incoming.mkdir(parents=True)
    db = db_manager.open_project_db(str(project), create=True)
    return project, incoming, db


def _jpg(path: Path, size=(32, 18)):
    Image.new("RGB", size, (200, 20, 20)).save(path, "JPEG")


def test_upsert_photo_file_records_file_and_metadata(tmp_path):
    from app.services.photo_asset_service import upsert_photo_file

    project, incoming, db = _project(tmp_path)
    img = incoming / "IMG_0001.JPG"
    _jpg(img, size=(40, 25))

    row = upsert_photo_file(db, str(project), str(img), compute_hash=True)

    assert row["original_filename"] == "IMG_0001.JPG"
    assert row["relative_path"] == "incoming-jpg/IMG_0001.JPG"
    assert row["sha256"]
    assert row["width"] == 40
    assert row["height"] == 25
    meta = db.execute("SELECT * FROM photo_metadata WHERE photo_id=?", (row["photo_id"],)).fetchone()
    assert meta["image_width"] == 40
    assert meta["image_height"] == 25


def test_upsert_same_path_updates_not_duplicates(tmp_path):
    from app.services.photo_asset_service import upsert_photo_file

    project, incoming, db = _project(tmp_path)
    img = incoming / "IMG_0002.JPG"
    _jpg(img)

    a = upsert_photo_file(db, str(project), str(img), compute_hash=False)
    b = upsert_photo_file(db, str(project), str(img), compute_hash=False)

    assert a["photo_id"] == b["photo_id"]
    assert db.execute("SELECT COUNT(*) FROM photos").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM photo_files").fetchone()[0] == 1


def test_assignment_history_keeps_current_only(tmp_path):
    from app.services.photo_asset_service import assign_photo_to_specimen, upsert_photo_file

    project, incoming, db = _project(tmp_path)
    img = incoming / "IMG_0003.JPG"
    _jpg(img)
    row = upsert_photo_file(db, str(project), str(img), specimen_uid="UID-1")

    assign_photo_to_specimen(db, row["photo_id"], "UID-2", assignment_source="manual")

    assignments = db.execute(
        "SELECT specimen_uid, is_current FROM photo_assignments ORDER BY assigned_at"
    ).fetchall()
    assert [r["specimen_uid"] for r in assignments] == ["UID-1", "UID-2"]
    assert [r["is_current"] for r in assignments] == [0, 1]


def test_mark_catalog_files_missing_on_disk(tmp_path):
    from app.services.photo_asset_service import mark_catalog_files_missing_on_disk, upsert_photo_file

    project, incoming, db = _project(tmp_path)
    img = incoming / "IMG_0004.JPG"
    _jpg(img)
    upsert_photo_file(db, str(project), str(img), compute_hash=False)
    img.unlink()

    assert mark_catalog_files_missing_on_disk(db, str(project)) == 1
    row = db.execute("SELECT exists_on_disk FROM photo_files").fetchone()
    assert row["exists_on_disk"] == 0
