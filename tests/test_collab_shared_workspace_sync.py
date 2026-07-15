"""tests/test_collab_shared_workspace_sync.py — 多工作区标本同步核心(阶段 1 a/c).

Claude Code 2026-07-15 — 团队共享工作区多主同步 spec 阶段 1:
把"只同步当前打开的 1 个工作区"解耦成"遍历一组共享工作区目录", 逐个用私有连接
读/合并(守红线), 按稳定 workspace_id 追踪各自的增量游标。纯服务层, temp 多库可测,
不碰网络 / 不碰 self._project_dir。
"""
from __future__ import annotations

import pytest

from app.db import db_manager
from app.services.collab_specimen_sync import (
    iter_shared_workspace_specimens,
    workspace_sync_id,
    write_specimens_to_local_db,
)


def _make_ws(tmp_path, name: str, specimens: list[dict]):
    ws = tmp_path / name
    (ws / "_data").mkdir(parents=True)
    db_manager.open_project_db_private(str(ws), create=True).close()
    if specimens:
        write_specimens_to_local_db(str(ws), specimens)
    return str(ws)


def test_workspace_sync_id_is_stable_and_distinct(tmp_path):
    a = _make_ws(tmp_path, "断面1", [])
    b = _make_ws(tmp_path, "断面1_other", [])
    # 同一目录两次调用 -> 同一 id(稳定)
    assert workspace_sync_id(a) == workspace_sync_id(a)
    # 两个不同工作区 -> 不同 id(即使人可读名字都叫"断面1"也不混淆)
    assert workspace_sync_id(a) != workspace_sync_id(b)


def test_iter_reads_every_shared_workspace(tmp_path):
    a = _make_ws(tmp_path, "wsA", [
        {"uid": "A-1", "collab_updated_at": "2026-01-01T00:00:00"},
        {"uid": "A-2", "collab_updated_at": "2026-01-01T00:00:00"},
    ])
    b = _make_ws(tmp_path, "wsB", [
        {"uid": "B-1", "collab_updated_at": "2026-01-01T00:00:00"},
    ])
    out = iter_shared_workspace_specimens([a, b])
    # 每个共享工作区一个条目, 带稳定 id + 目录 + 该库全部标本 + 当前最大 rev
    ids = {info["workspace_id"] for info in out}
    assert ids == {workspace_sync_id(a), workspace_sync_id(b)}
    by_dir = {info["dir"]: info for info in out}
    assert {s["uid"] for s in by_dir[a]["specimens"]} == {"A-1", "A-2"}
    assert {s["uid"] for s in by_dir[b]["specimens"]} == {"B-1"}
    assert by_dir[a]["max_rev"] >= 1


def test_iter_since_rev_returns_only_delta_per_workspace(tmp_path):
    a = _make_ws(tmp_path, "wsA", [
        {"uid": "A-1", "collab_updated_at": "2026-01-01T00:00:00"},
    ])
    first = iter_shared_workspace_specimens([a])[0]
    cursor = {first["workspace_id"]: first["max_rev"]}

    # 再改/加, 只应回增量
    write_specimens_to_local_db(a, [
        {"uid": "A-2", "collab_updated_at": "2026-02-01T00:00:00"},
    ])
    delta = iter_shared_workspace_specimens([a], since_rev_by_id=cursor)[0]
    assert {s["uid"] for s in delta["specimens"]} == {"A-2"}, "只回自上次游标后的增量"


def test_iter_tolerates_missing_or_broken_workspace(tmp_path):
    a = _make_ws(tmp_path, "wsA", [
        {"uid": "A-1", "collab_updated_at": "2026-01-01T00:00:00"},
    ])
    missing = str(tmp_path / "nope")  # 不存在的目录(盘没挂 / 被删)
    out = iter_shared_workspace_specimens([a, missing])
    # 坏的跳过, 好的照常 —— 绝不因一个坏库整批炸(跨库读容忍缺失/损坏/锁, 红线)
    dirs = {info["dir"] for info in out}
    assert a in dirs
    assert missing not in dirs


def test_merge_flows_both_ways_across_workspaces(tmp_path):
    """A 机器把 wsX 的编号合并进 B 机器的 wsX 副本, 反向也一样(多主)。"""
    # 模拟两台机器各有一份 wsX 副本
    a_wsx = _make_ws(tmp_path, "A_wsX", [
        {"uid": "X-1", "collector": "张三", "collab_updated_at": "2026-01-01T00:00:00"},
    ])
    b_wsx = _make_ws(tmp_path, "B_wsX", [])

    # A 的编号索引 -> 合并进 B
    a_records = iter_shared_workspace_specimens([a_wsx])[0]["specimens"]
    write_specimens_to_local_db(b_wsx, a_records)
    from app.services.collab_specimen_sync import get_local_specimens
    assert {s["uid"] for s in get_local_specimens(b_wsx)} == {"X-1"}

    # B 在自己副本里新增 X-2, 反向合并回 A
    write_specimens_to_local_db(b_wsx, [
        {"uid": "X-2", "collector": "李四", "collab_updated_at": "2026-02-01T00:00:00"},
    ])
    b_records = get_local_specimens(b_wsx)
    write_specimens_to_local_db(a_wsx, b_records)
    assert {s["uid"] for s in get_local_specimens(a_wsx)} == {"X-1", "X-2"}
