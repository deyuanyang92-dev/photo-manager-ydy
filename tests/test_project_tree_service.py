"""tests/test_project_tree_service.py — 文件夹树扫描."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.services.project_tree_service import scan_tree, is_workspace, RESERVED_DIR_NAMES


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
