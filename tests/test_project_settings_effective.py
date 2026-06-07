"""tests/test_project_settings_effective.py — 设置沿目录树继承 (get_effective)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.services.project_settings_service import (
    get_effective,
    effective_new_specimen_prefill,
    save_setting,
    DEFAULT_CODE_LABELS,
)


def _make_project(dir_path: Path, settings: dict | None = None) -> None:
    """Create <dir>/_data/project.db with a project_settings table + optional rows."""
    data_dir = dir_path / "_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(data_dir / "project.db"))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS project_settings "
        "(setting_key TEXT PRIMARY KEY, value_json TEXT NOT NULL DEFAULT '{}')"
    )
    conn.commit()
    for k, v in (settings or {}).items():
        save_setting(conn, k, v)
    conn.close()


def _cl(province="", site="", stations=None, species=None) -> dict:
    return {
        "province": province,
        "site": site,
        "stations": stations or {},
        "species": species or {},
    }


def test_effective_inherits_from_ancestor(tmp_path):
    root = tmp_path / "雷州半岛多样性"
    leaf = root / "断面a"
    _make_project(root, {"code_labels": _cl("GD", "雷州", {"S1": "一号"})})
    _make_project(leaf, {})  # leaf has its own db but no code_labels
    eff = get_effective(str(leaf), "code_labels", DEFAULT_CODE_LABELS, root=str(root))
    assert eff["province"] == "GD"
    assert eff["site"] == "雷州"
    assert eff["stations"] == {"S1": "一号"}


def test_leaf_overrides_ancestor_but_empty_does_not(tmp_path):
    root = tmp_path / "proj"
    leaf = root / "厦门"
    _make_project(root, {"code_labels": _cl("GD", "雷州", {"S1": "一号"})})
    _make_project(leaf, {"code_labels": _cl("FJ", "", {"B2": "北滩"})})
    eff = get_effective(str(leaf), "code_labels", DEFAULT_CODE_LABELS, root=str(root))
    assert eff["province"] == "FJ"                       # leaf non-empty overrides
    assert eff["site"] == "雷州"                          # leaf empty does NOT override
    assert eff["stations"] == {"S1": "一号", "B2": "北滩"}  # nested dict merges


def test_missing_returns_default_copy(tmp_path):
    leaf = tmp_path / "nodb"
    eff = get_effective(str(leaf), "code_labels", DEFAULT_CODE_LABELS)
    assert eff == DEFAULT_CODE_LABELS
    assert eff is not DEFAULT_CODE_LABELS


def test_does_not_create_any_db(tmp_path):
    leaf = tmp_path / "a" / "b" / "c"
    leaf.mkdir(parents=True)
    get_effective(str(leaf), "code_labels", DEFAULT_CODE_LABELS)
    assert not (leaf / "_data" / "project.db").exists()
    assert not (tmp_path / "a" / "_data").exists()
    assert not (tmp_path / "a" / "b" / "_data").exists()


def test_prefill_combines_codelabels_and_personnel_up_tree(tmp_path):
    root = tmp_path / "雷州半岛多样性"
    leaf = root / "断面a"
    _make_project(root, {
        "code_labels": _cl("GD", "雷州", {"S1": "一号", "S2": "二号"}),
        "personnel": {"collector": "张三", "photographer": "李四",
                      "identifier": "", "verifier": "", "logistics": ""},
    })
    _make_project(leaf, {})
    pf = effective_new_specimen_prefill(str(leaf), root=str(root))
    assert pf["province"] == "GD"
    assert pf["site"] == "雷州"
    assert pf["stations"] == {"S1": "一号", "S2": "二号"}
    assert pf["collector"] == "张三"
    assert pf["photographer"] == "李四"
    assert pf["identifier"] == ""


def test_prefill_empty_when_no_settings(tmp_path):
    leaf = tmp_path / "blank"
    pf = effective_new_specimen_prefill(str(leaf))
    assert pf == {"province": "", "site": "", "stations": {},
                  "collector": "", "photographer": "", "identifier": ""}


def test_stops_at_root(tmp_path):
    above = tmp_path / "ABOVE"
    root = above / "proj"
    leaf = root / "leaf"
    _make_project(above, {"code_labels": _cl("SHOULD_NOT_APPEAR")})
    _make_project(root, {"code_labels": _cl("GD")})
    _make_project(leaf, {})
    eff = get_effective(str(leaf), "code_labels", DEFAULT_CODE_LABELS, root=str(root))
    assert eff["province"] == "GD"
