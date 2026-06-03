"""registry.py — navigation entries (real module views).

Nav structure mirrors the web prototype's page layout: the 拍照主线 sub-modules
(目录监控 / 分组 / 标本命名 / Helicon合成 / 整理归档 / 成果记录) live INSIDE the
工作台 hub as panels, not as separate nav pages — same as the web `workspace` page.

To add/replace a view: import its BaseView subclass and put it in ALL_VIEWS.
MainWindow.register_view() handles nav + stack wiring (expects `cls(ctx)`).
"""
from __future__ import annotations

from app.views.workbench_view import WorkbenchView
from app.views.overview_view import OverviewView
from app.views.taxonomy_view import TaxonomyView
from app.views.worms_view import WormsView
from app.views.coords_view import CoordsView
from app.views.labels_view import LabelsView
from app.views.summary_view import SummaryView
from app.views.settings_view import SettingsView

# Order = nav order (mirrors web prototype topbar exactly)
# CollabView is NOT a nav tab — collaboration lives inline in the workbench sidebar.
ALL_VIEWS: list[type] = [
    WorkbenchView,   # 照片工作区（含 监控/分组/命名/合成/整理/成果 面板）
    OverviewView,    # 项目总览（+ 导出 Excel/CSV/DwC）
    LabelsView,      # 标签打印（双桶 + 二维码 WYSIWYG）
    WormsView,       # WoRMS 分类库（海洋物种验证）
    TaxonomyView,    # 内置分类库（+ 4 级补全浮层）
    CoordsView,      # 坐标工具（交互地图）
    SummaryView,     # 项目汇总（跨项目标本数据汇总表 + 导出 Excel/CSV）
    SettingsView,    # 配置
]
