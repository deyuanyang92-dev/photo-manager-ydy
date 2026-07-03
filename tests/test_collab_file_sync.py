from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.db import db_manager
from app.services.collab_file_sync import (
    build_project_manifest,
    project_relative_path,
    resolve_project_relative,
    sha256_file,
)


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db_manager.ensure_schema(conn)
    return conn


def test_manifest_includes_grouping_media_and_filters_uid(tmp_path):
    project = tmp_path / "project"
    incoming = project / "incoming-jpg"
    results = project / "results"
    incoming.mkdir(parents=True)
    results.mkdir()

    jpg = incoming / "cam-001.jpg"
    tiff = results / "U-1-1-20260703.tif"
    zipf = results / "U-1-1-20260703.zip"
    other = results / "U-2-1-20260703.tif"
    jpg.write_bytes(b"jpg-a")
    tiff.write_bytes(b"tiff-a")
    zipf.write_bytes(b"zip-a")
    other.write_bytes(b"other")

    db = _db()
    db.executemany(
        "INSERT INTO specimens (uid, owner_project_dir) VALUES (?, ?)",
        [("U-1", str(project)), ("U-2", str(project))],
    )
    db.execute(
        """
        INSERT INTO grouping (uid, group_index, jpg_paths, composed_tiff_path, archive_zip)
        VALUES (?, 0, ?, ?, ?)
        """,
        ("U-1", json.dumps([str(jpg)]), str(tiff), str(zipf)),
    )
    db.commit()

    manifest = build_project_manifest(str(project), db=db, uids=["U-1"], device_id="dev-a")
    by_rel = {entry.relative_path: entry for entry in manifest}

    assert set(by_rel) == {
        "incoming-jpg/cam-001.jpg",
        "results/U-1-1-20260703.tif",
        "results/U-1-1-20260703.zip",
    }
    assert by_rel["incoming-jpg/cam-001.jpg"].uid == "U-1"
    assert by_rel["incoming-jpg/cam-001.jpg"].sha256 == sha256_file(jpg)
    assert all(entry.device_id == "dev-a" for entry in manifest)


def test_manifest_scan_infers_uid_from_result_filename(tmp_path):
    project = tmp_path / "project"
    results = project / "results"
    results.mkdir(parents=True)
    tiff = results / "GXFCG-BLW-SC001-D79-20260618-1-20260703.tif"
    tiff.write_bytes(b"tiff")

    db = _db()
    uid = "GXFCG-BLW-SC001-D79-20260618"
    db.execute(
        "INSERT INTO specimens (uid, owner_project_dir) VALUES (?, ?)",
        (uid, str(project)),
    )
    db.commit()

    manifest = build_project_manifest(str(project), db=db)

    assert len(manifest) == 1
    assert manifest[0].uid == uid
    assert manifest[0].relative_path == "results/GXFCG-BLW-SC001-D79-20260618-1-20260703.tif"


def test_project_relative_path_and_resolve_reject_escape(tmp_path):
    project = tmp_path / "project"
    incoming = project / "incoming-jpg"
    incoming.mkdir(parents=True)
    jpg = incoming / "a.jpg"
    jpg.write_bytes(b"jpg")

    assert project_relative_path(project, jpg) == "incoming-jpg/a.jpg"
    assert resolve_project_relative(project, "incoming-jpg/a.jpg") == jpg.resolve()

    with pytest.raises(ValueError):
        resolve_project_relative(project, "../outside.jpg")
    with pytest.raises(ValueError):
        resolve_project_relative(project, "/tmp/outside.jpg")
