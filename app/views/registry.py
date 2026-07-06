"""registry.py — navigation entries (real module views).

Nav structure mirrors the web prototype's page layout: the 拍照主线 sub-modules
(目录监控 / 分组 / 标本命名 / Helicon合成 / 整理归档 / 成果记录) live INSIDE the
工作台 hub as panels, not as separate nav pages — same as the web `workspace` page.

To add/replace a view: add a LazyViewSpec to ALL_VIEW_SPECS. MainWindow registers
the lightweight metadata immediately, then imports the real module only when the
page is first opened. The legacy ALL_VIEWS sequence still resolves to concrete
classes for older tests and callers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
from typing import Iterator, Sequence


@dataclass(frozen=True)
class LazyViewSpec:
    view_id: str
    nav_title: str
    module: str
    class_name: str
    nav_icon: str = ""
    _resolved: type | None = field(default=None, init=False, repr=False, compare=False)

    def resolve(self) -> type:
        cached = self._resolved
        if cached is None:
            module = import_module(self.module)
            cached = getattr(module, self.class_name)
            object.__setattr__(self, "_resolved", cached)
        return cached


class _LazyViewClasses(Sequence[type]):
    """Compatibility wrapper for callers that still expect concrete classes."""

    def __init__(self, specs: Sequence[LazyViewSpec]) -> None:
        self._specs = specs

    def __len__(self) -> int:
        return len(self._specs)

    def __getitem__(self, idx):
        if isinstance(idx, slice):
            return [spec.resolve() for spec in self._specs[idx]]
        return self._specs[idx].resolve()

    def __iter__(self) -> Iterator[type]:
        for spec in self._specs:
            yield spec.resolve()

    def __contains__(self, item: object) -> bool:
        return any(spec.resolve() is item for spec in self._specs)


# Order = nav order (mirrors web prototype topbar exactly)
ALL_VIEW_SPECS: tuple[LazyViewSpec, ...] = (
    LazyViewSpec("workbench", "照片工作区", "app.views.workbench_view", "WorkbenchView"),
    LazyViewSpec("collab", "协作", "app.views.collab_view", "CollabView"),
    LazyViewSpec("overview", "最近使用", "app.views.overview_view", "OverviewView"),
    LazyViewSpec("project_tree", "项目树", "app.views.project_tree_view", "ProjectTreeView"),
    LazyViewSpec("labels", "标签打印", "app.views.labels_view", "LabelsView"),
    LazyViewSpec("worms", "WoRMS 分类库", "app.views.worms_view", "WormsView"),
    LazyViewSpec("taxonomy", "内置分类库", "app.views.taxonomy_view", "TaxonomyView"),
    LazyViewSpec("coords", "坐标工具", "app.views.coords_view", "CoordsView"),
    LazyViewSpec("summary", "项目汇总", "app.views.summary_view", "SummaryView"),
    LazyViewSpec(
        "collection_records",
        "采集记录",
        "app.views.collection_records_view",
        "CollectionRecordsView",
    ),
    LazyViewSpec("collection_map", "采集地图", "app.views.collection_map_view", "CollectionMapView"),
    LazyViewSpec("settings", "配置", "app.views.settings_view", "SettingsView"),
)

ALL_VIEWS: Sequence[type] = _LazyViewClasses(ALL_VIEW_SPECS)
