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
from app.views.collab_view import CollabView
from app.views.settings_view import SettingsView

# Order = nav order
ALL_VIEWS: list[type] = [
    WorkbenchView,   # 工作台（含 监控/分组/命名/合成/整理/成果 面板）
    OverviewView,    # 项目总览（+ 导出 Excel/CSV/DwC）
    TaxonomyView,    # 分类库（+ 4 级补全浮层）
    WormsView,       # WoRMS 海洋物种验证
    CoordsView,      # 坐标工具（交互地图）
    LabelsView,      # 标签打印（双桶 + 二维码 WYSIWYG）
    CollabView,      # 多人协作
    SettingsView,    # 全局设置
]
