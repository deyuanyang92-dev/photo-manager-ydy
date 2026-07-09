"""test_collab_service.py — Unit tests for collab_service pure logic.

Coverage:
  TaskStatus / is_valid_transition
  TaskStore: create (409), update_status, merge_from_peer, thread safety
  CollabService.create_task: local-409 and remote-409 (via mocked httpx)
  CollabService.add_manual_peer / remove_manual_peer
  CollabView offscreen smoke test

Tests that require a real network or two machines are marked
``@pytest.mark.needs_network`` and are skipped by default.

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_collab_service.py -v
"""

from __future__ import annotations

import concurrent.futures
import threading
from unittest.mock import MagicMock, patch

import pytest

from app.db import db_manager
from app.services.collab_service import (
    CollabService,
    PeerInfo,
    TaskRecord,
    TaskStatus,
    TaskStore,
    _now_iso,
    is_valid_transition,
)
from app.services.project_identity_service import project_sync_code, read_project_identity


# ── Mark for network-dependent tests ─────────────────────────────────────────

needs_network = pytest.mark.skipif(
    True,  # always skip in unit test run; run manually on LAN
    reason="requires two real machines on the same LAN",
)


# ── TaskStatus / state machine ────────────────────────────────────────────────

class TestStateMachine:
    """is_valid_transition covers all allowed and blocked edges."""

    def test_created_to_assigned_allowed(self):
        assert is_valid_transition(TaskStatus.CREATED, TaskStatus.ASSIGNED)

    def test_created_to_done_blocked(self):
        assert not is_valid_transition(TaskStatus.CREATED, TaskStatus.DONE)

    def test_full_happy_path(self):
        path = [
            (TaskStatus.CREATED,    TaskStatus.ASSIGNED),
            (TaskStatus.ASSIGNED,   TaskStatus.SHOOTING),
            (TaskStatus.SHOOTING,   TaskStatus.SHOT_DONE),
            (TaskStatus.SHOT_DONE,  TaskStatus.ORGANIZING),
            (TaskStatus.ORGANIZING, TaskStatus.DONE),
        ]
        for frm, to in path:
            assert is_valid_transition(frm, to), f"{frm} → {to} should be allowed"

    def test_void_is_terminal(self):
        """Once voided, no further transitions are allowed."""
        for status in TaskStatus:
            if status != TaskStatus.VOID:
                assert not is_valid_transition(TaskStatus.VOID, status), (
                    f"VOID → {status} should be blocked"
                )

    def test_done_to_void_allowed(self):
        assert is_valid_transition(TaskStatus.DONE, TaskStatus.VOID)

    def test_conflict_can_revert_to_created(self):
        assert is_valid_transition(TaskStatus.CONFLICT, TaskStatus.CREATED)

    def test_conflict_can_be_voided(self):
        assert is_valid_transition(TaskStatus.CONFLICT, TaskStatus.VOID)

    def test_backward_transition_blocked(self):
        assert not is_valid_transition(TaskStatus.ORGANIZING, TaskStatus.ASSIGNED)


# ── TaskStore ─────────────────────────────────────────────────────────────────

class TestTaskStore:

    def test_create_returns_task(self):
        store = TaskStore()
        task = store.create("UID001")
        assert task.uid == "UID001"
        assert task.status == TaskStatus.CREATED

    def test_local_409_on_duplicate(self):
        store = TaskStore()
        store.create("UID001")
        with pytest.raises(ValueError, match="409"):
            store.create("UID001")

    def test_exists_true_after_create(self):
        store = TaskStore()
        store.create("X001")
        assert store.exists("X001")

    def test_exists_false_for_unknown(self):
        store = TaskStore()
        assert not store.exists("UNKNOWN")

    def test_update_status_valid(self):
        store = TaskStore()
        store.create("A001")
        task = store.update_status("A001", TaskStatus.SHOOTING)
        assert task.status == TaskStatus.SHOOTING

    def test_update_status_invalid_transition_raises(self):
        store = TaskStore()
        store.create("A002")
        with pytest.raises(ValueError, match="Invalid transition"):
            store.update_status("A002", TaskStatus.DONE)

    def test_update_status_unknown_uid_raises(self):
        store = TaskStore()
        with pytest.raises(ValueError, match="Unknown UID"):
            store.update_status("GHOST", TaskStatus.SHOOTING)

    def test_all_returns_all(self):
        store = TaskStore()
        store.create("U1")
        store.create("U2")
        assert {t.uid for t in store.list_tasks()} == {"U1", "U2"}

    def test_get_returns_none_for_unknown(self):
        store = TaskStore()
        assert store.get_task("MISSING") is None

    def test_get_returns_task(self):
        store = TaskStore()
        store.create("Q1")
        t = store.get_task("Q1")
        assert t is not None and t.uid == "Q1"

    def test_clear(self):
        store = TaskStore()
        store.create("C1")
        store.clear()
        assert store.list_tasks() == []

    # ── merge_from_peer ───────────────────────────────────────────────────

    def test_merge_inserts_new_remote_task(self):
        store = TaskStore()
        remote = [{"uid": "R1", "status": "shooting", "createdAt": _now_iso(),
                   "updatedAt": _now_iso()}]
        changed = store.merge_from_peer(remote)
        assert changed == 1
        assert store.exists("R1")

    def test_merge_newer_remote_wins(self):
        """If remote updated_at is later than local, adopt remote."""
        store = TaskStore()
        store.create("R2")
        # Forge a remote record with a later timestamp and DONE status
        from datetime import datetime, timezone, timedelta
        future = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
        remote = [{"uid": "R2", "status": "done", "updatedAt": future,
                   "createdAt": _now_iso()}]
        changed = store.merge_from_peer(remote)
        assert changed == 1
        assert store.get_task("R2").status == TaskStatus.DONE  # type: ignore[union-attr]

    def test_merge_records_status_overwrite_when_requested(self):
        store = TaskStore()
        store.create("R2B")
        from datetime import datetime, timezone, timedelta
        future = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
        overwrites = []

        changed = store.merge_from_peer(
            [{"uid": "R2B", "status": "done", "updatedAt": future,
              "createdAt": _now_iso()}],
            overwrites_out=overwrites,
        )

        assert changed == 1
        assert overwrites == [{
            "uid": "R2B",
            "old_status": "created",
            "new_status": "done",
        }]

    def test_merge_older_remote_ignored(self):
        """If remote updated_at is earlier than local, local wins."""
        store = TaskStore()
        store.create("R3")
        store.update_status("R3", TaskStatus.SHOOTING)

        past = "2020-01-01T00:00:00+00:00"
        remote = [{"uid": "R3", "status": "created", "updatedAt": past,
                   "createdAt": past}]
        changed = store.merge_from_peer(remote)
        assert changed == 0  # no change
        assert store.get_task("R3").status == TaskStatus.SHOOTING  # type: ignore[union-attr]

    def test_merge_skips_records_without_uid(self):
        store = TaskStore()
        remote = [{"status": "done", "updatedAt": _now_iso()}]  # no uid
        changed = store.merge_from_peer(remote)
        assert changed == 0

    # ── Thread safety ─────────────────────────────────────────────────────

    def test_concurrent_creates_no_crash(self):
        """50 threads each trying to create a unique UID — no crashes."""
        store = TaskStore()
        errors: list[Exception] = []

        def _create(n: int) -> None:
            try:
                store.create(f"THREAD-{n}")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            list(ex.map(_create, range(50)))

        assert not errors
        assert len(store.list_tasks()) == 50

    def test_concurrent_duplicate_409(self):
        """Multiple threads trying to create the same UID — exactly one succeeds."""
        store = TaskStore()
        results: list[bool] = []
        lock = threading.Lock()

        def _try_create() -> None:
            try:
                store.create("SHARED-UID")
                with lock:
                    results.append(True)
            except ValueError:
                with lock:
                    results.append(False)

        threads = [threading.Thread(target=_try_create) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = [r for r in results if r]
        assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}"


# ── CollabService (offline logic) ─────────────────────────────────────────────

