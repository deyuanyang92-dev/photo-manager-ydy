"""test_settings_view.py — Smoke tests for SettingsView.

Runs offscreen (QT_QPA_PLATFORM=offscreen).

Checks:
- SettingsView instantiates and paints without crashing.
- Five tabs are present with correct titles.
- delete_jpg checkbox defaults to False (hard rule — TIFF永远保留, JPG删除默认关).
- Settings round-trip: write values → on_activate() reload → values persisted.
- QSettings keys match expected namespace.
"""

from __future__ import annotations

import os
import pytest

# ── Ensure offscreen platform is set before Qt is imported ───────────────────
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QCheckBox
from PyQt6.QtCore import QSettings

from app.app_context import AppContext
from app.views.settings_view import (
    SettingsView,
    APP_VERSION,
    _K_DELETE_JPG,
    _K_CURRENT_USER,
    _K_JXL_EFFORT,
    _K_HELICON_EXE,
    _K_HELICON_METHOD,
    _K_HELICON_RADIUS,
    _K_HELICON_SMOOTHING,
    _K_HELICON_QUALITY,
    _K_INCOMING_SUBDIR,
    _K_RESULTS_SUBDIR,
    _K_RECENT_PROJECTS,
)


# ── QApplication singleton (module-level for the whole test session) ──────────

_qapp: QApplication | None = None


def _get_app() -> QApplication:
    global _qapp
    if _qapp is None:
        _qapp = QApplication.instance() or QApplication([])
    return _qapp


@pytest.fixture()
def ctx() -> AppContext:
    """Fresh AppContext using an in-memory QSettings (unique org/app per test)."""
    _get_app()
    ctx = AppContext()
    # Wipe any persisted keys from prior test runs in this process
    ctx.settings._qs.clear()
    ctx.settings._qs.sync()
    return ctx


@pytest.fixture()
def view(ctx: AppContext) -> SettingsView:
    """Instantiated SettingsView (offscreen, never shown)."""
    v = SettingsView(ctx)
    # on_activate is called by MainWindow on navigation; simulate it here
    v.on_activate()
    return v


# ── Smoke: instantiation ──────────────────────────────────────────────────────

class TestInstantiation:
    def test_view_creates_without_error(self, ctx: AppContext) -> None:
        view = SettingsView(ctx)
        assert view is not None

    def test_view_id_correct(self, view: SettingsView) -> None:
        assert view.view_id == "settings"

    def test_nav_title_correct(self, view: SettingsView) -> None:
        assert view.nav_title == "全局设置"

    def test_nav_icon_set(self, view: SettingsView) -> None:
        assert view.nav_icon  # non-empty

    def test_object_name_matches_view_id(self, view: SettingsView) -> None:
        assert view.objectName() == "settings"


# ── Tabs ──────────────────────────────────────────────────────────────────────

class TestTabs:
    def test_has_five_tabs(self, view: SettingsView) -> None:
        assert view._tabs.count() == 5

    def test_tab_titles(self, view: SettingsView) -> None:
        expected = ["项目", "Helicon", "归档", "操作人", "关于"]
        actual = [view._tabs.tabText(i) for i in range(view._tabs.count())]
        assert actual == expected


# ── Hard rule: delete_jpg default = False ────────────────────────────────────

class TestDeleteJpgDefault:
    """Red-line invariant: delete_jpg MUST default to False."""

    def test_delete_jpg_checkbox_unchecked_by_default(
        self, view: SettingsView
    ) -> None:
        """Fresh settings → delete_jpg checkbox is OFF (False)."""
        assert view._delete_jpg_chk.isChecked() is False

    def test_delete_jpg_checkbox_is_qcheckbox(self, view: SettingsView) -> None:
        assert isinstance(view._delete_jpg_chk, QCheckBox)

    def test_qsettings_delete_jpg_default_not_true(self, ctx: AppContext) -> None:
        """QSettings should not have 'true' stored for delete_jpg without user action."""
        raw = ctx.settings._qs.value(_K_DELETE_JPG, "false")
        assert str(raw).lower() != "true"

    def test_delete_jpg_not_set_on_fresh_load(self, view: SettingsView) -> None:
        """on_activate() with no prior saved value → checkbox stays OFF."""
        view.ctx.settings._qs.remove(_K_DELETE_JPG)
        view.on_activate()
        assert view._delete_jpg_chk.isChecked() is False

    def test_delete_jpg_explicit_false_survives_reload(
        self, view: SettingsView
    ) -> None:
        """Saving False and reloading yields False."""
        view.ctx.settings._qs.setValue(_K_DELETE_JPG, "false")
        view.on_activate()
        assert view._delete_jpg_chk.isChecked() is False

    def test_delete_jpg_can_be_toggled_to_true(self, view: SettingsView) -> None:
        """Checkbox CAN be enabled (user must be able to opt-in); just not by default."""
        view._delete_jpg_chk.setChecked(True)
        view._save_archive()
        stored = view.ctx.settings._qs.value(_K_DELETE_JPG, "false")
        assert str(stored).lower() == "true"
        # But reloading after user explicitly set it stays True
        view.on_activate()
        assert view._delete_jpg_chk.isChecked() is True

    def test_delete_jpg_can_be_toggled_back_to_false(
        self, view: SettingsView
    ) -> None:
        view._delete_jpg_chk.setChecked(True)
        view._save_archive()
        view._delete_jpg_chk.setChecked(False)
        view._save_archive()
        view.on_activate()
        assert view._delete_jpg_chk.isChecked() is False


