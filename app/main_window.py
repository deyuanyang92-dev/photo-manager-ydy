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

import os
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import QObject, QSize, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QCloseEvent, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.config import icons
from app.config.i18n import tr
from app.config.version import APP_VERSION
from app.utils import ui
from app.views.base_view import BaseView

if TYPE_CHECKING:
    from app.app_context import AppContext

_K_SCREENSHOT_TOOL_ENABLED = "ui/screenshot_tool_enabled"
_K_SCREENSHOT_DEFAULT_MIGRATED = "ui/screenshot_tool_default_migrated"


class _UpdateWorker(QObject):
    progress = pyqtSignal(int, str)
    ready = pyqtSignal(object)
    no_update = pyqtSignal(object)
    failed = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, current_version: str) -> None:
        super().__init__()
        self._current_version = current_version

    def run(self) -> None:
        try:
            from app.services import update_service

            self.progress.emit(4, "正在检查 GitHub 最新版本...")
            release = update_service.fetch_latest_release()
            if not update_service.is_newer_version(
                release.tag_name, self._current_version
            ):
                self.no_update.emit(release)
                return
            self.progress.emit(10, f"发现 {release.tag_name}，正在下载...")

            def _on_download(done: int, total: int) -> None:
                if total > 0:
                    pct = 10 + min(80, int(done * 80 / total))
                else:
                    pct = 18
                self.progress.emit(pct, "正在下载更新包...")

            prepared = update_service.prepare_update(
                release,
                progress_cb=_on_download,
            )
            self.progress.emit(100, "更新包已准备，正在重启升级...")
            self.ready.emit(prepared)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


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
        self._view_classes: list[object] = []         # class/spec registration order
        self._nav_buttons: list[QPushButton] = []      # registration order
        self._nav_menu_actions: dict[str, QAction] = {}
        self._nav_pin_actions: dict[str, QAction] = {}
        self._nav_group_menus: dict[str, QMenu] = {}
        self._shot_actions: dict[str, QAction] = {}
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)

        self.setWindowTitle(tr("标本影像"))
        self.resize(1440, 900)
        # Minimum width must stay below common small/remote-desktop screens
        # (e.g. 1024×768). At 1040 the window can't shrink to fit a 1024-wide
        # screen, so its right edge — the screenshot/settings/Helicon cluster —
        # is pushed off-screen and the topbar reads as crowded.
        self.setMinimumSize(940, 600)

        self._migrate_screenshot_tool_default()
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
        bar.setFixedHeight(58)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 0, 16, 0)
        lay.setSpacing(0)

        # Brand: vector microscope mark + serif wordmark
        brand_mark = QLabel()
        brand_mark.setObjectName("BrandMark")
        brand_mark.setPixmap(
            icons.icon("mdi6.microscope", color=icons.TONE_ACCENT).pixmap(22, 22)
        )
        brand_mark.setFixedSize(24, 24)
        lay.addWidget(brand_mark)
        lay.addSpacing(8)
        self._brand = QLabel(tr("标本影像管理"))
        self._brand.setObjectName("BrandWord")
        self._brand.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        lay.addWidget(self._brand)

        lay.addSpacing(18)

        # Workspace path picker: current folder + visit history + open/new actions.
        from app.widgets.workspace_breadcrumb import WorkspaceBreadcrumb
        self._project_switcher = WorkspaceBreadcrumb(self.ctx)
        self._project_switcher.setMaximumWidth(470)
        self._project_switcher.navigate_requested.connect(self.navigate_to)
        self._project_switcher.workspace_changed.connect(
            self._on_breadcrumb_switch)
        self._project_switcher.new_workspace_requested.connect(self._on_new_project)
        self._project_switcher.open_workspace_requested.connect(self._on_open_workspace)
        lay.addWidget(self._project_switcher)

        self._btn_compress = QAction(tr("归档"), self)
        self._btn_compress.setEnabled(False)

        lay.addSpacing(14)

        # Segmented nav row (buttons added by register_view)
        self._nav_row = QHBoxLayout()
        self._nav_row.setContentsMargins(0, 0, 0, 0)
        self._nav_row.setSpacing(2)
        nav_wrap = QWidget()
        nav_wrap.setObjectName("NavWrap")
        nav_wrap.setLayout(self._nav_row)
        nav_wrap.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
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

        lay.addSpacing(8)

        self._nav_menu_btn = QToolButton()
        self._nav_menu_btn.setObjectName("NavMenuButton")
        self._nav_menu_btn.setText(tr("工具箱"))
        self._nav_menu_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._nav_menu_btn.setAccessibleName(tr("工具箱"))
        self._nav_menu_btn.setToolTip(tr("按功能分组打开页面，并选择哪些入口固定在顶栏"))
        self._nav_menu_btn.setFixedSize(92, 34)
        self._nav_menu_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._nav_menu_btn.setIcon(
            icons.icon("mdi6.toolbox-outline", color=icons.TONE_MUTED,
                       color_active=icons.TONE_ACCENT_HOVER)
        )
        self._nav_menu_btn.setIconSize(QSize(17, 17))
        self._nav_menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._nav_menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._nav_menu = QMenu(self._nav_menu_btn)
        self._nav_menu.setObjectName("NavMenu")
        self._nav_menu.setMinimumWidth(176)
        for group_key, group in _NAV_GROUPS.items():
            group_menu = self._nav_menu.addMenu(tr(group["title"]))
            group_menu.setObjectName("NavSubMenu")
            group_menu.setMinimumWidth(188)
            group_menu.setIcon(icons.icon(group["icon"], color=icons.TONE_MUTED))
            self._nav_group_menus[group_key] = group_menu
        self._build_screenshot_menu()
        self._build_update_action()
        self._nav_menu.addSeparator()
        self._nav_pin_menu = self._nav_menu.addMenu(tr("固定到顶栏"))
        self._nav_pin_menu.setObjectName("NavSubMenu")
        self._nav_pin_menu.setMinimumWidth(188)
        self._nav_pin_menu.setIcon(icons.icon("mdi6.pin-outline", color=icons.TONE_MUTED))
        self._nav_menu_btn.setMenu(self._nav_menu)
        self._shot_btn = QToolButton()
        self._shot_btn.setObjectName("ScreenshotButton")
        self._shot_btn.setText(tr("截图"))
        self._shot_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._shot_btn.setAccessibleName(tr("截图"))
        self._shot_btn.setToolTip(tr("截图工具：点击区域截图，下拉选择其他模式"))
        self._shot_btn.setFixedSize(86, 34)
        self._shot_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._shot_btn.setIcon(
            icons.icon("mdi6.scissors-cutting", color=icons.TONE_MUTED,
                       color_active=icons.TONE_ACCENT_HOVER)
        )
        self._shot_btn.setIconSize(QSize(17, 17))
        self._shot_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._shot_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self._shot_btn.setMenu(self._shot_menu)
        self._shot_btn.clicked.connect(lambda _=False: self._shot_ctrl.capture_region())
        lay.addWidget(self._shot_btn)

        lay.addSpacing(6)
        lay.addWidget(self._nav_menu_btn)

        lay.addSpacing(6)
        self._update_btn = QToolButton()
        self._update_btn.setObjectName("NavMenuButton")
        self._update_btn.setText(tr("更新"))
        self._update_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._update_btn.setAccessibleName(tr("软件更新"))
        self._update_btn.setToolTip(tr("检查并安装最新版本"))
        self._update_btn.setFixedSize(78, 34)
        self._update_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._update_btn.setIcon(
            icons.icon("mdi6.update", color=icons.TONE_MUTED,
                       color_active=icons.TONE_ACCENT_HOVER)
        )
        self._update_btn.setIconSize(QSize(17, 17))
        self._update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_btn.clicked.connect(self._on_upgrade_requested)
        lay.addWidget(self._update_btn)

        lay.addSpacing(10)
        lay.addWidget(self._topbar_divider())
        lay.addSpacing(10)

        self._settings_btn = QPushButton()
        self._settings_btn.setObjectName("IconGhost")
        self._settings_btn.setToolTip(tr("配置"))
        self._settings_btn.setFixedSize(34, 34)
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
        self._btn_helicon.setToolTip(tr("Helicon Focus 景深合成"))
        self._btn_helicon.setFixedSize(104, 34)
        self._btn_helicon.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
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

        self._active_label = QLabel(tr("激活标本"))
        self._active_label.setObjectName("ContextLabel")
        lay.addWidget(self._active_label)

        self._active_badge = QLabel(tr("无"))
        self._active_badge.setObjectName("ActiveBadgeOff")
        lay.addWidget(self._active_badge)

        lay.addStretch()

        # Quick new-specimen shortcut (wired in _quick_new_specimen)
        self._btn_new = QPushButton(tr("新增编号"))
        self._btn_new.setObjectName("Outline")
        self._btn_new.setToolTip(tr("开始填写新标本唯一编号"))
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
        self._status_specimen = QLabel(tr("未激活标本"))
        self._status_specimen.setObjectName("StatusSegment")
        bar.addWidget(self._status_specimen)

        bar.addWidget(_separator())

        # Segment 2 — collaboration
        self._status_collab = QLabel(tr("协作: 离线"))
        self._status_collab.setObjectName("StatusSegment")
        bar.addWidget(self._status_collab)

        bar.addWidget(_separator())

        # Segment 3 — Helicon
        self._status_helicon = QLabel(tr("Helicon: 未检测"))
        self._status_helicon.setObjectName("StatusSegment")
        bar.addWidget(self._status_helicon)

    # ── View registry ─────────────────────────────────────────────────────

    @staticmethod
    def _view_ref_id(view_ref: object) -> str:
        return str(getattr(view_ref, "view_id", ""))

    @staticmethod
    def _view_ref_title(view_ref: object) -> str:
        return str(getattr(view_ref, "nav_title", ""))

    @staticmethod
    def _resolve_view_ref(view_ref: object) -> type:
        view_cls = view_ref if isinstance(view_ref, type) else view_ref.resolve()
        if not issubclass(view_cls, BaseView):
            raise TypeError("view_ref must resolve to a BaseView subclass")
        return view_cls

    def register_view(self, view_cls: object) -> None:
        """Register a BaseView subclass as a top-nav segment + stack page.

        The segment button is appended to the top bar in registration order;
        the view itself is built **lazily** on first activation (see
        ``_ensure_view``) so startup does not pay to construct every page —
        only the page the user actually opens. Nothing reads stack *indices*
        (navigation is keyed by ``view_id`` and ``setCurrentWidget``), so the
        old "stack index == nav order" coupling is no longer required.

        Parameters
        ----------
        view_cls:
            A subclass of BaseView with view_id / nav_title / nav_icon.
        """
        if isinstance(view_cls, type):
            assert issubclass(view_cls, BaseView), "view_cls must subclass BaseView"
        else:
            assert callable(getattr(view_cls, "resolve", None)), "view_cls must be a BaseView subclass or lazy view spec"
        view_id = self._view_ref_id(view_cls)
        nav_title = self._view_ref_title(view_cls)
        idx = len(self._view_classes)
        self._view_classes.append(view_cls)

        # Top-nav segment button — vector glyph + title, accent when active.
        btn = QPushButton(tr(nav_title))
        btn.setObjectName("NavSegment")
        btn.setCheckable(True)
        btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(tr(nav_title))
        glyph = _NAV_GLYPHS.get(view_id, "mdi6.circle-outline")
        btn.setIcon(
            icons.icon(glyph, color=icons.TONE_MUTED,
                       color_active=icons.TONE_ACCENT_HOVER)
        )
        btn.setIconSize(QSize(16, 16))
        btn.setProperty("view_id", view_id)
        btn.clicked.connect(lambda _=False, i=idx: self._activate_index(i))
        self._nav_group.addButton(btn, idx)
        self._nav_buttons.append(btn)
        self._nav_row.addWidget(btn)
        self._add_view_to_nav_menu(view_cls, idx)
        btn.setVisible(self._is_nav_pinned(view_id))
        # View is NOT built here — see _ensure_view (lazy, first-activation).

    def _add_view_to_nav_menu(self, view_cls: object, idx: int) -> None:
        view_id = self._view_ref_id(view_cls)
        nav_title = self._view_ref_title(view_cls)
        group_key = _NAV_GROUP_FOR_VIEW.get(view_id, "tools")
        menu = self._nav_group_menus.get(group_key)
        if menu is None:
            group = _NAV_GROUPS[group_key]
            menu = self._nav_menu.addMenu(tr(group["title"]))
            menu.setObjectName("NavSubMenu")
            menu.setMinimumWidth(188)
            menu.setIcon(icons.icon(group["icon"], color=icons.TONE_MUTED))
            self._nav_group_menus[group_key] = menu

        glyph = _NAV_GLYPHS.get(view_id, "mdi6.circle-outline")
        action = QAction(
            icons.icon(glyph, color=icons.TONE_MUTED, color_active=icons.TONE_ACCENT_HOVER),
            tr(nav_title),
            self,
        )
        action.setCheckable(True)
        action.setToolTip(tr(nav_title))
        action.triggered.connect(lambda _=False, i=idx: self._activate_index(i))
        menu.addAction(action)
        self._nav_menu_actions[view_id] = action
        self._keep_screenshot_last_in_tools()

        self._rebuild_nav_pin_menu()

    def _build_screenshot_menu(self) -> None:
        tools_menu = self._nav_group_menus["tools"]
        self._shot_menu = tools_menu.addMenu(tr("截图"))
        self._shot_menu.setObjectName("NavSubMenu")
        self._shot_menu.setMinimumWidth(190)
        self._shot_menu.setIcon(
            icons.icon("mdi6.scissors-cutting", color=icons.TONE_MUTED,
                       color_active=icons.TONE_ACCENT_HOVER)
        )
        self._shot_menu.setToolTipsVisible(True)

        shot_specs = [
            ("region", "区域截图", "框选屏幕区域", lambda: self._shot_ctrl.capture_region()),
            ("fullscreen", "全屏截图", "截取整个屏幕", lambda: self._shot_ctrl.capture_fullscreen()),
            ("window", "当前窗口", "截取当前窗口", lambda: self._shot_ctrl.capture_window()),
            ("view", "当前页面", "截取当前应用页面", lambda: self._shot_ctrl.capture_view()),
        ]
        for key, text, tip, callback in shot_specs:
            action = QAction(tr(text), self)
            action.setToolTip(tr(tip))
            action.triggered.connect(callback)
            self._shot_menu.addAction(action)
            self._shot_actions[key] = action
        self._update_screenshot_tooltip()

    def _build_update_action(self) -> None:
        system_menu = self._nav_group_menus["system"]
        self._update_action = QAction(
            icons.icon("mdi6.update", color=icons.TONE_MUTED,
                       color_active=icons.TONE_ACCENT_HOVER),
            tr("软件更新"),
            self,
        )
        self._update_action.setToolTip(tr("下载 GitHub 最新版本并自动重启升级"))
        self._update_action.triggered.connect(self._on_upgrade_requested)
        system_menu.addAction(self._update_action)

    def _keep_screenshot_last_in_tools(self) -> None:
        tools_menu = self._nav_group_menus.get("tools")
        shot_menu = getattr(self, "_shot_menu", None)
        if tools_menu is None or shot_menu is None:
            return
        shot_action = shot_menu.menuAction()
        tools_menu.removeAction(shot_action)
        tools_menu.addAction(shot_action)

    def _rebuild_nav_pin_menu(self) -> None:
        self._nav_pin_menu.clear()
        self._nav_pin_actions.clear()
        for i, cls in enumerate(self._view_classes):
            view_id = self._view_ref_id(cls)
            action = QAction(tr(self._view_ref_title(cls)), self)
            action.setCheckable(True)
            action.setChecked(self._is_nav_pinned(view_id))
            action.toggled.connect(lambda checked, idx=i: self._set_nav_pinned(idx, checked))
            self._nav_pin_menu.addAction(action)
            self._nav_pin_actions[view_id] = action

    def _nav_pins_setting(self) -> set[str]:
        raw = self.ctx.settings._qs.value("ui/topbar_pinned_views", "", type=str) or ""
        if not raw:
            return set(_DEFAULT_PINNED_NAV)
        if raw == "__none__":
            return set()
        return {part for part in raw.split(",") if part}

    def _save_nav_pins(self, pins: set[str]) -> None:
        ordered = [self._view_ref_id(cls) for cls in self._view_classes if self._view_ref_id(cls) in pins]
        self.ctx.settings._qs.setValue("ui/topbar_pinned_views", ",".join(ordered) or "__none__")

    def _is_nav_pinned(self, view_id: str) -> bool:
        return view_id in self._nav_pins_setting()

    def _set_nav_pinned(self, idx: int, checked: bool) -> None:
        if idx < 0 or idx >= len(self._view_classes):
            return
        view_id = self._view_ref_id(self._view_classes[idx])
        pins = self._nav_pins_setting()
        if checked:
            pins.add(view_id)
        else:
            pins.discard(view_id)
        self._save_nav_pins(pins)
        self._nav_buttons[idx].setVisible(checked)
        action = self._nav_pin_actions.get(view_id)
        if action is not None and action.isChecked() != checked:
            action.setChecked(checked)

    def _ensure_view(self, view_cls: object) -> BaseView:
        """Build *view_cls* on first request, then cache + add to the stack.

        Idempotent: repeat calls return the cached instance. This is the single
        construction point for every page, so deferring it here spreads the
        ~1.3 s "build all views" cost across first visits instead of paying it
        up front at launch.
        """
        view_id = self._view_ref_id(view_cls)
        view = self._views.get(view_id)
        if view is None:
            resolved_cls = self._resolve_view_ref(view_cls)
            view = resolved_cls(self.ctx)
            self._views[view_id] = view
            self._stack.addWidget(view)
        return view

    def _recolor_nav_icons(self, active_idx: int) -> None:
        """Tint the active segment's glyph accent; others stay muted."""
        for i, b in enumerate(self._nav_buttons):
            vid = b.property("view_id")
            glyph = _NAV_GLYPHS.get(vid, "mdi6.circle-outline")
            tone = icons.TONE_ACCENT_HOVER if i == active_idx else icons.TONE_MUTED
            b.setIcon(icons.icon(glyph, color=tone,
                                 color_active=icons.TONE_ACCENT_HOVER))

    def retranslate_ui(self) -> None:
        """Apply the active language to the shell and loaded views immediately."""
        self.setWindowTitle(tr("标本影像"))
        self._brand.setText(tr("标本影像管理"))
        self._project_switcher.refresh()
        self._nav_menu_btn.setText(tr("工具箱"))
        self._nav_menu_btn.setAccessibleName(tr("工具箱"))
        self._nav_menu_btn.setToolTip(tr("按功能分组打开页面，并选择哪些入口固定在顶栏"))
        self._shot_btn.setText(tr("截图"))
        self._shot_btn.setAccessibleName(tr("截图"))
        self._update_btn.setText(tr("更新"))
        self._update_btn.setAccessibleName(tr("软件更新"))
        self._update_btn.setToolTip(tr("检查并安装最新版本"))

        for group_key, group in _NAV_GROUPS.items():
            menu = self._nav_group_menus.get(group_key)
            if menu is not None:
                menu.setTitle(tr(group["title"]))
        self._nav_pin_menu.setTitle(tr("固定到顶栏"))

        self._btn_compress.setText(tr("归档"))
        self._btn_compress.setToolTip(tr("整理归档（JPG→ZIP）"))
        self._settings_btn.setToolTip(tr("配置"))
        self._btn_helicon.setToolTip(tr("Helicon Focus 景深合成"))
        self._active_label.setText(tr("激活标本"))
        self._btn_new.setText(tr("新增编号"))
        self._btn_new.setToolTip(tr("开始填写新标本唯一编号"))

        for i, cls in enumerate(self._view_classes):
            if i < len(self._nav_buttons):
                self._nav_buttons[i].setText(tr(self._view_ref_title(cls)))
                self._nav_buttons[i].setToolTip(tr(self._view_ref_title(cls)))
            view_id = self._view_ref_id(cls)
            menu_action = self._nav_menu_actions.get(view_id)
            if menu_action is not None:
                menu_action.setText(tr(self._view_ref_title(cls)))
                menu_action.setToolTip(tr(self._view_ref_title(cls)))
            pin_action = self._nav_pin_actions.get(view_id)
            if pin_action is not None:
                pin_action.setText(tr(self._view_ref_title(cls)))

        if getattr(self, "_shot_menu", None) is not None:
            self._shot_menu.setTitle(tr("截图"))
        if getattr(self, "_update_action", None) is not None:
            self._update_action.setText(tr("软件更新"))
            self._update_action.setToolTip(tr("下载 GitHub 最新版本并自动重启升级"))
        for key, text, tip in (
            ("fullscreen", "全屏截图", "截取整个屏幕"),
            ("window", "当前窗口", "截取当前窗口"),
            ("view", "当前页面", "截取当前应用页面"),
        ):
            action = self._shot_actions.get(key)
            if action is not None:
                action.setText(tr(text))
                action.setToolTip(tr(tip))
        self._update_screenshot_tooltip()

        self.refresh_context_bar()
        if not self._views:
            self._status_collab.setText(tr("协作: 离线"))
            self._status_helicon.setText(tr("Helicon: 未检测"))

        for view in list(self._views.values()):
            handler = getattr(view, "retranslate_ui", None)
            if callable(handler):
                handler()

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

    def _on_upgrade_requested(self) -> None:
        from app.services import update_service

        if not update_service.can_self_update():
            QMessageBox.information(
                self,
                tr("软件更新"),
                tr("一键升级只支持 Windows 打包版。\n\n"
                   "当前像是源码/开发环境运行，不会自动替换程序目录。"),
            )
            return

        reply = QMessageBox.question(
            self,
            tr("软件更新"),
            tr("将下载 GitHub 最新 Windows 包，准备完成后自动关闭当前程序、替换文件并重启。\n\n"
               "当前版本：{}\n是否继续？").format(APP_VERSION),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        progress = QProgressDialog(tr("正在准备更新..."), "", 0, 100, self)
        progress.setWindowTitle(tr("软件更新"))
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        progress.show()

        thread = QThread(self)
        worker = _UpdateWorker(APP_VERSION)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(lambda value, text: self._set_update_progress(progress, value, text))
        worker.ready.connect(lambda prepared: self._apply_prepared_update(prepared, progress))
        worker.no_update.connect(lambda release: self._show_no_update(progress, release))
        worker.failed.connect(lambda message: self._show_update_failed(progress, message))
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._update_thread = thread
        self._update_worker = worker
        thread.start()

    def _set_update_progress(self, dialog: QProgressDialog, value: int, text: str) -> None:
        dialog.setLabelText(tr(text))
        dialog.setValue(value)

    def _show_update_failed(self, dialog: QProgressDialog, message: str) -> None:
        dialog.close()
        QMessageBox.warning(
            self,
            tr("软件更新失败"),
            tr("未能完成自动更新：\n{}").format(message),
        )

    def _show_no_update(self, dialog: QProgressDialog, release) -> None:
        dialog.close()
        QMessageBox.information(
            self,
            tr("软件更新"),
            tr("当前已是最新版本。\n\n当前版本：{}\nGitHub 最新：{}").format(
                APP_VERSION,
                getattr(release, "tag_name", "") or APP_VERSION,
            ),
        )

    def _apply_prepared_update(self, prepared, dialog: QProgressDialog) -> None:
        from app.services import update_service

        try:
            update_service.launch_update_script(prepared.script_path)
        except Exception as exc:  # noqa: BLE001
            self._show_update_failed(dialog, str(exc))
            return
        dialog.setValue(100)
        dialog.setLabelText(tr("更新脚本已启动，正在关闭当前版本..."))
        self.statusBar().showMessage(tr("正在升级到 {}...").format(prepared.release.tag_name), 3000)
        QApplication.quit()

    def navigate_to(self, view_id: str) -> None:
        """Programmatically switch to the view with the given view_id."""
        for i, cls in enumerate(self._view_classes):
            if self._view_ref_id(cls) == view_id:
                self._activate_index(i)
                return
        self.statusBar().showMessage(tr("未找到页面: {}").format(view_id), 4000)

    def _activate_index(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._view_classes):
            return
        btn = self._nav_buttons[idx]
        if not btn.isChecked():
            btn.setChecked(True)
        for i, cls in enumerate(self._view_classes):
            action = self._nav_menu_actions.get(self._view_ref_id(cls))
            if action is not None:
                action.setChecked(i == idx)
        self._recolor_nav_icons(idx)
        view_cls = self._view_classes[idx]
        with ui.busy_cursor():
            view = self._ensure_view(view_cls)
            if view:
                self._stack.setCurrentWidget(view)
                view.on_activate()
        self.ctx.settings.last_nav_index = idx
        self.refresh_context_bar()
        self.statusBar().showMessage(tr("已打开: {}").format(tr(self._view_ref_title(view_cls))), 1800)

    # ── Context bar ────────────────────────────────────────────────────────

    def _on_breadcrumb_switch(self, path: str) -> None:
        """面包屑 ◀▶/下拉 切换工作区后：刷新顶栏状态 + 让当前页重读新工作区."""
        self.refresh_context_bar()
        view = self._stack.currentWidget()
        if view is not None and hasattr(view, "on_activate"):
            try:
                with ui.busy_cursor():
                    view.on_activate()
            except Exception as exc:  # noqa: BLE001
                ui.exception(
                    self,
                    tr("工作区切换失败"),
                    exc,
                    text=tr("已切换工作区，但当前页面刷新失败。"),
                    hint=tr("可先切到其他页面再回来；详细信息可用于排查插件、数据库或路径问题。"),
                )
        self.set_status_specimen(tr("已切换工作区: {}").format(
            os.path.basename(path)))

    def refresh_context_bar(self) -> None:
        """Sync topbar project switcher + context bar active badge with current state."""
        project_dir = getattr(self.ctx, "current_project_dir", None)
        # Project switcher lives in the topbar now (breadcrumb rebuilds from ctx)
        self._project_switcher.refresh()

        active_uid = self._lookup_active_uid()
        if active_uid:
            short = active_uid.split("-")[3] if active_uid.count("-") >= 3 else active_uid
            self._active_badge.setText(short)
            self._active_badge.setObjectName("ActiveBadgeOn")
            self.set_status_specimen(tr("激活: {}").format(active_uid))
        else:
            self._active_badge.setText(tr("无"))
            self._active_badge.setObjectName("ActiveBadgeOff")
            self.set_status_specimen(tr("未激活标本"))
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
            except Exception as exc:  # noqa: BLE001
                ui.exception(
                    self,
                    tr("新增编号失败"),
                    exc,
                    text=tr("无法打开新增编号流程。"),
                    hint=tr("请确认当前项目数据库可访问，或把详细信息发给维护者。"),
                )

    # ── Top-bar workspace folder actions ──────────────────────────────────

    def _on_new_project(self) -> None:
        """Folder menu: create a photo workspace."""
        self._open_project_dialog(mode="new")

    def _on_open_workspace(self) -> None:
        """Folder menu: open an existing folder as a photo workspace."""
        self._open_project_dialog(mode="open")

    def _open_project_dialog(self, mode: str) -> None:
        """Open ProjectDialog, persist result, navigate to workbench."""
        from app.views.project_dialog import ProjectDialog
        from app.services.project_service import (
            default_user_projects_json_path,
            save_project_descriptor,
        )
        from app.views.overview_view import _load_projects

        existing = _load_projects()
        dlg = ProjectDialog(mode=mode, existing_projects=existing, parent=self)
        if dlg.exec() != ProjectDialog.DialogCode.Accepted:
            return
        proj = dlg.result_project()
        if not proj:
            return

        try:
            with ui.busy_cursor():
                # Persist to user_projects.json (dedup by directory, keep metadata).
                save_project_descriptor(
                    default_user_projects_json_path(),
                    proj,
                    existing_projects=existing,
                )

                # Activate project via the unified entry path
                from app.services.project_service import enter_workspace
                enter_workspace(
                    self.ctx,
                    proj.get("directory", ""),
                    projects_json_path=default_user_projects_json_path(),
                )
                self.navigate_to("workbench")
                self.refresh_context_bar()

                # Notify overview to reload next time it's activated
                ov = self._views.get("overview")
                if ov and hasattr(ov, "_load_projects"):
                    ov._load_projects()
            self.statusBar().showMessage(
                tr("已打开文件夹: {}").format(os.path.basename(proj.get("directory", ""))),
                4000,
            )
        except Exception as exc:  # noqa: BLE001
            ui.exception(
                self,
                tr("打开文件夹失败"),
                exc,
                text=tr("文件夹已创建/选择，但写入最近列表或进入工作台时失败。"),
                hint=tr("请检查项目路径、磁盘权限和 _data/project.db 是否可写。"),
            )

    # ── Status bar public API ─────────────────────────────────────────────

    def set_status_specimen(self, text: str) -> None:
        self._status_specimen.setText(text)

    def set_status_collab(self, text: str) -> None:
        self._status_collab.setText(text)

    def set_status_helicon(self, text: str) -> None:
        self._status_helicon.setText(text)

    # ── Screenshot ────────────────────────────────────────────────────────

    def screenshot_shortcut_seq(self) -> str:
        """Saved screenshot key sequence string, defaulting to Alt+A."""
        seq = self.ctx.settings._qs.value("shortcuts/screenshot_region", "", type=str) or ""
        return seq or "Alt+A"

    def _migrate_screenshot_tool_default(self) -> None:
        """Restore screenshot visibility once for configs saved by older builds."""
        qs = self.ctx.settings._qs
        migrated = qs.value(_K_SCREENSHOT_DEFAULT_MIGRATED, "", type=str)
        if str(migrated).lower() == "true":
            return
        qs.setValue(_K_SCREENSHOT_DEFAULT_MIGRATED, "true")
        qs.setValue(_K_SCREENSHOT_TOOL_ENABLED, "true")

    def screenshot_tool_enabled(self) -> bool:
        """Whether the screenshot menu and hotkey are enabled. Default: on."""
        raw = self.ctx.settings._qs.value(_K_SCREENSHOT_TOOL_ENABLED, "true")
        return str(raw).lower() == "true"

    def set_screenshot_tool_enabled(self, enabled: bool) -> None:
        """Persist and apply screenshot tool visibility / hotkey binding."""
        self.ctx.settings._qs.setValue(
            _K_SCREENSHOT_TOOL_ENABLED,
            "true" if enabled else "false",
        )
        self.ctx.settings.flush_to_disk()
        self._apply_screenshot_tool_enabled()

    def _apply_screenshot_tool_enabled(self) -> None:
        enabled = self.screenshot_tool_enabled()
        shot_menu = getattr(self, "_shot_menu", None)
        if shot_menu is not None:
            shot_menu.menuAction().setVisible(enabled)
            shot_menu.setEnabled(enabled)
        shot_btn = getattr(self, "_shot_btn", None)
        if shot_btn is not None:
            shot_btn.setVisible(enabled)
            shot_btn.setEnabled(enabled)
        for action in self._shot_actions.values():
            action.setEnabled(enabled)

        shortcut = getattr(self, "_screenshot_shortcut", None)
        if shortcut is not None:
            shortcut.setEnabled(enabled)

        hotkey = getattr(self, "_global_hotkey", None)
        if hotkey is not None:
            if enabled:
                hotkey.set_hotkey(self.screenshot_shortcut_seq())
            else:
                hotkey.stop()

    def _update_screenshot_tooltip(self, seq: str | None = None) -> None:
        """Keep the visible screenshot entry distinct from Settings."""
        seq = seq or self.screenshot_shortcut_seq()
        region = self._shot_actions.get("region")
        if region is not None:
            region.setText(f"{tr('区域截图')}    {seq}")
            region.setToolTip(f"{seq} {tr('区域截图')}")
        shot_menu = getattr(self, "_shot_menu", None)
        if shot_menu is not None:
            shot_menu.menuAction().setToolTip(tr("截图工具（{} 区域截图）").format(seq))

        shot_btn = getattr(self, "_shot_btn", None)
        if shot_btn is not None:
            shot_btn.setToolTip(
                tr("截图工具：点击区域截图（{}），下拉选择全屏/窗口/页面").format(seq)
            )

    def _wire_screenshot(self) -> None:
        """Build the screenshot controller and bind the screenshot hotkey.

        Two bindings for the same key:
        - In-app QShortcut (ApplicationShortcut) → fires whenever any app window
          has focus, on any page. No dependency.
        - Global OS hotkey via pynput (optional) → also fires while the app is
          minimised / another program is focused. Degrades to no-op if pynput
          is absent or the platform (Wayland) blocks global grabs.

        The key is user-configurable in 设置→界面→快捷键; rebind_screenshot_shortcut
        re-applies both bindings live.
        """
        from app.widgets.screenshot_controller import ScreenshotController
        from app.utils.global_hotkey import GlobalHotkeyManager

        self._shot_ctrl = ScreenshotController(
            self,
            ctx=self.ctx,
            view_provider=lambda: self._stack.currentWidget(),
            status_cb=self.set_status_specimen,
        )

        seq = self.screenshot_shortcut_seq()
        sc = QShortcut(QKeySequence(seq), self)
        sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
        sc.setAutoRepeat(False)  # holding the key must fire once, not stack overlays
        sc.activated.connect(self._shot_ctrl.capture_region)
        self._screenshot_shortcut = sc

        # Global system-wide hotkey (queued → main-thread capture_region).
        self._global_hotkey = GlobalHotkeyManager(self)
        self._global_hotkey.triggered.connect(
            self._shot_ctrl.capture_region, Qt.ConnectionType.QueuedConnection
        )
        if self.screenshot_tool_enabled():
            self._global_hotkey.set_hotkey(seq)
        self._apply_screenshot_tool_enabled()

    def rebind_screenshot_shortcut(self, seq: str) -> None:
        """Re-apply the screenshot key (in-app + global) after a settings change."""
        seq = seq or "Alt+A"
        if getattr(self, "_screenshot_shortcut", None) is not None:
            self._screenshot_shortcut.setKey(QKeySequence(seq))
        if getattr(self, "_global_hotkey", None) is not None:
            if self.screenshot_tool_enabled():
                self._global_hotkey.set_hotkey(seq)
            else:
                self._global_hotkey.stop()
        self._update_screenshot_tooltip(seq)

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
        self.ctx.settings.flush_to_disk()
        # Silent metadata safety net: snapshot the current project's tiny
        # project.db + the recent-projects list to the local user-data dir
        # (per-project model keeps the only live copy on possibly-removable
        # disks). Never blocks shutdown — backup_service swallows all errors.
        try:
            from app.services.backup_service import (
                snapshot_project,
                snapshot_projects_json,
            )
            from app.services.project_service import default_user_projects_json_path
            cur = getattr(self.ctx, "current_project_dir", None)
            if cur:
                snapshot_project(cur)
            snapshot_projects_json(default_user_projects_json_path())
        except Exception:  # noqa: BLE001
            pass
        self._teardown()
        super().closeEvent(event)

    def _teardown(self) -> None:
        """Release all background resources so the process can actually exit.

        Idempotent: safe to call from both closeEvent and app.aboutToQuit.
        This is the fix for the "close → reopen → broken, must reboot" bug:
        the per-project SQLite connections are opened in WAL mode and cached
        globally in db_manager._db_cache; on WSL/drvfs (/mnt/...) the advisory
        lock + -wal/-shm sidecars persist across an unclean exit, so the *next*
        launch can't reopen the project DB until the OS reclaims the zombie
        handle (a reboot). Closing every cached connection here checkpoints +
        releases them deterministically.
        """
        if getattr(self, "_torn_down", False):
            return
        self._torn_down = True
        # Stop every view's background QThread / subprocess FIRST (Helicon
        # compose, WoRMS batch job, …) so none can keep a SQLite handle alive
        # while we close the DB connections below.
        for view in list(getattr(self, "_views", {}).values()):
            try:
                view.stop_background_work()
            except Exception:  # noqa: BLE001
                pass
        # Stop the global hotkey listener thread before exit.
        gh = getattr(self, "_global_hotkey", None)
        if gh is not None:
            try:
                gh.stop()
            except Exception:  # noqa: BLE001
                pass
        # Gracefully stop collaboration service so mDNS un-registers + uvicorn
        # closes its sockets before exit (otherwise a stuck QThread can keep the
        # process alive holding the DB handle).
        svc = getattr(self.ctx, "collab_service", None)
        if svc is not None:
            try:
                svc.stop()
            except Exception:  # noqa: BLE001
                pass
        # Close + checkpoint every cached SQLite connection (WAL sidecar flush).
        try:
            from app.db import db_manager
            db_manager.close_all()
        except Exception:  # noqa: BLE001
            pass

    def _wire_collab_status_bar(self) -> None:
        """Connect CollabService signals to the status bar segment.

        Called once from __init__ after the status bar is built.  Safe to
        call when collab_service is None — becomes a no-op.
        """
        svc = getattr(self.ctx, "collab_service", None)
        if svc is None:
            return
        svc.server_ready.connect(lambda _port: self._refresh_collab_status())
        svc.peers_changed.connect(self._refresh_collab_status)
        svc.sync_error.connect(
            lambda msg: self.set_status_collab(f"协作: 错误")
        )
        self._refresh_collab_status()

    def _refresh_collab_status(self) -> None:
        svc = getattr(self.ctx, "collab_service", None)
        peers = svc.peers() if svc is not None else []
        try:
            from app.services.collab_status import build_collab_status
            status = build_collab_status(svc, peers)
            self.set_status_collab(f"协作: {status.status_badge}")
        except Exception:
            self.set_status_collab("协作: 未启动")


# ── Nav glyphs (view_id → qtawesome Material Design Icon) ───────────────────

_NAV_GLYPHS: dict[str, str] = {
    "workbench": "mdi6.microscope",
    "overview":  "mdi6.view-dashboard-outline",
    "project_tree": "mdi6.file-tree-outline",
    "labels":    "mdi6.tag-outline",
    "worms":     "mdi6.waves",
    "taxonomy":  "mdi6.dna",
    "coords":    "mdi6.map-marker-outline",
    "summary":   "mdi6.chart-box-outline",
    "collection_records": "mdi6.clipboard-list-outline",
    "collection_map": "mdi6.map-marker-multiple",
    "screenshot": "mdi6.scissors-cutting",
    "collab":    "mdi6.account-group-outline",
    "settings":  "mdi6.cog-outline",
}


_DEFAULT_PINNED_NAV: tuple[str, ...] = (
    "workbench",
    "collab",
    "project_tree",
    "collection_records",
)


_NAV_GROUPS: dict[str, dict[str, str]] = {
    "project": {"title": "项目", "icon": "mdi6.folder-outline"},
    "taxonomy": {"title": "分类", "icon": "mdi6.dna"},
    "collection": {"title": "采集", "icon": "mdi6.map-marker-outline"},
    "tools": {"title": "工具", "icon": "mdi6.tools"},
    "system": {"title": "系统", "icon": "mdi6.cog-outline"},
}


_NAV_GROUP_FOR_VIEW: dict[str, str] = {
    "workbench": "project",
    "collab": "project",
    "overview": "project",
    "project_tree": "project",
    "summary": "project",
    "labels": "tools",
    "worms": "taxonomy",
    "taxonomy": "taxonomy",
    "coords": "collection",
    "collection_records": "collection",
    "collection_map": "tools",
    "settings": "system",
}


# ── Helpers ───────────────────────────────────────────────────────────────

def _separator() -> QLabel:
    sep = QLabel("·")
    sep.setObjectName("StatusSegment")
    return sep
