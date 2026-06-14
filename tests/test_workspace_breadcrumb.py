"""test_workspace_breadcrumb.py — 顶栏工作区面包屑（OM 路径条式）.

契约：
  - breadcrumb_chain(root, ws)：根→当前工作区的 (name, path) 链；不在根下→只剩叶子；
    无工作区→空链。
  - sibling_dirs(ws)：同级目录（含自身），过滤文件/点号目录/RESERVED_DIR_NAMES，排序。
  - WorkspaceBreadcrumb：
      * 无项目 → text() 含「（未选）」，点击 → navigate_requested("overview")。
      * 有链   → text() = "根 / 断面A / B2"（>3 级折叠中间为 …）。
      * ◀ ▶   → 访问历史后退/前进（浏览器式），走 project_service.enter_workspace
                （唯一入口），成功后发 workspace_changed；首/末端禁用，不回绕。
                中途回退后再切新工作区 → 截断前向分支。同级切换改走 ▾ 下拉。
      * 根即工作区（chain==1）只要有历史也能 ◀▶（修复「光秃秃无箭头」）。
      * 叶子下拉 → 同级菜单（已是工作区的标 📷）+ 末尾「+ 新建断面…」：
                在当前工作区父目录下建新同级目录并进入；名字预填 YYYYMMDD(；
                拒 / \\ .. 空。
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


# ── ◀▶ 访问历史（浏览器式；同级步进退役，同级改走 ▾ 下拉）────────────────


def test_history_seeds_on_refresh(tmp_path):
    root, sect = _make_tree(tmp_path)
    w = WorkspaceBreadcrumb(_Ctx(str(sect / "B2"), str(root)))
    w.refresh()
    assert w._history == [str((sect / "B2").resolve())]
    assert w._history_pos == 0
    assert not w._btn_prev.isEnabled() and not w._btn_next.isEnabled()


def test_arrow_back_forward_navigates_history(tmp_path, monkeypatch):
    root, sect = _make_tree(tmp_path)
    ctx = _Ctx(str(sect / "B2"), str(root))
    w = WorkspaceBreadcrumb(ctx)
    w.refresh()
    calls, changed = [], []
    _patch_enter(monkeypatch, calls)
    w.workspace_changed.connect(changed.append)
    # 下拉/外部切到 B3 → 入历史
    w._switch_to(str(sect / "B3"))
    assert w._history == [str((sect / "B2").resolve()),
                          str((sect / "B3").resolve())]
    assert w._history_pos == 1
    assert w._btn_prev.isEnabled() and not w._btn_next.isEnabled()
    # ◀ 回 B2
    w._btn_prev.click()
    assert os.path.basename(calls[-1][0]) == "B2"
    assert w._history_pos == 0
    assert not w._btn_prev.isEnabled() and w._btn_next.isEnabled()
    # ▶ 前 B3
    w._btn_next.click()
    assert os.path.basename(calls[-1][0]) == "B3"
    assert w._history_pos == 1
    assert changed  # 每次切换都发 workspace_changed


def test_history_branch_truncates(tmp_path, monkeypatch):
    root, sect = _make_tree(tmp_path)
    ctx = _Ctx(str(sect / "B2"), str(root))
    w = WorkspaceBreadcrumb(ctx)
    w.refresh()
    _patch_enter(monkeypatch, [])
    # B2 → B3 → ◀回B2 → 切B1：B3 应被截断
    w._switch_to(str(sect / "B3"))
    w._btn_prev.click()                      # 回 B2 (pos0)
    w._switch_to(str(sect / "B1"))           # 新分支
    assert w._history == [str((sect / "B2").resolve()),
                          str((sect / "B1").resolve())]
    assert w._history_pos == 1
    assert not w._btn_next.isEnabled()       # B3 已丢，无前进


def test_history_no_wraparound(tmp_path, monkeypatch):
    root, sect = _make_tree(tmp_path)
    ctx = _Ctx(str(sect / "B2"), str(root))
    w = WorkspaceBreadcrumb(ctx)
    w.refresh()
    calls = []
    _patch_enter(monkeypatch, calls)
    n_before = len(calls)
    # 只有一条历史时 ◀▶ 都不动作
    w._btn_prev.click()
    w._btn_next.click()
    assert len(calls) == n_before


def test_external_switch_recorded(tmp_path, monkeypatch):
    """项目树/外部改 ctx.current 后 refresh → 入历史."""
    root, sect = _make_tree(tmp_path)
    ctx = _Ctx(str(sect / "B2"), str(root))
    w = WorkspaceBreadcrumb(ctx)
    w.refresh()
    _patch_enter(monkeypatch, [])
    w._switch_to(str(sect / "B3"))
    ctx.current_project_dir = str(sect / "B1")   # 外部进入（如项目树）
    w.refresh()
    assert str((sect / "B1").resolve()) in w._history
    assert w._history[-1] == str((sect / "B1").resolve())


def test_root_workspace_navigates_when_history(tmp_path, monkeypatch):
    """根即工作区（chain==1）只要有历史也能 ◀▶ —— 修复光秃秃无箭头."""
    root, _ = _make_tree(tmp_path)
    other = tmp_path / "另一航次"
    other.mkdir()
    ctx = _Ctx(str(root), str(root))
    w = WorkspaceBreadcrumb(ctx)
    w.refresh()
    assert not w._btn_prev.isEnabled()       # 单条历史，两箭头禁
    _patch_enter(monkeypatch, [])
    w._switch_to(str(other))                 # 切别处建历史
    w._btn_prev.click()                      # 回 root
    assert w._btn_next.isEnabled()           # 有前进


# ── ▾ + 新建断面 ────────────────────────────────────────────────────────


def test_dropdown_has_new_section_action(tmp_path):
    root, sect = _make_tree(tmp_path)
    w = WorkspaceBreadcrumb(_Ctx(str(sect / "B2"), str(root)))
    w.refresh()
    menu = w._build_sibling_menu()
    labels = [a.text() for a in menu.actions()]
    assert any("新建断面" in s for s in labels)


def test_default_section_name_is_date_prefixed(tmp_path):
    import re
    root, sect = _make_tree(tmp_path)
    w = WorkspaceBreadcrumb(_Ctx(str(sect / "B2"), str(root)))
    w.refresh()
    assert re.match(r"^\d{8}\(", w._default_section_name())   # YYYYMMDD(


def test_create_section_makes_dir_and_switches(tmp_path, monkeypatch):
    root, sect = _make_tree(tmp_path)
    ctx = _Ctx(str(sect / "B2"), str(root))
    w = WorkspaceBreadcrumb(ctx)
    w.refresh()
    calls = []
    _patch_enter(monkeypatch, calls)
    new_path = w.create_and_enter_section("20260612(草埔村)")
    assert new_path is not None
    # 建在当前工作区父目录下（= 新同级断面）
    assert (sect / "20260612(草埔村)").is_dir()
    # 走了 enter_workspace
    assert calls and os.path.basename(calls[-1][0]) == "20260612(草埔村)"
    # 入历史
    assert str((sect / "20260612(草埔村)").resolve()) in w._history


def test_create_section_rejects_bad_name(tmp_path, monkeypatch):
    root, sect = _make_tree(tmp_path)
    ctx = _Ctx(str(sect / "B2"), str(root))
    w = WorkspaceBreadcrumb(ctx)
    w.refresh()
    _patch_enter(monkeypatch, [])
    before = sorted(p.name for p in sect.iterdir())
    for bad in ("a/b", "a\\b", "..", "  ", ""):
        assert w.create_and_enter_section(bad) is None
    # 父目录前后内容一致 → 一个目录都没建
    after = sorted(p.name for p in sect.iterdir())
    assert before == after


def test_dropdown_lists_siblings_marks_workspaces(tmp_path):
    root, sect = _make_tree(tmp_path)
    w = WorkspaceBreadcrumb(_Ctx(str(sect / "B2"), str(root)))
    w.refresh()
    menu = w._build_sibling_menu()
    # 只数同级站位项（排除分隔线 + 末尾「新建断面」）
    sib_labels = [a.text() for a in menu.actions()
                  if not a.isSeparator() and not a.menu()
                  and "新建断面" not in a.text()]
    assert len(sib_labels) == 3
    assert any("B1" in s for s in sib_labels)
    # B2 是工作区 → 带 📷
    assert any("📷" in s and "B2" in s for s in sib_labels)


def test_dropdown_includes_recent_workspaces(tmp_path, monkeypatch):
    import json
    root, sect = _make_tree(tmp_path)
    other_root = tmp_path / "另一航次"
    other = other_root / "断面Z"
    other.mkdir(parents=True)
    recent_json = tmp_path / "user_projects.json"
    recent_json.write_text(json.dumps({
        "version": 1,
        "projects": [
            {
                "name": "另一航次 / 断面Z",
                "directory": str(other),
                "root": str(other_root),
            },
            {
                "name": "当前",
                "directory": str(sect / "B2"),
                "root": str(root),
            },
        ],
    }, ensure_ascii=False), encoding="utf-8")
    from app.services import project_service
    monkeypatch.setattr(
        project_service,
        "default_user_projects_json_path",
        lambda: str(recent_json),
    )
    w = WorkspaceBreadcrumb(_Ctx(str(sect / "B2"), str(root)))
    w.refresh()

    menu = w._build_sibling_menu()
    recent = next(a.menu() for a in menu.actions() if a.menu() and "最近工作区" in a.text())
    labels = [a.text() for a in recent.actions()]

    assert any("另一航次 / 断面Z" in s for s in labels)
    assert not any("当前" in s for s in labels)


def test_recent_workspace_switch_uses_recorded_root(tmp_path, monkeypatch):
    root, sect = _make_tree(tmp_path)
    other_root = tmp_path / "另一航次"
    other = other_root / "断面Z"
    other.mkdir(parents=True)
    ctx = _Ctx(str(sect / "B2"), str(root))
    w = WorkspaceBreadcrumb(ctx)
    w.refresh()
    calls = []
    _patch_enter(monkeypatch, calls)

    w._switch_to_recent(str(other), str(other_root))

    assert calls[-1] == (str(other), str(other_root))
    assert str(other.resolve()) in w._history


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
