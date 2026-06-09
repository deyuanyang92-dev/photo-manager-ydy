"""tests/test_metadata_panel.py — Unit tests for MetadataPanel widget.

Covers:
  2-D  metaReverseGeocode: geocode button exists; _NominatimWorker emits zh result.
  2-E  WoRMS quick-fill button: exists next to taxonomy section.
"""
from __future__ import annotations

import json
import sys
import types
import unittest.mock as mock
from pathlib import Path

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_ctx(**kwargs):
    """Return a minimal AppContext-like namespace."""
    ctx = types.SimpleNamespace(
        current_project_dir=kwargs.get("project_dir", None),
        worms_fill_specimen=None,
    )
    return ctx


# ── 2-E: WoRMS quick-fill button (now on the 分类标签 card) ────────────────────

class TestWormsButton:
    """WoRMS quick-fill button moved with taxonomy to TaxonCardPanel (2-E)."""

    def test_worms_button_exists(self, qtbot):
        from app.widgets.taxon_card_panel import TaxonCardPanel
        ctx = _make_ctx()
        panel = TaxonCardPanel(ctx)
        qtbot.addWidget(panel)
        assert hasattr(panel, "_worms_btn"), (
            "TaxonCardPanel must have _worms_btn attribute"
        )
        btn = panel._worms_btn
        assert btn is not None
        assert btn.text() == "WoRMS 查"

    def test_worms_button_is_enabled_by_default(self, qtbot):
        from app.widgets.taxon_card_panel import TaxonCardPanel
        ctx = _make_ctx()
        panel = TaxonCardPanel(ctx)
        qtbot.addWidget(panel)
        assert panel._worms_btn.isEnabled()

    def test_worms_button_has_tooltip(self, qtbot):
        from app.widgets.taxon_card_panel import TaxonCardPanel
        ctx = _make_ctx()
        panel = TaxonCardPanel(ctx)
        qtbot.addWidget(panel)
        tip = panel._worms_btn.toolTip()
        assert tip, "WoRMS button must have a tooltip"


# ── 2-D: _NominatimWorker emits zh result ─────────────────────────────────────

class TestGeoWorker:
    """_NominatimWorker emits nominatim_to_zh result via result_ready signal."""

    def test_geo_worker_emits_zh(self, qtbot):
        """Worker must emit a non-empty string from a mocked Nominatim response."""
        from app.widgets.metadata_panel import _NominatimWorker

        nominatim_payload = {
            "display_name": "鼓浪屿, 厦门市, 福建省, 中国",
            "address": {
                "state": "福建省",
                "city": "厦门市",
                "suburb": "鼓浪屿",
            },
        }
        raw_bytes = json.dumps(nominatim_payload).encode()

        received: list[str] = []
        errors: list[str] = []

        worker = _NominatimWorker(lat=24.44, lon=118.07)

        worker.result_ready.connect(received.append)
        worker.error_occurred.connect(errors.append)

        class _FakeResp:
            def read(self):
                return raw_bytes
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass

        with mock.patch("urllib.request.urlopen", return_value=_FakeResp()):
            with qtbot.waitSignal(worker.result_ready, timeout=3000):
                worker.run()

        assert received, f"result_ready not emitted; errors={errors}"
        assert received[0], "emitted result must be non-empty"
        assert errors == [], f"unexpected errors: {errors}"

    def test_geo_worker_emits_error_on_network_failure(self, qtbot):
        """Worker must emit error_occurred on network exception."""
        from app.widgets.metadata_panel import _NominatimWorker

        errors: list[str] = []
        results: list[str] = []

        worker = _NominatimWorker(lat=0.0, lon=0.0)
        worker.result_ready.connect(results.append)
        worker.error_occurred.connect(errors.append)

        with mock.patch(
            "urllib.request.urlopen",
            side_effect=OSError("network unreachable"),
        ):
            with qtbot.waitSignal(worker.error_occurred, timeout=3000):
                worker.run()

        assert errors, "error_occurred must be emitted on network failure"
        assert results == [], "result_ready must not be emitted on failure"

    def test_geo_worker_result_uses_nominatim_to_zh(self, qtbot):
        """Worker result must agree with what nominatim_to_zh would produce."""
        from app.widgets.metadata_panel import _NominatimWorker, _nominatim_to_zh
        from app.utils.coord_utils import nominatim_to_zh as coord_nominatim_to_zh

        nominatim_payload = {
            "display_name": "上海市, 中国",
            "address": {
                "state": "上海市",
                "city": "上海市",
            },
        }
        raw_bytes = json.dumps(nominatim_payload).encode()

        received: list[str] = []
        worker = _NominatimWorker(lat=31.23, lon=121.47)
        worker.result_ready.connect(received.append)

        class _FakeResp:
            def read(self):
                return raw_bytes
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass

        with mock.patch("urllib.request.urlopen", return_value=_FakeResp()):
            with qtbot.waitSignal(worker.result_ready, timeout=3000):
                worker.run()

        assert received
        expected = _nominatim_to_zh(nominatim_payload)
        assert received[0] == expected, (
            f"Worker emitted '{received[0]}' but _nominatim_to_zh returns '{expected}'"
        )


