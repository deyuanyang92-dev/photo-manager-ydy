"""手动删除 ZIP —— 省空间, 但绝不能删掉原片的唯一副本。

用户 2026-07-12:
  · "还原后 zip 可以被删, 以节约空间" —— 主流程已经这么做了。
  · 但**断电断在「写完库、还没删 ZIP」那一刻**, 会在 results/ 里留下一个孤儿 ZIP:
    数据库里已经没有它的登记, 界面上却删不掉(成果区右键菜单**没有删除 ZIP 这一项**),
    只能去文件管理器里手工删。
  · 用户还要求 TIF 也能手动删 —— 那条已经有了(成果区右键「删除 TIF」)。

红线(archive_service 不变式 2): JPG 一旦被归档消费(默认删除散 JPG), **ZIP 就是那批
原片的唯一副本**。所以:
  · 孤儿 ZIP(没挂在任何组上)         -> 允许删, 带确认框
  · 仍挂在某个组上的 ZIP(archive_zip) -> **拒绝裸删**, 引导用户走「还原原片」
    (还原会先把 JPG 恢复回原位、校验 SHA-256, 成功后才删 ZIP)

(Fable 5, 2026-07-12)
"""
from __future__ import annotations

import os
import sqlite3
import types
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from app.db import db_manager
from app.services.grouping_service import Group, save_grouping


UID = "FJ-XM-A01-DLC001-T95E-20260601"


class _Harness:
    """只装配「删除 ZIP」这条路所需的最小宿主。"""

    def __init__(self, db, project_dir: str) -> None:
        self.ctx = types.SimpleNamespace(
            get_db=lambda: db, current_project_dir=project_dir
        )
        self.messages: list[str] = []
        self.refreshed = 0

    from app.views.workbench_monitor_workflow import (
        WorkbenchMonitorWorkflowMixin as _MW,
    )
    _on_delete_result_zip_path = _MW._on_delete_result_zip_path

    def _status_message(self, text: str, *_a) -> None:
        self.messages.append(text)

    def _refresh_monitor(self) -> None:
        self.refreshed += 1

    def _on_show_current_results(self) -> None:
        pass

    def _on_show_all_results(self) -> None:
        pass


@pytest.fixture
def db_and_dir(tmp_path):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db_manager.ensure_schema(db)
    (tmp_path / "results").mkdir(parents=True, exist_ok=True)
    yield db, tmp_path
    db.close()


def _zip_at(path) -> str:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("a.jpg", b"\xff\xd8\xffX")
    return str(path)


def test_orphan_zip_can_be_deleted_after_confirm(db_and_dir, monkeypatch):
    """孤儿 ZIP(断电残留 / 外部拷入): 确认后可删 —— 用户要省空间。"""
    from PyQt6.QtWidgets import QMessageBox
    from app.views import workbench_monitor_workflow as mw

    db, tmp = db_and_dir
    zp = _zip_at(tmp / "results" / "orphan.zip")

    h = _Harness(db, str(tmp))
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(mw.ui, "warn", lambda *a, **k: None)

    h._on_delete_result_zip_path(zp)

    assert not os.path.exists(zp), "孤儿 ZIP 应被删除"


def test_declining_confirm_keeps_zip(db_and_dir, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    from app.views import workbench_monitor_workflow as mw

    db, tmp = db_and_dir
    zp = _zip_at(tmp / "results" / "orphan.zip")

    h = _Harness(db, str(tmp))
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )
    monkeypatch.setattr(mw.ui, "warn", lambda *a, **k: None)

    h._on_delete_result_zip_path(zp)

    assert os.path.exists(zp), "点「否」-> 不许删"


# Claude Code 修改 2026-07-15 — P0 数据丢失漏洞回归测试(codex 实测复现):
# 数据库查询异常必须 fail-closed(拒绝删除), 不能被 `except Exception: rows = []`
# 悄悄当成"查出来是空, 所以是孤儿 ZIP"而放行删除——那是这批原片可能唯一的副本。
def test_broken_db_query_refuses_delete_fail_closed(db_and_dir, monkeypatch):
    """数据库查询失败(锁库/损坏/schema 问题) -> 必须拒绝删除, 不能当成孤儿放行。"""
    from app.utils import ui as _ui

    db, tmp = db_and_dir
    zp = _zip_at(tmp / "results" / "orphan.zip")

    h = _Harness(db, str(tmp))
    warned = []
    monkeypatch.setattr(_ui, "warn", lambda *a, **k: warned.append(a))

    # sqlite3.Connection.execute is a read-only C method — can't monkeypatch the
    # instance directly, so swap in a mock connection whose execute() raises,
    # matching a real "database is locked" / corrupt-schema failure.
    from unittest.mock import MagicMock
    broken_db = MagicMock()
    broken_db.execute.side_effect = sqlite3.OperationalError("database is locked")
    h.ctx.get_db = lambda: broken_db

    h._on_delete_result_zip_path(zp)

    assert os.path.exists(zp), "数据库查询失败时必须拒绝删除, 不能当成孤儿 ZIP 放行"
    assert warned, "必须明确告知用户: 无法确认归属, 已拒绝删除"


def test_no_db_connection_refuses_delete_fail_closed(db_and_dir, monkeypatch):
    """拿不到数据库连接(ctx.get_db() 返回 None) -> 同样必须拒绝删除。"""
    from app.utils import ui as _ui

    db, tmp = db_and_dir
    zp = _zip_at(tmp / "results" / "orphan.zip")

    h = _Harness(db, str(tmp))
    h.ctx.get_db = lambda: None
    warned = []
    monkeypatch.setattr(_ui, "warn", lambda *a, **k: warned.append(a))

    h._on_delete_result_zip_path(zp)

    assert os.path.exists(zp), "拿不到数据库连接时必须拒绝删除"
    assert warned


def test_registered_zip_is_refused_and_points_at_restore(db_and_dir, monkeypatch):
    """红线: ZIP 还挂在某个组上 = 那批原片的唯一副本 -> 拒绝裸删, 引导走「还原原片」。"""
    from PyQt6.QtWidgets import QMessageBox
    from app.views import workbench_monitor_workflow as mw

    db, tmp = db_and_dir
    zp = _zip_at(tmp / "results" / "r.zip")
    tif = tmp / "results" / "t.tif"
    tif.write_bytes(b"tif")
    save_grouping(
        db, UID,
        [Group(group_index=0, composed_tiff_path=str(tif), archive_zip=zp,
               jpg_paths=["/gone/a.jpg"], status="organized")],
        clean_phantoms=False,
    )

    warned: list = []
    h = _Harness(db, str(tmp))
    monkeypatch.setattr(mw.ui, "warn", lambda *a, **k: warned.append(a))
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),  # 就算用户点是
    )

    h._on_delete_result_zip_path(zp)

    assert os.path.exists(zp), "登记在册的 ZIP 绝不能裸删(原片的唯一副本)"
    assert warned, "必须告诉用户去走「还原原片」"
    assert any("还原原片" in str(a) for a in warned[0])
