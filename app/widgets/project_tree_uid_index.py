"""Vertical UID index for project-tree photo grid (workbench-style quick jump)."""
from __future__ import annotations

from typing import Any, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

_CATALOG_ROLE = Qt.ItemDataRole.UserRole
_CATALOG_ROLE2 = Qt.ItemDataRole.UserRole + 1


class ProjectTreeUidIndex(QFrame):
    """Narrow left rail: tap a 编号 to jump the grid + preview."""

    uid_clicked = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("ProjectTreeUidIndex")
        self.setMinimumWidth(96)
        self.setMaximumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 4, 0)
        lay.setSpacing(4)
        head = QLabel("编号")
        head.setObjectName("MutedSmall")
        head.setStyleSheet("font-weight:600;")
        lay.addWidget(head)
        self._list = QListWidget()
        self._list.setObjectName("ProjectTreeUidList")
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.itemClicked.connect(self._on_item_clicked)
        lay.addWidget(self._list, 1)
        self._current_uid = ""

    def set_entries(self, entries: list[dict[str, Any]]) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for row in entries or []:
            uid = str(row.get("uid") or "")
            catalog_key = str(row.get("catalog_key") or row.get("abbrev") or uid)
            abbrev = str(row.get("abbrev") or catalog_key or "未分组")
            count = int(row.get("count") or 0)
            text = f"{abbrev}  ({count})" if count else abbrev
            it = QListWidgetItem(text)
            it.setData(_CATALOG_ROLE, catalog_key)
            it.setData(_CATALOG_ROLE2, uid)
            it.setToolTip("")
            self._list.addItem(it)
        self._list.blockSignals(False)
        if self._current_uid:
            self.set_current_uid(self._current_uid)

    def set_current_uid(self, uid: str) -> None:
        self._current_uid = str(uid or "")
        if not self._current_uid:
            self._list.clearSelection()
            return
        from app.widgets.uid_grouped_grid import catalog_key_for_uid, uid_matches_selection

        target_ck = catalog_key_for_uid(self._current_uid)
        for i in range(self._list.count()):
            it = self._list.item(i)
            ck = str(it.data(_CATALOG_ROLE) or "")
            stored_uid = str(it.data(_CATALOG_ROLE2) or "")
            if (
                ck == target_ck
                or uid_matches_selection(self._current_uid, stored_uid)
                or uid_matches_selection(self._current_uid, ck)
            ):
                self._list.setCurrentItem(it)
                self._list.scrollToItem(it, QAbstractItemView.ScrollHint.EnsureVisible)
                return

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        key = str(item.data(_CATALOG_ROLE) or "")
        uid = str(item.data(_CATALOG_ROLE2) or key)
        self._current_uid = key or uid
        self.uid_clicked.emit(key or uid)
