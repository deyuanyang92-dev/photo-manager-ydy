"""test_collab_offline_queue.py — Tests for StatusRetryQueue (offline status-push retry).

Historic class name ``OfflineDraftQueue`` is kept as a back-compat alias.

Coverage:
  test_mark_and_count
  test_mark_updates_existing_uid
  test_retry_success_removes_draft
  test_retry_failure_keeps_draft
  test_clear_empties_queue
  test_offline_draft_queue_alias_back_compat

Run:
    QT_QPA_PLATFORM=offscreen pytest tests/test_collab_offline_queue.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QSettings

from app.services.collab_offline_queue import OfflineDraftQueue, StatusRetryQueue


@pytest.fixture()
def settings(tmp_path):
    qs = QSettings(str(tmp_path / "test.ini"), QSettings.Format.IniFormat)
    return qs


@pytest.fixture()
def queue(settings):
    return StatusRetryQueue(settings)


def test_offline_draft_queue_alias_back_compat(settings):
    """Historic name must remain importable and construct the same queue."""
    assert OfflineDraftQueue is StatusRetryQueue
    legacy = OfflineDraftQueue(settings)
    canonical = StatusRetryQueue(settings)
    assert type(legacy) is type(canonical) is StatusRetryQueue


class TestMarkAndCount:
    def test_mark_and_count(self, queue):
        assert queue.count() == 0
        queue.mark_draft("uid-1", "shooting")
        assert queue.count() == 1

    def test_mark_updates_existing_uid(self, queue):
        queue.mark_draft("uid-1", "shooting")
        queue.mark_draft("uid-1", "shot_done")
        assert queue.count() == 1
        drafts = queue._load_drafts_from_settings()
        assert drafts[0]["status"] == "shot_done"

    def test_mark_multiple_uids(self, queue):
        queue.mark_draft("uid-1", "shooting")
        queue.mark_draft("uid-2", "done")
        assert queue.count() == 2


class TestRetry:
    def test_retry_success_removes_draft(self, queue):
        queue.mark_draft("uid-1", "shooting")
        svc = MagicMock()
        svc.update_task_status.return_value = (True, "ok")
        sent, remaining = queue.retry_all(svc)
        assert sent == 1
        assert remaining == 0
        assert queue.count() == 0

    def test_retry_failure_keeps_draft(self, queue):
        queue.mark_draft("uid-1", "shooting")
        svc = MagicMock()
        svc.update_task_status.return_value = (False, "network error")
        sent, remaining = queue.retry_all(svc)
        assert sent == 0
        assert remaining == 1
        assert queue.count() == 1

    def test_retry_partial_success(self, queue):
        queue.mark_draft("uid-1", "shooting")
        queue.mark_draft("uid-2", "done")

        def _side_effect(uid, status, **kwargs):
            if uid == "uid-1":
                return (False, "fail")
            return (True, "ok")

        svc = MagicMock()
        svc.update_task_status.side_effect = _side_effect
        sent, remaining = queue.retry_all(svc)
        assert sent == 1
        assert remaining == 1
        assert queue.count() == 1
        assert queue._load_drafts_from_settings()[0]["uid"] == "uid-1"


class TestClear:
    def test_clear_empties_queue(self, queue):
        queue.mark_draft("uid-1", "shooting")
        queue.mark_draft("uid-2", "done")
        queue.clear()
        assert queue.count() == 0
