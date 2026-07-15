"""tests/test_collab_join_shared_local.py — 物化队友共享项目到本地(服务层).

Claude Code 2026-07-15 — 队友共享、我本地还没有的项目, 双击"拉取到本地":
用真实原语(parse_project_sync_code + ensure_project_dirs + set_project_identity)
在本地建一个空项目并绑定到共享 project_id, 之后走已有同步把编号/照片拉进来。
纯服务层, temp 可测, 不碰网络/UI。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from app.services.collab_project_bind import join_shared_project_locally
from app.services.project_identity_service import parse_project_sync_code, project_sync_code


def _code(project_id: str, name: str) -> str:
    return project_sync_code(project_id, project_name=name)


def test_creates_local_project_bound_to_shared_id(tmp_path):
    pid = "a" * 32
    code = _code(pid, "广西项目")
    res = join_shared_project_locally(code, str(tmp_path), "广西项目")
    local = res["local_dir"]
    # 本地建出了项目目录 + project.db
    assert os.path.isdir(local)
    from pathlib import Path
    assert (Path(local) / "_data" / "project.db").is_file()
    # 绑定到了共享 project_id
    assert res["project_id"] == pid
    from app.db.db_manager import open_project_db_private
    from app.services.project_identity_service import ensure_project_identity
    db = open_project_db_private(local)
    try:
        assert ensure_project_identity(db) == pid
    finally:
        db.close()


def test_name_collision_gets_unique_dir(tmp_path):
    pid1 = "a" * 32
    pid2 = "b" * 32
    r1 = join_shared_project_locally(_code(pid1, "项目X"), str(tmp_path), "项目X")
    r2 = join_shared_project_locally(_code(pid2, "项目X"), str(tmp_path), "项目X")
    assert r1["local_dir"] != r2["local_dir"], "同名不同项目 -> 不同本地目录, 不覆盖"


def test_rejects_bad_code(tmp_path):
    with pytest.raises(ValueError):
        join_shared_project_locally("", str(tmp_path), "x")


def test_parses_raw_project_id_too(tmp_path):
    pid = "c" * 32
    # 直接给 32 位 id(不带名字)也能用
    res = join_shared_project_locally(pid, str(tmp_path), "无名共享")
    assert res["project_id"] == pid
    assert parse_project_sync_code(pid)["projectId"] == pid
