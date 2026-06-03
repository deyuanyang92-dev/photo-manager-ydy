"""main_window.py — QMainWindow shell with a modern top-bar segmented nav.

Layout (Linear / Figma / Notion-grade chrome)
----------------------------------------------
    ┌──────────────────────────────────────────────────────────────┐
    │ ◆ 标本影像   工作台 总览 分类 WoRMS 坐标 标签 协作      ⚙ ◑   │  TopBar
    ├──────────────────────────────────────────────────────────────┤
    │ 项目: 福建样地 ▾    激活: DLC001 ⚡        + 新建  🎬合成 📦整理 │  ContextBar
    ├──────────────────────────────────────────────────────────────┤
    │                                                                │
    │                     QStackedWidget (page)                      │
    │                                                                │
    ├──────────────────────────────────────────────────────────────┤
    │ 激活标本 · 协作 · Helicon                                       │  StatusBar
    └──────────────────────────────────────────────────────────────┘

- TopBar:    serif brand mark + horizontal segmented nav (one flat button
             per registered view; the current page gets a 2 px accent
             underline + accent text) + right-aligned settings / theme.
- ContextBar:project switcher + active-specimen badge + quick actions.
- Center:    QStackedWidget — one page per registered view.
- Bottom:    QStatusBar — activated specimen / collaboration / Helicon.

View registration (unchanged contract)
---------------------------------------
    win.register_view(MyModuleView)   # adds a top-nav segment + stack page

Clicking a nav segment shows the matching page and calls
``view.on_activate()``.  Views are instantiated eagerly so stack indices
line up with nav order, exactly as before.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from app.config import icons
from app.views.base_view import BaseView

if TYPE_CHECKING:
    from app.app_context import AppContext


class MainWindow(QMainWindow):
    """Application shell window with modern top-bar segmented navigation.

    Parameters
    ----------
    ctx:
        Shared AppContext injected from main.py and forwarded to every view.
    """

    def __init__(self, ctx: "AppContext") -> None:
        super().__init__()
        self.ctx = ctx
        self._views: dict[str, BaseView] = {}        # view_id → instance
        self._view_classes: list[type] = []           # registration order
        self._nav_buttons: list[QPushButton] = []      # registration order
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)

        self.setWindowTitle("标本影像")
        self.resize(1440, 900)
        self.setMinimumSize(1040, 660)

        self._build_shell()
        self._build_status_bar()

    # ── Shell layout ──────────────────────────────────────────────────────

    def _build_shell(self) -> None:
        container = QWidget()
        container.setObjectName("AppShell")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Top navigation bar (brand + segmented nav + global actions)
        layout.addWidget(self._build_topbar())

        # Context bar (project + active badge + quick actions)
        layout.addWidget(self._build_context_bar())

        # Content stack
        self._stack = QStackedWidget()
        self._stack.setObjectName("ContentStack")
        layout.addWidget(self._stack, stretch=1)

        self.setCentralWidget(container)

    def _build_topbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(58)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(22, 0, 18, 0)
        lay.setSpacing(0)

        # Brand: vector microscope mark + serif wordmark
        brand_mark = QLabel()
        brand_mark.setObjectName("BrandMark")
        brand_mark.setPixmap(
            icons.icon("mdi6.microscope", color=icons.TONE_ACCENT).pixmap(20, 20)
        )
        lay.addWidget(brand_mark)
        lay.addSpacing(8)
        brand = QLabel("标本影像")
        brand.setObjectName("BrandWord")
        lay.addWidget(brand)

        lay.addSpacing(32)

        # Segmented nav row (buttons added by register_view)
        self._nav_row = QHBoxLayout()
        self._nav_row.setContentsMargins(0, 0, 0, 0)
        self._nav_row.setSpacing(2)
        nav_wrap = QWidget()
        nav_wrap.setLayout(self._nav_row)
        lay.addWidget(nav_wrap)

        lay.addStretch()

        # Right side: theme toggle + settings cog (vector glyphs)
        self._theme_btn = QPushButton()
        self._theme_btn.setObjectName("IconGhost")
        self._theme_btn.setToolTip("切换主题")
        self._theme_btn.setFixedSize(34, 34)
        self._theme_btn.setIcon(
            icons.icon("mdi6.weather-night", color=icons.TONE_MUTED,
                       color_active=icons.TONE_ACCENT_HOVER)
        )
        self._theme_btn.setIconSize(QSize(18, 18))
        self._theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(self._theme_btn)

        self._settings_btn = QPushButton()
        self._settings_btn.setObjectName("IconGhost")
        self._settings_btn.setToolTip("全局设置")
        self._settings_btn.setFixedSize(34, 34)
        self._settings_btn.setIcon(
            icons.icon("mdi6.cog-outline", color=icons.TONE_MUTED,
                       color_active=icons.TONE_ACCENT_HOVER)
        )
        self._settings_btn.setIconSize(QSize(18, 18))
        self._settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._settings_btn.clicked.connect(lambda: self.navigate_to("settings"))
        lay.addWidget(self._settings_btn)

        return bar

    def _build_context_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("ContextBar")
        bar.setFixedHeight(50)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(22, 0, 22, 0)
        lay.setSpacing(12)

        proj_label = QLabel("项目")
        proj_label.setObjectName("ContextLabel")
        lay.addWidget(proj_label)

        self._project_switcher = QPushButton("（未选）")
        self._project_switcher.setObjectName("ProjectSwitcher")
        self._project_switcher.setToolTip("切换当前工作区项目")
        icons.set_button_icon(self._project_switcher, "mdi6.folder-outline",
                              color=icons.TONE_MUTED, size=15)
        self._project_switcher.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self._project_switcher.clicked.connect(lambda: self.navigate_to("overview"))
        self._project_switcher.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(self._project_switcher)

        lay.addSpacing(8)

        active_label = QLabel("激活")
        active_label.setObjectName("ContextLabel")
        lay.addWidget(active_label)

        self._active_badge = QLabel("无")
        self._active_badge.setObjectName("ActiveBadgeOff")
        lay.addWidget(self._active_badge)

        lay.addStretch()

        # Quick actions — wired to the workbench view's handlers when present.
        self._btn_new = QPushButton("新建标本")
        self._btn_new.setObjectName("Outline")
        self._btn_new.setToolTip("新建一个标本草稿")
        icons.set_button_icon(self._btn_new, "mdi6.plus", color=icons.TONE_ACCENT, size=15)
        self._btn_new.clicked.connect(self._quick_new_specimen)
        lay.addWidget(self._btn_new)

        self._btn_compose = QPushButton("合成")
        self._btn_compose.setObjectName("Primary")
        self._btn_compose.setToolTip("Helicon 景深合成")
        icons.set_button_icon(self._btn_compose, "fa5s.layer-group",
                              color=icons.TONE_ON_ACCENT, size=14)
        self._btn_compose.clicked.connect(lambda: self.navigate_to("workbench"))
        lay.addWidget(self._btn_compose)

        self._btn_organize = QPushButton("整理")
        self._btn_organize.setObjectName("Outline")
        self._btn_organize.setToolTip("整理归档（JPG→JXL→ZIP）")
        icons.set_button_icon(self._btn_organize, "mdi6.archive-outline",
                              color=icons.TONE_MUTED, size=15)
        self._btn_organize.clicked.connect(lambda: self.navigate_to("workbench"))
        lay.addWidget(self._btn_organize)

        return bar

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
        """Register a BaseView subclass as a top-nav segment + stack page.

        The segment button is appended to the top bar in registration order;
        the view is built eagerly and added to the stack so its index matches
        its nav position (preserving the previous index-based contract).

        Parameters
        ----------
        view_cls:
            A subclass of BaseView with view_id / nav_title / nav_icon.
        """
        assert issubclass(view_cls, BaseView), "view_cls must subclass BaseView"
        idx = len(self._view_classes)
        self._view_classes.append(view_cls)

        # Top-nav segment button — vector glyph + title, accent when active.
        btn = QPushButton(view_cls.nav_title)
        btn.setObjectName("NavSegment")
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(view_cls.nav_title)
        glyph = _NAV_GLYPHS.get(view_cls.view_id, "mdi6.circle-outline")
        btn.setIcon(
            icons.icon(glyph, color=icons.TONE_MUTED,
                       color_active=icons.TONE_ACCENT_HOVER)
        )
        btn.setIconSize(QSize(16, 16))
        btn.setProperty("view_id", view_cls.view_id)
        btn.clicked.connect(lambda _=False, i=idx: self._activate_index(i))
        self._nav_group.addButton(btn, idx)
        self._nav_buttons.append(btn)
        self._nav_row.addWidget(btn)

        # Eagerly build the view and add to stack so indices match nav order
        view = view_cls(self.ctx)
        self._views[view_cls.view_id] = view
        self._stack.addWidget(view)

    def _recolor_nav_icons(self, active_idx: int) -> None:
        """Tint the active segment's glyph accent; others stay muted."""
        for i, b in enumerate(self._nav_buttons):
            vid = b.property("view_id")
            glyph = _NAV_GLYPHS.get(vid, "mdi6.circle-outline")
            tone = icons.TONE_ACCENT_HOVER if i == active_idx else icons.TONE_MUTED
            b.setIcon(icons.icon(glyph, color=tone,
                                 color_active=icons.TONE_ACCENT_HOVER))

    def navigate_to(self, view_id: str) -> None:
        """Programmatically switch to the view with the given view_id."""
        for i, cls in enumerate(self._view_classes):
            if cls.view_id == view_id:
                self._activate_index(i)
                return

    def _activate_index(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._view_classes):
            return
        btn = self._nav_buttons[idx]
        if not btn.isChecked():
            btn.setChecked(True)
        self._recolor_nav_icons(idx)
        view_cls = self._view_classes[idx]
        view = self._views.get(view_cls.view_id)
        if view:
            self._stack.setCurrentWidget(view)
            view.on_activate()
        self.ctx.settings.last_nav_index = idx
        self.refresh_context_bar()

    # ── Context bar ────────────────────────────────────────────────────────

    def refresh_context_bar(self) -> None:
        """Sync the context bar (project + active badge) with current state."""
        from pathlib import Path

        project_dir = getattr(self.ctx, "current_project_dir", None)
        name = Path(project_dir).name if project_dir else "（未选）"
        self._project_switcher.setText(f"{name}  ▾")

        active_uid = self._lookup_active_uid()
        if active_uid:
            short = active_uid.split("-")[3] if active_uid.count("-") >= 3 else active_uid
            self._active_badge.setText(short)
            self._active_badge.setObjectName("ActiveBadgeOn")
            self.set_status_specimen(f"激活: {active_uid}")
        else:
            self._active_badge.setText("无")
            self._active_badge.setObjectName("ActiveBadgeOff")
            self.set_status_specimen("未激活标本")
        self._active_badge.style().unpolish(self._active_badge)
        self._active_badge.style().polish(self._active_badge)

        has_proj = bool(project_dir)
        for b in (self._btn_new, self._btn_compose, self._btn_organize):
            b.setEnabled(has_proj)

    def _lookup_active_uid(self) -> Optional[str]:
        try:
            db = self.ctx.get_db()
            if not db:
                return None
            from app.services.activation_service import get_active_uid
            return get_active_uid(db)
        except Exception:
            return None

    def _quick_new_specimen(self) -> None:
        self.navigate_to("workbench")
        wb = self._views.get("workbench")
        handler = getattr(wb, "_on_new_specimen", None)
        if callable(handler):
            try:
                handler()
            except Exception:
                pass

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
        if 0 <= last_idx < len(self._view_classes):
            self._activate_index(last_idx)
        elif self._view_classes:
            self._activate_index(0)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.ctx.settings.save_geometry(self.saveGeometry())
        self.ctx.settings.save_window_state(self.saveState())
        self.ctx.settings.sync()
        super().closeEvent(event)


# ── Nav glyphs (view_id → qtawesome Material Design Icon) ───────────────────

_NAV_GLYPHS: dict[str, str] = {
    "workbench": "mdi6.microscope",
    "overview":  "mdi6.view-dashboard-outline",
    "taxonomy":  "mdi6.dna",
    "worms":     "mdi6.waves",
    "coords":    "mdi6.map-marker-outline",
    "labels":    "mdi6.tag-outline",
    "collab":    "mdi6.account-group-outline",
    "settings":  "mdi6.cog-outline",
}


# ── Helpers ───────────────────────────────────────────────────────────────

def _separator() -> QLabel:
    sep = QLabel("·")
    sep.setObjectName("StatusSegment")
    return sep