class TestCollabServiceOffline:
    """Tests that run without network by patching httpx."""

    def _make_service(self) -> CollabService:
        svc = CollabService()
        svc._project_name = "TestProject"
        return svc

    def test_create_task_no_peers_succeeds(self):
        svc = self._make_service()
        ok, msg = svc.create_task("UID-A1", assignee="Alice")
        assert ok, msg
        assert svc.store.exists("UID-A1")

    def test_create_task_local_409(self):
        svc = self._make_service()
        svc.store.create("DUP001")
        ok, msg = svc.create_task("DUP001")
        assert not ok
        assert "409" in msg

    def test_conflict_signal_emitted_on_local_409(self):
        svc = self._make_service()
        svc.store.create("SIG001")
        received: list[str] = []
        svc.conflict_detected.connect(lambda uid: received.append(uid))
        svc.create_task("SIG001")
        assert "SIG001" in received

    def test_create_task_remote_409_via_mock(self):
        """Mock httpx to return 409 from a fake peer, confirm service rejects."""
        svc = self._make_service()
        svc.set_group_code("G")
        # Manually register a fake peer in the same group
        from app.services.collab_service import PeerInfo
        svc._peers["10.0.0.2:5050"] = PeerInfo(ip="10.0.0.2", port=5050, group_code="G")

        mock_response = MagicMock()
        mock_response.status_code = 409
        mock_response.json.return_value = {"detail": "UID 'REM001' already exists"}

        with patch("httpx.post", return_value=mock_response):
            ok, msg = svc.create_task("REM001")

        assert not ok
        assert "409" in msg
        # Should NOT have been stored locally
        assert not svc.store.exists("REM001")

    def test_create_task_remote_network_error_not_conflict(self):
        """Network failure to a peer is not treated as a conflict."""
        svc = self._make_service()
        svc.set_group_code("G")
        from app.services.collab_service import PeerInfo
        svc._peers["10.0.0.99:5050"] = PeerInfo(ip="10.0.0.99", port=5050, group_code="G")

        import httpx
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            ok, msg = svc.create_task("NET001")

        # Network error is silent pass-through; local create succeeds
        assert ok
        assert svc.store.exists("NET001")

    def test_create_task_rolls_back_local_and_remote_on_late_409(self):
        """Local-first claim rolls back peer claims when a later peer returns 409."""
        svc = self._make_service()
        svc.set_group_code("G")
        from app.services.collab_service import PeerInfo

        svc._peers["10.0.0.2:5050"] = PeerInfo(ip="10.0.0.2", port=5050, group_code="G")
        svc._peers["10.0.0.3:5050"] = PeerInfo(ip="10.0.0.3", port=5050, group_code="G")

        release_calls: list[str] = []

        def fake_post(url, **kwargs):
            mock = MagicMock()
            if "release" in url:
                release_calls.append(url)
                mock.status_code = 200
                return mock
            if "10.0.0.2" in url:
                mock.status_code = 201
                mock.json.return_value = {}
                return mock
            mock.status_code = 409
            mock.json.return_value = {"detail": "UID already exists"}
            return mock

        with patch("httpx.post", side_effect=fake_post):
            ok, msg = svc.create_task("ROLL001")

        assert not ok
        assert "409" in msg
        assert not svc.store.exists("ROLL001")
        assert release_calls
        svc = self._make_service()
        # Patch _fetch_peer_info to avoid real HTTP
        svc._fetch_peer_info = MagicMock()
        svc.add_manual_peer("192.168.5.10", 5051)
        peers = svc.peers()
        assert any(p.ip == "192.168.5.10" and p.port == 5051 for p in peers)
        assert all(p.manual for p in peers if p.ip == "192.168.5.10")

    def test_remove_manual_peer(self):
        svc = self._make_service()
        svc._fetch_peer_info = MagicMock()
        svc.add_manual_peer("192.168.5.11", 5051)
        svc.remove_manual_peer("192.168.5.11", 5051)
        peers = svc.peers()
        assert not any(p.ip == "192.168.5.11" for p in peers)

    def test_peers_changed_signal_on_add(self):
        svc = self._make_service()
        svc._fetch_peer_info = MagicMock()
        received: list[int] = []
        svc.peers_changed.connect(lambda: received.append(1))
        svc.add_manual_peer("1.2.3.4", 5050)
        assert len(received) >= 1

    def test_sync_all_peers_offloads_to_worker_not_main_thread(self):
        """_sync_all_peers must NOT do HTTP on the main thread.

        It delegates the pull cycle to ``_spawn`` so a slow/dead peer cannot
        freeze the UI (perf red line: sync is not allowed on the Qt main
        thread).  Inline HTTP here is a regression.
        """
        from app.services.collab_service import PeerInfo
        svc = self._make_service()
        svc.set_group_code("G")
        svc._peers["10.0.0.2:5050"] = PeerInfo(ip="10.0.0.2", port=5050, group_code="G")

        spawned: list = []
        svc._spawn = lambda fn: spawned.append(fn)   # capture, do NOT run inline

        # If any HTTP runs on the main thread, the patched httpx raises → fail.
        with patch("httpx.get", side_effect=AssertionError("sync ran HTTP inline on main thread")):
            svc._sync_all_peers()

        assert len(spawned) == 1, "sync cycle must be delegated to _spawn, not run inline"

    def test_sync_all_peers_skips_when_previous_cycle_busy(self):
        """Re-entrancy guard: a still-running cycle must not start a second one."""
        from app.services.collab_service import PeerInfo
        svc = self._make_service()
        svc.set_group_code("G")
        svc._peers["10.0.0.2:5050"] = PeerInfo(ip="10.0.0.2", port=5050, group_code="G")

        spawned: list = []
        svc._spawn = lambda fn: spawned.append(fn)

        # Simulate an in-flight cycle: hold the guard lock.
        assert svc._sync_lock.acquire(False)
        try:
            svc._sync_all_peers()
        finally:
            svc._sync_lock.release()

        assert spawned == [], "must not start a second cycle while one is in flight"

    def test_tasks_changed_signal_after_create(self):
        svc = self._make_service()
        received: list[int] = []
        svc.tasks_changed.connect(lambda: received.append(1))
        svc.create_task("SIG-TASK-001")
        assert len(received) >= 1

    def test_node_info_returns_required_keys(self):
        svc = self._make_service()
        info = svc._node_info()
        assert "hostname" in info
        assert "projectName" in info
        assert "projectId" in info
        assert "lanIp" in info
        assert "port" in info

    def test_apply_project_sync_code_updates_project_identity(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        db_manager.open_project_db(str(project), create=True)
        db_manager.close_project_db(str(project))

        svc = self._make_service()
        svc.set_project_dir(str(project))
        original = svc.project_id
        target = "b" * 32

        svc.apply_project_sync_code(project_sync_code(target, project_name="Shared"))

        db = db_manager.open_project_db_private(str(project))
        try:
            stored = read_project_identity(db)
        finally:
            db.close()
        assert original and original != target
        assert svc.project_id == target
        assert svc._node_info()["projectId"] == target
        assert stored == target

    def test_local_address_format(self):
        svc = self._make_service()
        addr = svc.local_address()
        assert ":" in addr

    # ── project_id exact-match beats fuzzy name matching ───────────────────

    def test_project_matches_by_id_even_when_names_differ(self):
        """Explicit project_id binding must win over name comparison."""
        svc = self._make_service()
        svc._project_id = "a" * 32
        svc._project_name = "本地叫法A"
        peer = PeerInfo(
            ip="10.0.0.2", port=5050,
            project_id="a" * 32, project_name="队友叫法B",
        )
        assert svc._project_matches(peer) is True

    def test_project_id_mismatch_blocks_even_when_names_match(self):
        """Two independently-created same-named projects must NOT sync once
        either side has an explicit (different) project_id."""
        svc = self._make_service()
        svc._project_id = "a" * 32
        svc._project_name = "三门湾调查"
        peer = PeerInfo(
            ip="10.0.0.2", port=5050,
            project_id="b" * 32, project_name="三门湾调查",
        )
        assert svc._project_matches(peer) is False

    def test_project_matches_falls_back_to_name_without_ids(self):
        """Legacy/no-id peers still get the zero-config name-based match."""
        svc = self._make_service()
        svc._project_id = ""
        svc._project_name = "三门湾 调查"
        peer = PeerInfo(ip="10.0.0.2", port=5050, project_id="", project_name="三门湾调查")
        assert svc._project_matches(peer) is True

    def test_discover_team_projects_dedupes_by_project_id(self):
        svc = self._make_service()
        svc.set_group_code("TEAM1")
        svc._peers["10.0.0.2:5050"] = PeerInfo(
            ip="10.0.0.2", port=5050, group_code="TEAM1",
            project_id="a" * 32, project_name="三门湾调查",
        )
        svc._peers["10.0.0.3:5050"] = PeerInfo(
            ip="10.0.0.3", port=5050, group_code="TEAM1",
            project_id="a" * 32, project_name="三门湾调查",
        )
        svc._peers["10.0.0.4:5050"] = PeerInfo(
            ip="10.0.0.4", port=5050, group_code="TEAM1",
            project_id="c" * 32, project_name="福建调查",
        )
        projects = svc.discover_team_projects()
        assert len(projects) == 2
        by_name = {p["name"]: p for p in projects}
        assert by_name["三门湾调查"]["peer_count"] == 2
        assert by_name["福建调查"]["peer_count"] == 1
        assert by_name["三门湾调查"]["code"].startswith("SPP-PROJECT:")

    def test_discover_team_projects_ignores_other_teams(self):
        svc = self._make_service()
        svc.set_group_code("TEAM1")
        svc._peers["10.0.0.9:5050"] = PeerInfo(
            ip="10.0.0.9", port=5050, group_code="OTHER-TEAM",
            project_id="d" * 32, project_name="别的团队项目",
        )
        assert svc.discover_team_projects() == []

    def test_discover_team_projects_skips_peers_without_id(self):
        svc = self._make_service()
        svc.set_group_code("TEAM1")
        svc._peers["10.0.0.9:5050"] = PeerInfo(
            ip="10.0.0.9", port=5050, group_code="TEAM1",
            project_id="", project_name="没有身份的项目",
        )
        assert svc.discover_team_projects() == []

    # ── same-name / different-ID bind suggestion ────────────────────────────

    def _bind_peer(self, pid: str, name: str = "三门湾调查") -> PeerInfo:
        return PeerInfo(
            ip="10.0.0.2", port=5050, hostname="peer-pc",
            group_code="TEAM1", project_id=pid, project_name=name,
        )

    def test_bind_suggested_for_same_name_different_id(self):
        svc = self._make_service()
        svc.set_group_code("TEAM1")
        svc._project_id = "f" * 32          # larger than peer's → we prompt
        svc._project_name = "D:/data/三门湾调查"
        received: list[tuple] = []
        svc.project_bind_suggested.connect(lambda *a: received.append(a))
        svc._check_project_bind_suggestions([self._bind_peer("a" * 32)])
        assert len(received) == 1
        peer_name, project_name, code = received[0]
        assert peer_name == "peer-pc"
        assert project_name == "三门湾调查"
        assert code.startswith("SPP-PROJECT:")

    def test_bind_not_suggested_when_our_id_is_smaller(self):
        """Deterministic tie-break: the smaller-ID side keeps quiet."""
        svc = self._make_service()
        svc.set_group_code("TEAM1")
        svc._project_id = "a" * 32
        svc._project_name = "三门湾调查"
        received: list[tuple] = []
        svc.project_bind_suggested.connect(lambda *a: received.append(a))
        svc._check_project_bind_suggestions([self._bind_peer("f" * 32)])
        assert received == []

    def test_bind_suggested_only_once_per_peer_project(self):
        svc = self._make_service()
        svc.set_group_code("TEAM1")
        svc._project_id = "f" * 32
        svc._project_name = "三门湾调查"
        received: list[tuple] = []
        svc.project_bind_suggested.connect(lambda *a: received.append(a))
        peer = self._bind_peer("a" * 32)
        svc._check_project_bind_suggestions([peer])
        svc._check_project_bind_suggestions([peer])
        assert len(received) == 1

    def test_bind_not_suggested_for_different_names(self):
        svc = self._make_service()
        svc.set_group_code("TEAM1")
        svc._project_id = "f" * 32
        svc._project_name = "三门湾调查"
        received: list[tuple] = []
        svc.project_bind_suggested.connect(lambda *a: received.append(a))
        svc._check_project_bind_suggestions(
            [self._bind_peer("a" * 32, name="福建调查")]
        )
        assert received == []

    def test_bind_not_suggested_across_teams(self):
        svc = self._make_service()
        svc.set_group_code("TEAM1")
        svc._project_id = "f" * 32
        svc._project_name = "三门湾调查"
        received: list[tuple] = []
        svc.project_bind_suggested.connect(lambda *a: received.append(a))
        peer = self._bind_peer("a" * 32)
        peer.group_code = "OTHER"
        svc._check_project_bind_suggestions([peer])
        assert received == []


# ── Release-to-reuse (revoke a UID claim) ────────────────────────────────────

class TestReleaseTask:
    """Revoking a UID = releasing it: deleted locally + broadcast, reusable."""

    def test_store_delete_removes_uid(self):
        store = TaskStore()
        store.create("U1")
        store.delete("U1")
        assert not store.exists("U1")

    def test_store_delete_unknown_is_noop(self):
        store = TaskStore()
        store.delete("nope")  # must not raise
        assert not store.exists("nope")

    def test_release_deletes_locally_and_allows_reclaim(self):
        svc = CollabService()
        svc.set_group_code("G")
        ok, _ = svc.create_task("REUSE-1")
        assert ok
        svc.release_task("REUSE-1")
        assert not svc.store.exists("REUSE-1")
        # The whole point: the UID can be claimed again afterwards.
        ok2, _ = svc.create_task("REUSE-1")
        assert ok2

    def test_release_broadcasts_to_same_group_peer(self):
        svc = CollabService()
        svc.set_group_code("G")
        from app.services.collab_service import PeerInfo
        svc.store.create("R1")
        with svc._peers_lock:
            svc._peers["9.9.9.9:5050"] = PeerInfo(ip="9.9.9.9", port=5050, group_code="G")
        with patch("httpx.post") as mock_post:
            svc.release_task("R1")
        urls = [c.args[0] if c.args else c.kwargs.get("url") for c in mock_post.call_args_list]
        assert any("/api/collab/tasks/release" in (u or "") for u in urls)

    def test_release_skips_foreign_group_peer(self):
        svc = CollabService()
        svc.set_group_code("G")
        from app.services.collab_service import PeerInfo
        svc.store.create("R2")
        with svc._peers_lock:
            svc._peers["8.8.8.8:5050"] = PeerInfo(ip="8.8.8.8", port=5050, group_code="OTHER")
        with patch("httpx.post") as mock_post:
            svc.release_task("R2")
        mock_post.assert_not_called()

    def test_release_emits_tasks_changed(self):
        svc = CollabService()
        svc.store.create("R3")
        fired = []
        svc.tasks_changed.connect(lambda: fired.append(1))
        svc.release_task("R3")
        assert fired


# ── Subnet scan (mDNS-failure fallback, no IP knowledge needed) ───────────────

class TestSubnetScan:
    def _resp(self, group="G1"):
        r = MagicMock(status_code=200)
        r.json.return_value = {"hostname": "host-x", "groupCode": group,
                               "projectName": "P", "serverTime": 0.0}
        return r

    def test_scan_adds_reachable_peer(self):
        svc = CollabService()
        svc.set_group_code("G1")
        with patch("httpx.get", return_value=self._resp("G1")):
            found = svc.scan_lan(hosts=["10.0.0.5"], ports=[5050])
        assert len(found) == 1
        assert "10.0.0.5:5050" in svc._peers
        assert svc._peers["10.0.0.5:5050"].group_code == "G1"

    def test_scan_skips_unreachable(self):
        import httpx
        svc = CollabService()
        with patch("httpx.get", side_effect=httpx.ConnectError("no")):
            found = svc.scan_lan(hosts=["10.0.0.6"], ports=[5050])
        assert found == []

    def test_scan_skips_self(self):
        svc = CollabService()
        svc._port = 5050
        from app.services import collab_service as _cs
        with patch.object(_cs, "_get_local_ip", return_value="10.0.0.9"), \
             patch("httpx.get", return_value=self._resp()):
            found = svc.scan_lan(hosts=["10.0.0.9"], ports=[5050])
        assert found == []

    def test_scan_skips_self_on_another_port(self):
        """A stale second instance on this host is not a LAN teammate."""
        svc = CollabService()
        svc._port = 5051
        from app.services import collab_service as _cs
        with patch.object(_cs, "_get_local_ip", return_value="10.0.0.9"), \
             patch("httpx.get", return_value=self._resp()):
            found = svc.scan_lan(hosts=["10.0.0.9"], ports=[5050])
        assert found == []


# ── mDNS peer enrichment (regression: group_code must be populated) ───────────

class TestMdnsEnrich:
    """mDNS-discovered peers must be enriched with group_code or they never sync."""

    def test_on_peer_found_enriches_group_code(self):
        svc = CollabService()
        svc.set_group_code("G1")
        svc._spawn = lambda fn: fn()  # run enrichment synchronously

        def fake_fetch(peer):
            peer.group_code = "G1"
            peer.project_name = "P"

        svc._fetch_peer_info = fake_fetch
        svc._on_peer_found("1.2.3.4", 5050, "host-b")
        peer = svc._peers["1.2.3.4:5050"]
        assert peer.group_code == "G1"
        # The whole point: an enriched same-group peer now passes the sync filter.
        assert svc._group_matches(peer)

    def test_on_peer_found_emits_peers_changed(self):
        svc = CollabService()
        svc._spawn = lambda fn: fn()
        svc._fetch_peer_info = lambda peer: None
        fired = []
        svc.peers_changed.connect(lambda: fired.append(1))
        svc._on_peer_found("5.6.7.8", 5050, "h")
        assert fired

    def test_on_peer_found_skips_same_host_on_another_port(self):
        """mDNS must not list another local process as an online member."""
        svc = CollabService()
        svc._port = 5051
        svc._fetch_peer_info = MagicMock()
        from app.services import collab_service as _cs
        with patch.object(_cs, "_get_local_ip", return_value="10.0.0.9"):
            svc._on_peer_found("10.0.0.9", 5050, "this-host")
        assert svc.peers() == []
        svc._fetch_peer_info.assert_not_called()


class TestSelfPeerFiltering:
    def test_manual_connection_rejects_same_host_on_another_port(self):
        svc = CollabService()
        svc._port = 5051
        svc._fetch_peer_info = MagicMock()
        from app.services import collab_service as _cs
        with patch.object(_cs, "_get_local_ip", return_value="10.0.0.9"):
            svc.add_manual_peer("10.0.0.9", 5050)
        assert svc.peers() == []
        svc._fetch_peer_info.assert_not_called()


# ── Collaboration-group code (group-scoped sync) ─────────────────────────────

class TestGroupCode:
    """group_code identifies a collaboration group; only matching peers sync."""

    def test_default_group_code_empty(self):
        assert CollabService().group_code == ""

    def test_set_group_code(self):
        svc = CollabService()
        svc.set_group_code("SMW-2026")
        assert svc.group_code == "SMW-2026"

    def test_group_code_trimmed(self):
        svc = CollabService()
        svc.set_group_code("  SMW-2026  ")
        assert svc.group_code == "SMW-2026"

    def test_node_info_includes_group_code(self):
        svc = CollabService()
        svc.set_group_code("G1")
        assert svc._node_info().get("groupCode") == "G1"

    def test_peer_info_has_group_code_field(self):
        from app.services.collab_service import PeerInfo
        p = PeerInfo(ip="1.2.3.4", port=5050)
        assert p.group_code == ""

    def test_fetch_peer_info_parses_group_code(self):
        svc = CollabService()
        from app.services.collab_service import PeerInfo
        peer = PeerInfo(ip="1.2.3.4", port=5050)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "hostname": "host-b", "projectName": "P", "groupCode": "G9",
        }
        with patch("httpx.get", return_value=mock_response):
            svc._fetch_peer_info(peer)
        assert peer.group_code == "G9"

    def test_is_running_false_before_start(self):
        assert CollabService().is_running() is False


