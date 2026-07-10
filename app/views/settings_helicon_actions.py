"""Helicon preset and detection actions for the settings view."""
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


class SettingsHeliconActionsMixin:
    """Helicon preset and detection actions for the settings view."""

    # ── Helicon preset CRUD ───────────────────────────────────────────────────

    def _load_presets(self) -> list:
        """Read preset list from QSettings (JSON)."""
        import json
        qs = self.ctx.settings._qs
        raw = qs.value(_sv._K_HELICON_PRESETS_JSON, "[]")
        try:
            data = json.loads(str(raw))
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, TypeError):
            pass
        return []

    def _save_preset_list(self, presets: list) -> None:
        """Persist preset list to QSettings."""
        import json
        qs = self.ctx.settings._qs
        qs.setValue(_sv._K_HELICON_PRESETS_JSON, json.dumps(presets))
        self.ctx.settings.flush_to_disk()

    def _refresh_preset_list_widget(self) -> None:
        """Reload QListWidget from QSettings."""
        self._preset_list.clear()
        for p in self._load_presets():
            self._preset_list.addItem(p.get("name", ""))

    def _save_current_as_preset(self) -> None:
        """保存为预设 — upsert by name (mirrors server.js:2449-2452)."""
        name = self._preset_name_edit.text().strip()
        if not name:
            return  # 空名称：静默忽略
        from datetime import datetime, timezone
        params = {
            "method": self._helicon_param_panel.get_params()["method"] + 1,
            "radius": self._helicon_param_panel.get_params()["radius"],
            "smoothing": self._helicon_param_panel.get_params()["smoothing"],
            "quality": self._quality_spin.value(),
        }
        preset = {
            "name": name,
            "params": params,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        presets = self._load_presets()
        existing = next((i for i, p in enumerate(presets) if p.get("name") == name), -1)
        if existing >= 0:
            presets[existing] = preset
        else:
            presets.append(preset)
        self._save_preset_list(presets)
        self._refresh_preset_list_widget()

    def _apply_selected_preset(self) -> None:
        """应用选中预设 — fill spinboxes + save."""
        item = self._preset_list.currentItem()
        if not item:
            return
        name = item.text()
        presets = self._load_presets()
        preset = next((p for p in presets if p.get("name") == name), None)
        if not preset:
            return
        params = preset.get("params", {})
        method_idx = int(params.get("method", 1)) - 1  # 1-based → 0-based index
        method_idx = max(0, min(method_idx, 2))
        self._helicon_param_panel.set_params({
            "method": method_idx,
            "radius": float(params.get("radius", 8.0)),
            "smoothing": int(params.get("smoothing", 4)),
        })
        self._quality_spin.setValue(int(params.get("quality", 95)))
        self._save_helicon()

    def _delete_selected_preset(self) -> None:
        """删除选中预设 — remove from list (mirrors server.js:2462-2471)."""
        item = self._preset_list.currentItem()
        if not item:
            return
        name = item.text()
        presets = [p for p in self._load_presets() if p.get("name") != name]
        self._save_preset_list(presets)
        self._preset_name_edit.clear()
        self._refresh_preset_list_widget()

    # ── Helicon button handlers (web: 检测 / 保存 / 清除自定义 / 重新探测) ───

    def _show_status(self, msg: str, timeout_ms: int = 3000) -> None:
        """Show a brief message on the main window status bar."""
        win = self.window()
        if hasattr(win, "statusBar"):
            bar = win.statusBar()
            if bar:
                bar.showMessage(msg, timeout_ms)

    def _flash_button(self, btn: QPushButton, text: str, duration_ms: int = 1500) -> None:
        """按钮文字瞬时确认后恢复 — 路径/标签不变时唯一可见反馈（同 40ce2c9 对话框）。"""
        if btn.property("_flashing"):
            return
        orig = btn.text()
        btn.setProperty("_flashing", True)
        btn.setText(text)

        def _restore() -> None:
            btn.setText(orig)
            btn.setProperty("_flashing", False)

        # Timer parented to the button: it dies with the button, so leaving the
        # page within the flash window can't fire _restore on a dead widget.
        timer = QTimer(btn)
        timer.setSingleShot(True)
        timer.timeout.connect(_restore)
        timer.timeout.connect(timer.deleteLater)
        timer.start(duration_ms)

    def _on_test_click(self) -> None:
        """检测 — validate custom path then auto-detect (mirrors web testBtn handler)."""
        custom = self._helicon_exe_edit.text().strip()
        if custom:
            self._show_status("正在验证自定义路径…")
        else:
            self._show_status("正在自动探测 Helicon Focus…")
        self._save_helicon()
        self._detect_helicon(custom_path=custom)
        self._flash_button(self._test_btn, "已检测 ✓")

    def _on_save_click(self) -> None:
        """保存 — persist custom path."""
        self._save_helicon()
        self._refresh_helicon_display()
        self._show_status("✓ Helicon 设置已保存")
        self._flash_button(self._save_btn, "已保存 ✓")

    def _on_clear_click(self) -> None:
        """清除自定义 — wipe custom path and re-detect."""
        self._helicon_exe_edit.clear()
        self._save_helicon()
        self._detect_helicon()
        self._show_status("已清除自定义路径，已重新探测")
        self._flash_button(self._clear_btn, "已清除 ✓")

    def _on_redetect_click(self) -> None:
        """重新探测按钮 — 结果不变时标签一字不动，必须闪按钮确认。"""
        self._detect_helicon()
        self._flash_button(self._refresh_btn, "已重新探测 ✓")

    def _detect_helicon(self, custom_path: str = "") -> None:
        """重新探测 / auto-detect — calls helicon_service.detect_helicon()."""
        try:
            from app.services.helicon_service import detect_helicon, reset_helicon_cache
            reset_helicon_cache()
            found = detect_helicon(custom_path=custom_path)
        except Exception:
            found = None

        if found:
            self._helicon_exe_edit.setText(found)
            self._save_helicon()
            self._detected_path_label.setText(found)
            self._effective_path_label.setText(found)
            self._detect_status_badge.setText("✓ 可用")
            self._detect_status_badge.setStyleSheet(
                f"font-size: 12px; font-weight: 600; color: {_sv._C_SUCCESS};"
                "background: transparent;"
            )
            self._show_status(f"✓ 检测成功: {found}", 5000)
        else:
            self._detected_path_label.setText("（未检测到）")
            self._effective_path_label.setText("—")
            self._detect_status_badge.setText("未检测到")
            self._detect_status_badge.setStyleSheet(
                f"font-size: 12px; font-weight: 600; color: {_sv._C_WARN};"
                "background: transparent;"
            )
            if custom_path:
                self._show_status("✗ 自定义路径无效，自动探测也未找到 Helicon Focus", 5000)
            else:
                self._show_status("✗ 未检测到 Helicon Focus（Windows 专有工具，Linux 下需通过 WSL 路径配置）", 5000)
        win = self.window()
        if hasattr(win, "refresh_helicon_status"):
            try:
                win.refresh_helicon_status()
            except Exception:
                pass
