"""tests/test_cover_pick_service.py"""
from __future__ import annotations

from pathlib import Path

from app.services.cover_pick_service import (
    clear_project_cover_path,
    pick_project_cover_path,
    set_project_cover_path,
)


def test_picks_latest_result_tif(tmp_path):
    ws = tmp_path / "ws"
    res = ws / "results"
    res.mkdir(parents=True)
    old = res / "old.tif"
    new = res / "new.tif"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    old.touch()
    import time
    time.sleep(0.02)
    new.touch()

    hit = pick_project_cover_path(str(ws))
    assert hit is not None
    assert hit.endswith("new.tif")


def test_picks_incoming_when_no_results(tmp_path):
    ws = tmp_path / "ws"
    inc = ws / "incoming-jpg"
    inc.mkdir(parents=True)
    (inc / "shot.jpg").write_bytes(b"x")
    assert pick_project_cover_path(str(ws)) is not None


def test_manual_cover_overrides_auto(tmp_path):
    ws = tmp_path / "ws"
    (ws / "_data").mkdir(parents=True)
    res = ws / "results"
    res.mkdir()
    auto = res / "auto.tif"
    auto.write_bytes(b"tif")
    manual = ws / "cover.jpg"
    manual.write_bytes(b"jpg")

    stored = set_project_cover_path(str(ws), str(manual))
    assert stored in {"cover.jpg", str(manual)}
    hit = pick_project_cover_path(str(ws))
    assert hit is not None
    assert Path(hit).resolve() == manual.resolve()

    clear_project_cover_path(str(ws))
    hit2 = pick_project_cover_path(str(ws))
    assert hit2 is not None
    assert hit2.endswith("auto.tif")