# ── Qt application singleton for view smoke tests ────────────────────────────

import os as _os

_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_QT_APP = None


@pytest.fixture(scope="module", autouse=False)
def qt_app():
    global _QT_APP
    from PyQt6.QtWidgets import QApplication
    if _QT_APP is None:
        _QT_APP = QApplication.instance() or QApplication([])
    return _QT_APP


# ── CollabView offscreen smoke test ──────────────────────────────────────────

class TestCollabViewSmoke:
    """Instantiate CollabView in offscreen mode; no service attached."""

    @pytest.fixture(autouse=True)
    def _qapp(self, qt_app):
        """Ensure QApplication exists for every view test."""
        return qt_app

    def test_view_instantiates_without_service(self):
        from app.views.collab_view import CollabView

        ctx = MagicMock()
        ctx.collab_service = None
        # Remove collab_service attribute so getattr fallback works
        del ctx.collab_service

        view = CollabView(ctx)
        assert view.view_id == "collab"
        assert view.nav_title == "协作"
        assert view.nav_icon == "👥"
        view.close()

    def test_share_address_without_service_opens_inline_setup_not_dialog(self, monkeypatch):
        from app.views import collab_view
        from app.views.collab_view import CollabView

        ctx = MagicMock()
        ctx.collab_service = None
        created = {}

        class _FakeShareDialog:
            def __init__(self, dialog_ctx, parent):
                created["ctx"] = dialog_ctx
                created["parent"] = parent
                self.exec_called = False

            def exec(self):
                self.exec_called = True
                created["exec_called"] = True

        monkeypatch.setattr(collab_view, "_CollabShareDialog", _FakeShareDialog)
        view = CollabView(ctx)

        view._on_share_addr()

        assert created == {}
        assert not view._team_setup_panel.isHidden()
        assert "先保存团队永久码" in view._team_setup_status.text()
        view.close()

    def test_view_with_service_not_running_shows_not_started(self):
        from app.views.collab_view import CollabView

        ctx = MagicMock()
        svc = CollabService()
        ctx.collab_service = svc

        view = CollabView(ctx)
        view.on_activate()
        assert "未启动" in view._status_badge.text()
        assert "协作未启动" in view._scope_label.text()
        view.close()
        svc.stop()

    def test_view_running_group_without_peers_shows_no_peers(self):
        from app.views.collab_view import CollabView

        ctx = MagicMock()
        svc = CollabService()
        svc._running = True
        svc.set_group_code("TEAM-1")
        ctx.collab_service = svc

        view = CollabView(ctx)
        view.on_activate()

        assert "未发现其他设备" in view._status_badge.text()
        assert "TEAM-1" in view._scope_label.text()
        view.close()
        svc.stop()

    def test_view_running_without_group_shows_missing_group_code(self):
        from app.views.collab_view import CollabView

        ctx = MagicMock()
        svc = CollabService()
        svc._running = True
        ctx.collab_service = svc

        view = CollabView(ctx)
        view.on_activate()

        assert "未配对团队" in view._status_badge.text()
        assert view._scope_label.text() == "团队永久码：未设置"
        view.close()
        svc.stop()

    def test_conflict_banner_appears_and_hides(self):
        from app.views.collab_view import CollabView

        ctx = MagicMock()
        svc = CollabService()
        ctx.collab_service = svc

        view = CollabView(ctx)
        # Banner starts hidden
        assert view._conflict_banner.isHidden()
        svc.conflict_detected.emit("TEST-UID")
        # After signal, banner is no longer hidden (show() was called).
        # Use not isHidden() — isVisible() also requires the parent to be shown.
        assert not view._conflict_banner.isHidden()
        assert "TEST-UID" in view._conflict_banner.text()
        view.close()
        svc.stop()

    def test_task_table_populated_on_activate(self):
        from app.views.collab_view import CollabView

        ctx = MagicMock()
        svc = CollabService()
        svc.store.create("TABLE-UID", assignee="Bob")
        ctx.collab_service = svc

        view = CollabView(ctx)
        view.on_activate()
        assert view._task_table.rowCount() == 1
        assert view._task_table.item(0, 0).text() == "TABLE-UID"
        view.close()
        svc.stop()

    def test_task_table_shows_project_column_and_filter(self):
        from app.views.collab_view import CollabView

        ctx = MagicMock()
        svc = CollabService()
        svc.store.create("P1-UID", assignee="Bob", project_name="/data/Project-A")
        svc.store.create("P2-UID", assignee="Ada", project_name="/data/Project-B")
        ctx.collab_service = svc

        view = CollabView(ctx)
        view.on_activate()

        assert view._task_table.horizontalHeaderItem(1).text() == "项目"
        idx = view._project_combo.findData("Project-A")
        assert idx >= 0
        view._project_combo.setCurrentIndex(idx)

        assert view._task_table.rowCount() == 1
        assert view._task_table.item(0, 0).text() == "P1-UID"
        assert view._task_table.item(0, 1).text() == "Project-A"
        view.close()
        svc.stop()

    def test_device_table_shows_peer_project_and_group(self):
        from app.views.collab_view import CollabView

        ctx = MagicMock()
        svc = CollabService()
        svc.set_group_code("TEAM-1")
        svc._project_id = "PROJECT-B"
        svc._peers["192.168.1.20:5050"] = PeerInfo(
            ip="192.168.1.20",
            port=5050,
            hostname="shoot-pc",
            project_name="/work/Project-B",
            project_id="PROJECT-B",
            group_code="TEAM-1",
        )
        ctx.collab_service = svc

        view = CollabView(ctx)
        view.on_activate()

        assert view._device_list.item(0, 0).text() == "shoot-pc"
        assert view._device_list.item(0, 1).text() == "Project-B"
        assert view._device_list.item(0, 2).text() == "TEAM-1"
        assert view._device_list.item(0, 3).text() == "可同步"
        view.close()
        svc.stop()

    def test_next_step_guides_photo_binding_when_only_tasks_ready(self):
        from app.views.collab_view import CollabView

        ctx = MagicMock()
        svc = CollabService()
        svc._running = True
        svc.set_group_code("TEAM-1")
        svc._project_id = "LOCAL-PROJECT"
        svc._peers["192.168.1.20:5050"] = PeerInfo(
            ip="192.168.1.20",
            port=5050,
            hostname="shoot-pc",
            project_name="/work/Project-B",
            project_id="OTHER-PROJECT",
            group_code="TEAM-1",
        )
        ctx.collab_service = svc

        view = CollabView(ctx)
        view.on_activate()

        assert view._next_step_label.text() == "下一步：使用项目码"
        assert "项目码" in view._next_step_detail.text()
        view.close()
        svc.stop()

    def test_next_step_reports_photo_sync_ready(self):
        from app.views.collab_view import CollabView

        ctx = MagicMock()
        svc = CollabService()
        svc._running = True
        svc.set_group_code("TEAM-1")
        svc._project_id = "PROJECT-B"
        svc._peers["192.168.1.20:5050"] = PeerInfo(
            ip="192.168.1.20",
            port=5050,
            hostname="shoot-pc",
            project_name="/work/Project-B",
            project_id="PROJECT-B",
            group_code="TEAM-1",
        )
        ctx.collab_service = svc

        view = CollabView(ctx)
        view.on_activate()

        assert view._next_step_label.text() == "照片同步已就绪"
        assert "同步当前编号" in view._next_step_detail.text()
        view.close()
        svc.stop()

    def test_debug_drawer_toggle(self):
        from app.views.collab_view import CollabView

        ctx = MagicMock()
        ctx.collab_service = None
        del ctx.collab_service

        view = CollabView(ctx)
        # Drawer starts hidden
        assert view._debug_drawer.isHidden()
        view._debug_btn.setChecked(True)
        assert not view._debug_drawer.isHidden()
        view._debug_btn.setChecked(False)
        assert view._debug_drawer.isHidden()
        view.close()

    def test_workbench_collab_panel_filters_current_project_tasks(self):
        from app.widgets.collab_panel import CollabPanel

        ctx = MagicMock()
        svc = CollabService()
        svc._project_name = "/data/Project-A"
        svc.store.create("A-UID", project_name="/data/Project-A")
        svc.store.create("B-UID", project_name="/data/Project-B")
        svc.store.create("LEGACY-UID")
        ctx.collab_service = svc
        ctx.current_project_dir = "/data/Project-A"
        ctx.settings = MagicMock()
        ctx.settings.last_project_dir = "/data/Project-A"

        panel = CollabPanel(ctx)
        panel.refresh()

        assert panel._task_title.text().startswith("当前项目任务 (2)")
        uids = {
            panel._task_table.item(row, 0).text()
            for row in range(panel._task_table.rowCount())
        }
        assert uids == {"A-UID", "LEGACY-UID"}
        panel.close()
        svc.stop()

    def test_workbench_collab_panel_uses_shared_status(self):
        from app.widgets.collab_panel import CollabPanel

        ctx = MagicMock()
        svc = CollabService()
        ctx.collab_service = svc
        ctx.current_project_dir = "/data/Project-A"
        ctx.settings = MagicMock()
        ctx.settings.last_project_dir = "/data/Project-A"

        panel = CollabPanel(ctx)
        try:
            panel.refresh()
            assert panel._health_text.text() == "协作未启动"
            assert not panel._setup_btn.isHidden()

            svc._running = True
            panel.refresh()
            assert panel._health_text.text() == "未配对团队"
            assert not panel._setup_btn.isHidden()

            svc.set_group_code("TEAM-1")
            panel.refresh()
            assert panel._health_text.text() == "未发现其他设备"
            assert panel._setup_btn.isHidden()
        finally:
            panel.close()
            svc.stop()

    def test_workbench_collab_panel_scan_buttons_are_independent(self):
        from app.widgets.collab_panel import CollabPanel

        ctx = MagicMock()
        svc = CollabService()
        svc.scan_lan = MagicMock()
        ctx.collab_service = svc
        ctx.current_project_dir = "/data/Project-A"
        ctx.settings = MagicMock()
        ctx.settings.last_project_dir = "/data/Project-A"

        panel = CollabPanel(ctx)
        try:
            panel._on_scan()
            assert panel._peer_scan_btn.text() == "搜索中…"
            assert panel._subnet_scan_btn.text() == "扫描局域网"
            panel._re_enable_scan()

            svc.scan_subnet_peers = lambda on_done=None: on_done and on_done(1)
            panel._on_scan_subnet()
            assert panel._peer_scan_btn.text() == "搜索队友"
            assert panel._subnet_scan_btn.text() == "扫描局域网"
        finally:
            panel.close()
            svc.stop()

    def test_hidden_collab_panel_conflict_signal_does_not_show_modal(self, monkeypatch):
        from app.utils import ui
        from app.widgets.collab_panel import CollabPanel

        ctx = MagicMock()
        svc = CollabService()
        ctx.collab_service = svc
        ctx.current_project_dir = "/data/Project-A"
        ctx.settings = MagicMock()
        ctx.settings.last_project_dir = "/data/Project-A"
        warned = []
        monkeypatch.setattr(ui, "warn", lambda *args, **kwargs: warned.append(args))

        panel = CollabPanel(ctx)
        try:
            panel.hide()
            svc.conflict_detected.emit("DUP-1")
            assert warned == []
        finally:
            panel.close()
            svc.stop()


