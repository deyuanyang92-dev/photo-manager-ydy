"""specimen_sidebar.py — Left-column specimen list widget.

Shows all specimens for the current project (filtered by ownerProjectDir),
with a search box to filter by UID or scientific name.

Data is loaded from the DB specimens table via AppContext.get_db().
Emits ``specimen_selected(uid: str)`` when the user clicks a row.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from app.app_context import AppContext


# ── Badge colours matching the 5 file-state palette ─────────────────────────
_ACTIVE_STYLE = (
    "background:#29b9ab; color:#08161b; border-radius:3px;"
    " font-size:11px; padding:1px 6px; font-weight:600;"
)
_INACTIVE_STYLE = (
    "background:transparent; color:#87a2a1; border-radius:3px;"
    " font-size:11px; padding:1px 6px;"
)


class SpecimenSidebar(QWidget):
    """Left-column specimen list with search and per-item activation badge.

    Signals
    -------
    specimen_selected(str)
        Emitted with the specimen UID when the user selects an entry.
    """

    specimen_selected = pyqtSignal(str)

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._all_items: list[dict] = []  # [{uid, display, active}]
        self._setup_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        # Header row
        header = QHBoxLayout()
        header.setContentsMargins(8, 8, 8, 4)
        lbl = QLabel("标本列表")
        lbl.setObjectName("Section")
        header.addWidget(lbl)
        header.addStretch()
        self._count_label = QLabel("0")
        self._count_label.setObjectName("Muted")
        header.addWidget(self._count_label)
        root.addLayout(header)

        # Search box
        search_row = QHBoxLayout()
        search_row.setContentsMargins(8, 0, 8, 4)
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索编号或物种名…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search)
        search_row.addWidget(self._search)
        root.addLayout(search_row)

        # List
        self._list = QListWidget()
        self._list.setObjectName("SpecimenList")
        self._list.setAlternatingRowColors(True)
        self._list.setSpacing(1)
        self._list.itemClicked.connect(self._on_item_clicked)
        root.addWidget(self._list)

        # Refresh button
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(8, 4, 8, 8)
        self._refresh_btn = QPushButton("刷新")
        self._refresh_btn.setFixedHeight(28)
        self._refresh_btn.clicked.connect(self.refresh)
        btn_row.addWidget(self._refresh_btn)
        root.addLayout(btn_row)

    # ── Public API ────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Reload specimens from the DB for the current project."""
        self._all_items = self._load_specimens()
        self._apply_filter(self._search.text())

    def select_uid(self, uid: str) -> None:
        """Programmatically select the row matching *uid* (no signal emitted)."""
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == uid:
                self._list.setCurrentItem(item)
                return

    def current_uid(self) -> Optional[str]:
        """Return the UID of the currently selected row, or None."""
        item = self._list.currentItem()
        if item:
            return item.data(Qt.ItemDataRole.UserRole)
        return None

    # ── Internal ──────────────────────────────────────────────────────────────

    def _load_specimens(self) -> list[dict]:
        """Query DB for specimens in the current project."""
        db = self.ctx.get_db()
        if not db:
            return []
        project_dir = self.ctx.current_project_dir or ""

        rows: list[dict] = []
        try:
            cursor = db.execute(
                """
                SELECT uid,
                       COALESCE(scientific_name, '') AS name,
                       COALESCE(scientific_name_cn, '') AS name_cn
                FROM   specimens
                WHERE  owner_project_dir = ?
                ORDER  BY uid
                """,
                (project_dir,),
            )
            for row in cursor.fetchall():
                rows.append(
                    {
                        "uid": row[0],
                        "name": row[1],
                        "name_cn": row[2],
                    }
                )
        except Exception:
            pass

        # Merge active status from tasks table
        active_uids: set[str] = set()
        try:
            cur2 = db.execute(
                "SELECT uid FROM tasks WHERE is_active = 1 AND uid IN "
                + (
                    "(" + ",".join("?" * len(rows)) + ")"
                    if rows
                    else "(SELECT NULL WHERE 0)"
                ),
                [r["uid"] for r in rows] if rows else [],
            )
            active_uids = {r[0] for r in cur2.fetchall()}
        except Exception:
            pass

        for r in rows:
            r["active"] = r["uid"] in active_uids

        return rows

    def _apply_filter(self, text: str) -> None:
        """Rebuild list based on search text."""
        self._list.clear()
        query = text.strip().lower()
        shown = 0
        for entry in self._all_items:
            uid: str = entry["uid"]
            name: str = entry["name"]
            name_cn: str = entry["name_cn"]
            if query and query not in uid.lower() and query not in name.lower() and query not in name_cn.lower():
                continue

            # Build display text
            display_parts = [uid]
            if name:
                display_parts.append(name)
            elif name_cn:
                display_parts.append(name_cn)
            display_text = "\n".join(display_parts)

            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, uid)
            item.setToolTip(uid)
            self._list.addItem(item)
            shown += 1

        self._count_label.setText(str(shown))

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_search(self, text: str) -> None:
        self._apply_filter(text)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        uid = item.data(Qt.ItemDataRole.UserRole)
        if uid:
            self.specimen_selected.emit(uid)
