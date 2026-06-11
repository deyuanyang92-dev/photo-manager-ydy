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
from app.config.i18n import set_language
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
    _K_HELICON_OUTPUT_FORMAT,
    _K_HELICON_TIFF_COMPRESSION,
    _K_HELICON_SAVE_DEPTH_MAP,
    _K_HELICON_RUN_MODE,
    _K_HELICON_CONCURRENCY,
    _K_INCOMING_SUBDIR,
    _K_RESULTS_SUBDIR,
    _K_RECENT_PROJECTS,
    _K_HELICON_PRESETS_JSON,
    _K_WB_AUTO_WATCH,
    _K_WB_AUTO_ACTIVATE_NEW,
    _K_WB_GROUPING_AUTO_WATCH,
    _K_WB_GROUPING_AUTO_WATCH_MODE,
    _K_WB_FILE_VIEW_MODE,
    _K_UI_FONT_FAMILY,
    _K_UI_FONT_SCALE,
    _K_UI_ICON_GPS,
    _K_UI_ICON_MAP,
    _K_UI_ICON_FOLDER,
    _K_UI_ICON_SEARCH,
    _K_DEBUG_USE_REAL_COMPRESSION,
    _K_SHORTCUT_MONITOR_ACTIVATE,
    _K_SHORTCUT_MONITOR_DEACTIVATE,
    _K_SHORTCUT_LABELS_PRINT,
    _K_SHORTCUT_LABELS_NEXT,
    _installed_font_families,
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
    set_language("zh")
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
        assert view.nav_title == "配置"

    def test_nav_icon_set(self, view: SettingsView) -> None:
        assert view.nav_icon  # non-empty

    def test_object_name_matches_view_id(self, view: SettingsView) -> None:
        assert view.objectName() == "settings"


# ── Tabs ──────────────────────────────────────────────────────────────────────

class TestTabs:
    def test_has_eight_tabs(self, view: SettingsView) -> None:
        assert view._tabs.count() == 8

    def test_tab_titles(self, view: SettingsView) -> None:
        expected = ["项目", "Helicon", "归档", "工作台", "操作人", "协作", "界面", "关于"]
        actual = [view._tabs.tabText(i) for i in range(view._tabs.count())]
        assert actual == expected


# ── Collaboration tab ─────────────────────────────────────────────────────────

class TestCollabTab:
    def test_enable_checkbox_default_off(self, view: SettingsView) -> None:
        assert view._collab_enabled_chk.isChecked() is False

    def test_team_code_edit_empty_by_default(self, view: SettingsView) -> None:
        assert view._collab_team_code_edit.text() == ""

    def test_toggling_enable_persists(self, view: SettingsView) -> None:
        view._collab_enabled_chk.setChecked(True)
        view._save_collab()
        assert view.ctx.settings.collab_enabled is True
        view.on_activate()
        assert view._collab_enabled_chk.isChecked() is True

    def test_team_code_persists(self, view: SettingsView) -> None:
        view._collab_team_code_edit.setText("SMW-2026")
        view._save_collab()
        assert view.ctx.settings.team_code == "SMW-2026"
        view.on_activate()
        assert view._collab_team_code_edit.text() == "SMW-2026"

    def test_doctor_and_helper_controls_exist(self, view: SettingsView) -> None:
        # Novice self-debug surface: diagnose, scan, pairing controls.
        assert view._collab_diagnose_btn is not None
        assert view._collab_scan_btn is not None
        assert view._collab_pairing_show_btn is not None
        assert view._collab_pairing_input is not None
        assert view._collab_health_light is not None


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
        view._helicon_param_panel.set_params({"radius": 22.5})
        view._save_helicon()
        view.on_activate()
        assert view._helicon_param_panel.get_params()["radius"] == 22.5

    def test_helicon_smoothing_round_trip(self, view: SettingsView) -> None:
        view._helicon_param_panel.set_params({"smoothing": 2})
        view._save_helicon()
        view.on_activate()
        assert view._helicon_param_panel.get_params()["smoothing"] == 2

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
        # Switch to "关于" tab (now index 6) — should not raise
        view._tabs.setCurrentIndex(6)
        assert view._tabs.currentIndex() == 6


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

    def test_helicon_presets_json_key(self) -> None:
        assert _K_HELICON_PRESETS_JSON == "helicon/presets_json"


