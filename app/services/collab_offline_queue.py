"""collab_offline_queue.py — Persistent queue for collab task updates sent while offline.

When a collabUpdateTaskStatus call fails (network error), the update is saved here.
A QTimer retries every 30 seconds.  On success the draft is removed.
"""
from __future__ import annotations
import json
import time
from typing import TYPE_CHECKING

from PyQt6.QtCore import QSettings

if TYPE_CHECKING:
    from app.services.collab_service import CollabService


class OfflineDraftQueue:
    SETTINGS_KEY = "collab/offline_drafts"

    def __init__(self, settings: QSettings) -> None:
        self._settings = settings

    def _load_drafts_from_settings(self) -> list:
        raw = self._settings.value(self.SETTINGS_KEY, "[]")
        try:
            return json.loads(raw) if isinstance(raw, str) else (raw or [])
        except Exception:
            return []

    def _save_drafts_to_settings(self, drafts: list) -> None:
        self._settings.setValue(self.SETTINGS_KEY, json.dumps(drafts))

    def mark_draft(self, uid: str, status: str, specimen: dict | None = None) -> None:
        drafts = self._load_drafts_from_settings()
        for d in drafts:
            if d["uid"] == uid:
                d.update({"status": status, "specimen": specimen, "ts": int(time.time())})
                self._save_drafts_to_settings(drafts)
                return
        drafts.append({"uid": uid, "status": status, "specimen": specimen, "ts": int(time.time())})
        self._save_drafts_to_settings(drafts)

    def retry_all(self, svc: "CollabService") -> tuple[int, int]:
        drafts = self._load_drafts_from_settings()
        remaining = []
        sent = 0
        for d in drafts:
            try:
                ok, _ = svc.update_task_status(
                    d["uid"], d["status"], force=True, broadcast=True,
                )
                if ok:
                    sent += 1
                else:
                    remaining.append(d)
            except Exception:
                remaining.append(d)
        self._save_drafts_to_settings(remaining)
        return sent, len(remaining)

    def count(self) -> int:
        return len(self._load_drafts_from_settings())

    def clear(self) -> None:
        self._save_drafts_to_settings([])
