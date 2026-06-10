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

from app.services.collab_service import (
    CollabService,
    TaskRecord,
    TaskStatus,
    TaskStore,
    _now_iso,
    is_valid_transition,
)


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
        assert {t.uid for t in store.all()} == {"U1", "U2"}

    def test_get_returns_none_for_unknown(self):
        store = TaskStore()
        assert store.get("MISSING") is None

    def test_get_returns_task(self):
        store = TaskStore()
        store.create("Q1")
        t = store.get("Q1")
        assert t is not None and t.uid == "Q1"

    def test_clear(self):
        store = TaskStore()
        store.create("C1")
        store.clear()
        assert store.all() == []

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
        assert store.get("R2").status == TaskStatus.DONE  # type: ignore[union-attr]

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
        assert store.get("R3").status == TaskStatus.SHOOTING  # type: ignore[union-attr]

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
        assert len(store.all()) == 50

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

    def test_add_manual_peer(self):
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
        assert "lanIp" in info
        assert "port" in info

    def test_local_address_format(self):
        svc = self._make_service()
        addr = svc.local_address()
        assert ":" in addr


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
        assert view.nav_title == "项目汇总"
        assert view.nav_icon == "📋"
        view.close()

    def test_view_with_service_shows_no_peers(self):
        from app.views.collab_view import CollabView

        ctx = MagicMock()
        svc = CollabService()
        ctx.collab_service = svc

        view = CollabView(ctx)
        view.on_activate()
        # Status badge should say "未发现" since no peers
        assert "未发现" in view._status_badge.text()
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
        assert sb._collab_addr.text() == "分享地址: —"
        assert sb._collab_members.text() == "成员: 0"
        sb.close()

    def test_update_with_service_shows_addr(self):
        sb = self._make_sidebar()
        svc = CollabService()
        sb.update_collab_status(svc)
        # Should contain ":" for ip:port
        assert ":" in sb._collab_addr.text()
        sb.close()
        svc.stop()

    def test_update_task_count(self):
        sb = self._make_sidebar()
        svc = CollabService()
        svc.store.create("SB-TEST-001")
        svc.store.create("SB-TEST-002")
        sb.update_collab_status(svc)
        assert "2" in sb._collab_sync.text()
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
        svc.mark_offline_draft("OD-RETRY")

        # Add a fake peer so the retry path runs
        from app.services.collab_service import PeerInfo
        svc._peers["10.9.9.1:5050"] = PeerInfo(ip="10.9.9.1", port=5050)

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"uid": "OD-RETRY", "status": "created"}

        with patch("httpx.post", return_value=mock_resp):
            promoted = svc.retry_offline_drafts()

        assert promoted == 1
        # Should be removed from queue after promotion
        assert all(d.uid != "OD-RETRY" for d in svc.load_offline_drafts())

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
        from app.services.collab_service import PeerInfo
        svc._peers["10.9.9.2:5050"] = PeerInfo(ip="10.9.9.2", port=5050)

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

    def test_post_photo_index_network_error_silent(self):
        """Network failure is silently swallowed (fire-and-forget)."""
        svc = self._make_service()
        from app.services.collab_service import PeerInfo
        svc._peers["10.9.9.3:5050"] = PeerInfo(ip="10.9.9.3", port=5050)

        import httpx
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            # Must not raise
            svc.post_photo_index("PI-003", "jpg")

    def test_photo_index_record_to_dict(self):
        from app.services.collab_service import PhotoIndexRecord
        r = PhotoIndexRecord(uid="R-001", kind="tiff", count=5, device_id="DEV-1")
        d = r.to_dict()
        assert d["uid"] == "R-001"
        assert d["kind"] == "tiff"
        assert d["count"] == 5
        assert d["deviceId"] == "DEV-1"
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
        assert svc.store.get("ZJ-TMW-B2-001").status is TaskStatus.SHOOTING

    def test_seed_status_allows_resumed_chain(self):
        svc = CollabService()
        ok, _ = svc.update_task_status("U1", "done", seed_status="organizing")
        assert ok is True
        assert svc.store.get("U1").status is TaskStatus.DONE

    def test_invalid_transition_returns_false_no_raise(self):
        svc = CollabService()
        assert svc.update_task_status("U2", "shooting")[0] is True
        ok, msg = svc.update_task_status("U2", "done")  # SHOOTING→DONE 非法
        assert ok is False
        assert msg
        assert svc.store.get("U2").status is TaskStatus.SHOOTING

    def test_same_status_idempotent_ok(self):
        svc = CollabService()
        assert svc.update_task_status("U3", "shooting")[0] is True
        ok, _ = svc.update_task_status("U3", "shooting")
        assert ok is True
        assert svc.store.get("U3").status is TaskStatus.SHOOTING

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
