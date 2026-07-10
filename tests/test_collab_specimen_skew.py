"""tests/test_collab_specimen_skew.py — 标本 L2 同步的时钟偏斜护栏.

背景(2026-07-11 用户报障根因):任务状态同步早有时钟护栏
(collab_store.merge_from_peer 的 trust_remote_clock),但**标本数据同步
write_specimens_to_local_db 之前没有**。后果:某台机器时钟快 1 小时时,
它的标本行 collab_updated_at 永远"更新",在 LWW 里永远赢 —— 别人真正更新
的修改推不动它,直到真实时间追上那 1 小时。这里把任务状态那套护栏语义
镜像到标本层:

  远端时钟不可信(测得偏斜 > 阈值) + 远端值与本地不同 → 拒绝覆盖、保留本地、
  记入 skew_guarded_out(供 UI 告警),不静默丢数据。
  默认 trust_remote_clock=True 时保持旧 LWW 行为(向后兼容)。
"""
from __future__ import annotations

import sqlite3

import pytest

from app.db import db_manager
from app.services.collab_specimen_sync import write_specimens_to_local_db


@pytest.fixture
def project(tmp_path):
    ws = tmp_path / "ws"
    (ws / "_data").mkdir(parents=True)
    conn = db_manager.open_project_db_private(str(ws), create=True)
    conn.close()
    return str(ws)


def _seed(project_dir: str, uid: str, *, collector: str, ts: str) -> None:
    conn = db_manager.open_project_db_private(project_dir)
    try:
        conn.execute(
            "INSERT INTO specimens (uid, collector, collab_updated_at) VALUES (?,?,?)",
            (uid, collector, ts),
        )
        conn.commit()
    finally:
        conn.close()


def _read(project_dir: str, uid: str) -> dict:
    conn = db_manager.open_project_db_private(project_dir)
    try:
        row = conn.execute(
            "SELECT collector, photographer, collab_updated_at FROM specimens WHERE uid=?",
            (uid,),
        ).fetchone()
        return {"collector": row[0], "photographer": row[1], "ts": row[2]}
    finally:
        conn.close()


class TestSpecimenSkewGuard:
    def test_untrusted_remote_clock_does_not_clobber_local(self, project):
        """远端时钟不可信 + 值不同 → 本地标本行不被覆盖,记入 guarded。"""
        _seed(project, "U1", collector="张三", ts="2026-07-09T10:00:00+00:00")
        # 快钟机:更晚的时间戳但内容是旧的(它其实没看到张三的修改)
        remote = [{
            "uid": "U1",
            "collector": "李四",
            "collab_updated_at": "2026-07-09T11:00:00+00:00",
        }]
        guarded: list[dict] = []
        written = write_specimens_to_local_db(
            project, remote, trust_remote_clock=False, skew_guarded_out=guarded
        )
        assert written == 0, "不可信时钟不得覆盖本地"
        assert _read(project, "U1")["collector"] == "张三"
        assert any(g["uid"] == "U1" for g in guarded)

    def test_trusted_remote_clock_keeps_legacy_lww(self, project):
        """默认 trust=True → 保持旧 LWW:更晚时间戳覆盖本地。"""
        _seed(project, "U2", collector="张三", ts="2026-07-09T10:00:00+00:00")
        remote = [{
            "uid": "U2",
            "collector": "李四",
            "collab_updated_at": "2026-07-09T11:00:00+00:00",
        }]
        written = write_specimens_to_local_db(project, remote)  # 默认 trust
        assert written == 1
        assert _read(project, "U2")["collector"] == "李四"

    def test_untrusted_but_identical_value_is_noop_not_guarded(self, project):
        """远端时钟不可信但值与本地相同 → 无需覆盖也无需告警(不是冲突)。"""
        _seed(project, "U3", collector="张三", ts="2026-07-09T10:00:00+00:00")
        remote = [{
            "uid": "U3",
            "collector": "张三",  # 同值
            "collab_updated_at": "2026-07-09T11:00:00+00:00",
        }]
        guarded: list[dict] = []
        written = write_specimens_to_local_db(
            project, remote, trust_remote_clock=False, skew_guarded_out=guarded
        )
        assert written == 0
        assert not guarded, "值相同不算被守护的冲突"

    def test_untrusted_clock_still_inserts_missing_row(self, project):
        """本地根本没有这条 → 不涉及"覆盖",照常 INSERT(护栏只防覆盖不同值)。"""
        remote = [{
            "uid": "U4",
            "collector": "王五",
            "collab_updated_at": "2026-07-09T11:00:00+00:00",
        }]
        guarded: list[dict] = []
        written = write_specimens_to_local_db(
            project, remote, trust_remote_clock=False, skew_guarded_out=guarded
        )
        assert written == 1
        assert _read(project, "U4")["collector"] == "王五"
        assert not guarded

    def test_trusted_clock_older_remote_still_skipped(self, project):
        """回归:trust=True 下,更旧的远端时间戳仍不覆盖(原 LWW 不变)。"""
        _seed(project, "U5", collector="张三", ts="2026-07-09T12:00:00+00:00")
        remote = [{
            "uid": "U5",
            "collector": "李四",
            "collab_updated_at": "2026-07-09T10:00:00+00:00",  # 更早
        }]
        written = write_specimens_to_local_db(project, remote)
        assert written == 0
        assert _read(project, "U5")["collector"] == "张三"


