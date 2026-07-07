"""test_main_window.py — Smoke tests for the modern top-bar chrome.

The shell was reworked from a left QListWidget sidebar into a horizontal
segmented top-nav + context bar.  These tests pin the new contract:

  - register_view() appends a checkable #NavSegment button per view; the
    matching stack page is built lazily on first navigation (not at register).
  - navigate_to(view_id) checks the right segment, shows the right page,
    and calls on_activate().
  - The context bar (project switcher + active badge) reflects ctx state.
  - restore_state() selects a default segment without a display.

Runs headless (QT_QPA_PLATFORM=offscreen).
"""
from __future__ import annotations

import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PyQt6.QtWidgets import QApplication, QMenu, QPushButton, QSizePolicy, QToolButton

from app.app_context import AppContext
from app.config.i18n import set_language
from app.main_window import MainWindow
from app.views.base_view import BaseView
from app.views.registry import ALL_VIEWS


_APP = None


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


class _DummyView(BaseView):
    view_id = "dummy"
    nav_title = "测试页"
    nav_icon = "🧪"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.activated = 0

    def on_activate(self) -> None:
        self.activated += 1


def _fresh_window() -> MainWindow:
    set_language("zh")
    ctx = AppContext()
    ctx.settings._qs.remove("ui/topbar_pinned_views")
    ctx.settings._qs.remove("ui/screenshot_tool_enabled")
    ctx.settings._qs.remove("ui/screenshot_tool_default_migrated")
    ctx.settings._qs.remove("ui/screenshot_tool_opt_in_migrated")
    return MainWindow(ctx)


def _window_with_screenshot_tool() -> MainWindow:
    set_language("zh")
    ctx = AppContext()
    ctx.settings._qs.remove("ui/topbar_pinned_views")
    ctx.settings._qs.setValue("ui/screenshot_tool_enabled", "true")
    ctx.settings._qs.setValue("ui/screenshot_tool_default_migrated", "true")
    ctx.settings._qs.setValue("ui/screenshot_tool_opt_in_migrated", "true")
    return MainWindow(ctx)


def _window_without_screenshot_tool() -> MainWindow:
    set_language("zh")
    ctx = AppContext()
    ctx.settings._qs.remove("ui/topbar_pinned_views")
    ctx.settings._qs.setValue("ui/screenshot_tool_enabled", "false")
    ctx.settings._qs.setValue("ui/screenshot_tool_default_migrated", "true")
    ctx.settings._qs.setValue("ui/screenshot_tool_opt_in_migrated", "true")
    return MainWindow(ctx)


# ── register_view wires a top-nav segment + stack page ─────────────────────

def test_register_view_adds_segment_and_page():
    win = _fresh_window()
    win.register_view(_DummyView)
    assert len(win._nav_buttons) == 1
    btn = win._nav_buttons[0]
    assert isinstance(btn, QPushButton)
    assert btn.objectName() == "NavSegment"
    assert btn.isCheckable()
    assert btn.text() == "测试页"
    # Lazy build: register_view wires the nav segment but does NOT construct the
    # page — that happens on first activation (keeps startup cheap).
    assert win._stack.count() == 0
    assert "dummy" not in win._views
    # First navigation builds + mounts it.
    win.navigate_to("dummy")
    assert win._stack.count() == 1
    assert win._views["dummy"] is win._stack.currentWidget()


def test_nav_segments_are_exclusive():
    win = _fresh_window()
    win.register_view(_DummyView)

    class _Second(_DummyView):
        view_id = "dummy2"
        nav_title = "第二页"

    win.register_view(_Second)
    win.navigate_to("dummy")
    assert win._nav_buttons[0].isChecked()
    assert not win._nav_buttons[1].isChecked()
    win.navigate_to("dummy2")
    assert not win._nav_buttons[0].isChecked()
    assert win._nav_buttons[1].isChecked()


def test_default_nav_pins_keep_topbar_focused():
    win = _fresh_window()
    for cls in ALL_VIEWS:
        win.register_view(cls)

    visible = [btn.property("view_id") for btn in win._nav_buttons if not btn.isHidden()]
    assert visible == [
        "workbench",
        "collab",
        "project_tree",
        "collection_records",
    ]