# ── Preset CRUD ──────────────────────────────────────────────────────────────

class TestPresetCRUD:
    """Test named preset save / apply / delete (mirrors server.js /api/helicon/presets)."""

    def test_save_preset_stores_in_settings(self, view: SettingsView) -> None:
        view._preset_name_edit.setText("标准景深")
        view._helicon_param_panel.set_params({"method": 1, "radius": 22.5, "smoothing": 2})  # B
        view._quality_spin.setValue(90)
        view._save_current_as_preset()
        presets = view._load_presets()
        assert len(presets) == 1
        assert presets[0]["name"] == "标准景深"
        assert presets[0]["params"]["method"] == 2   # index+1
        assert presets[0]["params"]["radius"] == 22.5
        assert presets[0]["params"]["smoothing"] == 2
        assert presets[0]["params"]["quality"] == 90

    def test_save_preset_upserts_existing_name(self, view: SettingsView) -> None:
        """Saving with the same name should overwrite, not duplicate."""
        view._preset_name_edit.setText("my-preset")
        view._helicon_param_panel.set_params({"radius": 4})
        view._save_current_as_preset()
        view._helicon_param_panel.set_params({"radius": 8})
        view._save_current_as_preset()
        presets = view._load_presets()
        assert len(presets) == 1
        assert presets[0]["params"]["radius"] == 8

    def test_empty_preset_name_not_saved(self, view: SettingsView) -> None:
        """Empty name → silent no-op, list stays empty."""
        view._preset_name_edit.setText("")
        view._save_current_as_preset()
        assert view._load_presets() == []

    def test_apply_preset_fills_spinboxes(self, view: SettingsView) -> None:
        # First save a preset
        view._preset_name_edit.setText("应用测试预设")
        view._helicon_param_panel.set_params({"method": 2, "radius": 22.5, "smoothing": 6})  # C
        view._quality_spin.setValue(80)
        view._save_current_as_preset()

        # Reset params to defaults
        view._helicon_param_panel.set_params({"method": 0, "radius": 4, "smoothing": 4})
        view._quality_spin.setValue(95)

        # Select the preset in the list and apply
        view._preset_list.setCurrentRow(0)
        view._apply_selected_preset()

        assert view._helicon_param_panel.get_params()["method"] == 2
        assert view._helicon_param_panel.get_params()["radius"] == 22.5
        assert view._helicon_param_panel.get_params()["smoothing"] == 6
        assert view._quality_spin.value() == 80

    def test_apply_preset_double_click(self, view: SettingsView) -> None:
        """Double-clicking a list item applies the preset."""
        view._preset_name_edit.setText("双击测试")
        view._helicon_param_panel.set_params({"radius": 22.5})
        view._save_current_as_preset()

        view._helicon_param_panel.set_params({"radius": 4})  # reset
        view._preset_list.setCurrentRow(0)
        # itemDoubleClicked is connected to _apply_selected_preset; simulate via direct call
        view._apply_selected_preset()
        assert view._helicon_param_panel.get_params()["radius"] == 22.5

    def test_delete_preset_removes_from_list(self, view: SettingsView) -> None:
        view._preset_name_edit.setText("删除测试")
        view._save_current_as_preset()
        assert view._preset_list.count() == 1

        view._preset_list.setCurrentRow(0)
        view._delete_selected_preset()

        assert view._preset_list.count() == 0
        assert view._load_presets() == []

    def test_preset_list_survives_reload(self, view: SettingsView) -> None:
        """Presets persisted to QSettings survive on_activate() reload."""
        view._preset_name_edit.setText("持久化测试")
        view._helicon_param_panel.set_params({"radius": 5})
        view._save_current_as_preset()

        view.on_activate()  # reload from QSettings
        assert view._preset_list.count() == 1
        assert view._preset_list.item(0).text() == "持久化测试"

    def test_multiple_presets_in_list(self, view: SettingsView) -> None:
        """Can store and list multiple presets."""
        for name in ["预设A", "预设B", "预设C"]:
            view._preset_name_edit.setText(name)
            view._save_current_as_preset()
        assert view._preset_list.count() == 3

    def test_delete_one_of_many_presets(self, view: SettingsView) -> None:
        for name in ["第一", "第二", "第三"]:
            view._preset_name_edit.setText(name)
            view._save_current_as_preset()
        # Delete the middle one
        view._preset_list.setCurrentRow(1)
        view._delete_selected_preset()
        remaining = [p["name"] for p in view._load_presets()]
        assert "第二" not in remaining
        assert "第一" in remaining
        assert "第三" in remaining


