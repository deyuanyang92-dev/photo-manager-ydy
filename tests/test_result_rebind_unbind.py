"""tests/test_result_rebind_unbind.py — 成果 TIF 关联错编号后的纠错入口.

使用场景(用户 2026-07-10):
  合成/自动归档把某张 TIF 挂到了**错误的编号**上。用户在成果区右键这张 TIF:
    · 「解绑此成果」   —— 从错误编号上摘下来(还没想好归谁), 文件原地不动;
    · 「改绑到其他编号…」—— 直接选正确编号, 一步改挂。
  两者都不移动/删除磁盘上的 TIF 母版(红线)。
"""
from __future__ import annotations

import os
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from app.views.workbench_result_workflow import WorkbenchResultWorkflowMixin


class _Harness(WorkbenchResultWorkflowMixin):
    def __init__(self, db) -> None:
        self.ctx = types.SimpleNamespace(get_db=lambda: db)
        self.messages: list[str] = []
        self.refreshed: list = []
        self._current_uid = None

    def _status_message(self, msg: str) -> None:
        self.messages.append(msg)

    def _refresh_monitor(self) -> None:
        self.refreshed.append("monitor")

    def _refresh_results_column(self, uid, grouping=None) -> None:
        self.refreshed.append(("results", uid))

    # 真实工作台上存在的重载入口(解绑无目标编号时按当前模式重载)
    def _on_show_current_results(self) -> None:
        self.refreshed.append("show_current")

    def _on_show_all_results(self) -> None:
        self.refreshed.append("show_all")


def _db_with_group(tmp_path, uid: str):
    import sqlite3
    from app.db import db_manager
    from app.services.grouping_service import Group, save_grouping

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db_manager.ensure_schema(conn)
    tiff = tmp_path / "wrong.tif"
    tiff.write_bytes(b"tif")
    save_grouping(
        conn, uid,
        [Group(group_index=0, composed_tiff_path=str(tiff), status="composed")],
        clean_phantoms=False,
    )
    return conn, tiff


def test_unbind_detaches_and_keeps_file(tmp_path, monkeypatch):
    from app.services.grouping_service import load_grouping

    uid = "FJ-XM-A01-DLC001-T95E-20260601"
    db, tiff = _db_with_group(tmp_path, uid)
    h = _Harness(db)

    from app.utils import ui as _ui
    monkeypatch.setattr(_ui, "question", lambda *a, **k: _yes())
    monkeypatch.setattr(_ui, "info", lambda *a, **k: None)

    h._on_unbind_result(str(tiff), "")

    assert load_grouping(db, uid).groups == [], "应从错误编号解绑"
    assert tiff.is_file(), "解绑绝不删 TIF 母版(红线)"
    assert "show_current" in h.refreshed, "解绑后必须重载成果区, 否则旧关联不消失"
    assert "monitor" in h.refreshed


def test_unbind_reloads_all_mode_when_showing_all(tmp_path, monkeypatch):
    """「全部」模式下解绑 → 必须重扫全项目, 否则那张 TIF 还挂在旧编号下。"""
    uid = "FJ-XM-A01-DLC001-T95E-20260601"
    db, tiff = _db_with_group(tmp_path, uid)
    h = _Harness(db)
    h._results = types.SimpleNamespace(_display_mode="many")

    from app.utils import ui as _ui
    monkeypatch.setattr(_ui, "question", lambda *a, **k: _yes())

    h._on_unbind_result(str(tiff), "")
    assert "show_all" in h.refreshed


def test_unbind_aborts_when_user_declines(tmp_path, monkeypatch):
    from app.services.grouping_service import load_grouping

    uid = "FJ-XM-A01-DLC001-T95E-20260601"
    db, tiff = _db_with_group(tmp_path, uid)
    h = _Harness(db)

    from app.utils import ui as _ui
    monkeypatch.setattr(_ui, "question", lambda *a, **k: _no())

    h._on_unbind_result(str(tiff), "")
    assert len(load_grouping(db, uid).groups) == 1, "用户取消 → 关联不变"


def test_rebind_moves_to_chosen_uid(tmp_path, monkeypatch):
    from app.services.grouping_service import load_grouping

    wrong = "FJ-XM-A01-DLC001-T95E-20260601"
    right = "FJ-XM-A02-DLC002-T95E-20260601"
    db, tiff = _db_with_group(tmp_path, wrong)
    db.execute("INSERT INTO specimens (uid) VALUES (?)", (right,))
    db.execute("INSERT INTO specimens (uid) VALUES (?)", (wrong,))
    db.commit()
    h = _Harness(db)

    monkeypatch.setattr(h, "_ask_target_uid", lambda *a, **k: right, raising=False)
    from app.utils import ui as _ui
    monkeypatch.setattr(_ui, "info", lambda *a, **k: None)

    h._on_rebind_result(str(tiff), "")

    assert load_grouping(db, wrong).groups == [], "旧编号应被摘掉"
    assert len(load_grouping(db, right).groups) == 1, "新编号应挂上"
    assert tiff.is_file()


def test_rebind_cancelled_changes_nothing(tmp_path, monkeypatch):
    from app.services.grouping_service import load_grouping

    wrong = "FJ-XM-A01-DLC001-T95E-20260601"
    db, tiff = _db_with_group(tmp_path, wrong)
    h = _Harness(db)
    monkeypatch.setattr(h, "_ask_target_uid", lambda *a, **k: "", raising=False)

    h._on_rebind_result(str(tiff), "")
    assert len(load_grouping(db, wrong).groups) == 1, "取消选择 → 关联不变"


def _yes():
    from PyQt6.QtWidgets import QMessageBox
    return QMessageBox.StandardButton.Yes


def _no():
    from PyQt6.QtWidgets import QMessageBox
    return QMessageBox.StandardButton.No
