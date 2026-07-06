from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.db import db_manager
from app.services.collab_file_sync import (
    build_project_manifest,
    manifest_payload,
    project_relative_path,
    resolve_project_relative,
    sha256_file,
    sync_from_peers,
)
from app.services.collab_service import PeerInfo
from app.services.project_identity_service import ensure_project_identity
from app.services.project_identity_service import (
    parse_project_sync_code,
    project_sync_code,
    read_project_identity,
    set_project_identity,
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


def test_project_identity_is_stable_in_project_db():
    db = _db()

    first = ensure_project_identity(db, project_name="Project A")
    second = ensure_project_identity(db, project_name="Project A")

    assert first
    assert second == first


def test_project_sync_code_roundtrip_and_explicit_set():
    db = _db()
    project_id = "a" * 32
    code = project_sync_code(project_id, project_name="Project A")

    parsed = parse_project_sync_code(code)
    saved = set_project_identity(db, parsed["projectId"], project_name=parsed["projectName"])

    assert parsed == {"projectId": project_id, "projectName": "Project A"}
    assert saved == project_id
    assert read_project_identity(db) == project_id


def test_manifest_payload_includes_project_id(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    db = _db()

    payload = manifest_payload(str(project), db=db, project_id="P-123")

    assert payload["projectId"] == "P-123"


def test_sync_from_peers_skips_different_project_without_http(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    peer = PeerInfo(
        ip="192.168.1.20",
        port=5050,
        group_code="G1",
        project_id="OTHER",
    )

    summary = sync_from_peers(
        project_dir=str(project),
        peers=[peer],
        group_code="G1",
        project_id="LOCAL",
    )

    assert summary.incompatible_peers == 1
    assert summary.planned == 0
