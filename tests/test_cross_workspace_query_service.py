"""tests/test_cross_workspace_query_service.py — 跨工作区汇总查询."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.services import cross_workspace_query_service as cwq


def _make_ws(p: Path, rows: list[tuple], tifs: list[str] | None = None) -> Path:
    (p / "_data").mkdir(parents=True)
    (p / "results").mkdir(parents=True)
    db = p / "_data" / "project.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE specimens ("
        "uid TEXT, storage TEXT, photographer TEXT, province TEXT, "
        "site TEXT, station TEXT, collection_date TEXT, photo_date TEXT, "
        "scientific_name TEXT, collector TEXT, identifier TEXT, "
        "lon REAL, lat REAL, geo_area TEXT, notes TEXT, custom_tag TEXT)"
    )
    for r in rows:
        conn.execute("INSERT INTO specimens VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", r)
    conn.commit()
    conn.close()
    for name in tifs or []:
        (p / "results" / name).write_bytes(b"x")
    return p


def test_summary_all_columns_unions_workspaces(tmp_path) -> None:
    a = _make_ws(tmp_path / "a", [])
    b = _make_ws(tmp_path / "b", [])
    cols = cwq.summary_all_columns([str(a), str(b)])
    keys = [k for k, _ in cols]
    assert "uid" in keys
    assert "_workspace_label" in keys
    assert "custom_tag" in keys
    assert "storage_is_rna" in keys
    assert keys.index("uid") < keys.index("_workspace_label")


def test_resolve_summary_visible_columns_respects_saved_order() -> None:
    all_cols = [
        ("uid", "编号"),
        ("_workspace_label", "工作区"),
        ("lon", "经度"),
        ("lat", "纬度"),
    ]
    visible = cwq.resolve_summary_visible_columns(all_cols, ["lat", "uid"])
    assert [k for k, _ in visible] == ["lat", "uid"]


def test_summary_cell_value_derived_rna() -> None:
    assert cwq.summary_cell_value({"storage": "R95E"}, "storage_is_rna") == "是"
    assert cwq.summary_cell_value({"storage": "T95E"}, "storage_is_rna") == "否"


def test_query_summary_scope_rna_filter(tmp_path) -> None:
    a = _make_ws(
        tmp_path / "a",
        [
            (
                "GD-SM-A1-001-R-20240601", "R95E", "张三", "广东", "三门", "A1",
                "2024-06-01", "2024-06-02", "Species a", "王五", "赵六",
                121.5, 29.1, "南海", "备注1", "tagA",
            ),
            (
                "GD-SM-A1-002-T-20240601", "T95E", "李四", "广东", "三门", "A1",
                "2024-06-01", "2024-06-02", "Species b", "", "",
                None, None, "", "", "tagB",
            ),
        ],
        tifs=["GD-SM-A1-001-R-1-240601.tif", "GD-SM-A1-002-T-1-240601.tif"],
    )
    res = cwq.query_summary_scope(
        [str(a)],
        [{"field": "storage_is_rna", "op": "eq", "value": "是"}],
    )
    assert len(res.specimens) == 1
    assert res.specimens[0]["uid"].startswith("GD-SM-A1-001")
    assert res.stats["specimen_count"] == 1
    assert res.stats["rna_count"] == 1
    assert len(res.groups) == 1
    assert len(res.groups[0]["items"]) >= 1
    assert res.stats["collector_rows"][0]["name"] == "王五"
    assert res.stats["identifier_rows"][0]["name"] == "赵六"
    assert len(res.stats["map_points"]) == 1
    assert abs(res.stats["map_points"][0]["lon"] - 121.5) < 0.01


def test_query_summary_scope_date_between(tmp_path) -> None:
    a = _make_ws(
        tmp_path / "a",
        [
            (
                "u-old", "R95E", "张三", "浙江", "x", "s1",
                "2024-01-15", "", "Aa", "", "", None, None, "", "", "",
            ),
            (
                "u-new", "R95E", "张三", "浙江", "x", "s1",
                "2024-06-15", "", "Bb", "", "", None, None, "", "", "",
            ),
        ],
    )
    res = cwq.query_summary_scope(
        [str(a)],
        [{"field": "collection_date", "op": "between", "value": "2024-06-01|2024-06-30"}],
    )
    assert {r["uid"] for r in res.specimens} == {"u-new"}


def test_export_filtered_csv_all_columns(tmp_path) -> None:
    ws = str(_make_ws(tmp_path / "a", []))
    rows = [{
        "uid": "u1",
        "_workspace": ws,
        "_workspace_label": "断面a",
        "scientific_name": "Aa",
        "lon": 121.1,
        "lat": 29.2,
        "collector": "采集甲",
        "identifier": "鉴定乙",
        "geo_area": "东海",
        "custom_tag": "扩展列",
        "storage": "R95E",
    }]
    out = cwq.export_filtered_specimens_csv(rows, tmp_path / "out.csv")
    text = out.read_text(encoding="utf-8-sig")
    assert "编号" in text
    assert "经度" in text
    assert "采集人" in text
    assert "custom_tag" in text
    assert "已取RNA" in text
    assert "u1" in text
    assert "121.1" in text
    assert "采集甲" in text
    assert "是" in text


def test_summary_all_columns_includes_photo_path(tmp_path) -> None:
    ws = _make_ws(tmp_path / "a", [])
    cols = cwq.summary_all_columns([str(ws)])
    keys = [k for k, _ in cols]
    assert "photo_absolute_path" in keys
    assert "storage_is_rna" in keys
    assert keys.index("photo_absolute_path") > keys.index("_workspace_label")


def test_find_specimen_result_tif_and_enrich(tmp_path) -> None:
    ws = tmp_path / "ws"
    (ws / "results").mkdir(parents=True)
    tif = ws / "results" / "GXFCG-BLW-SC001-1-D79-20260618.tif"
    tif.write_bytes(b"fake-tif")
    assert cwq.find_specimen_result_tif(str(ws), "GXFCG-BLW-SC001") == str(tif.resolve())
    rows = cwq.enrich_specimens_with_photo_info([
        {"uid": "GXFCG-BLW-SC001", "_workspace": str(ws)},
    ])
    assert rows[0]["photo_absolute_path"] == str(tif.resolve())


def test_find_specimen_result_tif_matches_sequence_in_middle(tmp_path) -> None:
    ws = tmp_path / "ws"
    (ws / "results").mkdir(parents=True)
    uid = "GXFCG-BLW-SC002-RD79-20260618"
    tif = ws / "results" / "GXFCG-BLW-SC002-10-RD79-20260618.tif"
    tif.write_bytes(b"fake-tif")
    assert cwq.find_specimen_result_tif(str(ws), uid) == str(tif.resolve())


def test_enrich_prefers_group_item_path(tmp_path) -> None:
    ws = tmp_path / "ws"
    uid = "GXFCG-BLW-SC003-R-20260618"
    group_tif = ws / "results" / "from-group.tif"
    group_tif.parent.mkdir(parents=True)
    group_tif.write_bytes(b"x")
    rows = cwq.enrich_specimens_with_photo_info(
        [{"uid": uid, "_workspace": str(ws)}],
        groups=[{
            "uid": uid,
            "_workspace": str(ws),
            "items": [{"path": str(group_tif), "seq": 1}],
        }],
    )
    assert rows[0]["photo_absolute_path"] == str(group_tif.resolve())


def test_enrich_records_absolute_path_when_exif_read_fails(tmp_path, monkeypatch) -> None:
    ws = tmp_path / "ws"
    (ws / "results").mkdir(parents=True)
    tif = ws / "results" / "GXFCG-BLW-SC004-R-20260618.tif"
    tif.write_bytes(b"fake-tif")

    def _boom(_path: str) -> dict:
        raise OSError("exif read failed")

    monkeypatch.setattr(
        "app.services.photo_asset_service.read_image_exif_metadata",
        _boom,
    )
    rows = cwq.enrich_specimens_with_photo_info([
        {"uid": "GXFCG-BLW-SC004-R-20260618", "_workspace": str(ws)},
    ])
    assert rows[0]["photo_absolute_path"] == str(tif.resolve())
    assert "camera_make" not in rows[0]


class TestQuerySummaryScopeCancel:
    """worker 线程取消钩子: cancel_callback=True → 阶段边界提前返回, 不抛异常."""

    def test_cancelled_returns_empty_result(self, tmp_path):
        from app.services import cross_workspace_query_service as cwq

        ws = tmp_path / "ws"
        (ws / "_data").mkdir(parents=True)
        import sqlite3 as _sq
        conn = _sq.connect(str(ws / "_data" / "project.db"))
        conn.execute("CREATE TABLE specimens (uid TEXT, storage TEXT)")
        conn.execute("INSERT INTO specimens VALUES ('U-1', 'R')")
        conn.commit()
        conn.close()

        result = cwq.query_summary_scope(
            [str(ws)], [], cancel_callback=lambda: True
        )
        assert result.workspaces == [str(ws)]
        assert result.specimens == [] or result.stats == {}, "取消后不产出完整结果"

    def test_no_callback_unchanged(self, tmp_path):
        from app.services import cross_workspace_query_service as cwq

        result = cwq.query_summary_scope([], [])
        assert result.workspaces == []
