"""Tests for project_station_import_service — 项目级站位总表导入与分发。

Build a temp survey root with subfolders:
  T01  already a workspace (project.db materialised),
  T02  a plain folder (mkdir only, no _data),
  (no folder for T03).

A single station total-table carries a 断面 column routing each row to its
subfolder's collection_records. Cover grouping, basename matching, unmatched
skip, distribution writes, db_created flag, snapshot reached, GCJ02→WGS84.
"""
from __future__ import annotations

import os

import pytest

from app.db import db_manager
from app.db.db_manager import open_project_db
from app.services import backup_service
from app.services import project_station_import_service as psis


# ── fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def survey_root(tmp_path):
    """A survey root with T01 (workspace), T02 (plain dir), no T03."""
    root = tmp_path / "survey"
    root.mkdir()
    t01 = root / "T01"
    t01.mkdir()
    # Materialise T01 as an actual workspace (gets _data/project.db).
    open_project_db(str(t01), create=True)
    (root / "T02").mkdir()  # plain folder, NO _data
    yield str(root)
    db_manager.close_all()


# A raw station total-table: 断面 column + province/site/station/经度/纬度.
# T01 rows: one normal WGS84, one GCJ02-shifted (mainland China), one bad coord.
# T02 row: one normal. T03 row: routes to a non-existent folder.
MAPPING = {
    "transect": "断面",
    "province": "地区",
    "site": "断面",          # reuse the transect column as the site too (real data does)
    "station": "站位",
    "lon": "经度",
    "lat": "纬度",
}


def _raw_rows():
    return [
        {"断面": "T01", "地区": "浙江", "站位": "B2", "经度": "121.6543", "纬度": "29.1234"},
        {"断面": "T01", "地区": "浙江", "站位": "B3", "经度": "121.6600", "纬度": "29.1300"},  # GCJ02 input
        {"断面": "T01", "地区": "浙江", "站位": "BAD", "经度": "abc", "纬度": "xyz"},  # unparseable
        {"断面": "T02", "地区": "浙江", "站位": "C1", "经度": "121.7000", "纬度": "29.2000"},
        {"断面": "T03", "地区": "福建", "站位": "L1", "经度": "119.7500", "纬度": "26.4500"},  # no folder
    ]


# ── preview_distribution ───────────────────────────────────────────────────────

def test_preview_groups_and_matches(survey_root):
    plan = psis.preview_distribution(survey_root, _raw_rows(), MAPPING, coord_system="WGS84")

    assert set(plan["matched"].keys()) == {"T01", "T02"}
    assert set(plan["unmatched"].keys()) == {"T03"}

    # T01: 2 ok rows, 1 error row; error surfaced in errors, not ok.
    t01 = plan["matched"]["T01"]
    assert len(t01["rows"]) == 2
    assert len(t01["errors"]) == 1
    assert all(r["ok"] for r in t01["rows"])
    assert all(not r["ok"] for r in t01["errors"])

    # T01 resolves to the real folder.
    assert t01["path"] == os.path.join(survey_root, "T01")
    assert t01["rel"] == "T01"

    # T02: 1 ok row.
    assert len(plan["matched"]["T02"]["rows"]) == 1

    # T03 unmatched, its ok row preserved (never silently dropped).
    assert len(plan["unmatched"]["T03"]["rows"]) == 1

    totals = plan["totals"]
    assert totals["rows"] == 5
    assert totals["ok"] == 4          # B2,B3,C1,L1
    assert totals["errors"] == 1      # BAD
    assert totals["matched_transects"] == 2
    assert totals["unmatched_transects"] == 1


def test_preview_basename_matching(survey_root):
    """A transect value equal to a folder basename matches (D4 basename branch)."""
    rows = [{"断面": "T02", "地区": "浙江", "站位": "C1", "经度": "121.7", "纬度": "29.2"}]
    plan = psis.preview_distribution(survey_root, rows, MAPPING)
    assert "T02" in plan["matched"]
    assert plan["matched"]["T02"]["path"] == os.path.join(survey_root, "T02")


def test_preview_empty_transect_surfaced(survey_root):
    """Rows with an empty 断面 value are surfaced (unmatched group), not dropped."""
    rows = [{"断面": "", "地区": "浙江", "站位": "C1", "经度": "121.7", "纬度": "29.2"}]
    plan = psis.preview_distribution(survey_root, rows, MAPPING)
    # The empty-transect group must appear somewhere in unmatched, never lost.
    assert "(未指定断面)" in plan["unmatched"]
    assert plan["totals"]["rows"] == 1
    # Not matched to any folder.
    assert "(未指定断面)" not in plan["matched"]


