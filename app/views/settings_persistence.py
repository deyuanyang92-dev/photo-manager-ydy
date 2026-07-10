"""QSettings load/save behaviour for the settings view."""
from __future__ import annotations

import os
import platform

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.config.i18n import tr
from app.config.version import APP_VERSION
from app.services.collab_status import build_collab_status
from app.utils import diagnostics
from app.views import settings_view_support as _sv


class SettingsPersistenceMixin:
    """QSettings load/save behaviour for the settings view."""

    def _load_all(self) -> None:
        """Read all settings from QSettings and populate widgets."""
        qs = self.ctx.settings._qs  # direct QSettings access

        # Helicon tab — exe path + preset params
        exe_val = qs.value(_sv._K_HELICON_EXE, "")
        self._helicon_exe_edit.setText(exe_val)
        if exe_val:
            os.environ["HELICON_FOCUS_PATH"] = exe_val
        method_idx = int(qs.value(_sv._K_HELICON_METHOD, 1))
        self._helicon_param_panel.set_params({
            "method": method_idx if 0 <= method_idx <= 2 else 1,
            "radius": float(qs.value(_sv._K_HELICON_RADIUS, 8.0)),
            "smoothing": int(qs.value(_sv._K_HELICON_SMOOTHING, 4)),
        })
        self._quality_spin.setValue(int(qs.value(_sv._K_HELICON_QUALITY, 95)))
        # Refresh detected / effective display
        self._refresh_helicon_display()

        # Helicon advanced params
        fmt = qs.value(_sv._K_HELICON_OUTPUT_FORMAT, "tif")
        fmt_idx = 1 if fmt == "jpg" else 0
        self._output_format_combo.setCurrentIndex(fmt_idx)
        tiff_map = {"u": 0, "lzw": 1, "zip": 2}
        tiff_val = str(qs.value(_sv._K_HELICON_TIFF_COMPRESSION, "u"))
        self._tiff_compression_combo.setCurrentIndex(tiff_map.get(tiff_val, 0))
        run_map = {"silent": 0, "progress": 1, "gui": 2}
        run_val = str(qs.value(_sv._K_HELICON_RUN_MODE, "silent"))
        self._run_mode_combo.setCurrentIndex(run_map.get(run_val, 0))
        self._concurrency_spin.setValue(
            max(1, min(8, int(qs.value(_sv._K_HELICON_CONCURRENCY, 1))))
        )
        raw_depth = qs.value(_sv._K_HELICON_SAVE_DEPTH_MAP, "false")
        self._save_depth_map_chk.setChecked(str(raw_depth).lower() == "true")

        # Archive tab
        effort_idx = int(qs.value(_sv._K_JXL_EFFORT, 0))
        self._jxl_effort_combo.setCurrentIndex(
            effort_idx if 0 <= effort_idx < self._jxl_effort_combo.count() else 0
        )
        self._jxl_concurrency_spin.setValue(
            max(1, min(8, int(qs.value(_sv._K_JXL_CONCURRENCY, 4))))
        )
        # delete_jpg: product default True; safety service may still refuse deletion.
        raw_del = qs.value(_sv._K_DELETE_JPG, "true")
        delete_jpg = str(raw_del).lower() == "true"
        self._delete_jpg_chk.setChecked(delete_jpg)

        # Project tab
        proj_dir = self.ctx.current_project_dir or ""
        self._project_dir_edit.setText(proj_dir)
        self._incoming_edit.setText(qs.value(_sv._K_INCOMING_SUBDIR, "incoming-jpg"))
        self._results_edit.setText(qs.value(_sv._K_RESULTS_SUBDIR, "results"))
        self._amap_key_edit.setText(self.ctx.settings.amap_web_key)
        self._load_recent_projects()

        # User tab
        self._current_user_edit.setText(qs.value(_sv._K_CURRENT_USER, ""))

        # Collaboration tab
        self._collab_enabled_chk.setChecked(self.ctx.settings.collab_enabled)
        self._collab_team_code_edit.setText(self.ctx.settings.team_code)
        self._refresh_collab_addr()
        self._refresh_collab_members()
        self._refresh_collab_health()

        # Workbench tab
        self._auto_watch_chk.setChecked(
            str(qs.value(_sv._K_WB_AUTO_WATCH, "true")).lower() != "false"
        )
        self._auto_activate_new_chk.setChecked(
            str(qs.value(_sv._K_WB_AUTO_ACTIVATE_NEW, "false")).lower() == "true"
        )
        self._auto_organize_chk.setChecked(
            str(qs.value(_sv._K_WB_AUTO_ORGANIZE, "false")).lower() == "true"
        )
        self._silent_compose_chk.setChecked(
            str(qs.value(_sv._K_WB_SILENT_COMPOSE, "false")).lower() == "true"
        )
        fv_map = {"jpg-tif": 0, "with-zip": 1, "all": 2}
        fv_val = str(qs.value(_sv._K_WB_FILE_VIEW_MODE, "jpg-tif"))
        self._file_view_mode_combo.setCurrentIndex(fv_map.get(fv_val, 0))

        # UI / 界面 tab
        current_theme = self.ctx.settings.current_theme
        theme_idx = self._theme_combo.findData(current_theme)
        if theme_idx < 0:
            theme_idx = self._theme_combo.findData("classic_light")
        self._theme_combo.blockSignals(True)
        self._theme_combo.setCurrentIndex(max(0, theme_idx))
        self._theme_combo.blockSignals(False)

        lang_idx = self._lang_combo.findData(self.ctx.settings.current_language)
        self._lang_combo.blockSignals(True)
        self._lang_combo.setCurrentIndex(max(0, lang_idx))
        self._lang_combo.blockSignals(False)

        self._perf_mode_chk.blockSignals(True)
        self._perf_mode_chk.setChecked(self.ctx.settings.performance_mode)
        self._perf_mode_chk.blockSignals(False)

        self._project_tree_ux_v2_chk.blockSignals(True)
        self._project_tree_ux_v2_chk.setChecked(
            bool(getattr(self.ctx.settings, "project_tree_ux_v2", True))
        )
        self._project_tree_ux_v2_chk.blockSignals(False)

        try:
            font_scale = float(qs.value(_sv._K_UI_FONT_SCALE, 1.0))
        except (TypeError, ValueError):
            font_scale = 1.0
        font_scale = max(0.7, min(1.5, font_scale))
        self._font_scale_spin.blockSignals(True)
        self._font_scale_spin.setValue(font_scale)
        self._font_scale_spin.blockSignals(False)
        self._font_scale_pct_label.setText(f"{round(font_scale * 100)}%")

        saved_family = qs.value(_sv._K_UI_FONT_FAMILY, "", type=str) or ""
        fam_idx = self._font_family_combo.findData(saved_family)
        self._font_family_combo.blockSignals(True)
        self._font_family_combo.setCurrentIndex(fam_idx if fam_idx >= 0 else 0)
        self._font_family_combo.blockSignals(False)

        self._icon_gps_edit.setText(qs.value(_sv._K_UI_ICON_GPS, ""))
        self._icon_map_edit.setText(qs.value(_sv._K_UI_ICON_MAP, ""))
        self._icon_folder_edit.setText(qs.value(_sv._K_UI_ICON_FOLDER, ""))
        self._icon_search_edit.setText(qs.value(_sv._K_UI_ICON_SEARCH, ""))

        raw_screenshot_tool = qs.value(_sv._K_SCREENSHOT_TOOL_ENABLED, "false")
        screenshot_tool_enabled = str(raw_screenshot_tool).lower() == "true"
        self._screenshot_tool_chk.blockSignals(True)
        self._screenshot_tool_chk.setChecked(screenshot_tool_enabled)
        self._screenshot_tool_chk.blockSignals(False)

        raw_real = qs.value(_sv._K_DEBUG_USE_REAL_COMPRESSION, "false")
        self._use_real_compression_chk.setChecked(str(raw_real).lower() == "true")

        # Shortcuts
        sc_ma = qs.value(_sv._K_SHORTCUT_MONITOR_ACTIVATE, "")
        self._sc_monitor_activate.setKeySequence(QKeySequence(str(sc_ma)))
        sc_md = qs.value(_sv._K_SHORTCUT_MONITOR_DEACTIVATE, "")
        self._sc_monitor_deactivate.setKeySequence(QKeySequence(str(sc_md)))
        sc_lp = qs.value(_sv._K_SHORTCUT_LABELS_PRINT, "")
        self._sc_labels_print.setKeySequence(QKeySequence(str(sc_lp)))
        sc_ln = qs.value(_sv._K_SHORTCUT_LABELS_NEXT, "")
        self._sc_labels_next.setKeySequence(QKeySequence(str(sc_ln)))
        sc_shot = qs.value(_sv._K_SHORTCUT_SCREENSHOT, "") or "Alt+A"
        self._sc_screenshot.blockSignals(True)
        self._sc_screenshot.setKeySequence(QKeySequence(str(sc_shot)))
        self._sc_screenshot.setEnabled(screenshot_tool_enabled)
        self._sc_screenshot.blockSignals(False)

        # Preset list widget
        self._refresh_preset_list_widget()

    def _on_perf_mode_changed(self) -> None:
        self.ctx.settings.performance_mode = self._perf_mode_chk.isChecked()
        self.ctx.settings.flush_to_disk()
        from app.utils import ui
        ui.info(self, "性能模式", "已保存。重启软件后生效。")

    def _on_project_tree_ux_v2_changed(self) -> None:
        self.ctx.settings.project_tree_ux_v2 = self._project_tree_ux_v2_chk.isChecked()
        self.ctx.settings.flush_to_disk()
        # 已打开的项目树页立即切换布局（若尚未打开则下次进入时生效）
        try:
            mw = self.window()
            views = getattr(mw, "_views", None) or {}
            page = views.get("project_tree")
            if page is not None and hasattr(page, "_apply_ux_profile"):
                page._apply_ux_profile()
        except Exception:
            pass
        from app.utils import ui
        on = self._project_tree_ux_v2_chk.isChecked()
        ui.info(
            self,
            "项目树界面",
            "已切换为精简界面。" if on else "已退回旧版布局。",
        )

    def _on_language_changed(self) -> None:
        lang = self._lang_combo.currentData() or "zh"
        self.ctx.settings.current_language = str(lang)
        self.ctx.settings.flush_to_disk()
        from app.config.i18n import set_language
        set_language(str(lang))
        win = self.window()
        handler = getattr(win, "retranslate_ui", None)
        if callable(handler):
            handler()
        else:
            self.retranslate_ui()

    def _on_theme_changed(self) -> None:
        current_tab = self._tabs.currentIndex()
        key = self._theme_combo.currentData() or "classic_light"
        shell_handler = getattr(self.window(), "apply_visual_theme", None)
        if callable(shell_handler):
            shell_handler(str(key))
        else:
            self.ctx.settings.current_theme = str(key)
            self.ctx.settings.flush_to_disk()
            from app.config.theme import apply_theme
            app = QApplication.instance()
            if app is not None:
                app.setStyleSheet(apply_theme(str(key)))
        _sv._refresh_palette()
        self._setup_ui()
        self._tabs.setCurrentIndex(min(current_tab, self._tabs.count() - 1))
        self._load_all()

    def _refresh_helicon_display(self) -> None:
        """Update auto-detected and effective path labels from stored/detected state."""
        qs = self.ctx.settings._qs
        stored_exe = qs.value(_sv._K_HELICON_EXE, "") or ""
        if stored_exe and os.path.isfile(stored_exe):
            self._detected_path_label.setText(stored_exe)
            self._effective_path_label.setText(stored_exe)
            self._detect_status_badge.setText("✓ 可用")
            self._detect_status_badge.setStyleSheet(
                f"font-size: 12px; font-weight: 600; color: {_sv._C_SUCCESS};"
                "background: transparent;"
            )
        else:
            self._detected_path_label.setText("（未检测到）")
            self._effective_path_label.setText("—")
            self._detect_status_badge.setText("未检测到")
            self._detect_status_badge.setStyleSheet(
                f"font-size: 12px; font-weight: 600; color: {_sv._C_WARN};"
                "background: transparent;"
            )

    def _load_recent_projects(self) -> None:
        self._recent_list.clear()
        qs = self.ctx.settings._qs
        raw = qs.value(_sv._K_RECENT_PROJECTS, "")
        paths = [p for p in str(raw).split("\n") if p.strip()] if raw else []
        for p in paths:
            self._recent_list.addItem(p)

    def _save_project(self) -> None:
        qs = self.ctx.settings._qs
        incoming = self._incoming_edit.text().strip() or "incoming-jpg"
        results = self._results_edit.text().strip() or "results"
        qs.setValue(_sv._K_INCOMING_SUBDIR, incoming)
        qs.setValue(_sv._K_RESULTS_SUBDIR, results)
        self.ctx.settings.amap_web_key = self._amap_key_edit.text()
        self.ctx.settings.flush_to_disk()

    def _save_helicon(self) -> None:
        qs = self.ctx.settings._qs
        qs.setValue(_sv._K_HELICON_EXE, self._helicon_exe_edit.text().strip())
        _p = self._helicon_param_panel.get_params()
        qs.setValue(_sv._K_HELICON_METHOD, _p["method"])
        qs.setValue(_sv._K_HELICON_RADIUS, _p["radius"])
        qs.setValue(_sv._K_HELICON_SMOOTHING, _p["smoothing"])
        qs.setValue(_sv._K_HELICON_QUALITY, self._quality_spin.value())
        self.ctx.settings.flush_to_disk()

    def _save_archive(self) -> None:
        qs = self.ctx.settings._qs
        qs.setValue(_sv._K_JXL_EFFORT, self._jxl_effort_combo.currentIndex())
        qs.setValue("archive/mode_v2_fast_default_applied", "true")
        qs.setValue(_sv._K_JXL_CONCURRENCY, self._jxl_concurrency_spin.value())
        # Store as explicit "true"/"false" string for unambiguous retrieval
        qs.setValue(_sv._K_DELETE_JPG, "true" if self._delete_jpg_chk.isChecked() else "false")
        qs.setValue(_sv._K_DELETE_JPG_DEFAULT_MIGRATION, "true")
        self.ctx.settings.flush_to_disk()

    def _save_helicon_advanced(self) -> None:
        """Persist Helicon advanced output params (mirrors web 高级参数 block)."""
        qs = self.ctx.settings._qs
        fmt_idx = self._output_format_combo.currentIndex()
        qs.setValue(_sv._K_HELICON_OUTPUT_FORMAT, "jpg" if fmt_idx == 1 else "tif")
        tiff_vals = ["u", "lzw", "zip"]
        qs.setValue(
            _sv._K_HELICON_TIFF_COMPRESSION,
            tiff_vals[self._tiff_compression_combo.currentIndex()]
            if self._tiff_compression_combo.currentIndex() < len(tiff_vals)
            else "u",
        )
        run_vals = ["silent", "progress", "gui"]
        qs.setValue(
            _sv._K_HELICON_RUN_MODE,
            run_vals[self._run_mode_combo.currentIndex()]
            if self._run_mode_combo.currentIndex() < len(run_vals)
            else "silent",
        )
        qs.setValue(_sv._K_HELICON_CONCURRENCY, self._concurrency_spin.value())
        qs.setValue(
            _sv._K_HELICON_SAVE_DEPTH_MAP,
            "true" if self._save_depth_map_chk.isChecked() else "false",
        )
        self.ctx.settings.flush_to_disk()

    def _save_workbench(self) -> None:
        """Persist workbench auto-watch toggles (mirrors saveV4Settings)."""
        qs = self.ctx.settings._qs
        qs.setValue(_sv._K_WB_AUTO_WATCH, "true" if self._auto_watch_chk.isChecked() else "false")
        qs.setValue(
            _sv._K_WB_AUTO_ACTIVATE_NEW,
            "true" if self._auto_activate_new_chk.isChecked() else "false",
        )
        qs.setValue(
            _sv._K_WB_AUTO_ORGANIZE,
            "true" if self._auto_organize_chk.isChecked() else "false",
        )
        qs.setValue(
            _sv._K_WB_SILENT_COMPOSE,
            "true" if self._silent_compose_chk.isChecked() else "false",
        )
        fv_vals = ["jpg-tif", "with-zip", "all"]
        fv_idx = self._file_view_mode_combo.currentIndex()
        qs.setValue(
            _sv._K_WB_FILE_VIEW_MODE,
            fv_vals[fv_idx] if 0 <= fv_idx < len(fv_vals) else "jpg-tif",
        )
        self.ctx.settings.flush_to_disk()

    def _save_user(self) -> None:
        qs = self.ctx.settings._qs
        name = self._current_user_edit.text().strip()
        qs.setValue(_sv._K_CURRENT_USER, name)
        self.ctx.settings.flush_to_disk()