def test_nav_segments_keep_content_width():
    win = _fresh_window()
    for cls in ALL_VIEWS:
        win.register_view(cls)

    for btn in win._nav_buttons:
        assert btn.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Fixed


def test_function_menu_groups_all_registered_views():
    win = _fresh_window()
    for cls in ALL_VIEWS:
        win.register_view(cls)

    menu = win._nav_menu_btn.menu()
    assert isinstance(menu, QMenu)
    assert [a.text() for a in menu.actions() if a.menu()] == [
        "项目",
        "分类",
        "采集",
        "工具",
        "系统",
        "固定到顶栏",
    ]
    project_menu = win._nav_group_menus["project"]
    assert [a.text() for a in project_menu.actions()] == [
        "照片工作区",
        "协作",
        "最近使用",
        "项目树",
        "项目汇总",
    ]
    tools_menu = win._nav_group_menus["tools"]
    assert [a.text() for a in tools_menu.actions() if a.isVisible()] == [
        "标签打印",
        "采集地图",
    ]
    system_menu = win._nav_group_menus["system"]
    assert "软件更新" in [a.text() for a in system_menu.actions()]
    assert isinstance(win._update_btn, QToolButton)
    assert win._update_btn.text() == "更新"
    assert win._update_btn.accessibleName() == "软件更新"
    assert win._update_btn.toolTip() == "检查并安装最新版本"


def test_collab_status_bar_uses_shared_status():
    from app.services.collab_service import CollabService

    win = _fresh_window()
    svc = CollabService()
    win.ctx.collab_service = svc
    try:
        win._refresh_collab_status()
        assert "协作未启动" in win._status_collab.text()

        svc._running = True
        win._refresh_collab_status()
        assert "未配对团队" in win._status_collab.text()

        svc.set_group_code("TEAM-1")
        win._refresh_collab_status()
        assert "未发现其他设备" in win._status_collab.text()
    finally:
        svc.stop()
        win.close()


def test_workspace_actions_are_integrated_into_breadcrumb(tmp_path):
    ctx = AppContext()
    ctx.current_project_dir = str(tmp_path / "demo")
    win = MainWindow(ctx)
    win.refresh_context_bar()
    folder_btn = win._project_switcher._btn_folder
    actions = [a.text() for a in folder_btn.menu().actions()]

    assert not hasattr(win, "_project_actions_btn")
    assert folder_btn.objectName() == "WorkspaceFolderButton"
    assert folder_btn.accessibleName() == "打开/新建工作区"
    assert actions == ["新建工作区…", "打开文件夹…"]


def test_nav_pin_menu_toggles_topbar_segments():
    win = _fresh_window()
    for cls in ALL_VIEWS:
        win.register_view(cls)

    overview_idx = next(i for i, cls in enumerate(win._view_classes) if cls.view_id == "overview")
    assert win._nav_buttons[overview_idx].isHidden()

    win._nav_pin_actions["overview"].setChecked(True)
    assert not win._nav_buttons[overview_idx].isHidden()

    win._nav_pin_actions["overview"].setChecked(False)
    assert win._nav_buttons[overview_idx].isHidden()


def test_navigate_to_shows_page_and_activates():
    win = _fresh_window()
    win.register_view(_DummyView)
    win.navigate_to("dummy")          # lazy build happens here
    view = win._views["dummy"]
    assert win._stack.currentWidget() is view
    assert view.activated >= 1


# ── Context bar reflects ctx state ─────────────────────────────────────────

def test_context_bar_no_project():
    win = _fresh_window()
    win.refresh_context_bar()
    assert "选择工作区" in win._project_switcher.text()
    assert win._project_switcher._btn_folder is None
    assert win._active_badge.objectName() == "ActiveBadgeOff"
    # Quick actions (智能压缩 / 🎬Helicon) disabled without a project.
    assert not win._btn_compress.isEnabled()
    assert not win._btn_helicon.isEnabled()


