"""tests/test_specimen_result_tif_service.py — 成果母版 TIF 路径索引."""
from __future__ import annotations

from pathlib import Path

from app.services import specimen_result_tif_service as srts


def _make_ws(tmp_path: Path, tifs: list[tuple[str, str]]) -> Path:
    from app.db.db_manager import open_project_db

    ws = tmp_path / "ws"
    res = ws / "results"
    res.mkdir(parents=True)
    open_project_db(str(ws), create=True)
    for name, _uid_prefix in tifs:
        (res / name).write_bytes(b"tif")
    return ws


def test_sync_persists_absolute_tif_paths(tmp_path) -> None:
    ws = _make_ws(tmp_path, [
        ("GXFCG-BLW-SC001-1-D79-20260618.tif", "GXFCG-BLW-SC001"),
        ("GXFCG-BLW-SC001-2-D79-20260618.tif", "GXFCG-BLW-SC001"),
    ])
    n = srts.sync_workspace_result_tifs(str(ws))
    assert n == 2

    from app.db.db_manager import open_project_db

    conn = open_project_db(str(ws))
    paths = srts.list_result_tif_paths(conn, "GXFCG-BLW-SC001-D79-20260618")
    assert len(paths) == 2
    assert all(p.endswith(".tif") for p in paths)
    assert all(Path(p).is_absolute() for p in paths)


def test_enrich_uses_db_index_over_scan(tmp_path, monkeypatch) -> None:
    from app.services import cross_workspace_query_service as cwq

    ws = _make_ws(tmp_path, [("GXFCG-BLW-SC002-1-R-20260618.tif", "GXFCG-BLW-SC002")])
    srts.sync_workspace_result_tifs(str(ws))

    rows = cwq.enrich_specimens_with_photo_info(
        [{"uid": "GXFCG-BLW-SC002-R-20260618", "_workspace": str(ws)}],
        groups=[],
    )
    assert rows[0]["photo_absolute_path"].endswith(".tif")
    assert "result_tif_paths" in rows[0]
    assert rows[0]["result_tif_paths"].endswith(".tif")
