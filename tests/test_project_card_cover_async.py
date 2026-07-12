"""tests/test_project_card_cover_async.py — 项目卡片封面：主线程零磁盘 I/O + 可见性派发.

守住三条不变量（性能红线，不改任何视觉/交互）：
  1. 构造 grid + ``set_entries`` 时，**主线程**绝不调用 ``pick_project_cover_path``
     （它会开 SQLite + 递归扫目录）。路径选取整体在常驻工作线程里做。
  2. 只有**视口内可见**的卡片才会派发「路径选取 + 解码」任务。
  3. 同一目录的封面路径按目录 stat 签名 memo 缓存 —— 重复目录只解析一次；
     ``invalidate_cover_path_cache`` 能精确失效（``refresh_cover`` 依赖它）。
"""
from __future__ import annotations

import threading

import pytest

pytest.importorskip("PyQt6")

from app.widgets import project_card as pc


@pytest.fixture(autouse=True)
def _clear_cover_cache():
    pc.invalidate_cover_path_cache()
    yield
    pc.invalidate_cover_path_cache()


def _make_dirs(tmp_path, n: int) -> list[str]:
    dirs = []
    for i in range(n):
        d = tmp_path / f"proj{i:02d}"
        d.mkdir()
        dirs.append(str(d))
    return dirs


def _entries(dirs: list[str]) -> list[dict]:
    return [{"name": f"P{i}", "directory": d} for i, d in enumerate(dirs)]


def _spy_pick(monkeypatch):
    """Record (directory, thread_ident) for every pick_project_cover_path call."""
    calls: list[tuple[str, int]] = []
    lock = threading.Lock()

    def _fake(directory: str):
        with lock:
            calls.append((str(directory), threading.get_ident()))
        return None

    monkeypatch.setattr(pc, "pick_project_cover_path", _fake)
    return calls


def test_set_entries_does_no_cover_disk_io_on_main_thread(qtbot, tmp_path, monkeypatch):
    """set_entries 返回时，主线程一次 pick_project_cover_path 都没跑过。"""
    calls = _spy_pick(monkeypatch)
    dirs = _make_dirs(tmp_path, 4)

    grid = pc.ProjectCardGrid()
    qtbot.addWidget(grid)
    try:
        grid.set_entries(_entries(dirs))
        # 旧实现在 _queue_cover 里同步调用 → 这里已经有 4 条记录（红）。
        assert calls == [], f"主线程在 set_entries 内做了封面磁盘 I/O: {calls}"
    finally:
        grid.teardown()


def test_cover_resolution_runs_off_main_thread(qtbot, tmp_path, monkeypatch):
    """派发后路径选取发生在工作线程（thread ident != 主线程）。"""
    calls = _spy_pick(monkeypatch)
    dirs = _make_dirs(tmp_path, 2)
    main_ident = threading.get_ident()

    grid = pc.ProjectCardGrid()
    qtbot.addWidget(grid)
    try:
        grid.resize(900, 700)
        grid.show()
        qtbot.waitExposed(grid)
        grid.set_entries(_entries(dirs))
        qtbot.waitUntil(lambda: len(calls) >= 2, timeout=5000)
        assert {d for d, _ in calls} == set(dirs)
        assert all(ident != main_ident for _, ident in calls), calls
    finally:
        grid.teardown()


def test_only_visible_cards_are_dispatched(qtbot, tmp_path, monkeypatch):
    """视口外的卡片不派发解码；滚到底后才补上。"""
    calls = _spy_pick(monkeypatch)
    decoded: list[str] = []

    def _fake_decode(path, max_size=280, **kw):
        decoded.append(str(path))
        return None

    monkeypatch.setattr(pc, "decode_image_data", _fake_decode)

    dirs = _make_dirs(tmp_path, 30)  # 3 列 → 10 行，单行 ≈ 200px+

    grid = pc.ProjectCardGrid()
    qtbot.addWidget(grid)
    try:
        grid.resize(900, 260)  # 视口只装得下第一行左右
        grid.show()
        qtbot.waitExposed(grid)
        grid.set_entries(_entries(dirs))
        qtbot.waitUntil(lambda: len(calls) >= 1, timeout=5000)
        qtbot.wait(150)

        touched = {d for d, _ in calls}
        assert touched, "至少要给可见卡片派发"
        assert len(touched) < len(dirs), f"派发了全部 {len(dirs)} 张卡（未做可见性裁剪）"
        assert dirs[-1] not in touched, "最后一张卡在视口外，不应派发"
        assert dirs[0] in touched

        # 滚到底 → 之前不可见的卡片补派发
        bar = grid._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        qtbot.waitUntil(lambda: dirs[-1] in {d for d, _ in calls}, timeout=5000)
    finally:
        grid.teardown()