# ── TaskRecord serialization ──────────────────────────────────────────────────

class TestTaskRecordSerialization:

    def test_to_dict_round_trip(self):
        t = TaskRecord(uid="ROUND-001", status=TaskStatus.SHOOTING,
                       assignee="Charlie", device_id="DEV-1")
        d = t.to_dict()
        t2 = TaskRecord.from_dict(d)
        assert t2.uid == "ROUND-001"
        assert t2.status == TaskStatus.SHOOTING
        assert t2.assignee == "Charlie"
        assert t2.device_id == "DEV-1"

    def test_from_dict_default_status(self):
        t = TaskRecord.from_dict({"uid": "DEF-001", "updatedAt": _now_iso()})
        assert t.status == TaskStatus.CREATED

    def test_from_dict_unknown_uid(self):
        t = TaskRecord.from_dict({"uid": "X", "status": "void"})
        assert t.status == TaskStatus.VOID


# ── CollabManagerDialog offscreen smoke tests ────────────────────────────────

class TestCollabManagerDialog:
    """Smoke tests for CollabManagerDialog — no service attached and with service."""

    @pytest.fixture(autouse=True)
    def _qapp(self, qt_app):
        return qt_app

    def test_dialog_opens_without_service(self):
        from app.widgets.collab_manager_dialog import CollabManagerDialog
        dlg = CollabManagerDialog(service=None)
        assert dlg.windowTitle() == "协作管理"
        # No-service: task table shows placeholder
        assert dlg._task_table.rowCount() >= 1
        dlg.close()

    def test_dialog_share_addr_without_service(self):
        from app.widgets.collab_manager_dialog import CollabManagerDialog
        dlg = CollabManagerDialog(service=None)
        assert "服务未启动" in dlg._share_addr.text()
        dlg.close()

    def test_dialog_with_service_populates_task_table(self):
        from app.widgets.collab_manager_dialog import CollabManagerDialog
        svc = CollabService()
        svc.store.create("DLGTEST-001", assignee="Alice")
        svc.store.create("DLGTEST-002")
        dlg = CollabManagerDialog(service=svc)
        # 2 real tasks → 2 rows (no placeholder span)
        assert dlg._task_table.rowCount() == 2
        uids = {dlg._task_table.item(r, 0).text() for r in range(2)}
        assert {"DLGTEST-001", "DLGTEST-002"}.issubset(uids)
        dlg.close()
        svc.stop()

    def test_dialog_with_service_shows_address(self):
        from app.widgets.collab_manager_dialog import CollabManagerDialog
        svc = CollabService()
        dlg = CollabManagerDialog(service=svc)
        # Should show a real IP or 127.0.0.1 + port
        assert "—" not in dlg._share_addr.text() or "5050" in dlg._share_addr.text()
        dlg.close()
        svc.stop()

    def test_dialog_conflict_banner_on_signal(self):
        from app.widgets.collab_manager_dialog import CollabManagerDialog
        svc = CollabService()
        dlg = CollabManagerDialog(service=svc)
        assert dlg._banner.isHidden()
        svc.conflict_detected.emit("CTEST-001")
        assert not dlg._banner.isHidden()
        assert "CTEST-001" in dlg._banner.text()
        dlg.close()
        svc.stop()

    def test_dialog_debug_drawer_toggle(self):
        from app.widgets.collab_manager_dialog import CollabManagerDialog
        dlg = CollabManagerDialog(service=None)
        assert dlg._debug_drawer.isHidden()
        dlg._debug_btn.setChecked(True)
        assert not dlg._debug_drawer.isHidden()
        dlg._debug_btn.setChecked(False)
        assert dlg._debug_drawer.isHidden()
        dlg.close()

    def test_dialog_no_service_summary_label(self):
        from app.widgets.collab_manager_dialog import CollabManagerDialog
        dlg = CollabManagerDialog(service=None)
        assert "未启动" in dlg._summary_label.text() or dlg._summary_label.text() != ""
        dlg.close()


