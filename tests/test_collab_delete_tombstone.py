"""tests/test_collab_delete_tombstone.py — 删除墓碑: 离线队友重连不复活编号.

Claude Code 2026-07-15 — codex 实测复现的分布式删除 bug:
  A 删除 UID -> 只从内存移除 + 通知在线设备; 离线的 B 没收到, 重连后把旧记录
  同步回来 -> UID 复活。
根因: 删除没留"墓碑"(tombstone)。正确做法: 删除 = 记一条持久墓碑
  (uid, deleted_at, deleted_by, workspace_id), 墓碑随同步传播、也持久化;
  合并时墓碑压制旧记录 -> 任何还留着旧记录的 peer 重连都不再复活。

区分(用户定稿):
  · 作废(void) = TaskStatus.VOID, 保留在历史、同步、不复用 —— 不是删除, 无墓碑。
  · 释放/删除(release/delete) = 墓碑, 离线重连也要删。
"""
from __future__ import annotations

from app.services.collab_store import TaskStore
from app.services.collab_types import TaskStatus


def test_delete_records_a_tombstone():
    store = TaskStore()
    store.create("GXFCG-001", workspace_id="ws-1")
    store.delete("GXFCG-001", deleted_by="张三", workspace_id="ws-1")
    assert not store.exists("GXFCG-001")
    tombs = {t["uid"]: t for t in store.list_tombstones()}
    assert "GXFCG-001" in tombs
    assert tombs["GXFCG-001"]["deleted_by"] == "张三"
    assert tombs["GXFCG-001"]["workspace_id"] == "ws-1"
    assert tombs["GXFCG-001"].get("deleted_at")


def test_tombstone_blocks_resurrection_from_stale_peer():
    """核心: 本地已删(有墓碑), 离线 peer 推来旧记录 -> 不复活。"""
    store = TaskStore()
    store.create("A", workspace_id="ws-1")
    # A 在 2026-05 被删
    store.delete("A", deleted_by="张三", workspace_id="ws-1")

    # 离线 B 重连, 推来它保留的旧 A(创建于更早)
    changed = store.merge_from_peer([
        {"uid": "A", "status": "created", "updatedAt": "2026-01-01T00:00:00",
         "workspaceId": "ws-1"},
    ])
    assert not store.exists("A"), "有墓碑时旧记录不得复活"
    assert changed == 0


def test_incoming_tombstone_deletes_local_task():
    """反向: 对方删了 A(删除发生在本地任务最后更新之后)-> 墓碑同步过来, 本地也删。"""
    store = TaskStore()
    # 本地有一条较旧的 A(用 merge 塞一个带旧 updatedAt 的记录, 模拟"很久前建的")
    store.merge_from_peer([
        {"uid": "A", "status": "created", "updatedAt": "2026-01-01T00:00:00",
         "workspaceId": "ws-1"},
    ])
    assert store.exists("A")
    # 对方在 2026-06(晚于本地任务)删了它 -> 墓碑推过来
    store.merge_from_peer(
        [],
        remote_tombstones=[{
            "uid": "A", "deleted_at": "2026-06-01T00:00:00",
            "deleted_by": "李四", "workspace_id": "ws-1",
        }],
    )
    assert not store.exists("A"), "删除晚于本地任务时, 收到墓碑必须删掉本地任务"
    assert "A" in {t["uid"] for t in store.list_tombstones()}


def test_newer_recreate_after_delete_wins_over_tombstone():
    """编号被删后又被明确重新创建(更新时间戳更新)-> 允许复用, 墓碑让位。"""
    store = TaskStore()
    store.create("A", workspace_id="ws-1")
    store.delete("A", deleted_by="张三", workspace_id="ws-1")
    # 有人在删除之后(更晚的 updatedAt)重新登记这个编号
    store.merge_from_peer([
        {"uid": "A", "status": "created", "updatedAt": "2099-01-01T00:00:00",
         "workspaceId": "ws-1"},
    ])
    assert store.exists("A"), "删除之后更晚的重新创建应能复用该编号"


def test_void_is_not_a_delete_no_tombstone():
    """作废是状态, 不是删除: 不产生墓碑, 记录仍在。"""
    store = TaskStore()
    store.create("A", workspace_id="ws-1")
    store.update_status("A", TaskStatus.VOID, force=True)
    assert store.exists("A"), "作废保留记录"
    assert store.get_task("A").status == TaskStatus.VOID
    assert "A" not in {t["uid"] for t in store.list_tombstones()}


def test_tombstones_persist_across_restart(tmp_path):
    """墓碑必须持久化 —— 否则重启后墓碑没了, 离线 peer 推来旧记录又复活。"""
    path = tmp_path / "collab_tasks.json"
    store = TaskStore(persist_path=str(path))
    store.create("A", workspace_id="ws-1")
    store.delete("A", deleted_by="张三", workspace_id="ws-1")

    reborn = TaskStore(persist_path=str(path))
    assert not reborn.exists("A")
    assert "A" in {t["uid"] for t in reborn.list_tombstones()}
    # 重启后依然压制复活
    reborn.merge_from_peer([
        {"uid": "A", "status": "created", "updatedAt": "2026-01-01T00:00:00"},
    ])
    assert not reborn.exists("A"), "重启后墓碑仍要压制复活"
