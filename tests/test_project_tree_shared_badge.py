"""tests/test_project_tree_shared_badge.py — 共享工作区标识(纯判定).

Claude Code 2026-07-15 — 用户定稿: 团队共做一个项目, 一个统一项目树, 不要单独协作
接口/按钮; 共享的工作区只加个小标识。这里测承重的纯判定逻辑 is_workspace_shared。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from app.views.project_tree_view import is_workspace_shared


def test_path_in_shared_set_is_shared():
    shared = {"/survey/gx/断面1", "/survey/gx/断面2"}
    assert is_workspace_shared("/survey/gx/断面1", shared) is True


def test_path_not_in_set_is_not_shared():
    shared = {"/survey/gx/断面1"}
    assert is_workspace_shared("/survey/gx/断面9", shared) is False


def test_empty_inputs_are_not_shared():
    assert is_workspace_shared("", {"/a"}) is False
    assert is_workspace_shared("/a", set()) is False
    assert is_workspace_shared("/a", None) is False


def test_tolerates_normalization_diff(tmp_path):
    # 同一目录的两种写法(带/不带尾斜杠)应判为同一个
    d = tmp_path / "断面1"
    d.mkdir()
    shared = {str(d)}
    assert is_workspace_shared(str(d) + os.sep, shared) is True
