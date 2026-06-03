"""worms_view.py — WoRMS (World Register of Marine Species) verification view.

Provides:
  - Search box: enter a scientific name → fetch WoRMS matches via QThread
  - Result list: shows AphiaID, valid name, rank, status (accepted / synonym)
  - Classification chain panel: shows the full taxonomic path
  - Synonyms sub-panel: lists known synonyms for the selected taxon
  - Batch job panel: create / list / control bulk-validation jobs

All network I/O runs on a QThread; the main UI thread is never blocked.

Oracle:
  server.js worms endpoints ~1798–2188
  app.js worms render ~10790
  docs/modules/worms.md
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QObject, QTimer,
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QListWidget, QListWidgetItem, QSplitter,
    QGroupBox, QTextEdit, QProgressBar, QComboBox,
    QScrollArea, QFrame, QSizePolicy, QAbstractItemView,
    QMessageBox,
)
from PyQt6.QtGui import QFont, QColor

from app.views.base_view import BaseView
from app.services.worms_service import WormsService

if TYPE_CHECKING:
    from app.app_context import AppContext

# ── Default cache / jobs paths (relative to user data dir) ────────────────────
_FALLBACK_DATA_DIR = Path.home() / ".photo_workbench" / "data"


def _default_cache_path() -> str:
    return str(_FALLBACK_DATA_DIR / "worms_cache.json")


def _default_jobs_path() -> str:
    return str(_FALLBACK_DATA_DIR / "worms_jobs.json")


# ── Worker: search ────────────────────────────────────────────────────────────

class _SearchWorker(QObject):
    """Run WormsService.search() on a background thread."""

    finished = pyqtSignal(list)            # list of AphiaRecord dicts
    error    = pyqtSignal(str)             # error message

    def __init__(self, service: WormsService, name: str, like: bool) -> None:
        super().__init__()
        self._service = service
        self._name = name
        self._like = like

    def run(self) -> None:
        try:
            results = self._service.search(self._name, like=self._like)
            self.finished.emit(results)
        except Exception as exc:
            self.error.emit(str(exc))


# ── Worker: classification + synonyms ────────────────────────────────────────

class _DetailWorker(QObject):
    """Fetch classification chain + synonyms for a given AphiaID."""

    finished = pyqtSignal(dict)    # {"chain": [...], "synonyms": [...]}
    error    = pyqtSignal(str)

    def __init__(self, service: WormsService, aphia_id: int) -> None:
        super().__init__()
        self._service = service
        self._aphia_id = aphia_id

    def run(self) -> None:
        try:
            raw_chain = self._service.classification(self._aphia_id)
            chain     = self._service.flatten_classification(raw_chain)
            synonyms  = self._service.synonyms(self._aphia_id)
            self.finished.emit({"chain": chain, "synonyms": synonyms})
        except Exception as exc:
            self.error.emit(str(exc))


# ── Main view ─────────────────────────────────────────────────────────────────

class WormsView(BaseView):
    """WoRMS species-validation module view.

    Layout
    ------
    ┌──────────────────────────────────────────────────────┐
    │  🌊  WoRMS                    [search box] [Search]  │
    ├────────────────────────┬─────────────────────────────┤
    │ Results list           │ Classification chain        │
    │ (left panel)           │ Synonyms                    │
    │                        │                             │
    ├────────────────────────┴─────────────────────────────┤
    │ Batch jobs panel                                     │
    └──────────────────────────────────────────────────────┘
    """

    view_id   = "worms"
    nav_title = "WoRMS"
    nav_icon  = "🌊"

    def __init__(self, ctx: "AppContext") -> None:
        # Service is initialised before _setup_ui (called from super().__init__)
        self._service: Optional[WormsService] = None
        self._search_thread:  Optional[QThread] = None
        self._detail_thread:  Optional[QThread] = None
        self._search_worker:  Optional[_SearchWorker] = None
        self._detail_worker:  Optional[_DetailWorker] = None
        self._current_results: list[dict] = []
        self._selected_record: Optional[dict] = None
        super().__init__(ctx)

    # ── Service initialisation ────────────────────────────────────────────

    def _init_service(self) -> WormsService:
        """Create WormsService with paths derived from ctx / fallback."""
        project_dir = getattr(self.ctx, "current_project_dir", None)
        if project_dir:
            data_dir = Path(project_dir) / "_data"
        else:
            data_dir = _FALLBACK_DATA_DIR
        data_dir.mkdir(parents=True, exist_ok=True)
        cache_path = str(data_dir / "worms_cache.json")
        jobs_path  = str(data_dir / "worms_jobs.json")
        return WormsService(cache_path=cache_path, jobs_path=jobs_path)

    # ── UI construction ───────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        """Build the full widget hierarchy."""
        self._service = self._init_service()

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # ── Header row ────────────────────────────────────────────────────
        header_row = QHBoxLayout()
        header_row.setSpacing(8)

        title = QLabel("🌊  WoRMS 物种验证")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        header_row.addWidget(title)
        header_row.addStretch()

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("输入学名（如 Acanthurus olivaceus）")
        self._search_box.setMinimumWidth(300)
        self._search_box.returnPressed.connect(self._on_search)
        header_row.addWidget(self._search_box)

        self._search_btn = QPushButton("搜索")
        self._search_btn.setFixedWidth(72)
        self._search_btn.clicked.connect(self._on_search)
        header_row.addWidget(self._search_btn)

        self._match_mode = QComboBox()
        self._match_mode.addItems(["模糊匹配", "精确匹配"])
        self._match_mode.setFixedWidth(96)
        self._match_mode.setToolTip("like=true → 模糊；like=false → 精确")
        header_row.addWidget(self._match_mode)

        root.addLayout(header_row)

        # ── Status / progress ─────────────────────────────────────────────
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #8888aa; font-size: 12px;")
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)   # indeterminate
        self._progress.setFixedHeight(4)
        self._progress.setVisible(False)
        root.addWidget(self._status_label)
        root.addWidget(self._progress)

        # ── Main horizontal splitter ──────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(4)
        root.addWidget(splitter, stretch=1)

        # Left: results list
        left_panel = self._build_results_panel()
        splitter.addWidget(left_panel)

        # Right: detail (chain + synonyms)
        right_panel = self._build_detail_panel()
        splitter.addWidget(right_panel)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        # ── Batch jobs panel ──────────────────────────────────────────────
        jobs_panel = self._build_jobs_panel()
        root.addWidget(jobs_panel)

    def _build_results_panel(self) -> QWidget:
        """Left panel: search results list."""
        box = QGroupBox("搜索结果")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(6, 8, 6, 6)

        self._result_list = QListWidget()
        self._result_list.setAlternatingRowColors(True)
        self._result_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._result_list.currentItemChanged.connect(self._on_result_selected)
        layout.addWidget(self._result_list)

        self._result_count_label = QLabel("")
        self._result_count_label.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self._result_count_label)
        return box

    def _build_detail_panel(self) -> QWidget:
        """Right panel: classification chain + synonyms."""
        box = QGroupBox("详情")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(6, 8, 6, 6)
        layout.setSpacing(6)

        # Selected record summary
        self._detail_name_label = QLabel("（未选择）")
        self._detail_name_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        self._detail_name_label.setWordWrap(True)
        layout.addWidget(self._detail_name_label)

        self._detail_meta_label = QLabel("")
        self._detail_meta_label.setStyleSheet("color: #888; font-size: 11px;")
        self._detail_meta_label.setWordWrap(True)
        layout.addWidget(self._detail_meta_label)

        # Classification chain
        chain_label = QLabel("分类链")
        chain_label.setStyleSheet("font-weight: bold; margin-top: 6px;")
        layout.addWidget(chain_label)

        self._chain_text = QTextEdit()
        self._chain_text.setReadOnly(True)
        self._chain_text.setFixedHeight(140)
        self._chain_text.setStyleSheet("background: #1a1a2e; border-radius: 4px; font-size: 12px;")
        layout.addWidget(self._chain_text)

        # Synonyms
        syn_label = QLabel("同物异名")
        syn_label.setStyleSheet("font-weight: bold; margin-top: 4px;")
        layout.addWidget(syn_label)

        self._syn_list = QListWidget()
        self._syn_list.setFixedHeight(110)
        self._syn_list.setStyleSheet("font-size: 12px;")
        layout.addWidget(self._syn_list)

        layout.addStretch()
        return box

    def _build_jobs_panel(self) -> QWidget:
        """Bottom panel: batch validation jobs."""
        box = QGroupBox("批量验证任务")
        box.setMaximumHeight(180)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(6, 8, 6, 6)
        layout.setSpacing(6)

        controls = QHBoxLayout()
        self._job_ids_input = QLineEdit()
        self._job_ids_input.setPlaceholderText("留空=全部未处理；或逗号分隔 record_id")
        controls.addWidget(self._job_ids_input, stretch=1)

        self._create_job_btn = QPushButton("创建任务")
        self._create_job_btn.setFixedWidth(88)
        self._create_job_btn.clicked.connect(self._on_create_job)
        controls.addWidget(self._create_job_btn)

        self._refresh_jobs_btn = QPushButton("刷新")
        self._refresh_jobs_btn.setFixedWidth(60)
        self._refresh_jobs_btn.clicked.connect(self._refresh_jobs)
        controls.addWidget(self._refresh_jobs_btn)
        layout.addLayout(controls)

        self._jobs_list = QListWidget()
        self._jobs_list.setFixedHeight(90)
        self._jobs_list.setStyleSheet("font-size: 12px;")
        layout.addWidget(self._jobs_list)
        return box

    # ── BaseView contract ─────────────────────────────────────────────────

    def on_activate(self) -> None:
        """Called every time user navigates to this view.

        Re-initialises the service path (in case the project changed)
        and refreshes the jobs list.
        """
        self._service = self._init_service()
        self._refresh_jobs()

    # ── Search logic ──────────────────────────────────────────────────────

    def _on_search(self) -> None:
        """Triggered by Search button or Return key in the search box."""
        name = self._search_box.text().strip()
        if not name:
            self._set_status("请输入学名")
            return
        if self._search_thread and self._search_thread.isRunning():
            return  # debounce

        like = (self._match_mode.currentIndex() == 0)  # 0=模糊, 1=精确
        self._set_busy(True, f'搜索 “{name}”…')
        self._result_list.clear()
        self._current_results = []
        self._clear_detail()

        worker = _SearchWorker(self._service, name, like)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_search_done)
        worker.error.connect(self._on_search_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._search_worker = worker
        self._search_thread = thread
        thread.start()

    def _on_search_done(self, results: list[dict]) -> None:
        self._set_busy(False)
        self._current_results = results
        self._result_list.clear()
        if not results:
            self._set_status("未找到匹配结果")
            self._result_count_label.setText("0 条结果")
            return
        for rec in results:
            label = self._format_result_item(rec)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, rec)
            # Colour-code by status
            status = (rec.get("status") or "").lower()
            if status == "accepted":
                item.setForeground(QColor("#6dde91"))
            elif status == "synonym":
                item.setForeground(QColor("#e0a050"))
            else:
                item.setForeground(QColor("#aaaacc"))
            self._result_list.addItem(item)
        self._set_status(f"找到 {len(results)} 条结果")
        self._result_count_label.setText(f"{len(results)} 条结果")

    def _on_search_error(self, msg: str) -> None:
        self._set_busy(False)
        self._set_status(f"搜索失败: {msg}")

    @staticmethod
    def _format_result_item(rec: dict) -> str:
        aphia   = rec.get("AphiaID", "?")
        name    = rec.get("scientificname") or rec.get("valid_name") or "—"
        rank    = rec.get("rank") or ""
        status  = rec.get("status") or ""
        auth    = rec.get("authority") or ""
        parts = [f"[{aphia}]  {name}"]
        if auth:
            parts.append(auth)
        tags = []
        if rank:
            tags.append(rank)
        if status:
            tags.append(status)
        if tags:
            parts.append("·".join(tags))
        return "   ".join(parts)

    # ── Result selection → detail fetch ───────────────────────────────────

    def _on_result_selected(
        self,
        current: Optional[QListWidgetItem],
        _previous: Optional[QListWidgetItem],
    ) -> None:
        if current is None:
            self._clear_detail()
            return
        rec = current.data(Qt.ItemDataRole.UserRole)
        if not rec:
            return
        self._selected_record = rec
        valid_id = rec.get("valid_AphiaID") or rec.get("AphiaID")
        if not valid_id:
            self._clear_detail()
            return

        # Show summary immediately (chain/synonyms fetched async)
        self._detail_name_label.setText(
            rec.get("scientificname") or rec.get("valid_name") or "—"
        )
        meta_parts = []
        if rec.get("valid_name") and rec.get("valid_name") != rec.get("scientificname"):
            meta_parts.append(f"接受名: {rec['valid_name']}")
        if rec.get("authority"):
            meta_parts.append(rec["authority"])
        if rec.get("rank"):
            meta_parts.append(f"阶元: {rec['rank']}")
        if rec.get("status"):
            meta_parts.append(f"状态: {rec['status']}")
        self._detail_meta_label.setText("   ".join(meta_parts))
        self._chain_text.setPlainText("加载中…")
        self._syn_list.clear()

        if self._detail_thread and self._detail_thread.isRunning():
            self._detail_thread.quit()
            self._detail_thread.wait(300)

        worker = _DetailWorker(self._service, int(valid_id))
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_detail_done)
        worker.error.connect(self._on_detail_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._detail_worker = worker
        self._detail_thread = thread
        thread.start()

    def _on_detail_done(self, data: dict) -> None:
        chain    = data.get("chain", [])
        synonyms = data.get("synonyms", [])

        # Render classification chain
        if chain:
            lines = []
            indent = 0
            for node in chain:
                rank = node.get("rank", "")
                sci  = node.get("scientificname", "")
                lines.append("  " * indent + f"{rank}: {sci}")
                indent += 1
            self._chain_text.setPlainText("\n".join(lines))
        else:
            self._chain_text.setPlainText("（无分类链数据）")

        # Render synonyms
        self._syn_list.clear()
        if synonyms:
            for s in synonyms:
                sname   = s.get("scientificname") or s.get("valid_name") or "—"
                sauth   = s.get("authority") or ""
                saphia  = s.get("AphiaID", "?")
                item = QListWidgetItem(f"[{saphia}]  {sname}  {sauth}")
                item.setForeground(QColor("#cc9944"))
                self._syn_list.addItem(item)
        else:
            item = QListWidgetItem("（无同物异名）")
            item.setForeground(QColor("#666688"))
            self._syn_list.addItem(item)

    def _on_detail_error(self, msg: str) -> None:
        self._chain_text.setPlainText(f"加载失败: {msg}")

    def _clear_detail(self) -> None:
        self._detail_name_label.setText("（未选择）")
        self._detail_meta_label.setText("")
        self._chain_text.clear()
        self._syn_list.clear()
        self._selected_record = None

    # ── Batch jobs ────────────────────────────────────────────────────────

    def _on_create_job(self) -> None:
        raw = self._job_ids_input.text().strip()
        if raw:
            record_ids = [r.strip() for r in raw.split(",") if r.strip()]
        else:
            # Empty = all unprocessed; we leave record_ids empty and show a
            # note — in a real integration the caller supplies IDs from the
            # taxonomy view.  This placeholder creates a 0-record job so the
            # UI flow is testable without taxonomy data.
            QMessageBox.information(
                self,
                "批量任务",
                '请从"分类输入"模块选择要验证的物种后，再创建批量任务。\n'
                "或在上方文本框中输入逗号分隔的 record_id。",
            )
            return
        try:
            job = self._service.create_job(record_ids, source="selected")
            self._set_status(f"任务已创建: {job.id[:8]}…（{len(record_ids)} 条）")
            self._refresh_jobs()
        except Exception as exc:
            self._set_status(f"创建任务失败: {exc}")

    def _refresh_jobs(self) -> None:
        if self._service is None:
            return
        jobs = self._service.list_jobs()
        self._jobs_list.clear()
        if not jobs:
            item = QListWidgetItem("（暂无任务）")
            item.setForeground(QColor("#666688"))
            self._jobs_list.addItem(item)
            return
        for j in jobs[:20]:   # show at most 20 most recent
            jid    = j.get("id", "?")[:8]
            status = j.get("status", "?")
            cursor = j.get("cursor", 0)
            total  = len(j.get("record_ids", []))
            ts     = (j.get("created_at") or "")[:10]
            counts = j.get("counts", {})
            summary = "  ".join(f"{k}:{v}" for k, v in counts.items() if v)
            label = f"[{ts}]  {jid}…  {status}  {cursor}/{total}"
            if summary:
                label += f"  ({summary})"
            item = QListWidgetItem(label)
            if status == "completed":
                item.setForeground(QColor("#6dde91"))
            elif status == "running":
                item.setForeground(QColor("#6699ff"))
            elif status in ("paused", "cancelled"):
                item.setForeground(QColor("#cc8844"))
            self._jobs_list.addItem(item)

    # ── UI state helpers ──────────────────────────────────────────────────

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._progress.setVisible(busy)
        self._search_btn.setEnabled(not busy)
        if message:
            self._set_status(message)

    def _set_status(self, message: str) -> None:
        self._status_label.setText(message)