# ── SpecimenSidebar collab strip wiring ──────────────────────────────────────

class TestSidebarCollabStrip:
    """SpecimenSidebar.update_collab_status updates labels correctly."""

    @pytest.fixture(autouse=True)
    def _qapp(self, qt_app):
        return qt_app

    def _make_sidebar(self):
        from app.widgets.specimen_sidebar import SpecimenSidebar
        ctx = MagicMock()
        ctx.get_db.return_value = None
        ctx.current_project_dir = None
        return SpecimenSidebar(ctx)

    def test_update_with_none_shows_dashes(self):
        sb = self._make_sidebar()
        sb.update_collab_status(None)
        assert sb._collab_addr.text() == "连接地址: —"
        assert sb._collab_members.text() == "成员: 0"
        assert "协作服务未启动" in sb._collab_sync.text()
        sb.close()

    def test_update_with_service_not_running_shows_status(self):
        sb = self._make_sidebar()
        svc = CollabService()
        sb.update_collab_status(svc)
        # Should contain ":" for ip:port
        assert ":" in sb._collab_addr.text()
        assert "协作未启动" in sb._collab_sync.text()
        assert not sb._collab_sync_selected_btn.isEnabled()
        assert not sb._collab_sync_project_btn.isEnabled()
        sb.close()
        svc.stop()

    def test_update_running_without_group_shows_status(self):
        sb = self._make_sidebar()
        svc = CollabService()
        svc._running = True
        sb.update_collab_status(svc)
        assert "未配对团队" in sb._collab_sync.text()
        assert not sb._collab_sync_selected_btn.isEnabled()
        assert not sb._collab_sync_project_btn.isEnabled()
        sb.close()
        svc.stop()

    def test_update_task_count(self):
        sb = self._make_sidebar()
        svc = CollabService()
        svc.store.create("SB-TEST-001")
        svc.store.create("SB-TEST-002")
        sb.update_collab_status(svc)
        assert "2" in sb._collab_sync.text()
        assert "协作未启动" in sb._collab_sync.text()
        sb.close()
        svc.stop()

    def test_update_same_project_peer_enables_sync(self):
        sb = self._make_sidebar()
        svc = CollabService()
        svc._running = True
        svc.set_group_code("TEAM-1")
        svc._project_id = "P1"
        svc._peers["10.0.0.2:5050"] = PeerInfo(
            ip="10.0.0.2",
            port=5050,
            group_code="TEAM-1",
            project_id="P1",
        )
        sb.update_collab_status(svc)
        assert "1 台在线" in sb._collab_sync.text()
        assert "可同步设备: 1" in sb._collab_sync.text()
        assert sb._collab_sync_selected_btn.isEnabled()
        assert sb._collab_sync_project_btn.isEnabled()
        sb.close()
        svc.stop()

    def test_collab_manager_signal_emitted(self):
        sb = self._make_sidebar()
        received: list[int] = []
        sb.collab_manager_requested.connect(lambda: received.append(1))
        sb._collab_mgr_btn.click()
        assert len(received) == 1
        sb.close()


