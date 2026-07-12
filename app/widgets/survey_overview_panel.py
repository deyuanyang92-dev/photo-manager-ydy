"""survey_overview_panel.py — 项目树右栏「调查概览」(系统汇总).

默认只显示 KPI 数字; 地图 / 分布 / 物种名录点击展开, 避免右栏信息堆砌。

线程模型 (v0.60 卡顿修复):
    ``aggregate_survey_overview`` 会逐库全表 SELECT specimens、再扫一遍物种名录、
    再对每个 workspace 的 results/ 做 iterdir + stat —— 20 断面 × 800 编号在网络盘/
    drvfs 上要数十秒。它过去跑在 **Qt 主线程**(每次进页 + 每次点选断面都跑) →
    Windows 直接判「未响应」。现在整段聚合搬进 ``_SurveyOverviewWorker(QThread)``,
    worker 只 emit 纯 python dict/list, 填控件一律回主线程槽 (AutoConnection ⇒
    队列投递)。token 防过期 + 退休名单的处置照抄 ``project_tree_view`` 里已验证的
    ``SummaryQueryWorker`` 模式, 防止「视图销毁连带销毁运行中线程 → 原生 abort」。
"""
from __future__ import annotations

from typing import Any, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.services.survey_overview_service import aggregate_survey_overview
from app.services.taxon_inventory_service import aggregate_taxon_inventory
from app.widgets.survey_overview_mini_map import SurveyOverviewMiniMap
from app.widgets.survey_summary_panel import SurveySummaryPanel

# 仍在跑的聚合线程: 模块级强引用, 保证 QThread 对象不会在运行中被 GC/析构
# (destroy-while-running 会 abort 整个进程 —— 与 _RETIRED_WARMUP_WORKERS 同因)。
# worker 一律 **不挂 Qt 父对象**: 本面板是 QStackedWidget 的子控件, 没有 closeEvent
# 兜底, 挂父后随父析构就是 abort 现场。
_LIVE_OVERVIEW_WORKERS: list = []

# KPI 加载态占位符（聚合在工作线程跑, 结果到达前显示这个, 而不是让 UI 冻住）。
_LOADING = "…"


def _reap_overview_workers() -> None:
    """收割已结束的聚合线程; 仍在跑的继续持有引用。"""
    still: list = []
    for w in _LIVE_OVERVIEW_WORKERS:
        try:
            if w.isRunning():
                still.append(w)
        except Exception:  # pragma: no cover - 判定失败的对象直接丢弃
            pass
    _LIVE_OVERVIEW_WORKERS[:] = still


