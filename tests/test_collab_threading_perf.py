"""test_collab_threading_perf.py — main-thread-blocking regressions in CollabService.

Every test here is a guard against a *specific* UI freeze that was measured in
the app:

  * post_photo_index                → 合成/整理 完成后主线程卡 3 s × N peers
  * _maybe_retry_offline_drafts     → 协作页每 15 s 卡 4 s × drafts × peers
  * pull_all_specimens_from_session → 加入会话 / 切项目 冻结 8 s × N peers
  * create_task                     → 认领编号 时串行 4 s × N peers（且早退漏回滚）
  * stop()                          → 退出时串行 wait ≈ 11 s

Pure logic + fakes; no real network, no real QThread.

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_collab_threading_perf.py -v
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from app.services.collab_service import CollabService, PeerInfo


def _svc_with_peer(*ips: str) -> CollabService:
    svc = CollabService()
    svc.set_group_code("G1")
    svc._project_id = "P1"
    for ip in ips:
        svc._peers[f"{ip}:5050"] = PeerInfo(
            ip=ip, port=5050, group_code="G1", project_id="P1",
        )
    return svc


# ── post_photo_index must not do HTTP on the calling (main) thread ───────────

class TestPhotoIndexOffThread:
    def test_post_photo_index_delegates_to_spawn(self):
        svc = _svc_with_peer("10.0.0.2")
        spawned: list = []
        svc._spawn = lambda fn: spawned.append(fn)

        with patch("httpx.post", side_effect=AssertionError(
                "photo-index POST ran inline on the calling thread")):
            svc.post_photo_index("PI-1", "tiff", count=2)

        assert len(spawned) == 1, "post_photo_index must hand the POSTs to _spawn"

    def test_spawned_body_posts_to_peer(self):
        svc = _svc_with_peer("10.0.0.2")
        spawned: list = []
        svc._spawn = lambda fn: spawned.append(fn)
        svc.post_photo_index("PI-1", "zip")

        posted: list[str] = []

        def fake_post(url, **kw):
            posted.append(url)
            m = MagicMock()
            m.status_code = 200
            return m

        with patch("httpx.post", side_effect=fake_post):
            spawned[0]()

        assert posted and "photo-index" in posted[0]

    def test_no_peers_no_spawn(self):
        svc = CollabService()
        spawned: list = []
        svc._spawn = lambda fn: spawned.append(fn)
        svc.post_photo_index("PI-1", "tiff")
        assert spawned == []


# ── 15 s retry timer must schedule, not block ────────────────────────────────

class TestOfflineDraftRetryOffThread:
    def test_timer_slot_delegates_to_spawn(self):
        svc = CollabService()
        spawned: list = []
        svc._spawn = lambda fn: spawned.append(fn)

        with patch("httpx.post", side_effect=AssertionError(
                "draft retry ran HTTP inline on the main thread")):
            svc._maybe_retry_offline_drafts()

        assert len(spawned) == 1

    def test_second_firing_is_noop_while_cycle_in_flight(self):
        """A slow cycle (D × P × 4 s > 15 s) must not stack up daemon threads."""
        svc = CollabService()
        spawned: list = []
        svc._spawn = lambda fn: spawned.append(fn)   # capture: cycle stays "in flight"

        svc._maybe_retry_offline_drafts()
        svc._maybe_retry_offline_drafts()
        assert len(spawned) == 1, "re-entrant firing must be skipped, not queued"

        # Running the captured cycle releases the guard → next tick works again.
        spawned[0]()
        svc._maybe_retry_offline_drafts()
        assert len(spawned) == 2

    def test_retry_cycle_releases_lock_on_exception(self):
        svc = CollabService()
        svc.retry_offline_drafts = MagicMock(side_effect=RuntimeError("boom"))
        svc._retry_lock.acquire()       # as the timer slot does before spawning
        svc._run_retry_cycle()          # must swallow
        assert svc._retry_lock.acquire(blocking=False)
        svc._retry_lock.release()


# ── join / project-switch specimen pull must not block the main thread ───────

class TestSpecimenPullOffThread:
    def test_async_pull_delegates_to_spawn(self):
        svc = _svc_with_peer("10.0.0.2")
        spawned: list = []
        svc._spawn = lambda fn: spawned.append(fn)

        with patch("httpx.get", side_effect=AssertionError(
                "specimen pull ran HTTP inline on the main thread")):
            svc.pull_all_specimens_from_session_async()

        assert len(spawned) == 1

    def test_async_pull_is_reentrancy_guarded(self):
        svc = _svc_with_peer("10.0.0.2")
        spawned: list = []
        svc._spawn = lambda fn: spawned.append(fn)
        svc.pull_all_specimens_from_session_async()
        svc.pull_all_specimens_from_session_async()
        assert len(spawned) == 1, "two rapid project switches must not pull concurrently"

    def test_pull_guard_uses_its_own_lock_not_sync_lock(self):
        """A running 5 s sync cycle must NOT swallow a project-switch pull."""
        svc = _svc_with_peer("10.0.0.2")
        spawned: list = []
        svc._spawn = lambda fn: spawned.append(fn)
        assert svc._sync_lock.acquire(blocking=False)   # pretend a cycle is in flight
        try:
            svc.pull_all_specimens_from_session_async()
        finally:
            svc._sync_lock.release()
        assert len(spawned) == 1

    def test_pull_cycle_releases_lock_on_exception(self):
        svc = CollabService()
        svc.pull_all_specimens_from_session = MagicMock(side_effect=RuntimeError("boom"))
        svc._specimen_pull_lock.acquire()   # as the async entry point does
        svc._run_specimen_pull_cycle()
        assert svc._specimen_pull_lock.acquire(blocking=False)
        svc._specimen_pull_lock.release()


# ── create_task stays synchronous (409 red line) but probes peers in parallel ─

class TestCreateTaskParallelBroadcast:
    def test_all_peers_probed_even_when_first_returns_409(self):
        """Ghost-claim guard: a peer that answered 201 must still be released."""
        svc = CollabService()
        svc.set_group_code("G")
        svc._peers["10.0.0.2:5050"] = PeerInfo(ip="10.0.0.2", port=5050, group_code="G")
        svc._peers["10.0.0.3:5050"] = PeerInfo(ip="10.0.0.3", port=5050, group_code="G")

        seen_create: list[str] = []
        released: list[str] = []

        def fake_post(url, **kw):
            m = MagicMock()
            if "release" in url:
                released.append(url)
                m.status_code = 200
                return m
            seen_create.append(url)
            if "10.0.0.2" in url:          # first peer in insertion order → conflict
                m.status_code = 409
                m.json.return_value = {"detail": "UID already exists"}
                return m
            m.status_code = 201            # second peer accepted the claim
            m.json.return_value = {}
            return m

        with patch("httpx.post", side_effect=fake_post):
            ok, msg = svc.create_task("GHOST-1")

        assert not ok and "409" in msg
        assert not svc.store.exists("GHOST-1")
        assert len(seen_create) == 2, "both peers must be probed before judging"
        assert any("10.0.0.3" in u for u in released), \
            "the peer that returned 201 must be rolled back"

    def test_conflict_free_create_still_succeeds(self):
        svc = CollabService()
        svc.set_group_code("G")
        svc._peers["10.0.0.2:5050"] = PeerInfo(ip="10.0.0.2", port=5050, group_code="G")
        svc._peers["10.0.0.3:5050"] = PeerInfo(ip="10.0.0.3", port=5050, group_code="G")

        def fake_post(url, **kw):
            m = MagicMock()
            m.status_code = 201
            m.json.return_value = {}
            return m

        with patch("httpx.post", side_effect=fake_post):
            ok, _msg = svc.create_task("OK-1")

        assert ok
        assert svc.store.exists("OK-1")

    def test_peer_timeout_is_split_connect_read(self):
        t = CollabService._task_http_timeout()
        import httpx
        assert isinstance(t, httpx.Timeout)
        assert t.connect == 1.5
        assert t.read == 4.0


# ── stop() must be bounded, not 2+3+5+1 s serial ─────────────────────────────

class _HungThread:
    """Fake QThread that never finishes; records how long it was waited on."""

    def __init__(self) -> None:
        self.interrupted = False
        self.terminated = False
        self.stopped = False
        self.waits: list[int] = []

    def requestInterruption(self) -> None:
        self.interrupted = True

    def wait(self, ms: int = 0) -> bool:
        self.waits.append(int(ms))
        time.sleep(min(int(ms), 400) / 1000.0)   # simulate a hung thread
        return False

    def isRunning(self) -> bool:
        return not self.terminated

    def terminate(self) -> None:
        self.terminated = True

    def quit(self) -> None:
        pass

    def stop(self) -> None:
        self.stopped = True


class TestBoundedStop:
    def test_stop_signals_first_then_waits_within_one_budget(self):
        svc = CollabService()
        svc._STOP_BUDGET_MS = 200          # instance override — keep the test fast
        svc._STOP_TERMINATE_WAIT_MS = 50

        scanner, discovery, server = _HungThread(), _HungThread(), _HungThread()
        svc._subnet_scanner = scanner        # type: ignore[assignment]
        svc._discovery_thread = discovery    # type: ignore[assignment]
        svc._server_thread = server          # type: ignore[assignment]

        t0 = time.monotonic()
        svc.stop()
        elapsed = time.monotonic() - t0

        # Old serial path asked for 2000 + 3000 + 5000 (+1000) ms of waiting.
        total_requested = sum(scanner.waits) + sum(discovery.waits) + sum(server.waits)
        assert total_requested <= 200 + 2 * 50 + 5, \
            f"waits must share one deadline, got {total_requested} ms"
        assert elapsed < 1.5, f"stop() blocked the main thread for {elapsed:.2f}s"

        # Signalled before waiting…
        assert scanner.interrupted and discovery.interrupted
        assert server.interrupted, "server thread must be asked to exit before waiting"
        assert not server.stopped, \
            "stop() must not call the thread's own blocking quit/wait/terminate tail"
        # …and force-stopped when the budget ran out, never GC'd while running.
        assert discovery.terminated and server.terminated
        assert scanner in svc._retired_threads
        assert svc._server_thread is None and svc._discovery_thread is None

    def test_stop_is_idempotent_without_threads(self):
        svc = CollabService()
        svc.stop()
        svc.stop()
        assert not svc.is_running()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
