"""tests/test_project_tree_service.py — 文件夹树扫描."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from app.services.project_tree_service import (
    discover_project_roots,
    scan_tree,
    is_workspace,
    is_workspace_candidate,
    flatten_workspaces,
    discover_workspaces,
    discover_workspace_candidates,
    clear_project_tree_cache,
    RESERVED_DIR_NAMES,
)


def test_discovers_app_created_project_roots_not_just_workspaces(tmp_path):
    """更新后重建索引时，必须识别 _data/region.json 项目根。"""
    project = tmp_path / "项目仓库" / "航次2026"
    marker = project / "_data" / "region.json"
    marker.parent.mkdir(parents=True)
    marker.write_text('{"name": "航次2026"}', encoding="utf-8")
    (project / "断面A").mkdir()

    found = discover_project_roots(str(tmp_path / "项目仓库"), max_depth=3)

    assert [Path(row["path"]).name for row in found] == ["航次2026"]


def _make_workspace(dir_path: Path) -> None:
    (dir_path / "_data").mkdir(parents=True, exist_ok=True)
    sqlite3.connect(str(dir_path / "_data" / "project.db")).close()


def test_scan_nested_folders(tmp_path):
    root = tmp_path / "雷州半岛多样性"
    for name in ("断面a", "断面b", "断面c"):
        (root / name).mkdir(parents=True)
    tree = scan_tree(str(root))
    assert tree["name"] == "雷州半岛多样性"
    kids = sorted(c["name"] for c in tree["children"])
    assert kids == ["断面a", "断面b", "断面c"]


def test_skips_reserved_and_dotfiles(tmp_path):
    root = tmp_path / "proj"
    (root / "断面a").mkdir(parents=True)
    for r in RESERVED_DIR_NAMES:
        (root / r).mkdir(parents=True)
    (root / ".hidden").mkdir()
    tree = scan_tree(str(root))
    names = [c["name"] for c in tree["children"]]
    assert names == ["断面a"]


def test_legacy_retired_zip_is_hidden_without_deleting_it(tmp_path):
    root = tmp_path / "proj"
    retired = root / "_retired-zip"
    retired.mkdir(parents=True)
    archive = retired / "legacy-backup.zip"
    archive.write_bytes(b"kept")

    tree = scan_tree(str(root))

    assert "_retired-zip" not in [child["name"] for child in tree["children"]]
    assert archive.read_bytes() == b"kept"


def test_has_data_flag_marks_adopted_workspaces(tmp_path):
    root = tmp_path / "proj"
    leaf = root / "断面a"
    leaf.mkdir(parents=True)
    (root / "断面b").mkdir()
    _make_workspace(leaf)
    tree = scan_tree(str(root))
    by_name = {c["name"]: c for c in tree["children"]}
    assert by_name["断面a"]["has_data"] is True
    assert by_name["断面b"]["has_data"] is False
    assert is_workspace(str(leaf)) is True


def test_workspace_candidate_recognizes_legacy_layout(tmp_path):
    legacy = tmp_path / "ceshi8"
    (legacy / "_data").mkdir(parents=True)
    (legacy / "incoming-jpg").mkdir()
    assert is_workspace(str(legacy)) is False
    assert is_workspace_candidate(str(legacy)) is True


def test_discover_workspace_candidates_finds_legacy_and_db_dirs(tmp_path):
    root = tmp_path / "root"
    db_ws = root / "ceshi6"
    legacy = root / "ceshi8"
    plain = root / "plain"
    db_ws.mkdir(parents=True)
    legacy.mkdir()
    plain.mkdir()
    _make_workspace(db_ws)
    (legacy / "_data").mkdir()
    (legacy / "incoming-jpg").mkdir()

    found = discover_workspace_candidates(str(root), max_depth=1)
    by_name = {Path(x["path"]).name: x for x in found}
    assert set(by_name) == {"ceshi6", "ceshi8"}
    assert by_name["ceshi6"]["has_data"] is True
    assert by_name["ceshi8"]["has_data"] is False


def test_default_depth_reaches_six_levels(tmp_path):
    # Real field directories nest deeper than the old default of 4
    # (航次/区域/岛/断面/站位/…). Default must reach a 6-level-deep leaf.
    root = tmp_path / "r"
    deep = root / "L1" / "L2" / "L3" / "L4" / "L5" / "L6"
    deep.mkdir(parents=True)
    tree = scan_tree(str(root))  # default max_depth
    node = tree
    for lvl in ("L1", "L2", "L3", "L4", "L5", "L6"):
        node = next((c for c in node["children"] if c["name"] == lvl), None)
        assert node is not None, f"default scan did not reach {lvl}"


def test_max_depth_limits_recursion(tmp_path):
    root = tmp_path / "r"
    (root / "a" / "b" / "c" / "d").mkdir(parents=True)
    tree = scan_tree(str(root), max_depth=2)
    a = tree["children"][0]
    b = a["children"][0]
    assert a["name"] == "a"
    assert b["name"] == "b"
    assert b["children"] == []  # depth capped before c


def test_does_not_create_anything(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    scan_tree(str(root))
    assert list(root.iterdir()) == []  # scan must not create files/dirs


def test_scan_tree_cache_returns_copy(tmp_path):
    clear_project_tree_cache()
    root = tmp_path / "r"
    (root / "a").mkdir(parents=True)
    tree = scan_tree(str(root))
    tree["children"].clear()

    fresh = scan_tree(str(root))

    assert [c["name"] for c in fresh["children"]] == ["a"]


def test_clear_project_tree_cache_refreshes_deep_change(tmp_path):
    clear_project_tree_cache()
    root = tmp_path / "r"
    child = root / "a"
    child.mkdir(parents=True)
    scan_tree(str(root))
    (child / "b").mkdir()

    clear_project_tree_cache(str(root))
    tree = scan_tree(str(root))

    a = tree["children"][0]
    assert [c["name"] for c in a["children"]] == ["b"]


def test_flatten_workspaces_preorder():
    # Hand-built node dict; collect only has_data=True paths, root first.
    node = {
        "name": "root",
        "path": "/r",
        "has_data": True,
        "children": [
            {
                "name": "A",
                "path": "/r/A",
                "has_data": True,
                "children": [
                    {"name": "sub", "path": "/r/A/sub", "has_data": True, "children": []},
                ],
            },
            {
                "name": "B",
                "path": "/r/B",
                "has_data": False,
                "children": [
                    {"name": "C", "path": "/r/B/C", "has_data": True, "children": []},
                ],
            },
        ],
    }
    assert flatten_workspaces(node) == ["/r", "/r/A", "/r/A/sub", "/r/B/C"]


def test_flatten_workspaces_none_when_no_data():
    node = {"name": "root", "path": "/r", "has_data": False, "children": []}
    assert flatten_workspaces(node) == []


def test_discover_workspaces_relative_labels(tmp_path):
    root = tmp_path / "survey"
    a = root / "A"
    sub = a / "sub"
    b = root / "B"
    a.mkdir(parents=True)
    sub.mkdir(parents=True)
    b.mkdir(parents=True)  # plain folder, no _data
    _make_workspace(a)
    _make_workspace(sub)

    found = discover_workspaces(str(root))
    rels = [w["rel"] for w in found]
    assert rels == ["A", os.path.join("A", "sub")]  # pre-order, B excluded
    by_rel = {w["rel"]: w for w in found}
    assert by_rel["A"]["name"] == "A"
    assert by_rel["A"]["path"] == str(a)
    assert by_rel[os.path.join("A", "sub")]["name"] == os.path.join("A", "sub")


def test_discover_workspaces_root_is_workspace(tmp_path):
    root = tmp_path / "survey"
    root.mkdir()
    _make_workspace(root)

    found = discover_workspaces(str(root))
    assert len(found) == 1
    assert found[0]["rel"] == "."
    assert found[0]["name"] == os.path.basename(str(root))
    assert found[0]["name"] == "survey"


def test_discover_workspaces_empty_when_none(tmp_path):
    root = tmp_path / "survey"
    (root / "plain1").mkdir(parents=True)
    (root / "plain2").mkdir()
    assert discover_workspaces(str(root)) == []


def test_discover_workspaces_never_returns_reserved_dirs(tmp_path):
    root = tmp_path / "survey"
    leaf = root / "断面a"
    leaf.mkdir(parents=True)
    _make_workspace(leaf)
    # _make_workspace already created leaf/_data; add another reserved dir.
    (root / "incoming-jpg").mkdir()
    found = discover_workspaces(str(root))
    names = {os.path.basename(w["path"]) for w in found}
    assert "_data" not in names
    assert "incoming-jpg" not in names
    assert names == {"断面a"}
