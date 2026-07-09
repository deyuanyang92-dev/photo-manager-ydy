"""test_collab_group_sync.py — cross-group isolation (headline contract test).

A collaboration group is identified by an explicit ``group_code``.  Two nodes
sync UID claims/tasks ONLY when they share the same non-empty code.  This
prevents two teams on the same LAN from polluting each other's UID namespace.

Empty code = no group = no sync (the safe default).

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_collab_group_sync.py -v
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.collab_service import (
    CollabService,
    PeerInfo,
    TaskStore,
    _build_fastapi_app,
)


def _post(app, path: str, payload: dict):
    """Call an ASGI app without Starlette's thread-based TestClient.

    Starlette 1.0 + AnyIO 4.12 can deadlock TestClient's blocking portal under
    Python 3.13 before the request reaches the endpoint.  ASGITransport tests
    the same application directly and has no background lifecycle thread.
    """
    import asyncio
    import httpx

    async def request():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(path, json=payload)

    return asyncio.run(request())


def _get(app, path: str, params: dict | None = None):
    import asyncio
    import httpx

    async def request():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path, params=params or {})

    return asyncio.run(request())


# ── FastAPI create endpoint: reject cross-group ──────────────────────────────

class TestCreateEndpointGroupGuard:
    """POST /api/collab/tasks/create must reject claims from a foreign group."""

    def _client(self, local_group: str):
        store = TaskStore()
        app = _build_fastapi_app(store, lambda: {"groupCode": local_group})
        return app, store

    def test_same_group_create_accepted(self):
        app, store = self._client("G1")
        r = _post(app, "/api/collab/tasks/create", {"uid": "U1", "groupCode": "G1"})
        assert r.status_code == 201
        assert store.exists("U1")

    def test_foreign_group_create_rejected_403(self):
        app, store = self._client("G1")
        r = _post(app, "/api/collab/tasks/create", {"uid": "U2", "groupCode": "G2"})
        assert r.status_code == 403
        assert not store.exists("U2")

    def test_missing_group_create_rejected_403(self):
        app, store = self._client("G1")
        r = _post(app, "/api/collab/tasks/create", {"uid": "U3"})
        assert r.status_code == 403
        assert not store.exists("U3")

    def test_node_with_empty_group_rejects_everyone(self):
        """A node not in any group must not accept foreign claims."""
        app, store = self._client("")
        r = _post(app, "/api/collab/tasks/create", {"uid": "U4", "groupCode": "G1"})
        assert r.status_code == 403
        assert not store.exists("U4")


# ── FastAPI release endpoint ─────────────────────────────────────────────────

class TestReleaseEndpointGroupGuard:
    def _client(self, local_group: str):
        store = TaskStore()
        app = _build_fastapi_app(store, lambda: {"groupCode": local_group})
        return app, store

    def test_same_group_release_deletes(self):
        app, store = self._client("G1")
        store.create("U1")
        r = _post(app, "/api/collab/tasks/release", {"uid": "U1", "groupCode": "G1"})
        assert r.status_code == 200
        assert not store.exists("U1")

    def test_foreign_group_release_rejected_403(self):
        app, store = self._client("G1")
        store.create("U1")
        r = _post(app, "/api/collab/tasks/release", {"uid": "U1", "groupCode": "G2"})
        assert r.status_code == 403
        assert store.exists("U1")  # untouched


# ── File manifest/download endpoint group guard ─────────────────────────────

class TestFileEndpointGroupGuard:
    def test_manifest_requires_same_group(self):
        seen = []
        app = _build_fastapi_app(
            TaskStore(),
            lambda: {"groupCode": "G1", "projectId": "P1"},
            file_manifest_fn=lambda uids=None: seen.append(uids) or {"files": []},
        )

        ok = _get(app, "/api/collab/files/manifest", {"groupCode": "G1", "projectId": "P1", "uids": "U1,U2"})
        bad = _get(app, "/api/collab/files/manifest", {"groupCode": "G2", "projectId": "P1"})
        bad_project = _get(app, "/api/collab/files/manifest", {"groupCode": "G1", "projectId": "P2"})

        assert ok.status_code == 200
        assert bad.status_code == 403
        assert bad_project.status_code == 403
        assert seen == [["U1", "U2"]]

    def test_download_requires_same_group_before_resolving_path(self):
        seen = []

        def _missing_path(_rel):
            seen.append(_rel)
            raise FileNotFoundError("missing")

        app = _build_fastapi_app(
            TaskStore(),
            lambda: {"groupCode": "G1", "projectId": "P1"},
            file_path_fn=_missing_path,
        )

        ok = _get(app, "/api/collab/files/download", {"groupCode": "G1", "projectId": "P1", "path": "incoming-jpg/a.jpg"})
        bad = _get(app, "/api/collab/files/download", {"groupCode": "G2", "projectId": "P1", "path": "incoming-jpg/a.jpg"})
        bad_project = _get(app, "/api/collab/files/download", {"groupCode": "G1", "projectId": "P2", "path": "incoming-jpg/a.jpg"})

        assert ok.status_code == 404
        assert bad.status_code == 403
        assert bad_project.status_code == 403
        assert seen == ["incoming-jpg/a.jpg"]


# ── Photo-index endpoint guard ────────────────────────────────────────────────

class TestPhotoIndexEndpointGuard:
    def test_photo_index_requires_same_group_and_project(self):
        app = _build_fastapi_app(
            TaskStore(),
            lambda: {"groupCode": "G1", "projectId": "P1"},
        )

        ok = _post(app, "/api/collab/photo-index", {
            "uid": "U1",
            "kind": "tiff",
            "count": 1,
            "groupCode": "G1",
            "projectId": "P1",
        })
        bad_group = _post(app, "/api/collab/photo-index", {
            "uid": "U1",
            "kind": "tiff",
            "groupCode": "G2",
            "projectId": "P1",
        })
        bad_project = _post(app, "/api/collab/photo-index", {
            "uid": "U1",
            "kind": "tiff",
            "groupCode": "G1",
            "projectId": "P2",
        })

        assert ok.status_code == 200
        assert bad_group.status_code == 403
        assert bad_project.status_code == 403

    def test_photo_index_invokes_callback(self):
        seen: list[tuple] = []

        app = _build_fastapi_app(
            TaskStore(),
            lambda: {"groupCode": "G1", "projectId": "P1"},
            photo_index_fn=lambda uid, kind, count, device: seen.append(
                (uid, kind, count, device)
            ),
        )

        resp = _post(app, "/api/collab/photo-index", {
            "uid": "U1",
            "kind": "zip",
            "count": 3,
            "deviceId": "lab-pc",
            "groupCode": "G1",
            "projectId": "P1",
        })

        assert resp.status_code == 200
        assert seen == [("U1", "zip", 3, "lab-pc")]


class TestUpdateStatusForce:
    def test_api_force_allows_skip_transition(self):
        from app.services.collab_types import TaskStatus

        store = TaskStore()
        store.create("U1")
        store.update_status("U1", TaskStatus.SHOOTING)
        app = _build_fastapi_app(
            store,
            lambda: {"groupCode": "G1", "projectId": "P1"},
        )

        blocked = _post(app, "/api/collab/tasks/update-status", {
            "uid": "U1",
            "status": "done",
            "groupCode": "G1",
        })
        forced = _post(app, "/api/collab/tasks/update-status", {
            "uid": "U1",
            "status": "done",
            "groupCode": "G1",
            "force": True,
        })

        assert blocked.status_code == 422
        assert forced.status_code == 200
        assert store.get_task("U1").status.value == "done"


class TestTaskListEndpointGroupGuard:
    def test_list_tasks_requires_same_group(self):
        store = TaskStore()
        store.create("U1")
        app = _build_fastapi_app(
            store,
            lambda: {"groupCode": "G1", "projectId": "P1"},
        )

        ok = _get(app, "/api/collab/tasks", {"groupCode": "G1"})
        missing = _get(app, "/api/collab/tasks")
        bad = _get(app, "/api/collab/tasks", {"groupCode": "G2"})

        assert ok.status_code == 200
        assert ok.json()[0]["uid"] == "U1"
        assert missing.status_code == 403
        assert bad.status_code == 403


class TestSpecimenEndpointGuard:
    def test_list_specimens_requires_same_group_and_project_before_provider(self):
        seen = []
        app = _build_fastapi_app(
            TaskStore(),
            lambda: {"groupCode": "G1", "projectId": "P1"},
            specimen_provider_fn=lambda uid=None: seen.append(uid) or [{"uid": "U1"}],
        )

        ok = _get(app, "/api/collab/specimens", {"groupCode": "G1", "projectId": "P1"})
        missing = _get(app, "/api/collab/specimens")
        bad_group = _get(app, "/api/collab/specimens", {"groupCode": "G2", "projectId": "P1"})
        bad_project = _get(app, "/api/collab/specimens", {"groupCode": "G1", "projectId": "P2"})

        assert ok.status_code == 200
        assert ok.json() == [{"uid": "U1"}]
        assert missing.status_code == 403
        assert bad_group.status_code == 403
        assert bad_project.status_code == 403
        assert seen == [None]

    def test_push_specimens_requires_same_group_and_project_before_write(self):
        seen = []
        app = _build_fastapi_app(
            TaskStore(),
            lambda: {"groupCode": "G1", "projectId": "P1"},
            specimen_writer_fn=lambda specimens: seen.append(specimens) or len(specimens),
        )

        ok = _post(app, "/api/collab/specimens/push", {
            "groupCode": "G1",
            "projectId": "P1",
            "specimens": [{"uid": "U1"}],
        })
        missing = _post(app, "/api/collab/specimens/push", {
            "specimens": [{"uid": "U2"}],
        })
        bad_group = _post(app, "/api/collab/specimens/push", {
            "groupCode": "G2",
            "projectId": "P1",
            "specimens": [{"uid": "U3"}],
        })
        bad_project = _post(app, "/api/collab/specimens/push", {
            "groupCode": "G1",
            "projectId": "P2",
            "specimens": [{"uid": "U4"}],
        })

        assert ok.status_code == 200
        assert ok.json()["written"] == 1
        assert missing.status_code == 403
        assert bad_group.status_code == 403
        assert bad_project.status_code == 403
        assert seen == [[{"uid": "U1"}]]


class TestActivityEndpointGroupGuard:
    def test_get_activity_requires_same_group(self):
        activity = MagicMock()
        activity.to_dicts.return_value = [{"type": "claimed", "uid": "U1"}]
        app = _build_fastapi_app(
            TaskStore(),
            lambda: {"groupCode": "G1", "projectId": "P1"},
            activity_log=activity,
        )

        ok = _get(app, "/api/collab/activity", {"groupCode": "G1"})
        missing = _get(app, "/api/collab/activity")
        bad = _get(app, "/api/collab/activity", {"groupCode": "G2"})

        assert ok.status_code == 200
        assert ok.json() == [{"type": "claimed", "uid": "U1"}]
        assert missing.status_code == 403
        assert bad.status_code == 403
        activity.to_dicts.assert_called_once_with()


# ── /api/node/reachback (one-way firewall detection) ─────────────────────────

class TestReachbackEndpoint:
    def _client(self):
        return _build_fastapi_app(TaskStore(), lambda: {"groupCode": "G1"})

    def test_reachback_reports_reachable(self):
        from unittest.mock import patch, MagicMock
        app = self._client()
        with patch("httpx.get", return_value=MagicMock(status_code=200)):
            r = _post(app, "/api/node/reachback", {"ip": "1.2.3.4", "port": 5050})
        assert r.status_code == 200
        assert r.json()["reachable"] is True

    def test_reachback_reports_unreachable_on_error(self):
        from unittest.mock import patch
        import httpx
        app = self._client()
        with patch("httpx.get", side_effect=httpx.ConnectError("no")):
            r = _post(app, "/api/node/reachback", {"ip": "1.2.3.4", "port": 5050})
        assert r.json()["reachable"] is False

    def test_reachback_requires_ip_port(self):
        app = self._client()
        r = _post(app, "/api/node/reachback", {})
        assert r.status_code == 400


# ── _sync_peer: skip foreign-group peers ─────────────────────────────────────

class TestSyncPeerGroupFilter:
    def test_sync_skips_foreign_group_peer(self):
        svc = CollabService()
        svc.set_group_code("G1")
        peer = PeerInfo(ip="1.2.3.4", port=5050, group_code="G2")
        with patch("httpx.get") as mock_get:
            changed = svc._sync_peer(peer)
        assert changed == 0
        mock_get.assert_not_called()

    def test_sync_skips_when_local_group_empty(self):
        svc = CollabService()  # no group set
        peer = PeerInfo(ip="1.2.3.4", port=5050, group_code="G1")
        with patch("httpx.get") as mock_get:
            changed = svc._sync_peer(peer)
        assert changed == 0
        mock_get.assert_not_called()

    def test_sync_proceeds_for_same_group_peer(self):
        svc = CollabService()
        svc.set_group_code("G1")
        peer = PeerInfo(ip="1.2.3.4", port=5050, group_code="G1")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"uid": "U9", "status": "created", "updatedAt": "2026-01-01T00:00:00+00:00"}
        ]
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            changed = svc._sync_peer(peer)
        mock_get.assert_called_once()
        assert changed == 1
        assert svc.store.exists("U9")

    def test_task_sync_proceeds_for_same_group_different_project(self):
        """UID/task coordination is team-scoped; media/specimen data is not."""
        svc = CollabService()
        svc.set_group_code("G1")
        svc._project_id = "P-LOCAL"
        peer = PeerInfo(
            ip="1.2.3.4",
            port=5050,
            group_code="G1",
            project_id="P-OTHER",
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"uid": "U10", "status": "created", "updatedAt": "2026-01-01T00:00:00+00:00"}
        ]
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            changed = svc._sync_peer(peer)
        mock_get.assert_called_once()
        assert changed == 1
        assert svc.store.exists("U10")
        assert svc._data_sync_allowed(peer) is False


# ── LWW clock-skew guard (task store merge) ──────────────────────────────────

class TestLwwClockSkewGuard:
    """LWW relies on wall-clock updated_at.  When a peer's clock is measured
    to skew beyond the trust threshold, a fast-clock peer would always "win"
    and silently clobber genuinely-newer local edits.  The guard refuses to
    overwrite a differing local status when the remote clock is untrusted."""

    def test_untrusted_remote_clock_does_not_clobber_local_status(self):
        store = TaskStore()
        # Local task edited recently (local clock).
        store.merge_from_peer([{"uid": "U1", "status": "shot_done",
                                "updatedAt": "2026-07-09T10:00:00+00:00"}])
        # Remote claims DONE with a LATER timestamp (peer clock is fast).
        remote = [{"uid": "U1", "status": "done",
                   "updatedAt": "2026-07-09T11:00:00+00:00"}]
        guarded: list[dict] = []
        changed = store.merge_from_peer(remote, overwrites_out=[],
                                        trust_remote_clock=False,
                                        skew_guarded_out=guarded)
        assert changed == 0                                 # local NOT overwritten
        assert store.get_task("U1").status.value == "shot_done"
        assert any(g["uid"] == "U1" for g in guarded)

    def test_trusted_remote_clock_keeps_legacy_lww(self):
        """Default (trust_remote_clock=True) preserves legacy LWW overwrite."""
        store = TaskStore()
        store.merge_from_peer([{"uid": "U2", "status": "shot_done",
                                "updatedAt": "2026-07-09T10:00:00+00:00"}])
        remote = [{"uid": "U2", "status": "done",
                   "updatedAt": "2026-07-09T11:00:00+00:00"}]
        changed = store.merge_from_peer(remote)             # default trust
        assert changed == 1
        assert store.get_task("U2").status.value == "done"

    def test_skewed_peer_does_not_overwrite_local_via_sync(self):
        """End-to-end: a peer with measured large skew must not overwrite a
        local task during the 5 s pull-sync."""
        svc = CollabService()
        svc.set_group_code("G1")
        # Local newer task.
        svc.store.merge_from_peer([{"uid": "U3", "status": "shot_done",
                                    "updatedAt": "2026-07-09T10:00:00+00:00"}])
        peer = PeerInfo(ip="1.2.3.4", port=5050, group_code="G1")
        peer.clock_skew_ms = 60_000.0                        # 60 s skew → untrusted
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"uid": "U3", "status": "done",
             "updatedAt": "2026-07-09T11:00:00+00:00"}      # later ts (fast clock)
        ]
        with patch("httpx.get", return_value=mock_resp):
            changed = svc._sync_peer(peer)
        assert changed == 0
        assert svc.store.get_task("U3").status.value == "shot_done"


# ── create_task: broadcast only to same-group peers ──────────────────────────

class TestCreateTaskGroupBroadcast:
    def test_broadcast_only_to_same_group(self):
        svc = CollabService()
        svc.set_group_code("G1")
        with svc._peers_lock:
            svc._peers["1.1.1.1:5050"] = PeerInfo(ip="1.1.1.1", port=5050, group_code="G1")
            svc._peers["2.2.2.2:5050"] = PeerInfo(ip="2.2.2.2", port=5050, group_code="G2")

        called_peers: list[str] = []

        def fake_remote(peer, uid, assignee, device_id):
            called_peers.append(peer.ip)
            return True, "", False

        svc._remote_create = fake_remote  # type: ignore[assignment]
        ok, msg = svc.create_task("UID-G", assignee="A")
        assert ok, msg
        assert called_peers == ["1.1.1.1"]  # foreign-group peer skipped

    def test_broadcast_to_same_group_even_when_project_differs(self):
        svc = CollabService()
        svc.set_group_code("G1")
        svc._project_id = "P-LOCAL"
        with svc._peers_lock:
            svc._peers["1.1.1.1:5050"] = PeerInfo(
                ip="1.1.1.1",
                port=5050,
                group_code="G1",
                project_id="P-OTHER",
            )

        called_peers: list[str] = []

        def fake_remote(peer, uid, assignee, device_id):
            called_peers.append(peer.ip)
            return True, "", False

        svc._remote_create = fake_remote  # type: ignore[assignment]
        ok, msg = svc.create_task("UID-CROSS-PROJECT", assignee="A")
        assert ok, msg
        assert called_peers == ["1.1.1.1"]

    def test_no_group_no_broadcast(self):
        """With empty local group, create stays local-only (no peer broadcast)."""
        svc = CollabService()  # no group
        with svc._peers_lock:
            svc._peers["1.1.1.1:5050"] = PeerInfo(ip="1.1.1.1", port=5050, group_code="G1")

        called = []
        svc._remote_create = lambda *a: (called.append(a), (True, "", False))[1]  # type: ignore
        ok, _ = svc.create_task("UID-LOCAL")
        assert ok
        assert called == []
