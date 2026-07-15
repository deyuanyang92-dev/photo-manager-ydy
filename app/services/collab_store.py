"""collab_store.py — Thread-safe in-memory task store for collaboration.

Used both by the FastAPI server (background thread) and the Qt UI (main
thread).  All mutations are protected by a threading.Lock.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.services.collab_types import (
    TaskRecord,
    TaskStatus,
    _now_iso,
    is_valid_transition,
)

logger = logging.getLogger(__name__)


# Two devices claiming the SAME uid within this wall-clock window are treated
# as a distributed-claim collision (best-effort; P2P has no coordinator so the
# race itself cannot be prevented — only detected and surfaced).
CLAIM_COLLISION_WINDOW_S = 30.0


def _created_within(a: str, b: str, window_s: float) -> bool:
    """True if two ISO-8601 created_at stamps are within *window_s* of each other."""
    try:
        ta = datetime.fromisoformat(str(a)).timestamp()
        tb = datetime.fromisoformat(str(b)).timestamp()
    except (ValueError, TypeError):
        return False
    return abs(ta - tb) <= window_s


class TaskStore:
    """Thread-safe in-memory store for collab tasks.

    Used both by the FastAPI server (background thread) and the Qt UI (main
    thread).  All mutations are protected by a threading.Lock.
    """

    def __init__(self, persist_path: Optional[str] = None) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        # Claude Code 修改 2026-07-15 — 删除墓碑(codex 复现的离线复活 bug): 删一个
        # 编号 = 记一条持久墓碑, 而不是单纯移除内存记录。墓碑随同步传播、也落盘;
        # 合并时压制"更早"的旧记录, 任何还留着旧记录的 peer 重连都不再复活。
        # uid -> {"uid","deleted_at","deleted_by","workspace_id"}
        self._tombstones: dict[str, dict] = {}
        self._lock = threading.Lock()
        # Claude Code 修改 2026-07-15 — codex 验证指出任务只在内存, 全部电脑重启后
        # 任务目录变空。给一个可选磁盘落点(团队级 collab_tasks.json), 有则启动时
        # 载入、每次改动后原子落盘。不给则纯内存(向后兼容)。
        self._persist_path = str(persist_path) if persist_path else None
        if self._persist_path:
            self._load_from_disk()

    # ── Persistence ───────────────────────────────────────────────────────
    def _load_from_disk(self) -> None:
        path = Path(self._persist_path)
        if not path.is_file():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("collab: 载入任务清单失败(忽略, 从空开始): %s", exc)
            return
        items = raw.get("tasks", raw) if isinstance(raw, dict) else raw
        if isinstance(items, list):
            for d in items:
                try:
                    rec = TaskRecord.from_dict(d)
                    self._tasks[rec.uid] = rec
                except Exception:  # noqa: BLE001 — 单条坏记录跳过, 不拖垮整份
                    continue
        # Claude Code 修改 2026-07-15 — 墓碑也载入(重启后仍压制复活)
        tombs = raw.get("tombstones", []) if isinstance(raw, dict) else []
        if isinstance(tombs, list):
            for t in tombs:
                uid = (t or {}).get("uid")
                if uid:
                    self._tombstones[uid] = dict(t)

    def _save_to_disk_locked(self) -> None:
        """在持锁状态下调用: 把全部任务原子写盘。失败静默(不能因存盘失败拖垮同步)。"""
        if not self._persist_path:
            return
        path = Path(self._persist_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "tasks": [t.to_dict() for t in self._tasks.values()],
                "tombstones": list(self._tombstones.values()),
            }
            tmp = path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            logger.debug("collab: 任务清单落盘失败: %s", exc)

    # ── Queries ───────────────────────────────────────────────────────────

    def list_tasks(self) -> list[TaskRecord]:
        with self._lock:
            return list(self._tasks.values())

    def get_task(self, uid: str) -> Optional[TaskRecord]:
        with self._lock:
            return self._tasks.get(uid)

    def exists(self, uid: str) -> bool:
        with self._lock:
            return uid in self._tasks

    # ── Mutations ─────────────────────────────────────────────────────────

    def create(self, uid: str, assignee: Optional[str] = None,
               device_id: Optional[str] = None,
               project_name: Optional[str] = None,
               workspace_id: Optional[str] = None,
               project_id: Optional[str] = None) -> TaskRecord:
        """Create task.  Raises ValueError if UID already exists (local 409).

        Callers broadcasting to remote peers must also check each peer.

        Claude Code 修改 2026-07-15 — 带上稳定 workspace_id / project_id(重名工作区
        不再混淆), 并落盘(重启后仍在)。
        """
        with self._lock:
            if uid in self._tasks:
                raise ValueError(f"409: UID '{uid}' already exists locally")
            task = TaskRecord(
                uid=uid,
                assignee=assignee,
                device_id=device_id,
                project_name=project_name,
                workspace_id=workspace_id,
                project_id=project_id,
            )
            self._tasks[uid] = task
            self._save_to_disk_locked()
            return task

    def update_status(self, uid: str, to_status: TaskStatus,
                      assignee: Optional[str] = None,
                      force: bool = False) -> TaskRecord:
        """Update task status.  Raises ValueError on invalid transition or unknown UID.

        ``force=True`` bypasses the state machine — for explicit human marking
        (sidebar phase dots / batch-bar pills), which mirrors the web oracle's
        free assignment (app.js:3303).  Programmatic/auto callers keep the
        default strict transition checking.
        """
        with self._lock:
            task = self._tasks.get(uid)
            if task is None:
                raise ValueError(f"Unknown UID: {uid}")
            if not force and not is_valid_transition(task.status, to_status):
                raise ValueError(
                    f"Invalid transition: {task.status} → {to_status}"
                )
            task.status = to_status
            if assignee is not None:
                task.assignee = assignee
            task.updated_at = _now_iso()
            self._save_to_disk_locked()  # Claude Code 2026-07-15 — 状态改动落盘
            return task

    def merge_from_peer(self, remote_tasks: list[dict],
                        overwrites_out: list | None = None, *,
                        trust_remote_clock: bool = True,
                        skew_guarded_out: list | None = None,
                        claim_collisions_out: list | None = None,
                        remote_tombstones: list | None = None) -> int:
        """Merge peer task list; newer updated_at wins.  Returns changed count.

        If *overwrites_out* is supplied, dicts describing each case where a
        local task's status was silently replaced by a remote value are
        appended: ``{"uid": ..., "old_status": ..., "new_status": ...}``.

        Clock-skew guard: LWW relies on wall-clock ``updated_at``.  When
        *trust_remote_clock* is False (caller measured the peer's clock to skew
        beyond the trust threshold), a remote record that is wall-clock-newer
        is NOT allowed to overwrite a *differing* local status — the local
        value is kept (conservative: local wins under untrusted ordering).
        Each such skipped case is appended to *skew_guarded_out* (same dict
        shape as *overwrites_out*).  Same-status refreshes are still applied
        (harmless).  Default ``trust_remote_clock=True`` preserves legacy LWW.
        """
        changed = 0
        with self._lock:
            # Claude Code 修改 2026-07-15 — 先应用收到的墓碑: 对方删了某编号, 我这边
            # 也删(若墓碑比本地任务新), 并留下墓碑压制后续旧记录。
            for td in (remote_tombstones or []):
                t_uid = (td or {}).get("uid")
                if not t_uid:
                    continue
                t_at = str(td.get("deleted_at") or "")
                existing = self._tombstones.get(t_uid)
                # 保留最新的墓碑(删除时刻更晚的赢)
                if existing is None or t_at > str(existing.get("deleted_at") or ""):
                    self._tombstones[t_uid] = dict(td)
                local_t = self._tasks.get(t_uid)
                if local_t is not None and (not t_at or t_at >= str(local_t.updated_at or "")):
                    # 墓碑不早于本地任务 -> 删除本地任务
                    self._tasks.pop(t_uid, None)
                    changed += 1

            for rd in remote_tasks:
                uid = rd.get("uid")
                if not uid:
                    continue
                remote = TaskRecord.from_dict(rd)
                # Claude Code 修改 2026-07-15 — 墓碑压制复活: 本地已有墓碑且墓碑不早于
                # 这条记录的 updatedAt -> 不复活(离线 peer 推回旧记录时的核心防线)。
                # 但记录若比墓碑更晚(删除之后又明确重新创建)-> 放行并清掉墓碑(可复用)。
                tomb = self._tombstones.get(uid)
                if tomb is not None:
                    tomb_at = str(tomb.get("deleted_at") or "")
                    if tomb_at and str(remote.updated_at or "") <= tomb_at:
                        continue  # 旧记录, 压制, 不复活
                    # 删除之后的新登记 -> 让位, 清墓碑
                    self._tombstones.pop(uid, None)
                local = self._tasks.get(uid)
                if local is None:
                    self._tasks[uid] = remote
                    changed += 1
                    continue
                # Best-effort distributed-claim collision detection (P2P has no
                # coordinator): same uid claimed by two different devices within
                # a short window → record it.  This only DETECTS the inherent
                # race; it does not prevent it.  Fires regardless of who is
                # wall-clock newer so the human can resolve a split-brain.
                if (claim_collisions_out is not None
                        and local.device_id and remote.device_id
                        and local.device_id != remote.device_id
                        and _created_within(local.created_at, remote.created_at,
                                            CLAIM_COLLISION_WINDOW_S)):
                    claim_collisions_out.append({
                        "uid": uid,
                        "local_device": local.device_id,
                        "remote_device": remote.device_id,
                    })
                if not (remote.updated_at > local.updated_at):
                    continue                       # local same/newer — legacy skip
                # Remote is wall-clock newer.  Under large measured clock skew
                # that ordering is unreliable: refuse to clobber a differing
                # local status; record and keep local.  (legacy path: the
                # ``if local is None or remote.updated_at > local.updated_at``
                # branch below used to overwrite unconditionally — kept as the
                # trust_remote_clock=True behaviour.)
                if (not trust_remote_clock and local.status != remote.status):
                    if skew_guarded_out is not None:
                        skew_guarded_out.append({
                            "uid": uid,
                            "old_status": local.status.value,
                            "new_status": remote.status.value,
                        })
                    continue
                if (
                    overwrites_out is not None
                    and local.status != remote.status
                ):
                    overwrites_out.append({
                        "uid": uid,
                        "old_status": local.status.value,
                        "new_status": remote.status.value,
                    })
                self._tasks[uid] = remote
                changed += 1
            # Claude Code 2026-07-15 — 合并进任何远端任务后落盘(重启后同队看得到)
            if changed:
                self._save_to_disk_locked()
        return changed

    def delete(self, uid: str, *, deleted_by: Optional[str] = None,
               workspace_id: Optional[str] = None) -> None:
        """Remove a task and record a persistent tombstone.  Idempotent.

        Claude Code 修改 2026-07-15 — codex 复现的离线复活修复: 删除不再只是移除
        内存记录, 而是留一条墓碑(uid, deleted_at, deleted_by, workspace_id)。墓碑
        持久化 + 随同步传播, 让离线 peer 重连也删、且旧记录推回来不复活。
        """
        with self._lock:
            existed = self._tasks.pop(uid, None) is not None
            # 墓碑时间用现在 —— 删除动作发生的时刻(LWW 里压制更早的旧记录)
            self._tombstones[uid] = {
                "uid": uid,
                "deleted_at": _now_iso(),
                "deleted_by": deleted_by,
                "workspace_id": workspace_id,
            }
            self._save_to_disk_locked()
            _ = existed

    def list_tombstones(self) -> list[dict]:
        with self._lock:
            return [dict(t) for t in self._tombstones.values()]

    def replace_all(self, tasks: list[TaskRecord]) -> None:
        """Overwrite store (used in tests or full-sync scenarios)."""
        with self._lock:
            self._tasks = {t.uid: t for t in tasks}
            self._save_to_disk_locked()

    def clear(self) -> None:
        with self._lock:
            self._tasks.clear()
            self._save_to_disk_locked()
