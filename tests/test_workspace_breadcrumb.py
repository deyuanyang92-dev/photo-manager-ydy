"""test_workspace_breadcrumb.py — 顶栏项目与照片保存位置入口.

契约：
  - breadcrumb_chain(root, ws)：根→当前工作区的 (name, path) 链；不在根下→只剩叶子；
    无工作区→空链。
  - sibling_dirs(ws)：同级目录（含自身），过滤文件/点号目录/RESERVED_DIR_NAMES，排序。
  - WorkspaceBreadcrumb：
      * 无项目 → text() 含「新建或打开项目」，点击 → 打开场景化入口。
      * 有链   → text() = "根 / 断面A / B2"（>3 级折叠中间为 …）。
      * 访问历史保留为内部能力，不在顶栏显示一组含义不清的小箭头。
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

from PyQt6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton, QWidget

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
    assert "新建或打开项目" in w.text()
    assert w._btn_folder is w._placeholder_btn
    assert w._btn_menu is w._placeholder_btn
    assert w._placeholder_btn.objectName() == "WorkspaceLocationSwitcher"
    got = []
    w.navigate_requested.connect(got.append)
    menu = w._build_placeholder_menu()
    panel = menu.findChild(QWidget, "WorkspaceLocationPanel")
    assert panel is not None
    buttons = {b.text(): b for b in panel.findChildren(QPushButton)}
    assert "＋ 新建调查项目" in buttons
    assert "＋ 独立工作区" in buttons
    assert "＋ 追加到当前项目…" in buttons
    assert "打开已有项目或工作区" in buttons
    assert "管理全部项目" in buttons
    buttons["管理全部项目"].click()
    assert got == ["project_tree"]


def test_topbar_location_panel_shows_project_and_save_folder(tmp_path):
    root = tmp_path / "江苏盐城2026"
    workspace = root / "断面A"
    workspace.mkdir(parents=True)
    w = WorkspaceBreadcrumb(_Ctx(str(workspace), str(root)))

    menu = w._btn_folder.menu()
    w._populate_workspace_menu(menu)
    panel = menu.findChild(QWidget, "WorkspaceLocationPanel")
    assert panel is not None
    buttons = [b.text() for b in panel.findChildren(QPushButton)]
    labels = [x.text() for x in panel.findChildren(QLabel)]

    assert "＋ 新建调查项目" in buttons
    assert "＋ 独立工作区" in buttons
    assert any("江苏盐城2026" in b for b in buttons), buttons
    assert any("断面A" in b for b in buttons), buttons
    assert "项目" in labels
    assert "拍摄位置" in labels


def test_unified_location_panel_searches_the_global_project_tree(tmp_path):
    root, sect = _make_tree(tmp_path)
    w = WorkspaceBreadcrumb(_Ctx(str(sect / "B2"), str(root)))
    menu = w._btn_folder.menu()
    w._populate_workspace_menu(menu)
    panel = menu.findChild(QWidget, "WorkspaceLocationPanel")
    search = panel.findChild(QLineEdit, "WorkspaceLocationSearch")
    got = []
    w.project_search_requested.connect(got.append)

    search.setText("断面A")
    search.returnPressed.emit()

    assert got == ["断面A"]


def test_location_panel_append_action_uses_current_project(tmp_path):
    root, sect = _make_tree(tmp_path)
    w = WorkspaceBreadcrumb(_Ctx(str(sect / "B2"), str(root)))
    menu = w._btn_folder.menu()
    w._populate_workspace_menu(menu)
    panel = menu.findChild(QWidget, "WorkspaceLocationPanel")
    append = panel.findChild(QPushButton, "WorkspaceAppendCurrent")
    got = []
    w.new_project_child_requested.connect(lambda: got.append(True))

    assert append.isEnabled()
    append.click()

    assert got == [True]


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
    panel = menu.findChild(QWidget, "WorkspaceLocationPanel")
    labels = [
        button.text()
        for button in panel.findChildren(QPushButton)
        if button.objectName() == "WorkspaceRecentChip"
    ]

    assert any("航次2026" in label and "断面A" in label for label in labels)


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


def test_location_switcher_consolidates_chain_and_keeps_tree_entry(tmp_path):
    root, sect = _make_tree(tmp_path)
    w = WorkspaceBreadcrumb(_Ctx(str(sect / "B2"), str(root)))
    w.refresh()
    got = []
    w.navigate_requested.connect(got.append)

    assert w._segment_btns == []
    assert w._btn_menu is w._leaf_btn
    assert w._btn_folder is w._leaf_btn
    assert "航次2026 › 断面A › B2" == w._leaf_btn.text()
    assert w._leaf_btn.menu().parent() is w._leaf_btn
    menu = w._leaf_btn.menu()
    w._populate_workspace_menu(menu)
    panel = menu.findChild(QWidget, "WorkspaceLocationPanel")
    tree = panel.findChild(QPushButton, "WorkspaceManageAll")
    tree.click()
    assert got == ["project_tree"]


def test_topbar_does_not_render_history_arrow_group(tmp_path):
    root, sect = _make_tree(tmp_path)
    w = WorkspaceBreadcrumb(_Ctx(str(sect / "B2"), str(root)))

    assert w._btn_prev is None
    assert w._btn_next is None
    assert w.findChild(QWidget, "WorkspaceHistoryNav") is None


def test_topbar_stays_a_single_location_control(tmp_path):
    root, sect = _make_tree(tmp_path)
    ctx = _Ctx(str(sect / "B2"), str(root))
    w = WorkspaceBreadcrumb(ctx)

    assert w.text() == "航次2026 › 断面A › B2"
    assert w._segment_btns == []
    assert not hasattr(w, "_settings_btn")
    assert w._leaf_btn.objectName() == "WorkspaceLocationSwitcher"


def test_refresh_preserves_parent_update_suppression(tmp_path):
    root, sect = _make_tree(tmp_path)
    w = WorkspaceBreadcrumb(_Ctx(str(sect / "B2"), str(root)))
    w.setUpdatesEnabled(False)

    w.refresh()

    assert not w.updatesEnabled()


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
    assert w._btn_prev is None and w._btn_next is None


def test_internal_back_forward_navigates_history(tmp_path, monkeypatch):
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
    # 内部历史回到 B2
    w._history_step(-1)
    assert os.path.basename(calls[-1][0]) == "B2"
    assert w._history_pos == 0
    # 再前进到 B3
    w._history_step(+1)
    assert os.path.basename(calls[-1][0]) == "B3"
    assert w._history_pos == 1
    assert changed  # 每次切换都发 workspace_changed


def test_history_branch_truncates(tmp_path, monkeypatch):
    root, sect = _make_tree(tmp_path)
    ctx = _Ctx(str(sect / "B2"), str(root))
    w = WorkspaceBreadcrumb(ctx)
    w.refresh()
    _patch_enter(monkeypatch, [])
    # B2 → B3 → 回B2 → 切B1：B3 应被截断
    w._switch_to(str(sect / "B3"))
    w._history_step(-1)                      # 回 B2 (pos0)
    w._switch_to(str(sect / "B1"))           # 新分支
    assert w._history == [str((sect / "B2").resolve()),
                          str((sect / "B1").resolve())]
    assert w._history_pos == 1
    before = w._history_pos
    w._history_step(+1)
    assert w._history_pos == before          # B3 已丢，无前进


def test_history_no_wraparound(tmp_path, monkeypatch):
    root, sect = _make_tree(tmp_path)
    ctx = _Ctx(str(sect / "B2"), str(root))
    w = WorkspaceBreadcrumb(ctx)
    w.refresh()
    calls = []
    _patch_enter(monkeypatch, calls)
    n_before = len(calls)
    # 只有一条历史时前后移动都不动作
    w._history_step(-1)
    w._history_step(+1)
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


def test_root_workspace_keeps_internal_history(tmp_path, monkeypatch):
    """根即工作区时访问历史仍有效，但顶栏不显示箭头."""
    root, _ = _make_tree(tmp_path)
    other = tmp_path / "另一航次"
    other.mkdir()
    ctx = _Ctx(str(root), str(root))
    w = WorkspaceBreadcrumb(ctx)
    w.refresh()
    assert w._btn_prev is None and w._btn_next is None
    _patch_enter(monkeypatch, [])
    w._switch_to(str(other))                 # 切别处建历史
    w._history_step(-1)                      # 回 root
    assert w._history_pos == 0
    w._history_step(+1)                      # 可再次前进
    assert w._history_pos == 1


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
    assert any(s == "ceshi8" for s in peer_labels)
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


def test_recent_workspaces_excludes_project_only_records(tmp_path, monkeypatch):
    import json
    from app.services import project_service

    root = tmp_path / "广西调查2026"
    workspace = root / "北海区域" / "断面A"
    workspace.mkdir(parents=True)
    recent_json = tmp_path / "user_projects.json"
    recent_json.write_text(json.dumps({
        "version": 1,
        "projects": [
            {"name": root.name, "directory": str(root), "isProjectRoot": True},
            {"name": workspace.name, "directory": str(workspace), "root": str(root)},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        project_service, "default_user_projects_json_path", lambda: str(recent_json)
    )

    w = WorkspaceBreadcrumb(_Ctx())
    recent = w._recent_workspaces()

    assert [item["directory"] for item in recent] == [str(workspace.resolve())]


def test_recent_label_adds_parent_for_short_names():
    item = {"name": "ce", "directory": "/tmp/survey/ce"}
    assert WorkspaceBreadcrumb._recent_label(item) == "survey / ce"
    item2 = {"name": "survey / ce", "directory": "/tmp/survey/ce"}
    assert WorkspaceBreadcrumb._recent_label(item2) == "survey / ce"


# ── 定位台变体：主按钮真的进入工作台 ──────────────────────────────────────


def test_locator_enter_button_navigates_to_workbench(tmp_path):
    """定位台(第11种)「进入照片工作台」主按钮必须真进入工作台，而非仅关闭菜单.

    回归：该按钮曾只连 menu.close()，点了没反应（与 om_capture 变体死按钮同类）。
    """
    from PyQt6.QtWidgets import QMenu

    root, sect = _make_tree(tmp_path)
    w = WorkspaceBreadcrumb(_Ctx(str(sect / "B2"), str(root)))
    w._mode_override = "locator"
    w.refresh()
    got = []
    w.navigate_requested.connect(got.append)

    menu = QMenu()
    panel = w._add_locator_panel(menu)
    enter_btn = next(
        b for b in panel.findChildren(QPushButton)
        if b.text() == "进入照片工作台"
    )
    assert enter_btn.isEnabled()  # 有工作区 → 主按钮应可点

    enter_btn.click()

    assert got == ["workbench"]


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
    assert "尚未选择拍摄位置" in text


def test_still_plain_placeholder_when_no_root(qtbot):
    """§7 回归: 完全没项目根时仍是「选择工作区 ▾」。"""
    w = WorkspaceBreadcrumb(_Ctx(None, None))
    qtbot.addWidget(w)

    w.refresh()

    assert "新建或打开项目" in w.text()