def test_context_bar_with_project(tmp_path):
    ctx = AppContext()
    ctx.current_project_dir = str(tmp_path / "FJ-YGLZ-2026")
    win = MainWindow(ctx)
    win.refresh_context_bar()
    assert "FJ-YGLZ-2026" in win._project_switcher.text()
    assert win._btn_compress.isEnabled()
    assert win._btn_helicon.isEnabled()


# ── Screenshot lives in the grouped tools menu, Settings only configures it ─

def test_screenshot_tool_is_disabled_by_default():
    win = _fresh_window()
    for cls in ALL_VIEWS:
        win.register_view(cls)

    assert isinstance(win._shot_btn, QToolButton)
    assert win._shot_btn.isHidden()
    assert not win.screenshot_tool_enabled()
    assert not win._shot_menu.menuAction().isVisible()
    assert not win._screenshot_shortcut.isEnabled()
    assert win._settings_btn.toolTip() == "配置"


def test_screenshot_tool_can_be_disabled_by_setting():
    win = _window_without_screenshot_tool()
    for cls in ALL_VIEWS:
        win.register_view(cls)

    assert isinstance(win._shot_btn, QToolButton)
    assert win._shot_btn.isHidden()
    assert not win.screenshot_tool_enabled()
    assert not win._shot_menu.menuAction().isVisible()
    assert not win._screenshot_shortcut.isEnabled()


def test_legacy_hidden_screenshot_setting_stays_hidden_after_migration():
    set_language("zh")
    ctx = AppContext()
    ctx.settings._qs.remove("ui/topbar_pinned_views")
    ctx.settings._qs.setValue("ui/screenshot_tool_enabled", "false")
    ctx.settings._qs.remove("ui/screenshot_tool_default_migrated")

    win = MainWindow(ctx)
    for cls in ALL_VIEWS:
        win.register_view(cls)

    assert not win.screenshot_tool_enabled()
    assert ctx.settings._qs.value("ui/screenshot_tool_enabled") == "false"
    assert ctx.settings._qs.value("ui/screenshot_tool_default_migrated") == "true"
    assert isinstance(win._shot_btn, QToolButton)
    assert not win._shot_menu.menuAction().isVisible()


def test_legacy_enabled_screenshot_setting_migrates_to_disabled():
    set_language("zh")
    ctx = AppContext()
    ctx.settings._qs.remove("ui/topbar_pinned_views")
    ctx.settings._qs.setValue("ui/screenshot_tool_enabled", "true")
    ctx.settings._qs.setValue("ui/screenshot_tool_default_migrated", "true")
    ctx.settings._qs.remove("ui/screenshot_tool_opt_in_migrated")

    win = MainWindow(ctx)

    assert not win.screenshot_tool_enabled()
    assert ctx.settings._qs.value("ui/screenshot_tool_enabled") == "false"
    assert ctx.settings._qs.value("ui/screenshot_tool_default_migrated") == "true"
    assert ctx.settings._qs.value("ui/screenshot_tool_opt_in_migrated") == "true"
    assert win._shot_btn.isHidden()
    assert not win._screenshot_shortcut.isEnabled()


def test_missing_screenshot_setting_migrates_to_disabled():
    set_language("zh")
    ctx = AppContext()
    ctx.settings._qs.remove("ui/topbar_pinned_views")
    ctx.settings._qs.remove("ui/screenshot_tool_enabled")
    ctx.settings._qs.remove("ui/screenshot_tool_default_migrated")
    ctx.settings._qs.remove("ui/screenshot_tool_opt_in_migrated")

    win = MainWindow(ctx)

    assert not win.screenshot_tool_enabled()
    assert ctx.settings._qs.value("ui/screenshot_tool_enabled") == "false"
    assert ctx.settings._qs.value("ui/screenshot_tool_default_migrated") == "true"
    assert ctx.settings._qs.value("ui/screenshot_tool_opt_in_migrated") == "true"
    assert win._shot_btn.isHidden()
    assert not win._screenshot_shortcut.isEnabled()


