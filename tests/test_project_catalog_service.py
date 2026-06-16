from pathlib import Path

import pytest

from app.db import db_manager


@pytest.fixture(autouse=True)
def close_dbs():
    db_manager.close_all()
    yield
    db_manager.close_all()


def test_register_workspace_writes_root_catalog_and_workspace_meta(tmp_path):
    from app.services.project_catalog_service import register_workspace

    root = tmp_path / "广西采集项目-2026"
    workspace = root / "广西儒艮2026" / "断面201260612"
    workspace.mkdir(parents=True)

    result = register_workspace(
        str(root),
        str(workspace),
        name="广西儒艮2026 / 断面201260612",
        project_meta={"name": "广西采集项目-2026", "code": "GX-2026"},
    )

    assert result["project"]["name"] == "广西采集项目-2026"
    assert result["workspace"]["relative_path"] == "广西儒艮2026/断面201260612"
    assert result["workspace"]["name"] == "广西儒艮2026 / 断面201260612"
    assert result["workspace"]["workspace_id"] == result["workspace_meta"]["workspace_id"]

    root_db = db_manager.get_db(str(root))
    row = root_db.execute("SELECT * FROM workspaces").fetchone()
    assert row["relative_path"] == "广西儒艮2026/断面201260612"

    ws_db = db_manager.get_db(str(workspace))
    meta = ws_db.execute("SELECT * FROM workspace_meta").fetchone()
    assert meta["project_id"] == result["project"]["project_id"]
    assert meta["root_project_hint"] == str(root.resolve())


def test_register_workspace_updates_relative_path_for_existing_workspace(tmp_path):
    from app.services.project_catalog_service import register_workspace

    root = tmp_path / "广西采集项目-2026"
    old = root / "广西白龙尾2026" / "断面A"
    new = root / "广西白龙尾2026" / "断面A_重命名"
    old.mkdir(parents=True)

    first = register_workspace(str(root), str(old))
    old.rename(new)
    second = register_workspace(str(root), str(new), name="断面A_重命名")

    assert second["workspace"]["workspace_id"] == first["workspace"]["workspace_id"]
    assert second["workspace"]["relative_path"] == "广西白龙尾2026/断面A_重命名"
    rows = db_manager.get_db(str(root)).execute("SELECT * FROM workspaces").fetchall()
    assert len(rows) == 1


def test_enter_workspace_with_root_registers_catalog(tmp_path):
    from app.services.project_catalog_service import list_registered_workspaces
    from app.services.project_service import enter_workspace

    root = tmp_path / "广西采集项目-2026"
    leaf = root / "广西儒艮2026" / "断面C-201260613"
    leaf.mkdir(parents=True)

    class Ctx:
        current_project_dir = None
        current_project_root = None

    ctx = Ctx()
    enter_workspace(ctx, str(leaf), root=str(root))

    rows = list_registered_workspaces(str(root))
    assert len(rows) == 1
    assert rows[0]["relative_path"] == "广西儒艮2026/断面C-201260613"
    assert (root / "_data" / "project.db").exists()
    assert (leaf / "_data" / "project.db").exists()
