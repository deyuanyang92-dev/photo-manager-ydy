"""main_window.py — QMainWindow shell with nav sidebar + QStackedWidget.

Architecture
------------
- Left sidebar: QListWidget#NavList (fixed 200 px wide, icon + title per item).
- Center:       QStackedWidget — one page per registered view.
- Bottom:       QStatusBar — three label segments:
    1. Activated specimen UID
    2. Collaboration status
    3. Helicon Focus status

View registration
-----------------
    win.register_view(MyModuleView)   # adds nav item + stack page

When the user clicks a nav item, the corresponding page is shown and
``view.on_activate()`` is called.  The view is only instantiated once
(lazy singleton per class).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QWidget,
)

from app.views.base_view import BaseView

if TYPE_CHECKING:
    from app.app_context import AppContext

# ── Nav item width ────────────────────────────────────────────────────────
_NAV_WIDTH = 200


class MainWindow(QMainWindow):
    """Application shell window.

    Parameters
    ----------
    ctx:
        Shared AppContext injected from main.py and forwarded to every view.
    """

    def __init__(self, ctx: "AppContext") -> None:
        super().__init__()
        self.ctx = ctx
        self._views: dict[str, BaseView] = {}       # view_id → instance
        self._view_classes: list[type] = []          # registration order

        self.setWindowTitle("标本照片工作台")
        self.resize(1400, 860)
        self.setMinimumSize(900, 600)

        self._build_shell()
        self._build_status_bar()

    # ── Shell layout ──────────────────────────────────────────────────────

    def _build_shell(self) -> None:
        container = QWidget()
        container.setObjectName("AppShell")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Navigation sidebar
        self._nav = QListWidget()
        self._nav.setObjectName("NavList")
        self._nav.setFixedWidth(_NAV_WIDTH)
        self._nav.setSpacing(0)
        self._nav.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._nav.currentRowChanged.connect(self._on_nav_changed)
        layout.addWidget(self._nav)

        # Content stack
        self._stack = QStackedWidget()
        self._stack.setObjectName("ContentStack")
        layout.addWidget(self._stack, stretch=1)

        self.setCentralWidget(container)

    def _build_status_bar(self) -> None:
        bar = QStatusBar()
        bar.setObjectName("StatusBar")
        bar.setSizeGripEnabled(False)
        self.setStatusBar(bar)

        # Segment 1 — activated specimen
        self._status_specimen = QLabel("未激活标本")
        self._status_specimen.setObjectName("StatusSegment")
        bar.addWidget(self._status_specimen)

        bar.addWidget(_separator())

        # Segment 2 — collaboration
        self._status_collab = QLabel("协作: 离线")
        self._status_collab.setObjectName("StatusSegment")
        bar.addWidget(self._status_collab)

        bar.addWidget(_separator())

        # Segment 3 — Helicon
        self._status_helicon = QLabel("Helicon: 未检测")
        self._status_helicon.setObjectName("StatusSegment")
        bar.addWidget(self._status_helicon)

    # ── View registry ─────────────────────────────────────────────────────

    def register_view(self, view_cls: type) -> None:
        """Register a BaseView subclass as a navigation + stack entry.

        The view is instantiated lazily on first navigation to it,
        but we add the nav item immediately.

        Parameters
        ----------
        view_cls:
            A class that is a subclass of BaseView.
            Must have view_id, nav_title, nav_icon defined.
        """
        assert issubclass(view_cls, BaseView), "view_cls must subclass BaseView"
        self._view_classes.append(view_cls)

        # Nav item
        item = QListWidgetItem(f"  {view_cls.nav_icon}  {view_cls.nav_title}")
        item.setData(Qt.ItemDataRole.UserRole, view_cls.view_id)
        item.setSizeHint(item.sizeHint().__class__(
            _NAV_WIDTH, 44
        ))
        self._nav.addItem(item)

        # Eagerly build the view and add to stack so indices match nav rows
        view = view_cls(self.ctx)
        self._views[view_cls.view_id] = view
        self._stack.addWidget(view)

    def navigate_to(self, view_id: str) -> None:
        """Programmatically switch to the view with the given view_id."""
        for row in range(self._nav.count()):
            item = self._nav.item(row)
            if item and item.data(Qt.ItemDataRole.UserRole) == view_id:
                self._nav.setCurrentRow(row)
                return

    def _on_nav_changed(self, row: int) -> None:
        if row < 0 or row >= len(self._view_classes):
            return
        view_cls = self._view_classes[row]
        view = self._views.get(view_cls.view_id)
        if view:
            self._stack.setCurrentWidget(view)
            view.on_activate()
        self.ctx.settings.last_nav_index = row

    # ── Status bar public API ─────────────────────────────────────────────

    def set_status_specimen(self, text: str) -> None:
        self._status_specimen.setText(text)

    def set_status_collab(self, text: str) -> None:
        self._status_collab.setText(text)

    def set_status_helicon(self, text: str) -> None:
        self._status_helicon.setText(text)

    # ── Persistence ───────────────────────────────────────────────────────

    def restore_state(self) -> None:
        """Restore window geometry and last nav selection from QSettings."""
        geom = self.ctx.settings.restore_geometry()
        if geom:
            self.restoreGeometry(geom)
        state = self.ctx.settings.restore_window_state()
        if state:
            self.restoreState(state)
        last_idx = self.ctx.settings.last_nav_index
        if 0 <= last_idx < self._nav.count():
            self._nav.setCurrentRow(last_idx)
        elif self._nav.count() > 0:
            self._nav.setCurrentRow(0)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.ctx.settings.save_geometry(self.saveGeometry())
        self.ctx.settings.save_window_state(self.saveState())
        self.ctx.settings.sync()
        super().closeEvent(event)


# ── Helpers ───────────────────────────────────────────────────────────────

def _separator() -> QLabel:
    sep = QLabel("·")
    sep.setObjectName("StatusSegment")
    return sep