def test_screenshot_tool_can_be_enabled_in_tools_menu():
    win = _window_with_screenshot_tool()
    for cls in ALL_VIEWS:
        win.register_view(cls)

    assert isinstance(win._shot_btn, QToolButton)
    assert not win._shot_btn.isHidden()
    assert win.screenshot_tool_enabled()
    assert win._shot_menu.title() == "截图"
    assert win._shot_menu.menuAction().isVisible()
    assert win._screenshot_shortcut.isEnabled()
    assert [a.text() for a in win._shot_menu.actions()] == [
        "开始区域截图    Alt+A",
        "全屏截图",
        "当前窗口",
        "当前页面",
        "",
        "截图设置",
        "关闭截图工具",
    ]
    assert win._settings_btn.toolTip() == "配置"


def test_screenshot_menu_can_disable_tool_from_menu():
    win = _window_with_screenshot_tool()
    for cls in ALL_VIEWS:
        win.register_view(cls)

    win._shot_actions["disable"].trigger()

    assert not win.screenshot_tool_enabled()
    assert win._shot_btn.isHidden()
    assert not win._shot_menu.menuAction().isVisible()
    assert not win._screenshot_shortcut.isEnabled()


def test_screenshot_is_menu_only_not_nav_segment():
    win = _fresh_window()
    for cls in ALL_VIEWS:
        win.register_view(cls)

    assert "截图" not in [btn.text() for btn in win._nav_buttons]
    assert not win._shot_menu.menuAction().isVisible()


def test_rebind_screenshot_shortcut_updates_tooltip():
    win = _fresh_window()
    win.rebind_screenshot_shortcut("Ctrl+Alt+S")
    assert "Ctrl+Alt+S" in win._shot_actions["region"].text()
    assert "Ctrl+Alt+S" in win._shot_btn.toolTip()


def test_retranslate_ui_updates_shell_and_grouped_menu():
    win = _fresh_window()
    for cls in ALL_VIEWS:
        win.register_view(cls)

    set_language("en")
    win.retranslate_ui()

    assert win.windowTitle() == "Specimen Imaging"
    assert win._brand.text() == "Specimen Imaging Manager"
    assert win._nav_menu_btn.text() == "Toolbox"
    assert win._nav_buttons[0].text() == "Photo Workspace"
    assert [a.text() for a in win._nav_group_menus["tools"].actions() if a.isVisible()] == [
        "Label Printing",
        "Collection Map",
    ]
    assert "Alt+A" in win._shot_actions["region"].text()


# ── restore_state selects a default segment ────────────────────────────────

def test_restore_state_selects_default():
    win = _fresh_window()
    for cls in ALL_VIEWS:
        win.register_view(cls)
    win.restore_state()
    # At least one segment is checked after restore.
    assert any(b.isChecked() for b in win._nav_buttons)


def test_restore_state_fast_startup_skips_last_heavy_page():
    win = _fresh_window()

    class _Second(_DummyView):
        view_id = "dummy2"
        nav_title = "第二页"

    win.register_view(_DummyView)
    win.register_view(_Second)
    win.ctx.settings.last_nav_index = 1

    win.restore_state(activate_last_view=False)

    assert win._nav_buttons[0].isChecked()
    assert not win._nav_buttons[1].isChecked()
    assert "dummy" in win._views
    assert "dummy2" not in win._views
    assert win.ctx.settings.last_nav_index == 1


# ── Full registry boots through the new chrome ─────────────────────────────

def test_all_views_register():
    win = _fresh_window()
    for cls in ALL_VIEWS:
        win.register_view(cls)
    assert len(win._nav_buttons) == len(ALL_VIEWS)
    # Lazy build: nothing constructed until visited.
    assert win._stack.count() == 0
    # Visiting every view builds them all without error.
    for cls in ALL_VIEWS:
        win.navigate_to(cls.view_id)
    assert win._stack.count() == len(ALL_VIEWS)
    # settings cog navigation target exists.
    assert "settings" in win._views


