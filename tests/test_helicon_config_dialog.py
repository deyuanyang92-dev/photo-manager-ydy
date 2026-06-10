"""tests/test_helicon_config_dialog.py — HeliconConfigDialog unit tests.

The top-bar "Helicon" button opens a standalone config dialog mirroring the web
oracle's renderHeliconConfigModal() (app.js:7029-7368): path config + synthesis
params (method/radius/smoothing) + advanced output + live CLI preview + save/reset.

Covers:
  - dialog constructs headless without a project
  - path-status text follows detect_helicon()
  - CLI preview is derived from helicon_service.build_helicon_args (no hand-rolled
    forbidden flags; reflects current method/radius/smoothing)
  - 保存为默认 persists params to the same QSettings keys settings_view uses
"""
from __future__ import annotations

import types
import unittest.mock as mock
from pathlib import Path

import pytest
from PyQt6.QtCore import QSettings


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_ctx(tmp_path: Path):
    """ctx exposing settings._qs backed by a throwaway ini file."""
    ini = str(tmp_path / "helicon_test.ini")
    qs = QSettings(ini, QSettings.Format.IniFormat)
    settings = types.SimpleNamespace(_qs=qs, sync=qs.sync)
    return types.SimpleNamespace(
        settings=settings,
        current_project_dir=None,
        has_project=False,
    )


# ── Construction ───────────────────────────────────────────────────────────────

class TestConstruction:
    def test_dialog_constructs(self, qtbot, tmp_path):
        from app.widgets.helicon_config_dialog import HeliconConfigDialog
        ctx = _make_ctx(tmp_path)
        with mock.patch("app.services.helicon_service.detect_helicon", return_value=None):
            dlg = HeliconConfigDialog(ctx)
        qtbot.addWidget(dlg)
        assert dlg.windowTitle()  # has a title
        # Core sections present
        assert hasattr(dlg, "_path_edit")
        assert hasattr(dlg, "_params")          # embedded HeliconParamsPanel
        assert hasattr(dlg, "_cli_preview")
        assert hasattr(dlg, "_save_btn")
        assert hasattr(dlg, "_reset_btn")

    def test_path_status_not_detected(self, qtbot, tmp_path):
        from app.widgets.helicon_config_dialog import HeliconConfigDialog
        ctx = _make_ctx(tmp_path)
        with mock.patch("app.services.helicon_service.detect_helicon", return_value=None):
            dlg = HeliconConfigDialog(ctx)
        qtbot.addWidget(dlg)
        assert "未检测" in dlg._status_label.text()

    def test_path_status_detected(self, qtbot, tmp_path):
        from app.widgets.helicon_config_dialog import HeliconConfigDialog
        ctx = _make_ctx(tmp_path)
        fake_exe = r"C:\Program Files\Helicon\HeliconFocus.exe"
        with mock.patch("app.services.helicon_service.detect_helicon", return_value=fake_exe):
            dlg = HeliconConfigDialog(ctx)
        qtbot.addWidget(dlg)
        assert "已检测" in dlg._status_label.text() or "✓" in dlg._status_label.text()


# ── CLI preview ────────────────────────────────────────────────────────────────

class TestCliPreview:
    def test_preview_reflects_params(self, qtbot, tmp_path):
        from app.widgets.helicon_config_dialog import HeliconConfigDialog
        ctx = _make_ctx(tmp_path)
        with mock.patch("app.services.helicon_service.detect_helicon", return_value=None):
            dlg = HeliconConfigDialog(ctx)
        qtbot.addWidget(dlg)
        dlg._params.set_params({"method": 1, "radius": 6.0, "smoothing": 3})
        dlg._refresh_cli_preview()
        text = dlg._cli_preview.toPlainText() if hasattr(dlg._cli_preview, "toPlainText") \
            else dlg._cli_preview.text()
        assert "-mp:1" in text
        assert "-rp:6" in text
        assert "-sp:3" in text

    def test_preview_has_no_forbidden_cjxl_flags(self, qtbot, tmp_path):
        # Red line: Helicon CLI must never carry cjxl flags.
        from app.widgets.helicon_config_dialog import HeliconConfigDialog
        ctx = _make_ctx(tmp_path)
        with mock.patch("app.services.helicon_service.detect_helicon", return_value=None):
            dlg = HeliconConfigDialog(ctx)
        qtbot.addWidget(dlg)
        dlg._refresh_cli_preview()
        text = dlg._cli_preview.toPlainText() if hasattr(dlg._cli_preview, "toPlainText") \
            else dlg._cli_preview.text()
        for forbidden in ("--quality", "--modular", "--distance"):
            assert forbidden not in text


