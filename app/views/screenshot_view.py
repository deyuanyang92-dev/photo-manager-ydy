"""screenshot_view.py — Snipaste-style screenshot hub."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.config import icons
from app.services.screenshot_service import recent_screenshots, screenshot_dir
from app.views.base_view import BaseView


class ScreenshotView(BaseView):
    view_id = "screenshot"
    nav_title = "截图"
    nav_icon = "✂"

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("截图")
        title.setObjectName("WorkspaceTitle")
        header.addWidget(title)
        header.addStretch(1)

        self._shortcut_lbl = QLabel("")
        self._shortcut_lbl.setObjectName("MutedSmall")
        header.addWidget(self._shortcut_lbl)
        root.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(14)
        root.addLayout(body, stretch=1)

        left = QFrame()
        left.setObjectName("DirStrip")
        left.setMinimumWidth(360)
        left.setMaximumWidth(440)
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(16, 16, 16, 16)
        left_lay.setSpacing(12)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        left_lay.addLayout(grid)

        self._region_btn = self._capture_button("区域截图", "mdi6.selection-drag", "region")
        self._fullscreen_btn = self._capture_button("全屏截图", "mdi6.monitor-screenshot", "fullscreen")
        self._window_btn = self._capture_button("当前窗口", "mdi6.application-outline", "window")
        self._view_btn = self._capture_button("当前页面", "mdi6.view-dashboard-outline", "view")
        grid.addWidget(self._region_btn, 0, 0)
        grid.addWidget(self._fullscreen_btn, 0, 1)
        grid.addWidget(self._window_btn, 1, 0)
        grid.addWidget(self._view_btn, 1, 1)

        self._dir_lbl = QLabel("")
        self._dir_lbl.setObjectName("MutedSmall")
        self._dir_lbl.setWordWrap(True)
        left_lay.addWidget(self._dir_lbl)

        open_row = QHBoxLayout()
        self._open_dir_btn = QPushButton("打开截图文件夹")
        self._open_dir_btn.setObjectName("Outline")
        self._open_dir_btn.setFixedHeight(30)
        icons.set_button_icon(
            self._open_dir_btn, "mdi6.folder-open-outline",
            color=icons.TONE_MUTED, size=15,
        )
        self._open_dir_btn.clicked.connect(self._open_screenshot_dir)
        open_row.addWidget(self._open_dir_btn)
        open_row.addStretch(1)
        left_lay.addLayout(open_row)
        left_lay.addStretch(1)

        body.addWidget(left)

        right = QFrame()
        right.setObjectName("DirStrip")
        right_lay = QHBoxLayout(right)
        right_lay.setContentsMargins(16, 16, 16, 16)
        right_lay.setSpacing(14)

        list_panel = QVBoxLayout()
        recent_title = QLabel("最近截图")
        recent_title.setObjectName("DirLabel")
        list_panel.addWidget(recent_title)
        self._recent_list = QListWidget()
        self._recent_list.setObjectName("RecentScreenshots")
        self._recent_list.currentItemChanged.connect(self._on_recent_changed)
        self._recent_list.itemDoubleClicked.connect(self._open_recent_item)
        list_panel.addWidget(self._recent_list, stretch=1)
        right_lay.addLayout(list_panel, stretch=1)

        preview_panel = QVBoxLayout()
        preview_title = QLabel("预览")
        preview_title.setObjectName("DirLabel")
        preview_panel.addWidget(preview_title)
        self._preview = QLabel("无")
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setMinimumSize(260, 180)
        self._preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._preview.setStyleSheet(
            "background:#ffffff;border:1px solid rgba(15,23,42,0.10);"
            "border-radius:8px;color:#94a3b8;"
        )
        preview_panel.addWidget(self._preview, stretch=1)
        right_lay.addLayout(preview_panel, stretch=2)

        body.addWidget(right, stretch=1)

        self._bound_ctrl = None
        self._refresh()

    def on_activate(self) -> None:
        self._bind_controller()
        self._refresh()

    def _capture_button(self, text: str, glyph: str, mode: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("Outline")
        btn.setFixedHeight(44)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda _=False, m=mode: self._capture(m))
        icons.set_button_icon(btn, glyph, color=icons.TONE_MUTED, size=18)
        return btn

    def _controller(self):
        win = self.window()
        return getattr(win, "_shot_ctrl", None)

    def _bind_controller(self) -> None:
        ctrl = self._controller()
        if ctrl is None or ctrl is self._bound_ctrl:
            return
        if self._bound_ctrl is not None:
            try:
                self._bound_ctrl.captured.disconnect(self._on_captured)
            except TypeError:
                pass
        ctrl.captured.connect(self._on_captured)
        self._bound_ctrl = ctrl

    def _capture(self, mode: str) -> None:
        ctrl = self._controller()
        if ctrl is None:
            return
        {
            "region": ctrl.capture_region,
            "fullscreen": ctrl.capture_fullscreen,
            "window": ctrl.capture_window,
            "view": ctrl.capture_view,
        }[mode]()

    def _on_captured(self, _pixmap: QPixmap) -> None:
        self._refresh_recent()

    def _project_dir(self) -> str:
        return getattr(self.ctx, "current_project_dir", "") or ""

    def _refresh(self) -> None:
        seq = "Alt+A"
        win = self.window()
        getter = getattr(win, "screenshot_shortcut_seq", None)
        if callable(getter):
            seq = getter()
        self._shortcut_lbl.setText(f"区域截图快捷键：{seq}")

        project_dir = self._project_dir()
        if project_dir:
            self._dir_lbl.setText(str(screenshot_dir(project_dir)))
            self._open_dir_btn.setEnabled(True)
        else:
            self._dir_lbl.setText("未选择项目时，截图仍会复制到剪贴板，可另存。")
            self._open_dir_btn.setEnabled(False)
        self._refresh_recent()

    def _refresh_recent(self) -> None:
        self._recent_list.clear()
        project_dir = self._project_dir()
        if not project_dir:
            self._preview.setText("无")
            self._preview.setPixmap(QPixmap())
            return
        for path in recent_screenshots(project_dir):
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self._recent_list.addItem(item)
        if self._recent_list.count():
            self._recent_list.setCurrentRow(0)
        else:
            self._preview.setText("无")
            self._preview.setPixmap(QPixmap())

    def _on_recent_changed(self, current: QListWidgetItem | None, _previous) -> None:
        if current is None:
            return
        path = Path(current.data(Qt.ItemDataRole.UserRole))
        pix = QPixmap(str(path))
        if pix.isNull():
            self._preview.setText("无法预览")
            self._preview.setPixmap(QPixmap())
            return
        size = self._preview.size() - QSize(18, 18)
        self._preview.setPixmap(
            pix.scaled(
                size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _open_screenshot_dir(self) -> None:
        project_dir = self._project_dir()
        if not project_dir:
            return
        path = screenshot_dir(project_dir)
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_recent_item(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
