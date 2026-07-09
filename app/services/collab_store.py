"""collab_store.py — Thread-safe in-memory task store for collaboration.

Used both by the FastAPI server (background thread) and the Qt UI (main
thread).  All mutations are protected by a threading.Lock.
"""
from __future__ import annotations

import threading
from typing import Optional

from app.services.collab_types import (
    TaskRecord,
    TaskStatus,
    _now_iso,
    is_valid_transition,
)


class TaskStore:
    """Thread-safe in-memory store for collab tasks.

    Used both by the FastAPI server (background thread) and the Qt UI (main
    thread).  All mutations are protected by a threading.Lock.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._lock = threading.Lock()

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
               project_name: Optional[str] = None) -> TaskRecord:
        """Create task.  Raises ValueError if UID already exists (local 409).

        Callers broadcasting to remote peers must also check each peer.
        """
        with self._lock:
            if uid in self._tasks:
                raise ValueError(f"409: UID '{uid}' already exists locally")
            task = TaskRecord(
                uid=uid,
                assignee=assignee,
                device_id=device_id,
                project_name=project_name,
            )
            self._tasks[uid] = task
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
            return task

    def merge_from_peer(self, remote_tasks: list[dict],
                        overwrites_out: list | None = None, *,
                        trust_remote_clock: bool = True,
                        skew_guarded_out: list | None = None) -> int:
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
            for rd in remote_tasks:
                uid = rd.get("uid")
                if not uid:
                    continue
                remote = TaskRecord.from_dict(rd)
                local = self._tasks.get(uid)
                if local is None:
                    self._tasks[uid] = remote
                    changed += 1
                    continue
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
        return changed

    def delete(self, uid: str) -> None:
        """Remove a task entirely so its UID becomes reclaimable.  Idempotent."""
        with self._lock:
            self._tasks.pop(uid, None)

    def replace_all(self, tasks: list[TaskRecord]) -> None:
        """Overwrite store (used in tests or full-sync scenarios)."""
        with self._lock:
            self._tasks = {t.uid: t for t in tasks}

    def clear(self) -> None:
        with self._lock:
            self._tasks.clear()
