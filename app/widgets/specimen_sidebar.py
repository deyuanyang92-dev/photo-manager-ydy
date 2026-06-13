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
    QApplication,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.config import icons

if TYPE_CHECKING:
    from app.app_context import AppContext
    from app.services.collab_service import CollabService


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
    collab_manager_requested = pyqtSignal()   # "协作管理" button clicked
    print_labels_requested = pyqtSignal(str)
    phase_mark_requested = pyqtSignal(str, str)  # (uid, status_code) — phase dot click

    # 4 per-编号 phase dots: (status_code, objectName, tooltip).  Order = workflow.
    _PHASE_DOTS = (
        ("shooting",   "PhaseDotShooting",   "拍摄中"),
        ("shot_done",  "PhaseDotShotDone",   "已拍完"),
        ("organizing", "PhaseDotOrganizing", "整理中"),
        ("done",       "PhaseDotDone",       "完成"),
    )

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._all_items: list[dict] = []  # [{uid, display, active}]
        # uid -> {code: QPushButton} for the 4 phase dots; uid -> current code.
        self._phase_dots: dict[str, dict[str, QPushButton]] = {}
        self._phase_state: dict[str, Optional[str]] = {}
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
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
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

        self._collab_mgr_btn = QPushButton("协作面板")
        self._collab_mgr_btn.setObjectName("Ghost")
        self._collab_mgr_btn.setFixedHeight(26)
        self._collab_mgr_btn.clicked.connect(self.collab_manager_requested.emit)
        cs_lay.addWidget(self._collab_mgr_btn)

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
        """Rebuild list based on search text.

        Each row is a custom widget: UID + 学名/中文名 + 协作 badge + a row of
        4 clickable phase dots (拍摄中/已拍完/整理中/完成).  Clicking a dot marks
        that 编号's phase via :attr:`phase_mark_requested` — no activation needed.
        """
        self._list.clear()
        self._phase_dots.clear()
        self._phase_state.clear()
        query = text.strip().lower()
        shown = 0
        svc = getattr(self.ctx, "collab_service", None)

        for entry in self._all_items:
            uid: str = entry["uid"]
            name: str = entry["name"]
            name_cn: str = entry["name_cn"]
            if query and query not in uid.lower() and query not in name.lower() and query not in name_cn.lower():
                continue

            row = self._build_row_widget(uid, name, name_cn, svc)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, uid)
            item.setToolTip(uid)
            item.setSizeHint(row.sizeHint())
            self._list.addItem(item)
            self._list.setItemWidget(item, row)
            shown += 1

        self._count_label.setText(str(shown))

    def _build_row_widget(self, uid: str, name: str, name_cn: str, svc) -> QWidget:
        """Build one specimen row: UID + name + collab badge + 4 phase dots."""
        row = QWidget()
        v = QVBoxLayout(row)
        v.setContentsMargins(2, 3, 2, 3)
        v.setSpacing(2)

        uid_lbl = QLabel(uid)
        uid_lbl.setObjectName("SpecimenUid")
        v.addWidget(uid_lbl)

        name_text = name or name_cn
        badge = self._collab_badge(uid, svc)
        if name_text or badge:
            line = QHBoxLayout()
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(6)
            if name_text:
                nm = QLabel(name_text)
                nm.setObjectName("MutedSmall")
                line.addWidget(nm)
            line.addStretch()
            if badge:
                bd = QLabel(badge)
                bd.setObjectName("MutedSmall")
                line.addWidget(bd)
            v.addLayout(line)

        # ── Phase dots ──
        current = self._phase_for(uid, svc)
        self._phase_state[uid] = current
        dots_row = QHBoxLayout()
        dots_row.setContentsMargins(0, 1, 0, 0)
        dots_row.setSpacing(7)
        self._phase_dots[uid] = {}
        for code, obj_name, tip in self._PHASE_DOTS:
            dot = QPushButton()
            dot.setObjectName(obj_name)
            # 强制正方固定尺寸 —— 仅靠 QSS max-width 拗不过按钮默认 padding，会被撑成
            # 矩形；setFixedSize 锁死，配 QSS border-radius=半径 → 真·小圆点。
            dot.setFixedSize(13, 13)
            dot.setCheckable(True)
            dot.setChecked(code == current)
            dot.setCursor(Qt.CursorShape.PointingHandCursor)
            dot.setToolTip(f"标记为「{tip}」")
            dot.clicked.connect(
                lambda _=False, u=uid, c=code: self._on_dot_clicked(u, c)
            )
            dots_row.addWidget(dot)
            self._phase_dots[uid][code] = dot
        dots_row.addStretch()
        v.addLayout(dots_row)
        return row

    def _phase_for(self, uid: str, svc) -> Optional[str]:
        """Resolve *uid*'s confirmed phase (collab task first, else project DB)."""
        try:
            from app.services.activation_service import resolve_phase
            return resolve_phase(svc, self.ctx.get_db(), uid)
        except Exception:
            return None

    def _on_dot_clicked(self, uid: str, code: str) -> None:
        """A phase dot was clicked: roll back Qt's auto-toggle, then request mark.

        The workbench confirms the change and calls :meth:`refresh_phases`, which
        re-syncs the dots to the persisted truth — so an out-of-order or failed
        mark never leaves a stale dot lit.
        """
        self._sync_row_dots(uid, self._phase_state.get(uid))
        self.phase_mark_requested.emit(uid, code)

    def _sync_row_dots(self, uid: str, current: Optional[str]) -> None:
        """Set checked-state of *uid*'s dots so only *current* is filled."""
        dots = self._phase_dots.get(uid)
        if not dots:
            return
        for code, dot in dots.items():
            dot.setChecked(code == current)

    def refresh_phases(self) -> None:
        """Re-read each visible 编号's phase and update its dots (no rebuild)."""
        svc = getattr(self.ctx, "collab_service", None)
        for uid in list(self._phase_dots.keys()):
            current = self._phase_for(uid, svc)
            self._phase_state[uid] = current
            self._sync_row_dots(uid, current)

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

    def _on_context_menu(self, pos) -> None:
        """Right-click specimen actions: copy UID / print labels / activate."""
        item = self._list.itemAt(pos)
        if item is None:
            return
        self._list.setCurrentItem(item)
        uid = item.data(Qt.ItemDataRole.UserRole)
        if not uid:
            return

        menu = QMenu(self)
        copy_act = menu.addAction("复制编号")
        print_act = menu.addAction("打印标签...")
        menu.addSeparator()
        rename_act = menu.addAction("修改编号…")
        menu.addSeparator()
        activate_act = menu.addAction("激活")
        deactivate_act = menu.addAction("去激活")

        chosen = menu.exec(self._list.viewport().mapToGlobal(pos))
        if chosen == copy_act:
            QApplication.clipboard().setText(uid)
        elif chosen == print_act:
            self.print_labels_requested.emit(uid)
        elif chosen == rename_act:
            self._on_rename_specimen_code(uid)
        elif chosen == activate_act:
            self.activate_requested.emit(uid)
        elif chosen == deactivate_act:
            self.deactivate_requested.emit(uid)

    def _on_rename_specimen_code(self, uid: str) -> None:
        """Prompt for a new specimen id segment and apply rename via the service."""
        from app.utils.naming import parse_uid
        parsed = parse_uid(uid)
        current_code = (parsed or {}).get("speciesId") or ""
        new_code, ok = QInputDialog.getText(
            self, "修改标本编号", "新编号：", text=current_code
        )
        if not ok or not new_code.strip():
            return

        from app.services.specimen_rename_service import (
            rename_specimen_code,
            specimen_has_risky_references,
        )
        db = self.ctx.get_db()
        if not db:
            QMessageBox.critical(self, "错误", "未打开项目，无法修改编号")
            return

        if specimen_has_risky_references(db, uid):
            reply = QMessageBox.warning(
                self,
                "警告",
                f"该标本已有分组/任务记录。\n"
                f"编号将从 {uid} 改变。\n"
                "已生成的 TIFF/ZIP 文件名不会自动重命名。\n\n确认修改？",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Ok:
                return

        try:
            rename_specimen_code(db, uid, new_code.strip())
            self.refresh()
        except ValueError as exc:
            QMessageBox.critical(self, "错误", str(exc))

    def copy_current_uid(self) -> bool:
        """Copy selected UID to clipboard. Returns False when nothing selected."""
        uid = self.current_uid()
        if not uid:
            return False
        QApplication.clipboard().setText(uid)
        return True

    def print_current_labels(self) -> bool:
        """Request label printing for the selected UID."""
        uid = self.current_uid()
        if not uid:
            return False
        self.print_labels_requested.emit(uid)
        return True

    # ── Collab badge helpers ──────────────────────────────────────────────────

    @staticmethod
    def _collab_badge(uid: str, svc) -> str:
        """Return a short collab status badge string for *uid*, or "" if none."""
        if svc is None or not svc.is_running():
            return ""
        task = svc.store.get(uid)
        if task is None:
            return ""
        sv = task.status.value if hasattr(task.status, "value") else str(task.status)
        # Check if this device owns the task
        hostname = svc._hostname if hasattr(svc, "_hostname") else ""
        is_mine = task.device_id == hostname if task.device_id else False
        if sv == "conflict":
            return "🔴 冲突"
        if is_mine:
            return "🟢 我"
        if task.assignee:
            return f"🔵 {task.assignee[:4]}"
        return "🔵 已认领"

    def _refresh_collab_badges(self) -> None:
        """Re-apply the current filter to refresh collab badges."""
        self._apply_filter(self._search.text())

    # ── Collab strip update ───────────────────────────────────────────────────

    def _open_collab_view(self) -> None:
        """Navigate the main window to the collab view."""
        win = self.window()
        if hasattr(win, "navigate_to"):
            win.navigate_to("collab")
        elif hasattr(win, "switch_to_view"):
            win.switch_to_view("collab")

    def update_collab_status(self, service: Optional["CollabService"]) -> None:
        """Refresh the sidebar collab strip from *service*.

        Designed to be called on CollabService.peers_changed and
        CollabService.server_ready signals.  Safe to call with service=None
        (shows static placeholder values).
        """
        if service is None:
            self._collab_addr.setText("分享地址: —")
            self._collab_device.setText("匿名·本机")
            self._collab_members.setText("成员: 0")
            self._collab_sync.setText("同步编号: —")
            return

        addr = service.local_address()
        self._collab_addr.setText(f"分享地址: {addr}")

        peers = service.peers()
        n_peers = len(peers)
        self._collab_members.setText(f"成员: {n_peers}")

        import socket as _socket
        hostname = _socket.gethostname()
        self._collab_device.setText(f"{hostname}")

        task_count = len(service.store.all())
        self._collab_sync.setText(f"同步编号: {task_count} 条")

        # Refresh badges in the specimen list
        self._refresh_collab_badges()
