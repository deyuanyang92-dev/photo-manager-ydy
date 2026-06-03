"""project_settings_drawer.py — Project settings side drawer.

Mirrors renderProjectSettingsDrawer() (app.js:9418) and
renderHeliconConfigModal() (app.js:7028).

Contains:
  - Helicon Focus path detection + manual override
  - Auto-activate on new specimen toggle
  - Read-only display of incomingJpgSubdir / resultsSubdir
"""
from __future__ import annotations

import os
from typing import Optional, TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from app.app_context import AppContext


class ProjectSettingsDrawer(QWidget):
    """Overlay drawer for project + Helicon settings.

    Show by calling .show(); hide with .hide().
    """

    closed = pyqtSignal()
    helicon_path_changed = pyqtSignal(str)   # new exe path

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("SettingsDrawer")
        self._setup_ui()
        self.hide()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        # Header
        head = QHBoxLayout()
        title = QLabel("项目设置")
        title.setObjectName("WorkspaceTitle")
        head.addWidget(title)
        head.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setObjectName("Ghost")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self._on_close)
        head.addWidget(close_btn)
        root.addLayout(head)

        sep = QFrame()
        sep.setObjectName("Divider")
        sep.setFixedHeight(1)
        root.addWidget(sep)

        # ── Helicon section ────────────────────────────────────────────────────
        hel_title = QLabel("Helicon Focus 配置")
        hel_title.setObjectName("Section")
        root.addWidget(hel_title)

        self._helicon_status_lbl = QLabel("检测中…")
        self._helicon_status_lbl.setObjectName("MutedSmall")
        self._helicon_status_lbl.setWordWrap(True)
        root.addWidget(self._helicon_status_lbl)

        path_row = QHBoxLayout()
        self._helicon_path_edit = QLineEdit()
        self._helicon_path_edit.setPlaceholderText("自定义 Helicon.exe 路径（留空=自动检测）")
        self._helicon_path_edit.setFixedHeight(30)
        path_row.addWidget(self._helicon_path_edit)
        detect_btn = QPushButton("检测")
        detect_btn.setObjectName("Outline")
        detect_btn.setFixedSize(52, 30)
        detect_btn.clicked.connect(self._on_detect_helicon)
        path_row.addWidget(detect_btn)
        root.addLayout(path_row)

        # ── Project paths (read-only) ─────────────────────────────────────────
        sep2 = QFrame()
        sep2.setObjectName("Divider")
        sep2.setFixedHeight(1)
        root.addWidget(sep2)

        proj_title = QLabel("工作目录子目录")
        proj_title.setObjectName("Section")
        root.addWidget(proj_title)

        self._dir_info_lbl = QLabel("（未选择项目）")
        self._dir_info_lbl.setObjectName("MutedSmall")
        self._dir_info_lbl.setWordWrap(True)
        root.addWidget(self._dir_info_lbl)

        # ── Auto-activate toggle ──────────────────────────────────────────────
        sep3 = QFrame()
        sep3.setObjectName("Divider")
        sep3.setFixedHeight(1)
        root.addWidget(sep3)

        self._auto_activate_cb = QCheckBox("新建编号后自动激活")
        self._auto_activate_cb.setChecked(False)
        self._auto_activate_cb.toggled.connect(self._on_auto_activate_changed)
        root.addWidget(self._auto_activate_cb)

        root.addStretch()

    # ── Public ────────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Update Helicon status + dir info from current state."""
        try:
            from app.services.helicon_service import detect_helicon
            exe = detect_helicon()
            if exe:
                self._helicon_status_lbl.setText(f"✅ 已检测到：{exe}")
            else:
                self._helicon_status_lbl.setText(
                    "⚠️ 未检测到 Helicon Focus。请安装后重新检测，"
                    "或在下方填写自定义路径。"
                )
        except Exception as e:
            self._helicon_status_lbl.setText(f"检测失败：{e}")

        project_dir = getattr(self.ctx, "current_project_dir", None)
        if project_dir:
            from app.services.project_service import INCOMING_JPG_DIR, RESULTS_DIR
            self._dir_info_lbl.setText(
                f"相机 JPG：{INCOMING_JPG_DIR}/\n成果 TIFF/ZIP：{RESULTS_DIR}/"
            )
        else:
            self._dir_info_lbl.setText("（未选择项目）")

        # Load auto-activate setting from ctx.settings
        try:
            val = bool(getattr(self.ctx.settings, "auto_activate_on_new_specimen", False))
            self._auto_activate_cb.setChecked(val)
        except Exception:
            pass

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_close(self) -> None:
        self.hide()
        self.closed.emit()

    def _on_detect_helicon(self) -> None:
        custom_path = self._helicon_path_edit.text().strip()
        if custom_path:
            os.environ["HELICON_FOCUS_PATH"] = custom_path
        self.refresh()
        if custom_path:
            self.helicon_path_changed.emit(custom_path)

    def _on_auto_activate_changed(self, checked: bool) -> None:
        try:
            self.ctx.settings.auto_activate_on_new_specimen = checked
        except Exception:
            pass