# ── Helicon advanced output params ───────────────────────────────────────────


class TestHeliconAdvancedParams:
    """Round-trip tests for Helicon 高级输出选项 block (◐ → ✓).

    Mirrors web renderHeliconConfigModal <details>输出选项 block:
    outputFormat / tiffCompression / saveDepthMap / runMode / concurrency
    """

    def test_output_format_key(self) -> None:
        assert _K_HELICON_OUTPUT_FORMAT == "helicon/output_format"

    def test_tiff_compression_key(self) -> None:
        assert _K_HELICON_TIFF_COMPRESSION == "helicon/tiff_compression"

    def test_save_depth_map_key(self) -> None:
        assert _K_HELICON_SAVE_DEPTH_MAP == "helicon/save_depth_map"

    def test_run_mode_key(self) -> None:
        assert _K_HELICON_RUN_MODE == "helicon/run_mode"

    def test_concurrency_key(self) -> None:
        assert _K_HELICON_CONCURRENCY == "helicon/concurrency"

    def test_output_format_default_tif(self, view: SettingsView) -> None:
        """Fresh settings → output format defaults to TIF (index 0)."""
        assert view._output_format_combo.currentIndex() == 0

    def test_output_format_round_trip_jpg(self, view: SettingsView) -> None:
        view._output_format_combo.setCurrentIndex(1)  # JPG
        view._save_helicon_advanced()
        view.on_activate()
        assert view._output_format_combo.currentIndex() == 1

    def test_output_format_round_trip_tif(self, view: SettingsView) -> None:
        view._output_format_combo.setCurrentIndex(0)  # TIF
        view._save_helicon_advanced()
        view.on_activate()
        assert view._output_format_combo.currentIndex() == 0

    def test_tiff_compression_default_u(self, view: SettingsView) -> None:
        """Fresh settings → TIFF compression defaults to 'u' (index 0)."""
        assert view._tiff_compression_combo.currentIndex() == 0

    def test_tiff_compression_round_trip_lzw(self, view: SettingsView) -> None:
        view._tiff_compression_combo.setCurrentIndex(1)  # LZW
        view._save_helicon_advanced()
        view.on_activate()
        assert view._tiff_compression_combo.currentIndex() == 1

    def test_tiff_compression_round_trip_zip(self, view: SettingsView) -> None:
        view._tiff_compression_combo.setCurrentIndex(2)  # ZIP
        view._save_helicon_advanced()
        view.on_activate()
        assert view._tiff_compression_combo.currentIndex() == 2

    def test_run_mode_default_silent(self, view: SettingsView) -> None:
        """Fresh settings → runMode defaults to 'silent' (index 0)."""
        assert view._run_mode_combo.currentIndex() == 0

    def test_run_mode_round_trip_progress(self, view: SettingsView) -> None:
        view._run_mode_combo.setCurrentIndex(1)  # progress
        view._save_helicon_advanced()
        view.on_activate()
        assert view._run_mode_combo.currentIndex() == 1

    def test_run_mode_round_trip_gui(self, view: SettingsView) -> None:
        view._run_mode_combo.setCurrentIndex(2)  # gui
        view._save_helicon_advanced()
        view.on_activate()
        assert view._run_mode_combo.currentIndex() == 2

    def test_concurrency_default_1(self, view: SettingsView) -> None:
        assert view._concurrency_spin.value() == 1

    def test_concurrency_round_trip(self, view: SettingsView) -> None:
        view._concurrency_spin.setValue(4)
        view._save_helicon_advanced()
        view.on_activate()
        assert view._concurrency_spin.value() == 4

    def test_save_depth_map_default_false(self, view: SettingsView) -> None:
        assert view._save_depth_map_chk.isChecked() is False

    def test_save_depth_map_round_trip_true(self, view: SettingsView) -> None:
        view._save_depth_map_chk.setChecked(True)
        view._save_helicon_advanced()
        view.on_activate()
        assert view._save_depth_map_chk.isChecked() is True

    def test_qsettings_stores_output_format_as_string(
        self, view: SettingsView
    ) -> None:
        view._output_format_combo.setCurrentIndex(1)
        view._save_helicon_advanced()
        stored = view.ctx.settings._qs.value(_K_HELICON_OUTPUT_FORMAT, "tif")
        assert stored == "jpg"

    def test_qsettings_stores_run_mode_silent(self, view: SettingsView) -> None:
        view._run_mode_combo.setCurrentIndex(0)
        view._save_helicon_advanced()
        stored = view.ctx.settings._qs.value(_K_HELICON_RUN_MODE, "silent")
        assert stored == "silent"

    def test_qsettings_stores_tiff_compression_lzw(
        self, view: SettingsView
    ) -> None:
        view._tiff_compression_combo.setCurrentIndex(1)
        view._save_helicon_advanced()
        stored = view.ctx.settings._qs.value(_K_HELICON_TIFF_COMPRESSION, "u")
        assert stored == "lzw"