# ── Settings round-trip ───────────────────────────────────────────────────────

class TestRoundTrip:
    def test_current_user_round_trip(self, view: SettingsView) -> None:
        view._current_user_edit.setText("李明")
        view._save_user()
        view.on_activate()
        assert view._current_user_edit.text() == "李明"

    def test_helicon_exe_round_trip(self, view: SettingsView) -> None:
        view._helicon_exe_edit.setText("/mnt/i/Helicon Focus 8/HeliconFocus.exe")
        view._save_helicon()
        view.on_activate()
        assert "HeliconFocus" in view._helicon_exe_edit.text()

    def test_jxl_effort_round_trip(self, view: SettingsView) -> None:
        view._jxl_effort_combo.setCurrentIndex(1)  # maximum
        view._save_archive()
        view.on_activate()
        assert view._jxl_effort_combo.currentIndex() == 1

    def test_helicon_radius_round_trip(self, view: SettingsView) -> None:
        view._radius_spin.setValue(8)
        view._save_helicon()
        view.on_activate()
        assert view._radius_spin.value() == 8

    def test_helicon_smoothing_round_trip(self, view: SettingsView) -> None:
        view._smoothing_spin.setValue(2)
        view._save_helicon()
        view.on_activate()
        assert view._smoothing_spin.value() == 2

    def test_helicon_quality_round_trip(self, view: SettingsView) -> None:
        view._quality_spin.setValue(90)
        view._save_helicon()
        view.on_activate()
        assert view._quality_spin.value() == 90

    def test_incoming_subdir_round_trip(self, view: SettingsView) -> None:
        view._incoming_edit.setText("raw-photos")
        view._save_project()
        view.on_activate()
        assert view._incoming_edit.text() == "raw-photos"

    def test_results_subdir_round_trip(self, view: SettingsView) -> None:
        view._results_edit.setText("output")
        view._save_project()
        view.on_activate()
        assert view._results_edit.text() == "output"


# ── Recent projects ───────────────────────────────────────────────────────────

class TestRecentProjects:
    def test_add_to_recent_shows_in_list(self, view: SettingsView) -> None:
        view._add_to_recent("/mnt/n/projects/specimen_A")
        assert view._recent_list.count() == 1
        assert view._recent_list.item(0).text() == "/mnt/n/projects/specimen_A"

    def test_recent_deduplicates(self, view: SettingsView) -> None:
        view._add_to_recent("/mnt/n/proj/A")
        view._add_to_recent("/mnt/n/proj/A")
        assert view._recent_list.count() == 1

    def test_recent_moves_to_front(self, view: SettingsView) -> None:
        view._add_to_recent("/mnt/n/proj/A")
        view._add_to_recent("/mnt/n/proj/B")
        view._add_to_recent("/mnt/n/proj/A")
        assert view._recent_list.item(0).text() == "/mnt/n/proj/A"

    def test_clear_recent_empties_list(self, view: SettingsView) -> None:
        view._add_to_recent("/mnt/n/proj/X")
        view._clear_recent()
        assert view._recent_list.count() == 0


# ── About tab ────────────────────────────────────────────────────────────────

class TestAboutTab:
    def test_app_version_constant_nonempty(self) -> None:
        assert APP_VERSION
        assert APP_VERSION != ""

    def test_about_tab_accessible(self, view: SettingsView) -> None:
        # Switch to "关于" tab (index 4) — should not raise
        view._tabs.setCurrentIndex(4)
        assert view._tabs.currentIndex() == 4


# ── QSettings key namespaces ─────────────────────────────────────────────────

class TestSettingsKeys:
    """Verify key strings follow section/key convention."""

    def test_delete_jpg_key(self) -> None:
        assert _K_DELETE_JPG == "archive/delete_jpg"

    def test_current_user_key(self) -> None:
        assert _K_CURRENT_USER == "user/current_user"

    def test_jxl_effort_key(self) -> None:
        assert _K_JXL_EFFORT == "archive/jxl_effort"

    def test_helicon_exe_key(self) -> None:
        assert _K_HELICON_EXE == "helicon/exe_path"

    def test_incoming_subdir_key(self) -> None:
        assert _K_INCOMING_SUBDIR == "project/incoming_subdir"

    def test_results_subdir_key(self) -> None:
        assert _K_RESULTS_SUBDIR == "project/results_subdir"
