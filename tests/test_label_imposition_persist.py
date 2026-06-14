"""tests/test_label_imposition_persist.py — 排版设计 settings persistence.

Imposition (拼版) settings are stored per bucket as JSON in QSettings via
label_service.persist_imposition / persisted_imposition, validated through
sanitize_imposition (whitelist + clamp). QSettings is isolated to a temp INI
by monkeypatching the class in the label_service module namespace (same
pattern as tests/test_label_library.py).
"""

from __future__ import annotations

import sys

import pytest

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])
    yield app


@pytest.fixture()
def iso_settings(qt_app, tmp_path, monkeypatch):
    """Route label_service QSettings(org, app) calls to a temp INI."""
    import app.services.label_service as svc

    ini = str(tmp_path / "imposition.ini")

    class _IniSettings(QSettings):
        def __init__(self, *args, **kwargs):
            super().__init__(ini, QSettings.Format.IniFormat)

    monkeypatch.setattr(svc, "QSettings", _IniSettings)
    return ini


class TestSanitizeImposition:
    def test_non_dict_returns_empty(self):
        from app.services.label_service import sanitize_imposition
        assert sanitize_imposition(None) == {}
        assert sanitize_imposition("junk") == {}
        assert sanitize_imposition([1, 2]) == {}

    def test_unknown_keys_dropped(self):
        from app.services.label_service import sanitize_imposition
        assert sanitize_imposition({"foo": 1, "bar": "x"}) == {}

    def test_floats_clamped(self):
        from app.services.label_service import sanitize_imposition
        out = sanitize_imposition({"marginMm": 99, "marginLeftMm": -4,
                                   "gapXMm": 77, "gapMm": 1.5})
        assert out["marginMm"] == 50.0
        assert out["marginLeftMm"] == 0.0
        assert out["gapXMm"] == 30.0
        assert out["gapMm"] == 1.5

    def test_force_grid_clamped_and_auto_dropped(self):
        from app.services.label_service import sanitize_imposition
        out = sanitize_imposition({"forceCols": 200, "forceRows": 0})
        assert out == {"forceCols": 50}

    def test_start_slot_positive_only(self):
        from app.services.label_service import sanitize_imposition
        assert sanitize_imposition({"startSlot": -3}) == {}
        assert sanitize_imposition({"startSlot": 0}) == {}
        assert sanitize_imposition({"startSlot": 7}) == {"startSlot": 7}

    def test_bools_kept_only_when_true(self):
        from app.services.label_service import sanitize_imposition
        assert sanitize_imposition({"cutMarks": True, "shrinkToFit": False}) \
            == {"cutMarks": True}

    def test_portrait_orientation_not_stored(self):
        from app.services.label_service import sanitize_imposition
        assert sanitize_imposition({"orientation": "portrait"}) == {}
        assert sanitize_imposition({"orientation": "sideways"}) == {}
        assert sanitize_imposition({"orientation": "landscape"}) \
            == {"orientation": "landscape"}

    def test_non_numeric_values_dropped(self):
        from app.services.label_service import sanitize_imposition
        assert sanitize_imposition({"marginMm": "abc", "forceCols": "x"}) == {}


class TestPersistRoundtrip:
    def test_roundtrip_per_bucket(self, iso_settings):
        from app.services.label_service import (persist_imposition,
                                                persisted_imposition)
        sample = {"marginLeftMm": 4.0, "gapXMm": 0.0, "forceCols": 4,
                  "shrinkToFit": True, "orientation": "landscape",
                  "startSlot": 3}
        persist_imposition("sample", sample)
        persist_imposition("tissue", {"marginMm": 10.0})
        assert persisted_imposition("sample") == sample
        assert persisted_imposition("tissue") == {"marginMm": 10.0}

    def test_missing_key_returns_empty(self, iso_settings):
        from app.services.label_service import persisted_imposition
        assert persisted_imposition("sample") == {}

    def test_invalid_json_returns_empty(self, iso_settings):
        import app.services.label_service as svc
        qs = svc.QSettings()
        qs.setValue(svc.LABEL_IMPOSITION_QSETTINGS_KEY["sample"], "{not json")
        qs.sync()
        assert svc.persisted_imposition("sample") == {}

    def test_persist_sanitizes(self, iso_settings):
        from app.services.label_service import (persist_imposition,
                                                persisted_imposition)
        persist_imposition("sample", {"marginMm": 999, "junk": 1})
        assert persisted_imposition("sample") == {"marginMm": 50.0}

    def test_empty_dict_removes_settings_key(self, iso_settings):
        import app.services.label_service as svc
        svc.persist_imposition("sample", {"marginMm": 9.0})
        qs = svc.QSettings()
        key = svc.LABEL_IMPOSITION_QSETTINGS_KEY["sample"]
        assert qs.contains(key)
        svc.persist_imposition("sample", {})
        qs.sync()
        assert not svc.QSettings().contains(key)