# ── Shutdown closes cached DB connections (no "must reboot" lock leak) ─────
# On WSL/drvfs the per-project SQLite connection is opened in WAL mode and
# cached globally in db_manager._db_cache.  If it is never closed at exit, the
# -wal/-shm sidecars + advisory file lock persist on the Windows side of /mnt,
# so the *next* launch hits "database is locked" / fails to open the project
# until the OS reclaims the zombie handle (i.e. a reboot).  teardown() must
# release those connections on every exit path.

def test_teardown_closes_cached_db_connections(tmp_path):
    from app.db import db_manager

    db_manager.close_all()
    project = tmp_path / "FJ-SHUTDOWN"
    project.mkdir(parents=True, exist_ok=True)
    # Open the project DB the same way the app does -> populates the cache.
    conn = db_manager.open_project_db(str(project), create=True)
    conn.execute("CREATE TABLE IF NOT EXISTS t(x)")
    conn.commit()
    assert db_manager._db_cache, "setup: DB connection should be cached"

    ctx = AppContext()
    ctx.current_project_dir = str(project)
    win = MainWindow(ctx)
    win._teardown()

    assert db_manager._db_cache == {}, "teardown must close + evict cached DB connections"
    # Reopening after teardown works (locks released) — the "must reboot" check.
    conn2 = db_manager.open_project_db(str(project))
    conn2.close()
    db_manager.close_all()


def test_teardown_is_idempotent():
    # closeEvent + aboutToQuit can both fire; teardown must not double-close.
    win = _fresh_window()
    win._teardown()
    win._teardown()  # second call must be a no-op, not raise
    from app.db import db_manager
    assert db_manager._db_cache == {}


def test_teardown_cancels_view_background_workers():
    # The must-reboot bug: an in-flight QThread (Helicon/WoRMS) reading the DB
    # outlives exit and holds the SQLite handle. _teardown must ask every view
    # to stop its background work BEFORE closing DB connections.
    class _TrackingView(_DummyView):
        stopped = 0

        def stop_background_work(self):
            self.stopped += 1

    win = _fresh_window()
    win.register_view(_TrackingView)
    win.navigate_to("dummy")
    view = win._views["dummy"]
    assert view.stopped == 0
    win._teardown()
    assert view.stopped == 1, "teardown must call stop_background_work on each view"


def test_base_view_stop_background_work_is_noop():
    from app.views.base_view import BaseView
    # Default must not raise — views that own no workers inherit it.
    class _Plain(BaseView):
        view_id = "plain"
        nav_title = "P"
        nav_icon = ""

        def _setup_ui(self):
            pass

        def on_activate(self):
            pass

    p = _Plain(AppContext())
    p.stop_background_work()  # must not raise


# ── Startup stays lean: heavy libs are NOT imported at launch ──────────────
# Locks the lazy-import wins. matplotlib (~1.8 s) must load only when the
# 采集地图 tab is opened; openpyxl only when exporting. Run in a clean
# subprocess so other tests' imports don't pollute sys.modules.

def test_startup_does_not_import_heavy_libs():
    code = (
        "import os; os.environ['QT_QPA_PLATFORM']='offscreen';"
        "from PyQt6.QtWidgets import QApplication; app=QApplication([]);"
        "from app.app_context import AppContext;"
        "from app.main_window import MainWindow;"
        "from app.views.registry import ALL_VIEWS;"
        "ctx=AppContext(); win=MainWindow(ctx);"
        "[win.register_view(c) for c in ALL_VIEWS];"
        "import sys;"
        "assert 'matplotlib' not in sys.modules, 'matplotlib imported at startup';"
        "assert 'openpyxl' not in sys.modules, 'openpyxl imported at startup';"
        "print('OK')"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout


def test_startup_stderr_logging_ignores_broken_pipe(monkeypatch):
    import main

    class _BrokenStderr:
        def write(self, _text):
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self):
            pass

    monkeypatch.setattr(main.sys, "stderr", _BrokenStderr())

    main._safe_stderr_print("启动窗口目标屏幕: test")
