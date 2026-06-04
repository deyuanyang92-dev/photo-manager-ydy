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


# ── 2-E: WoRMS quick-fill button ─────────────────────────────────────────────

class TestWormsButton:
    """MetadataPanel exposes a WoRMS quick-fill button (2-E)."""

    def test_worms_button_exists(self, qtbot):
        from app.widgets.metadata_panel import MetadataPanel
        ctx = _make_ctx()
        panel = MetadataPanel(ctx)
        qtbot.addWidget(panel)
        assert hasattr(panel, "_worms_quick_btn"), (
            "MetadataPanel must have _worms_quick_btn attribute"
        )
        btn = panel._worms_quick_btn
        assert btn is not None
        assert btn.text() == "WoRMS 查"

    def test_worms_button_is_enabled_by_default(self, qtbot):
        from app.widgets.metadata_panel import MetadataPanel
        ctx = _make_ctx()
        panel = MetadataPanel(ctx)
        qtbot.addWidget(panel)
        assert panel._worms_quick_btn.isEnabled()

    def test_worms_button_has_tooltip(self, qtbot):
        from app.widgets.metadata_panel import MetadataPanel
        ctx = _make_ctx()
        panel = MetadataPanel(ctx)
        qtbot.addWidget(panel)
        tip = panel._worms_quick_btn.toolTip()
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


# ── 2-D: geocode button in MetadataPanel ──────────────────────────────────────

class TestGeocodeButton:
    """MetadataPanel has a geocode button that triggers reverse geocoding (2-D)."""

    def test_geocode_button_exists(self, qtbot):
        from app.widgets.metadata_panel import MetadataPanel
        ctx = _make_ctx()
        panel = MetadataPanel(ctx)
        qtbot.addWidget(panel)
        assert hasattr(panel, "_geocode_btn")
        assert panel._geocode_btn is not None
        assert "📍" in panel._geocode_btn.text() or panel._geocode_btn.text()

    def test_geocode_button_enabled_at_start(self, qtbot):
        from app.widgets.metadata_panel import MetadataPanel
        ctx = _make_ctx()
        panel = MetadataPanel(ctx)
        qtbot.addWidget(panel)
        assert panel._geocode_btn.isEnabled()
