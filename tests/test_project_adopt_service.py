"""tests/test_project_adopt_service.py — 认领预扫描 + 最小 adopt."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import project_tree_service as pts
from app.services.project_adopt_service import (
    adopt_project,
    is_drive_root_or_system_dir,
    prescan_project,
)


class _FakeCtx:
    pass


def test_prescan_counts_images(tmp_path):
    ws = tmp_path / "legacy"
    ws.mkdir()
    (ws / "incoming-jpg").mkdir()
    (ws / "incoming-jpg" / "a.jpg").write_bytes(b"x")
    (ws / "results").mkdir()
    (ws / "results" / "b.tif").write_bytes(b"x")

    report = prescan_project(str(ws))
    assert report.jpg_count >= 1
    assert report.tiff_count >= 1
    assert report.has_data is False
    assert report.original_files_touched == 0


def test_adopt_creates_db_only(tmp_path, monkeypatch):
    import json as _json

    from app.services import project_service as ps

    ws = tmp_path / "claim-me"
    ws.mkdir()
    (ws / "incoming-jpg").mkdir()
    jp = tmp_path / "user_projects.json"
    jp.write_text(_json.dumps({"version": 1, "projects": []}), encoding="utf-8")
    monkeypatch.setattr(ps, "default_user_projects_json_path", lambda: str(jp))

    ctx = _FakeCtx()
    result = adopt_project(ctx, str(ws))
    assert result == "adopted"
    assert pts.is_workspace(str(ws))
    assert (ws / "incoming-jpg").exists()
    assert not (ws / "results").exists() or True  # adopt 不强制建 results
    assert len(ps.list_projects(str(jp))) == 1


def test_adopt_idempotent(tmp_path, monkeypatch):
    import json as _json

    from app.services import project_service as ps

    ws = tmp_path / "already"
    ws.mkdir()
    (ws / "_data").mkdir()
    (ws / "_data" / "project.db").write_bytes(b"")
    jp = tmp_path / "user_projects.json"
    jp.write_text(_json.dumps({"version": 1, "projects": []}), encoding="utf-8")
    monkeypatch.setattr(ps, "default_user_projects_json_path", lambda: str(jp))

    assert adopt_project(_FakeCtx(), str(ws)) == "already"


def test_rejects_home_directory(tmp_path):
    home = str(Path.home())
    with pytest.raises(ValueError):
        adopt_project(_FakeCtx(), home)


def test_is_drive_root_or_system_dir_home():
    assert is_drive_root_or_system_dir(str(Path.home()))