# ── CollabService.broadcast via CollabManagerDialog ─────────────────────────

class TestStatusBroadcast:
    """CollabManagerDialog._broadcast_status_update POSTs to peers (mocked)."""

    @pytest.fixture(autouse=True)
    def _qapp(self, qt_app):
        return qt_app

    def test_broadcast_update_calls_httpx(self):
        from app.widgets.collab_manager_dialog import CollabManagerDialog
        from app.services.collab_service import PeerInfo

        svc = CollabService()
        svc.store.create("BCAST-001")
        svc._peers["10.0.0.5:5050"] = PeerInfo(ip="10.0.0.5", port=5050)

        dlg = CollabManagerDialog(service=svc)

        posted_urls: list[str] = []
        import httpx
        original_post = httpx.post

        def fake_post(url: str, **kwargs):
            posted_urls.append(url)
            m = MagicMock()
            m.status_code = 200
            return m

        with patch("httpx.post", side_effect=fake_post):
            dlg._broadcast_status_update("BCAST-001", "shooting")

        assert any("10.0.0.5" in u for u in posted_urls)
        dlg.close()
        svc.stop()

    def test_broadcast_no_peers_no_httpx_call(self):
        from app.widgets.collab_manager_dialog import CollabManagerDialog

        svc = CollabService()  # no peers
        svc.store.create("BCAST-NOPEER-001")
        dlg = CollabManagerDialog(service=svc)

        with patch("httpx.post") as mock_post:
            dlg._broadcast_status_update("BCAST-NOPEER-001", "shooting")
            mock_post.assert_not_called()

        dlg.close()
        svc.stop()


# ── Specimen LWW merge safety ─────────────────────────────────────────────────


