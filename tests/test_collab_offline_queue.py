"""test_collab_offline_queue.py — Tests for OfflineDraftQueue.

Coverage:
  test_mark_and_count
  test_mark_updates_existing_uid
  test_retry_success_removes_draft
  test_retry_failure_keeps_draft
  test_clear_empties_queue

Run:
    QT_QPA_PLATFORM=offscreen pytest tests/test_collab_offline_queue.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QSettings

from app.services.collab_offline_queue import OfflineDraftQueue


@pytest.fixture()
def settings(tmp_path):
    qs = QSettings(str(tmp_path / "test.ini"), QSettings.Format.IniFormat)
    return qs


@pytest.fixture()
def queue(settings):
    return OfflineDraftQueue(settings)


class TestMarkAndCount:
    def test_mark_and_count(self, queue):
        assert queue.count() == 0
        queue.mark_draft("uid-1", "shooting")
        assert queue.count() == 1

    def test_mark_updates_existing_uid(self, queue):
        queue.mark_draft("uid-1", "shooting")
        queue.mark_draft("uid-1", "shot_done")
        assert queue.count() == 1
        drafts = queue._load()
        assert drafts[0]["status"] == "shot_done"

    def test_mark_multiple_uids(self, queue):
        queue.mark_draft("uid-1", "shooting")
        queue.mark_draft("uid-2", "done")
        assert queue.count() == 2


class TestRetry:
    def test_retry_success_removes_draft(self, queue):
        queue.mark_draft("uid-1", "shooting")
        svc = MagicMock()
        svc.update_task_status.return_value = None
        sent, remaining = queue.retry_all(svc)
        assert sent == 1
        assert remaining == 0
        assert queue.count() == 0

    def test_retry_failure_keeps_draft(self, queue):
        queue.mark_draft("uid-1", "shooting")
        svc = MagicMock()
        svc.update_task_status.side_effect = Exception("network error")
        sent, remaining = queue.retry_all(svc)
        assert sent == 0
        assert remaining == 1
        assert queue.count() == 1

    def test_retry_partial_success(self, queue):
        queue.mark_draft("uid-1", "shooting")
        queue.mark_draft("uid-2", "done")

        def _side_effect(uid, status):
            if uid == "uid-1":
                raise Exception("fail")

        svc = MagicMock()
        svc.update_task_status.side_effect = _side_effect
        sent, remaining = queue.retry_all(svc)
        assert sent == 1
        assert remaining == 1
        assert queue.count() == 1
        assert queue._load()[0]["uid"] == "uid-1"


class TestClear:
    def test_clear_empties_queue(self, queue):
        queue.mark_draft("uid-1", "shooting")
        queue.mark_draft("uid-2", "done")
        queue.clear()
        assert queue.count() == 0
