"""Theme, typography, screenshot, and shortcut settings behaviour."""
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


class SettingsUiWorkflowMixin:
    """Theme, typography, screenshot, and shortcut settings behaviour."""

    def _sync_font_scale_preset(self, value: float) -> None:
        """Select the matching friendly preset, or 自定义 for other values."""
        custom_index = self._font_scale_preset_combo.count() - 1
        target_index = custom_index
        for index in range(custom_index):
            preset = self._font_scale_preset_combo.itemData(index)
            if preset is not None and abs(float(preset) - float(value)) < 0.001:
                target_index = index
                break
        self._font_scale_preset_combo.blockSignals(True)
        self._font_scale_preset_combo.setCurrentIndex(target_index)
        self._font_scale_preset_combo.blockSignals(False)

    def _on_font_scale_preset_changed(self) -> None:
        """Apply a friendly size preset through the existing live-save path."""
        scale = self._font_scale_preset_combo.currentData()
        if scale is not None:
            self._font_scale_spin.setValue(float(scale))

    def _on_font_scale_changed(self, value: float) -> None:
        """Realtime: update percentage label, persist, and re-skin the app."""
        self._font_scale_pct_label.setText(f"{round(value * 100)}%")
        self._sync_font_scale_preset(value)
        self._save_ui()
        self._apply_typography_live()

    def _on_font_family_changed(self) -> None:
        """字体族切换 → 立即生效 + 持久化。"""
        self._save_ui()
        self._apply_typography_live()

    def _on_screenshot_shortcut_changed(self) -> None:
        """截图快捷键录制完成 → 持久化 + 立即重绑（窗口内 + 系统全局）。"""
        self._save_ui()
        seq = self._sc_screenshot.keySequence().toString() or "Alt+A"
        from app.main_window import MainWindow
        for w in QApplication.instance().topLevelWidgets():
            if isinstance(w, MainWindow):
                w.rebind_screenshot_shortcut(seq)
                break

    def _on_screenshot_tool_enabled_changed(self) -> None:
        enabled = self._screenshot_tool_chk.isChecked()
        self._sc_screenshot.setEnabled(enabled)
        self._save_ui()
        w = self.window()
        handler = getattr(w, "set_screenshot_tool_enabled", None)
        if callable(handler):
            handler(enabled)

    def _apply_typography_live(self) -> None:
        """Push current 字体 / 字体大小 into the live theme + default font."""
        from app.config.theme import set_typography, apply_theme, apply_default_font
        family = self._font_family_combo.currentData() or ""
        set_typography(scale=self._font_scale_spin.value(), family=str(family))
        app = QApplication.instance()
        if app is not None:
            apply_default_font(app)
            app.setStyleSheet(apply_theme(self.ctx.settings.current_theme))
        _sv._refresh_palette()

    def _save_ui(self) -> None:
        """Persist 界面 tab settings (fontScale / icons / shortcuts / useRealCompression)."""
        qs = self.ctx.settings._qs
        qs.setValue(_sv._K_UI_FONT_SCALE, self._font_scale_spin.value())
        qs.setValue(_sv._K_UI_FONT_FAMILY, self._font_family_combo.currentData() or "")
        qs.setValue(_sv._K_UI_ICON_GPS, self._icon_gps_edit.text())
        qs.setValue(_sv._K_UI_ICON_MAP, self._icon_map_edit.text())
        qs.setValue(_sv._K_UI_ICON_FOLDER, self._icon_folder_edit.text())
        qs.setValue(_sv._K_UI_ICON_SEARCH, self._icon_search_edit.text())
        qs.setValue(
            _sv._K_SCREENSHOT_TOOL_ENABLED,
            "true" if self._screenshot_tool_chk.isChecked() else "false",
        )
        qs.setValue(
            _sv._K_DEBUG_USE_REAL_COMPRESSION,
            "true" if self._use_real_compression_chk.isChecked() else "false",
        )
        # Shortcuts — store as portable string (QKeySequence.toString)
        qs.setValue(
            _sv._K_SHORTCUT_MONITOR_ACTIVATE,
            self._sc_monitor_activate.keySequence().toString(),
        )
        qs.setValue(
            _sv._K_SHORTCUT_MONITOR_DEACTIVATE,
            self._sc_monitor_deactivate.keySequence().toString(),
        )
        qs.setValue(
            _sv._K_SHORTCUT_LABELS_PRINT,
            self._sc_labels_print.keySequence().toString(),
        )
        qs.setValue(
            _sv._K_SHORTCUT_LABELS_NEXT,
            self._sc_labels_next.keySequence().toString(),
        )
        qs.setValue(
            _sv._K_SHORTCUT_SCREENSHOT,
            self._sc_screenshot.keySequence().toString(),
        )
        self.ctx.settings.flush_to_disk()
