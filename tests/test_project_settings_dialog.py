"""项目根的「项目设置」入口(需求 2026-07-12)。

项目根是容器(非工作区), 设置抽屉却只挂在工作台上 —— 项目级默认值(采集人/地区代码/默认坐标/
拍摄场地)本来无处可填。这是把「新建项目」对话框砍到 2 个字段的**前提条件**(spec §3.4)。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.app_context import AppContext
from app.db.db_manager import open_project_db_private
from app.services import project_settings_service as pss
from app.services.project_scaffold_service import create_survey_project
from app.widgets.project_settings_dialog import RootSettingsCtx


def test_root_ctx_points_db_at_project_root(tmp_path):
    res = create_survey_project(str(tmp_path), name="江苏盐城2026", sites=[])
    root = res["root"]

    proxy = RootSettingsCtx(AppContext(), root)
    try:
        assert proxy.current_project_dir == root
        db = proxy.get_db()
        assert isinstance(db, sqlite3.Connection)
        pss.save_setting(db, "personnel", {"collector": "张三"})
        db.commit()
    finally:
        proxy.close()

    check = open_project_db_private(root, create=False)
    try:
        assert pss.load_setting(check, "personnel", {}).get("collector") == "张三"
    finally:
        check.close()


def test_root_ctx_delegates_other_attrs(tmp_path):
    res = create_survey_project(str(tmp_path), name="P", sites=[])
    real = AppContext()

    proxy = RootSettingsCtx(real, res["root"])
    try:
        assert proxy.settings is real.settings  # 全局设置照旧委托给真 ctx
    finally:
        proxy.close()


def test_root_ctx_close_releases_lock_and_is_idempotent(tmp_path):
    """红线: 关闭后不得持有文件锁(Windows 上会导致项目文件夹移不动/删不掉)。"""
    res = create_survey_project(str(tmp_path), name="P", sites=[])

    proxy = RootSettingsCtx(AppContext(), res["root"])
    proxy.get_db()
    proxy.close()

    assert proxy._db is None
    proxy.close()  # 幂等: 关两次不抛


def test_root_ctx_uses_private_conn_not_cached(tmp_path):
    """项目根的库必须走 private 连接 —— 缓存连接会锁住项目文件夹到进程退出。"""
    from app.db import db_manager

    res = create_survey_project(str(tmp_path), name="P", sites=[])
    proxy = RootSettingsCtx(AppContext(), res["root"])
    try:
        db = proxy.get_db()
        cached = db_manager._DB_CACHE if hasattr(db_manager, "_DB_CACHE") else {}
        assert db not in cached.values()
    finally:
        proxy.close()


def test_settings_written_at_root_are_inherited_by_child(tmp_path):
    """闭环: 项目根设一次 → 断面自动继承 → 工作台右栏预填(需求的核心)。"""
    res = create_survey_project(str(tmp_path), name="江苏盐城2026", sites=[])
    root = Path(res["root"])

    proxy = RootSettingsCtx(AppContext(), str(root))
    try:
        db = proxy.get_db()
        pss.save_setting(
            db, "personnel", {"collector": "张三", "photographer": "李四"}
        )
        pss.save_setting(
            db,
            "code_labels",
            {"province": "JSYC", "site": "", "stations": {}, "species": {}},
        )
        db.commit()
    finally:
        proxy.close()

    child = root / "断面A"
    child.mkdir()

    prefill = pss.effective_new_specimen_prefill(str(child), root=str(root))

    assert prefill["collector"] == "张三"
    assert prefill["photographer"] == "李四"
    assert prefill["province"] == "JSYC"
