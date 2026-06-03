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
    last_seen: float = field(default_factory=time.time)
    latency_ms: Optional[float] = None
    manual: bool = False          # True = added via manual IP, not mDNS

    @property
    def base_url(self) -> str:
        return f"http://{self.ip}:{self.port}"


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

    def replace_all(self, tasks: list[TaskRecord]) -> None:
        """Overwrite store (used in tests or full-sync scenarios)."""
        with self._lock:
            self._tasks = {t.uid: t for t in tasks}

    def clear(self) -> None:
        with self._lock:
            self._tasks.clear()


# ── FastAPI application ───────────────────────────────────────────────────────

def _build_fastapi_app(store: TaskStore, node_info_fn: Callable[[], dict]) -> Any:
    """Build and return the FastAPI app.  Imported lazily to avoid startup cost."""
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

class CollabService(QObject):
    """Top-level collaboration service owned by the main window / AppContext.

    Signals
    -------
    peers_changed():          peer list updated (added/removed/latency change)
    tasks_changed():          task store updated after sync
    conflict_detected(str):   uid that triggered a 409
    sync_error(str):          human-readable sync error message
    server_ready(int):        FastAPI server is up, listening on given port
    """

    peers_changed    = pyqtSignal()
    tasks_changed    = pyqtSignal()
    conflict_detected = pyqtSignal(str)        # uid
    sync_error       = pyqtSignal(str)
    server_ready     = pyqtSignal(int)         # port

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.store = TaskStore()
        self._peers: dict[str, PeerInfo] = {}   # key = "ip:port"
        self._peers_lock = threading.Lock()
        self._hostname = socket.gethostname()
        self._port: Optional[int] = None
        self._project_name: str = ""

        self._server_thread: Optional[CollabServerThread] = None
        self._discovery_thread: Optional[CollabDiscoveryThread] = None
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(5000)
        self._sync_timer.timeout.connect(self._sync_all_peers)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self, project_name: str = "", preferred_port: int = 5050) -> None:
        """Start server, mDNS, and sync timer.  Safe to call from main thread."""
        self._project_name = project_name

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
        self._discovery_thread.start()
        self._sync_timer.start()

    def stop(self) -> None:
        """Gracefully shut down all background threads."""
        self._sync_timer.stop()
        if self._discovery_thread:
            self._discovery_thread.stop()
        if self._server_thread:
            self._server_thread.stop()

    # ── Peer management ───────────────────────────────────────────────────

    def _on_peer_found(self, ip: str, port: int, hostname: str) -> None:
        key = f"{ip}:{port}"
        with self._peers_lock:
            self._peers[key] = PeerInfo(ip=ip, port=port, hostname=hostname)
        logger.info("collab: peer found %s (%s:%d)", hostname, ip, port)
        self.peers_changed.emit()

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

        # 2. Remote check — broadcast POST, abort on first 409
        peers_snapshot: list[PeerInfo]
        with self._peers_lock:
            peers_snapshot = list(self._peers.values())

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

    # ── Node info ─────────────────────────────────────────────────────────

    def _node_info(self) -> dict:
        return {
            "hostname":    self._hostname,
            "projectName": self._project_name,
            "lanIp":       _get_local_ip(),
            "port":        self._port,
        }

    def local_address(self) -> str:
        """Return "ip:port" string for display in the debug drawer."""
        ip = _get_local_ip()
        port = self._port or 5050
        return f"{ip}:{port}"


# ── Helpers ───────────────────────────────────────────────────────────────────

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
