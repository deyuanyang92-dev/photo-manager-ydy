"""activity_feed_widget.py — Scrollable collaboration activity feed.

Displays a chronological list of ActivityEntry items with colour-coded
severity.  Used inside CollabPanel.
"""

from __future__ import annotations

from datetime import datetime, timezone

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.models.activity_log import ActivityEntry

# ── Severity colours ──────────────────────────────────────────────────────────

_SEVERITY_FG = {
    "info": "#586069",
    "warn": "#d29922",
    "error": "#cf222e",
}

_SEVERITY_ICON = {
    "info": "●",
    "warn": "⚠",
    "error": "✕",
}


class ActivityFeedWidget(QWidget):
    """Bounded, debounced activity feed list."""

    # Exposed so CollabPanel can connect it.
    entry_count_changed = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: list[ActivityEntry] = []
        self._max_visible = 100

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._list = QListWidget()
        self._list.setObjectName("ActivityFeed")
        self._list.setWordWrap(True)
        self._list.setSpacing(4)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setEditTriggers(QListWidget.EditTrigger.NoEditTriggers)
        self._list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        lay.addWidget(self._list)

        # Debounce timer — avoid repaint storm on rapid signals.
        self._debounce = QTimer(self)
        self._debounce.setInterval(50)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._render)

    # ── Public API ─────────────────────────────────────────────────────────

    def set_entries(self, entries: list[ActivityEntry]) -> None:
        """Replace the full entry list (debounced)."""
        self._entries = entries[-self._max_visible :]
        self._debounce.start()

    def append_entry(self, entry: ActivityEntry) -> None:
        """Append a single entry and trigger a debounced render."""
        self._entries.append(entry)
        if len(self._entries) > self._max_visible:
            self._entries = self._entries[-self._max_visible :]
        self._debounce.start()

    def clear(self) -> None:
        self._entries.clear()
        self._list.clear()
        self.entry_count_changed.emit(0)

    # ── Internal ───────────────────────────────────────────────────────────

    def _render(self) -> None:
        """Rebuild the QListWidget from ``self._entries``."""
        self._list.setUpdatesEnabled(False)
        self._list.clear()

        for entry in self._entries:
            time_str = self._fmt_time(entry.timestamp)
            icon = _SEVERITY_ICON.get(entry.severity, "●")
            fg = _SEVERITY_FG.get(entry.severity, _SEVERITY_FG["info"])

            # Build a compact one-line summary
            text = f"{time_str}  {entry.detail}" if entry.detail else f"{time_str}  {entry.action}"

            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setToolTip(f"{entry.actor}: {entry.action} {entry.target_uid}")

            # Colour the foreground per severity
            item.setForeground(Qt.GlobalColor.darkGray)
            # Use QSS on the list — avoid per-item font colour fights by
            # embedding the colour hint in a simple HTML label inside the row.

            self._list.addItem(item)

        self._list.setUpdatesEnabled(True)

        # Auto-scroll to bottom
        if self._list.count() > 0:
            self._list.scrollToBottom()

        self.entry_count_changed.emit(len(self._entries))

    @staticmethod
    def _fmt_time(iso: str) -> str:
        """Format ISO timestamp to HH:MM in local time."""
        try:
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is not None:
                dt = dt.astimezone()
            return dt.strftime("%H:%M")
        except (ValueError, TypeError):
            return "??:??"