def test_preview_gcj02_converted_to_wgs84(survey_root):
    """A GCJ02 input is stored shifted (WGS84) and stays in valid range."""
    raw_lon, raw_lat = 121.6600, 29.1300
    rows = [{"断面": "T01", "地区": "浙江", "站位": "B3",
             "经度": str(raw_lon), "纬度": str(raw_lat)}]
    plan = psis.preview_distribution(survey_root, rows, MAPPING, coord_system="GCJ02")
    rec = plan["matched"]["T01"]["rows"][0]
    # Shifted from the raw input (mainland-China offset is ~hundreds of metres).
    assert rec["lon"] != raw_lon
    assert rec["lat"] != raw_lat
    assert abs(rec["lon"] - raw_lon) < 0.05  # a sane GCJ02 offset, not garbage
    assert abs(rec["lat"] - raw_lat) < 0.05
    assert -180 <= rec["lon"] <= 180
    assert -90 <= rec["lat"] <= 90


# ── distribute ─────────────────────────────────────────────────────────────────

def test_distribute_writes_and_db_created(survey_root):
    plan = psis.preview_distribution(survey_root, _raw_rows(), MAPPING, coord_system="WGS84")
    result = psis.distribute(survey_root, plan)

    # 2 (T01) + 1 (T02) ok rows written.
    assert result["written"] == 3
    # T03's single ok row skipped.
    assert result["skipped_unmatched_rows"] == 1
    assert result["unmatched_transects"] == ["T03"]

    # db_created flags: T01 pre-existed (False), T02 was materialised (True).
    assert result["targets"]["T01"]["db_created"] is False
    assert result["targets"]["T02"]["db_created"] is True
    assert result["targets"]["T01"]["written"] == 2
    assert result["targets"]["T02"]["written"] == 1

    # Rows actually landed in each workspace's collection_records.
    t01_db = open_project_db(os.path.join(survey_root, "T01"))
    stations = {r["station"] for r in
                t01_db.execute("SELECT station FROM collection_records").fetchall()}
    assert stations == {"B2", "B3"}

    t02_dir = os.path.join(survey_root, "T02")
    assert os.path.exists(os.path.join(t02_dir, "_data", "project.db"))
    t02_db = open_project_db(t02_dir)
    t02_stations = {r["station"] for r in
                    t02_db.execute("SELECT station FROM collection_records").fetchall()}
    assert t02_stations == {"C1"}

    # T03 never written anywhere — no folder exists for it.
    assert not os.path.exists(os.path.join(survey_root, "T03"))


def test_distribute_strips_helper_keys(survey_root):
    """Helper keys (ok/error/_raw) must not leak into stored real columns."""
    plan = psis.preview_distribution(survey_root, _raw_rows(), MAPPING)
    psis.distribute(survey_root, plan)
    t01_db = open_project_db(os.path.join(survey_root, "T01"))
    cols = {r[1] for r in t01_db.execute("PRAGMA table_info(collection_records)").fetchall()}
    assert "ok" not in cols and "error" not in cols and "_raw" not in cols
    # And lon/lat were stored as REAL (not the helper '' / None mush).
    row = t01_db.execute(
        "SELECT lon, lat FROM collection_records WHERE station='B2'").fetchone()
    assert row["lon"] is not None and row["lat"] is not None


def test_distribute_takes_snapshot(survey_root, monkeypatch):
    """snapshot_project is reached before writing (best-effort, never aborts)."""
    seen: list[str] = []
    real_snapshot = backup_service.snapshot_project

    def _spy(project_dir, *a, **kw):
        seen.append(project_dir)
        return real_snapshot(project_dir, *a, **kw)

    monkeypatch.setattr(psis.backup_service, "snapshot_project", _spy)

    plan = psis.preview_distribution(survey_root, _raw_rows(), MAPPING)
    result = psis.distribute(survey_root, plan)

    # Both matched targets had a snapshot attempt.
    assert os.path.join(survey_root, "T01") in seen
    assert os.path.join(survey_root, "T02") in seen
    # Distribution completed without raising even though snapshot ran.
    assert result["written"] == 3

    # T01 pre-existed with a db, so a real snapshot file should have been produced.
    snaps = backup_service.list_snapshots(os.path.join(survey_root, "T01"))
    assert len(snaps) >= 1


def test_distribute_snapshot_failure_never_aborts(survey_root, monkeypatch):
    """A raising snapshot must not abort the write (wrapped in try/except)."""
    def _boom(project_dir, *a, **kw):
        raise RuntimeError("snapshot blew up")

    monkeypatch.setattr(psis.backup_service, "snapshot_project", _boom)
    plan = psis.preview_distribution(survey_root, _raw_rows(), MAPPING)
    result = psis.distribute(survey_root, plan)
    assert result["written"] == 3  # writes still happened
