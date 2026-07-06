"""collab_service.py — P2P LAN collaboration service for the PyQt6 workbench.

Architecture (confirmed by user 2026-06-02, oracle: collab.md § Desktop GUI):

  CollabService spawns two QThread workers:
    • CollabServerThread  — FastAPI + uvicorn embedded server (port 5050)
    • CollabDiscoveryThread — zeroconf mDNS registration + peer discovery

  A QTimer (5 s) drives CollabSyncWorker which does HTTP pulls from known
  peers using httpx (synchronous, runs in the same sync slot, cheap).

  Each peer exposes:
    GET  /api/node/info       → {hostname, projectName, projectId, lanIp, port}
    GET  /api/node/health     → {"ok": true}
    GET  /api/collab/tasks    → list[TaskRecord]
    POST /api/collab/tasks/create   → 201 | 409 Conflict
    POST /api/collab/tasks/update-status
    GET  /api/collab/specimens      → list[SpecimenRecord]
    POST /api/collab/specimens/push → accept push from peer
    GET  /api/collab/files/manifest → media manifest scoped to UID(s)
    GET  /api/collab/files/download → project-relative media download

Conflict (409) policy:
    Creating a UID that already exists on *any* online peer returns HTTP 409.
    The creator must abandon or rename the UID.

Manual IP fallback:
    mDNS may fail across VLANs or on Windows Firewall-strict networks.
    Call CollabService.add_manual_peer(ip, port) to hard-add a peer endpoint.

Scope:
    L1: specimenTasks (UID + status + assignee).
    L2: specimen JSON pushed on create/update.
    L3: basic media manifest + LAN file download; higher-level conflict UI is
        handled by app.services.collab_file_sync and the workbench.

NOTE: mDNS discovery and the HTTP sync require real network / two machines.
Tests that exercise these are marked with ``@pytest.mark.needs_network`` and
are skipped in the default CI run.  All pure-logic tests (409 conflict
detection, task state-machine, sync merge) run offline with mocks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from app.models.activity_log import ActivityEntry, ActivityLog

logger = logging.getLogger(__name__)


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


# ── In-memory task store (single project scope) ───────────────────────────────

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
                        overwrites_out: list | None = None) -> int:
        """Merge peer task list; newer updated_at wins.  Returns changed count.

        If *overwrites_out* is supplied, dicts describing each case where a
        local task's status was silently replaced by a remote value are
        appended: ``{"uid": ..., "old_status": ..., "new_status": ...}``.
        """
        changed = 0
        with self._lock:
            for rd in remote_tasks:
                uid = rd.get("uid")
                if not uid:
                    continue
                remote = TaskRecord.from_dict(rd)
                local = self._tasks.get(uid)
                if local is None or remote.updated_at > local.updated_at:
                    if (
                        overwrites_out is not None
                        and local is not None
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


# ── FastAPI application ───────────────────────────────────────────────────────

def _build_fastapi_app(store: TaskStore, node_info_fn: Callable[[], dict],
                       activity_log: Optional[ActivityLog] = None,
                       file_manifest_fn: Optional[Callable[[Optional[list[str]]], dict]] = None,
                       file_path_fn: Optional[Callable[[str], Path]] = None,
                       pairing_request_fn: Optional[Callable[[str, str, str], None]] = None,
                       pairing_accept_fn: Optional[Callable[[str, str], None]] = None,
                       specimen_provider_fn: Optional[Callable[[Optional[str]], list]] = None,
                       specimen_writer_fn: Optional[Callable[[list], int]] = None) -> Any:
    """Build and return the FastAPI app.  Imported lazily to avoid startup cost.

    The fastapi names are bound into module globals (``global`` below) so that
    the nested endpoint functions' ``request: Request`` annotations resolve via
    ``typing.get_type_hints`` (which reads the function's ``__globals__``).
    A purely function-local import leaves them unresolvable and FastAPI then
    mis-reads ``request`` as a query parameter → every POST 422s.
    """
    global FastAPI, HTTPException, Request, JSONResponse, FileResponse
    try:
        from fastapi import FastAPI, HTTPException, Request
        from fastapi.responses import FileResponse, JSONResponse
    except ImportError as exc:
        raise ImportError("fastapi is required for CollabService") from exc

    app = FastAPI(title="Specimen Collab Node", version="1.0.0")

    @app.get("/api/node/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/api/node/info")
    async def node_info() -> dict:
        return node_info_fn()

    @app.get("/api/collab/tasks")
    async def list_tasks() -> list:
        return [t.to_dict() for t in store.list_tasks()]

    @app.post("/api/collab/tasks/create")
    async def create_task(request: Request) -> JSONResponse:
        body = await request.json()
        uid = body.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="uid required")
        # Group guard: only accept claims from our own collaboration group.
        # Empty local group = not participating → reject everyone.
        local_group = node_info_fn().get("groupCode", "")
        if not local_group or body.get("groupCode", "") != local_group:
            raise HTTPException(status_code=403, detail="collaboration group mismatch")
        try:
            task = store.create(
                uid=uid,
                assignee=body.get("assignee"),
                device_id=body.get("deviceId"),
                project_name=body.get("projectName"),
            )
        except ValueError as exc:
            # Local 409 — UID exists on this node
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(content=task.to_dict(), status_code=201)

    @app.post("/api/collab/tasks/update-status")
    async def update_task_status(request: Request) -> dict:
        body = await request.json()
        uid = body.get("uid")
        status_raw = body.get("status")
        if not uid or not status_raw:
            raise HTTPException(status_code=400, detail="uid and status required")
        local_group = node_info_fn().get("groupCode", "")
        if not local_group or body.get("groupCode", "") != local_group:
            raise HTTPException(status_code=403, detail="collaboration group mismatch")
        try:
            to_status = TaskStatus(status_raw)
            task = store.update_status(uid, to_status, assignee=body.get("assignee"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return task.to_dict()

    @app.post("/api/node/reachback")
    async def reachback(request: Request) -> dict:
        """Test whether the *caller* is reachable from this node's side.

        The caller passes its own {ip, port}; we try to GET its /api/node/health
        and report back.  Lets a peer detect a one-way firewall block (it can
        reach us, but we cannot reach it).
        """
        body = await request.json()
        ip = body.get("ip")
        port = body.get("port")
        if not ip or not port:
            raise HTTPException(status_code=400, detail="ip and port required")
        reachable = False
        try:
            import httpx
            r = httpx.get(f"http://{ip}:{port}/api/node/health", timeout=3.0)
            reachable = r.status_code == 200
        except Exception:  # noqa: BLE001
            reachable = False
        return {"reachable": reachable}

    @app.post("/api/collab/tasks/release")
    async def release_task(request: Request) -> dict:
        """Release (delete) a UID claim so it becomes reclaimable by anyone."""
        body = await request.json()
        uid = body.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="uid required")
        local_group = node_info_fn().get("groupCode", "")
        if not local_group or body.get("groupCode", "") != local_group:
            raise HTTPException(status_code=403, detail="collaboration group mismatch")
        store.delete(uid)
        return {"ok": True, "uid": uid}

    # ── Pairing (zero-config peer connection) ────────────────────────────────

    @app.post("/api/collab/pairing/request")
    async def pairing_request(request: Request) -> dict:
        """Incoming pairing request from another device.

        Body: {fromIp, fromHostname, groupCode}
        The receiving app shows a confirmation dialog; if accepted the devices
        adopt a shared group code and begin syncing.
        """
        body = await request.json()
        from_ip = body.get("fromIp", "")
        from_hostname = body.get("fromHostname", "未知设备")
        their_code = body.get("groupCode", "")
        if pairing_request_fn is not None:
            pairing_request_fn(from_ip, from_hostname, their_code)
        return {"ok": True, "status": "pending"}

    @app.post("/api/collab/pairing/accept")
    async def pairing_accept(request: Request) -> dict:
        """Notification that the remote device has accepted our pairing request."""
        body = await request.json()
        from_ip = body.get("fromIp", "")
        from_hostname = body.get("fromHostname", "未知设备")
        adopted_code = body.get("groupCode", "")
        if adopted_code:
            node_info_fn()  # ensure state is current
        if pairing_accept_fn is not None:
            pairing_accept_fn(from_ip, from_hostname)
        return {"ok": True, "groupCode": adopted_code}

    @app.post("/api/collab/photo-index")
    async def receive_photo_index(request: Request) -> dict:
        """Receive photo-index report from a peer after helicon/archive completion.

        Mirrors web collabPostPhotoIndex — peers call this to inform us that
        their specimen has jpg/tiff/zip files ready.  We acknowledge and let
        the UI subscribe to signals for richer handling.
        """
        body = await request.json()
        local_group = node_info_fn().get("groupCode", "")
        if not local_group or body.get("groupCode", "") != local_group:
            raise HTTPException(status_code=403, detail="collaboration group mismatch")
        uid = body.get("uid", "")
        kind = body.get("kind", "")
        count = int(body.get("count", 0))
        logger.debug("collab: photo-index received uid=%s kind=%s count=%d",
                     uid, kind, count)
        return {"ok": True, "uid": uid, "kind": kind, "count": count}

    @app.get("/api/collab/files/manifest")
    async def file_manifest(groupCode: str = "", projectId: str = "", uids: str = "") -> dict:
        """Return project media manifest for selected UIDs or the whole project."""
        local_group = node_info_fn().get("groupCode", "")
        if not local_group or groupCode != local_group:
            raise HTTPException(status_code=403, detail="collaboration group mismatch")
        local_project = str(node_info_fn().get("projectId", "") or "")
        if not local_project or projectId != local_project:
            raise HTTPException(status_code=403, detail="project identity mismatch")
        if file_manifest_fn is None:
            return {"files": []}
        uid_list = [u.strip() for u in str(uids or "").split(",") if u.strip()]
        try:
            return file_manifest_fn(uid_list or None)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/collab/files/download")
    async def download_file(path: str = "", groupCode: str = "", projectId: str = "") -> Any:
        """Download one project-relative media file."""
        local_group = node_info_fn().get("groupCode", "")
        if not local_group or groupCode != local_group:
            raise HTTPException(status_code=403, detail="collaboration group mismatch")
        local_project = str(node_info_fn().get("projectId", "") or "")
        if not local_project or projectId != local_project:
            raise HTTPException(status_code=403, detail="project identity mismatch")
        if file_path_fn is None:
            raise HTTPException(status_code=404, detail="file sync unavailable")
        try:
            resolved = file_path_fn(path)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not resolved.is_file():
            raise HTTPException(status_code=404, detail="file not found")
        return FileResponse(str(resolved), filename=resolved.name)

    @app.post("/api/collab/specimens/push")
    async def receive_specimen_push(request: Request) -> dict:
        """Accept specimen records pushed from a peer; writes to local DB."""
        body = await request.json()
        local_group = node_info_fn().get("groupCode", "")
        if not local_group or body.get("groupCode", "") != local_group:
            raise HTTPException(status_code=403, detail="collaboration group mismatch")
        specimens = body.get("specimens", [])
        if not isinstance(specimens, list):
            raise HTTPException(status_code=400, detail="specimens must be a list")
        written = 0
        if specimen_writer_fn is not None and specimens:
            written = specimen_writer_fn(specimens)
        logger.debug("collab: received %d specimen(s) from peer", written)
        return {"ok": True, "written": written}

    @app.get("/api/collab/specimens")
    async def list_specimens() -> list:
        """Return local specimen records for peer sync."""
        if specimen_provider_fn is None:
            return []
        return specimen_provider_fn(None)

    @app.get("/api/collab/activity")
    async def get_activity() -> list:
        """Return recent activity entries from this node."""
        if activity_log is None:
            return []
        return activity_log.to_dicts()

    @app.post("/api/collab/activity")
    async def receive_activity(request: Request) -> dict:
        """Receive an activity entry pushed from a peer (best-effort).

        Double guard: LAN IP check (defense-in-depth) + matching groupCode.
        """
        if activity_log is None:
            return {"ok": True}
        client_host = request.client.host if request.client else ""
        import ipaddress
        try:
            addr = ipaddress.ip_address(client_host)
            if not addr.is_private and client_host not in ("127.0.0.1", "::1"):
                raise HTTPException(status_code=403, detail="sender not in LAN")
        except ValueError:
            raise HTTPException(status_code=403, detail="invalid sender address")
        body = await request.json()
        local_group = node_info_fn().get("groupCode", "")
        if not local_group or body.get("groupCode", "") != local_group:
            raise HTTPException(status_code=403, detail="collaboration group mismatch")
        entry = ActivityEntry.from_dict(body)
        activity_log.append(entry)
        return {"ok": True}

    return app


# ── Server thread ─────────────────────────────────────────────────────────────

class CollabServerThread(QThread):
    """Runs FastAPI + uvicorn in a background QThread.

    Signals
    -------
    started_on_port(int):   emitted when server is listening.
    server_error(str):      emitted if startup fails.
    """

    started_on_port = pyqtSignal(int)
    server_error = pyqtSignal(str)

    def __init__(self, store: TaskStore, node_info_fn: Callable[[], dict],
                 preferred_port: int = 5050,
                 activity_log: Optional[ActivityLog] = None,
                 file_manifest_fn: Optional[Callable[[Optional[list[str]]], dict]] = None,
                 file_path_fn: Optional[Callable[[str], Path]] = None,
                 pairing_request_fn: Optional[Callable[[str, str, str], None]] = None,
                 pairing_accept_fn: Optional[Callable[[str, str], None]] = None,
                 specimen_provider_fn: Optional[Callable[[Optional[str]], list]] = None,
                 specimen_writer_fn: Optional[Callable[[list], int]] = None) -> None:
        super().__init__()
        self._store = store
        self._node_info_fn = node_info_fn
        self._preferred_port = preferred_port
        self._activity_log = activity_log
        self._file_manifest_fn = file_manifest_fn
        self._file_path_fn = file_path_fn
        self._pairing_request_fn = pairing_request_fn
        self._pairing_accept_fn = pairing_accept_fn
        self._specimen_provider_fn = specimen_provider_fn
        self._specimen_writer_fn = specimen_writer_fn
        self._actual_port: Optional[int] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server: Optional[Any] = None  # uvicorn.Server, set in run()

    @property
    def actual_port(self) -> Optional[int]:
        return self._actual_port

    def _find_free_port(self, start: int) -> int:
        port = start
        while port < start + 20:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("127.0.0.1", port)) != 0:
                    return port
            port += 1
        raise OSError("No free port found near %d" % start)

    def run(self) -> None:
        try:
            import uvicorn
        except ImportError:
            self.server_error.emit("uvicorn not installed")
            return

        try:
            port = self._find_free_port(self._preferred_port)
        except OSError as exc:
            self.server_error.emit(str(exc))
            return

        self._actual_port = port
        app = _build_fastapi_app(
            self._store,
            self._node_info_fn,
            self._activity_log,
            file_manifest_fn=self._file_manifest_fn,
            file_path_fn=self._file_path_fn,
            pairing_request_fn=self._pairing_request_fn,
            pairing_accept_fn=self._pairing_accept_fn,
            specimen_provider_fn=self._specimen_provider_fn,
            specimen_writer_fn=self._specimen_writer_fn,
        )

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=port,
            loop="asyncio",
            log_level="warning",
        )
        server = uvicorn.Server(config)
        self._server = server

        # Emit port once server startup is complete (uvicorn calls startup first)
        async def _serve() -> None:
            await server.serve()

        async def _runner() -> None:
            # Small delay then emit so callers know the port
            serve_task = self._loop.create_task(_serve())  # type: ignore[union-attr]
            await asyncio.sleep(0.3)
            self.started_on_port.emit(port)
            await serve_task

        try:
            self._loop.run_until_complete(_runner())
        except Exception as exc:  # noqa: BLE001
            self.server_error.emit(str(exc))
        finally:
            self._loop.close()

    def stop(self) -> None:
        # Ask uvicorn to shut down gracefully (closes its listening sockets +
        # in-flight handlers via should_exit). Force-stopping the asyncio loop
        # instead strands the serve() coroutine and can leave the socket in
        # CLOSE_WAIT — and on Windows that can keep this QThread alive long
        # enough to hold the SQLite DB handle past exit. Fallback to a hard
        # loop.stop() only if the server never started.
        srv = getattr(self, "_server", None)
        loop = self._loop
        if srv is not None and loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(setattr, srv, "should_exit", True)
            except RuntimeError:  # loop closed between the check and the call
                pass
        elif loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
        self.quit()
        self.wait(5000)
        # Last-resort hard stop: if uvicorn ignored should_exit for 5+ s (stuck
        # handler / CLOSE_WAIT socket on Windows), terminate so this QThread
        # cannot keep the python process alive past app.exit() — which would
        # re-introduce the must-reboot lock leak. The DB itself is not held by
        # this thread, so terminate() here only risks an orphaned uvicorn task.
        if self.isRunning():
            self.terminate()
            self.wait(1000)


# ── mDNS discovery thread ─────────────────────────────────────────────────────

_MDNS_SERVICE_TYPE = "_specimen._tcp.local."


class CollabDiscoveryThread(QThread):
    """Registers this node's mDNS service and discovers peers.

    Signals
    -------
    peer_found(str, int, str):    ip, port, hostname
    peer_lost(str, int):          ip, port
    """

    peer_found = pyqtSignal(str, int, str)    # ip, port, hostname
    peer_lost  = pyqtSignal(str, int)         # ip, port
    discovery_error = pyqtSignal(str)         # mDNS unavailable / register failed

    def __init__(self, hostname: str, port: int) -> None:
        super().__init__()
        self._hostname = hostname
        self._port = port
        self._zc: Any = None
        self._info: Any = None
        self._browser: Any = None

    def run(self) -> None:
        try:
            from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf
            import ipaddress
        except ImportError:
            logger.warning("zeroconf not installed — mDNS discovery disabled")
            self.discovery_error.emit("未安装 zeroconf")
            return

        local_ip = _get_local_ip()
        name = f"{self._hostname}.{_MDNS_SERVICE_TYPE}"

        try:
            addr_bytes = socket.inet_aton(local_ip)
        except OSError:
            addr_bytes = socket.inet_aton("127.0.0.1")

        self._info = ServiceInfo(
            _MDNS_SERVICE_TYPE,
            name,
            addresses=[addr_bytes],
            port=self._port,
            properties={"hostname": self._hostname.encode()},
        )

        self._zc = Zeroconf()

        try:
            self._zc.register_service(self._info)
        except Exception as exc:  # noqa: BLE001
            logger.warning("collab: mDNS register failed: %s", exc)
            self.discovery_error.emit(f"注册失败:{exc}")

        handler = _BrowserHandler(
            local_ip=local_ip,
            local_port=self._port,
            on_found=lambda ip, port, hn: self.peer_found.emit(ip, port, hn),
            on_lost=lambda ip, port: self.peer_lost.emit(ip, port),
        )
        self._browser = ServiceBrowser(self._zc, _MDNS_SERVICE_TYPE, handler)

        # Block until stop() is called
        self._browser._handlers_lock = getattr(self._browser, "_handlers_lock", threading.Event())
        while not self.isInterruptionRequested():
            time.sleep(0.5)

    def stop(self) -> None:
        self.requestInterruption()
        if self._zc:
            try:
                if self._info:
                    self._zc.unregister_service(self._info)
                self._zc.close()
            except Exception:  # noqa: BLE001
                pass
        self.quit()
        self.wait(3000)


class _BrowserHandler:
    """zeroconf ServiceBrowser callback adapter."""

    def __init__(self, local_ip: str, local_port: int,
                 on_found: Callable, on_lost: Callable) -> None:
        self._local_ip = local_ip
        self._local_port = local_port
        self._on_found = on_found
        self._on_lost = on_lost

    def add_service(self, zeroconf: Any, service_type: str, name: str) -> None:
        info = zeroconf.get_service_info(service_type, name)
        if not info:
            return
        ips = info.parsed_scoped_addresses()
        if not ips:
            return
        ip = ips[0]
        port = info.port
        # A host may have another/stale app instance listening on a neighbouring
        # port (for example current=5051 while an old process still owns 5050).
        # It is still this machine, not a collaboration peer.
        if ip == self._local_ip:
            return   # skip self on every port
        hostname = (info.properties.get(b"hostname") or b"").decode("utf-8", errors="replace")
        self._on_found(ip, port, hostname)

    def update_service(self, *_: Any) -> None:
        pass

    def remove_service(self, zeroconf: Any, service_type: str, name: str) -> None:
        info = zeroconf.get_service_info(service_type, name)
        if info:
            ips = info.parsed_scoped_addresses()
            if ips:
                self._on_lost(ips[0], info.port)


# ── Main service object ───────────────────────────────────────────────────────

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

    def to_dict(self) -> dict:
        return {
            "uid":        self.uid,
            "kind":       self.kind,
            "count":      self.count,
            "reportedAt": self.reported_at,
            "deviceId":   self.device_id,
        }


class CollabService(QObject):
    """Top-level collaboration service owned by the main window / AppContext.

    Signals
    -------
    peers_changed():          peer list updated (added/removed/latency change)
    tasks_changed():          task store updated after sync
    conflict_detected(str):   uid that triggered a 409
    sync_error(str):          human-readable sync error message
    server_ready(int):        FastAPI server is up, listening on given port
    offline_drafts_changed(): offline draft queue updated
    """

    peers_changed    = pyqtSignal()
    tasks_changed    = pyqtSignal()
    conflict_detected = pyqtSignal(str)        # uid
    data_overwritten  = pyqtSignal(list)       # list[dict{uid,old_status,new_status}]
    sync_error       = pyqtSignal(str)
    server_ready     = pyqtSignal(int)         # port
    offline_drafts_changed = pyqtSignal()      # draft queue added/cleared
    diagnostics_changed = pyqtSignal()         # self-diagnostics list updated
    activity_logged  = pyqtSignal()            # new activity entry appended
    specimen_status_changed = pyqtSignal(str)  # uid whose collab status changed
    pairing_requested = pyqtSignal(str, str, str)  # ip, hostname, their_group_code
    pairing_accepted  = pyqtSignal(str, str)       # ip, hostname
    specimens_updated = pyqtSignal()               # local DB got new/updated specimens
    project_bind_suggested = pyqtSignal(str, str, str)  # peer_hostname, project_name, sync_code

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.store = TaskStore()
        self.activity_log = ActivityLog()
        self._peers: dict[str, PeerInfo] = {}   # key = "ip:port"
        self._peers_lock = threading.Lock()
        self._hostname = socket.gethostname()
        self._port: Optional[int] = None
        self._project_name: str = ""
        self._project_id: str = ""
        self._project_dir: str = ""
        self._group_code: str = ""
        self._session_name: str = ""   # human-readable session label
        self._running: bool = False
        self._diagnostics: list[Diagnostic] = []
        self._discovery_error: str = ""

        # Offline draft queue (mirrors loadCollabOfflineDrafts / saveCollabOfflineDrafts)
        self._offline_drafts: list[OfflineDraft] = []
        self._offline_drafts_lock = threading.Lock()

        # Same-name project bind suggestions already shown (peer project_ids);
        # prevents re-nagging after the user declines.  Reset on project switch.
        self._bind_prompted: set[str] = set()

        self._server_thread: Optional[CollabServerThread] = None
        self._discovery_thread: Optional[CollabDiscoveryThread] = None
        self._subnet_scanner: Optional[QThread] = None
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(5000)     # 5 s pull-sync (backup for failed pushes)
        self._sync_timer.timeout.connect(self._sync_all_peers)
        # Retry timer — attempt to flush offline drafts when peers are present
        self._retry_timer = QTimer(self)
        self._retry_timer.setInterval(15000)   # 15 s retry cadence
        self._retry_timer.timeout.connect(self._maybe_retry_offline_drafts)
        # Periodic subnet scan — catches devices missed by mDNS (esp. Windows)
        self._subnet_scan_timer = QTimer(self)
        self._subnet_scan_timer.setInterval(60000)  # every 60 s
        self._subnet_scan_timer.timeout.connect(self._periodic_subnet_scan)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self, project_name: str = "", preferred_port: int = 5050,
              group_code: str = "", project_dir: str = "") -> None:
        """Start server, mDNS, and sync timer.  Safe to call from main thread.

        Idempotent: a second call while already running is a no-op.
        """
        if self._running:
            return
        self._project_name = project_name
        if project_dir:
            self._project_dir = project_dir
            self._project_id = self._ensure_project_id(project_dir)
            # Restore any drafts that survived a previous app restart
            persisted = self._load_drafts_from_disk()
            if persisted:
                with self._offline_drafts_lock:
                    existing = {d.uid for d in self._offline_drafts}
                    self._offline_drafts.extend(
                        d for d in persisted if d.uid not in existing
                    )
                logger.info("collab: restored %d offline draft(s) from disk", len(persisted))
        # Team code: explicit param wins; otherwise keep whatever was set
        # (persisted team code is passed in by callers from settings).
        if group_code:
            self._group_code = group_code.strip()
        self._running = True

        self._server_thread = CollabServerThread(
            store=self.store,
            node_info_fn=self._node_info,
            preferred_port=preferred_port,
            activity_log=self.activity_log,
            file_manifest_fn=self._file_manifest_payload,
            file_path_fn=self._resolve_file_path,
            pairing_request_fn=self._on_pairing_request_received,
            pairing_accept_fn=self._on_pairing_accept_received,
            specimen_provider_fn=self._get_local_specimens,
            specimen_writer_fn=self._write_specimens_to_local_db,
        )
        self._server_thread.started_on_port.connect(self._on_server_started)
        self._server_thread.server_error.connect(
            lambda msg: self.sync_error.emit(f"Server error: {msg}")
        )
        self._server_thread.start()

    def _on_server_started(self, port: int) -> None:
        self._port = port
        local_ip = _get_local_ip()
        with self._peers_lock:
            removed_self = any(p.ip == local_ip for p in self._peers.values())
            self._peers = {
                key: peer for key, peer in self._peers.items()
                if peer.ip != local_ip
            }
        if removed_self:
            self.peers_changed.emit()
        self.server_ready.emit(port)
        # Now start mDNS with the real port
        self._discovery_thread = CollabDiscoveryThread(
            hostname=self._hostname,
            port=port,
        )
        self._discovery_thread.peer_found.connect(self._on_peer_found)
        self._discovery_thread.peer_lost.connect(self._on_peer_lost)
        self._discovery_thread.discovery_error.connect(self._on_discovery_error)
        self._discovery_thread.start()
        self._sync_timer.start()
        self._retry_timer.start()
        # First subnet scan 5 s after start (mDNS may not have seen all peers yet)
        QTimer.singleShot(5000, self._periodic_subnet_scan)
        self._subnet_scan_timer.start()
        self.run_diagnostics()

    def stop(self) -> None:
        """Gracefully shut down all background threads.  Idempotent."""
        self._running = False
        self._sync_timer.stop()
        self._retry_timer.stop()
        self._subnet_scan_timer.stop()
        self._stop_subnet_scanner()
        if self._discovery_thread:
            self._discovery_thread.stop()
            self._discovery_thread = None
        if self._server_thread:
            self._server_thread.stop()
            self._server_thread = None

    def is_running(self) -> bool:
        """True between start() and stop()."""
        return self._running

    @property
    def project_name(self) -> str:
        """Current project label advertised to collaboration peers."""
        return self._project_name

    @property
    def project_id(self) -> str:
        """Stable current-project identity used to gate media sync."""
        return self._project_id

    def project_sync_code(self) -> str:
        """Return the copy/paste code for binding another PC to this project."""
        if not self._project_id:
            return ""
        from app.services.project_identity_service import project_sync_code
        return project_sync_code(
            self._project_id,
            project_name=Path(str(self._project_name or "")).name,
        )

    def discover_team_projects(self) -> list[dict]:
        """List distinct projects currently open by same-team peers.

        Each entry: ``{"name": str, "code": str, "peer_count": int}`` where
        *code* is a ready-to-apply project sync code (see
        ``apply_project_sync_code``).  Used by "新建项目" to offer "加入团队
        现有项目" instead of guessing by name.
        """
        from app.services.project_identity_service import project_sync_code

        with self._peers_lock:
            peers = [p for p in self._peers.values() if self._group_matches(p)]
        by_id: dict[str, dict] = {}
        for p in peers:
            if not p.project_id:
                continue
            entry = by_id.setdefault(p.project_id, {
                "name": Path(str(p.project_name or "")).name or p.project_id,
                "code": project_sync_code(p.project_id, project_name=Path(str(p.project_name or "")).name),
                "peer_count": 0,
            })
            entry["peer_count"] += 1
        return sorted(by_id.values(), key=lambda e: e["name"])

    def apply_project_sync_code(self, code: str) -> str:
        """Adopt a project sync code after the UI has asked for confirmation."""
        if not self._project_dir:
            raise ValueError("current project is not open")
        from app.db.db_manager import open_project_db_private
        from app.services.project_identity_service import (
            parse_project_sync_code,
            set_project_identity,
        )

        parsed = parse_project_sync_code(code)
        new_project_id = parsed["projectId"]
        remote_name = parsed.get("projectName", "")
        db = open_project_db_private(self._project_dir)
        try:
            self._project_id = set_project_identity(
                db,
                new_project_id,
                project_name=remote_name or Path(str(self._project_name or "")).name,
                previous_project_id=self._project_id,
            )
        finally:
            db.close()
        self._log_activity(
            "project-sync-code",
            detail="当前项目已加入共享照片同步身份",
        )
        self.peers_changed.emit()
        return self._project_id

    # ── Activity logging ──────────────────────────────────────────────────

    def _log_activity(self, action: str, target_uid: str = "",
                      actor: str = "", detail: str = "",
                      severity: str = "info") -> None:
        """Append an entry to the activity log and emit *activity_logged*."""
        if not actor:
            actor = self._hostname
        self.activity_log.append(ActivityEntry(
            actor=actor,
            action=action,
            target_uid=target_uid,
            detail=detail,
            severity=severity,
        ))
        self.activity_logged.emit()

    # ── Collaboration group ───────────────────────────────────────────────

    @property
    def group_code(self) -> str:
        return self._group_code

    def set_group_code(self, code: str) -> None:
        """Override the auto-derived group code (advanced / cross-project pairing)."""
        self._group_code = (code or "").strip()

    # ── Session management (zero-config team collaboration) ───────────────

    @property
    def session_name(self) -> str:
        return self._session_name

    @staticmethod
    def _generate_session_code() -> str:
        """6-char uppercase alphanumeric session code (e.g. 'A3B7C2')."""
        import random
        import string
        chars = string.ascii_uppercase + string.digits
        return "".join(random.choices(chars, k=6))

    def create_session(self, name: str = "") -> str:
        """Create a new collaboration session; returns the session code.

        Generates a short unique code, sets it as the group code, and
        broadcasts this node as the session host.  Other devices on the
        subnet can then see and join this session.
        """
        code = self._generate_session_code()
        self._group_code = code
        self._session_name = name or f"{self._hostname}的会话"
        self._log_activity("session", detail=f"新建协作会话：{self._session_name}（{code}）")
        self.peers_changed.emit()
        return code

    def join_session(self, session_code: str, session_name: str = "") -> None:
        """Join an existing session by adopting its code as the group code.

        Immediately pulls all specimen records from peers so this device has
        the full current dataset before starting its own work.
        """
        code = (session_code or "").strip().upper()
        if not code:
            return
        self._group_code = code
        self._session_name = session_name or f"加入了 {code}"
        self._log_activity("session", detail=f"加入协作会话：{code}")
        self.peers_changed.emit()
        # Pull task records immediately
        QTimer.singleShot(100, self._sync_all_peers)
        # Pull specimen records (full dataset from all peers in this session)
        QTimer.singleShot(300, self.pull_all_specimens_from_session)

    def leave_session(self) -> None:
        """Leave the current team (clears team code, stops syncing)."""
        self._group_code = ""
        self._session_name = ""
        self._log_activity("session", detail="已退出协作团队")
        self.peers_changed.emit()

    def set_project_dir(self, project_dir: str | None) -> None:
        """Update the project directory used by file-sync endpoints.

        The team code is independent of the project: switching projects keeps
        the team connection alive; data sync simply follows whichever peers
        have the same project open (see _data_sync_allowed).
        """
        self._project_dir = str(project_dir or "")
        self._bind_prompted.clear()
        if project_dir:
            self._project_name = str(project_dir)
            self._project_id = self._ensure_project_id(project_dir)
            # Project switched: immediately sync with teammates now on the
            # same project (team connection itself is unaffected).
            if self._running and self._group_code:
                QTimer.singleShot(500, self._sync_all_peers)
                QTimer.singleShot(1000, self.pull_all_specimens_from_session)
        else:
            self._project_id = ""

    def _ensure_project_id(self, project_dir: str | Path | None) -> str:
        if not project_dir:
            return ""
        try:
            from app.db.db_manager import open_project_db_private
            from app.services.project_identity_service import ensure_project_identity
            db = open_project_db_private(str(project_dir))
            try:
                return ensure_project_identity(
                    db,
                    project_name=Path(str(project_dir)).name,
                )
            finally:
                db.close()
        except Exception:  # noqa: BLE001
            return ""

    def _group_matches(self, peer: PeerInfo) -> bool:
        """Team membership: both sides share a non-empty team code."""
        return bool(self._group_code) and peer.group_code == self._group_code

    @staticmethod
    def _normalize_project_label(name: str) -> str:
        """Normalise a project name/path for cross-device comparison.

        Takes the directory basename, strips whitespace (incl. internal),
        and casefolds — so "三门湾 调查" and "三门湾调查" match, and full
        paths on different disks (D:\\x\\三门湾 vs E:\\y\\三门湾) match too.
        """
        raw = str(name or "").strip().replace("\\", "/").rstrip("/")
        if not raw:
            return ""
        base = raw.rsplit("/", 1)[-1]
        return "".join(base.split()).casefold()

    def _project_matches(self, peer: PeerInfo) -> bool:
        """Decide whether *peer* is working on "the same" project as us.

        Authoritative check: project_id (a UUID stamped into each project's
        DB by ``project_identity_service``).  Two devices whose local folders
        were explicitly bound to the same ID via a project sync code always
        match/never match deterministically — no string-matching fragility.

        Fallback: normalised project *name* comparison, used only when either
        side has not yet been bound to an explicit ID (fresh/legacy project).
        This keeps zero-config sync working for the common case (both people
        just create identically-named folders) while still allowing the
        exact-ID path for teams that care about correctness.
        """
        if self._project_id and peer.project_id:
            return self._project_id == peer.project_id

        mine = self._normalize_project_label(self._project_name)
        theirs = self._normalize_project_label(peer.project_name)
        if not mine or not theirs:
            return True
        return mine == theirs

    def _data_sync_allowed(self, peer: PeerInfo) -> bool:
        """Data (tasks + specimens) flows only within the same team AND the
        same project.  Team members on other projects stay visible but their
        data never mixes into ours."""
        return self._group_matches(peer) and self._project_matches(peer)

    def _check_project_bind_suggestions(self, peers: list[PeerInfo]) -> None:
        """Detect teammates whose project has the same NAME but a different ID.

        Happens when two people independently create identically-named local
        folders instead of using the "加入团队现有项目" flow.  We suggest
        binding (once per peer project) via ``project_bind_suggested``; the UI
        asks the user to confirm, then calls ``apply_project_sync_code``.

        Tie-break: only the side with the LARGER project_id prompts (and would
        adopt the smaller ID) so both machines never adopt each other's ID
        simultaneously and end up swapped-but-still-different.
        """
        if not self._project_id or not self._project_name:
            return
        mine_label = self._normalize_project_label(self._project_name)
        if not mine_label:
            return
        from app.services.project_identity_service import project_sync_code

        for p in peers:
            if not self._group_matches(p):
                continue
            if not p.project_id or p.project_id == self._project_id:
                continue
            if p.project_id in self._bind_prompted:
                continue
            if self._normalize_project_label(p.project_name) != mine_label:
                continue
            if self._project_id <= p.project_id:
                continue  # the other side prompts; we keep our ID
            self._bind_prompted.add(p.project_id)
            display = Path(str(p.project_name or "")).name or mine_label
            try:
                code = project_sync_code(p.project_id, project_name=display)
            except ValueError:
                continue
            self.project_bind_suggested.emit(p.hostname or p.ip, display, code)

    # ── Self-diagnostics ──────────────────────────────────────────────────

    CLOCK_SKEW_THRESHOLD_MS = 5_000

    def diagnostics(self) -> list[Diagnostic]:
        """Return the last computed diagnostics list."""
        return list(self._diagnostics)

    def run_diagnostics(self) -> list[Diagnostic]:
        """Run the synchronous health checks and store/emit the result.

        Network probes (reachability, clock skew measurement) run separately in
        a background worker and call this again once peer attributes are updated.
        """
        diags: list[Diagnostic] = []
        diags += self._diag_deps()
        diags += self._diag_config()
        diags += self._diag_mdns()
        diags += self._diag_group_mismatch()
        diags += self._diag_clock_skew()
        diags += self._diag_reachability()
        if not diags:
            diags = [Diagnostic("ok", "ok", "协作正常", "未发现配置问题。")]
        self._diagnostics = diags
        self.diagnostics_changed.emit()
        return diags

    def overall_health(self) -> str:
        """Roll up to a traffic-light colour: red > yellow > green."""
        if any(d.level == "error" for d in self._diagnostics):
            return "red"
        if any(d.level == "warn" for d in self._diagnostics):
            return "yellow"
        return "green"

    def _diag_deps(self) -> list[Diagnostic]:
        missing = _missing_deps()
        if missing:
            return [Diagnostic(
                "deps_missing", "error", "缺少协作组件",
                f"未安装:{', '.join(missing)}。协作功能无法运行。",
                f"运行 pip install {' '.join(missing)}")]
        return []

    def _diag_config(self) -> list[Diagnostic]:
        if not self._group_code:
            return [Diagnostic(
                "config_no_group", "warn", "未设置协作组码",
                "未填写协作组码,不会与任何设备同步标本编号。",
                "在「设置 → 协作」里给同组每台设备填写相同的协作组码。")]
        return []

    def _diag_group_mismatch(self) -> list[Diagnostic]:
        if not self._group_code:
            return []
        others = sorted({
            p.group_code for p in self.peers()
            if p.group_code and p.group_code != self._group_code
        })
        if others:
            return [Diagnostic(
                "group_mismatch", "warn", "发现组码不同的设备",
                f"同网段设备的组码为:{', '.join(others)};你的组码是 {self._group_code}。"
                "组码不同的设备不会互相同步,可能各自占用了相同编号。",
                "若你们应在同一组,请核对并统一组码。",
                action="adopt_group")]
        return []

    def _diag_clock_skew(self) -> list[Diagnostic]:
        bad = [p for p in self.peers()
               if p.clock_skew_ms is not None
               and abs(p.clock_skew_ms) > self.CLOCK_SKEW_THRESHOLD_MS]
        if bad:
            worst = max(abs(p.clock_skew_ms) for p in bad)  # type: ignore[arg-type]
            return [Diagnostic(
                "clock_skew", "warn", "设备时间不一致",
                f"与队友的系统时间相差约 {round(worst / 1000)} 秒。"
                "同步按修改时间先后合并,时间不准会导致较新的修改被覆盖。",
                "请校准各设备的系统时间(建议开启「自动设置时间」)。")]
        return []

    def _diag_mdns(self) -> list[Diagnostic]:
        if self._discovery_error:
            return [Diagnostic(
                "mdns_unavailable", "warn", "局域网自动发现不可用",
                f"无法启动自动发现({self._discovery_error})。",
                "改用「搜索局域网」或「配对码」连接队友。")]
        return []

    def _diag_reachability(self) -> list[Diagnostic]:
        blocked = [p for p in self.peers()
                   if p.reachable is True and p.reachback_ok is False]
        if blocked:
            port = self._port or 5050
            return [Diagnostic(
                "firewall_blocked", "error", "队友连不到你",
                f"你能看到队友,但他们无法连回你(端口 {port})。"
                "很可能是本机防火墙挡住了入站连接。",
                f"放行端口 {port} 的入站连接。",
                action="open_firewall")]
        return []

    # ── Network probes (run off the main thread) ──────────────────────────

    def _on_discovery_error(self, msg: str) -> None:
        """Record an mDNS discovery failure and fall back to subnet scan."""
        self._discovery_error = msg
        self.run_diagnostics()
        # mDNS is unavailable (Windows Firewall / VLAN) — scan the local subnet
        QTimer.singleShot(500, self.scan_subnet_peers)

    def _periodic_subnet_scan(self) -> None:
        """Periodic scan to catch devices missed by mDNS (runs every 60 s).

        Only scans if there are no known peers or mDNS has reported errors,
        to avoid unnecessary network traffic when everything is working fine.
        """
        if not self._running:
            return
        with self._peers_lock:
            peer_count = len(self._peers)
        if peer_count == 0 or self._discovery_error:
            self.scan_subnet_peers()

    # ── Subnet scan fallback ───────────────────────────────────────────────

    def scan_subnet_peers(self, on_done: Optional[Callable[[int], None]] = None) -> None:
        """Scan the local /24 subnet for collab nodes (mDNS fallback).

        Probes every host in the subnet concurrently (1 s timeout, 30 workers).
        Safe to call multiple times — kills any in-progress scanner first.
        """
        self._stop_subnet_scanner()

        port = self._port or 5050
        local_ip = _get_local_ip()

        class _SubnetScanner(QThread):
            peer_found = pyqtSignal(str, int, str)  # ip, port, hostname
            scan_done  = pyqtSignal(int)             # found count

            def __init__(self, ip: str, p: int) -> None:
                super().__init__()
                self._local_ip = ip
                self._port = p

            def run(self) -> None:
                import concurrent.futures
                parts = self._local_ip.split(".")
                if len(parts) != 4:
                    self.scan_done.emit(0)
                    return
                prefix = ".".join(parts[:3])
                candidates = [
                    f"{prefix}.{i}" for i in range(1, 255)
                    if f"{prefix}.{i}" != self._local_ip
                ]
                found = 0

                def probe(ip: str) -> Optional[tuple[str, str]]:
                    if self.isInterruptionRequested():
                        return None
                    try:
                        import httpx
                        r = httpx.get(
                            f"http://{ip}:{self._port}/api/node/health",
                            timeout=1.0,
                        )
                        if r.status_code == 200:
                            # Try to get hostname from /info
                            try:
                                info = httpx.get(
                                    f"http://{ip}:{self._port}/api/node/info",
                                    timeout=1.0,
                                ).json()
                                hostname = info.get("hostname", ip)
                            except Exception:
                                hostname = ip
                            return ip, hostname
                    except Exception:
                        pass
                    return None

                with concurrent.futures.ThreadPoolExecutor(max_workers=30) as pool:
                    for result in pool.map(probe, candidates):
                        if result is not None:
                            ip_addr, hn = result
                            self.peer_found.emit(ip_addr, self._port, hn)
                            found += 1
                self.scan_done.emit(found)

        scanner = _SubnetScanner(local_ip, port)
        scanner.peer_found.connect(self._on_peer_found)
        if on_done is not None:
            scanner.scan_done.connect(on_done)
        scanner.finished.connect(lambda: self._clear_subnet_scanner(scanner))
        scanner.finished.connect(scanner.deleteLater)
        scanner.start()
        self._subnet_scanner = scanner

    def _stop_subnet_scanner(self) -> None:
        scanner = self._subnet_scanner
        if scanner is None:
            return
        try:
            scanner.requestInterruption()
            scanner.wait(2000)
        except Exception:
            pass
        if self._subnet_scanner is scanner:
            self._subnet_scanner = None

    def _clear_subnet_scanner(self, scanner: QThread) -> None:
        if self._subnet_scanner is scanner:
            self._subnet_scanner = None

    def _probe_peer(self, peer: PeerInfo) -> None:
        """Measure reachability, clock skew and reachback for one peer."""
        try:
            import httpx
            r = httpx.get(f"{peer.base_url}/api/node/info", timeout=3.0)
            if r.status_code == 200:
                peer.reachable = True
                data = r.json()
                st = data.get("serverTime")
                if isinstance(st, (int, float)):
                    peer.clock_skew_ms = (time.time() - float(st)) * 1000.0
                peer.project_name = data.get("projectName", peer.project_name)
                peer.project_id = data.get("projectId", peer.project_id)
                if not peer.group_code:
                    peer.group_code = data.get("groupCode", "")
                if data.get("sessionName"):
                    peer.session_name = data["sessionName"]
                try:
                    rb = httpx.post(
                        f"{peer.base_url}/api/node/reachback",
                        json={"ip": _get_local_ip(), "port": self._port},
                        timeout=3.0,
                    )
                    if rb.status_code == 200:
                        peer.reachback_ok = bool(rb.json().get("reachable"))
                except Exception:  # noqa: BLE001
                    peer.reachback_ok = None
            else:
                peer.reachable = False
        except Exception:  # noqa: BLE001
            peer.reachable = False

    def run_probes(self) -> None:
        """Probe every known peer (background) then refresh diagnostics."""
        for peer in self.peers():
            self._probe_peer(peer)
        self.run_diagnostics()

    # ── Subnet scan (mDNS-failure fallback) ───────────────────────────────

    SCAN_PORTS = tuple(range(5050, 5070))

    def _local_subnet_hosts(self) -> list[str]:
        """All host IPs in the local /24, excluding our own address."""
        ip = _get_local_ip()
        parts = ip.split(".")
        if len(parts) != 4:
            return []
        base = ".".join(parts[:3])
        return [f"{base}.{i}" for i in range(1, 255) if f"{base}.{i}" != ip]

    def scan_lan(self, hosts: Optional[list[str]] = None,
                 ports: Optional[list[int]] = None,
                 timeout: float = 0.3) -> list[PeerInfo]:
        """Ping-sweep the LAN for collab nodes and add reachable ones as peers.

        Novice fallback when mDNS fails — no IP knowledge required.  Runs the
        probes concurrently; pass small host/port lists in tests.
        """
        hosts = hosts if hosts is not None else self._local_subnet_hosts()
        ports = ports if ports is not None else list(self.SCAN_PORTS)
        try:
            import httpx
        except ImportError:
            return []

        local_ip = _get_local_ip()
        targets = [(h, p) for h in hosts for p in ports if h != local_ip]

        def _probe(target: tuple[str, int]) -> Optional[PeerInfo]:
            host, port = target
            try:
                r = httpx.get(f"http://{host}:{port}/api/node/info", timeout=timeout)
                if r.status_code != 200:
                    return None
                data = r.json()
                return PeerInfo(
                    ip=host, port=port,
                    hostname=data.get("hostname", ""),
                    group_code=data.get("groupCode", ""),
                    session_name=data.get("sessionName", ""),
                    project_name=data.get("projectName", ""),
                    project_id=data.get("projectId", ""),
                    manual=True,
                )
            except Exception:  # noqa: BLE001
                return None

        found: list[PeerInfo] = []
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
            for peer in pool.map(_probe, targets):
                if peer is not None:
                    with self._peers_lock:
                        self._peers[f"{peer.ip}:{peer.port}"] = peer
                    found.append(peer)

        if found:
            self.peers_changed.emit()
        return found

    # ── Peer management ───────────────────────────────────────────────────

    def _spawn(self, fn: Callable[[], None]) -> None:
        """Run *fn* on a short-lived daemon thread (non-blocking).

        Overridden in tests to run synchronously.
        """
        threading.Thread(target=fn, daemon=True).start()

    def _on_peer_found(self, ip: str, port: int, hostname: str) -> None:
        if ip == _get_local_ip():
            return
        key = f"{ip}:{port}"
        with self._peers_lock:
            self._peers[key] = PeerInfo(ip=ip, port=port, hostname=hostname)
        logger.info("collab: peer found %s (%s:%d)", hostname, ip, port)
        self.peers_changed.emit()
        self._log_activity("joined", actor=hostname, detail=f"{hostname} 加入了协作组")
        # Enrich with group_code / project_name from /api/node/info so the peer
        # can pass the group filter.  HTTP → do it off the main thread.
        peer = self._peers[key]
        self._spawn(lambda: (self._fetch_peer_info(peer), self.peers_changed.emit()))

    def _on_peer_lost(self, ip: str, port: int) -> None:
        key = f"{ip}:{port}"
        with self._peers_lock:
            peer = self._peers.pop(key, None)
        hostname = peer.hostname if peer else f"{ip}:{port}"
        logger.info("collab: peer lost %s:%d", ip, port)
        self.peers_changed.emit()
        self._log_activity("left", actor=hostname, detail=f"{hostname} 离开了协作组")

    def add_manual_peer(self, ip: str, port: int) -> None:
        """Manually register a peer (fallback when mDNS fails across VLANs)."""
        if ip == _get_local_ip():
            return
        key = f"{ip}:{port}"
        with self._peers_lock:
            self._peers[key] = PeerInfo(ip=ip, port=port, manual=True)
        self.peers_changed.emit()
        # Immediately attempt to pull info
        self._fetch_peer_info(self._peers[key])

    def remove_manual_peer(self, ip: str, port: int) -> None:
        """Remove a manually added peer."""
        key = f"{ip}:{port}"
        with self._peers_lock:
            self._peers.pop(key, None)
        self.peers_changed.emit()

    def peers(self) -> list[PeerInfo]:
        with self._peers_lock:
            return list(self._peers.values())

    def _fetch_peer_info(self, peer: PeerInfo) -> None:
        """Try to enrich PeerInfo with hostname/projectName from /api/node/info."""
        try:
            import httpx
            resp = httpx.get(f"{peer.base_url}/api/node/info", timeout=3.0)
            if resp.status_code == 200:
                data = resp.json()
                peer.hostname = data.get("hostname", peer.hostname)
                peer.project_name = data.get("projectName", "")
                peer.project_id = data.get("projectId", "")
                peer.group_code = data.get("groupCode", "")
                peer.last_seen = time.time()
        except Exception:  # noqa: BLE001
            pass

    # ── Sync ──────────────────────────────────────────────────────────────

    def _sync_all_peers(self) -> None:
        """Pull tasks from every known peer.  Runs on the Qt main thread (timer)."""
        peers_snapshot: list[PeerInfo]
        with self._peers_lock:
            peers_snapshot = list(self._peers.values())

        if not peers_snapshot:
            return

        changed_total = 0
        all_overwrites: list[dict] = []
        for peer in peers_snapshot:
            n, overwrites = self._sync_peer_with_overwrites(peer)
            changed_total += n
            all_overwrites.extend(overwrites)

        if changed_total:
            self.tasks_changed.emit()
            self._log_activity("status_changed", detail=f"同步更新了 {changed_total} 条任务")

        if all_overwrites:
            self.data_overwritten.emit(all_overwrites)
            for o in all_overwrites:
                self._log_activity(
                    "sync_overwrite", o["uid"],
                    detail=(
                        f"同步时本机状态被覆盖：{o['old_status']} → {o['new_status']}"
                    ),
                    severity="warn",
                )

        # Specimen metadata sync: every 6th task-sync cycle (~30 s)
        self._spec_sync_counter = getattr(self, "_spec_sync_counter", 0) + 1
        if self._spec_sync_counter >= 6:
            self._spec_sync_counter = 0
            for peer in peers_snapshot:
                self._sync_specimens_from_peer(peer)

        # Same-name / different-ID projects: suggest binding (confirm in UI)
        self._check_project_bind_suggestions(peers_snapshot)

    def _sync_peer(self, peer: PeerInfo) -> int:
        """Pull one peer and return the number of changed local tasks."""
        changed, _overwrites = self._sync_peer_with_overwrites(peer)
        return changed

    def _sync_peer_with_overwrites(self, peer: PeerInfo) -> tuple[int, list[dict]]:
        """Pull /api/collab/tasks from one peer and merge.

        Returns ``(changed_count, overwrites)`` where *overwrites* is a list of
        dicts ``{uid, old_status, new_status}`` for tasks whose local status was
        silently replaced by a newer remote value.
        """
        if not self._data_sync_allowed(peer):
            return 0, []
        try:
            import httpx
            t0 = time.monotonic()
            resp = httpx.get(f"{peer.base_url}/api/collab/tasks", timeout=4.0)
            peer.latency_ms = (time.monotonic() - t0) * 1000
            peer.last_seen = time.time()
            if resp.status_code == 200:
                remote_tasks: list[dict] = resp.json()
                overwrites: list[dict] = []
                changed = self.store.merge_from_peer(remote_tasks, overwrites_out=overwrites)
                return changed, overwrites
        except Exception as exc:  # noqa: BLE001
            logger.debug("collab: sync failed for %s: %s", peer.base_url, exc)
        return 0, []

    # ── Task creation (with remote 409 check) ─────────────────────────────

    def create_task(self, uid: str, assignee: Optional[str] = None,
                    device_id: Optional[str] = None) -> tuple[bool, str]:
        """Create a new task, broadcasting to all online peers.

        Returns (success: bool, message: str).
        On conflict returns (False, "409: …conflict message…").

        NOTE: Network 409 checks require live peers — tested with doubles.
        """
        # 1. Local check — memory store + SQLite specimens table
        if self.store.exists(uid):
            msg = f"409: UID '{uid}' already exists on this device"
            self.conflict_detected.emit(uid)
            self._log_activity("conflict", uid, detail=f"编号 {uid} 在本机已存在", severity="error")
            return False, msg
        if self._project_dir:
            try:
                from app.db.db_manager import open_project_db_private
                _db = open_project_db_private(self._project_dir)
                try:
                    row = _db.execute(
                        "SELECT 1 FROM specimens WHERE uid = ? LIMIT 1", (uid,)
                    ).fetchone()
                finally:
                    _db.close()
                if row:
                    msg = f"409: UID '{uid}' already exists in local DB"
                    self.conflict_detected.emit(uid)
                    self._log_activity("conflict", uid, detail=f"编号 {uid} 在本地数据库已存在", severity="error")
                    return False, msg
            except Exception:
                pass

        # 2. Create locally first — this device is the source of truth.
        try:
            self.store.create(uid, assignee=assignee, device_id=device_id,
                              project_name=self._project_name)
        except ValueError as exc:
            self.conflict_detected.emit(uid)
            return False, str(exc)

        # 3. Broadcast to peers; roll back local + remote claims on conflict.
        peers_snapshot: list[PeerInfo]
        with self._peers_lock:
            peers_snapshot = [p for p in self._peers.values() if self._data_sync_allowed(p)]

        claimed_peers: list[PeerInfo] = []
        unreachable_peers: list[PeerInfo] = []
        for peer in peers_snapshot:
            ok, conflict_msg, created = self._remote_create(peer, uid, assignee, device_id)
            if not ok:
                self.conflict_detected.emit(uid)
                self._log_activity("conflict", uid, detail=f"编号 {uid} 在远程设备已存在", severity="error")
                self._rollback_task_claim(uid, claimed_peers)
                return False, conflict_msg
            if created:
                claimed_peers.append(peer)
            else:
                # Network failure (not a 409) — peer unreachable, task not broadcast
                unreachable_peers.append(peer)

        # If any peer was unreachable, queue for retry so it gets the task later.
        # This prevents split-brain if B comes online after A created the UID.
        if unreachable_peers:
            self.mark_offline_draft(uid, assignee=assignee, device_id=device_id)
            logger.debug(
                "collab: %d peer(s) unreachable during create of %s — queued offline draft",
                len(unreachable_peers), uid,
            )

        self.tasks_changed.emit()
        self._log_activity("claimed", uid, detail=f"认领了编号 {uid}")
        self.specimen_status_changed.emit(uid)
        return True, "ok"

    def _remote_release(self, peer: PeerInfo, uid: str) -> None:
        """Ask a peer to release a UID claim (best-effort compensation)."""
        try:
            import httpx
            httpx.post(
                f"{peer.base_url}/api/collab/tasks/release",
                json={"uid": uid, "groupCode": self._group_code},
                timeout=4.0,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("collab: remote release failed for %s: %s", peer.base_url, exc)

    def _rollback_task_claim(self, uid: str, remote_peers: list[PeerInfo]) -> None:
        """Undo a failed distributed claim (local delete + remote release)."""
        try:
            self.store.delete(uid)
        except Exception:
            pass
        for peer in remote_peers:
            self._remote_release(peer, uid)

    def _remote_create(self, peer: PeerInfo, uid: str,
                       assignee: Optional[str], device_id: Optional[str]
                       ) -> tuple[bool, str, bool]:
        """POST create to a single peer.

        Returns ``(ok, message, created_on_peer)``.
        Network failure is treated as peer unavailable (``ok=True``, ``created=False``).
        """
        try:
            import httpx
            resp = httpx.post(
                f"{peer.base_url}/api/collab/tasks/create",
                json={
                    "uid": uid,
                    "assignee": assignee,
                    "deviceId": device_id,
                    "projectName": self._project_name,
                    "groupCode": self._group_code,
                },
                timeout=4.0,
            )
            if resp.status_code == 409:
                detail = resp.json().get("detail", "conflict")
                return False, f"409: {detail} (peer {peer.hostname or peer.ip})", False
            if resp.status_code == 201:
                return True, "", True
            return False, f"remote create failed ({resp.status_code})", False
        except Exception as exc:  # noqa: BLE001
            # Network failure is treated as "peer unavailable, not a conflict"
            logger.debug("collab: remote create failed for %s: %s", peer.base_url, exc)
        return True, "", False

    # ── Offline draft queue ───────────────────────────────────────────────
    # Mirrors: loadCollabOfflineDrafts / saveCollabOfflineDrafts /
    #          collabMarkOfflineDraft / collabRetryOfflineDrafts

    def load_offline_drafts(self) -> list[OfflineDraft]:
        """Return a snapshot of the current offline draft queue (thread-safe)."""
        with self._offline_drafts_lock:
            return list(self._offline_drafts)

    def save_offline_drafts(self, drafts: list[OfflineDraft]) -> None:
        """Replace the entire offline draft queue (thread-safe).

        In-memory only — mirrors web localStorage writes but avoids filesystem
        coupling.  Serialisation callers can use ``draft.to_dict()`` themselves.
        """
        with self._offline_drafts_lock:
            self._offline_drafts = list(drafts)
        self.offline_drafts_changed.emit()

    # ── Offline draft persistence helpers ─────────────────────────────────

    def _drafts_path(self) -> Optional[Path]:
        """JSON file for persisting offline drafts across restarts."""
        if not self._project_dir:
            return None
        return Path(self._project_dir) / "_data" / "collab_drafts.json"

    def _load_drafts_from_disk(self) -> list[OfflineDraft]:
        path = self._drafts_path()
        if path is None or not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [OfflineDraft.from_dict(d) for d in data if isinstance(d, dict)]
        except Exception:
            return []

    def _save_drafts_to_disk(self) -> None:
        path = self._drafts_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    [d.to_dict() for d in self._offline_drafts],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug("collab: could not save offline drafts: %s", exc)

    def mark_offline_draft(self, uid: str,
                           assignee: Optional[str] = None,
                           device_id: Optional[str] = None) -> OfflineDraft:
        """Queue *uid* as an offline draft (mirrors collabMarkOfflineDraft).

        Persists to disk so drafts survive app restarts.
        Deduplicates by uid: calling again for the same uid is a no-op.
        """
        with self._offline_drafts_lock:
            if any(d.uid == uid for d in self._offline_drafts):
                return next(d for d in self._offline_drafts if d.uid == uid)
            draft = OfflineDraft(uid=uid, assignee=assignee, device_id=device_id)
            self._offline_drafts.append(draft)
        logger.debug("collab: offline draft queued uid=%s", uid)
        self._save_drafts_to_disk()
        self.offline_drafts_changed.emit()
        return draft

    def retry_offline_drafts(self) -> int:
        """Attempt to promote offline drafts to real tasks (mirrors collabRetryOfflineDrafts).

        Returns the number of drafts that were successfully promoted.
        Drafts that fail (still no peers or still conflict) remain in the queue.
        """
        with self._offline_drafts_lock:
            pending = list(self._offline_drafts)

        if not pending:
            return 0

        with self._peers_lock:
            has_peers = bool(self._peers)

        if not has_peers:
            logger.debug("collab: retry skipped — no peers online")
            return 0

        promoted: list[str] = []
        for draft in pending:
            ok, msg = self._retry_offline_draft(draft)
            if ok:
                promoted.append(draft.uid)
                logger.info("collab: offline draft promoted uid=%s", draft.uid)
            else:
                logger.debug("collab: offline draft still failing uid=%s msg=%s",
                             draft.uid, msg)

        if promoted:
            with self._offline_drafts_lock:
                self._offline_drafts = [
                    d for d in self._offline_drafts if d.uid not in promoted
                ]
            self._save_drafts_to_disk()
            self.offline_drafts_changed.emit()

        return len(promoted)

    def _retry_offline_draft(self, draft: OfflineDraft) -> tuple[bool, str]:
        """Push an already-created local draft to currently reachable peers."""
        with self._peers_lock:
            peers_snapshot = [p for p in self._peers.values() if self._data_sync_allowed(p)]
        if not peers_snapshot:
            return False, "no peers online"

        all_delivered = True
        for peer in peers_snapshot:
            ok, conflict_msg, created = self._remote_create(
                peer,
                draft.uid,
                draft.assignee,
                draft.device_id,
            )
            if not ok:
                self.conflict_detected.emit(draft.uid)
                self._log_activity(
                    "conflict",
                    draft.uid,
                    detail=f"离线草稿补推时远程设备已存在编号 {draft.uid}",
                    severity="error",
                )
                return False, conflict_msg
            if not created:
                all_delivered = False
        if not all_delivered:
            return False, "some peers still unreachable"
        return True, "ok"

    def _maybe_retry_offline_drafts(self) -> None:
        """Timer slot: silently attempt to flush offline drafts."""
        try:
            self.retry_offline_drafts()
        except Exception:  # noqa: BLE001
            pass

    # ── Specimen data sync (L2: metadata replication) ────────────────────

    #: Columns synced between peers (excludes large/derived columns)
    _SPEC_SYNC_COLS = (
        "uid", "province", "site", "station", "id", "storage",
        "collectionDate", "photoDate", "collector", "photographer",
        "identifier", "geoArea", "taxonGroup", "orderName", "family",
        "genus", "scientificName", "scientificNameCn", "notes",
        "photoNotes", "lon", "lat", "habitat", "raw_json",
    )

    def _get_local_specimens(self, uid: Optional[str] = None) -> list[dict]:
        """Read specimen records from local project DB (used by FastAPI endpoint)."""
        if not self._project_dir:
            return []
        try:
            from app.db.db_manager import open_project_db_private
            db = open_project_db_private(self._project_dir)
            cols = ", ".join(self._SPEC_SYNC_COLS)
            try:
                if uid:
                    rows = db.execute(
                        f"SELECT {cols} FROM specimens WHERE uid=?", (uid,)
                    ).fetchall()
                else:
                    rows = db.execute(
                        f"SELECT {cols} FROM specimens"
                    ).fetchall()
                return [dict(zip(self._SPEC_SYNC_COLS, row)) for row in rows]
            finally:
                db.close()
        except Exception as exc:
            logger.debug("collab: _get_local_specimens error: %s", exc)
            return []

    def _write_specimens_to_local_db(self, specimens: list[dict]) -> int:
        """Upsert incoming specimen records into local project DB.

        Uses INSERT OR REPLACE so incoming records overwrite local ones with
        the same UID.  In normal field use each person works on distinct UIDs,
        so conflicts are rare.
        """
        if not self._project_dir or not specimens:
            return 0
        try:
            from app.db.db_manager import open_project_db_private
            db = open_project_db_private(self._project_dir)
            written = 0
            try:
                for spec in specimens:
                    uid = spec.get("uid")
                    if not uid:
                        continue
                    cols = [c for c in self._SPEC_SYNC_COLS if c in spec]
                    if "uid" not in cols:
                        continue
                    placeholders = ", ".join(f":{c}" for c in cols)
                    col_str = ", ".join(cols)
                    db.execute(
                        f"INSERT OR REPLACE INTO specimens ({col_str}) "
                        f"VALUES ({placeholders})",
                        {c: spec.get(c) for c in cols},
                    )
                    written += 1
                db.commit()
            finally:
                db.close()
            if written:
                self.specimens_updated.emit()
            return written
        except Exception as exc:
            logger.debug("collab: _write_specimens error: %s", exc)
            return 0

    def push_specimen(self, uid: str) -> None:
        """Push one specimen record from local DB to all session peers.

        Call this after saving a specimen so other devices see the update
        immediately (< 1 s) without waiting for the 5 s pull sync.
        """
        with self._peers_lock:
            peers = [p for p in self._peers.values() if self._data_sync_allowed(p)]
        if not peers:
            return
        specs = self._get_local_specimens(uid)
        if not specs:
            return

        def _send() -> None:
            try:
                import httpx
            except ImportError:
                return
            payload = {"specimens": specs, "groupCode": self._group_code}
            for peer in peers:
                try:
                    httpx.post(
                        f"{peer.base_url}/api/collab/specimens/push",
                        json=payload,
                        timeout=4.0,
                    )
                except Exception:  # noqa: BLE001
                    pass

        import threading
        threading.Thread(target=_send, daemon=True, name="collab-spec-push").start()

    def _sync_specimens_from_peer(self, peer: PeerInfo) -> int:
        """Pull all specimens from one peer and merge into local DB.

        Returns the number of records written (0 on error or no change).
        """
        if not self._data_sync_allowed(peer):
            return 0
        try:
            import httpx
            resp = httpx.get(f"{peer.base_url}/api/collab/specimens", timeout=8.0)
            if resp.status_code == 200:
                specimens = resp.json()
                if isinstance(specimens, list) and specimens:
                    return self._write_specimens_to_local_db(specimens)
        except Exception as exc:  # noqa: BLE001
            logger.debug("collab: specimen pull from %s failed: %s", peer.base_url, exc)
        return 0

    def pull_all_specimens_from_session(self) -> int:
        """Pull all specimens from same-team same-project peers (used on join)."""
        with self._peers_lock:
            peers = [p for p in self._peers.values() if self._data_sync_allowed(p)]
        total = 0
        for peer in peers:
            total += self._sync_specimens_from_peer(peer)
        if total:
            self._log_activity(
                "specimen_sync",
                detail=f"从会话中同步了 {total} 条标本记录",
            )
        return total

    # ── Zero-config pairing ───────────────────────────────────────────────

    def request_pairing(self, peer_ip: str, peer_port: int) -> bool:
        """Send a pairing request to a specific device (fire-and-forget).

        Returns True if the HTTP POST reached the peer.  The actual acceptance
        is asynchronous — listen for ``pairing_accepted`` signal.
        """
        try:
            import httpx
            resp = httpx.post(
                f"http://{peer_ip}:{peer_port}/api/collab/pairing/request",
                json={
                    "fromIp":       _get_local_ip(),
                    "fromHostname": self._hostname,
                    "groupCode":    self._group_code,
                },
                timeout=4.0,
            )
            return resp.status_code == 200
        except Exception as exc:  # noqa: BLE001
            logger.debug("collab: pairing request failed: %s", exc)
            return False

    def accept_pairing(self, peer_ip: str, peer_port: int,
                       their_group_code: str) -> None:
        """Accept an incoming pairing request.

        Adopts the peer's group code if we don't already have one, then notifies
        the peer so both sides can start syncing immediately.
        """
        if their_group_code and not self._group_code:
            self._group_code = their_group_code
            logger.info("collab: adopted group code %s from peer %s", their_group_code, peer_ip)

        # Notify the peer that we accepted
        try:
            import httpx
            httpx.post(
                f"http://{peer_ip}:{peer_port}/api/collab/pairing/accept",
                json={
                    "fromIp":       _get_local_ip(),
                    "fromHostname": self._hostname,
                    "groupCode":    self._group_code,
                },
                timeout=4.0,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("collab: pairing accept notification failed: %s", exc)

        self.pairing_accepted.emit(peer_ip, peer_ip)
        self._log_activity("pairing", detail=f"已与 {peer_ip} 配对，开始协作同步")
        self.peers_changed.emit()

    def _on_pairing_request_received(self, from_ip: str, from_hostname: str,
                                     their_code: str) -> None:
        """Called from the FastAPI thread when a pairing request arrives."""
        # Emit signal to the main thread — the UI shows a confirmation dialog
        self.pairing_requested.emit(from_ip, from_hostname, their_code)

    def _on_pairing_accept_received(self, from_ip: str, from_hostname: str) -> None:
        """Called when the peer we invited has accepted."""
        self.pairing_accepted.emit(from_ip, from_hostname)
        self._log_activity("pairing", detail=f"{from_hostname} 已接受协作邀请")
        self.peers_changed.emit()

    # ── Status broadcast (push to peers immediately) ──────────────────────

    def broadcast_status_update(self, uid: str, status: str,
                                assignee: Optional[str] = None) -> None:
        """Push a task status change to all online peers (fire-and-forget).

        Runs in a background thread so the UI is never blocked.
        Falls back gracefully when httpx is not installed.
        """
        with self._peers_lock:
            peers = [p for p in self._peers.values() if self._data_sync_allowed(p)]
        if not peers:
            return
        payload = {
            "uid":        uid,
            "status":     status,
            "assignee":   assignee,
            "deviceId":   self._hostname,
            "groupCode":  self._group_code,
        }

        def _send() -> None:
            try:
                import httpx
            except ImportError:
                return
            for peer in peers:
                try:
                    httpx.post(
                        f"{peer.base_url}/api/collab/tasks/update-status",
                        json=payload,
                        timeout=3.0,
                    )
                except Exception:  # noqa: BLE001
                    pass

        import threading
        threading.Thread(target=_send, daemon=True, name="collab-push").start()

    # ── Photo-index reporting ─────────────────────────────────────────────
    # Mirrors: collabPostPhotoIndex(uid, kind)
    # Called by HeliconeService / ArchiveService after completion.

    def post_photo_index(self, uid: str, kind: str, count: int = 1) -> None:
        """Report a photo-index update to all online peers (mirrors collabPostPhotoIndex).

        Parameters
        ----------
        uid:
            Specimen UID that was just composed / archived.
        kind:
            ``"jpg"`` | ``"tiff"`` | ``"zip"``
        count:
            Number of files in the batch (default 1).

        Posts best-effort to each online peer's ``/api/collab/photo-index`` endpoint
        (if the endpoint does not exist on the remote, the 404 is silently swallowed).
        No return value — fire-and-forget.
        """
        with self._peers_lock:
            peers_snapshot = list(self._peers.values())

        if not peers_snapshot:
            return

        record = PhotoIndexRecord(
            uid=uid,
            kind=kind,
            count=count,
            device_id=self._hostname,
        )
        payload = record.to_dict()

        try:
            import httpx
        except ImportError:
            return

        for peer in peers_snapshot:
            try:
                httpx.post(
                    f"{peer.base_url}/api/collab/photo-index",
                    json=payload,
                    timeout=3.0,
                )
            except Exception:  # noqa: BLE001
                pass

    # ── Node info ─────────────────────────────────────────────────────────

    def _node_info(self) -> dict:
        return {
            "hostname":    self._hostname,
            "projectName": self._project_name,
            "projectId":   self._project_id,
            "groupCode":   self._group_code,
            "sessionName": self._session_name,
            "serverTime":  time.time(),
            "lanIp":       _get_local_ip(),
            "port":        self._port,
        }

    def _file_manifest_payload(self, uids: Optional[list[str]] = None) -> dict:
        """Build the current project's media manifest for LAN peers."""
        if not self._project_dir:
            return {"files": []}
        from app.db.db_manager import open_project_db_private
        from app.services.collab_file_sync import manifest_payload

        db = open_project_db_private(self._project_dir)
        try:
            return manifest_payload(
                self._project_dir,
                db=db,
                uids=uids,
                device_id=self._hostname,
                project_id=self._project_id,
            )
        finally:
            db.close()

    def _resolve_file_path(self, relative_path: str) -> Path:
        """Resolve a project-relative media file for the download endpoint."""
        if not self._project_dir:
            raise FileNotFoundError("project directory is not set")
        from app.services.collab_file_sync import resolve_project_relative

        return resolve_project_relative(self._project_dir, relative_path)

    def local_address(self) -> str:
        """Return "ip:port" string for display in the debug drawer."""
        ip = _get_local_ip()
        port = self._port or 5050
        return f"{ip}:{port}"

    # ── Task action stubs (UI-level helpers) ──────────────────────────────

    def assign_task(self, uid: str, operator: str) -> None:
        """Assign task *uid* to *operator* (transition → ASSIGNED).

        Convenience wrapper for the UI context menu; updates the local store
        and emits tasks_changed.  Logs a warning when the transition is invalid.
        """
        try:
            self.store.update_status(uid, TaskStatus.ASSIGNED, assignee=operator)
            self.tasks_changed.emit()
        except ValueError as exc:
            logger.warning("assign_task failed uid=%s: %s", uid, exc)

    def update_task_status(self, uid: str, status: str,
                           seed_status: Optional[str] = None,
                           force: bool = False) -> tuple[bool, str]:
        """Set task *uid* to *status* — UI entry for the workbench phase pills.

        Mirrors oracle ensureCollabTask + /api/collab/tasks/update-status
        (server.js:4015-4031): a missing task is seeded first (at
        *seed_status*, e.g. the status persisted in the project DB across
        restarts), then transitioned.  Never raises: returns (ok, message)
        so the caller can surface failures in the status bar.

        ``force=True`` bypasses the local state machine so explicit human
        marking (sidebar phase dots / batch-bar pills) can jump or step back
        freely — this realigns with the oracle, which never restricts manual
        status assignment (app.js:3303).  The default (force=False) keeps the
        strict transition machine for programmatic/auto callers, so an
        out-of-order jump returns (False, msg) instead of being applied.
        """
        if self.store.get_task(uid) is None:
            self.store.merge_from_peer([{
                "uid": uid,
                "status": seed_status or "created",
                "updatedAt": _now_iso(),
            }])
        try:
            to_status = TaskStatus(status)
        except ValueError:
            return (False, f"未知状态: {status}")
        task = self.store.get_task(uid)
        if task is not None and task.status is to_status:
            return (True, "ok")  # idempotent re-set, oracle allows it
        try:
            self.store.update_status(uid, to_status, force=force)
        except ValueError as exc:
            return (False, str(exc))
        self.tasks_changed.emit()
        self.specimen_status_changed.emit(uid)
        self._log_activity("status_changed", uid,
                           detail=f"编号 {uid} 阶段 → {to_status.value}")
        return (True, "ok")

    def release_task(self, uid: str) -> None:
        """Revoke a UID claim = *release* it for reuse.

        Deletes the task locally and broadcasts a delete to every same-group
        peer so the UID becomes claimable again by anyone.  This deliberately
        bypasses the VOID terminal-state rule — a release is a delete, not a
        status transition.
        """
        self.store.delete(uid)

        with self._peers_lock:
            peers_snapshot = [p for p in self._peers.values() if self._data_sync_allowed(p)]

        if peers_snapshot:
            try:
                import httpx
                for peer in peers_snapshot:
                    try:
                        httpx.post(
                            f"{peer.base_url}/api/collab/tasks/release",
                            json={"uid": uid, "groupCode": self._group_code},
                            timeout=4.0,
                        )
                    except Exception:  # noqa: BLE001
                        pass
            except ImportError:
                pass

        self.tasks_changed.emit()
        self._log_activity("released", uid, detail=f"释放了编号 {uid}")
        self.specimen_status_changed.emit(uid)

    def void_task(self, uid: str) -> None:
        """Revoke a UID claim.  Alias for :meth:`release_task` (release = reuse).

        Kept for backward-compatible callers; semantics are now *release*, not
        a VOID status flip, per the confirmed UX (revoke frees the UID).
        """
        self.release_task(uid)

    def resolve_conflict(self, uid: str) -> None:
        """Resolve a conflicted task by resetting it to CREATED.

        Logs a warning when the transition is invalid.
        """
        try:
            self.store.update_status(uid, TaskStatus.CREATED)
            self.tasks_changed.emit()
        except ValueError as exc:
            logger.warning("resolve_conflict failed uid=%s: %s", uid, exc)