class TestSpecimenLwwMerge:
    """_write_specimens_to_local_db must never clobber newer local edits."""

    def _svc_with_project(self, tmp_path) -> CollabService:
        project = tmp_path / "proj"
        project.mkdir()
        db_manager.open_project_db(str(project), create=True)
        db_manager.close_project_db(str(project))
        svc = CollabService()
        svc._project_dir = str(project)
        return svc

    def _insert_local(self, svc, uid: str, notes: str, stamp: str) -> None:
        db = db_manager.open_project_db_private(svc._project_dir)
        try:
            db.execute(
                "INSERT INTO specimens (uid, notes, collab_updated_at) VALUES (?,?,?)",
                (uid, notes, stamp),
            )
            db.commit()
        finally:
            db.close()

    def _local_notes(self, svc, uid: str) -> str:
        db = db_manager.open_project_db_private(svc._project_dir)
        try:
            row = db.execute(
                "SELECT notes FROM specimens WHERE uid=?", (uid,)
            ).fetchone()
            return row[0] if row else ""
        finally:
            db.close()

    def test_newer_remote_overwrites_older_local(self, tmp_path):
        svc = self._svc_with_project(tmp_path)
        self._insert_local(svc, "U1", "本地旧值", "2026-01-01T00:00:00+00:00")
        written = svc._write_specimens_to_local_db([{
            "uid": "U1", "notes": "远端新值",
            "collab_updated_at": "2026-06-01T00:00:00+00:00",
        }])
        assert written == 1
        assert self._local_notes(svc, "U1") == "远端新值"

    def test_older_remote_never_clobbers_newer_local(self, tmp_path):
        svc = self._svc_with_project(tmp_path)
        self._insert_local(svc, "U2", "本地新值", "2026-06-01T00:00:00+00:00")
        written = svc._write_specimens_to_local_db([{
            "uid": "U2", "notes": "远端旧值",
            "collab_updated_at": "2026-01-01T00:00:00+00:00",
        }])
        assert written == 0
        assert self._local_notes(svc, "U2") == "本地新值"

    def test_unstamped_remote_only_fills_missing_rows(self, tmp_path):
        svc = self._svc_with_project(tmp_path)
        self._insert_local(svc, "U3", "本地值", "")
        written = svc._write_specimens_to_local_db([
            {"uid": "U3", "notes": "远端无戳"},           # exists → skip
            {"uid": "U4", "notes": "全新记录"},           # missing → write
        ])
        assert written == 1
        assert self._local_notes(svc, "U3") == "本地值"
        assert self._local_notes(svc, "U4") == "全新记录"

    def test_push_specimen_stamps_local_record(self, tmp_path):
        svc = self._svc_with_project(tmp_path)
        self._insert_local(svc, "U5", "值", "")
        svc._stamp_specimen("U5")
        db = db_manager.open_project_db_private(svc._project_dir)
        try:
            row = db.execute(
                "SELECT collab_updated_at FROM specimens WHERE uid=?", ("U5",)
            ).fetchone()
        finally:
            db.close()
        assert row[0]  # non-empty ISO stamp

    def test_sync_cols_match_schema(self, tmp_path):
        """Every synced column must exist in the specimens table."""
        svc = self._svc_with_project(tmp_path)
        db = db_manager.open_project_db_private(svc._project_dir)
        try:
            cols = {r[1] for r in db.execute("PRAGMA table_info(specimens)")}
        finally:
            db.close()
        missing = [c for c in CollabService._SPEC_SYNC_COLS if c not in cols]
        assert missing == []


# ── Offline draft queue ───────────────────────────────────────────────────────


class TestOfflineDraftQueue:
    """Mirrors web loadCollabOfflineDrafts / saveCollabOfflineDrafts /
    collabMarkOfflineDraft / collabRetryOfflineDrafts.
    """

    def _make_service(self) -> CollabService:
        svc = CollabService()
        svc._project_name = "OfflineTest"
        return svc

    def test_initial_queue_empty(self):
        svc = self._make_service()
        assert svc.load_offline_drafts() == []

    def test_mark_offline_draft_adds_entry(self):
        svc = self._make_service()
        draft = svc.mark_offline_draft("OD-001", assignee="Alice")
        assert draft.uid == "OD-001"
        assert draft.assignee == "Alice"
        drafts = svc.load_offline_drafts()
        assert len(drafts) == 1
        assert drafts[0].uid == "OD-001"

    def test_mark_offline_draft_deduplicates(self):
        """Marking the same UID twice keeps only one entry."""
        svc = self._make_service()
        svc.mark_offline_draft("OD-DUP", assignee="A")
        svc.mark_offline_draft("OD-DUP", assignee="B")
        assert len(svc.load_offline_drafts()) == 1

    def test_mark_offline_draft_emits_signal(self):
        svc = self._make_service()
        received: list[int] = []
        svc.offline_drafts_changed.connect(lambda: received.append(1))
        svc.mark_offline_draft("OD-SIG")
        assert len(received) >= 1

    def test_save_offline_drafts_replaces_queue(self):
        from app.services.collab_service import OfflineDraft
        svc = self._make_service()
        svc.mark_offline_draft("OD-OLD")
        new_drafts = [OfflineDraft(uid="OD-NEW", assignee=None, device_id=None)]
        svc.save_offline_drafts(new_drafts)
        drafts = svc.load_offline_drafts()
        assert len(drafts) == 1
        assert drafts[0].uid == "OD-NEW"

    def test_save_empty_clears_queue(self):
        svc = self._make_service()
        svc.mark_offline_draft("OD-CLEAR")
        svc.save_offline_drafts([])
        assert svc.load_offline_drafts() == []

    def test_retry_skips_when_no_peers(self):
        """Without peers, retry returns 0 and queue is unchanged."""
        svc = self._make_service()
        svc.mark_offline_draft("OD-NOPEER")
        promoted = svc.retry_offline_drafts()
        assert promoted == 0
        assert len(svc.load_offline_drafts()) == 1

    def test_retry_promotes_when_peer_available(self):
        """With a peer that accepts, draft is promoted and removed from queue."""
        svc = self._make_service()
        svc.set_group_code("G1")
        svc.mark_offline_draft("OD-RETRY")

        # Add a fake peer so the retry path runs
        from app.services.collab_service import PeerInfo
        svc._peers["10.9.9.1:5050"] = PeerInfo(ip="10.9.9.1", port=5050, group_code="G1")

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"uid": "OD-RETRY", "status": "created"}

        with patch("httpx.post", return_value=mock_resp):
            promoted = svc.retry_offline_drafts()

        assert promoted == 1
        # Should be removed from queue after promotion
        assert all(d.uid != "OD-RETRY" for d in svc.load_offline_drafts())

    def test_retry_promotes_when_local_task_already_exists(self):
        svc = self._make_service()
        svc.set_group_code("G1")
        svc.store.create("OD-LOCAL")
        svc.mark_offline_draft("OD-LOCAL")
        svc._peers["10.9.9.1:5050"] = PeerInfo(ip="10.9.9.1", port=5050, group_code="G1")

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"uid": "OD-LOCAL", "status": "created"}

        with patch("httpx.post", return_value=mock_resp):
            promoted = svc.retry_offline_drafts()

        assert promoted == 1
        assert svc.store.exists("OD-LOCAL")
        assert all(d.uid != "OD-LOCAL" for d in svc.load_offline_drafts())

    def test_offline_draft_to_dict_round_trip(self):
        from app.services.collab_service import OfflineDraft
        d = OfflineDraft(uid="RT-001", assignee="Bob", device_id="DEV-X")
        rd = OfflineDraft.from_dict(d.to_dict())
        assert rd.uid == "RT-001"
        assert rd.assignee == "Bob"
        assert rd.device_id == "DEV-X"


# ── Photo-index reporting ─────────────────────────────────────────────────────


class TestPhotoIndexReporting:
    """Mirrors web collabPostPhotoIndex(uid, kind).
    Verifies post_photo_index POSTs to peers and the FastAPI endpoint accepts it.
    """

    def _make_service(self) -> CollabService:
        svc = CollabService()
        svc._project_name = "PhotoIndexTest"
        return svc

    def test_post_photo_index_no_peers_no_http(self):
        """Without peers, no HTTP call is made."""
        svc = self._make_service()
        with patch("httpx.post") as mock_post:
            svc.post_photo_index("PI-001", "tiff", count=3)
            mock_post.assert_not_called()

    def test_post_photo_index_calls_peer(self):
        """With one peer, httpx.post is called with the photo-index endpoint."""
        svc = self._make_service()
        svc.set_group_code("G1")
        svc._project_id = "P1"
        from app.services.collab_service import PeerInfo
        svc._peers["10.9.9.2:5050"] = PeerInfo(
            ip="10.9.9.2",
            port=5050,
            group_code="G1",
            project_id="P1",
        )

        posted: list[dict] = []

        def fake_post(url: str, **kwargs):
            posted.append({"url": url, "json": kwargs.get("json", {})})
            m = MagicMock()
            m.status_code = 200
            return m

        with patch("httpx.post", side_effect=fake_post):
            svc.post_photo_index("PI-002", "zip", count=1)

        assert len(posted) == 1
        assert "photo-index" in posted[0]["url"]
        assert posted[0]["json"]["uid"] == "PI-002"
        assert posted[0]["json"]["kind"] == "zip"
        assert posted[0]["json"]["count"] == 1
        assert posted[0]["json"]["groupCode"] == "G1"
        assert posted[0]["json"]["projectId"] == "P1"

    def test_post_photo_index_skips_different_project_peer(self):
        svc = self._make_service()
        svc.set_group_code("G1")
        svc._project_id = "P1"
        from app.services.collab_service import PeerInfo
        svc._peers["10.9.9.2:5050"] = PeerInfo(
            ip="10.9.9.2",
            port=5050,
            group_code="G1",
            project_id="P2",
        )

        with patch("httpx.post") as mock_post:
            svc.post_photo_index("PI-002", "zip", count=1)

        mock_post.assert_not_called()

    def test_post_photo_index_network_error_silent(self):
        """Network failure is silently swallowed (fire-and-forget)."""
        svc = self._make_service()
        svc.set_group_code("G1")
        svc._project_id = "P1"
        from app.services.collab_service import PeerInfo
        svc._peers["10.9.9.3:5050"] = PeerInfo(
            ip="10.9.9.3",
            port=5050,
            group_code="G1",
            project_id="P1",
        )

        import httpx
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            # Must not raise
            svc.post_photo_index("PI-003", "jpg")

    def test_photo_index_record_to_dict(self):
        from app.services.collab_service import PhotoIndexRecord
        r = PhotoIndexRecord(
            uid="R-001",
            kind="tiff",
            count=5,
            device_id="DEV-1",
            group_code="G1",
            project_id="P1",
        )
        d = r.to_dict()
        assert d["uid"] == "R-001"
        assert d["kind"] == "tiff"
        assert d["count"] == 5
        assert d["deviceId"] == "DEV-1"
        assert d["groupCode"] == "G1"
        assert d["projectId"] == "P1"
        assert "reportedAt" in d


