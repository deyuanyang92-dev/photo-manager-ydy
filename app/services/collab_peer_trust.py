"""collab_peer_trust.py — Local trust / block lists for LAN collaboration peers.

Peers in the same team permanent code are not synced until explicitly trusted
(or auto-blocked when they broadcast no operator name).  Lists persist in
QSettings so blocked devices stay blocked across restarts.
"""
from __future__ import annotations

import json
from typing import Iterable

from PyQt6.QtCore import QSettings


_TRUSTED_KEY = "collab/trusted_peers"
_BLOCKED_KEY = "collab/blocked_peers"


def peer_key(ip: str, port: int) -> str:
    return f"{ip}:{port}"


class CollabPeerTrustStore:
    """Persist trusted / blocked peer keys (``ip:port`` strings)."""

    def __init__(self, qs: QSettings | None = None) -> None:
        self._qs = qs or QSettings()

    @staticmethod
    def _load_set(qs: QSettings, key: str) -> set[str]:
        raw = qs.value(key, "[]", type=str)
        if not raw:
            return set()
        try:
            data = json.loads(str(raw))
        except (json.JSONDecodeError, TypeError):
            return set()
        if not isinstance(data, list):
            return set()
        return {str(x).strip() for x in data if str(x).strip()}

    @staticmethod
    def _save_set(qs: QSettings, key: str, items: Iterable[str]) -> None:
        qs.setValue(key, json.dumps(sorted({str(x).strip() for x in items if str(x).strip()})))

    def trusted_keys(self) -> set[str]:
        return self._load_set(self._qs, _TRUSTED_KEY)

    def blocked_keys(self) -> set[str]:
        return self._load_set(self._qs, _BLOCKED_KEY)

    def is_trusted(self, key: str) -> bool:
        return key in self.trusted_keys()

    def is_blocked(self, key: str) -> bool:
        return key in self.blocked_keys()

    def trust(self, key: str) -> None:
        blocked = self.blocked_keys()
        blocked.discard(key)
        trusted = self.trusted_keys()
        trusted.add(key)
        self._save_set(self._qs, _BLOCKED_KEY, blocked)
        self._save_set(self._qs, _TRUSTED_KEY, trusted)

    def block(self, key: str) -> None:
        trusted = self.trusted_keys()
        trusted.discard(key)
        blocked = self.blocked_keys()
        blocked.add(key)
        self._save_set(self._qs, _TRUSTED_KEY, trusted)
        self._save_set(self._qs, _BLOCKED_KEY, blocked)

    def unblock(self, key: str) -> None:
        blocked = self.blocked_keys()
        if key not in blocked:
            return
        blocked.discard(key)
        self._save_set(self._qs, _BLOCKED_KEY, blocked)
