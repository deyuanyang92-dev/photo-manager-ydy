"""collab_specimen_card.py — Right-rail Card 4: specimen collaboration status.

Follows the exact QFrame + PanelCard + shadow + collapse pattern used by
NamingPanel, TaxonCardPanel, and MetadataPanel.  Shows the collab task
status for the currently selected specimen (or "未认领" if unclaimed).
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from app.app_context import AppContext

# Status display (shared with CollabPanel / CollabManagerDialog)
_STATUS_LABEL: dict[str, str] = {
    "created":    "已创建",
    "assigned":   "已指派",
    "shooting":   "拍摄中",
    "shot_done":  "拍摄完成",
    "organizing": "整理中",
    "done":       "完成",
    "void":       "作废",
    "conflict":   "冲突",
}

_STATUS_COLOURS: dict[str, str] = {
    "created":    "#6eb5ff",
    "assigned":   "#a8d8ea",
    "shooting":   "#f6d365",
    "shot_done":  "#b8f0b8",
    "organizing": "#ffd180",
    "done":       "#69f0ae",
    "void":       "#9e9e9e",
    "conflict":   "#ff5252",
}

_STATUS_ICON: dict[str, str] = {
    "created":    "🔵",
    "assigned":   "🔵",
    "shooting":   "🟡",
    "shot_done":  "🟢",
    "organizing": "🟡",
    "done":       "✅",
    "void":       "⚫",
    "conflict":   "🔴",
}


class CollabSpecimenCard(QWidget):
    """Right-rail Card 4: collaboration status for the selected specimen."""

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._uid: Optional[str] = None
        self._collapsed = True  # default collapsed to save vertical space
        self._setup_ui()

    def _setup_ui(self) -> None:
        card = QFrame(self)
        card.setObjectName("PanelCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        from app.config.effects import apply_card_shadow
        apply_card_shadow(card)

        root = QVBoxLayout(card)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)
        self._root = root

        # Header: title + collapse
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)
        title = QLabel("协作状态")
        title.setObjectName("CardTitle")
        header.addWidget(title)
        header.addStretch()
        self._collapse_btn = QPushButton("▸")
        self._collapse_btn.setObjectName("Ghost")
        self._collapse_btn.setFixedSize(28, 26)
        self._collapse_btn.setToolTip("展开")
        self._collapse_btn.clicked.connect(
            lambda: self.set_collapsed(not self._collapsed)
        )
        header.addWidget(self._collapse_btn)
        root.addLayout(header)

        # Body (hidden by default — collapsed)
        self._body = QWidget()
        body_lay = QVBoxLayout(self._body)
        body_lay.setContentsMargins(4, 4, 4, 4)
        body_lay.setSpacing(8)

        # Status info rows
        self._status_label = QLabel("—")
        self._status_label.setObjectName("Muted")
        body_lay.addWidget(self._status_label)

        self._assignee_label = QLabel("—")
        self._assignee_label.setObjectName("MutedSmall")
        body_lay.addWidget(self._assignee_label)

        self._time_label = QLabel("")
        self._time_label.setObjectName("MutedSmall")
        body_lay.addWidget(self._time_label)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self._claim_btn = QPushButton("认领")
        self._claim_btn.setObjectName("Outline")
        self._claim_btn.setFixedHeight(26)
        self._claim_btn.clicked.connect(self._on_claim)
        btn_row.addWidget(self._claim_btn)

        self._release_btn = QPushButton("释放")
        self._release_btn.setObjectName("Ghost")
        self._release_btn.setFixedHeight(26)
        self._release_btn.setStyleSheet("color: #e57373;")
        self._release_btn.clicked.connect(self._on_release)
        btn_row.addWidget(self._release_btn)

        self._shooting_btn = QPushButton("拍摄中")
        self._shooting_btn.setObjectName("Ghost")
        self._shooting_btn.setFixedHeight(26)
        self._shooting_btn.clicked.connect(lambda: self._on_transition("shooting"))
        btn_row.addWidget(self._shooting_btn)

        self._done_btn = QPushButton("完成")
        self._done_btn.setObjectName("Ghost")
        self._done_btn.setFixedHeight(26)
        self._done_btn.clicked.connect(lambda: self._on_transition("done"))
        btn_row.addWidget(self._done_btn)

        btn_row.addStretch()
        body_lay.addLayout(btn_row)

        root.addWidget(self._body)
        self._body.hide()  # start collapsed

    # ── Collapse ───────────────────────────────────────────────────────────

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self._body.setVisible(not collapsed)
        self._collapse_btn.setText("▸" if collapsed else "▾")
        self._collapse_btn.setToolTip("展开" if collapsed else "收起")

    def is_collapsed(self) -> bool:
        return self._collapsed

    # ── Public API ─────────────────────────────────────────────────────────

    def load_specimen(self, uid: str) -> None:
        """Update the card for the given specimen UID."""
        self._uid = uid
        self._refresh()

    def clear(self) -> None:
        """Reset to empty state."""
        self._uid = None
        self._status_label.setText("—")
        self._assignee_label.setText("")
        self._time_label.setText("")
        self._claim_btn.setEnabled(False)
        self._release_btn.setEnabled(False)
        self._shooting_btn.setEnabled(False)
        self._done_btn.setEnabled(False)

    # ── Refresh ────────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        svc = getattr(self.ctx, "collab_service", None)
        if svc is None or not svc.is_running() or not self._uid:
            self._show_no_collab()
            return

        task = svc.store.get(self._uid)
        if task is None:
            self._show_unclaimed()
            return

        sv = task.status.value if hasattr(task.status, "value") else str(task.status)
        icon_str = _STATUS_ICON.get(sv, "●")
        label = _STATUS_LABEL.get(sv, sv)
        colour = _STATUS_COLOURS.get(sv, "#ffffff")

        self._status_label.setText(f"{icon_str} {label}")
        self._status_label.setStyleSheet(f"color: {colour}; font-weight: bold;")

        assignee = task.assignee or "—"
        device = task.device_id or ""
        self._assignee_label.setText(f"负责人: {assignee}" + (f" ({device})" if device else ""))

        ts = task.updated_at[:16].replace("T", " ") if task.updated_at else ""
        self._time_label.setText(f"更新: {ts}" if ts else "")

        # Enable action buttons based on state
        self._claim_btn.setEnabled(False)  # Already claimed
        self._release_btn.setEnabled(True)
        self._shooting_btn.setEnabled(sv in ("created", "assigned"))
        self._done_btn.setEnabled(sv in ("shot_done", "organizing"))

    def _show_no_collab(self) -> None:
        """Collab service not running — show muted state."""
        self._status_label.setText("⚪ 协作未启用")
        self._status_label.setStyleSheet("")
        self._assignee_label.setText("")
        self._time_label.setText("")
        self._claim_btn.setEnabled(False)
        self._release_btn.setEnabled(False)
        self._shooting_btn.setEnabled(False)
        self._done_btn.setEnabled(False)

    def _show_unclaimed(self) -> None:
        """Specimen exists but no collab task — show claim button."""
        self._status_label.setText("⚪ 未被任何设备认领")
        self._status_label.setStyleSheet("")
        self._assignee_label.setText("")
        self._time_label.setText("")
        self._claim_btn.setEnabled(True)
        self._release_btn.setEnabled(False)
        self._shooting_btn.setEnabled(False)
        self._done_btn.setEnabled(False)

    # ── Actions ────────────────────────────────────────────────────────────

    def _on_claim(self) -> None:
        svc = getattr(self.ctx, "collab_service", None)
        if svc is None or not self._uid:
            return
        operator = self.ctx.settings.value("user/current_user", "", type=str) or ""
        ok, msg = svc.create_task(self._uid, assignee=operator, device_id=svc._hostname)
        if not ok:
            self._status_label.setText(f"🔴 认领失败: {msg}")
            self._status_label.setStyleSheet("color: #cf222e;")
        else:
            self._refresh()

    def _on_release(self) -> None:
        svc = getattr(self.ctx, "collab_service", None)
        if svc is None or not self._uid:
            return
        svc.release_task(self._uid)
        self._refresh()

    def _on_transition(self, new_status: str) -> None:
        svc = getattr(self.ctx, "collab_service", None)
        if svc is None or not self._uid:
            return
        try:
            from app.services.collab_service import TaskStatus
            svc.store.update_status(self._uid, TaskStatus(new_status))
            svc._log_activity(
                "status_changed", self._uid,
                detail=f"编号 {self._uid} 状态变为 {_STATUS_LABEL.get(new_status, new_status)}",
            )
            svc.specimen_status_changed.emit(self._uid)
        except ValueError:
            pass
        self._refresh()
