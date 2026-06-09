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
from PyQt6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStatusBar,
    QToolButton,
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
        # Minimum width must stay below common small/remote-desktop screens
        # (e.g. 1024×768). At 1040 the window can't shrink to fit a 1024-wide
        # screen, so its right edge — the screenshot/settings/Helicon cluster —
        # is pushed off-screen and the topbar reads as crowded.
        self.setMinimumSize(940, 600)

        self._build_shell()
        self._build_status_bar()
        self._wire_collab_status_bar()
        self._wire_screenshot()

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
        bar.setFixedHeight(54)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 0, 18, 0)
        lay.setSpacing(0)

        # Brand: vector microscope mark + serif wordmark
        brand_mark = QLabel()
        brand_mark.setObjectName("BrandMark")
        brand_mark.setPixmap(
            icons.icon("mdi6.microscope", color=icons.TONE_ACCENT).pixmap(20, 20)
        )
        lay.addWidget(brand_mark)
        lay.addSpacing(8)
        brand = QLabel("标本影像管理")
        brand.setObjectName("BrandWord")
        lay.addWidget(brand)

        lay.addSpacing(18)

        # Project switcher in topbar (left side)
        self._project_switcher = QPushButton("（未选）")
        self._project_switcher.setObjectName("ProjectSwitcher")
        self._project_switcher.setToolTip("切换当前工作区项目")
        self._project_switcher.setMinimumWidth(160)
        self._project_switcher.setMaximumWidth(240)
        icons.set_button_icon(self._project_switcher, "mdi6.folder-outline",
                              color=icons.TONE_MUTED, size=15)
        self._project_switcher.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self._project_switcher.clicked.connect(lambda: self.navigate_to("overview"))
        self._project_switcher.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(self._project_switcher)

        lay.addSpacing(16)

        # Segmented nav row (buttons added by register_view)
        self._nav_row = QHBoxLayout()
        self._nav_row.setContentsMargins(0, 0, 0, 0)
        self._nav_row.setSpacing(2)
        nav_wrap = QWidget()
        nav_wrap.setObjectName("NavWrap")
        nav_wrap.setLayout(self._nav_row)
        # Narrow screens (e.g. 1024px) can't fit brand + switcher + all nav tabs
        # + action buttons on one line; without this the nav buttons overflow and
        # their labels overlap.  Hosting the nav row in a horizontal scroll area
        # lets it shrink (and scroll) instead of overlapping.  On wide screens the
        # content fits, no scrollbar shows, and it looks identical to before — the
        # scroll area takes the flexible middle space that the old stretch held.
        nav_scroll = QScrollArea()
        nav_scroll.setObjectName("NavScroll")
        nav_scroll.setWidget(nav_wrap)
        nav_scroll.setWidgetResizable(True)
        nav_scroll.setFrameShape(QFrame.Shape.NoFrame)
        nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        nav_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(nav_scroll, stretch=1)

        # Divider: separate the flexible nav region from the fixed action cluster
        # so the right side reads as one tidy group rather than buttons crowding
        # the tabs.
        lay.addSpacing(12)
        lay.addWidget(self._topbar_divider())
        lay.addSpacing(12)

        # Right side: compact global actions. Uniform 30px height keeps every
        # control on one baseline; uniform 6px gaps keep the cluster even.
        self._btn_new_project = QPushButton("新建")
        self._btn_new_project.setObjectName("Outline")
        self._btn_new_project.setToolTip("新建一个项目工作区目录")
        self._btn_new_project.setFixedHeight(30)
        icons.set_button_icon(self._btn_new_project, "mdi6.plus",
                              color=icons.TONE_MUTED, size=15)
        self._btn_new_project.clicked.connect(self._on_new_project)
        self._btn_new_project.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(self._btn_new_project)

        lay.addSpacing(6)

        self._btn_open_ws = QPushButton("打开")
        self._btn_open_ws.setObjectName("Outline")
        self._btn_open_ws.setToolTip("打开已有项目工作区目录")
        self._btn_open_ws.setFixedHeight(30)
        icons.set_button_icon(self._btn_open_ws, "mdi6.folder-open-outline",
                              color=icons.TONE_MUTED, size=15)
        self._btn_open_ws.clicked.connect(self._on_open_workspace)
        self._btn_open_ws.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(self._btn_open_ws)

        lay.addSpacing(6)

        self._btn_compress = QPushButton("归档")
        self._btn_compress.setObjectName("Outline")
        self._btn_compress.setToolTip("智能压缩归档（JPG→JXL→ZIP）")
        self._btn_compress.setFixedHeight(30)
        icons.set_button_icon(self._btn_compress, "mdi6.archive-outline",
                              color=icons.TONE_MUTED, size=15)
        self._btn_compress.clicked.connect(lambda: self.navigate_to("workbench"))
        self._btn_compress.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(self._btn_compress)

        # Thin divider before the icon-only tools, grouping text buttons apart
        # from the icon cluster.
        lay.addSpacing(10)
        lay.addWidget(self._topbar_divider())
        lay.addSpacing(10)

        # Screenshot tool: click = region capture; dropdown = other modes.
        self._shot_btn = QToolButton()
        self._shot_btn.setObjectName("IconGhost")
        self._shot_btn.setToolTip("截图（Alt+A 区域截图）")
        self._shot_btn.setFixedSize(44, 30)  # extra width for the dropdown caret
        self._shot_btn.setIcon(
            icons.icon("mdi6.scissors-cutting", color=icons.TONE_MUTED,
                       color_active=icons.TONE_ACCENT_HOVER)
        )
        self._shot_btn.setIconSize(QSize(18, 18))
        self._shot_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._shot_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        shot_menu = QMenu(self._shot_btn)
        shot_menu.addAction("区域截图", lambda: self._shot_ctrl.capture_region())
        shot_menu.addAction("全屏截图", lambda: self._shot_ctrl.capture_fullscreen())
        shot_menu.addAction("当前窗口", lambda: self._shot_ctrl.capture_window())
        shot_menu.addAction("当前页面", lambda: self._shot_ctrl.capture_view())
        self._shot_btn.setMenu(shot_menu)
        self._shot_btn.clicked.connect(lambda: self._shot_ctrl.capture_region())
        lay.addWidget(self._shot_btn)

        lay.addSpacing(6)

        self._settings_btn = QPushButton()
        self._settings_btn.setObjectName("IconGhost")
        self._settings_btn.setToolTip("配置")
        self._settings_btn.setFixedSize(30, 30)
        self._settings_btn.setIcon(
            icons.icon("mdi6.cog-outline", color=icons.TONE_MUTED,
                       color_active=icons.TONE_ACCENT_HOVER)
        )
        self._settings_btn.setIconSize(QSize(18, 18))
        self._settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._settings_btn.clicked.connect(lambda: self.navigate_to("settings"))
        lay.addWidget(self._settings_btn)

        lay.addSpacing(6)

        self._btn_helicon = QPushButton("Helicon")
        self._btn_helicon.setObjectName("Primary")
        self._btn_helicon.setToolTip("Helicon Focus 景深合成")
        self._btn_helicon.setFixedHeight(30)
        icons.set_button_icon(self._btn_helicon, "mdi6.image-filter-center-focus",
                              color=icons.TONE_ON_ACCENT, size=15)
        self._btn_helicon.clicked.connect(self._open_helicon_config)
        self._btn_helicon.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(self._btn_helicon)

        return bar

    def _topbar_divider(self) -> QFrame:
        """A short, subtle vertical hairline that groups topbar clusters."""
        line = QFrame()
        line.setObjectName("TopBarDivider")
        line.setFixedSize(1, 22)
        return line

    def _build_context_bar(self) -> QFrame:
        """Hidden compatibility bar for active-specimen state.

        The old visible strip duplicated information already shown in the
        status bar and workbench header.  Keep its widgets alive because
        refresh_context_bar(), tests, and quick-new wiring still reference them.
        """
        bar = QFrame()
        bar.setObjectName("ContextBar")
        bar.setFixedHeight(0)
        bar.hide()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 0, 20, 0)
        lay.setSpacing(10)

        active_label = QLabel("激活标本")
        active_label.setObjectName("ContextLabel")
        lay.addWidget(active_label)

        self._active_badge = QLabel("无")
        self._active_badge.setObjectName("ActiveBadgeOff")
        lay.addWidget(self._active_badge)

        lay.addStretch()

        # Quick new-specimen shortcut (wired in _quick_new_specimen)
        self._btn_new = QPushButton("新增编号")
        self._btn_new.setObjectName("Outline")
        self._btn_new.setToolTip("开始填写新标本唯一编号")
        icons.set_button_icon(self._btn_new, "mdi6.dna", color=icons.TONE_ACCENT, size=15)
        self._btn_new.clicked.connect(self._quick_new_specimen)
        lay.addWidget(self._btn_new)

        # Provide stub attributes so refresh_context_bar doesn't crash
        # (it checks isEnabled on _btn_compose / _btn_organize)
        self._btn_compose = QPushButton()
        self._btn_compose.hide()
        self._btn_organize = QPushButton()
        self._btn_organize.hide()

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

    def _open_helicon_config(self) -> None:
        """Open the standalone Helicon Focus config dialog (web 顶栏 Helicon)."""
        dlg = getattr(self, "_helicon_config_dlg", None)
        if dlg is None:
            from app.widgets.helicon_config_dialog import HeliconConfigDialog
            dlg = HeliconConfigDialog(self.ctx, parent=self)
            self._helicon_config_dlg = dlg
        else:
            dlg._detect_and_refresh()
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

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
        """Sync topbar project switcher + context bar active badge with current state."""
        from pathlib import Path

        project_dir = getattr(self.ctx, "current_project_dir", None)
        name = Path(project_dir).name if project_dir else "（未选）"
        # Project switcher lives in the topbar now
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
        self._btn_new.setEnabled(has_proj)
        self._btn_compress.setEnabled(has_proj)
        self._btn_helicon.setEnabled(has_proj)

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

    # ── Top-bar project actions ───────────────────────────────────────────

    def _on_new_project(self) -> None:
        """「+ 新建项目」topbar button → ProjectDialog(mode="new")."""
        self._open_project_dialog(mode="new")

    def _on_open_workspace(self) -> None:
        """「+ 打开工作区」topbar button → ProjectDialog(mode="open")."""
        self._open_project_dialog(mode="open")

    def _open_project_dialog(self, mode: str) -> None:
        """Open ProjectDialog, persist result, navigate to workbench."""
        from app.views.project_dialog import ProjectDialog
        from app.views.overview_view import _load_projects, _save_projects

        existing = _load_projects()
        dlg = ProjectDialog(mode=mode, existing_projects=existing, parent=self)
        if dlg.exec() != ProjectDialog.DialogCode.Accepted:
            return
        proj = dlg.result_project()
        if not proj:
            return

        # Persist to user_projects.json (dedup by directory)
        all_projects = _load_projects()
        existing_dirs = {p.get("directory") or p.get("dir") for p in all_projects}
        if proj.get("directory") not in existing_dirs:
            all_projects.append(proj)
            _save_projects(all_projects)

        # Activate project in context and navigate
        self.ctx.current_project_dir = proj.get("directory", "")
        self.navigate_to("workbench")
        self.refresh_context_bar()

        # Notify overview to reload next time it's activated
        ov = self._views.get("overview")
        if ov and hasattr(ov, "_load_projects"):
            ov._load_projects()

    # ── Status bar public API ─────────────────────────────────────────────

    def set_status_specimen(self, text: str) -> None:
        self._status_specimen.setText(text)

    def set_status_collab(self, text: str) -> None:
        self._status_collab.setText(text)

    def set_status_helicon(self, text: str) -> None:
        self._status_helicon.setText(text)

    # ── Screenshot ────────────────────────────────────────────────────────

    def _wire_screenshot(self) -> None:
        """Build the screenshot controller and bind Alt+A (= macOS Option+A).

        ApplicationShortcut context → Alt+A fires whenever any app window has
        focus, never while the app is in the background. The controller is the
        reusable entry point shared with the topbar 截图 button's mode menu.
        """
        from app.widgets.screenshot_controller import ScreenshotController

        self._shot_ctrl = ScreenshotController(
            self,
            ctx=self.ctx,
            view_provider=lambda: self._stack.currentWidget(),
            status_cb=self.set_status_specimen,
        )
        sc = QShortcut(QKeySequence("Alt+A"), self)
        sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
        sc.setAutoRepeat(False)  # holding the key must fire once, not stack overlays
        sc.activated.connect(self._shot_ctrl.capture_region)
        self._screenshot_shortcut = sc

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
        # Gracefully stop collaboration service so mDNS un-registers before exit
        svc = getattr(self.ctx, "collab_service", None)
        if svc is not None:
            try:
                svc.stop()
            except Exception:  # noqa: BLE001
                pass
        super().closeEvent(event)

    def _wire_collab_status_bar(self) -> None:
        """Connect CollabService signals to the status bar segment.

        Called once from __init__ after the status bar is built.  Safe to
        call when collab_service is None — becomes a no-op.
        """
        svc = getattr(self.ctx, "collab_service", None)
        if svc is None:
            return
        svc.server_ready.connect(
            lambda port: self.set_status_collab(f"协作: 端口 {port}")
        )
        svc.peers_changed.connect(self._refresh_collab_status)
        svc.sync_error.connect(
            lambda msg: self.set_status_collab(f"协作: 错误")
        )

    def _refresh_collab_status(self) -> None:
        svc = getattr(self.ctx, "collab_service", None)
        if svc is None:
            self.set_status_collab("协作: 未启动")
            return
        n = len(svc.peers())
        if n:
            self.set_status_collab(f"协作: 🟢 {n} 台在线")
        else:
            self.set_status_collab("协作: ⚪ 未发现其他设备")


# ── Nav glyphs (view_id → qtawesome Material Design Icon) ───────────────────

_NAV_GLYPHS: dict[str, str] = {
    "workbench": "mdi6.microscope",
    "overview":  "mdi6.view-dashboard-outline",
    "labels":    "mdi6.tag-outline",
    "worms":     "mdi6.waves",
    "taxonomy":  "mdi6.dna",
    "coords":    "mdi6.map-marker-outline",
    "collection_map": "mdi6.map-marker-multiple",
    "collab":    "mdi6.chart-bar-stacked",
    "settings":  "mdi6.cog-outline",
}


# ── Helpers ───────────────────────────────────────────────────────────────

def _separator() -> QLabel:
    sep = QLabel("·")
    sep.setObjectName("StatusSegment")
    return sep