# ── Persistence ────────────────────────────────────────────────────────────────

class TestPersistence:
    def test_save_writes_qsettings(self, qtbot, tmp_path):
        from app.widgets.helicon_config_dialog import HeliconConfigDialog
        from app.views.settings_view import (
            _K_HELICON_METHOD, _K_HELICON_RADIUS, _K_HELICON_SMOOTHING,
        )
        ctx = _make_ctx(tmp_path)
        with mock.patch("app.services.helicon_service.detect_helicon", return_value=None):
            dlg = HeliconConfigDialog(ctx)
        qtbot.addWidget(dlg)
        dlg._params.set_params({"method": 2, "radius": 7.0, "smoothing": 5})
        dlg._on_save_defaults()
        qs = ctx.settings._qs
        assert int(qs.value(_K_HELICON_METHOD)) == 2
        assert int(float(qs.value(_K_HELICON_RADIUS))) == 7
        assert int(qs.value(_K_HELICON_SMOOTHING)) == 5

    def test_reset_restores_oracle_defaults(self, qtbot, tmp_path):
        from app.widgets.helicon_config_dialog import HeliconConfigDialog
        ctx = _make_ctx(tmp_path)
        with mock.patch("app.services.helicon_service.detect_helicon", return_value=None):
            dlg = HeliconConfigDialog(ctx)
        qtbot.addWidget(dlg)
        dlg._params.set_params({"method": 0, "radius": 1.0, "smoothing": 1})
        dlg._on_reset()
        p = dlg._params.get_params()
        # Oracle defaults: method B (1), radius 8, smoothing 4
        assert p["method"] == 1
        assert int(p["radius"]) == 8
        assert p["smoothing"] == 4


# ── Button feedback ────────────────────────────────────────────────────────────
# When detection state doesn't change, 重新探测/清除自定义/保存… used to update
# NOTHING visible — users read that as a dead button ("点击没有响应"). Every
# action button must flash a transient confirmation, then restore its label.

class TestButtonFeedback:
    def _dlg(self, qtbot, tmp_path):
        from app.widgets.helicon_config_dialog import HeliconConfigDialog
        ctx = _make_ctx(tmp_path)
        fake_exe = r"C:\Program Files\Helicon\HeliconFocus.exe"
        with mock.patch("app.services.helicon_service.detect_helicon", return_value=fake_exe):
            dlg = HeliconConfigDialog(ctx)
        qtbot.addWidget(dlg)
        return dlg

    def _assert_flash(self, qtbot, btn, expect_text, trigger):
        orig = btn.text()
        trigger()
        assert expect_text in btn.text(), f"no visible feedback on {orig!r}"
        qtbot.waitUntil(lambda: btn.text() == orig, timeout=4000)

    def test_redetect_flashes(self, qtbot, tmp_path):
        dlg = self._dlg(qtbot, tmp_path)
        with mock.patch("app.services.helicon_service.detect_helicon",
                        return_value=r"C:\x\HeliconFocus.exe"):
            self._assert_flash(qtbot, dlg._redetect_btn, "已重新探测",
                               dlg._redetect_btn.click)

    def test_clear_flashes(self, qtbot, tmp_path):
        dlg = self._dlg(qtbot, tmp_path)
        with mock.patch("app.services.helicon_service.detect_helicon",
                        return_value=None):
            self._assert_flash(qtbot, dlg._clear_btn, "已清除",
                               dlg._clear_btn.click)

    def test_save_path_flashes(self, qtbot, tmp_path):
        dlg = self._dlg(qtbot, tmp_path)
        with mock.patch("app.services.helicon_service.detect_helicon",
                        return_value=None):
            self._assert_flash(qtbot, dlg._save_path_btn, "已保存",
                               dlg._save_path_btn.click)

    def test_save_defaults_flashes(self, qtbot, tmp_path):
        dlg = self._dlg(qtbot, tmp_path)
        self._assert_flash(qtbot, dlg._save_btn, "已保存", dlg._save_btn.click)