# ── 2-D: auto reverse-geocode + map pick in MetadataPanel ─────────────────────

class TestGeocode:
    """经纬度 → 采集地名 is now AUTO + INLINE (no button, no popup), plus a
    地图选点 button.  Mirrors the web meta card (debounced auto-fill)."""

    def test_map_pick_button_exists_on_lon_row(self, qtbot):
        """📍 map button must be on the lon row (oracle app.js:10295), not geo_area row."""
        from app.widgets.metadata_panel import MetadataPanel
        ctx = _make_ctx()
        panel = MetadataPanel(ctx)
        qtbot.addWidget(panel)
        assert hasattr(panel, "_map_btn") and panel._map_btn is not None
        assert panel._map_btn.text() == "📍"
        assert panel._map_btn.toolTip() == "地图选点"

    def test_gps_button_exists_on_geo_area_row(self, qtbot):
        """📡 GPS button must be on the 采集地理区 row (oracle app.js:10336)."""
        from app.widgets.metadata_panel import MetadataPanel
        ctx = _make_ctx()
        panel = MetadataPanel(ctx)
        qtbot.addWidget(panel)
        assert hasattr(panel, "_gps_btn") and panel._gps_btn is not None
        assert panel._gps_btn.text() == "📡"
        assert "当前位置" in panel._gps_btn.toolTip()

    def test_gps_worker_fills_lonlat(self, qtbot):
        """_GpsWorker result → lon/lat fields filled + auto-reverse triggered."""
        from app.widgets.metadata_panel import MetadataPanel
        ctx = _make_ctx()
        panel = MetadataPanel(ctx)
        qtbot.addWidget(panel)
        with mock.patch.object(panel, "_do_auto_reverse") as mock_rev:
            panel._on_gps_result(26.345678, 119.123456)
            assert panel._lon.text() == "119.123456"
            assert panel._lat.text() == "26.345678"
            assert panel._geo_autofilled is True
            mock_rev.assert_called_once()

    def test_gps_worker_emits_result_on_success(self, qtbot):
        """_GpsWorker must emit result_ready with (lat, lon) on success."""
        from app.widgets.metadata_panel import _GpsWorker
        results: list = []
        worker = _GpsWorker()
        worker.result_ready.connect(lambda la, lo: results.append((la, lo)))
        payload = {"latitude": 31.23, "longitude": 121.47}
        with mock.patch("httpx.get") as mock_get:
            mock_get.return_value.json.return_value = payload
            with qtbot.waitSignal(worker.result_ready, timeout=3000):
                worker.run()
        assert results == [(31.23, 121.47)]

    def test_gps_worker_emits_error_on_failure(self, qtbot):
        """_GpsWorker must emit error_occurred when ipapi.co returns no coords."""
        from app.widgets.metadata_panel import _GpsWorker
        errors: list = []
        worker = _GpsWorker()
        worker.error_occurred.connect(errors.append)
        payload = {"error": True, "reason": "RateLimited"}
        with mock.patch("httpx.get") as mock_get:
            mock_get.return_value.json.return_value = payload
            with qtbot.waitSignal(worker.error_occurred, timeout=3000):
                worker.run()
        assert errors

    def test_geocode_result_fills_inline_no_dialog(self, qtbot):
        """A reverse-geocode result fills 采集地理区 inline + sets status text;
        it must NOT pop a QMessageBox."""
        from app.widgets.metadata_panel import MetadataPanel
        ctx = _make_ctx()
        panel = MetadataPanel(ctx)
        qtbot.addWidget(panel)
        with mock.patch("app.utils.ui.warn") as warn_mock, \
                mock.patch("app.utils.ui.question") as q_mock:
            panel._geo_autofilled = True  # allow fill
            panel._on_geocode_result("浙江省·三门湾")
            assert panel._geo_area.text() == "浙江省·三门湾"
            assert "已自动填入" in panel._geo_status.text()
            warn_mock.assert_not_called()
            q_mock.assert_not_called()

    def test_geocode_error_inline_no_dialog(self, qtbot):
        from app.widgets.metadata_panel import MetadataPanel
        ctx = _make_ctx()
        panel = MetadataPanel(ctx)
        qtbot.addWidget(panel)
        with mock.patch("app.utils.ui.warn") as warn_mock:
            panel._on_geocode_error("network down")
            assert panel._geo_status.text()  # inline status set
            warn_mock.assert_not_called()

    def test_auto_reverse_skips_user_typed_place(self, qtbot):
        """If the user typed a place name, auto-reverse must not overwrite it."""
        from app.widgets.metadata_panel import MetadataPanel
        ctx = _make_ctx()
        panel = MetadataPanel(ctx)
        qtbot.addWidget(panel)
        panel._lon.setText("119.5")
        panel._lat.setText("26.3")
        panel._geo_area.setText("手填地名")
        panel._geo_autofilled = False
        with mock.patch.object(panel, "_geocode_worker", create=True):
            panel._do_auto_reverse()
        assert panel._geo_area.text() == "手填地名"
