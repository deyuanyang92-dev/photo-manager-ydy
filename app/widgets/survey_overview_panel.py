"""survey_overview_panel.py — 项目树右栏「调查概览」(系统汇总).

默认只显示 KPI 数字; 地图 / 分布 / 物种名录点击展开, 避免右栏信息堆砌。
"""
from __future__ import annotations

from typing import Any, Optional

from PyQt6.QtCore import Qt
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
from app.widgets.survey_overview_mini_map import SurveyOverviewMiniMap
from app.widgets.survey_summary_panel import SurveySummaryPanel


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
        self._card_photos = _StatCard("成片照片")
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
        overview = aggregate_survey_overview(self._workspaces, labels=label_list)
        self._fill_overview(overview, labels)
        self._species_panel.set_workspaces(self._workspaces, labels=labels)

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
        self._fill_overview(stats, labels)
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
