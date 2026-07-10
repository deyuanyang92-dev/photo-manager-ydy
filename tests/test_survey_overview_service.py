"""test_survey_overview_service.py — 调查概览聚合."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.services.survey_overview_service import aggregate_survey_overview


def _make_ws(p: Path, rows: list[tuple]) -> None:
    (p / "_data").mkdir(parents=True)
    conn = sqlite3.connect(str(p / "_data" / "project.db"))
    conn.execute(
        "CREATE TABLE specimens ("
        "uid TEXT, storage TEXT, photographer TEXT, site TEXT, station TEXT, "
        "province TEXT, scientific_name TEXT, scientific_name_cn TEXT, "
        "family TEXT, genus TEXT, order_name TEXT, collector TEXT, identifier TEXT)"
    )
    for r in rows:
        conn.execute(
            "INSERT INTO specimens VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            r,
        )
    conn.commit()
    conn.close()


def test_empty_workspaces() -> None:
    out = aggregate_survey_overview([])
    assert out["specimen_count"] == 0
    assert out["species_count"] == 0


def test_aggregates_counts_and_distributions(tmp_path, monkeypatch) -> None:
    a = tmp_path / "断面a"
    b = tmp_path / "断面b"
    _make_ws(
        a,
        [
            ("u1", "R95E", "张三", "三门湾", "B2", "浙江", "Aa sp", "甲", "A", "Aa", "O1", "采集甲", "鉴定甲"),
            ("u2", "T95E", "李四", "三门湾", "B3", "浙江", "Bb sp", "乙", "B", "Bb", "O2", "采集乙", "鉴定乙"),
        ],
    )
    _make_ws(
        b,
        [
            ("u3", "R95E", "张三", "厦门", "C1", "福建", "Aa sp", "甲", "A", "Aa", "O1", "采集甲", "鉴定甲"),
        ],
    )
    monkeypatch.setattr(
        "app.services.survey_overview_service._photo_count",
        lambda ws: 5 if "断面a" in ws else 2,
    )
    out = aggregate_survey_overview([str(a), str(b)], labels=["A区", "B区"])
    assert out["workspace_count"] == 2
    assert out["specimen_count"] == 3
    assert out["photo_count"] == 7
    assert out["rna_count"] == 2
    assert out["species_count"] == 2
    assert out["site_rows"][0]["name"] == "三门湾"
    assert out["site_rows"][0]["count"] == 2
    assert any(r["name"] == "张三" and r["count"] == 2 for r in out["photographer_rows"])
    assert any(r["name"] == "采集甲" and r["count"] == 2 for r in out["collector_rows"])
    assert len(out["identifier_rows"]) == 2


def test_map_points_from_specimen_rows() -> None:
    from app.services.survey_overview_service import map_points_from_specimen_rows

    rows = [
        {
            "province": "浙江",
            "site": "三门湾",
            "station": "B2",
            "lon": 121.0,
            "lat": 29.0,
        },
        {
            "province": "浙江",
            "site": "三门湾",
            "station": "B2",
            "lon": 121.2,
            "lat": 29.2,
        },
    ]
    pts = map_points_from_specimen_rows(rows, level="station")
    assert len(pts) == 1
    assert pts[0]["label"] == "B2"
    assert abs(pts[0]["lon"] - 121.1) < 0.01
    assert pts[0]["count"] == 2


def test_labels_length_mismatch_raises(tmp_path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    _make_ws(a, [])
    _make_ws(b, [])
    with pytest.raises(ValueError):
        aggregate_survey_overview([str(a), str(b)], labels=["only"])


def test_map_points_from_collection_records(tmp_path) -> None:
    from app.db.db_manager import ensure_schema
    from app.services.survey_overview_service import map_points_for_workspaces

    ws = tmp_path / "断面a"
    (ws / "_data").mkdir(parents=True)
    conn = sqlite3.connect(str(ws / "_data" / "project.db"))
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO collection_records "
        "(province, site, station, collection_date, lon, lat, station_label) "
        "VALUES (?,?,?,?,?,?,?)",
        ("浙江", "三门湾", "B2", "20260601", 121.5, 29.1, "北滩"),
    )
    conn.commit()
    conn.close()
    pts = map_points_for_workspaces([str(ws)], level="station")
    assert len(pts) == 1
    assert pts[0]["label"] == "北滩"
    assert abs(pts[0]["lon"] - 121.5) < 0.01


def test_overview_includes_map_points(tmp_path, monkeypatch) -> None:
    from app.db.db_manager import ensure_schema

    ws = tmp_path / "断面a"
    (ws / "_data").mkdir(parents=True)
    conn = sqlite3.connect(str(ws / "_data" / "project.db"))
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO collection_records "
        "(province, site, station, collection_date, lon, lat, station_label) "
        "VALUES (?,?,?,?,?,?,?)",
        ("浙江", "三门湾", "B2", "20260601", 121.6, 29.2, "北滩"),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(
        "app.services.survey_overview_service._photo_count", lambda _ws: 0
    )
    out = aggregate_survey_overview([str(ws)])
    assert len(out["map_points"]) == 1
    assert out["map_points"][0]["label"] == "北滩"



def test_open_workspace_dbs_connect_failure_keeps_prior_conn_usable(
    tmp_path, monkeypatch
) -> None:
    """v0.56: 第 N 个工作区 connect 抛错时, except 分支不得误关上一轮
    已入列的连接(v0.55 的 conn 变量跨迭代残留 → 后续查询 ProgrammingError)."""
    from app.services.project import survey_overview_service as sos

    a = tmp_path / "a"
    b = tmp_path / "b"
    for ws in (a, b):
        (ws / "_data").mkdir(parents=True)
        c = sqlite3.connect(str(ws / "_data" / "project.db"))
        c.execute("CREATE TABLE collection_records (id INTEGER)")
        c.commit()
        c.close()

    real_connect = sqlite3.connect
    calls = {"n": 0}

    def flaky_connect(path, *a_, **k_):
        calls["n"] += 1
        if calls["n"] == 2:
            raise sqlite3.OperationalError("disk I/O error")
        return real_connect(path, *a_, **k_)

    monkeypatch.setattr(sos.sqlite3, "connect", flaky_connect)

    conns = sos._open_workspace_dbs([str(a), str(b)])
    try:
        assert len(conns) == 1
        # 上一轮连接必须仍然可用
        conns[0].execute("SELECT 1").fetchone()
    finally:
        for c in conns:
            c.close()
