"""tests/test_project_settings_service.py"""
from __future__ import annotations

import sqlite3
import pytest

from app.services.project_settings_service import (
    load_setting,
    save_setting,
    DEFAULT_TIFF_FIELDS,
    DEFAULT_PERSONNEL,
    DEFAULT_CODE_LABELS,
    DEFAULT_PROJECT_META,
    BUILTIN_STORAGES,
)


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE project_settings (setting_key TEXT PRIMARY KEY, value_json TEXT NOT NULL DEFAULT '{}')"
    )
    conn.commit()
    yield conn
    conn.close()


def test_load_missing_returns_default(db):
    result = load_setting(db, "tiff_fields", DEFAULT_TIFF_FIELDS)
    assert result == DEFAULT_TIFF_FIELDS
    assert result is not DEFAULT_TIFF_FIELDS  # copy, not same object


def test_save_and_load_roundtrip(db):
    data = {"collector": "张三", "photographer": "李四", "verifier": "", "logistics": "", "identifier": ""}
    save_setting(db, "personnel", data)
    result = load_setting(db, "personnel", DEFAULT_PERSONNEL)
    assert result == data


def test_save_overwrites(db):
    save_setting(db, "personnel", {"collector": "A"})
    save_setting(db, "personnel", {"collector": "B"})
    assert load_setting(db, "personnel", {})["collector"] == "B"


def test_tiff_defaults_match_oracle():
    assert DEFAULT_TIFF_FIELDS["uniqueId"] is True
    assert DEFAULT_TIFF_FIELDS["projectName"] is True
    assert DEFAULT_TIFF_FIELDS["geoArea"] is False
    assert DEFAULT_TIFF_FIELDS["taxonGroup"] is False
    assert DEFAULT_TIFF_FIELDS["photoNotes"] is True
    assert len(DEFAULT_TIFF_FIELDS) == 17


def test_builtin_storages_count():
    assert len(BUILTIN_STORAGES) == 15
    codes = [s["code"] for s in BUILTIN_STORAGES]
    assert "T95E" in codes
    assert "RGLU" in codes
    rna = [s for s in BUILTIN_STORAGES if s["transcriptome"]]
    assert len(rna) == 8


def test_code_labels_default_structure():
    assert "province" in DEFAULT_CODE_LABELS
    assert "stations" in DEFAULT_CODE_LABELS
    assert isinstance(DEFAULT_CODE_LABELS["stations"], dict)


# ── get_effective: inheritance along the folder tree ─────────────────────────
# This is the core of the folder-tree-inherit feature and was previously
# untested. A workspace inherits 区域级 settings from ancestor folders that have
# their own _data/project.db; `root` bounds that walk so inheritance never leaks
# from folders outside the chosen survey tree.

from pathlib import Path
from app.services.project_settings_service import get_effective


def _seed_settings(dir_path: Path, key: str, data: dict) -> None:
    """Create <dir>/_data/project.db with one project_settings row."""
    import json as _json
    data_dir = dir_path / "_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(data_dir / "project.db"))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS project_settings "
            "(setting_key TEXT PRIMARY KEY, value_json TEXT NOT NULL DEFAULT '{}')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO project_settings VALUES (?, ?)",
            (key, _json.dumps(data)),
        )
        conn.commit()
    finally:
        conn.close()


class TestGetEffectiveInheritance:
    def test_leaf_inherits_from_region_root(self, tmp_path):
        # 南海采集2026/雷州岛 (region: province=广东) → 断面a (leaf, no own setting)
        region = tmp_path / "南海采集2026" / "雷州岛"
        leaf = region / "断面a"
        leaf.mkdir(parents=True)
        _seed_settings(region, "code_labels", {"province": "广东", "site": "雷州"})
        eff = get_effective(str(leaf), "code_labels", DEFAULT_CODE_LABELS, root=str(region))
        assert eff["province"] == "广东"
        assert eff["site"] == "雷州"

    def test_nearer_overrides_farther(self, tmp_path):
        region = tmp_path / "雷州岛"
        leaf = region / "断面a"
        leaf.mkdir(parents=True)
        _seed_settings(region, "code_labels", {"province": "广东", "site": "雷州"})
        _seed_settings(leaf, "code_labels", {"site": "断面a东段"})  # nearer wins
        eff = get_effective(str(leaf), "code_labels", DEFAULT_CODE_LABELS, root=str(region))
        assert eff["province"] == "广东"          # inherited from region
        assert eff["site"] == "断面a东段"          # overridden locally

    def test_root_bounds_the_walk(self, tmp_path):
        # An UNRELATED survey above root must NOT leak its settings.
        outside = tmp_path                        # province=江苏 (outside the tree)
        region = tmp_path / "雷州岛"
        leaf = region / "断面a"
        leaf.mkdir(parents=True)
        _seed_settings(outside, "code_labels", {"province": "江苏"})
        _seed_settings(region, "code_labels", {"province": "广东"})
        eff = get_effective(str(leaf), "code_labels", DEFAULT_CODE_LABELS, root=str(region))
        assert eff["province"] == "广东"          # bounded — never sees 江苏

    def test_empty_value_does_not_override(self, tmp_path):
        region = tmp_path / "雷州岛"
        leaf = region / "断面a"
        leaf.mkdir(parents=True)
        _seed_settings(region, "code_labels", {"province": "广东"})
        _seed_settings(leaf, "code_labels", {"province": ""})  # empty must not clobber
        eff = get_effective(str(leaf), "code_labels", DEFAULT_CODE_LABELS, root=str(region))
        assert eff["province"] == "广东"