# ── Workbench auto-watch toggles ──────────────────────────────────────────────


class TestWorkbenchToggles:
    """Round-trip tests for 工作台 tab (mirrors saveV4Settings / loadV4Settings)."""

    def test_wb_auto_watch_key(self) -> None:
        assert _K_WB_AUTO_WATCH == "workbench/auto_watch"

    def test_wb_auto_activate_key(self) -> None:
        assert _K_WB_AUTO_ACTIVATE_NEW == "workbench/auto_activate_new"

    def test_wb_grouping_auto_watch_key(self) -> None:
        assert _K_WB_GROUPING_AUTO_WATCH == "workbench/grouping_auto_watch"

    def test_wb_grouping_mode_key(self) -> None:
        assert _K_WB_GROUPING_AUTO_WATCH_MODE == "workbench/grouping_auto_watch_mode"

    def test_wb_file_view_mode_key(self) -> None:
        assert _K_WB_FILE_VIEW_MODE == "workbench/file_view_mode"

    def test_auto_watch_default_true(self, view: SettingsView) -> None:
        """autoWatch web default = true."""
        assert view._auto_watch_chk.isChecked() is True

    def test_auto_activate_new_default_false(self, view: SettingsView) -> None:
        """autoActivateOnNewSpecimen web default = false."""
        assert view._auto_activate_new_chk.isChecked() is False

    def test_grouping_auto_watch_default_false(self, view: SettingsView) -> None:
        assert view._grouping_auto_watch_chk.isChecked() is False

    def test_grouping_mode_default_compose_organize(
        self, view: SettingsView
    ) -> None:
        """web default groupingAutoWatchMode = 'compose+organize' (index 2)."""
        assert view._grouping_mode_combo.currentIndex() == 2

    def test_file_view_mode_default_jpg_tif(self, view: SettingsView) -> None:
        """web default fileViewMode = 'jpg-tif' (index 0)."""
        assert view._file_view_mode_combo.currentIndex() == 0

    def test_auto_watch_round_trip_false(self, view: SettingsView) -> None:
        view._auto_watch_chk.setChecked(False)
        view._save_workbench()
        view.on_activate()
        assert view._auto_watch_chk.isChecked() is False

    def test_auto_activate_round_trip_true(self, view: SettingsView) -> None:
        view._auto_activate_new_chk.setChecked(True)
        view._save_workbench()
        view.on_activate()
        assert view._auto_activate_new_chk.isChecked() is True

    def test_grouping_auto_watch_round_trip_true(
        self, view: SettingsView
    ) -> None:
        view._grouping_auto_watch_chk.setChecked(True)
        view._save_workbench()
        view.on_activate()
        assert view._grouping_auto_watch_chk.isChecked() is True

    def test_grouping_mode_round_trip_compose(self, view: SettingsView) -> None:
        view._grouping_mode_combo.setCurrentIndex(0)  # compose
        view._save_workbench()
        view.on_activate()
        assert view._grouping_mode_combo.currentIndex() == 0

    def test_grouping_mode_round_trip_organize(self, view: SettingsView) -> None:
        view._grouping_mode_combo.setCurrentIndex(1)  # organize
        view._save_workbench()
        view.on_activate()
        assert view._grouping_mode_combo.currentIndex() == 1

    def test_file_view_mode_round_trip_with_zip(self, view: SettingsView) -> None:
        view._file_view_mode_combo.setCurrentIndex(1)  # with-zip
        view._save_workbench()
        view.on_activate()
        assert view._file_view_mode_combo.currentIndex() == 1

    def test_file_view_mode_round_trip_all(self, view: SettingsView) -> None:
        view._file_view_mode_combo.setCurrentIndex(2)  # all
        view._save_workbench()
        view.on_activate()
        assert view._file_view_mode_combo.currentIndex() == 2

    def test_qsettings_stores_auto_watch_false(self, view: SettingsView) -> None:
        view._auto_watch_chk.setChecked(False)
        view._save_workbench()
        stored = view.ctx.settings._qs.value(_K_WB_AUTO_WATCH, "true")
        assert stored == "false"

    def test_qsettings_stores_grouping_mode_string(
        self, view: SettingsView
    ) -> None:
        view._grouping_mode_combo.setCurrentIndex(0)  # compose
        view._save_workbench()
        stored = view.ctx.settings._qs.value(
            _K_WB_GROUPING_AUTO_WATCH_MODE, "compose+organize"
        )
        assert stored == "compose"

    def test_qsettings_stores_file_view_mode_all(
        self, view: SettingsView
    ) -> None:
        view._file_view_mode_combo.setCurrentIndex(2)  # all
        view._save_workbench()
        stored = view.ctx.settings._qs.value(_K_WB_FILE_VIEW_MODE, "jpg-tif")
        assert stored == "all"


