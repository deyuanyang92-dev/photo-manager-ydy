"""collab_service.py — P2P LAN collaboration service for the PyQt6 workbench.

Architecture (confirmed by user 2026-06-02, oracle: collab.md § Desktop GUI):

  CollabService spawns two QThread workers:
    • CollabServerThread  — FastAPI + uvicorn embedded server (port 5050)
    • CollabDiscoveryThread — zeroconf mDNS registration + peer discovery

  A QTimer (5 s) drives CollabSyncWorker which does HTTP pulls from known
  peers using httpx (synchronous, runs in the same sync slot, cheap).

  Each peer exposes:
    GET  /api/node/info       → {hostname, projectName, lanIp, port}
    GET  /api/node/health     → {"ok": true}
    GET  /api/collab/tasks    → list[TaskRecord]
    POST /api/collab/tasks/create   → 201 | 409 Conflict
    POST /api/collab/tasks/update-status
    GET  /api/collab/specimens      → list[SpecimenRecord]
    POST /api/collab/specimens/push → accept push from peer

Conflict (409) policy:
    Creating a UID that already exists on *any* online peer returns HTTP 409.
    The creator must abandon or rename the UID.

Manual IP fallback:
    mDNS may fail across VLANs or on Windows Firewall-strict networks.
    Call CollabService.add_manual_peer(ip, port) to hard-add a peer endpoint.

Scope:
    L1 sync only: specimenTasks (UID + status + assignee).
    L2: specimen JSON pushed on create/update.
    L3 (file transfer): out of scope.

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
from typing import Any, Callable, Optional

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

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
    group_code: str = ""          # collaboration-group code; only matching peers sync
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

    def all(self) -> list[TaskRecord]:
        with self._lock:
            return list(self._tasks.values())

    def get(self, uid: str) -> Optional[TaskRecord]:
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
                      assignee: Optional[str] = None) -> TaskRecord:
        """Update task status.  Raises ValueError on invalid transition or unknown UID."""
        with self._lock:
            task = self._tasks.get(uid)
            if task is None:
                raise ValueError(f"Unknown UID: {uid}")
            if not is_valid_transition(task.status, to_status):
                raise ValueError(
                    f"Invalid transition: {task.status} → {to_status}"
                )
            task.status = to_status
            if assignee is not None:
                task.assignee = assignee
            task.updated_at = _now_iso()
            return task

    def merge_from_peer(self, remote_tasks: list[dict]) -> int:
        """Merge peer task list; newer updated_at wins.  Returns changed count."""
        changed = 0
        with self._lock:
            for rd in remote_tasks:
                uid = rd.get("uid")
                if not uid:
                    continue
                remote = TaskRecord.from_dict(rd)
                local = self._tasks.get(uid)
                if local is None or remote.updated_at > local.updated_at:
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

def _build_fastapi_app(store: TaskStore, node_info_fn: Callable[[], dict]) -> Any:
    """Build and return the FastAPI app.  Imported lazily to avoid startup cost.

    The fastapi names are bound into module globals (``global`` below) so that
    the nested endpoint functions' ``request: Request`` annotations resolve via
    ``typing.get_type_hints`` (which reads the function's ``__globals__``).
    A purely function-local import leaves them unresolvable and FastAPI then
    mis-reads ``request`` as a query parameter → every POST 422s.
    """
    global FastAPI, HTTPException, Request, JSONResponse
    try:
        from fastapi import FastAPI, HTTPException, Request
        from fastapi.responses import JSONResponse
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
        return [t.to_dict() for t in store.all()]

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

    @app.post("/api/collab/photo-index")
    async def receive_photo_index(request: Request) -> dict:
        """Receive photo-index report from a peer after helicon/archive completion.

        Mirrors web collabPostPhotoIndex — peers call this to inform us that
        their specimen has jpg/tiff/zip files ready.  We acknowledge and let
        the UI subscribe to signals for richer handling.
        """
        body = await request.json()
        uid = body.get("uid", "")
        kind = body.get("kind", "")
        count = int(body.get("count", 0))
        logger.debug("collab: photo-index received uid=%s kind=%s count=%d",
                     uid, kind, count)
        return {"ok": True, "uid": uid, "kind": kind, "count": count}

    @app.post("/api/collab/specimens/push")
    async def receive_specimen_push(request: Request) -> dict:
        """Accept specimen record pushed from a peer (L2 sync).

        For now we just acknowledge — the view can subscribe to the
        CollabService.specimen_received signal for richer handling.
        """
        body = await request.json()
        uid = body.get("uid", "")
        logger.debug("collab: specimen push received uid=%s", uid)
        return {"ok": True, "uid": uid}

    @app.get("/api/collab/specimens")
    async def list_specimens() -> list:
        """Return local specimen records.  Stubbed — override via app context."""
        return []

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
                 preferred_port: int = 5050) -> None:
        super().__init__()
        self._store = store
        self._node_info_fn = node_info_fn
        self._preferred_port = preferred_port
        self._actual_port: Optional[int] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

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
        app = _build_fastapi_app(self._store, self._node_info_fn)

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
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self.quit()
        self.wait(3000)


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
        if ip == self._local_ip and port == self._local_port:
            return   # skip self
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
    sync_error       = pyqtSignal(str)
    server_ready     = pyqtSignal(int)         # port
    offline_drafts_changed = pyqtSignal()      # draft queue added/cleared
    diagnostics_changed = pyqtSignal()         # self-diagnostics list updated

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.store = TaskStore()
        self._peers: dict[str, PeerInfo] = {}   # key = "ip:port"
        self._peers_lock = threading.Lock()
        self._hostname = socket.gethostname()
        self._port: Optional[int] = None
        self._project_name: str = ""
        self._group_code: str = ""
        self._running: bool = False
        self._diagnostics: list[Diagnostic] = []
        self._discovery_error: str = ""

        # Offline draft queue (mirrors loadCollabOfflineDrafts / saveCollabOfflineDrafts)
        self._offline_drafts: list[OfflineDraft] = []
        self._offline_drafts_lock = threading.Lock()

        self._server_thread: Optional[CollabServerThread] = None
        self._discovery_thread: Optional[CollabDiscoveryThread] = None
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(5000)
        self._sync_timer.timeout.connect(self._sync_all_peers)
        # Retry timer — attempt to flush offline drafts when peers are present
        self._retry_timer = QTimer(self)
        self._retry_timer.setInterval(15000)   # 15 s retry cadence
        self._retry_timer.timeout.connect(self._maybe_retry_offline_drafts)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self, project_name: str = "", preferred_port: int = 5050,
              group_code: str = "") -> None:
        """Start server, mDNS, and sync timer.  Safe to call from main thread.

        Idempotent: a second call while already running is a no-op.
        """
        if self._running:
            return
        self._project_name = project_name
        if group_code:
            self._group_code = group_code.strip()
        self._running = True

        self._server_thread = CollabServerThread(
            store=self.store,
            node_info_fn=self._node_info,
            preferred_port=preferred_port,
        )
        self._server_thread.started_on_port.connect(self._on_server_started)
        self._server_thread.server_error.connect(
            lambda msg: self.sync_error.emit(f"Server error: {msg}")
        )
        self._server_thread.start()

    def _on_server_started(self, port: int) -> None:
        self._port = port
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
        self.run_diagnostics()

    def stop(self) -> None:
        """Gracefully shut down all background threads.  Idempotent."""
        self._running = False
        self._sync_timer.stop()
        self._retry_timer.stop()
        if self._discovery_thread:
            self._discovery_thread.stop()
            self._discovery_thread = None
        if self._server_thread:
            self._server_thread.stop()
            self._server_thread = None

    def is_running(self) -> bool:
        """True between start() and stop()."""
        return self._running

    # ── Collaboration group ───────────────────────────────────────────────

    @property
    def group_code(self) -> str:
        return self._group_code

    def set_group_code(self, code: str) -> None:
        """Set the collaboration-group code at runtime (e.g. from settings)."""
        self._group_code = (code or "").strip()

    def _group_matches(self, peer: PeerInfo) -> bool:
        """A peer syncs with us only when both sides share a non-empty code."""
        return bool(self._group_code) and peer.group_code == self._group_code

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
        """Record an mDNS discovery failure and refresh diagnostics."""
        self._discovery_error = msg
        self.run_diagnostics()

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
                if not peer.group_code:
                    peer.group_code = data.get("groupCode", "")
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
        targets = [(h, p) for h in hosts for p in ports
                   if not (h == local_ip and p == self._port)]

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
                    project_name=data.get("projectName", ""),
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
        key = f"{ip}:{port}"
        with self._peers_lock:
            self._peers[key] = PeerInfo(ip=ip, port=port, hostname=hostname)
        logger.info("collab: peer found %s (%s:%d)", hostname, ip, port)
        self.peers_changed.emit()
        # Enrich with group_code / project_name from /api/node/info so the peer
        # can pass the group filter.  HTTP → do it off the main thread.
        peer = self._peers[key]
        self._spawn(lambda: (self._fetch_peer_info(peer), self.peers_changed.emit()))

    def _on_peer_lost(self, ip: str, port: int) -> None:
        key = f"{ip}:{port}"
        with self._peers_lock:
            self._peers.pop(key, None)
        logger.info("collab: peer lost %s:%d", ip, port)
        self.peers_changed.emit()

    def add_manual_peer(self, ip: str, port: int) -> None:
        """Manually register a peer (fallback when mDNS fails across VLANs)."""
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
        for peer in peers_snapshot:
            changed_total += self._sync_peer(peer)

        if changed_total:
            self.tasks_changed.emit()

    def _sync_peer(self, peer: PeerInfo) -> int:
        """Pull /api/collab/tasks from one peer and merge.  Returns changed count."""
        if not self._group_matches(peer):
            return 0  # different (or no) collaboration group → never sync
        try:
            import httpx
            t0 = time.monotonic()
            resp = httpx.get(f"{peer.base_url}/api/collab/tasks", timeout=4.0)
            peer.latency_ms = (time.monotonic() - t0) * 1000
            peer.last_seen = time.time()
            if resp.status_code == 200:
                remote_tasks: list[dict] = resp.json()
                return self.store.merge_from_peer(remote_tasks)
        except Exception as exc:  # noqa: BLE001
            logger.debug("collab: sync failed for %s: %s", peer.base_url, exc)
        return 0

    # ── Task creation (with remote 409 check) ─────────────────────────────

    def create_task(self, uid: str, assignee: Optional[str] = None,
                    device_id: Optional[str] = None) -> tuple[bool, str]:
        """Create a new task, broadcasting to all online peers.

        Returns (success: bool, message: str).
        On conflict returns (False, "409: …conflict message…").

        NOTE: Network 409 checks require live peers — tested with doubles.
        """
        # 1. Local check
        if self.store.exists(uid):
            msg = f"409: UID '{uid}' already exists on this device"
            self.conflict_detected.emit(uid)
            return False, msg

        # 2. Remote check — broadcast POST to same-group peers, abort on first 409
        peers_snapshot: list[PeerInfo]
        with self._peers_lock:
            peers_snapshot = [p for p in self._peers.values() if self._group_matches(p)]

        for peer in peers_snapshot:
            ok, conflict_msg = self._remote_create(peer, uid, assignee, device_id)
            if not ok:
                self.conflict_detected.emit(uid)
                return False, conflict_msg

        # 3. Record locally
        try:
            self.store.create(uid, assignee=assignee, device_id=device_id,
                              project_name=self._project_name)
        except ValueError as exc:
            # race condition — another peer created it between steps 1 and 3
            self.conflict_detected.emit(uid)
            return False, str(exc)

        self.tasks_changed.emit()
        return True, "ok"

    def _remote_create(self, peer: PeerInfo, uid: str,
                       assignee: Optional[str], device_id: Optional[str]
                       ) -> tuple[bool, str]:
        """POST create to a single peer.  Returns (True, "") or (False, reason)."""
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
                return False, f"409: {detail} (peer {peer.hostname or peer.ip})"
        except Exception as exc:  # noqa: BLE001
            # Network failure is treated as "peer unavailable, not a conflict"
            logger.debug("collab: remote create failed for %s: %s", peer.base_url, exc)
        return True, ""

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

    def mark_offline_draft(self, uid: str,
                           assignee: Optional[str] = None,
                           device_id: Optional[str] = None) -> OfflineDraft:
        """Queue *uid* as an offline draft (mirrors collabMarkOfflineDraft).

        Called when ``create_task`` detects a network failure (at least one
        peer unreachable — not a 409 conflict).  The draft is stored in-memory
        and retried when ``retry_offline_drafts`` is called.

        Deduplicates by uid: calling again for the same uid is a no-op.
        """
        with self._offline_drafts_lock:
            if any(d.uid == uid for d in self._offline_drafts):
                return next(d for d in self._offline_drafts if d.uid == uid)
            draft = OfflineDraft(uid=uid, assignee=assignee, device_id=device_id)
            self._offline_drafts.append(draft)
        logger.debug("collab: offline draft queued uid=%s", uid)
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
            ok, msg = self.create_task(
                uid=draft.uid,
                assignee=draft.assignee,
                device_id=draft.device_id,
            )
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
            self.offline_drafts_changed.emit()

        return len(promoted)

    def _maybe_retry_offline_drafts(self) -> None:
        """Timer slot: silently attempt to flush offline drafts."""
        try:
            self.retry_offline_drafts()
        except Exception:  # noqa: BLE001
            pass

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
            "groupCode":   self._group_code,
            "serverTime":  time.time(),
            "lanIp":       _get_local_ip(),
            "port":        self._port,
        }

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

    def release_task(self, uid: str) -> None:
        """Revoke a UID claim = *release* it for reuse.

        Deletes the task locally and broadcasts a delete to every same-group
        peer so the UID becomes claimable again by anyone.  This deliberately
        bypasses the VOID terminal-state rule — a release is a delete, not a
        status transition.
        """
        self.store.delete(uid)

        with self._peers_lock:
            peers_snapshot = [p for p in self._peers.values() if self._group_matches(p)]

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


