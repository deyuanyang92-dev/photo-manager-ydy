"""「最近使用」必须是真的最近（用户 2026-07-13，顶栏系统优化的地基）。

现状 bug：``record_recent_workspace`` 只在目录**首次出现**时 append，重复进入什么都不做
—— 于是「最近使用」实为「首次登记的倒序」。条目里也没有任何时间戳，既不能按最近排序，
也没法显示「昨天用过」。

契约：
1. 每次进入都刷新该条目的 ``lastOpenedAt``（epoch 秒）；
2. ``_recent_workspaces()``（顶栏读取）按 ``lastOpenedAt`` 降序 —— 刚进过的排最前；
3. 老数据没有 ``lastOpenedAt`` 也不崩（排在有时间戳的后面，维持旧的倒序行为）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.services.project_service import (
    clear_project_list_cache,
    record_recent_workspace,
)


@pytest.fixture
def jp(tmp_path):
    p = tmp_path / "user_projects.json"
    p.write_text(json.dumps({"version": 1, "projects": []}), encoding="utf-8")
    clear_project_list_cache(str(p))
    return p


def _entries(p: Path) -> list[dict]:
    clear_project_list_cache(str(p))
    return json.loads(p.read_text(encoding="utf-8"))["projects"]


def test_first_visit_stamps_last_opened_at(jp, tmp_path):
    ws = tmp_path / "断面A"
    ws.mkdir()

    before = time.time()
    record_recent_workspace(str(jp), str(ws))

    e = _entries(jp)[0]
    assert "lastOpenedAt" in e, "新条目必须带 lastOpenedAt 时间戳"
    assert e["lastOpenedAt"] >= int(before)


def test_revisit_updates_timestamp_not_duplicates(jp, tmp_path):
    """重复进入：不重复建条目，但**必须**刷新时间戳 —— 这就是「最近」的定义。"""
    a = tmp_path / "断面A"
    a.mkdir()
    b = tmp_path / "断面B"
    b.mkdir()

    record_recent_workspace(str(jp), str(a))
    record_recent_workspace(str(jp), str(b))

    # 手动把 A 的时间戳改旧，再「重新进入」A
    data = json.loads(jp.read_text(encoding="utf-8"))
    for e in data["projects"]:
        if e["directory"].endswith("断面A"):
            e["lastOpenedAt"] = 1000
    jp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    clear_project_list_cache(str(jp))

    record_recent_workspace(str(jp), str(a))

    entries = _entries(jp)
    assert len(entries) == 2, "重复进入不得产生重复条目"
    ea = next(e for e in entries if e["directory"].endswith("断面A"))
    assert ea["lastOpenedAt"] > 1000, "重复进入必须刷新 lastOpenedAt —— 否则「最近使用」永远不准"


def test_breadcrumb_recents_sorted_by_last_opened(jp, tmp_path, monkeypatch, qtbot):
    """顶栏「最近使用」按 lastOpenedAt 降序 —— 刚进过的排第一。"""
    import app.services.project_service as ps
    monkeypatch.setattr(ps, "default_user_projects_json_path", lambda: str(jp))

    dirs = []
    for name in ("老工作区", "中间的", "刚进过的"):
        d = tmp_path / name
        d.mkdir()
        dirs.append(d)
        record_recent_workspace(str(jp), str(d))
        time.sleep(0.01)

    # 再进一次「老工作区」→ 它应该跳到最前
    record_recent_workspace(str(jp), str(dirs[0]))

    from app.app_context import AppContext
    from app.widgets.workspace_breadcrumb import WorkspaceBreadcrumb

    ctx = AppContext()
    ctx.current_project_dir = None
    bc = WorkspaceBreadcrumb(ctx)
    qtbot.addWidget(bc)

    recents = bc._recent_workspaces()
    names = [r["name"] for r in recents]
    assert names and "老工作区" in names[0], (
        f"刚重新进入的「老工作区」必须排第一, 实际顺序: {names}"
    )


def test_legacy_entries_without_timestamp_do_not_crash(jp, tmp_path, monkeypatch, qtbot):
    old = tmp_path / "旧条目"
    old.mkdir()
    jp.write_text(json.dumps({
        "version": 1,
        "projects": [{"id": "x", "name": "旧条目", "directory": str(old), "dir": str(old)}],
    }, ensure_ascii=False), encoding="utf-8")
    clear_project_list_cache(str(jp))

    import app.services.project_service as ps
    monkeypatch.setattr(ps, "default_user_projects_json_path", lambda: str(jp))

    from app.app_context import AppContext
    from app.widgets.workspace_breadcrumb import WorkspaceBreadcrumb

    ctx = AppContext()
    ctx.current_project_dir = None
    bc = WorkspaceBreadcrumb(ctx)
    qtbot.addWidget(bc)
    recents = bc._recent_workspaces()
    assert recents and recents[0]["name"] == "旧条目"
