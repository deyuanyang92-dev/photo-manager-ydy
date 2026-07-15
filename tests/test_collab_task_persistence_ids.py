"""tests/test_collab_task_persistence_ids.py — 协作任务持久化 + 稳定工作区/项目 ID.

Claude Code 2026-07-15 — codex 验证指出的两个缺口(团队共享工作区多主同步收尾):
  A. TaskStore 只在内存, 全部电脑重启后任务目录变空 -> 加磁盘持久化。
  B. TaskRecord 只有 project_name(显示名) + 工作区路径, 没有稳定 workspace_id/
     project_id, 两个同名工作区会混 -> 给 TaskRecord 加稳定 id 字段。
"""
from __future__ import annotations

from app.services.collab_store import TaskStore
from app.services.collab_types import TaskRecord, TaskStatus


# ── B. 稳定 workspace_id / project_id ─────────────────────────────────────────

def test_task_record_has_stable_id_fields():
    t = TaskRecord(uid="GXFCG-001", workspace_id="ws-abc", project_id="proj-xyz")
    assert t.workspace_id == "ws-abc"
    assert t.project_id == "proj-xyz"


def test_task_record_roundtrips_ids_through_dict():
    t = TaskRecord(uid="A", workspace_id="ws-1", project_id="p-1",
                   project_name="广西项目")
    d = t.to_dict()
    assert d["workspaceId"] == "ws-1"
    assert d["projectId"] == "p-1"
    back = TaskRecord.from_dict(d)
    assert back.workspace_id == "ws-1"
    assert back.project_id == "p-1"
    assert back.project_name == "广西项目"


def test_from_dict_tolerates_old_peer_without_ids():
    """旧版 peer 推来的任务没有这俩字段 -> 默认空, 不报错(向后兼容)。"""
    old = {"uid": "A", "status": "created", "projectName": "老项目"}
    t = TaskRecord.from_dict(old)
    assert t.workspace_id is None
    assert t.project_id is None
    assert t.project_name == "老项目"


def test_store_create_records_stable_ids():
    store = TaskStore()
    store.create("A", assignee="张三", workspace_id="ws-9", project_id="p-9")
    t = store.get_task("A")
    assert t.workspace_id == "ws-9"
    assert t.project_id == "p-9"


# ── A. 持久化: 重启后任务清单不丢 ─────────────────────────────────────────────

def test_tasks_survive_restart(tmp_path):
    path = tmp_path / "collab_tasks.json"
    store = TaskStore(persist_path=str(path))
    store.create("GXFCG-001", assignee="张三", workspace_id="ws-1", project_id="p-1")
    store.create("GXFCG-002", assignee="李四", workspace_id="ws-1", project_id="p-1")
    store.update_status("GXFCG-001", TaskStatus.SHOOTING, force=True)

    # 模拟"全部电脑重启": 新建一个 store 指向同一个文件
    reborn = TaskStore(persist_path=str(path))
    uids = {t.uid for t in reborn.list_tasks()}
    assert uids == {"GXFCG-001", "GXFCG-002"}, "重启后任务清单必须完整恢复"
    t1 = reborn.get_task("GXFCG-001")
    assert t1.status == TaskStatus.SHOOTING, "状态也要恢复"
    assert t1.workspace_id == "ws-1", "稳定 id 也要恢复"


def test_no_persist_path_stays_in_memory_only(tmp_path):
    """不给 persist_path 时行为不变(纯内存), 向后兼容。"""
    store = TaskStore()
    store.create("A")
    assert store.exists("A")
    # 不给 persist_path -> 不落任何任务文件(tmp_path 里可能有 conftest 的其它文件,
    # 只断言我们没写 collab_tasks.json)
    assert not list(tmp_path.rglob("collab_tasks*.json"))


def test_delete_and_merge_are_persisted(tmp_path):
    path = tmp_path / "collab_tasks.json"
    store = TaskStore(persist_path=str(path))
    store.create("A", workspace_id="w", project_id="p")
    store.create("B", workspace_id="w", project_id="p")
    store.delete("A")
    # 合并一个远端任务进来也要落盘
    store.merge_from_peer([
        {"uid": "C", "status": "created", "updatedAt": "2026-01-01T00:00:00",
         "workspaceId": "w2", "projectId": "p2"},
    ])
    reborn = TaskStore(persist_path=str(path))
    uids = {t.uid for t in reborn.list_tasks()}
    assert uids == {"B", "C"}, "删除 + 合并都要持久化"
    assert reborn.get_task("C").workspace_id == "w2"
