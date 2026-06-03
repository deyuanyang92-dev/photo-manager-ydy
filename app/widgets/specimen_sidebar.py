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

from app.config import icons

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
    activate_requested(str)
        Emitted when the user clicks the "激活" button for a specimen.
    deactivate_requested(str)
        Emitted when the user clicks the "去激活" button for the active specimen.
    """

    specimen_selected = pyqtSignal(str)
    activate_requested = pyqtSignal(str)
    deactivate_requested = pyqtSignal(str)
    new_specimen_requested = pyqtSignal()

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._all_items: list[dict] = []  # [{uid, display, active}]
        self._setup_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("PanelCard")
        outer.addWidget(card)
        from app.config.effects import apply_card_shadow
        apply_card_shadow(card)

        root = QVBoxLayout(card)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # New-specimen entry — vector + glyph
        self._new_btn = QPushButton("新增标本唯一编号")
        self._new_btn.setObjectName("Outline")
        self._new_btn.setFixedHeight(34)
        icons.set_button_icon(self._new_btn, "mdi6.plus", color=icons.TONE_ACCENT, size=15)
        self._new_btn.setToolTip("开始一个新的标本唯一编号（右侧填写）")
        self._new_btn.clicked.connect(self.new_specimen_requested.emit)
        root.addWidget(self._new_btn)

        # Search box with a leading magnifier action.
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索标本唯一编号")
        self._search.setClearButtonEnabled(True)
        self._search.setFixedHeight(32)
        if icons.available():
            self._search.addAction(
                icons.icon("mdi6.magnify", color=icons.TONE_MUTED),
                QLineEdit.ActionPosition.LeadingPosition,
            )
        self._search.textChanged.connect(self._on_search)
        root.addWidget(self._search)

        # Section label + count
        header = QHBoxLayout()
        header.setContentsMargins(2, 0, 2, 0)
        lbl = QLabel("已有标本唯一编号")
        lbl.setObjectName("Section")
        header.addWidget(lbl)
        header.addStretch()
        self._count_label = QLabel("0")
        self._count_label.setObjectName("MutedSmall")
        header.addWidget(self._count_label)
        root.addLayout(header)

        # List
        self._list = QListWidget()
        self._list.setObjectName("SpecimenList")
        self._list.setAlternatingRowColors(True)
        self._list.setSpacing(1)
        self._list.itemClicked.connect(self._on_item_clicked)
        root.addWidget(self._list)

        # Activate / Deactivate + Refresh buttons
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 4, 0, 0)
        btn_row.setSpacing(8)

        self._activate_btn = QPushButton("激活")
        self._activate_btn.setFixedHeight(32)
        self._activate_btn.setObjectName("Primary")
        icons.set_button_icon(self._activate_btn, "mdi6.lightning-bolt",
                              color=icons.TONE_ON_ACCENT, size=14)
        self._activate_btn.setToolTip("激活选中标本（全局互斥）")
        self._activate_btn.clicked.connect(self._on_activate_clicked)
        btn_row.addWidget(self._activate_btn)

        self._deactivate_btn = QPushButton("去激活")
        self._deactivate_btn.setObjectName("Outline")
        self._deactivate_btn.setFixedHeight(32)
        self._deactivate_btn.setToolTip("取消当前激活标本")
        self._deactivate_btn.clicked.connect(self._on_deactivate_clicked)
        btn_row.addWidget(self._deactivate_btn)

        self._refresh_btn = QPushButton()
        self._refresh_btn.setObjectName("Ghost")
        self._refresh_btn.setFixedSize(32, 32)
        icons.set_button_icon(self._refresh_btn, "mdi6.refresh", color=icons.TONE_MUTED, size=16)
        self._refresh_btn.setToolTip("刷新标本列表")
        self._refresh_btn.clicked.connect(self.refresh)
        btn_row.addWidget(self._refresh_btn)
        root.addLayout(btn_row)

        # ── collab-status bottom strip ──
        collab_strip = QFrame()
        collab_strip.setObjectName("CollabStrip")
        cs_lay = QVBoxLayout(collab_strip)
        cs_lay.setContentsMargins(10, 8, 10, 8)
        cs_lay.setSpacing(5)

        cs_title = QLabel("协作状态")
        cs_title.setObjectName("Section")
        cs_lay.addWidget(cs_title)

        self._collab_addr = QLabel("分享地址: —")
        self._collab_addr.setObjectName("MutedSmall")
        cs_lay.addWidget(self._collab_addr)

        cs_device_row = QHBoxLayout()
        cs_device_row.setContentsMargins(0, 0, 0, 0)
        cs_device_row.setSpacing(6)
        self._collab_device = QLabel("匿名·本机")
        self._collab_device.setObjectName("MutedSmall")
        cs_device_row.addWidget(self._collab_device)
        cs_device_row.addStretch()
        self._collab_members = QLabel("成员: 0")
        self._collab_members.setObjectName("MutedSmall")
        cs_device_row.addWidget(self._collab_members)
        cs_lay.addLayout(cs_device_row)

        self._collab_sync = QLabel("同步编号: —")
        self._collab_sync.setObjectName("MutedSmall")
        cs_lay.addWidget(self._collab_sync)

        collab_mgr_btn = QPushButton("协作管理")
        collab_mgr_btn.setObjectName("Ghost")
        collab_mgr_btn.setFixedHeight(26)
        cs_lay.addWidget(collab_mgr_btn)

        root.addWidget(collab_strip)

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

    def _on_activate_clicked(self) -> None:
        """Emit activate_requested for the currently selected specimen."""
        uid = self.current_uid()
        if uid:
            self.activate_requested.emit(uid)

    def _on_deactivate_clicked(self) -> None:
        """Emit deactivate_requested for the currently selected specimen."""
        uid = self.current_uid()
        if uid:
            self.deactivate_requested.emit(uid)
