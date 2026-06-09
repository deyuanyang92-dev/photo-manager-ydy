"""Tests for app/services/geocode_service.py — unified geocoding."""
from __future__ import annotations

import pytest

from app.services import geocode_service as gs
from app.utils.coord_utils import gcj02_to_wgs84


# ── Nominatim backend ────────────────────────────────────────────────────────

def test_nominatim_parses_results_and_biases_to_china(monkeypatch):
    captured = {}

    def fake_get(url, *, headers=None, timeout=gs._TIMEOUT):
        captured["url"] = url
        captured["headers"] = headers
        return [
            {"lat": "21.4810", "lon": "109.1200", "display_name": "北海市, 广西, 中国"},
            {"lat": "bad", "lon": "0"},  # malformed → skipped
        ]

    monkeypatch.setattr(gs, "_http_get_json", fake_get)

    out = gs.geocode("北海", backend="nominatim")

    assert "countrycodes=cn" in captured["url"]
    assert len(out) == 1
    assert out[0]["wgs"]["lat"] == pytest.approx(21.4810)
    assert out[0]["wgs"]["lon"] == pytest.approx(109.1200)
    assert out[0]["name"]


# ── AMap backend ─────────────────────────────────────────────────────────────

def test_amap_converts_gcj02_to_wgs84(monkeypatch):
    # A known GCJ-02 point inside China; result must be its WGS-84 inverse.
    gcj_lon, gcj_lat = 109.1200, 21.4810
    expected = gcj02_to_wgs84(gcj_lon, gcj_lat)

    def fake_get(url, *, headers=None, timeout=gs._TIMEOUT):
        assert "restapi.amap.com" in url
        assert "key=AMAPKEY" in url
        return {
            "status": "1",
            "pois": [{
                "name": "北海市政府",
                "location": f"{gcj_lon},{gcj_lat}",
                "pname": "广西壮族自治区",
                "cityname": "北海市",
                "adname": "海城区",
            }],
        }

    monkeypatch.setattr(gs, "_http_get_json", fake_get)

    out = gs.geocode("北海", backend="amap", amap_key="AMAPKEY")

    assert len(out) == 1
    assert out[0]["wgs"]["lon"] == pytest.approx(expected["lon"])
    assert out[0]["wgs"]["lat"] == pytest.approx(expected["lat"])
    # GCJ→WGS shifts the point, so it must NOT equal the raw GCJ input.
    assert out[0]["wgs"]["lon"] != pytest.approx(gcj_lon)
    assert "北海市政府" in out[0]["name"]


# ── backend selection ────────────────────────────────────────────────────────

def test_amap_backend_without_key_falls_back_to_nominatim(monkeypatch):
    calls = {}

    def fake_get(url, *, headers=None, timeout=gs._TIMEOUT):
        calls["url"] = url
        return []

    monkeypatch.setattr(gs, "_http_get_json", fake_get)
    gs.geocode("x", backend="amap", amap_key="   ")
    assert "nominatim" in calls["url"]


def test_empty_query_returns_empty_without_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network must not be hit for empty query")

    monkeypatch.setattr(gs, "_http_get_json", boom)
    assert gs.geocode("   ") == []


def test_resolve_backend_prefers_amap_when_key_present():
    class S:
        amap_web_key = "  KEY  "

    assert gs.resolve_backend(S()) == ("amap", "KEY")

    class S2:
        amap_web_key = ""

    assert gs.resolve_backend(S2()) == ("nominatim", "")

    assert gs.resolve_backend(object()) == ("nominatim", "")  # no attr → safe


# ── GeocodeWorker (thread wrapper) ───────────────────────────────────────────

def test_worker_emits_done(qtbot, monkeypatch):
    monkeypatch.setattr(
        gs, "_http_get_json",
        lambda *a, **k: [{"lat": "1.0", "lon": "2.0", "display_name": "x"}],
    )
    w = gs.GeocodeWorker("北海", backend="nominatim")
    got = []
    w.done.connect(got.append)
    w.run()
    assert len(got) == 1
    assert got[0][0]["wgs"] == {"lat": 1.0, "lon": 2.0}


def test_worker_emits_failed_on_exception(qtbot, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(gs, "_http_get_json", boom)
    w = gs.GeocodeWorker("北海", backend="nominatim")
    errors = []
    w.failed.connect(errors.append)
    w.run()  # must NOT raise
    assert errors and "network down" in errors[0]
