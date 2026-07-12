"""test_data_filter_view.py — 数据筛选视图(spec 2026-07-08)."""
from __future__ import annotations

import json
import sqlite3

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QInputDialog

from app.services import edit_lock_service
from app.views.data_filter_view import DataFilterView


class _Ctx:
    def __init__(self) -> None:
        self.edit_unlocked = False
        self.edit_actor = ""
        self.settings = None
        self.current_project_dir = None
        self.current_project_root = None

    def get_db(self):
        return None


def _make_ws(p, rows) -> None:
    (p / "_data").mkdir(parents=True)
    conn = sqlite3.connect(str(p / "_data" / "project.db"))
    conn.execute(
        "CREATE TABLE specimens ("
        "uid TEXT, storage TEXT, photographer TEXT, site TEXT, scientific_name TEXT)"
    )
    for r in rows:
        conn.execute("INSERT INTO specimens VALUES (?,?,?,?,?)", r)
    conn.commit()
    conn.close()


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_view_instantiates(qtbot, qapp) -> None:
    ctx = _Ctx()
    v = DataFilterView(ctx)
    qtbot.addWidget(v)
    assert v.view_id == "data_filter"
    assert v.nav_title == "数据筛选"


def test_run_query_fills_table_and_stats(qtbot, qapp, tmp_path, monkeypatch) -> None:
    a = tmp_path / "断面a"
    _make_ws(a, [("u1", "R95E", "张三", "浙江", "Aa"),
                 ("u2", "T95E", "李四", "福建", "Bb")])
    ctx = _Ctx()
    v = DataFilterView(ctx)
    qtbot.addWidget(v)
    v._set_workspaces([(str(a), "断面a")])
    monkeypatch.setattr(v, "_collect_conditions", lambda: [])  # 无条件 = 全通过
    v._run_query()
    # 查询已移入 _FilterQueryWorker(主线程不假死), 结果异步回填
    qtbot.waitUntil(lambda: v._table.rowCount() == 2, timeout=5000)
    v._stop_filter_query_worker(wait_ms=2000)
    assert v._table.rowCount() == 2
    # 已取RNA 列(索引 6): u1 R95E=是, u2 T95E=否
    rna_vals = {v._table.item(r, 6).text() for r in range(v._table.rowCount())}
    assert "是" in rna_vals and "否" in rna_vals
    assert "共 2" in v._stats_lbl.text()
    assert "已取RNA 1" in v._stats_lbl.text()


def test_preselect_workspaces_checks_matching_dirs(qtbot, qapp, tmp_path) -> None:
    a = tmp_path / "断面a"
    b = tmp_path / "断面b"
    _make_ws(a, [("u1", "R95E", "张三", "浙江", "Aa")])
    _make_ws(b, [("u2", "T95E", "李四", "福建", "Bb")])
    ctx = _Ctx()
    v = DataFilterView(ctx)
    qtbot.addWidget(v)
    v._set_workspaces([(str(a), "断面a"), (str(b), "断面b")])
    v.preselect_workspaces([str(a)])
    checked = {v._src_list.item(i).text() for i in range(v._src_list.count())
               if v._src_list.item(i).checkState() == Qt.CheckState.Checked}
    assert checked == {"断面a"}


def test_preselect_adds_missing_workspace(qtbot, qapp, tmp_path) -> None:
    ws = tmp_path / "树里选的"
    _make_ws(ws, [("u1", "R95E", "张三", "浙江", "Aa")])
    ctx = _Ctx()
    v = DataFilterView(ctx)
    qtbot.addWidget(v)
    v._refresh_workspaces()
    v.preselect_workspaces([str(ws)])
    labels = {v._src_list.item(i).text() for i in range(v._src_list.count())}
    assert "树里选的" in labels


def test_unlock_wrong_password_keeps_locked(qtbot, qapp, monkeypatch) -> None:
    from app.utils import ui as _ui
    monkeypatch.setattr(_ui, "warn", lambda *a, **k: None)  # 吞掉模态弹窗
    ctx = _Ctx()
    v = DataFilterView(ctx)
    qtbot.addWidget(v)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("wrong", True))
    v._on_unlock()
    assert not edit_lock_service.is_unlocked(ctx), "错密不应解锁"


def test_unlock_correct_password_sets_actor(qtbot, qapp, tmp_path, monkeypatch) -> None:
    ctx = _Ctx()
    v = DataFilterView(ctx)
    qtbot.addWidget(v)
    monkeypatch.setattr(edit_lock_service, "_default_config_path", lambda: str(tmp_path / "c.json"))
    answers = iter([("123", True), ("张三", True)])
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: next(answers))
    v._on_unlock()
    assert edit_lock_service.is_unlocked(ctx)
    assert edit_lock_service.current_actor(ctx) == "张三"


