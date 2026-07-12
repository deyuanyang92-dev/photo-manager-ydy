"""test_workspace_breadcrumb.py — 顶栏工作区面包屑（OM 路径条式）.

契约：
  - breadcrumb_chain(root, ws)：根→当前工作区的 (name, path) 链；不在根下→只剩叶子；
    无工作区→空链。
  - sibling_dirs(ws)：同级目录（含自身），过滤文件/点号目录/RESERVED_DIR_NAMES，排序。
  - WorkspaceBreadcrumb：
      * 无项目 → text() 含「选择工作区」，点击 → 打开工作区菜单（含项目总览）。
      * 有链   → text() = "根 / 断面A / B2"（>3 级折叠中间为 …）。
      * ◀ ▶   → 访问历史后退/前进（浏览器式），走 project_service.enter_workspace
                （唯一入口），成功后发 workspace_changed；首/末端禁用，不回绕。
                中途回退后再切新工作区 → 截断前向分支。同级切换改走 ▾ 下拉。
      * 根即工作区（chain==1）只要有历史也能 ◀▶（修复「光秃秃无箭头」）。
      * 叶子下拉 → 本项目内 / 磁盘上的其他项目 / 最近使用 / 新建文件夹分组；
                已是工作区的标「· 工作区」；新建在当前工作区父目录下建新文件夹并进入；
                拒 / \\ .. 空。
  - MainWindow._project_switcher 即该控件；refresh_context_bar() 后 text() 反映链。

Runs headless (QT_QPA_PLATFORM=offscreen).
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from app.widgets.workspace_breadcrumb import (
    WorkspaceBreadcrumb,
    breadcrumb_chain,
    project_tree_dirs,
    sibling_project_dirs,
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


def test_project_tree_dirs_lists_root_and_all_children(tmp_path):
    root, sect = _make_tree(tmp_path)
    (root / "学习子目录1").mkdir()
    dirs = project_tree_dirs(str(root), str(root))
    rels = [os.path.relpath(p, root) for p in dirs]
    assert "." in rels
    assert "断面A" in rels
    assert os.path.join("断面A", "B1") in rels
    assert "学习子目录1" in rels
    assert "results" not in rels


def test_project_tree_scan_resolves_only_root_paths(tmp_path, monkeypatch):
    """Walking descendants must not realpath every node on slow mounted disks."""
    root, _sect = _make_tree(tmp_path)
    original = Path.resolve
    calls = []

    def counted_resolve(self, *args, **kwargs):
        calls.append(str(self))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", counted_resolve)
    project_tree_dirs(str(root), str(root))

    assert len(calls) <= 3


def test_sibling_project_dirs_lists_project_root_peers(tmp_path):
    parent = tmp_path / "work"
    proj = parent / "proj_x"
    ceshi7 = parent / "ceshi7"
    ceshi8 = parent / "ceshi8"
    proj.mkdir(parents=True)
    ceshi7.mkdir()
    ceshi8.mkdir()

    dirs = sibling_project_dirs(str(proj), str(proj))
    names = [os.path.basename(p) for p in dirs]
    assert {"proj_x", "ceshi7", "ceshi8"} <= set(names)


# ── 控件 ──────────────────────────────────────────────────────────────────

def test_widget_no_project_placeholder():
    w = WorkspaceBreadcrumb(_Ctx())
    w.refresh()
    assert "选择工作区" in w.text()
    assert w._btn_folder is None
    got = []
    w.navigate_requested.connect(got.append)
    menu = w._build_placeholder_menu()
    labels = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert any("最近使用" in s for s in labels)
    assert any("选择已有文件夹" in s for s in labels)
    # 用户 2026-07-12: 顶栏「选择工作区 ▾」里也要能一次建好「项目 + 采样点」
    # §7 旧: assert any("新建工作区" in s for s in labels)
    panel = menu.findChild(QWidget, "WorkspaceLocationPanel")
    assert panel is not None
    buttons = [b.text() for b in panel.findChildren(QPushButton)]
    assert "＋ 项目" in buttons
    assert "＋ 下级目录" in buttons
    tree = next(a for a in menu.actions() if "打开项目树" in a.text())
    tree.trigger()
    assert got == ["project_tree"]


def test_topbar_location_panel_shows_project_and_save_folder(tmp_path):
    root = tmp_path / "江苏盐城2026"
    workspace = root / "断面A"
    workspace.mkdir(parents=True)
    w = WorkspaceBreadcrumb(_Ctx(str(workspace), str(root)))

    menu = w._btn_folder.menu()
    panel = menu.findChild(QWidget, "WorkspaceLocationPanel")
    assert panel is not None
    buttons = [b.text() for b in panel.findChildren(QPushButton)]
    labels = [x.text() for x in panel.findChildren(QLabel)]

    assert buttons == ["＋ 项目", "＋ 下级目录"]
    assert "江苏盐城2026" in labels
    assert "断面A" in labels


def test_placeholder_menu_lists_recent_workspaces(tmp_path, monkeypatch):
    import json
    recent = tmp_path / "user_projects.json"
    project = tmp_path / "航次2026" / "断面A"
    project.mkdir(parents=True)
    recent.write_text(json.dumps({
        "version": 1,
        "projects": [{
            "name": "航次2026 / 断面A",
            "directory": str(project),
            "root": str(project.parent),
        }],
    }, ensure_ascii=False), encoding="utf-8")
    from app.services import project_service
    monkeypatch.setattr(
        project_service,
        "default_user_projects_json_path",
        lambda: str(recent),
    )
    w = WorkspaceBreadcrumb(_Ctx())
    w.refresh()

    menu = w._build_placeholder_menu()
    recent_menu = next(a.menu() for a in menu.actions() if a.menu() and "最近使用" in a.text())
    labels = [a.text() for a in recent_menu.actions()]

    assert any("航次2026 / 断面A" in s for s in labels)


def test_widget_shows_chain(tmp_path):
    root, sect = _make_tree(tmp_path)
    w = WorkspaceBreadcrumb(_Ctx(str(sect / "B2"), str(root)))
    w.refresh()
    assert "航次2026" in w.text()
    assert "断面A" in w.text()
    assert "B2" in w.text()


def test_refresh_does_not_scan_entire_project_tree(tmp_path, monkeypatch):
    """Top-bar refresh is a hot path and must not recurse through the disk."""
    root, sect = _make_tree(tmp_path)
    calls = []

    def record_scan(*args, **kwargs):
        calls.append((args, kwargs))
        return []

    monkeypatch.setattr(
        "app.widgets.workspace_breadcrumb.project_tree_dirs",
        record_scan,
    )
    w = WorkspaceBreadcrumb(_Ctx(str(sect / "B2"), str(root)))
    w.refresh()

    assert calls == []
    w._build_sibling_menu()
    assert len(calls) == 1


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


# ── ▾ + 新建文件夹 ──────────────────────────────────────────────────────


def test_dropdown_has_new_section_action(tmp_path):
    root, sect = _make_tree(tmp_path)
    w = WorkspaceBreadcrumb(_Ctx(str(sect / "B2"), str(root)))
    w.refresh()
    menu = w._build_sibling_menu()
    labels = [a.text() for a in menu.actions()]
    assert any("新建文件夹" in s for s in labels)


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
    # 建在当前工作区父目录下。
    assert (sect / "20260612(草埔村)").is_dir()
    # 走了 enter_workspace
    assert calls and os.path.basename(calls[-1][0]) == "20260612(草埔村)"
    # 入历史
    assert str((sect / "20260612(草埔村)").resolve()) in w._history


def test_create_section_under_root_makes_child_not_peer(tmp_path, monkeypatch):
    root = tmp_path / "proj_x"
    root.mkdir()
    ctx = _Ctx(str(root), str(root))
    w = WorkspaceBreadcrumb(ctx)
    w.refresh()
    calls = []
    _patch_enter(monkeypatch, calls)

    new_path = w.create_and_enter_section("子目录1")

    assert new_path == str((root / "子目录1").resolve())
    assert (root / "子目录1").is_dir()
    assert not (tmp_path / "子目录1").exists()
    assert calls[-1] == (str(root / "子目录1"), str(root))


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


def test_dropdown_lists_project_tree_marks_workspaces(tmp_path):
    root, sect = _make_tree(tmp_path)
    w = WorkspaceBreadcrumb(_Ctx(str(sect / "B2"), str(root)))
    w.refresh()
    menu = w._build_sibling_menu()
    sib_menu = next(a.menu() for a in menu.actions() if a.menu() and "本项目内" in a.text())
    sib_labels = [a.text() for a in sib_menu.actions()]
    assert len(sib_labels) >= 5
    assert any("B1" in s for s in sib_labels)
    # B2 是工作区 → 带工作区标记
    assert any("工作区" in s and "B2" in s for s in sib_labels)


def test_dropdown_root_workspace_lists_child_dirs(tmp_path):
    root, sect = _make_tree(tmp_path)
    w = WorkspaceBreadcrumb(_Ctx(str(root), str(root)))
    w.refresh()
    menu = w._build_sibling_menu()
    tree_menu = next(a.menu() for a in menu.actions() if a.menu() and "本项目内" in a.text())
    labels = [a.text() for a in tree_menu.actions()]
    assert any("断面A" in s for s in labels)
    assert any(os.path.join("断面A", "B2") in s for s in labels)


def test_dropdown_lists_peer_project_dirs(tmp_path):
    parent = tmp_path / "work"
    proj = parent / "proj_x"
    ceshi7 = parent / "ceshi7"
    ceshi8 = parent / "ceshi8"
    proj.mkdir(parents=True)
    ceshi7.mkdir()
    (ceshi8 / "_data").mkdir(parents=True)
    (ceshi8 / "_data" / "project.db").write_text("")

    w = WorkspaceBreadcrumb(_Ctx(str(proj), str(proj)))
    w.refresh()
    menu = w._build_sibling_menu()
    peer_menu = next(
        a.menu() for a in menu.actions() if a.menu() and "磁盘上的其他项目" in a.text()
    )
    peer_labels = [a.text() for a in peer_menu.actions()]
    assert any("ceshi7" in s for s in peer_labels)
    assert any("工作区" in s and "ceshi8" in s for s in peer_labels)
    top_labels = [
        a.text() for a in menu.actions()
        if a.text() and not a.isSeparator() and not a.menu()
        and not a.text().startswith("当前：")
    ]
    assert not any("ceshi7" in s for s in top_labels)
    assert not any("ceshi8" in s for s in top_labels)


def test_switch_peer_root_uses_peer_as_root(tmp_path, monkeypatch):
    parent = tmp_path / "work"
    proj = parent / "proj_x"
    ceshi7 = parent / "ceshi7"
    proj.mkdir(parents=True)
    ceshi7.mkdir()
    ctx = _Ctx(str(proj), str(proj))
    w = WorkspaceBreadcrumb(ctx)
    w.refresh()
    calls = []
    _patch_enter(monkeypatch, calls)

    w._switch_to_peer_root(str(ceshi7))

    assert calls[-1] == (str(ceshi7), str(ceshi7))


def test_dropdown_groups_current_project_tree_recent_and_new(tmp_path):
    root, sect = _make_tree(tmp_path)
    w = WorkspaceBreadcrumb(_Ctx(str(sect / "B2"), str(root)))
    w.refresh()
    menu = w._build_sibling_menu()
    labels = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert labels[0] == "当前：B2"
    assert any("本项目内" in s for s in labels)
    assert any("最近使用" in s for s in labels)
    assert labels[-1].endswith("新建文件夹…")


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
    recent = next(a.menu() for a in menu.actions() if a.menu() and "最近使用" in a.text())
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


def test_recent_label_adds_parent_for_short_names():
    item = {"name": "ce", "directory": "/tmp/survey/ce"}
    assert WorkspaceBreadcrumb._recent_label(item) == "survey / ce"
    item2 = {"name": "survey / ce", "directory": "/tmp/survey/ce"}
    assert WorkspaceBreadcrumb._recent_label(item2) == "survey / ce"


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


def test_shows_project_name_when_root_set_but_no_workspace(qtbot, tmp_path):
    """刚建完空项目、还没建采样点(需求 2026-07-12)。

    新建项目现在只建一个空项目目录(容器, 非工作区)。那一刻没有工作区可显示, 面包屑
    旧行为退回「选择工作区 ▾」—— 用户刚建完项目却看不到项目名, 会以为没建成。
    """
    root = tmp_path / "江苏盐城2026"
    root.mkdir()
    w = WorkspaceBreadcrumb(_Ctx(None, str(root)))
    qtbot.addWidget(w)

    w.refresh()

    text = w.text()
    assert "江苏盐城2026" in text
    assert "未选采样点" in text


def test_still_plain_placeholder_when_no_root(qtbot):
    """§7 回归: 完全没项目根时仍是「选择工作区 ▾」。"""
    w = WorkspaceBreadcrumb(_Ctx(None, None))
    qtbot.addWidget(w)

    w.refresh()

    assert "选择工作区" in w.text()
