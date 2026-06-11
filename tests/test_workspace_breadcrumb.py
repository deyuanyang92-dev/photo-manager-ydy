"""test_workspace_breadcrumb.py — 顶栏工作区面包屑（EOS Utility 式目录显示）.

契约：
  - breadcrumb_chain(root, ws)：根→当前工作区的 (name, path) 链；不在根下→只剩叶子；
    无工作区→空链。
  - sibling_dirs(ws)：同级目录（含自身），过滤文件/点号目录/RESERVED_DIR_NAMES，排序。
  - WorkspaceBreadcrumb：
      * 无项目 → text() 含「（未选）」，点击 → navigate_requested("overview")。
      * 有链   → text() = "根 / 断面A / B2"（>3 级折叠中间为 …）。
      * ◀ ▶   → 切到上/下一个同级，走 project_service.enter_workspace（唯一入口），
                成功后发 workspace_changed；首/末端对应箭头禁用。
      * 根即工作区 → 两箭头都禁用（不允许横跳出项目）。
      * 叶子下拉 → 同级菜单，已是工作区的标 📷。
  - MainWindow._project_switcher 即该控件；refresh_context_bar() 后 text() 反映链。

Runs headless (QT_QPA_PLATFORM=offscreen).
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PyQt6.QtWidgets import QApplication

from app.widgets.workspace_breadcrumb import (
    WorkspaceBreadcrumb,
    breadcrumb_chain,
    sibling_dirs,
)

_APP = None


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


class _Ctx:
    """Minimal stand-in for AppContext — breadcrumb 只读这两个属性."""

    def __init__(self, ws=None, root=None):
        self.current_project_dir = ws
        self.current_project_root = root


def _make_tree(tmp_path):
    """root/断面A/{B1,B2,B3} + 工作区内部目录 + 干扰项."""
    root = tmp_path / "航次2026"
    sect = root / "断面A"
    for st in ("B1", "B2", "B3"):
        (sect / st).mkdir(parents=True)
    # B2 已是工作区
    (sect / "B2" / "_data").mkdir()
    (sect / "B2" / "_data" / "project.db").write_text("")
    # 干扰：保留目录 / 点号目录 / 普通文件
    (sect / "_data").mkdir()
    (sect / "results").mkdir()
    (sect / ".git").mkdir()
    (sect / "notes.txt").write_text("x")
    return root, sect


# ── 纯逻辑 ────────────────────────────────────────────────────────────────

def test_chain_nested(tmp_path):
    root, sect = _make_tree(tmp_path)
    chain = breadcrumb_chain(str(root), str(sect / "B2"))
    assert [n for n, _ in chain] == ["航次2026", "断面A", "B2"]
    assert chain[-1][1] == str((sect / "B2").resolve())


def test_chain_root_is_workspace(tmp_path):
    root, _ = _make_tree(tmp_path)
    chain = breadcrumb_chain(str(root), str(root))
    assert [n for n, _ in chain] == ["航次2026"]


def test_chain_outside_root(tmp_path):
    root, sect = _make_tree(tmp_path)
    other = tmp_path / "别处" / "X1"
    other.mkdir(parents=True)
    chain = breadcrumb_chain(str(root), str(other))
    assert [n for n, _ in chain] == ["X1"]


def test_chain_no_workspace(tmp_path):
    assert breadcrumb_chain(str(tmp_path), None) == []


def test_siblings_filter(tmp_path):
    root, sect = _make_tree(tmp_path)
    sibs = sibling_dirs(str(sect / "B2"))
    assert [os.path.basename(p) for p in sibs] == ["B1", "B2", "B3"]


# ── 控件 ──────────────────────────────────────────────────────────────────

def test_widget_no_project_placeholder():
    w = WorkspaceBreadcrumb(_Ctx())
    w.refresh()
    assert "（未选）" in w.text()
    got = []
    w.navigate_requested.connect(got.append)
    w._placeholder_btn.click()
    assert got == ["overview"]


def test_widget_shows_chain(tmp_path):
    root, sect = _make_tree(tmp_path)
    w = WorkspaceBreadcrumb(_Ctx(str(sect / "B2"), str(root)))
    w.refresh()
    assert "航次2026" in w.text()
    assert "断面A" in w.text()
    assert "B2" in w.text()


def test_widget_collapses_deep_chain(tmp_path):
    root = tmp_path / "根"
    deep = root / "a" / "b" / "c" / "D4"
    deep.mkdir(parents=True)
    w = WorkspaceBreadcrumb(_Ctx(str(deep), str(root)))
    w.refresh()
    t = w.text()
    assert "根" in t and "…" in t and "c" in t and "D4" in t
    assert "a" not in t.split("…")[1]  # 中间层被折叠


def test_ancestor_click_jumps_to_tree(tmp_path):
    root, sect = _make_tree(tmp_path)
    w = WorkspaceBreadcrumb(_Ctx(str(sect / "B2"), str(root)))
    w.refresh()
    got = []
    w.navigate_requested.connect(got.append)
    w._segment_btns[0].click()  # 根段
    assert got == ["project_tree"]


def _patch_enter(monkeypatch, calls):
    def fake_enter(ctx, path, root=None, *, projects_json_path=None):
        calls.append((path, root))
        ctx.current_project_dir = path
        return path
    from app.services import project_service
    monkeypatch.setattr(project_service, "enter_workspace", fake_enter)


def test_arrow_next_switches_sibling(tmp_path, monkeypatch):
    root, sect = _make_tree(tmp_path)
    ctx = _Ctx(str(sect / "B2"), str(root))
    w = WorkspaceBreadcrumb(ctx)
    w.refresh()
    calls, changed = [], []
    _patch_enter(monkeypatch, calls)
    w.workspace_changed.connect(changed.append)
    assert w._btn_prev.isEnabled() and w._btn_next.isEnabled()
    w._btn_next.click()
    assert len(calls) == 1
    path, passed_root = calls[0]
    assert os.path.basename(path) == "B3"
    assert passed_root == str(root)
    assert changed and os.path.basename(changed[0]) == "B3"


def test_arrows_disabled_at_edges(tmp_path):
    root, sect = _make_tree(tmp_path)
    w = WorkspaceBreadcrumb(_Ctx(str(sect / "B1"), str(root)))
    w.refresh()
    assert not w._btn_prev.isEnabled()
    assert w._btn_next.isEnabled()
    w2 = WorkspaceBreadcrumb(_Ctx(str(sect / "B3"), str(root)))
    w2.refresh()
    assert w2._btn_prev.isEnabled()
    assert not w2._btn_next.isEnabled()


def test_root_workspace_arrows_disabled(tmp_path):
    root, _ = _make_tree(tmp_path)
    w = WorkspaceBreadcrumb(_Ctx(str(root), str(root)))
    w.refresh()
    assert not w._btn_prev.isEnabled()
    assert not w._btn_next.isEnabled()


def test_dropdown_lists_siblings_marks_workspaces(tmp_path):
    root, sect = _make_tree(tmp_path)
    w = WorkspaceBreadcrumb(_Ctx(str(sect / "B2"), str(root)))
    w.refresh()
    menu = w._build_sibling_menu()
    labels = [a.text() for a in menu.actions()]
    assert len(labels) == 3
    assert any("B1" in s for s in labels)
    # B2 是工作区 → 带 📷
    assert any("📷" in s and "B2" in s for s in labels)


# ── MainWindow 集成 ──────────────────────────────────────────────────────

def test_main_window_uses_breadcrumb(tmp_path):
    from app.app_context import AppContext
    from app.config.i18n import set_language
    from app.main_window import MainWindow

    set_language("zh")
    root, sect = _make_tree(tmp_path)
    ctx = AppContext()
    ctx.current_project_dir = str(sect / "B2")
    ctx.current_project_root = str(root)
    win = MainWindow(ctx)
    win.refresh_context_bar()
    assert isinstance(win._project_switcher, WorkspaceBreadcrumb)
    t = win._project_switcher.text()
    assert "断面A" in t and "B2" in t