# ── 界面 tab: Global UI settings ─────────────────────────────────────────────


class TestUISettings:
    """Round-trip and key tests for renderGlobalSettings() equivalent (界面 tab).

    Covers: fontScale slider, icon emoji fields, useRealCompression debug switch,
    and keyboard shortcut QKeySequenceEdit recording.
    """

    def test_ui_font_scale_key(self) -> None:
        assert _K_UI_FONT_SCALE == "ui/font_scale"

    def test_ui_font_family_key(self) -> None:
        assert _K_UI_FONT_FAMILY == "ui/font_family"

    def test_ui_icon_gps_key(self) -> None:
        assert _K_UI_ICON_GPS == "ui/icon_gps"

    def test_ui_icon_map_key(self) -> None:
        assert _K_UI_ICON_MAP == "ui/icon_map"

    def test_ui_icon_folder_key(self) -> None:
        assert _K_UI_ICON_FOLDER == "ui/icon_folder"

    def test_ui_icon_search_key(self) -> None:
        assert _K_UI_ICON_SEARCH == "ui/icon_search"

    def test_debug_use_real_compression_key(self) -> None:
        assert _K_DEBUG_USE_REAL_COMPRESSION == "debug/use_real_compression"

    def test_shortcut_monitor_activate_key(self) -> None:
        assert _K_SHORTCUT_MONITOR_ACTIVATE == "shortcuts/monitor_activate"

    def test_shortcut_labels_print_key(self) -> None:
        assert _K_SHORTCUT_LABELS_PRINT == "shortcuts/labels_print"

    def test_font_scale_default_1(self, view: SettingsView) -> None:
        """Fresh settings → fontScale defaults to 1.0."""
        assert view._font_scale_spin.value() == pytest.approx(1.0, abs=0.01)

    def test_font_scale_round_trip(self, view: SettingsView) -> None:
        view._font_scale_spin.setValue(1.2)
        view._save_ui()
        view.on_activate()
        assert view._font_scale_spin.value() == pytest.approx(1.2, abs=0.01)

    def test_font_scale_min_clamp(self, view: SettingsView) -> None:
        """Values below 0.7 are clamped on load."""
        view.ctx.settings._qs.setValue(_K_UI_FONT_SCALE, 0.3)
        view.on_activate()
        assert view._font_scale_spin.value() >= 0.7

    def test_font_scale_max_clamp(self, view: SettingsView) -> None:
        view.ctx.settings._qs.setValue(_K_UI_FONT_SCALE, 9.9)
        view.on_activate()
        assert view._font_scale_spin.value() <= 1.5

    def test_font_scale_pct_label_updates(self, view: SettingsView) -> None:
        """Percentage label shows rounded integer."""
        view._font_scale_spin.setValue(1.1)
        view._on_font_scale_changed(1.1)
        assert "110%" in view._font_scale_pct_label.text()

    def test_font_picker_surfaces_common_cjk_and_latin_faces(
        self, view: SettingsView
    ) -> None:
        values = {
            view._font_family_combo.itemData(i)
            for i in range(view._font_family_combo.count())
        }

        assert {"SimSun", "宋体", "Microsoft YaHei", "Times New Roman"} <= values

    def test_font_family_round_trip(self, view: SettingsView) -> None:
        idx = view._font_family_combo.findData("Times New Roman")
        assert idx >= 0

        view._font_family_combo.setCurrentIndex(idx)
        view._save_ui()
        view._font_family_combo.blockSignals(True)
        view._font_family_combo.setCurrentIndex(0)
        view._font_family_combo.blockSignals(False)
        view.on_activate()

        assert view._font_family_combo.currentData() == "Times New Roman"

    def test_font_picker_list_contains_common_faces_without_qt_enumeration(
        self,
    ) -> None:
        families = _installed_font_families()

        assert families[:4] == ["Microsoft YaHei", "微软雅黑", "SimSun", "宋体"]

    def test_icon_gps_round_trip(self, view: SettingsView) -> None:
        view._icon_gps_edit.setText("🛰")
        view._save_ui()
        view.on_activate()
        assert view._icon_gps_edit.text() == "🛰"

    def test_icon_map_round_trip(self, view: SettingsView) -> None:
        view._icon_map_edit.setText("🗺")
        view._save_ui()
        view.on_activate()
        assert view._icon_map_edit.text() == "🗺"

    def test_icon_folder_round_trip(self, view: SettingsView) -> None:
        view._icon_folder_edit.setText("🗂")
        view._save_ui()
        view.on_activate()
        assert view._icon_folder_edit.text() == "🗂"

    def test_icon_search_round_trip(self, view: SettingsView) -> None:
        view._icon_search_edit.setText("🔎")
        view._save_ui()
        view.on_activate()
        assert view._icon_search_edit.text() == "🔎"

    def test_use_real_compression_default_false(self, view: SettingsView) -> None:
        """useRealCompression defaults to False — debug switch off by default."""
        assert view._use_real_compression_chk.isChecked() is False

    def test_use_real_compression_round_trip_true(self, view: SettingsView) -> None:
        view._use_real_compression_chk.setChecked(True)
        view._save_ui()
        view.on_activate()
        assert view._use_real_compression_chk.isChecked() is True

    def test_use_real_compression_round_trip_false(self, view: SettingsView) -> None:
        view._use_real_compression_chk.setChecked(True)
        view._save_ui()
        view._use_real_compression_chk.setChecked(False)
        view._save_ui()
        view.on_activate()
        assert view._use_real_compression_chk.isChecked() is False

    def test_qsettings_stores_use_real_compression_true(
        self, view: SettingsView
    ) -> None:
        view._use_real_compression_chk.setChecked(True)
        view._save_ui()
        stored = view.ctx.settings._qs.value(_K_DEBUG_USE_REAL_COMPRESSION, "false")
        assert stored == "true"

    def test_shortcut_monitor_activate_round_trip(self, view: SettingsView) -> None:
        from PyQt6.QtGui import QKeySequence
        view._sc_monitor_activate.setKeySequence(QKeySequence("Ctrl+A"))
        view._save_ui()
        view.on_activate()
        ks = view._sc_monitor_activate.keySequence()
        assert ks.toString() == "Ctrl+A"

    def test_shortcut_empty_on_fresh_settings(self, view: SettingsView) -> None:
        """Fresh settings → all shortcut widgets start empty."""
        assert view._sc_monitor_activate.keySequence().isEmpty()
        assert view._sc_labels_print.keySequence().isEmpty()

    def test_ui_tab_accessible(self, view: SettingsView) -> None:
        """Switching to 界面 tab (index 5) should not raise."""
        view._tabs.setCurrentIndex(5)
        assert view._tabs.currentIndex() == 5

    def test_theme_change_rebuilds_settings_local_styles(
        self, view: SettingsView
    ) -> None:
        """Changing theme refreshes SettingsView inline styles, not just app QSS."""
        view._tabs.setCurrentIndex(6)
        idx = view._theme_combo.findData("graphite_focus")
        assert idx >= 0

        view._theme_combo.setCurrentIndex(idx)

        assert view.ctx.settings.current_theme == "graphite_focus"
        assert "#111827" in view._tabs.styleSheet()
        assert view._tabs.currentIndex() == 6

    def test_language_change_rebuilds_ui_tab_immediately(
        self, view: SettingsView
    ) -> None:
        view._tabs.setCurrentIndex(6)
        idx = view._lang_combo.findData("en")
        assert idx >= 0

        view._lang_combo.setCurrentIndex(idx)

        assert view.ctx.settings.current_language == "en"
        assert view._tabs.currentIndex() == 6
        assert [view._tabs.tabText(i) for i in range(view._tabs.count())] == [
            "Project",
            "Helicon",
            "Archive",
            "Workbench",
            "Operator",
            "Collaboration",
            "Interface",
            "About",
        ]
        assert view._lang_combo.toolTip() == "Language changes take effect immediately."
        assert view._theme_combo.itemText(0) == "Current style"