class TestSpecimenSkewEndToEnd:
    """端到端: 大偏斜的 peer 在标本拉取同步中不得覆盖本地标本(镜像
    TestLwwClockSkewGuard.test_skewed_peer_does_not_overwrite_local_via_sync)."""

    def test_skewed_peer_does_not_overwrite_local_specimen(self, project, monkeypatch):
        from unittest.mock import MagicMock, patch

        from app.services.collab_service import CollabService, PeerInfo

        _seed(project, "U9", collector="张三", ts="2026-07-09T10:00:00+00:00")

        svc = CollabService()
        svc.set_group_code("G1")
        svc._project_dir = project
        svc._project_id = "P1"

        peer = PeerInfo(ip="1.2.3.4", port=5050, group_code="G1")
        peer.project_id = "P1"
        peer.clock_skew_ms = 60_000.0        # 60s 偏斜 → 不可信

        # 绕过 trust 审批(全新机默认放行, 但显式确保)
        monkeypatch.setattr(svc, "_data_sync_allowed", lambda p: True)
        monkeypatch.setattr(svc, "_ensure_peer_clock_skew", lambda p: None)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{
            "uid": "U9", "collector": "李四",
            "collab_updated_at": "2026-07-09T11:00:00+00:00",   # 更晚(快钟)
        }]

        with patch("httpx.get", return_value=mock_resp):
            written = svc._sync_specimens_from_peer(peer)

        assert written == 0, "偏斜 peer 不得覆盖本地标本"
        assert _read(project, "U9")["collector"] == "张三"

    def test_trusted_peer_still_syncs_specimen(self, project, monkeypatch):
        from unittest.mock import MagicMock, patch

        from app.services.collab_service import CollabService, PeerInfo

        _seed(project, "U10", collector="张三", ts="2026-07-09T10:00:00+00:00")

        svc = CollabService()
        svc.set_group_code("G1")
        svc._project_dir = project
        svc._project_id = "P1"

        peer = PeerInfo(ip="1.2.3.4", port=5050, group_code="G1")
        peer.project_id = "P1"
        peer.clock_skew_ms = 100.0           # 0.1s → 可信

        monkeypatch.setattr(svc, "_data_sync_allowed", lambda p: True)
        monkeypatch.setattr(svc, "_ensure_peer_clock_skew", lambda p: None)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{
            "uid": "U10", "collector": "李四",
            "collab_updated_at": "2026-07-09T11:00:00+00:00",
        }]

        with patch("httpx.get", return_value=mock_resp):
            written = svc._sync_specimens_from_peer(peer)

        assert written == 1
        assert _read(project, "U10")["collector"] == "李四"
