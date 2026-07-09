"""collab_types.py — Shared datatypes & helpers for the collaboration subsystem.

Split out of collab_service.py so the task store / HTTP API / network threads
can each live in a focused module.  ``collab_service`` re-exports everything
here, so external imports (views, tests) keep working unchanged.
"""
from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


# ── Module-level helpers (defined early — used in dataclass field defaults) ───

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_local_ip() -> str:
    """Best-effort LAN IP (not loopback).  Falls back to 127.0.0.1."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


# ── Optional-dep lazy accessor ──────────────────────────────────────────────────
# httpx is the single optional collab network dependency.  Importing it once
# here (cached, thread-safe) replaces ~17 inline ``import httpx`` + ImportError
# guards that were scattered across the collab subsystem.  Call sites do
# ``httpx = get_httpx()`` and treat ``None`` as "not installed → degrade".
_HTTPX_MODULE = None
_HTTPX_PROBED = False
_HTTPX_LOCK = threading.Lock()


def get_httpx():
    """Return the httpx module, or None if it is not installed.

    Cached after the first call.  CPython's import is already thread-safe and
    idempotent; the lock only avoids redundant re-import probes.  Returning the
    real module object means ``unittest.mock.patch("httpx.get")`` etc. still
    patch the same object the call sites use.
    """
    global _HTTPX_MODULE, _HTTPX_PROBED
    if _HTTPX_PROBED:
        return _HTTPX_MODULE
    with _HTTPX_LOCK:
        if not _HTTPX_PROBED:
            try:
                import httpx as _h  # noqa: PLC0415
                _HTTPX_MODULE = _h
            except ImportError:
                _HTTPX_MODULE = None
            _HTTPX_PROBED = True
    return _HTTPX_MODULE


# ── Task state machine ────────────────────────────────────────────────────────

class TaskStatus(str, Enum):
    """Collab task states (mirrors server.js COLLAB_STATUSES)."""
    CREATED    = "created"
    ASSIGNED   = "assigned"
    SHOOTING   = "shooting"
    SHOT_DONE  = "shot_done"
    ORGANIZING = "organizing"
    DONE       = "done"
    VOID       = "void"
    CONFLICT   = "conflict"


_VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.CREATED:    {TaskStatus.ASSIGNED, TaskStatus.SHOOTING, TaskStatus.VOID, TaskStatus.CONFLICT},
    TaskStatus.ASSIGNED:   {TaskStatus.SHOOTING, TaskStatus.VOID, TaskStatus.CONFLICT},
    TaskStatus.SHOOTING:   {TaskStatus.SHOT_DONE, TaskStatus.VOID, TaskStatus.CONFLICT},
    TaskStatus.SHOT_DONE:  {TaskStatus.ORGANIZING, TaskStatus.VOID, TaskStatus.CONFLICT},
    TaskStatus.ORGANIZING: {TaskStatus.DONE, TaskStatus.VOID, TaskStatus.CONFLICT},
    TaskStatus.DONE:       {TaskStatus.VOID},
    TaskStatus.VOID:       set(),
    TaskStatus.CONFLICT:   {TaskStatus.CREATED, TaskStatus.VOID},
}


def is_valid_transition(from_status: TaskStatus, to_status: TaskStatus) -> bool:
    """Return True if the state transition is allowed."""
    return to_status in _VALID_TRANSITIONS.get(from_status, set())


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class TaskRecord:
    """Minimal task record synced across peers."""
    uid: str
    status: TaskStatus = TaskStatus.CREATED
    assignee: Optional[str] = None          # operator name
    device_id: Optional[str] = None
    project_name: Optional[str] = None
    created_at: str = field(default_factory=lambda: _now_iso())
    updated_at: str = field(default_factory=lambda: _now_iso())

    def to_dict(self) -> dict:
        return {
            "uid":         self.uid,
            "status":      self.status.value,
            "assignee":    self.assignee,
            "deviceId":    self.device_id,
            "projectName": self.project_name,
            "createdAt":   self.created_at,
            "updatedAt":   self.updated_at,
        }

    @staticmethod
    def from_dict(d: dict) -> "TaskRecord":
        return TaskRecord(
            uid=d["uid"],
            status=TaskStatus(d.get("status", "created")),
            assignee=d.get("assignee"),
            device_id=d.get("deviceId"),
            project_name=d.get("projectName"),
            created_at=d.get("createdAt", _now_iso()),
            updated_at=d.get("updatedAt", _now_iso()),
        )


@dataclass
class PeerInfo:
    """Discovered or manually added LAN peer."""
    ip: str
    port: int
    hostname: str = ""
    project_name: str = ""
    project_id: str = ""
    group_code: str = ""          # session code; only matching peers sync
    session_name: str = ""        # human-readable session label, e.g. "张三的会话"
    last_seen: float = field(default_factory=time.time)
    latency_ms: Optional[float] = None
    clock_skew_ms: Optional[float] = None   # local_time - peer serverTime (ms)
    reachable: Optional[bool] = None        # can I reach this peer?
    reachback_ok: Optional[bool] = None     # can this peer reach me back?
    manual: bool = False          # True = added via manual IP, not mDNS
    shared_projects: list = field(default_factory=list)  # [{projectId, projectName}, ...]

    @property
    def base_url(self) -> str:
        return f"http://{self.ip}:{self.port}"


# ── Self-diagnostics ──────────────────────────────────────────────────────────

@dataclass
class Diagnostic:
    """One collaboration health finding, novice-readable (Chinese)."""
    code: str                       # machine key, e.g. "deps_missing"
    level: str                      # "ok" | "warn" | "error"
    title: str                      # short Chinese title
    detail: str = ""                # what / why
    fix: str = ""                   # how to fix (plain Chinese)
    action: Optional[str] = None    # optional one-click action key


def _missing_deps() -> list[str]:
    """Return the collaboration packages that are not importable."""
    import importlib.util
    need = ["fastapi", "uvicorn", "zeroconf", "httpx"]
    return [n for n in need if importlib.util.find_spec(n) is None]


# ── Offline queue / photo-index records ──────────────────────────────────────

@dataclass
class OfflineDraft:
    """Queued create-task that failed to reach at least one peer (network unavailable).

    Mirrors web ``collabMarkOfflineDraft`` / ``collabRetryOfflineDrafts``:
        loadCollabOfflineDrafts()   → CollabService.load_offline_drafts()
        saveCollabOfflineDrafts()   → CollabService.save_offline_drafts()
        collabMarkOfflineDraft()    → CollabService.mark_offline_draft()
        collabRetryOfflineDrafts()  → CollabService.retry_offline_drafts()
    """
    uid: str
    assignee: Optional[str]
    device_id: Optional[str]
    queued_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return {
            "uid":       self.uid,
            "assignee":  self.assignee,
            "deviceId":  self.device_id,
            "queuedAt":  self.queued_at,
        }

    @staticmethod
    def from_dict(d: dict) -> "OfflineDraft":
        return OfflineDraft(
            uid=d["uid"],
            assignee=d.get("assignee"),
            device_id=d.get("deviceId"),
            queued_at=d.get("queuedAt", _now_iso()),
        )


@dataclass
class PhotoIndexRecord:
    """Photo-index entry reported after helicon/archive completion.

    Mirrors web ``collabPostPhotoIndex(uid, kind)``:
        kind = "jpg" | "tiff" | "zip"
    """
    uid: str
    kind: str          # "jpg" | "tiff" | "zip"
    count: int = 0
    reported_at: str = field(default_factory=_now_iso)
    device_id: str = ""
    group_code: str = ""
    project_id: str = ""

    def to_dict(self) -> dict:
        data = {
            "uid":        self.uid,
            "kind":       self.kind,
            "count":      self.count,
            "reportedAt": self.reported_at,
            "deviceId":   self.device_id,
        }
        if self.group_code:
            data["groupCode"] = self.group_code
        if self.project_id:
            data["projectId"] = self.project_id
        return data
