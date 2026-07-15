"""tests/test_collab_specimen_rev.py — 标本同步 revision 增量游标(阶段 1d).

Claude Code 2026-07-15 — 团队共享工作区多主同步 spec 阶段 1:
specimens 加本地单调 collab_rev; 每次本地写(INSERT/UPDATE)给该行一个新的递增 rev;
get_local_specimens(since_rev=N) 只回 collab_rev > N 的行 —— 对方只拉自己没见过的
增量, 几十万工作区不再每轮全表传。

红线: collab_rev 是**本地**计数, 不在 SPEC_SYNC_COLS(远端白名单)里, 远端 payload
里的 rev 一律忽略 —— 每台机器合并时给行分配自己的本地 rev。
"""
from __future__ import annotations

import pytest

from app.db import db_manager
from app.services.collab_specimen_sync import (
    SPEC_SYNC_COLS,
    get_local_specimens,
    write_specimens_to_local_db,
)


@pytest.fixture
def project(tmp_path):
    ws = tmp_path / "ws"
    (ws / "_data").mkdir(parents=True)
    conn = db_manager.open_project_db_private(str(ws), create=True)
    conn.close()
    return str(ws)


def test_collab_rev_is_not_a_synced_column():
    """collab_rev 是本地元数据, 绝不在远端白名单里(否则会被对方的 rev 污染)。"""
    assert "collab_rev" not in SPEC_SYNC_COLS


def test_written_rows_get_monotonic_rev(project):
    write_specimens_to_local_db(project, [
        {"uid": "A", "collector": "张三", "collab_updated_at": "2026-01-01T00:00:00"},
        {"uid": "B", "collector": "李四", "collab_updated_at": "2026-01-01T00:00:00"},
    ])
    rows = {r["uid"]: r for r in get_local_specimens(project)}
    assert "collab_rev" in rows["A"]
    # 两行各拿到不同的、递增的本地 rev
    assert rows["A"]["collab_rev"] >= 1
    assert rows["B"]["collab_rev"] > rows["A"]["collab_rev"]


def test_since_rev_returns_only_newer_rows(project):
    write_specimens_to_local_db(project, [
        {"uid": "A", "collector": "张三", "collab_updated_at": "2026-01-01T00:00:00"},
    ])
    first_max = max(r["collab_rev"] for r in get_local_specimens(project))

    # 之后再来一行 + 改一行
    write_specimens_to_local_db(project, [
        {"uid": "B", "collector": "李四", "collab_updated_at": "2026-01-02T00:00:00"},
        {"uid": "A", "collector": "张三改", "collab_updated_at": "2026-01-03T00:00:00"},
    ])

    delta = get_local_specimens(project, since_rev=first_max)
    uids = {r["uid"] for r in delta}
    assert uids == {"A", "B"}, "since_rev 后只回改动过的行(A 被改、B 新增)"
    # 没改动过的行不在增量里
    write_specimens_to_local_db(project, [
        {"uid": "C", "collector": "王五", "collab_updated_at": "2026-01-04T00:00:00"},
    ])
    latest_max = max(r["collab_rev"] for r in get_local_specimens(project))
    only_c = get_local_specimens(project, since_rev=latest_max - 1)
    assert {r["uid"] for r in only_c} == {"C"}


def test_since_rev_zero_returns_everything(project):
    write_specimens_to_local_db(project, [
        {"uid": "A", "collab_updated_at": "2026-01-01T00:00:00"},
        {"uid": "B", "collab_updated_at": "2026-01-01T00:00:00"},
    ])
    assert len(get_local_specimens(project, since_rev=0)) == 2


def test_remote_rev_in_payload_is_ignored(project):
    """对方 payload 带的 collab_rev 是它的本地序号, 必须被忽略, 用本地自己的 rev。"""
    write_specimens_to_local_db(project, [
        {"uid": "A", "collector": "张三", "collab_updated_at": "2026-01-01T00:00:00",
         "collab_rev": 999999},  # 对方的 rev, 应被无视
    ])
    row = get_local_specimens(project)[0]
    assert row["collab_rev"] != 999999, "远端 rev 不能污染本地 rev"
    assert row["collab_rev"] >= 1


def test_unchanged_row_keeps_its_rev(project):
    """LWW 判定"本地更新/相同"而跳过写入的行, rev 不该变(它没真的改)。"""
    write_specimens_to_local_db(project, [
        {"uid": "A", "collector": "张三", "collab_updated_at": "2026-05-01T00:00:00"},
    ])
    rev_before = get_local_specimens(project)[0]["collab_rev"]
    # 推一个更旧的时间戳 -> LWW 跳过, 不写
    write_specimens_to_local_db(project, [
        {"uid": "A", "collector": "旧值", "collab_updated_at": "2026-01-01T00:00:00"},
    ])
    row = get_local_specimens(project)[0]
    assert row["collector"] == "张三", "更旧的推送不该覆盖"
    assert row["collab_rev"] == rev_before, "没真的改的行 rev 不变(不进增量)"
