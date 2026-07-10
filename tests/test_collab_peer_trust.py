"""Tests for collab peer trust / block lists."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from PyQt6.QtCore import QSettings

from app.services.collab_peer_trust import CollabPeerTrustStore, peer_key
from app.services.collab_service import CollabService, PeerInfo


@pytest.fixture
def isolated_trust_qs(tmp_path, monkeypatch):
    path = tmp_path / "trust.ini"
    qs = QSettings(str(path), QSettings.Format.IniFormat)
    monkeypatch.setattr(CollabService, "_spawn", lambda self, fn: fn())
    return qs


class TestCollabPeerTrustStore:
    def test_peer_key_format(self):
        assert peer_key("192.168.1.5", 5050) == "192.168.1.5:5050"

    def test_trust_and_block_are_mutually_exclusive(self, isolated_trust_qs):
        store = CollabPeerTrustStore(isolated_trust_qs)
        key = peer_key("10.0.0.2", 5050)
        store.trust(key)
        assert store.is_trusted(key)
        store.block(key)
        assert store.is_blocked(key)
        assert not store.is_trusted(key)


class TestCollabServicePeerTrust:
    def _svc(self, qs) -> CollabService:
        svc = CollabService()
        svc._peer_trust = CollabPeerTrustStore(qs)
        svc._group_code = "TEAM-1"
        svc._running = True
        return svc

    def test_unknown_operator_auto_blocked(self, isolated_trust_qs):
        svc = self._svc(isolated_trust_qs)
        peer = PeerInfo(
            ip="192.168.1.10",
            port=5050,
            hostname="DESKTOP-A",
            group_code="TEAM-1",
            operator_name="",
        )
        svc._peers["192.168.1.10:5050"] = peer
        svc._review_peer_after_enrich(peer)
        assert svc._peer_trust.is_blocked("192.168.1.10:5050")
        assert "192.168.1.10:5050" not in svc._peers

    def test_named_peer_open_mode_syncs_without_trust(self, isolated_trust_qs):
        svc = self._svc(isolated_trust_qs)
        peer = PeerInfo(
            ip="192.168.1.11",
            port=5050,
            group_code="TEAM-1",
            operator_name="小王",
        )
        assert svc._peer_sync_allowed(peer)

    def test_strict_mode_blocks_untrusted_peer(self, isolated_trust_qs):
        svc = self._svc(isolated_trust_qs)
        svc._peer_trust.block("192.168.1.99:5050")
        peer = PeerInfo(
            ip="192.168.1.12",
            port=5050,
            group_code="TEAM-1",
            operator_name="小李",
        )
        assert not svc._peer_sync_allowed(peer)

    def test_trust_peer_enables_sync(self, isolated_trust_qs):
        svc = self._svc(isolated_trust_qs)
        svc._peer_trust.block("192.168.1.99:5050")
        peer = PeerInfo(
            ip="192.168.1.13",
            port=5050,
            group_code="TEAM-1",
            operator_name="小张",
        )
        svc._peers["192.168.1.13:5050"] = peer
        svc.trust_peer("192.168.1.13", 5050)
        assert svc._peer_sync_allowed(peer)

    def test_peer_join_review_emitted_in_strict_mode(self, isolated_trust_qs, qtbot):
        svc = self._svc(isolated_trust_qs)
        svc._peer_trust.trust("192.168.1.1:5050")
        peer = PeerInfo(
            ip="192.168.1.20",
            port=5050,
            group_code="TEAM-1",
            operator_name="小赵",
        )
        svc._peers["192.168.1.20:5050"] = peer
        with qtbot.waitSignal(svc.peer_join_review, timeout=1000) as blocker:
            svc._review_peer_after_enrich(peer)
        assert blocker.args == ["192.168.1.20", 5050, "小赵"]

    def test_blocked_peer_not_readded(self, isolated_trust_qs):
        svc = self._svc(isolated_trust_qs)
        svc._peer_trust.block("192.168.1.30:5050")
        svc._on_peer_found("192.168.1.30", 5050, "DESKTOP-X")
        assert "192.168.1.30:5050" not in svc._peers

    def test_fetch_peer_info_then_auto_blocks_unknown(self, isolated_trust_qs):
        svc = self._svc(isolated_trust_qs)
        peer = PeerInfo(ip="192.168.1.40", port=5050, group_code="TEAM-1")
        svc._peers["192.168.1.40:5050"] = peer
        with patch.object(
            svc,
            "_fetch_node_info",
            return_value={
                "hostname": "DESKTOP-Y",
                "groupCode": "TEAM-1",
                "operatorName": "",
            },
        ):
            svc._after_peer_enriched(peer)
        assert svc._peer_trust.is_blocked("192.168.1.40:5050")
