"""Project directory and recent-project actions for the settings view."""
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


class SettingsProjectHistoryMixin:
    """Project directory and recent-project actions for the settings view."""

    # ── Project directory ─────────────────────────────────────────────────

    def _browse_project_dir(self) -> None:
        start = self.ctx.current_project_dir or os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(
            self, "选择项目目录", start
        )
        if chosen:
            try:
                from app.services.project_service import enter_workspace
                enter_workspace(self.ctx, chosen)
                self._project_dir_edit.setText(self.ctx.current_project_dir or chosen)
                self._add_to_recent(chosen)
            except Exception as exc:
                self._show_status(f"打开项目失败：{exc}", 5000)

    def _add_to_recent(self, path: str) -> None:
        qs = self.ctx.settings._qs
        raw = qs.value(_sv._K_RECENT_PROJECTS, "")
        paths = [p for p in str(raw).split("\n") if p.strip()] if raw else []
        # Move to front, deduplicate, cap at RECENT_MAX
        if path in paths:
            paths.remove(path)
        paths.insert(0, path)
        paths = paths[:_sv._RECENT_MAX]
        qs.setValue(_sv._K_RECENT_PROJECTS, "\n".join(paths))
        self.ctx.settings.flush_to_disk()
        self._load_recent_projects()

    def _open_recent(self) -> None:
        item = self._recent_list.currentItem()
        if item:
            path = item.text()
            if os.path.isdir(path):
                try:
                    from app.services.project_service import enter_workspace
                    enter_workspace(self.ctx, path)
                    self._project_dir_edit.setText(self.ctx.current_project_dir or path)
                except Exception as exc:
                    self._show_status(f"打开项目失败：{exc}", 5000)

    def _clear_recent(self) -> None:
        qs = self.ctx.settings._qs
        qs.remove(_sv._K_RECENT_PROJECTS)
        self.ctx.settings.flush_to_disk()
        self._recent_list.clear()

    # ── Legacy helicon exe accessors (kept for round-trip tests) ──────────

    def _browse_helicon_exe(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "选择 HeliconFocus.exe",
            self._helicon_exe_edit.text() or "",
            "Executable (*.exe);;All files (*)"
        )
        if chosen:
            self._helicon_exe_edit.setText(chosen)
            self._save_helicon()
