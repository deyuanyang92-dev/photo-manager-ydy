"""collab_service.py — P2P LAN collaboration service for the PyQt6 workbench.

Architecture (confirmed by user 2026-06-02, oracle: collab.md § Desktop GUI):

  CollabService spawns two QThread workers:
    • CollabServerThread  — FastAPI + uvicorn embedded server (port 5050)
    • CollabDiscoveryThread — zeroconf mDNS registration + peer discovery

  A QTimer (5 s) schedules a pull cycle.  The cycle runs OFF the Qt main
  thread via ``_spawn`` → ``_run_sync_cycle`` (a short-lived daemon thread)
  so a slow/dead peer cannot freeze the UI; a non-blocking ``_sync_lock``
  skips a firing if the previous cycle is still in flight.

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

import json
import logging
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from app.models.activity_log import ActivityEntry, ActivityLog

logger = logging.getLogger(__name__)


# ── Split modules (re-exported for backward compatibility) ────────────────────
# Datatypes / helpers   → app.services.collab_types
# In-memory task store  → app.services.collab_store
# FastAPI endpoints     → app.services.collab_api
# Server + mDNS threads → app.services.collab_net
# External code (views, tests) may keep importing everything from here.

from app.services.collab_types import (  # noqa: F401
    Diagnostic,
    OfflineDraft,
    PeerInfo,
    PhotoIndexRecord,
    TaskRecord,
    TaskStatus,
    _VALID_TRANSITIONS,
    _get_local_ip,
    _missing_deps,
    _now_iso,
    get_httpx,
    is_valid_transition,
)
from app.services.collab_store import TaskStore  # noqa: F401
from app.services.collab_api import _build_fastapi_app  # noqa: F401
from app.services.collab_net import (  # noqa: F401
    CollabDiscoveryThread,
    CollabServerThread,
    _MDNS_SERVICE_TYPE,
    _BrowserHandler,
)
from app.services.collab_diagnostics import (  # noqa: F401
    CLOCK_SKEW_THRESHOLD_MS,
    build_diagnostics,
    overall_health as _overall_health,
)
from app.services.collab_specimen_sync import (  # noqa: F401
    SPEC_SYNC_COLS,
    get_local_specimens,
    stamp_specimen,
    write_specimens_to_local_db,
)


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
    offline_drafts_changed(): offline draft queue updated
    """

    _SPEC_SYNC_COLS = SPEC_SYNC_COLS

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
    peer_join_review  = pyqtSignal(str, int, str)  # ip, port, display label
    specimens_updated = pyqtSignal()               # local DB got new/updated specimens
    project_bind_suggested = pyqtSignal(str, str, str)  # peer_hostname, project_name, sync_code
    photo_index_received = pyqtSignal(str, str, int, str)  # uid, kind, count, device_id

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
        self._shared_project_dirs: set[str] = set()
        self._project_dir: str = ""
        self._group_code: str = ""
        self._session_name: str = ""   # human-readable session label
        self._operator_name: str = ""  # user-chosen display name (broadcast to peers)
        self._running: bool = False
        self._diagnostics: list[Diagnostic] = []
        self._discovery_error: str = ""

        # Offline draft queue (mirrors loadCollabOfflineDrafts / saveCollabOfflineDrafts)
        self._offline_drafts: list[OfflineDraft] = []
        self._offline_drafts_lock = threading.Lock()

        # Re-entrancy guard for the pull-sync cycle: only one cycle runs at a
        # time.  The cycle is offloaded off the Qt main thread (see
        # _sync_all_peers) so a 5 s timer can fire again before a slow cycle
        # finishes — this lock makes the second firing a no-op instead of
        # racing the store / spec-sync counter.
        self._sync_lock = threading.Lock()

        # Same-name project bind suggestions already shown (peer project_ids);
        # prevents re-nagging after the user declines.  Reset on project switch.
        self._bind_prompted: set[str] = set()

        # Distributed-claim collisions already surfaced (uid set); prevents the
        # 5 s pull-sync re-firing conflict_detected for the same split-brain.
        # Reset on project switch.
        self._claim_collision_prompted: set[str] = set()

        from app.services.collab_peer_trust import CollabPeerTrustStore

        self._peer_trust = CollabPeerTrustStore()
        # Session-only: avoid duplicate join-review dialogs for one peer.
        self._peer_review_prompted: set[str] = set()
        # v0.56: 同一次 peer_join_review emit 会触达多个界面(协作页 + 工作台
        # 侧栏), 弹窗权先到先得, 否则一次加入弹两个确认框且答案互踩。
        self._peer_review_prompt_claimed: set[str] = set()

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
        self.reload_shared_project_dirs()
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
            photo_index_fn=self._on_photo_index_received,
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

    def ensure_running(self, ctx=None) -> bool:
        """Start the embedded server when a team code exists but service is idle.

        Called from协作中心 / 协作面板 / 照片同步 so users are not stuck on
        「协作未启动」 after saving a team code or reopening the app.
        """
        if self._running:
            return True

        code = (self._group_code or "").strip()
        project_dir = self._project_dir or self._project_name or ""
        settings = getattr(ctx, "settings", None) if ctx is not None else None

        if not code and settings is not None:
            raw_code = getattr(settings, "team_code", "")
            if isinstance(raw_code, str):
                code = raw_code.strip()
                if code:
                    self._group_code = code

        if not code:
            return False

        if ctx is not None and settings is not None:
            for key in ("operator_name",):
                raw_op = getattr(settings, key, "")
                if isinstance(raw_op, str) and raw_op.strip():
                    self.set_operator_name(raw_op.strip())
                    break
            if not self._operator_name:
                try:
                    qs = getattr(settings, "_qs", settings)
                    saved = str(qs.value("user/current_user", "", type=str)).strip()
                    if saved:
                        self.set_operator_name(saved)
                except Exception:  # noqa: BLE001
                    pass

        if ctx is not None:
            raw_dir = getattr(ctx, "current_project_dir", None)
            if isinstance(raw_dir, str) and raw_dir.strip():
                project_dir = raw_dir.strip()
            elif settings is not None:
                raw_last = getattr(settings, "last_project_dir", "")
                if isinstance(raw_last, str) and raw_last.strip():
                    project_dir = raw_last.strip()

        try:
            self.start(
                project_name=project_dir,
                group_code=code,
                project_dir=project_dir,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("collab ensure_running failed: %s", exc)
            return False
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
        """List distinct projects teammates advertise (incl. multi-project share list)."""
        from app.services.collab_share_registry import iter_peer_shared_projects
        from app.services.project_identity_service import project_sync_code

        with self._peers_lock:
            peers = [p for p in self._peers.values() if self._group_matches(p)]
        by_id: dict[str, dict] = {}
        for p in peers:
            for pid, name in iter_peer_shared_projects(p):
                entry = by_id.setdefault(pid, {
                    "name": name or pid,
                    "code": project_sync_code(pid, project_name=name),
                    "peer_count": 0,
                })
                entry["peer_count"] += 1
        return sorted(by_id.values(), key=lambda e: e["name"])

    def set_shared_project_dirs(self, directories) -> None:
        """Persist and advertise which local workspaces are shared with the team."""
        normalised: set[str] = set()
        for item in directories or []:
            text = str(item or "").strip()
            if not text:
                continue
            try:
                normalised.add(str(Path(text).resolve()))
            except OSError:
                normalised.add(text)
        self._shared_project_dirs = normalised
        try:
            from PyQt6.QtCore import QSettings
            from app.config.settings import _APP, _ORG
            from app.services.collab_share_registry import save_shared_dirs
            save_shared_dirs(QSettings(_ORG, _APP), normalised)
        except Exception:  # noqa: BLE001
            pass
        if self._project_dir:
            try:
                current = str(Path(self._project_dir).resolve())
            except OSError:
                current = self._project_dir
            if current in self._shared_project_dirs and not self._project_id:
                self._project_id = self._ensure_project_id(current)
        self.peers_changed.emit()

    def reload_shared_project_dirs(self, settings_qs=None) -> None:
        """Load saved share ticks; default to the active workspace."""
        from app.services.collab_share_registry import default_shared_dirs, load_shared_dirs

        if settings_qs is None:
            from PyQt6.QtCore import QSettings
            from app.config.settings import _APP, _ORG
            settings_qs = QSettings(_ORG, _APP)
        current = str(self._project_dir or "")
        saved = load_shared_dirs(settings_qs)
        dirs = saved if saved else default_shared_dirs(settings_qs, current_directory=current or None)
        self.set_shared_project_dirs(dirs)

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

    def reset_project_sync_code(self) -> str:
        """Break current project-code pairing by assigning a fresh local ID."""
        if not self._project_dir:
            raise ValueError("current project is not open")
        from app.db.db_manager import open_project_db_private
        from app.services.project_identity_service import (
            read_project_identity,
            reset_project_identity,
        )

        db = open_project_db_private(self._project_dir)
        try:
            previous = read_project_identity(db)
            self._project_id = reset_project_identity(
                db,
                project_name=Path(str(self._project_name or self._project_dir)).name,
                previous_project_id=previous,
            )
        finally:
            db.close()
        self._log_activity(
            "project-sync-code",
            detail="当前项目已解除项目码配对",
        )
        self.peers_changed.emit()
        return self._project_id

    # ── Activity logging ──────────────────────────────────────────────────

    def _log_activity(self, action: str, target_uid: str = "",
                      actor: str = "", detail: str = "",
                      severity: str = "info") -> None:
        """Append an entry to the activity log and emit *activity_logged*."""
        if not actor:
            actor = self._operator_name or self._hostname
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

    @property
    def operator_name(self) -> str:
        return self._operator_name

    def set_operator_name(self, name: str) -> None:
        """Set the display name broadcast to teammates via /api/node/info."""
        cleaned = (name or "").strip()
        self._operator_name = cleaned
        if cleaned:
            self._session_name = f"{cleaned}的会话"

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
        self.ensure_running()
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
        self.ensure_running()
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
        self._claim_collision_prompted.clear()
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

    def _task_sync_allowed(self, peer: PeerInfo) -> bool:
        """Task/UID coordination is team-scoped.

        A team may have several projects open at once.  UID claims, task status,
        assignment, release, and conflict checks stay shared across the whole
        team, while specimen rows and media files remain project-scoped.
        """
        return self._group_matches(peer) and self._peer_sync_allowed(peer)

    @staticmethod
    def _peer_has_operator(peer: PeerInfo) -> bool:
        return bool(str(getattr(peer, "operator_name", "") or "").strip())

    def _peer_key(self, peer: PeerInfo) -> str:
        from app.services.collab_peer_trust import peer_key

        return peer_key(peer.ip, peer.port)

    def is_peer_blocked(self, peer: PeerInfo) -> bool:
        return self._peer_trust.is_blocked(self._peer_key(peer))

    def is_peer_trusted(self, peer: PeerInfo) -> bool:
        return self._peer_trust.is_trusted(self._peer_key(peer))

    def is_peer_pending_review(self, peer: PeerInfo) -> bool:
        """Same-team peer with a name but not yet trusted/blocked."""
        if not self._group_matches(peer):
            return False
        if not self._strict_trust_mode():
            return False
        key = self._peer_key(peer)
        if self._peer_trust.is_blocked(key) or self._peer_trust.is_trusted(key):
            return False
        return self._peer_has_operator(peer)

    def _strict_trust_mode(self) -> bool:
        """Once any peer is trusted or blocked, new teammates need approval."""
        return bool(self._peer_trust.trusted_keys() or self._peer_trust.blocked_keys())

    def _peer_sync_allowed(self, peer: PeerInfo) -> bool:
        """Gate all sync paths: blocked / unknown operators never sync."""
        if not self._group_matches(peer):
            return True
        key = self._peer_key(peer)
        if self._peer_trust.is_blocked(key):
            return False
        if not self._strict_trust_mode():
            return True
        if not self._peer_has_operator(peer):
            return False
        return self._peer_trust.is_trusted(key)

    def trust_peer(self, ip: str, port: int) -> None:
        """Allow sync with a teammate (persisted)."""
        from app.services.collab_peer_trust import peer_key

        key = peer_key(ip, port)
        self._peer_trust.trust(key)
        self._peer_review_prompted.discard(key)
        with self._peers_lock:
            peer = self._peers.get(key)
        if peer is not None:
            from app.services.collab_status import peer_display_name

            name = peer_display_name(peer)
            self._log_activity("joined", actor=name, detail=f"{name} 已加入协作组")
        self.peers_changed.emit()
        QTimer.singleShot(100, self._sync_all_peers)

    def block_peer(self, ip: str, port: int, *, reason: str = "") -> None:
        """Locally block a device — remove from peers and skip future sync."""
        from app.services.collab_peer_trust import peer_key

        key = peer_key(ip, port)
        self._peer_trust.block(key)
        self._peer_review_prompted.discard(key)
        with self._peers_lock:
            peer = self._peers.pop(key, None)
        from app.services.collab_status import peer_display_name

        label = peer_display_name(peer) if peer else key
        detail = reason or f"已屏蔽 {label}，本机不再与其同步"
        self._log_activity("blocked", actor=label, detail=detail, severity="warn")
        self.peers_changed.emit()

    def _remove_peer_key(self, key: str) -> None:
        with self._peers_lock:
            self._peers.pop(key, None)
        self.peers_changed.emit()

    def _review_peer_after_enrich(self, peer: PeerInfo) -> None:
        """Apply trust policy once /node/info is known."""
        if not self._group_matches(peer):
            return

        key = self._peer_key(peer)
        if self._peer_trust.is_blocked(key):
            self._remove_peer_key(key)
            return

        if not self._peer_has_operator(peer):
            self.block_peer(
                peer.ip,
                peer.port,
                reason=f"未知用户（{peer.hostname or peer.ip} 未填写操作者姓名），已自动屏蔽",
            )
            return

        if self._peer_trust.is_trusted(key):
            from app.services.collab_status import peer_display_name

            name = peer_display_name(peer)
            self._log_activity("joined", actor=name, detail=f"{name} 加入了协作组")
            return

        if not self._strict_trust_mode():
            from app.services.collab_status import peer_display_name

            name = peer_display_name(peer)
            self._log_activity("joined", actor=name, detail=f"{name} 加入了协作组")
            return

        if key in self._peer_review_prompted:
            return

        self._peer_review_prompted.add(key)
        from app.services.collab_status import peer_display_name

        self._peer_review_prompt_claimed.discard(key)
        self.peer_join_review.emit(peer.ip, peer.port, peer_display_name(peer))

    def claim_peer_join_prompt(self, ip: str, port: int) -> bool:
        """UI 侧领取「新设备加入」弹窗权.

        peer_join_review 是单例信号, 协作页与工作台侧栏可能同时连接;
        每次 emit 只有第一个调用本方法的界面拿到 True 并弹窗, 其余跳过。
        """
        key = f"{ip}:{port}"
        if key in self._peer_review_prompt_claimed:
            return False
        self._peer_review_prompt_claimed.add(key)
        return True

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
        if not mine and not theirs:
            return True
        if not mine or not theirs:
            return False
        return mine == theirs

    def _data_sync_allowed(self, peer: PeerInfo) -> bool:
        """Project-scoped data flows only within the same team and project.

        This gates specimen metadata and media-related notifications.  Do not
        use it for UID claims/status; those are intentionally team-scoped via
        ``_task_sync_allowed``.
        """
        return self._group_matches(peer) and self._project_matches(peer) and self._peer_sync_allowed(peer)

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

    def diagnostics(self) -> list[Diagnostic]:
        """Return the last computed diagnostics list."""
        return list(self._diagnostics)

    def run_diagnostics(self) -> list[Diagnostic]:
        """Run the synchronous health checks and store/emit the result.

        Network probes (reachability, clock skew measurement) run separately in
        a background worker and call this again once peer attributes are updated.
        """
        self._diagnostics = build_diagnostics(
            group_code=self._group_code,
            discovery_error=self._discovery_error,
            peers=self.peers(),
            port=self._port,
        )
        self.diagnostics_changed.emit()
        return self._diagnostics

    def overall_health(self) -> str:
        """Roll up to a traffic-light colour: red > yellow > green."""
        return _overall_health(self._diagnostics)

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
                        httpx = get_httpx()
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
        # §7 keep-old: inlined httpx.get(/info)+status+reachable moved to _fetch_node_info
        data = self._fetch_node_info(peer.base_url, 3.0)
        if data is None:
            peer.reachable = False
            return
        peer.reachable = True
        st = data.get("serverTime")
        if isinstance(st, (int, float)):
            peer.clock_skew_ms = (time.time() - float(st)) * 1000.0
        peer.project_name = data.get("projectName", peer.project_name)
        peer.project_id = data.get("projectId", peer.project_id)
        raw_shared = data.get("sharedProjects")
        if isinstance(raw_shared, list):
            peer.shared_projects = raw_shared
        if not peer.group_code:
            peer.group_code = data.get("groupCode", "")
        if data.get("sessionName"):
            peer.session_name = data["sessionName"]
        op = str(data.get("operatorName") or "").strip()
        if op:
            peer.operator_name = op
        httpx = get_httpx()
        if httpx is not None:
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
        # §7 keep-old: was ``try: import httpx / except ImportError: return []``
        httpx = get_httpx()
        if httpx is None:
            return []

        local_ip = _get_local_ip()
        targets = [(h, p) for h in hosts for p in ports if h != local_ip]

        def _probe(target: tuple[str, int]) -> Optional[PeerInfo]:
            host, port = target
            # §7 keep-old: inlined httpx.get(/info)+status moved to _fetch_node_info
            data = self._fetch_node_info(f"http://{host}:{port}", timeout)
            if data is None:
                return None
            return PeerInfo(
                ip=host, port=port,
                hostname=data.get("hostname", ""),
                group_code=data.get("groupCode", ""),
                session_name=data.get("sessionName", ""),
                operator_name=str(data.get("operatorName") or "").strip(),
                project_name=data.get("projectName", ""),
                project_id=data.get("projectId", ""),
                manual=True,
            )

        found: list[PeerInfo] = []
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
            for peer in pool.map(_probe, targets):
                if peer is None:
                    continue
                key = f"{peer.ip}:{peer.port}"
                if self._peer_trust.is_blocked(key):
                    continue
                with self._peers_lock:
                    self._peers[key] = peer
                found.append(peer)
                self._spawn(lambda p=peer: self._review_peer_after_enrich(p))

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
        if self._peer_trust.is_blocked(key):
            return
        # Capture the peer reference INSIDE the lock: a concurrent peer_lost
        # (or a peers_changed slot) can pop the key after we release the lock,
        # and the old ``peer = self._peers[key]`` read below would KeyError.
        with self._peers_lock:
            peer = self._peers[key] = PeerInfo(ip=ip, port=port, hostname=hostname)
        logger.info("collab: peer found %s (%s:%d)", hostname, ip, port)
        self.peers_changed.emit()
        self._spawn(lambda: self._after_peer_enriched(peer))

    def _after_peer_enriched(self, peer: PeerInfo) -> None:
        """Fetch /node/info off-thread, then apply trust / join-review policy."""
        self._fetch_peer_info(peer)
        self._review_peer_after_enrich(peer)
        self.peers_changed.emit()

    def _on_peer_lost(self, ip: str, port: int) -> None:
        key = f"{ip}:{port}"
        with self._peers_lock:
            peer = self._peers.pop(key, None)
        hostname = peer.hostname if peer else f"{ip}:{port}"
        from app.services.collab_status import peer_display_name

        label = peer_display_name(peer) if peer else hostname
        logger.info("collab: peer lost %s:%d", ip, port)
        self.peers_changed.emit()
        self._log_activity("left", actor=label, detail=f"{label} 离开了协作组")

    def add_manual_peer(self, ip: str, port: int) -> None:
        """Manually register a peer (fallback when mDNS fails across VLANs)."""
        if ip == _get_local_ip():
            return
        key = f"{ip}:{port}"
        if self._peer_trust.is_blocked(key):
            return
        with self._peers_lock:
            self._peers[key] = PeerInfo(ip=ip, port=port, manual=True)
            peer = self._peers[key]
        self.peers_changed.emit()
        self._spawn(lambda: self._after_peer_enriched(peer))

    def remove_manual_peer(self, ip: str, port: int) -> None:
        """Remove a manually added peer."""
        key = f"{ip}:{port}"
        with self._peers_lock:
            self._peers.pop(key, None)
        self.peers_changed.emit()

    def peers(self) -> list[PeerInfo]:
        with self._peers_lock:
            return list(self._peers.values())

    def _fetch_node_info(self, base_url: str, timeout: float = 3.0) -> Optional[dict]:
        """GET /api/node/info from a peer; return parsed body or None.

        Shared atom for the places that probe a peer's identity
        (_fetch_peer_info, _probe_peer, scan_lan).  Returns None on any failure
        (network error, non-200, httpx missing) so callers treat "unreadable"
        uniformly instead of each re-implementing the GET + status check.
        """
        httpx = get_httpx()
        if httpx is None:
            return None
        try:
            resp = httpx.get(f"{base_url}/api/node/info", timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
        except Exception:  # noqa: BLE001
            pass
        return None

    def _fetch_peer_info(self, peer: PeerInfo) -> None:
        """Try to enrich PeerInfo with hostname/projectName from /api/node/info."""
        # §7 keep-old: inlined httpx.get(/info)+status check moved to _fetch_node_info
        data = self._fetch_node_info(peer.base_url, 3.0)
        if isinstance(data, dict):
            peer.hostname = data.get("hostname", peer.hostname)
            peer.project_name = data.get("projectName", "")
            peer.project_id = data.get("projectId", "")
            peer.group_code = data.get("groupCode", "")
            op = str(data.get("operatorName") or "").strip()
            if op:
                peer.operator_name = op
            st = data.get("serverTime")
            if isinstance(st, (int, float)):
                peer.clock_skew_ms = (time.time() - float(st)) * 1000.0
            raw_shared = data.get("sharedProjects")
            if isinstance(raw_shared, list):
                peer.shared_projects = raw_shared
            peer.last_seen = time.time()

    def _ensure_peer_clock_skew(self, peer: PeerInfo) -> None:
        """Measure clock skew once per peer before LWW merge (sync path)."""
        if peer.clock_skew_ms is not None:
            return
        data = self._fetch_node_info(peer.base_url, 2.0)
        if not isinstance(data, dict):
            return
        st = data.get("serverTime")
        if isinstance(st, (int, float)):
            peer.clock_skew_ms = (time.time() - float(st)) * 1000.0

    # ── Sync ──────────────────────────────────────────────────────────────

    def _sync_all_peers(self) -> None:
        """Pull tasks from every known peer.

        Runs on the Qt main thread (5 s timer) but **only schedules** the work:
        the actual HTTP pulls happen off the main thread via ``_spawn`` so a
        slow/dead peer cannot freeze the UI (perf red line).  ``_spawn`` is
        overridden to run synchronously in tests.

        Re-entrancy: if the previous cycle is still in flight (slow peer), the
        non-blocking ``_sync_lock`` makes this firing a no-op instead of
        spawning a second concurrent cycle that would race the task store and
        the spec-sync counter.
        """
        if not self._sync_lock.acquire(blocking=False):
            return
        self._spawn(self._run_sync_cycle)

    def _run_sync_cycle(self) -> None:
        """One pull-sync pass — runs in a worker thread (or sync in tests).

        Emits ``tasks_changed`` / ``data_overwritten`` and appends activity
        entries from here; ``pyqtSignal.emit`` is thread-safe (queued back to
        the main thread) and ``ActivityLog.append`` holds its own lock, so this
        is safe off the main thread.
        """
        try:
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
        finally:
            self._sync_lock.release()

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
        if not self._task_sync_allowed(peer):
            return 0, []
        try:
            self._ensure_peer_clock_skew(peer)
            httpx = get_httpx()
            t0 = time.monotonic()
            resp = httpx.get(
                f"{peer.base_url}/api/collab/tasks",
                params={"groupCode": self._group_code},
                timeout=4.0,
            )
            peer.latency_ms = (time.monotonic() - t0) * 1000
            peer.last_seen = time.time()
            if resp.status_code == 200:
                remote_tasks: list[dict] = resp.json()
                overwrites: list[dict] = []
                # Clock-skew guard: only trust the remote wall-clock ordering
                # when the peer's clock hasn't been measured to skew beyond
                # the threshold.  None = not yet probed → trust (legacy path).
                skew = peer.clock_skew_ms
                trust = skew is None or abs(skew) <= CLOCK_SKEW_THRESHOLD_MS
                guarded: list[dict] = []
                collisions: list[dict] = []
                changed = self.store.merge_from_peer(
                    remote_tasks,
                    overwrites_out=overwrites,
                    trust_remote_clock=trust,
                    skew_guarded_out=guarded,
                    claim_collisions_out=collisions,
                )
                for g in guarded:
                    self._log_activity(
                        "conflict", g["uid"],
                        detail=(
                            f"时钟偏斜过大({int(skew or 0)}ms)，已忽略远端状态 "
                            f"{g['new_status']}，保留本地 {g['old_status']}"
                        ),
                        severity="warn",
                    )
                # Best-effort distributed-claim collision surfacing (deduped):
                # P2P cannot prevent two devices claiming the same uid at once;
                # we only detect and prompt a human once per uid.
                for c in collisions:
                    if c["uid"] in self._claim_collision_prompted:
                        continue
                    self._claim_collision_prompted.add(c["uid"])
                    self.conflict_detected.emit(c["uid"])
                    self._log_activity(
                        "conflict", c["uid"],
                        detail=(
                            f"编号 {c['uid']} 被两台设备近乎同时认领"
                            f"({c['local_device']} / {c['remote_device']})，需人工确认"
                        ),
                        severity="error",
                    )
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
            peers_snapshot = [p for p in self._peers.values() if self._task_sync_allowed(p)]

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
            httpx = get_httpx()
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
            httpx = get_httpx()
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
        with self._offline_drafts_lock:
            snapshot = [d.to_dict() for d in self._offline_drafts]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    snapshot,
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
            peers_snapshot = [p for p in self._peers.values() if self._task_sync_allowed(p)]
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

    def _get_local_specimens(self, uid: Optional[str] = None) -> list[dict]:
        """Read specimen records from local project DB (used by FastAPI endpoint)."""
        return get_local_specimens(self._project_dir, uid)

    def _write_specimens_to_local_db(self, specimens: list[dict]) -> int:
        """Merge incoming specimen records into local project DB (LWW)."""
        written = write_specimens_to_local_db(self._project_dir, specimens)
        if written:
            self.specimens_updated.emit()
        return written

    def _stamp_specimen(self, uid: str) -> None:
        """Write a fresh LWW timestamp onto a local record before pushing."""
        stamp_specimen(self._project_dir, uid)

    def push_specimen(self, uid: str) -> None:
        """Push one specimen record from local DB to all session peers.

        Call this after saving a specimen so other devices see the update
        immediately (< 1 s) without waiting for the 5 s pull sync.
        """
        # Stamp even when no peers are online: the fresh timestamp makes the
        # later pull-sync merge correctly once peers appear.
        self._stamp_specimen(uid)
        with self._peers_lock:
            peers = [p for p in self._peers.values() if self._data_sync_allowed(p)]
        if not peers:
            return
        specs = self._get_local_specimens(uid)
        if not specs:
            return

        def _send() -> None:
            # §7 keep-old: was ``try: import httpx / except ImportError: return``
            httpx = get_httpx()
            if httpx is None:
                return
            payload = {
                "specimens": specs,
                "groupCode": self._group_code,
                "projectId": self._project_id,
            }
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
            httpx = get_httpx()
            resp = httpx.get(
                f"{peer.base_url}/api/collab/specimens",
                params={
                    "groupCode": self._group_code,
                    "projectId": self._project_id,
                },
                timeout=8.0,
            )
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
            httpx = get_httpx()
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
            httpx = get_httpx()
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
                                assignee: Optional[str] = None,
                                force: bool = True) -> None:
        """Push a task status change to all online peers (fire-and-forget).

        Runs in a background thread so the UI is never blocked.
        Falls back gracefully when httpx is not installed.

        ``force=True`` (default) mirrors manual human marking — remote peers
        accept out-of-order / backward transitions the same way the oracle
        does (app.js:3303).
        """
        with self._peers_lock:
            peers = [p for p in self._peers.values() if self._task_sync_allowed(p)]
        if not peers:
            return
        payload = {
            "uid":        uid,
            "status":     status,
            "assignee":   assignee,
            "deviceId":   self._hostname,
            "groupCode":  self._group_code,
            "force":      force,
        }

        def _send() -> None:
            # §7 keep-old: was ``try: import httpx / except ImportError: return``
            httpx = get_httpx()
            if httpx is None:
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

    def _on_photo_index_received(self, uid: str, kind: str,
                                 count: int, device_id: str) -> None:
        """Handle incoming photo-index from a peer (FastAPI callback thread).

        Logs activity and emits ``photo_index_received`` so UI layers can
        refresh task tables / prompt file sync.
        """
        kind_labels = {"jpg": "JPG", "tiff": "TIFF", "zip": "ZIP"}
        label = kind_labels.get(kind, kind.upper() or "文件")
        who = device_id or "队友"
        detail = f"{who} 的编号 {uid} 已有 {label}"
        if count > 1:
            detail += f" ×{count}"
        self._log_activity("photo_index", uid, detail=detail)
        self.photo_index_received.emit(uid, kind, count, device_id)
        self.tasks_changed.emit()

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
            peers_snapshot = [p for p in self._peers.values() if self._data_sync_allowed(p)]

        if not peers_snapshot:
            return

        record = PhotoIndexRecord(
            uid=uid,
            kind=kind,
            count=count,
            device_id=self._hostname,
            group_code=self._group_code,
            project_id=self._project_id,
        )
        payload = record.to_dict()

        # §7 keep-old: was ``try: import httpx / except ImportError: return``
        httpx = get_httpx()
        if httpx is None:
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
        from app.services.collab_share_registry import build_shared_projects_payload

        share_dirs = set(self._shared_project_dirs)
        if self._project_dir:
            try:
                share_dirs.add(str(Path(self._project_dir).resolve()))
            except OSError:
                share_dirs.add(str(self._project_dir))
        return {
            "hostname":    self._hostname,
            "operatorName": self._operator_name,
            "projectName": self._project_name,
            "projectId":   self._project_id,
            "groupCode":   self._group_code,
            "sessionName": self._session_name,
            "serverTime":  time.time(),
            "lanIp":       _get_local_ip(),
            "port":        self._port,
            "sharedProjects": build_shared_projects_payload(share_dirs),
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
            self.store.update_status(
                uid, TaskStatus.ASSIGNED, assignee=operator, force=True,
            )
            self.tasks_changed.emit()
            self.specimen_status_changed.emit(uid)
            self.broadcast_status_update(uid, "assigned", assignee=operator)
            self._log_activity("status_changed", uid,
                               detail=f"编号 {uid} 分配给 {operator}")
        except ValueError as exc:
            logger.warning("assign_task failed uid=%s: %s", uid, exc)

    def update_task_status(self, uid: str, status: str,
                           seed_status: Optional[str] = None,
                           force: bool = False,
                           broadcast: bool = False) -> tuple[bool, str]:
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
        if broadcast:
            self.broadcast_status_update(uid, status, force=force)
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
            peers_snapshot = [p for p in self._peers.values() if self._task_sync_allowed(p)]

        if peers_snapshot:
            # §7 keep-old: was ``try: import httpx / ...<loop>... / except ImportError: pass``
            httpx = get_httpx()
            if httpx is not None:
                for peer in peers_snapshot:
                    try:
                        httpx.post(
                            f"{peer.base_url}/api/collab/tasks/release",
                            json={"uid": uid, "groupCode": self._group_code},
                            timeout=4.0,
                        )
                    except Exception:  # noqa: BLE001
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