def test_cover_path_memoized_per_directory(tmp_path, monkeypatch):
    """同一目录（stat 签名不变）只解析一次；invalidate 后重新解析。"""
    calls: list[str] = []

    def _fake(directory: str):
        calls.append(str(directory))
        return None

    monkeypatch.setattr(pc, "pick_project_cover_path", _fake)
    d = str(tmp_path / "one")
    (tmp_path / "one").mkdir()

    assert pc.resolve_cover_path(d) is None
    assert pc.resolve_cover_path(d) is None
    assert calls == [d], f"重复目录应命中 memo 缓存，实际: {calls}"

    pc.invalidate_cover_path_cache(d)
    pc.resolve_cover_path(d)
    assert calls == [d, d]

    pc.invalidate_cover_path_cache()
    pc.resolve_cover_path(d)
    assert calls == [d, d, d]


def test_rebuild_does_not_rescan_same_directories(qtbot, tmp_path, monkeypatch):
    """重复 set_entries 同一批目录 → 路径选取仍然只跑一次。"""
    calls = _spy_pick(monkeypatch)
    dirs = _make_dirs(tmp_path, 3)

    grid = pc.ProjectCardGrid()
    qtbot.addWidget(grid)
    try:
        grid.resize(900, 700)
        grid.show()
        qtbot.waitExposed(grid)
        grid.set_entries(_entries(dirs))
        qtbot.waitUntil(lambda: len(calls) >= 3, timeout=5000)

        grid.set_entries(_entries(dirs))
        qtbot.wait(200)
        assert len(calls) == 3, f"重建后重复扫盘: {calls}"
    finally:
        grid.teardown()


def test_teardown_stops_worker_thread(qtbot, tmp_path, monkeypatch):
    """teardown 只 quit+wait 一次，线程真的停了。"""
    _spy_pick(monkeypatch)
    dirs = _make_dirs(tmp_path, 2)

    grid = pc.ProjectCardGrid()
    qtbot.addWidget(grid)
    grid.resize(900, 700)
    grid.show()
    qtbot.waitExposed(grid)
    grid.set_entries(_entries(dirs))
    qtbot.wait(120)

    grid.teardown()
    assert grid._cover_thread is None
    grid.teardown()  # 幂等


def test_refresh_cover_invalidates_and_redispatches(qtbot, tmp_path, monkeypatch):
    """refresh_cover 让该目录的路径缓存失效并重新派发。"""
    calls = _spy_pick(monkeypatch)
    dirs = _make_dirs(tmp_path, 2)

    grid = pc.ProjectCardGrid()
    qtbot.addWidget(grid)
    try:
        grid.resize(900, 700)
        grid.show()
        qtbot.waitExposed(grid)
        grid.set_entries(_entries(dirs))
        qtbot.waitUntil(lambda: len(calls) >= 2, timeout=5000)

        grid.refresh_cover(dirs[0])
        qtbot.waitUntil(
            lambda: sum(1 for d, _ in calls if d == dirs[0]) >= 2,
            timeout=5000,
        )
    finally:
        grid.teardown()


def test_teardown_reenter_cycles_do_not_stack_destroyed_connections(tmp_path, qtbot, monkeypatch):
    """反复「进页面 → teardown」不得堆积线程/destroyed 连接。

    旧实现在 _ensure_cover_worker 里每起一条线程就 self.destroyed.connect(_cleanup),
    于是 N 次进出 = N 个残留连接, 每个都捏着一条已死线程的引用; holder 里也会攒下
    一堆已 quit 的线程, 窗口销毁时还要挨个 wait。
    """
    _spy_pick(monkeypatch)
    dirs = _make_dirs(tmp_path, 2)

    grid = pc.ProjectCardGrid()
    qtbot.addWidget(grid)
    grid.resize(900, 700)
    grid.show()
    qtbot.waitExposed(grid)

    try:
        for _ in range(3):
            grid.set_entries(_entries(dirs))
            qtbot.waitUntil(lambda: grid._cover_thread is not None, timeout=5000)
            grid.teardown()
            # teardown 后线程已回收, holder 不留残骸
            assert grid._cover_thread is None
            assert grid._cover_thread_holder == []
    finally:
        grid.teardown()