def test_table_readonly_until_unlocked(qtbot, qapp, tmp_path, monkeypatch) -> None:
    a = tmp_path / "断面a"
    _make_ws(a, [("u1", "R95E", "张三", "浙江", "Aa")])
    ctx = _Ctx()
    v = DataFilterView(ctx)
    qtbot.addWidget(v)
    v._set_workspaces([(str(a), "断面a")])
    monkeypatch.setattr(v, "_collect_conditions", lambda: [])
    v._run_query()
    qtbot.waitUntil(lambda: v._table.rowCount() == 1, timeout=5000)
    v._stop_filter_query_worker(wait_ms=2000)
    # 只读态: photographer 列(idx 4)不可编辑
    ro_item = v._table.item(0, 4)
    assert not (ro_item.flags() & Qt.ItemFlag.ItemIsEditable)
    # 解锁
    monkeypatch.setattr(edit_lock_service, "_default_config_path", lambda: str(tmp_path / "c.json"))
    answers = iter([("123", True), ("张三", True)])
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: next(answers))
    v._on_unlock()
    rw_item = v._table.item(0, 4)
    assert (rw_item.flags() & Qt.ItemFlag.ItemIsEditable), "解锁后应可编辑"


def test_persist_edit_writes_to_db(qtbot, qapp, tmp_path) -> None:
    a = tmp_path / "断面a"
    _make_ws(a, [("u1", "R95E", "张三", "浙江", "Aa")])
    ctx = _Ctx()
    v = DataFilterView(ctx)
    qtbot.addWidget(v)
    ok = v._persist_edit("u1", "photographer", "李四", str(a))
    assert ok is True
    conn = sqlite3.connect(str(a / "_data" / "project.db"))
    val = conn.execute("SELECT photographer FROM specimens WHERE uid='u1'").fetchone()[0]
    conn.close()
    assert val == "李四"


def test_persist_edit_keeps_raw_json_and_collab_timestamp_in_sync(
    qtbot, qapp, tmp_path
) -> None:
    a = tmp_path / "断面a"
    _make_ws(a, [("u1", "R95E", "张三", "浙江", "Aa")])
    conn = sqlite3.connect(str(a / "_data" / "project.db"))
    conn.execute("ALTER TABLE specimens ADD COLUMN raw_json TEXT")
    conn.execute("ALTER TABLE specimens ADD COLUMN collab_updated_at TEXT")
    conn.execute(
        "UPDATE specimens SET raw_json=? WHERE uid='u1'",
        (json.dumps({"photographer": "张三", "custom": "保留"}, ensure_ascii=False),),
    )
    conn.commit()
    conn.close()
    v = DataFilterView(_Ctx())
    qtbot.addWidget(v)

    assert v._persist_edit("u1", "photographer", "李四", str(a)) is True

    conn = sqlite3.connect(str(a / "_data" / "project.db"))
    row = conn.execute(
        "SELECT photographer, raw_json, collab_updated_at FROM specimens WHERE uid='u1'"
    ).fetchone()
    conn.close()
    raw = json.loads(row[1])
    assert row[0] == "李四"
    assert raw["photographer"] == "李四"
    assert raw["custom"] == "保留"
    assert raw["updatedAt"] == row[2]


def test_find_specimen_photo_matches_uid_prefix(qtbot, qapp, tmp_path) -> None:
    """选中编号 → results 下 uid 前缀匹配的首个 .tif。"""
    ws = tmp_path / "断面a"
    (ws / "results").mkdir(parents=True)
    uid = "浙江-三门湾-B2-1-R95E-260621"
    (ws / "results" / f"{uid}-001.tif").write_bytes(b"")
    (ws / "results" / f"{uid}-002.tif").write_bytes(b"")
    (ws / "results" / "其他-xxx-001.tif").write_bytes(b"")
    ctx = _Ctx()
    v = DataFilterView(ctx)
    qtbot.addWidget(v)
    path = v._find_specimen_photo(uid, str(ws))
    assert path is not None
    assert path.endswith(f"{uid}-001.tif"), "应取排序首个 uid 前缀 tif"
    # 无成果 → None
    assert v._find_specimen_photo("不存在-uid", str(ws)) is None
    # freeform 兜底
    ws2 = tmp_path / "断面b"
    (ws2 / "results" / "freeform").mkdir(parents=True)
    (ws2 / "results" / "freeform" / f"{uid}-005.tif").write_bytes(b"")
    path2 = v._find_specimen_photo(uid, str(ws2))
    assert path2 is not None and path2.endswith("-005.tif")