class _SurveyOverviewWorker(QThread):
    """跨 workspace 概览聚合 + 物种名录聚合 (全部只读 sqlite / 文件 stat)。

    run() 里只碰纯 python 数据: **禁止**构造任何 QWidget / QPixmap / QTimer。
    结果经 signal 回主线程再填控件。
    """

    finished_result = pyqtSignal(object, object)  # (overview: dict|None, inventory: list)
    failed = pyqtSignal(str)

    def __init__(
        self,
        workspaces: list[str],
        label_list: Optional[list[str]],
        *,
        with_overview: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._workspaces = list(workspaces or [])
        self._label_list = list(label_list) if label_list else None
        self._with_overview = bool(with_overview)
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:  # noqa: D401 - Qt override
        try:
            overview = None
            if self._with_overview:
                # 模块级名字, 保证测试的 monkeypatch 生效。
                overview = aggregate_survey_overview(
                    self._workspaces, labels=self._label_list
                )
            if self._cancel_requested:
                return
            inventory = aggregate_taxon_inventory(
                self._workspaces, labels=self._label_list
            )
        except Exception as exc:  # 坏库/锁库也要给反馈, 不能静默假死
            if not self._cancel_requested:
                self.failed.emit(str(exc))
            return
        if self._cancel_requested:
            return
        self.finished_result.emit(overview, list(inventory or []))


class _StatCard(QFrame):
    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("SurveyStatCard")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(2)
        self._value = QLabel("—")
        self._value.setObjectName("SurveyStatValue")
        self._title = QLabel(title)
        self._title.setObjectName("SurveyStatTitle")
        lay.addWidget(self._value)
        lay.addWidget(self._title)

    def set_value(self, text: str) -> None:
        self._value.setText(text)

    def set_title(self, text: str) -> None:
        self._title.setText(text)


class _OverviewSection(QFrame):
    """可折叠区块 — 默认收起, 标题行显示一行摘要."""

    def __init__(
        self,
        title: str,
        *,
        expanded: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self._summary = ""
        self._collapsed = not expanded
        self.setObjectName("SurveyOverviewSection")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)
        self._toggle = QPushButton()
        self._toggle.setObjectName("Ghost")
        self._toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle.setFlat(True)
        self._toggle.clicked.connect(self._on_toggle)
        root.addWidget(self._toggle)
        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(4, 0, 0, 4)
        self._body_layout.setSpacing(6)
        root.addWidget(self._body)
        self._refresh_header()
        self._body.setVisible(expanded)

    def body_layout(self) -> QVBoxLayout:
        return self._body_layout

    def set_summary(self, text: str) -> None:
        self._summary = str(text or "").strip()
        self._refresh_header()

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self._body.setVisible(not collapsed)
        self._refresh_header()

    def is_collapsed(self) -> bool:
        return self._collapsed

    def _on_toggle(self) -> None:
        self.set_collapsed(not self._collapsed)

    def _refresh_header(self) -> None:
        arrow = "▸" if self._collapsed else "▾"
        suffix = f"  ·  {self._summary}" if self._summary else ""
        self._toggle.setText(f"{arrow} {self._title}{suffix}")
        self._toggle.setToolTip("点击展开" if self._collapsed else "点击收起")


class SurveyOverviewPanel(QWidget):
    """调查概览右栏."""

    def __init__(self, ctx: Any = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._workspaces: list[str] = []
        self._scope_label: Optional[str] = None
        # 聚合代次令牌: 每次 set_workspaces / set_filtered_stats 自增。worker 结果回来时
        # token 不匹配就丢弃 —— 快速切换断面时, 慢的旧请求不许把旧数字盖到新选中上;
        # 中栏「数据汇总」落地后, 迟到的概览结果也不许覆盖筛选 KPI。
        self._token = 0
        self._worker: Optional[_SurveyOverviewWorker] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll, 1)

        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self._title = QLabel("调查概览")
        self._title.setObjectName("SectionTitle")
        layout.addWidget(self._title)
        self._scope = QLabel("未选择范围")
        self._scope.setObjectName("MutedSmall")
        self._scope.setWordWrap(True)
        layout.addWidget(self._scope)

        cards = QGridLayout()
        cards.setHorizontalSpacing(6)
        cards.setVerticalSpacing(6)
        self._card_specimens = _StatCard("标本编号")
        # self._card_photos = _StatCard("成片照片")  # §7 旧: 静态标题, 两种口径共用一卡易混淆
        # photo_count 双口径是刻意的, 标签必须说清数的是什么:
        #   概览模式 = get_project_results total(含未归编号成片) → 全部成片
        #   数据汇总模式 = 筛选范围内 uid 归组成片 → 编号成片
        self._card_photos = _StatCard("全部成片")
        self._card_rna = _StatCard("已取 RNA")
        self._card_species = _StatCard("物种数")
        self._card_workspaces = _StatCard("断面数")
        cards.addWidget(self._card_specimens, 0, 0)
        cards.addWidget(self._card_photos, 0, 1)
        cards.addWidget(self._card_rna, 0, 2)
        cards.addWidget(self._card_species, 1, 0)
        cards.addWidget(self._card_workspaces, 1, 1)
        layout.addLayout(cards)

        self._sec_map = _OverviewSection("站位地图", expanded=False)
        self._mini_map = SurveyOverviewMiniMap(self._sec_map, compact=True)
        self._sec_map.body_layout().addWidget(self._mini_map)
        layout.addWidget(self._sec_map)

        self._sec_dist = _OverviewSection("分布明细", expanded=False)
        dist_body = self._sec_dist.body_layout()
        dist_row = QHBoxLayout()
        dist_row.setSpacing(8)
        self._site_box = self._make_dist_column("地区/样地")
        self._province_box = self._make_dist_column("省/市")
        dist_row.addLayout(self._site_box["layout"], 1)
        dist_row.addLayout(self._province_box["layout"], 1)
        dist_body.addLayout(dist_row)
        self._station_box = self._make_dist_column("站位")
        dist_body.addLayout(self._station_box["layout"])
        dist_row2 = QHBoxLayout()
        dist_row2.setSpacing(8)
        self._collector_box = self._make_dist_column("采集人")
        self._identifier_box = self._make_dist_column("鉴定人")
        dist_row2.addLayout(self._collector_box["layout"], 1)
        dist_row2.addLayout(self._identifier_box["layout"], 1)
        dist_body.addLayout(dist_row2)
        self._photo_box = self._make_dist_column("拍摄人")
        dist_body.addLayout(self._photo_box["layout"])
        layout.addWidget(self._sec_dist)

        self._sec_species = _OverviewSection("物种名录", expanded=False)
        self._species_panel = SurveySummaryPanel(self._ctx, embedded=True)
        self._sec_species.body_layout().addWidget(self._species_panel)
        layout.addWidget(self._sec_species)
        layout.addStretch(1)

    def _make_dist_column(self, title: str) -> dict[str, Any]:
        col = QVBoxLayout()
        col.setSpacing(2)
        lbl = QLabel(title)
        lbl.setObjectName("MutedSmall")
        lbl.setStyleSheet("font-weight:600;")
        body = QLabel("—")
        body.setObjectName("SurveyDistBody")
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignmentFlag.AlignTop)
        col.addWidget(lbl)
        col.addWidget(body)
        return {"layout": col, "body": body}

    @staticmethod
    def _format_dist_rows(rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "暂无数据"
        return "\n".join(f"{r['name']}  ·  {r['count']}" for r in rows)

    @staticmethod
    def _dist_summary(rows: list[dict[str, Any]], *, empty: str = "暂无") -> str:
        if not rows:
            return empty
        top = rows[0]
        extra = len(rows) - 1
        if extra > 0:
            return f"{top['name']} {top['count']} 等 {len(rows)} 项"
        return f"{top['name']} {top['count']}"

    def species_panel(self) -> SurveySummaryPanel:
        return self._species_panel

    def set_workspaces(
        self,
        workspace_dirs: list[str],
        labels: Optional[dict[str, str]] = None,
        *,
        scope_label: Optional[str] = None,
    ) -> None:
        self._workspaces = [str(w) for w in (workspace_dirs or []) if w]
        self._scope_label = scope_label
        label_list = None
        if labels:
            label_list = [labels.get(w) for w in self._workspaces]

        # §7 旧实现（同步聚合, 跑在 GUI 线程 —— 多断面选中时逐库全表 SELECT + 物种
        # 名录扫描 + results/ 逐文件 stat, 数十秒, Windows 直接判「未响应」）:
        # overview = aggregate_survey_overview(self._workspaces, labels=label_list)
        # self._fill_overview(overview, labels)
        # self._card_photos.set_title("全部成片")
        # self._species_panel.set_workspaces(self._workspaces, labels=labels)

        self._card_photos.set_title("全部成片")
        # 断面数不需要聚合就知道 → 立刻显示; 其余 KPI 进加载态。
        self._card_workspaces.set_value(str(len(self._workspaces)))
        for card in (self._card_specimens, self._card_photos,
                     self._card_rna, self._card_species):
            card.set_value(_LOADING)

        self._token += 1
        token = self._token
        self._cancel_worker()

        worker = _SurveyOverviewWorker(self._workspaces, label_list)
        # lambda 里按值捕获 token/labels: 结果回来时 self._token 可能已经又变了。
        worker.finished_result.connect(
            lambda overview, _inv, t=token, lb=labels: self._on_overview_ready(t, overview, lb)
        )
        worker.failed.connect(lambda msg, t=token: self._on_overview_failed(t, msg))
        worker.finished.connect(_reap_overview_workers)
        # 强引用: QThread 被 GC 掉而线程还在跑 = "QThread: Destroyed while thread is
        # still running" 崩溃。模块级列表兜着, 结束后由 _reap_overview_workers 收割。
        _LIVE_OVERVIEW_WORKERS.append(worker)
        self._worker = worker
        worker.start()

        self._species_panel.set_workspaces(self._workspaces, labels=labels)

    def _cancel_worker(self) -> None:
        """作废在飞的聚合请求（不阻塞主线程等它: 只置取消位, 结果按 token 丢弃）。"""
        worker = self._worker
        self._worker = None
        if worker is None:
            return
        try:
            worker.cancel()
        except Exception:  # pragma: no cover - 防御性
            pass
        # 断开回调: cancel 只挡「还没 emit」的; 已经排队的 queued 信号仍会送达, 那时
        # 面板可能已被销毁 → RuntimeError: wrapped C/C++ object has been deleted。
        for sig in (worker.finished_result, worker.failed):
            try:
                sig.disconnect()
            except (TypeError, RuntimeError):  # 没连过 / 对象已没了
                pass

    def _is_alive(self) -> bool:
        """底层 C++ 控件还在吗 —— 面板被销毁后, 迟到的信号不许再碰控件。"""
        try:
            from PyQt6 import sip
        except ImportError:  # pragma: no cover - 非 PyQt6 环境
            return True
        return not sip.isdeleted(self)

    def _on_overview_ready(
        self,
        token: int,
        overview: Any,
        labels: Optional[dict[str, str]] = None,
    ) -> None:
        if token != self._token or not isinstance(overview, dict):
            return  # 过期代次（用户已切走 / 数据汇总已落地）→ 丢弃
        if not self._is_alive():
            return  # 面板已被销毁, 结果迟到 → 不许再碰控件
        self._worker = None
        self._fill_overview(overview, labels)

    def _on_overview_failed(self, token: int, message: str) -> None:
        """坏库 / 锁库 / 权限错 —— 给出「—」而不是永远停在加载态。"""
        if token != self._token or not self._is_alive():
            return
        self._worker = None
        for card in (self._card_specimens, self._card_photos,
                     self._card_rna, self._card_species):
            card.set_value("—")

    def teardown(self) -> None:
        """离页 / 关闭：作废在飞请求。仍在跑的线程由模块级列表持有, 不在主线程 wait。"""
        self._token += 1  # 之后回来的结果一律过期
        self._cancel_worker()

    def _fill_overview(
        self,
        overview: dict[str, Any],
        labels: Optional[dict[str, str]] = None,
    ) -> None:
        n_ws = overview.get("workspace_count", 0)
        if self._scope_label:
            self._scope.setText(self._scope_label)
        elif n_ws == 0:
            self._scope.setText("所选范围内没有已初始化工作区")
        elif n_ws == 1:
            lbl = (labels or {}).get(self._workspaces[0]) or self._workspaces[0]
            self._scope.setText(str(lbl))
        else:
            self._scope.setText(f"已汇总 {n_ws} 个断面")

        self._card_specimens.set_value(str(overview.get("specimen_count", 0)))
        self._card_photos.set_value(str(overview.get("photo_count", 0)))
        self._card_rna.set_value(str(overview.get("rna_count", 0)))
        self._card_species.set_value(str(overview.get("species_count", 0)))
        self._card_workspaces.set_value(str(n_ws))

        map_pts = overview.get("map_points") or []
        self._mini_map.set_points(map_pts)
        if map_pts:
            self._sec_map.set_summary(f"{len(map_pts)} 个站位")
        else:
            self._sec_map.set_summary("暂无坐标")

        site_rows = overview.get("site_rows") or []
        province_rows = overview.get("province_rows") or []
        station_rows = overview.get("station_rows") or []
        collector_rows = overview.get("collector_rows") or []
        identifier_rows = overview.get("identifier_rows") or []
        photo_rows = overview.get("photographer_rows") or []

        self._site_box["body"].setText(self._format_dist_rows(site_rows))
        self._province_box["body"].setText(self._format_dist_rows(province_rows))
        self._station_box["body"].setText(self._format_dist_rows(station_rows))
        self._collector_box["body"].setText(self._format_dist_rows(collector_rows))
        self._identifier_box["body"].setText(self._format_dist_rows(identifier_rows))
        self._photo_box["body"].setText(self._format_dist_rows(photo_rows))

        dist_bits = []
        if site_rows:
            dist_bits.append(f"地区/样地 {len(site_rows)}")
        if station_rows:
            dist_bits.append(f"站位 {len(station_rows)}")
        if collector_rows:
            dist_bits.append(f"采集人 {len(collector_rows)}")
        if identifier_rows:
            dist_bits.append(f"鉴定人 {len(identifier_rows)}")
        if photo_rows:
            dist_bits.append(f"拍摄人 {len(photo_rows)}")
        self._sec_dist.set_summary(
            " · ".join(dist_bits) if dist_bits else "暂无明细"
        )

        n_species = overview.get("species_count", 0)
        n_records = overview.get("specimen_count", 0)
        self._sec_species.set_summary(f"{n_species} 种 · {n_records} 编号")

    def set_filtered_stats(
        self,
        stats: dict[str, Any],
        *,
        workspace_dirs: list[str],
        labels: Optional[dict[str, str]] = None,
        scope_label: Optional[str] = None,
    ) -> None:
        """筛选后的 KPI / 分布（数据汇总模式）."""
        self._workspaces = [str(w) for w in (workspace_dirs or []) if w]
        # 概览 worker 可能还在飞。自增令牌 → 它回来时算过期, 不会把「全部成片」
        # 的数字盖到刚落地的「编号成片」筛选 KPI 上。
        self._token += 1
        self._cancel_worker()
        self._fill_overview(stats, labels)
        self._card_photos.set_title("编号成片")
        if scope_label:
            self._scope.setText(scope_label)
        self._species_panel.set_workspaces(self._workspaces, labels=labels)

    def workspaces(self) -> list[str]:
        return list(self._workspaces)

    def set_overview_sections_collapsed(self) -> None:
        """概览模式：折叠地图/分布/物种，只保留 KPI."""
        self._sec_map.set_collapsed(True)
        self._sec_dist.set_collapsed(True)
        self._sec_species.set_collapsed(True)

    def focus_species_catalog(self) -> None:
        """物种名录模式：展开物种表."""
        self._sec_species.set_collapsed(False)
