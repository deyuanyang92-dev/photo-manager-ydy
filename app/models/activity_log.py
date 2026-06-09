"""
Bounded ring-buffer for collaboration activity events.

No Qt dependency — pure data layer used by CollabService.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class ActivityEntry:
    """A single collaboration activity event."""

    timestamp: str = ""
    actor: str = ""
    action: str = ""  # claimed | released | status_changed | conflict | photo_index | joined | left
    target_uid: str = ""
    detail: str = ""
    severity: str = "info"  # info | warn | error

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "actor": self.actor,
            "action": self.action,
            "targetUid": self.target_uid,
            "detail": self.detail,
            "severity": self.severity,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ActivityEntry":
        return cls(
            timestamp=d.get("timestamp", ""),
            actor=d.get("actor", ""),
            action=d.get("action", ""),
            target_uid=d.get("targetUid", ""),
            detail=d.get("detail", ""),
            severity=d.get("severity", "info"),
        )


class ActivityLog:
    """Thread-safe bounded ring buffer (max *maxlen* entries)."""

    def __init__(self, maxlen: int = 200) -> None:
        self._entries: deque[ActivityEntry] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    # -- mutators ----------------------------------------------------------

    def append(self, entry: ActivityEntry) -> None:
        with self._lock:
            self._entries.append(entry)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    # -- readers -----------------------------------------------------------

    def recent(self, n: int = 50) -> list[ActivityEntry]:
        with self._lock:
            items = list(self._entries)
        return items[-n:]

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def to_dicts(self, n: int = 50) -> list[dict]:
        return [e.to_dict() for e in self.recent(n)]
