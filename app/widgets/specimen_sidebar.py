"""specimen_sidebar.py — Left-column specimen list widget.

Shows all specimens for the current project (filtered by ownerProjectDir),
with a search box to filter by UID or scientific name.

Data is loaded from the DB specimens table via AppContext.get_db().
Emits ``specimen_selected(uid: str)`` when the user clicks a row.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.config import icons
from app.utils.naming import (
    build_configured_uid,
    component_values_from_specimen,
    derive_uid,
    normalize_uid,
    parse_uid,
)
from app.utils.path_utils import equivalent_paths

if TYPE_CHECKING:
    from app.app_context import AppContext
    from app.services.collab_service import CollabService


# 行是否为「当前激活编号」—— 重算行高时要还原基准高度, 存在 item data 上。
_ACTIVE_ROLE = int(Qt.ItemDataRole.UserRole) + 1


# ── Badge colours matching the 5 file-state palette ─────────────────────────
_ACTIVE_STYLE = (
    "background:#29b9ab; color:#08161b; border-radius:3px;"
    " font-size:11px; padding:1px 6px; font-weight:600;"
)
_INACTIVE_STYLE = (
    "background:transparent; color:#87a2a1; border-radius:3px;"
    " font-size:11px; padding:1px 6px;"
)


def _chunks(values: list[str], size: int = 900):
    for i in range(0, len(values), size):
        yield values[i:i + size]


class _ClickableLabel(QLabel):
    clicked = pyqtSignal()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


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
    # Ctrl/Shift 多选编号 → 成果区只显示这些编号的成果(单选/清空时发 1/0 个)。
    selection_scope_changed = pyqtSignal(list)
    show_all_requested = pyqtSignal()
    activate_requested = pyqtSignal(str)
    deactivate_requested = pyqtSignal(str)
    activate_no_selection = pyqtSignal()
    new_specimen_requested = pyqtSignal()
    edit_specimen_requested = pyqtSignal(str)
    collab_manager_requested = pyqtSignal()   # "协作管理" button clicked
    sync_selected_requested = pyqtSignal()
    sync_project_requested = pyqtSignal()
    sync_selected_overwrite_requested = pyqtSignal()
    sync_project_overwrite_requested = pyqtSignal()
    print_labels_requested = pyqtSignal(str)
    delete_specimen_requested = pyqtSignal(str)
    print_rna_queue_requested = pyqtSignal()
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
        self._filter_mode = "all"
        self._sort_key = "uid"
        self._sort_reverse = False
        # uid -> {code: QPushButton} for the 4 phase dots; uid -> current code.
        self._phase_dots: dict[str, dict[str, QPushButton]] = {}
        self._phase_state: dict[str, Optional[str]] = {}
        self._uid_normalized_projects: set[str] = set()
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
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(8)

        self._new_btn = QPushButton("新编号")
        self._new_btn.setObjectName("Primary")
        self._new_btn.setFixedHeight(32)
        self._new_btn.setMinimumWidth(84)
        icons.set_button_icon(self._new_btn, "mdi6.plus", color=icons.TONE_ON_ACCENT, size=14)
        self._new_btn.setToolTip("新建标本唯一编号（右侧填写）")
        self._new_btn.clicked.connect(self.new_specimen_requested.emit)
        top_row.addWidget(self._new_btn)

        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索编号 / 物种")
        self._search.setClearButtonEnabled(True)
        self._search.setFixedHeight(32)
        if icons.available():
            self._search.addAction(
                icons.icon("mdi6.magnify", color=icons.TONE_MUTED),
                QLineEdit.ActionPosition.LeadingPosition,
            )
        self._search.textChanged.connect(self._on_search)
        top_row.addWidget(self._search, stretch=1)

        self._sort_btn = QPushButton("编号")
        self._sort_btn.setObjectName("Ghost")
        self._sort_btn.setFixedHeight(32)
        self._sort_btn.setToolTip("排序：编号、采集日期、拍照日期、RNA、资料状态")
        icons.set_button_icon(self._sort_btn, "mdi6.sort", color=icons.TONE_MUTED, size=14)
        self._sort_btn.clicked.connect(self._show_sort_menu)
        top_row.addWidget(self._sort_btn)
        root.addLayout(top_row)

        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(6)
        self._filter_all_btn = QPushButton("全部")
        self._filter_all_btn.setObjectName("Ghost")
        self._filter_all_btn.setCheckable(True)
        self._filter_all_btn.setChecked(True)
        self._filter_all_btn.setFixedHeight(24)
        self._filter_all_btn.setToolTip("显示全部编号，并在成果区展示全部编号对应结果")
        self._filter_all_btn.clicked.connect(lambda: self._set_filter_mode("all"))
        filter_row.addWidget(self._filter_all_btn)
        self._filter_rna_btn = QPushButton("RNA编号")
        self._filter_rna_btn.setObjectName("Ghost")
        self._filter_rna_btn.setCheckable(True)
        self._filter_rna_btn.setFixedHeight(24)
        self._filter_rna_btn.setToolTip("筛选保存方式以 R 开头的 RNA 编号")
        self._filter_rna_btn.clicked.connect(lambda: self._set_filter_mode("rna"))
        filter_row.addWidget(self._filter_rna_btn)
        self._filter_attention_btn = QPushButton("资料待补")
        self._filter_attention_btn.setObjectName("Ghost")
        self._filter_attention_btn.setCheckable(True)
        self._filter_attention_btn.setFixedHeight(24)
        self._filter_attention_btn.setToolTip(
            "筛选资料不完整的编号：缺物种名、保存方式、采集日期或拍照日期"
        )
        self._filter_attention_btn.clicked.connect(lambda: self._set_filter_mode("attention"))
        filter_row.addWidget(self._filter_attention_btn)
        filter_row.addStretch()
        root.addLayout(filter_row)

        # List
        self._list = QListWidget()
        self._list.setObjectName("SpecimenList")
        self._list.setAlternatingRowColors(True)
        self._list.setSpacing(1)
        # §7 旧: 默认 SingleSelection —— 用户无法「选多个编号看它们的成果」。
        # 普通单击行为不变(仍单选 + 加载该编号), Ctrl/Shift 才进入多选。
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.currentItemChanged.connect(self._on_current_item_changed)
        self._list.itemSelectionChanged.connect(self._on_selection_scope_changed)
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
        cs_lay.setContentsMargins(8, 6, 8, 6)
        cs_lay.setSpacing(4)

        cs_title = QLabel("协作")
        cs_title.setObjectName("MutedSmall")
        cs_lay.addWidget(cs_title)

        self._collab_addr = QLabel("连接地址: —")
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

        sync_btn_row = QHBoxLayout()
        sync_btn_row.setContentsMargins(0, 0, 0, 0)
        sync_btn_row.setSpacing(6)

        self._collab_sync_selected_btn = QPushButton("同步当前编号")
        self._collab_sync_selected_btn.setObjectName("Ghost")
        self._collab_sync_selected_btn.setFixedHeight(26)
        self._collab_sync_selected_btn.setToolTip(
            "从同组且同项目同步码的在线设备补齐当前编号的照片/TIF/ZIP；不要求两台电脑盘符一致。"
        )
        selected_menu = QMenu(self._collab_sync_selected_btn)
        selected_smart = selected_menu.addAction("智能补齐")
        selected_force = selected_menu.addAction("强制覆盖本机")
        selected_smart.triggered.connect(lambda _=False: self.sync_selected_requested.emit())
        selected_force.triggered.connect(lambda _=False: self.sync_selected_overwrite_requested.emit())
        self._collab_sync_selected_btn.setMenu(selected_menu)
        sync_btn_row.addWidget(self._collab_sync_selected_btn)

        self._collab_sync_project_btn = QPushButton("同步项目")
        self._collab_sync_project_btn.setObjectName("Ghost")
        self._collab_sync_project_btn.setFixedHeight(26)
        self._collab_sync_project_btn.setToolTip(
            "从同组且同项目同步码的在线设备补齐当前项目缺失的照片/TIF/ZIP；不同项目只同步任务状态。"
        )
        project_menu = QMenu(self._collab_sync_project_btn)
        project_smart = project_menu.addAction("智能补齐")
        project_force = project_menu.addAction("强制覆盖本机")
        project_smart.triggered.connect(lambda _=False: self.sync_project_requested.emit())
        project_force.triggered.connect(lambda _=False: self.sync_project_overwrite_requested.emit())
        self._collab_sync_project_btn.setMenu(project_menu)
        sync_btn_row.addWidget(self._collab_sync_project_btn)
        cs_lay.addLayout(sync_btn_row)

        self._collab_mgr_btn = QPushButton("协作面板")
        self._collab_mgr_btn.setObjectName("Ghost")
        self._collab_mgr_btn.setFixedHeight(26)
        self._collab_mgr_btn.clicked.connect(self.collab_manager_requested.emit)
        cs_lay.addWidget(self._collab_mgr_btn)

        root.addWidget(collab_strip)

    def set_rna_queue_count(self, count: int) -> None:
        # RNAlater queue is still maintained by the workbench, but the sidebar
        # no longer exposes a separate "RNA 待打印" control in this compact rail.
        return

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
        project_dirs = equivalent_paths(project_dir)
        self._normalize_project_specimen_uids(db, project_dir)

        rows: list[dict] = []
        try:
            owner_filter = ",".join("?" * len(project_dirs)) or "?"
            params = project_dirs or [project_dir]
            cursor = db.execute(
                f"""
                SELECT uid,
                       COALESCE(scientific_name, '') AS name,
                       COALESCE(scientific_name_cn, '') AS name_cn,
                       COALESCE(storage, '') AS storage,
                       COALESCE(collection_date, '') AS collection_date,
                       COALESCE(photo_date, '') AS photo_date,
                       rowid AS row_order
                FROM   specimens
                WHERE  owner_project_dir IN ({owner_filter})
                ORDER  BY uid
                """,
                params,
            )
            for row in cursor.fetchall():
                rows.append(
                    {
                        "uid": normalize_uid(row[0]),
                        "name": row[1],
                        "name_cn": row[2],
                        "storage": row[3],
                        "collection_date": row[4],
                        "photo_date": row[5],
                        "row_order": row[6],
                        "is_rna": str(row[3] or "").upper().startswith("R"),
                        "needs_attention": (
                            not str(row[1] or row[2] or "").strip()
                            or not str(row[3] or "").strip()
                            or not str(row[4] or "").strip()
                            or not str(row[5] or "").strip()
                        ),
                    }
                )
        except Exception:
            pass

        # Merge UID claims synced from LAN peers so the left rail is the live
        # collaboration index even before that specimen's full metadata exists
        # locally.
        try:
            svc = getattr(self.ctx, "collab_service", None)
            if svc is not None and svc.is_running():
                existing = {r["uid"] for r in rows}
                for task in svc.store.list_tasks():
                    uid = normalize_uid(getattr(task, "uid", "") or "")
                    if not uid or uid in existing:
                        continue
                    rows.append(
                        {
                            "uid": uid,
                            "name": "",
                            "name_cn": "局域网同步编号",
                            "storage": "",
                            "collection_date": "",
                            "photo_date": "",
                            "row_order": 999999,
                            "is_rna": False,
                            "needs_attention": False,
                            "collab_only": True,
                        }
                    )
                    existing.add(uid)
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
        progress = self._load_grouping_progress(db, [r["uid"] for r in rows])
        for r in rows:
            r["progress"] = progress.get(r["uid"], {})

        return rows

    def _load_grouping_progress(self, db, uids: list[str]) -> dict[str, dict]:
        """Return per-UID grouping progress derived from persisted groups."""
        if not uids:
            return {}
        progress: dict[str, dict] = {
            uid: {"total": 0, "grouped": 0, "composed": 0, "organized": 0}
            for uid in uids
        }
        rows = []
        try:
            for chunk in _chunks(uids):
                placeholders = ",".join("?" * len(chunk))
                rows.extend(
                    db.execute(
                        f"""
                        SELECT uid, jpg_paths, composed_tiff_path, status, archive_zip
                        FROM grouping
                        WHERE uid IN ({placeholders})
                        """,
                        chunk,
                    ).fetchall()
                )
        except Exception:
            return progress

        for row in rows:
            uid = normalize_uid(row["uid"] if hasattr(row, "keys") else row[0])
            if uid not in progress:
                continue
            jpg_paths = row["jpg_paths"] if hasattr(row, "keys") else row[1]
            composed = row["composed_tiff_path"] if hasattr(row, "keys") else row[2]
            status = row["status"] if hasattr(row, "keys") else row[3]
            archive = row["archive_zip"] if hasattr(row, "keys") else row[4]
            p = progress[uid]
            p["total"] += 1
            try:
                if len(json.loads(jpg_paths or "[]")) > 0:
                    p["grouped"] += 1
            except Exception:
                if str(jpg_paths or "").strip() not in ("", "[]"):
                    p["grouped"] += 1
            if composed:
                p["composed"] += 1
            if str(status or "").lower() == "organized" or archive:
                p["organized"] += 1
        return progress

    def _apply_filter(self, text: str) -> None:
        """Rebuild list based on search text.

        Each row is a custom widget: UID + 学名/中文名 + 协作 badge + a row of
        4 clickable phase dots (拍摄中/已拍完/整理中/完成).  Clicking a dot marks
        that 编号's phase via :attr:`phase_mark_requested` — no activation needed.
        """
        was_enabled = self._list.updatesEnabled()
        self._list.setUpdatesEnabled(False)
        # 重建期间 clear()/addItem() 会连发 itemSelectionChanged —— 不抑制的话
        # 每次刷新都往工作台推一串空/半截选区, 触发无谓的成果区重载。
        self._list.blockSignals(True)
        try:
            self._list.clear()
            self._phase_dots.clear()
            self._phase_state.clear()
            query = text.strip().lower()
            shown = 0
            svc = getattr(self.ctx, "collab_service", None)
            # 一次批量取相位; 下面每行的 _phase_for 直接命中缓存, 不再逐行查库。
            self._prefetch_phases([e["uid"] for e in self._all_items])

            for entry in self._sorted_items(self._all_items):
                uid: str = entry["uid"]
                name: str = entry["name"]
                name_cn: str = entry["name_cn"]
                storage: str = entry.get("storage") or ""
                is_rna = bool(entry.get("is_rna"))
                progress = entry.get("progress") or {}
                if self._filter_mode == "rna" and not is_rna:
                    continue
                if self._filter_mode == "attention" and not entry.get("needs_attention"):
                    continue
                if (
                    query
                    and query not in uid.lower()
                    and query not in name.lower()
                    and query not in name_cn.lower()
                    and query not in storage.lower()
                    and query not in str(entry.get("collection_date") or "").lower()
                    and query not in str(entry.get("photo_date") or "").lower()
                ):
                    continue

                active = bool(entry.get("active"))
                row = self._build_row_widget(
                    uid,
                    name,
                    name_cn,
                    storage,
                    svc,
                    active=active,
                    is_rna=is_rna,
                    progress=progress,
                )
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, uid)
                item.setToolTip("")
                item.setData(_ACTIVE_ROLE, active)
                # §7 旧: item.setSizeHint(QSize(0, 96 if active else 90)) —— 写死高度,
                # 长编号(7 段)折 3 行时尾段被卡片下沿裁掉(用户 2026-07-10 报障)。
                # item.setSizeHint(QSize(0, 96 if active else 90))
                self._list.addItem(item)
                self._list.setItemWidget(item, row)
                item.setSizeHint(QSize(0, self._row_height_for(row, active=active)))
                shown += 1

            self._update_filter_counts(shown)
            # 先解除抑制:下面的 setCurrentItem 要真的发 currentItemChanged,
            # 否则选中行的 selected 样式不会被应用(2026-07-10 回归)。
            self._list.blockSignals(False)
            if self._list.count() > 0 and self._list.currentItem() is None:
                self._list.setCurrentItem(self._list.item(0))
            self._sync_action_buttons()
        finally:
            self._list.blockSignals(False)
            self._list.setUpdatesEnabled(was_enabled)

    def _sorted_items(self, items: list[dict]) -> list[dict]:
        def date_key(value) -> str:
            return str(value or "")

        def progress_score(entry: dict) -> tuple[int, int, int, int]:
            p = entry.get("progress") or {}
            return (
                int(p.get("organized") or 0),
                int(p.get("composed") or 0),
                int(p.get("grouped") or 0),
                int(p.get("total") or 0),
            )

        def sort_key_for_specimen_entry(entry: dict):
            uid = str(entry.get("uid") or "")
            if self._sort_key == "collection_date":
                return (date_key(entry.get("collection_date")), uid)
            if self._sort_key == "photo_date":
                return (date_key(entry.get("photo_date")), uid)
            if self._sort_key == "rna":
                return (1 if entry.get("is_rna") else 0, uid)
            if self._sort_key == "attention":
                return (1 if entry.get("needs_attention") else 0, uid)
            if self._sort_key == "progress":
                return (progress_score(entry), uid)
            if self._sort_key == "created":
                return (int(entry.get("row_order") or 0), uid)
            return uid

        return sorted(list(items or []), key=sort_key_for_specimen_entry, reverse=self._sort_reverse)

    def _resolve_action_uid(self) -> Optional[str]:
        """UID for activate/deactivate: current row, or the sole visible row."""
        uid = self.current_uid()
        if uid:
            return uid
        if self._list.count() == 1:
            item = self._list.item(0)
            if item:
                picked = item.data(Qt.ItemDataRole.UserRole)
                if picked:
                    self._list.setCurrentItem(item)
                    return picked
        return None

    def _is_uid_active(self, uid: Optional[str]) -> bool:
        if not uid:
            return False
        return any(
            entry.get("uid") == uid and entry.get("active")
            for entry in self._all_items
        )

    def _sync_action_buttons(self) -> None:
        """Make bottom actions reflect the selected row's actual active state."""
        uid = self._resolve_action_uid()
        active = self._is_uid_active(uid)

        self._activate_btn.setEnabled(bool(uid) and not active)
        self._deactivate_btn.setEnabled(bool(uid) and active)

        if not uid:
            self._activate_btn.setText("激活")
            self._deactivate_btn.setText("去激活")
            self._activate_btn.setToolTip("请先选择一个编号")
            self._deactivate_btn.setToolTip("请先选择当前激活的编号")
            return

        if active:
            self._activate_btn.setText("已激活")
            self._deactivate_btn.setText("取消激活")
            self._activate_btn.setToolTip("该编号已经是当前激活编号")
            self._deactivate_btn.setToolTip("取消当前激活编号，开始新的未激活任务")
        else:
            self._activate_btn.setText("激活此编号")
            self._deactivate_btn.setText("未激活")
            self._activate_btn.setToolTip("把选中编号设为当前拍摄编号")
            self._deactivate_btn.setToolTip("选中编号没有激活，无需取消")

    def _build_row_widget(
        self,
        uid: str,
        name: str,
        name_cn: str,
        storage: str,
        svc,
        *,
        active: bool = False,
        is_rna: bool = False,
        progress: Optional[dict] = None,
    ) -> QWidget:
        """Build one specimen row: UID + name + collab badge + 4 phase dots."""
        row = QFrame()
        row.setObjectName("SpecimenRowActive" if active else "SpecimenRow")
        row.setProperty("selected", False)
        row.setMinimumHeight(88)
        _row_sp = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        _row_sp.setHeightForWidth(True)  # 让 heightForWidth 从 uid 标签透传上来
        row.setSizePolicy(_row_sp)
        v = QVBoxLayout(row)
        v.setContentsMargins(7, 5, 7, 5)
        v.setSpacing(2)

        uid_lbl = QLabel(self._display_uid(uid))
        uid_lbl.setObjectName("SpecimenUid")
        uid_lbl.setToolTip(uid)  # 折行后仍可悬停看完整编号
        uid_lbl.setMinimumWidth(0)
        uid_lbl.setWordWrap(True)
        # §7 旧: setSizePolicy(Expanding, Preferred) —— 未开 heightForWidth,
        # 布局不知道折行后需要多高, 配合写死的 90px 卡片高度 → 长编号被裁掉尾段。
        # uid_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        _uid_sp = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        _uid_sp.setHeightForWidth(True)
        uid_lbl.setSizePolicy(_uid_sp)
        uid_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        status_pill = QLabel("当前" if active else "未激活")
        status_pill.setObjectName(
            "SpecimenActivePill" if active else "SpecimenInactivePill"
        )
        status_pill.setToolTip("")
        status_pill.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        title_row.addWidget(uid_lbl, stretch=1)
        title_row.addWidget(status_pill)
        v.addLayout(title_row)

        name_text = name or name_cn
        nm = QLabel(name_text or "未填写物种信息")
        nm.setObjectName("SpecimenSubtext" if name_text else "SpecimenMissingText")
        nm.setToolTip("")
        nm.setWordWrap(False)
        v.addWidget(nm)

        # ── Phase dots ──
        current = self._phase_for(uid, svc)
        self._phase_state[uid] = current
        self._phase_dots[uid] = {}

        badge = self._collab_badge(uid, svc)
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(6)

        if is_rna:
            rna_badge = _ClickableLabel("RNA")
            rna_badge.setObjectName("SpecimenRnaBadge")
            rna_badge.setCursor(Qt.CursorShape.PointingHandCursor)
            rna_badge.setToolTip("点击筛选所有已取 RNA 组织的编号")
            rna_badge.clicked.connect(lambda: self._set_filter_mode("rna"))
            line.addWidget(rna_badge)
        # 保存方式段（如 T95E）已是 UID 的一部分，不再重复显示，避免冗余。

        progress_text = self._progress_badge_text(progress or {})
        if progress_text:
            progress_badge = QLabel(progress_text)
            progress_badge.setObjectName("SpecimenProgressBadge")
            progress_badge.setToolTip(self._progress_tooltip(progress or {}))
            progress_badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            line.addWidget(progress_badge)

        if badge:
            bd = QLabel(badge)
            bd.setObjectName("SpecimenBadge")
            line.addWidget(bd)
        line.addStretch(1)

        dots_row = QHBoxLayout()
        dots_row.setContentsMargins(0, 0, 0, 0)
        dots_row.setSpacing(6)
        for code, obj_name, tip in self._PHASE_DOTS:
            dot = QPushButton()
            dot.setObjectName(obj_name)
            # 强制正方固定尺寸 —— 仅靠 QSS max-width 拗不过按钮默认 padding，会被撑成
            # 矩形；setFixedSize 锁死，配 QSS border-radius=半径 → 真·小圆点。
            dot.setFixedSize(12, 12)
            dot.setCheckable(True)
            dot.setChecked(code == current)
            dot.setCursor(Qt.CursorShape.PointingHandCursor)
            dot.setToolTip(f"标记为「{tip}」")
            dot.clicked.connect(
                lambda _=False, u=uid, c=code: self._on_dot_clicked(u, c)
            )
            dots_row.addWidget(dot)
            self._phase_dots[uid][code] = dot
        line.addLayout(dots_row)

        print_btn = QPushButton()
        print_btn.setObjectName("SpecimenPrintButton")
        print_btn.setFixedSize(20, 20)
        print_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        print_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        print_btn.setToolTip("按默认模板打印该编号标签")
        icons.set_button_icon(print_btn, "mdi6.printer-outline", color=icons.TONE_ACCENT, size=13)
        print_btn.clicked.connect(
            lambda _=False, u=uid: self.print_labels_requested.emit(u)
        )
        line.addWidget(print_btn)

        v.addLayout(line)
        return row

    @staticmethod
    def _format_angle_count(count: int, total: int) -> str:
        if count <= 0:
            return ""
        if total > count:
            return f"{count}/{total}"
        return str(count)

    def _progress_badge_text(self, progress: dict) -> str:
        total = int(progress.get("total") or 0)
        organized = int(progress.get("organized") or 0)
        composed = int(progress.get("composed") or 0)
        grouped = int(progress.get("grouped") or 0)
        if organized:
            return f"整 {organized}"
        if composed:
            return f"合 {self._format_angle_count(composed, total)}"
        if grouped:
            return f"组 {self._format_angle_count(grouped, total)}"
        return ""

    @staticmethod
    def _progress_tooltip(progress: dict) -> str:
        total = int(progress.get("total") or 0)
        grouped = int(progress.get("grouped") or 0)
        composed = int(progress.get("composed") or 0)
        organized = int(progress.get("organized") or 0)
        return (
            f"整理进度：共 {total} 组；"
            f"已分组 {grouped}，已合成 {composed}，已整理 {organized}"
        )

    def _update_filter_counts(self, shown: Optional[int] = None) -> None:
        total = len(self._all_items)
        rna_total = sum(1 for item in self._all_items if item.get("is_rna"))
        attention_total = sum(
            1 for item in self._all_items if item.get("needs_attention")
        )
        query = self._search.text().strip()
        narrowed = (
            shown is not None
            and shown != total
            and (bool(query) or self._filter_mode in ("rna", "attention"))
        )
        if narrowed:
            self._filter_all_btn.setText(f"全部 {shown}/{total}")
        else:
            self._filter_all_btn.setText(f"全部 {total}")
        if self._filter_mode == "rna" and shown is not None:
            self._filter_rna_btn.setText(f"RNA编号 {shown}")
        else:
            self._filter_rna_btn.setText(f"RNA编号 {rna_total}")
        self._filter_rna_btn.setEnabled(rna_total > 0)
        if self._filter_mode == "attention" and shown is not None:
            self._filter_attention_btn.setText(f"资料待补 {shown}")
        else:
            self._filter_attention_btn.setText(f"资料待补 {attention_total}")
        self._filter_attention_btn.setEnabled(attention_total > 0)

    def _row_height_for(self, row, *, active: bool) -> int:
        """卡片高度 = max(基准高, 内容在当前侧栏宽度下真正需要的高度)。

        侧栏很窄, ``_display_uid`` 强制折一行后 wordWrap 还可能再折 ——
        写死高度必然裁字。宽度取 viewport(尚未布局时回退到控件宽)。
        """
        base = 96 if active else 90
        width = self._list.viewport().width()
        if width <= 8:
            width = max(1, self._list.width() - 8)
        need = row.heightForWidth(width) if row.hasHeightForWidth() else -1
        if need <= 0:
            need = row.sizeHint().height()
        return max(base, int(need) + 4)  # +4 呼吸位, 防贴边

    def _resync_row_heights(self) -> None:
        """侧栏宽度变化会改变折行数 → 重算每行高度(纯几何, 不重建 widget)。"""
        for i in range(self._list.count()):
            item = self._list.item(i)
            row = self._list.itemWidget(item)
            if row is None:
                continue
            active = bool(item.data(_ACTIVE_ROLE))
            h = self._row_height_for(row, active=active)
            if item.sizeHint().height() != h:
                item.setSizeHint(QSize(0, h))

    def resizeEvent(self, event) -> None:  # noqa: D401 - Qt override
        super().resizeEvent(event)
        self._maybe_resync_row_heights()

    def showEvent(self, event) -> None:  # noqa: D401 - Qt override
        # refresh() 可能发生在首次布局之前(viewport 宽度还是默认值),
        # 那时算出的行高偏小 —— 首次显示时按真实宽度重算一次。
        super().showEvent(event)
        self._maybe_resync_row_heights()

    def _maybe_resync_row_heights(self) -> None:
        new_w = self._list.viewport().width()
        if new_w != getattr(self, "_last_list_width", -1):
            self._last_list_width = new_w
            self._resync_row_heights()

    @staticmethod
    def _display_uid(uid: str) -> str:
        """Readable sidebar UID: keep the full value, wrap long IDs by segment."""
        text = str(uid or "").strip()
        if len(text) <= 30 or "-" not in text:
            return text
        parts = text.split("-")
        if len(parts) < 5:
            return text
        head = "-".join(parts[:3])
        tail = "-".join(parts[3:])
        return f"{head}-\n{tail}"

    def _normalize_project_specimen_uids(self, db, project_dir: str) -> None:
        """Migrate old lowercase UID rows to the canonical uppercase spelling.

        If the uppercase UID already exists, leave the old row untouched to avoid
        accidentally merging two records.
        """
        if not project_dir:
            return
        project_dirs = equivalent_paths(project_dir)
        project_key = "|".join(project_dirs or [project_dir])
        if project_key in self._uid_normalized_projects:
            return
        self._uid_normalized_projects.add(project_key)
        try:
            from app.services.project_settings_service import (
                DEFAULT_NAMING_RULES,
                load_setting,
            )
            rules = load_setting(db, "naming_rules", DEFAULT_NAMING_RULES)
            naming_components = rules.get(
                "components",
                DEFAULT_NAMING_RULES["components"],
            )
        except Exception:
            naming_components = []
        try:
            owner_filter = ",".join("?" * len(project_dirs)) or "?"
            rows = db.execute(
                f"""
                SELECT uid, province, site, station, id, storage,
                       collection_date, photo_date, raw_json
                FROM specimens
                WHERE owner_project_dir IN ({owner_filter})
                  AND (uid GLOB '*[a-z]*' OR LENGTH(uid) < 12)
                LIMIT 500
                """,
                project_dirs or [project_dir],
            ).fetchall()
        except Exception:
            return

        for row in rows:
            old_uid = row["uid"] if hasattr(row, "keys") else row[0]
            new_uid = normalize_uid(old_uid)
            if not old_uid or not new_uid:
                continue
            raw_text = row["raw_json"] if hasattr(row, "keys") else row[8]
            try:
                raw = json.loads(raw_text or "{}")
            except Exception:
                raw = {}
            if not isinstance(raw, dict):
                raw = {}

            def _value(key: str, index: int, *raw_keys: str) -> str:
                value = row[key] if hasattr(row, "keys") else row[index]
                if value:
                    return str(value)
                for raw_key in raw_keys:
                    raw_value = raw.get(raw_key)
                    if raw_value:
                        return str(raw_value)
                return ""

            configured_values = dict(raw)
            configured_values.update({
                "province": _value("province", 1, "province"),
                "site": _value("site", 2, "site"),
                "station": _value("station", 3, "station"),
                "id": _value("id", 4, "id", "speciesId", "species_id"),
                "storage": _value("storage", 5, "storage"),
                "collectionDate": _value(
                    "collection_date", 6, "collectionDate", "collection_date"
                ),
                "photoDate": _value("photo_date", 7, "photoDate", "photo_date"),
            })
            try:
                configured_uid = build_configured_uid(
                    naming_components,
                    component_values_from_specimen(configured_values),
                )
            except Exception:
                configured_uid = ""
            configured_ok = bool(
                configured_uid and normalize_uid(configured_uid) == new_uid
            )
            if configured_ok:
                new_uid = configured_uid
            elif not parse_uid(new_uid):
                candidate = derive_uid({
                    "province": _value("province", 1, "province"),
                    "site": _value("site", 2, "site"),
                    "station": _value("station", 3, "station"),
                    "id": _value("id", 4, "id", "speciesId", "species_id"),
                    "storage": _value("storage", 5, "storage"),
                    "collectionDate": _value(
                        "collection_date", 6, "collectionDate", "collection_date"
                    ),
                    "photoDate": _value("photo_date", 7, "photoDate", "photo_date"),
                })
                if parse_uid(candidate):
                    new_uid = candidate
                elif new_uid == old_uid:
                    continue
            if old_uid == new_uid:
                continue
            try:
                if db.execute(
                    "SELECT 1 FROM specimens WHERE uid = ?",
                    (new_uid,),
                ).fetchone():
                    continue
                if isinstance(raw, dict):
                    for key in (
                        "uid", "uniqueId", "province", "site", "station",
                        "id", "storage", "collectionDate", "photoDate",
                    ):
                        if isinstance(raw.get(key), str):
                            raw[key] = raw[key].upper()
                    raw["uid"] = new_uid
                    raw["uniqueId"] = new_uid
                else:
                    raw = {}

                from app.services.specimen_rename_service import migrate_uid_references
                with db:
                    db.execute(
                        """
                        UPDATE specimens
                        SET uid = ?,
                            province = UPPER(COALESCE(province, '')),
                            site = UPPER(COALESCE(site, '')),
                            station = UPPER(COALESCE(station, '')),
                            id = UPPER(COALESCE(id, '')),
                            storage = UPPER(COALESCE(storage, '')),
                            raw_json = ?
                        WHERE uid = ?
                        """,
                        (new_uid, json.dumps(raw, ensure_ascii=False), old_uid),
                    )
                    migrate_uid_references(db, old_uid, new_uid)
            except Exception:
                continue

    def selected_uids(self) -> list[str]:
        """当前多选中的编号(按列表顺序);单选时返回 1 个,无选中返回 []。"""
        out: list[str] = []
        for item in self._list.selectedItems():
            uid = item.data(Qt.ItemDataRole.UserRole)
            if uid:
                out.append(str(uid))
        order = {
            self._list.item(i).data(Qt.ItemDataRole.UserRole): i
            for i in range(self._list.count())
        }
        return sorted(out, key=lambda u: order.get(u, 0))

    def _on_selection_scope_changed(self) -> None:
        self.selection_scope_changed.emit(self.selected_uids())

    def _prefetch_phases(self, uids: list[str]) -> None:
        """一次批量取回全部编号的 DB 相位, 供 `_phase_for` 命中(消灭 N+1)。"""
        self._phase_db_cache = {}
        try:
            from app.services.activation_service import get_collab_statuses
            self._phase_db_cache = get_collab_statuses(self.ctx.get_db(), uids)
        except Exception:
            self._phase_db_cache = {}
        self._phase_cache_primed = True

    def _phase_for(self, uid: str, svc) -> Optional[str]:
        """Resolve *uid*'s confirmed phase (collab task first, else project DB)."""
        # 在线协作任务优先(内存 store, 免查库) —— 与 resolve_phase 语义一致。
        try:
            task = svc.store.get_task(uid) if svc is not None else None
            if task is not None:
                status = task.status
                return status.value if hasattr(status, "value") else str(status)
        except Exception:
            pass
        # §7 旧: return resolve_phase(svc, self.ctx.get_db(), uid) —— 其 DB 分支
        # 对每一行发一条 SELECT(N+1), 编号一多「返回工作台/激活」就发涩。
        if getattr(self, "_phase_cache_primed", False):
            return self._phase_db_cache.get(uid)
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
        # 外部刚写过库(如相位点击已持久化) → 必须重新批量取, 否则读到陈旧缓存。
        self._prefetch_phases(list(self._phase_dots.keys()))
        for uid in list(self._phase_dots.keys()):
            current = self._phase_for(uid, svc)
            self._phase_state[uid] = current
            self._sync_row_dots(uid, current)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_search(self, text: str) -> None:
        self._apply_filter(text)

    def _show_sort_menu(self) -> None:
        menu = QMenu(self)
        options = [
            ("uid", "编号 A-Z", False),
            ("created", "建号顺序 新→旧", True),
            ("collection_date", "采集日期 新→旧", True),
            ("collection_date", "采集日期 旧→新", False),
            ("photo_date", "拍照日期 新→旧", True),
            ("photo_date", "拍照日期 旧→新", False),
            ("rna", "RNA 优先", True),
            ("attention", "资料待补优先", True),
            ("progress", "整理进度优先", True),
        ]
        for key, label, reverse in options:
            checked = self._sort_key == key and self._sort_reverse == reverse
            act = menu.addAction(("✓ " if checked else "") + label)
            act.triggered.connect(
                lambda _=False, k=key, r=reverse, text=label: self._set_sort(k, r, text)
            )
        menu.exec(self._sort_btn.mapToGlobal(self._sort_btn.rect().bottomLeft()))

    def _set_sort(self, key: str, reverse: bool, label: str = "") -> None:
        self._sort_key = key
        self._sort_reverse = bool(reverse)
        self._sort_btn.setText(label.split()[0] if label else "排序")
        self._apply_filter(self._search.text())

    def _set_filter_mode(self, mode: str) -> None:
        self._filter_mode = mode if mode in ("rna", "attention") else "all"
        self._filter_all_btn.setChecked(self._filter_mode == "all")
        self._filter_rna_btn.setChecked(self._filter_mode == "rna")
        self._filter_attention_btn.setChecked(self._filter_mode == "attention")
        self._apply_filter(self._search.text())
        if self._filter_mode == "all":
            self.show_all_requested.emit()

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        uid = item.data(Qt.ItemDataRole.UserRole)
        if uid:
            self.specimen_selected.emit(uid)

    def _on_current_item_changed(
        self,
        current: Optional[QListWidgetItem],
        previous: Optional[QListWidgetItem],
    ) -> None:
        """Make selection visible on the embedded card, not the transparent list item."""
        for item, selected in ((previous, False), (current, True)):
            if item is None:
                continue
            row = self._list.itemWidget(item)
            if row is None:
                continue
            row.setProperty("selected", selected)
            style = row.style()
            style.unpolish(row)
            style.polish(row)
            row.update()
        self._sync_action_buttons()

    def _on_activate_clicked(self) -> None:
        """Emit activate_requested for the currently selected specimen."""
        uid = self._resolve_action_uid()
        if uid:
            self.activate_requested.emit(uid)
        else:
            self.activate_no_selection.emit()

    def _on_deactivate_clicked(self) -> None:
        """Emit deactivate_requested for the currently selected specimen."""
        uid = self._resolve_action_uid()
        if uid:
            self.deactivate_requested.emit(uid)
        else:
            self.activate_no_selection.emit()

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
        print_act = menu.addAction("打印默认标签")
        menu.addSeparator()
        edit_act = menu.addAction("在右侧编辑…")
        menu.addSeparator()
        activate_act = menu.addAction("激活")
        deactivate_act = menu.addAction("去激活")
        menu.addSeparator()
        delete_act = menu.addAction("删除编号…")

        chosen = menu.exec(self._list.viewport().mapToGlobal(pos))
        if chosen == copy_act:
            QApplication.clipboard().setText(uid)
        elif chosen == print_act:
            self.print_labels_requested.emit(uid)
        elif chosen == edit_act:
            self.edit_current_specimen()
        elif chosen == activate_act:
            self.activate_requested.emit(uid)
        elif chosen == deactivate_act:
            self.deactivate_requested.emit(uid)
        elif chosen == delete_act:
            self.delete_specimen_requested.emit(uid)

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

    def edit_current_specimen(self) -> bool:
        """Request loading the selected specimen into the right-rail editor."""
        uid = self.current_uid()
        if not uid:
            return False
        self.edit_specimen_requested.emit(uid)
        return True

    # ── Collab badge helpers ──────────────────────────────────────────────────

    @staticmethod
    def _collab_badge(uid: str, svc) -> str:
        """Return a short collab status badge string for *uid*, or "" if none."""
        if svc is None or not svc.is_running():
            return ""
        task = svc.store.get_task(uid)
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
            self._collab_addr.setText("连接地址: —")
            self._collab_device.setText("匿名·本机")
            self._collab_members.setText("成员: 0")
            self._collab_sync.setText("同步编号: 协作服务未启动")
            self._collab_sync_selected_btn.setEnabled(False)
            self._collab_sync_project_btn.setEnabled(False)
            return

        addr = service.local_address()
        self._collab_addr.setText(f"连接地址: {addr}")

        peers = service.peers()
        n_peers = len(peers)
        self._collab_members.setText(f"成员: {n_peers}")

        import socket as _socket
        hostname = _socket.gethostname()
        self._collab_device.setText(f"{hostname}")

        task_count = len(service.store.list_tasks())
        local_project_id = str(getattr(service, "project_id", "") or "")
        same_project_count = sum(
            1 for peer in peers
            if getattr(peer, "group_code", "") == service.group_code
            and str(getattr(peer, "project_id", "") or "") == local_project_id
        ) if local_project_id else 0
        from app.services.collab_status import build_collab_status
        status = build_collab_status(service, peers)
        self._collab_sync.setText(
            f"同步编号: {task_count} 条 · {status.plain_status} · 可同步设备: {same_project_count}"
        )
        can_file_sync = bool(
            service.is_running()
            and service.group_code
            and same_project_count
        )
        self._collab_sync_selected_btn.setEnabled(can_file_sync)
        self._collab_sync_project_btn.setEnabled(can_file_sync)

        # Refresh badges in the specimen list
        self._refresh_collab_badges()
