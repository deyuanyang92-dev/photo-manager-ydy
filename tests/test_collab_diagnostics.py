"""test_collab_diagnostics.py — collaboration self-diagnostics (pure logic).

The diagnostics layer turns silent failures into plain-Chinese "problem + fix"
items so novice field users can self-debug.  These tests cover the pure check
logic (no network): missing deps, no group code, group-code mismatch, clock
skew, and the green/yellow/red health rollup.

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_collab_diagnostics.py -v
"""
from __future__ import annotations

import pytest

from PyQt6.QtWidgets import QApplication

import app.services.collab_service as cs
from app.services.collab_service import CollabService, Diagnostic, PeerInfo


_qapp = None


@pytest.fixture(autouse=True)
def qt_app():
    global _qapp
    if _qapp is None:
        _qapp = QApplication.instance() or QApplication([])
    return _qapp


def _codes(diags):
    return {d.code for d in diags}


class TestConfigCheck:
    def test_empty_group_code_warns(self):
        svc = CollabService()
        diags = svc.run_diagnostics()
        assert "config_no_group" in _codes(diags)
        assert svc.overall_health() == "yellow"

    def test_group_set_no_peers_is_ok(self):
        svc = CollabService()
        svc.set_group_code("G1")
        diags = svc.run_diagnostics()
        assert _codes(diags) == {"ok"}
        assert svc.overall_health() == "green"


class TestGroupMismatch:
    def test_mismatch_detected_and_lists_codes(self):
        svc = CollabService()
        svc.set_group_code("G1")
        with svc._peers_lock:
            svc._peers["1.1.1.1:5050"] = PeerInfo(ip="1.1.1.1", port=5050, group_code="G2")
        diags = svc.run_diagnostics()
        d = next(d for d in diags if d.code == "group_mismatch")
        assert d.level == "warn"
        assert "G2" in d.detail
        assert d.action == "adopt_group"

    def test_same_group_peer_no_mismatch(self):
        svc = CollabService()
        svc.set_group_code("G1")
        with svc._peers_lock:
            svc._peers["1.1.1.1:5050"] = PeerInfo(ip="1.1.1.1", port=5050, group_code="G1")
        diags = svc.run_diagnostics()
        assert "group_mismatch" not in _codes(diags)


class TestClockSkew:
    def test_skew_over_threshold_warns(self):
        svc = CollabService()
        svc.set_group_code("G1")
        with svc._peers_lock:
            p = PeerInfo(ip="1.1.1.1", port=5050, group_code="G1")
            p.clock_skew_ms = 30_000
            svc._peers["1.1.1.1:5050"] = p
        diags = svc.run_diagnostics()
        assert "clock_skew" in _codes(diags)

    def test_small_skew_ok(self):
        svc = CollabService()
        svc.set_group_code("G1")
        with svc._peers_lock:
            p = PeerInfo(ip="1.1.1.1", port=5050, group_code="G1")
            p.clock_skew_ms = 1_200
            svc._peers["1.1.1.1:5050"] = p
        diags = svc.run_diagnostics()
        assert "clock_skew" not in _codes(diags)


class TestDepsCheck:
    def test_missing_deps_is_error(self, monkeypatch):
        monkeypatch.setattr(cs, "_missing_deps", lambda: ["httpx"])
        svc = CollabService()
        svc.set_group_code("G1")
        diags = svc.run_diagnostics()
        assert "deps_missing" in _codes(diags)
        assert svc.overall_health() == "red"


class TestHealthRollup:
    def test_error_beats_warn(self):
        svc = CollabService()
        svc._diagnostics = [
            Diagnostic("a", "warn", "w"),
            Diagnostic("b", "error", "e"),
        ]
        assert svc.overall_health() == "red"

    def test_warn_when_only_warn(self):
        svc = CollabService()
        svc._diagnostics = [Diagnostic("a", "warn", "w")]
        assert svc.overall_health() == "yellow"

    def test_green_when_all_ok(self):
        svc = CollabService()
        svc._diagnostics = [Diagnostic("ok", "ok", "fine")]
        assert svc.overall_health() == "green"


class TestServerTime:
    def test_node_info_includes_server_time(self):
        svc = CollabService()
        info = svc._node_info()
        assert isinstance(info.get("serverTime"), float)


class TestSignal:
    def test_run_diagnostics_emits(self):
        svc = CollabService()
        fired = []
        svc.diagnostics_changed.connect(lambda: fired.append(1))
        svc.run_diagnostics()
        assert fired


class TestReachabilityProbe:
    def test_probe_sets_clock_skew(self, monkeypatch):
        import time as _t
        from unittest.mock import MagicMock
        svc = CollabService()
        svc._group_code = "G1"
        svc._port = 5050
        peer = PeerInfo(ip="2.2.2.2", port=5050, group_code="G1")

        info_resp = MagicMock(status_code=200)
        info_resp.json.return_value = {"serverTime": _t.time() - 10}  # peer 10s behind
        rb_resp = MagicMock(status_code=200)
        rb_resp.json.return_value = {"reachable": True}
        import httpx
        monkeypatch.setattr(httpx, "get", lambda *a, **k: info_resp)
        monkeypatch.setattr(httpx, "post", lambda *a, **k: rb_resp)
        svc._probe_peer(peer)
        assert peer.reachable is True
        assert peer.clock_skew_ms is not None and peer.clock_skew_ms > 8_000
        assert peer.reachback_ok is True

    def test_probe_unreachable(self, monkeypatch):
        import httpx
        svc = CollabService()
        monkeypatch.setattr(httpx, "get",
                            lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("x")))
        peer = PeerInfo(ip="2.2.2.2", port=5050)
        svc._probe_peer(peer)
        assert peer.reachable is False

    def test_firewall_diag_when_reachback_fails(self):
        svc = CollabService()
        svc.set_group_code("G1")
        with svc._peers_lock:
            p = PeerInfo(ip="2.2.2.2", port=5050, group_code="G1")
            p.reachable = True
            p.reachback_ok = False
            svc._peers["2.2.2.2:5050"] = p
        diags = svc.run_diagnostics()
        d = next(d for d in diags if d.code == "firewall_blocked")
        assert d.level == "error"
        assert d.action == "open_firewall"

    def test_mdns_error_produces_warn(self):
        svc = CollabService()
        svc.set_group_code("G1")
        svc._on_discovery_error("zeroconf not installed")
        diags = svc.run_diagnostics()
        assert "mdns_unavailable" in {d.code for d in diags}