# ── Needs-network placeholder tests ──────────────────────────────────────────

@needs_network
def test_mdns_discovery_real_network():
    """Start service and confirm peer discovery within 10 s on real LAN.

    Run manually:  pytest tests/test_collab_service.py -m needs_network -s
    Requires at least two machines running the app on the same subnet.
    """
    svc = CollabService()
    discovered: list = []
    svc.peers_changed.connect(lambda: discovered.append(svc.peers()))
    svc.start(project_name="TestNet")

    import time
    time.sleep(10)
    svc.stop()
    assert len(svc.peers()) > 0, "No peers found on LAN within 10 s"


@needs_network
def test_remote_409_real_two_machines():
    """Create the same UID on two machines — second should get 409.

    Run manually on two machines (A and B):
      Machine A: pytest tests/test_collab_service.py::test_remote_409_real_two_machines -m needs_network -s
      Machine B: same (run simultaneously)
    """
    pytest.skip("manual two-machine test — run manually on a real LAN")


# ── update_task_status: 工作台阶段按钮的 UI 入口 ─────────────────────────────

class TestUpdateTaskStatusUiHelper:
    """镜像 oracle ensureCollabTask + update-status(server.js:4015-4031):
    任务不存在则先植入;非法迁移不抛、返回 (False, msg)。"""

    def test_creates_missing_task_then_sets_status(self):
        svc = CollabService()
        ok, msg = svc.update_task_status("ZJ-TMW-B2-001", "shooting")
        assert ok is True
        assert svc.store.get_task("ZJ-TMW-B2-001").status is TaskStatus.SHOOTING

    def test_seed_status_allows_resumed_chain(self):
        svc = CollabService()
        ok, _ = svc.update_task_status("U1", "done", seed_status="organizing")
        assert ok is True
        assert svc.store.get_task("U1").status is TaskStatus.DONE

    def test_invalid_transition_returns_false_no_raise(self):
        svc = CollabService()
        assert svc.update_task_status("U2", "shooting")[0] is True
        ok, msg = svc.update_task_status("U2", "done")  # SHOOTING→DONE 非法
        assert ok is False
        assert msg
        assert svc.store.get_task("U2").status is TaskStatus.SHOOTING

    def test_same_status_idempotent_ok(self):
        svc = CollabService()
        assert svc.update_task_status("U3", "shooting")[0] is True
        ok, _ = svc.update_task_status("U3", "shooting")
        assert ok is True
        assert svc.store.get_task("U3").status is TaskStatus.SHOOTING

    def test_invalid_status_string_returns_false(self):
        svc = CollabService()
        ok, msg = svc.update_task_status("U4", "not-a-status")
        assert ok is False

    def test_emits_tasks_changed_on_success(self):
        svc = CollabService()
        seen = []
        svc.tasks_changed.connect(lambda: seen.append(True))
        svc.update_task_status("U5", "shooting")
        assert seen

    # ── force=True: 人工标记旁路状态机(回归原型自由赋值, app.js:3303) ──────────

    def test_force_allows_skip_transition(self):
        """force=True: SHOOTING→DONE 跳格成功(默认仍非法, 见上)。"""
        svc = CollabService()
        assert svc.update_task_status("F1", "shooting")[0] is True
        ok, _ = svc.update_task_status("F1", "done", force=True)
        assert ok is True
        assert svc.store.get_task("F1").status is TaskStatus.DONE

    def test_force_allows_backward_transition(self):
        """force=True: 完成→整理中 回退成功(状态机本禁回退)。"""
        svc = CollabService()
        svc.update_task_status("F2", "done", seed_status="organizing")
        ok, _ = svc.update_task_status("F2", "organizing", force=True)
        assert ok is True
        assert svc.store.get_task("F2").status is TaskStatus.ORGANIZING

    def test_force_still_validates_status_string(self):
        """force 不放过非法枚举。"""
        svc = CollabService()
        ok, _ = svc.update_task_status("F3", "not-a-status", force=True)
        assert ok is False

    def test_default_still_enforces_state_machine(self):
        """红线: 默认 force=False 时 SHOOTING→DONE 仍非法(契约不破)。"""
        svc = CollabService()
        assert svc.update_task_status("F4", "shooting")[0] is True
        ok, _ = svc.update_task_status("F4", "done")  # 无 force
        assert ok is False
        assert svc.store.get_task("F4").status is TaskStatus.SHOOTING


class TestUpdateStatusBroadcast:
    def test_broadcast_sends_force_flag(self):
        svc = CollabService()
        svc.set_group_code("G1")
        svc._peers["10.0.0.2:5050"] = PeerInfo(
            ip="10.0.0.2", port=5050, group_code="G1",
        )
        svc.store.create("BC-001")
        posted: list[dict] = []

        def fake_post(url: str, **kwargs):
            posted.append(kwargs.get("json", {}))
            m = MagicMock()
            m.status_code = 200
            return m

        with patch("httpx.post", side_effect=fake_post):
            svc.update_task_status("BC-001", "shooting", force=True, broadcast=True)

        assert posted
        assert posted[0]["force"] is True
        assert posted[0]["status"] == "shooting"


class TestPhotoIndexReceive:
    def test_on_photo_index_received_logs_and_emits(self):
        svc = CollabService()
        seen: list[tuple] = []
        svc.photo_index_received.connect(
            lambda uid, kind, count, device: seen.append((uid, kind, count, device))
        )
        svc._on_photo_index_received("U9", "tiff", 2, "peer-pc")
        assert seen == [("U9", "tiff", 2, "peer-pc")]
        entries = svc.activity_log.recent()
        assert any(e.action == "photo_index" and e.target_uid == "U9" for e in entries)


class TestEnsureRunning:
    def test_starts_when_team_code_saved(self):
        svc = CollabService()
        ctx = MagicMock()
        ctx.settings.team_code = "TEAM-99"
        ctx.settings.last_project_dir = ""
        ctx.current_project_dir = None

        def _fake_start(**kwargs):
            svc._running = True
            if kwargs.get("group_code"):
                svc._group_code = kwargs["group_code"]

        with patch.object(svc, "start", side_effect=_fake_start):
            assert svc.ensure_running(ctx) is True
        assert svc.is_running()
        assert svc.group_code == "TEAM-99"

    def test_no_op_without_team_code(self):
        svc = CollabService()
        ctx = MagicMock()
        ctx.settings.team_code = ""
        ctx.current_project_dir = None
        assert svc.ensure_running(ctx) is False
        assert not svc.is_running()

    def test_create_session_calls_ensure_running(self):
        svc = CollabService()
        with patch.object(svc, "ensure_running", return_value=True) as er:
            code = svc.create_session()
        assert code
        er.assert_called_once()
