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


# ── FastAPI create endpoint: reject cross-group ──────────────────────────────

class TestCreateEndpointGroupGuard:
    """POST /api/collab/tasks/create must reject claims from a foreign group."""

    def _client(self, local_group: str):
        from fastapi.testclient import TestClient
        store = TaskStore()
        app = _build_fastapi_app(store, lambda: {"groupCode": local_group})
        return TestClient(app), store

    def test_same_group_create_accepted(self):
        client, store = self._client("G1")
        r = client.post("/api/collab/tasks/create",
                        json={"uid": "U1", "groupCode": "G1"})
        assert r.status_code == 201
        assert store.exists("U1")

    def test_foreign_group_create_rejected_403(self):
        client, store = self._client("G1")
        r = client.post("/api/collab/tasks/create",
                        json={"uid": "U2", "groupCode": "G2"})
        assert r.status_code == 403
        assert not store.exists("U2")

    def test_missing_group_create_rejected_403(self):
        client, store = self._client("G1")
        r = client.post("/api/collab/tasks/create", json={"uid": "U3"})
        assert r.status_code == 403
        assert not store.exists("U3")

    def test_node_with_empty_group_rejects_everyone(self):
        """A node not in any group must not accept foreign claims."""
        client, store = self._client("")
        r = client.post("/api/collab/tasks/create",
                        json={"uid": "U4", "groupCode": "G1"})
        assert r.status_code == 403
        assert not store.exists("U4")


# ── FastAPI release endpoint ─────────────────────────────────────────────────

class TestReleaseEndpointGroupGuard:
    def _client(self, local_group: str):
        from fastapi.testclient import TestClient
        store = TaskStore()
        app = _build_fastapi_app(store, lambda: {"groupCode": local_group})
        return TestClient(app), store

    def test_same_group_release_deletes(self):
        client, store = self._client("G1")
        store.create("U1")
        r = client.post("/api/collab/tasks/release",
                        json={"uid": "U1", "groupCode": "G1"})
        assert r.status_code == 200
        assert not store.exists("U1")

    def test_foreign_group_release_rejected_403(self):
        client, store = self._client("G1")
        store.create("U1")
        r = client.post("/api/collab/tasks/release",
                        json={"uid": "U1", "groupCode": "G2"})
        assert r.status_code == 403
        assert store.exists("U1")  # untouched


# ── /api/node/reachback (one-way firewall detection) ─────────────────────────

class TestReachbackEndpoint:
    def _client(self):
        from fastapi.testclient import TestClient
        return TestClient(_build_fastapi_app(TaskStore(), lambda: {"groupCode": "G1"}))

    def test_reachback_reports_reachable(self):
        from unittest.mock import patch, MagicMock
        client = self._client()
        with patch("httpx.get", return_value=MagicMock(status_code=200)):
            r = client.post("/api/node/reachback", json={"ip": "1.2.3.4", "port": 5050})
        assert r.status_code == 200
        assert r.json()["reachable"] is True

    def test_reachback_reports_unreachable_on_error(self):
        from unittest.mock import patch
        import httpx
        client = self._client()
        with patch("httpx.get", side_effect=httpx.ConnectError("no")):
            r = client.post("/api/node/reachback", json={"ip": "1.2.3.4", "port": 5050})
        assert r.json()["reachable"] is False

    def test_reachback_requires_ip_port(self):
        client = self._client()
        r = client.post("/api/node/reachback", json={})
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
            return True, ""

        svc._remote_create = fake_remote  # type: ignore[assignment]
        ok, msg = svc.create_task("UID-G", assignee="A")
        assert ok, msg
        assert called_peers == ["1.1.1.1"]  # foreign-group peer skipped

    def test_no_group_no_broadcast(self):
        """With empty local group, create stays local-only (no peer broadcast)."""
        svc = CollabService()  # no group
        with svc._peers_lock:
            svc._peers["1.1.1.1:5050"] = PeerInfo(ip="1.1.1.1", port=5050, group_code="G1")

        called = []
        svc._remote_create = lambda *a: (called.append(a), (True, ""))[1]  # type: ignore
        ok, _ = svc.create_task("UID-LOCAL")
        assert ok
        assert called == []