# ── Named round-trip tests required by task spec 1-H / 1-I ───────────────────


class TestTaskSpecRoundTrips:
    """Exact test names required by the 1-H / 1-I task spec."""

    def test_helicon_advanced_tiff_compression_roundtrip(
        self, view: SettingsView
    ) -> None:
        view._tiff_compression_combo.setCurrentIndex(1)  # LZW
        view._save_helicon_advanced()
        view.on_activate()
        assert view._tiff_compression_combo.currentIndex() == 1

        view._tiff_compression_combo.setCurrentIndex(2)  # ZIP
        view._save_helicon_advanced()
        view.on_activate()
        assert view._tiff_compression_combo.currentIndex() == 2

        stored = view.ctx.settings._qs.value(_K_HELICON_TIFF_COMPRESSION, "u")
        assert stored == "zip"

    def test_helicon_advanced_save_depth_map_roundtrip(
        self, view: SettingsView
    ) -> None:
        view._save_depth_map_chk.setChecked(True)
        view._save_helicon_advanced()
        view.on_activate()
        assert view._save_depth_map_chk.isChecked() is True

        stored = view.ctx.settings._qs.value(_K_HELICON_SAVE_DEPTH_MAP, "false")
        assert stored == "true"

    def test_helicon_advanced_concurrency_roundtrip(
        self, view: SettingsView
    ) -> None:
        view._concurrency_spin.setValue(4)
        view._save_helicon_advanced()
        view.on_activate()
        assert view._concurrency_spin.value() == 4

        stored = int(view.ctx.settings._qs.value(_K_HELICON_CONCURRENCY, 1))
        assert stored == 4

    def test_workbench_auto_watch_roundtrip(self, view: SettingsView) -> None:
        view._auto_watch_chk.setChecked(False)
        view._save_workbench()
        view.on_activate()
        assert view._auto_watch_chk.isChecked() is False

        stored = view.ctx.settings._qs.value(_K_WB_AUTO_WATCH, "true")
        assert stored == "false"

    def test_workbench_file_view_mode_roundtrip(self, view: SettingsView) -> None:
        view._file_view_mode_combo.setCurrentIndex(1)  # with-zip
        view._save_workbench()
        view.on_activate()
        assert view._file_view_mode_combo.currentIndex() == 1

        view._file_view_mode_combo.setCurrentIndex(2)  # all
        view._save_workbench()
        view.on_activate()
        assert view._file_view_mode_combo.currentIndex() == 2

        stored = view.ctx.settings._qs.value(_K_WB_FILE_VIEW_MODE, "jpg-tif")
        assert stored == "all"


