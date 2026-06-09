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

from PyQt6.QtWidgets import QApplication, QPushButton

from app.app_context import AppContext
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
    ctx = AppContext()
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
    assert "（未选）" in win._project_switcher.text()
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


# ── restore_state selects a default segment ────────────────────────────────

def test_restore_state_selects_default():
    win = _fresh_window()
    for cls in ALL_VIEWS:
        win.register_view(cls)
    win.restore_state()
    # At least one segment is checked after restore.
    assert any(b.isChecked() for b in win._nav_buttons)


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