def test_export_csv_button_writes_file(qtbot, qapp, tmp_path, monkeypatch) -> None:
    a = tmp_path / "断面a"
    _make_ws(a, [("u1", "R95E", "张三", "浙江", "Aa")])
    ctx = _Ctx()
    v = DataFilterView(ctx)
    qtbot.addWidget(v)
    v._set_workspaces([(str(a), "断面a")])
    for i in range(v._src_list.count()):
        v._src_list.item(i).setCheckState(Qt.CheckState.Checked)
    monkeypatch.setattr(v, "_collect_conditions", lambda: [])
    v._run_query()
    qtbot.waitUntil(lambda: v._btn_export.isEnabled(), timeout=5000)
    v._stop_filter_query_worker(wait_ms=2000)
    out = tmp_path / "out.csv"
    monkeypatch.setattr(
        "app.utils.ui.get_save_file_name",
        lambda *_a, **_k: str(out),
    )
    monkeypatch.setattr("app.utils.ui.info", lambda *_a, **_k: None)
    v._export_csv()
    assert out.exists()
    text = out.read_text(encoding="utf-8-sig")
    assert "u1" in text


def test_export_selected_csv_only_writes_selected_rows(
    qtbot, qapp, tmp_path, monkeypatch
) -> None:
    a = tmp_path / "断面a"
    _make_ws(
        a,
        [
            ("u1", "R95E", "张三", "浙江", "Aa"),
            ("u2", "T95E", "李四", "福建", "Bb"),
        ],
    )
    v = DataFilterView(_Ctx())
    qtbot.addWidget(v)
    v._set_workspaces([(str(a), "断面a")])
    monkeypatch.setattr(v, "_collect_conditions", lambda: [])
    v._run_query()
    qtbot.waitUntil(lambda: v._table.rowCount() == 2, timeout=5000)
    v._stop_filter_query_worker(wait_ms=2000)

    from PyQt6.QtCore import QItemSelectionModel
    v._table.selectionModel().select(
        v._table.model().index(1, 0),
        QItemSelectionModel.SelectionFlag.ClearAndSelect
        | QItemSelectionModel.SelectionFlag.Rows,
    )
    assert [row["uid"] for row in v._selected_rows()] == ["u2"]
    assert v._btn_export_selected.isEnabled()
    assert "1" in v._selection_lbl.text()

    out = tmp_path / "selected.csv"
    monkeypatch.setattr(
        "app.utils.ui.get_save_file_name",
        lambda *_a, **_k: str(out),
    )
    monkeypatch.setattr("app.utils.ui.info", lambda *_a, **_k: None)
    v._export_csv(selected_only=True)

    text = out.read_text(encoding="utf-8-sig")
    assert "u2" in text
    assert "u1" not in text


def test_query_runs_in_worker_thread(qtbot, qapp, tmp_path, monkeypatch) -> None:
    """查询必须在 worker 线程执行, 不得阻塞 Qt 主线程(点击零反馈=bug)."""
    import threading

    from app.views import data_filter_view as dfv

    a = tmp_path / "断面a"
    _make_ws(a, [("u1", "R95E", "张三", "浙江", "Aa")])
    ctx = _Ctx()
    v = DataFilterView(ctx)
    qtbot.addWidget(v)
    v._set_workspaces([(str(a), "断面a")])
    monkeypatch.setattr(v, "_collect_conditions", lambda: [])

    seen: list = []
    real = dfv.filter_svc.query_specimens

    def spy(*args, **kwargs):
        seen.append(threading.current_thread())
        return real(*args, **kwargs)

    monkeypatch.setattr(dfv.filter_svc, "query_specimens", spy)
    v._run_query()
    assert not v._btn_run.isEnabled(), "查询中按钮应禁用并显示查询中"
    qtbot.waitUntil(lambda: v._table.rowCount() == 1, timeout=5000)
    v._stop_filter_query_worker(wait_ms=2000)
    assert seen and seen[0] is not threading.main_thread()
    assert v._btn_run.isEnabled(), "查询完成后按钮应恢复"


def test_query_failure_restores_button_and_warns(qtbot, qapp, tmp_path, monkeypatch) -> None:
    from app.views import data_filter_view as dfv

    a = tmp_path / "断面a"
    _make_ws(a, [("u1", "R95E", "张三", "浙江", "Aa")])
    ctx = _Ctx()
    v = DataFilterView(ctx)
    qtbot.addWidget(v)
    v._set_workspaces([(str(a), "断面a")])
    monkeypatch.setattr(v, "_collect_conditions", lambda: [])

    def boom(*_a, **_k):
        raise RuntimeError("scan failed")

    monkeypatch.setattr(dfv.filter_svc, "query_specimens", boom)
    warned: list = []
    monkeypatch.setattr(dfv.ui, "warn", lambda *a, **k: warned.append(a))

    v._run_query()
    qtbot.waitUntil(lambda: v._btn_run.isEnabled(), timeout=5000)
    v._stop_filter_query_worker(wait_ms=2000)
    assert warned, "失败必须给用户可见提示, 不得静默"