# ── Helicon 配置按钮可见反馈（镜像 40ce2c9 对话框同病修复）────────────────────
# 「保存」成功只闪底部状态栏：路径/标签均不变时按钮附近纹丝不动 → 用户读作
# "无响应死按钮"。修复 = 按钮文字瞬时确认（已保存 ✓），1.5s 后恢复。

class TestHeliconButtonFlash:
    def test_save_click_flashes_button(self, view):
        view._helicon_exe_edit.setText("/tmp/HeliconFocus.exe")
        view._save_btn.click()
        assert view._save_btn.text() == "已保存 ✓"
        assert view._save_btn.property("_flashing") is True

    def test_flash_restores_after_timeout(self, view, qtbot):
        view._save_btn.click()
        qtbot.waitUntil(lambda: view._save_btn.text() == "保存", timeout=3000)
        assert not view._save_btn.property("_flashing")

    def test_clear_click_flashes_button(self, view, monkeypatch):
        monkeypatch.setattr(view, "_detect_helicon", lambda *a, **k: None)
        view._clear_btn.click()
        assert view._clear_btn.text() == "已清除 ✓"

    def test_redetect_click_flashes_button(self, view, monkeypatch):
        monkeypatch.setattr(view, "_detect_helicon", lambda *a, **k: None)
        view._refresh_btn.click()
        assert view._refresh_btn.text() == "已重新探测 ✓"

    def test_test_click_flashes_button(self, view, monkeypatch):
        monkeypatch.setattr(view, "_detect_helicon", lambda *a, **k: None)
        view._test_btn.click()
        assert view._test_btn.text() == "已检测 ✓"

    def test_programmatic_detect_does_not_flash_redetect(self, view, monkeypatch):
        """on_activate 等程序化探测不得闪「重新探测」按钮。"""
        monkeypatch.setattr(
            "app.services.helicon_service.detect_helicon", lambda **k: None
        )
        view._detect_helicon()
        assert view._refresh_btn.text() == "重新探测"
