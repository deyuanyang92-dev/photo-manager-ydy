"""TIF 删除的管理员闸门 —— 可以删, 但必须留下是谁删的。

用户 2026-07-12: "TIF 不是不能删, 只是需要管理员。要输入密码, 给出删除的操作人。"
"""
from __future__ import annotations

import json
import os
import sqlite3

import pytest

from app.db import db_manager
from app.services.tiff_delete_gate import (
    TiffDeleteDenied,
    check_admin,
    delete_tiff_with_audit,
)


@pytest.fixture
def cfg(tmp_path):
    """独立的密码配置(不碰真的 data/app_config.json)。密码 = admin888。"""
    import hashlib

    p = tmp_path / "app_config.json"
    p.write_text(
        json.dumps({"edit_password": hashlib.sha256(b"admin888").hexdigest()}),
        encoding="utf-8",
    )
    return str(p)


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db_manager.ensure_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def tif(tmp_path):
    p = tmp_path / "FJ-XM-A01-1-T95E-20260601.tif"
    p.write_bytes(b"II*\x00" + b"master" * 100)
    return str(p)


def test_wrong_password_refuses_delete(db, tif, cfg):
    with pytest.raises(TiffDeleteDenied, match="密码"):
        delete_tiff_with_audit(db, tif, actor="张三", password="xxx", config_path=cfg)
    assert os.path.isfile(tif), "密码错 -> 文件必须还在"


def test_missing_actor_refuses_delete(db, tif, cfg):
    with pytest.raises(TiffDeleteDenied, match="操作人"):
        delete_tiff_with_audit(db, tif, actor="   ", password="admin888", config_path=cfg)
    assert os.path.isfile(tif), "没填操作人 -> 文件必须还在"


def test_admin_delete_removes_file_and_records_who(db, tif, cfg):
    delete_tiff_with_audit(
        db, tif, actor="张三", password="admin888", reason="重拍", config_path=cfg
    )

    assert not os.path.isfile(tif), "管理员确认后 TIF 应被删除"

    row = db.execute(
        "SELECT actor, action, entity_id, old_value_json FROM audit_log "
        "WHERE action='delete_tiff' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert row is not None, "删除必须写进 audit_log —— 文件可以没, 记录不能没"
    assert row["actor"] == "张三"
    assert row["entity_id"] == "FJ-XM-A01-1-T95E-20260601.tif"
    old = json.loads(row["old_value_json"])
    assert old["size"] > 0 and old["reason"] == "重拍", "要记下删的是多大的文件、为什么删"


def test_check_admin_returns_trimmed_actor(cfg):
    assert check_admin(" 李四 ", "admin888", config_path=cfg) == "李四"


def test_audit_survives_without_project_db(tif, cfg):
    """没有打开项目(db=None)时也能删, 只是审计只落到日志文件 —— 不许因此崩。"""
    delete_tiff_with_audit(None, tif, actor="王五", password="admin888", config_path=cfg)
    assert not os.path.isfile(tif)
