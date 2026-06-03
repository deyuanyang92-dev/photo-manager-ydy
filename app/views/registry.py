"""registry.py — 14 navigation entries for the workbench skeleton.

All modules start as PlaceholderView instances. Replace an entry by
importing the real view class and swapping it here; the rest of the
wiring (nav + stack) is handled by MainWindow.register_view().
"""
from __future__ import annotations

from app.views.placeholder_view import make_placeholder

# ── 14 module entries (order = nav order) ────────────────────────────────
#   (view_id, nav_title, nav_icon)
_ENTRIES = [
    ("workbench",       "工作台",     "🔬"),
    ("overview",        "项目总览",   "📊"),
    ("naming",          "标本命名",   "🏷"),
    ("monitor",         "目录监控",   "👁"),
    ("grouping",        "分组",       "🗂"),
    ("helicon",         "Helicon合成","🔧"),
    ("archive",         "整理归档",   "📦"),
    ("results",         "成果记录",   "🖼"),
    ("taxonomy",        "分类输入",   "🌿"),
    ("worms",           "WoRMS",      "🐚"),
    ("coords",          "坐标工具",   "🗺"),
    ("labels",          "标签打印",   "🖨"),
    ("collab",          "协作",       "👥"),
    ("settings",        "全局设置",   "⚙"),
]

# Build placeholder view classes
ALL_VIEWS: list[type] = [
    make_placeholder(vid, title, icon)
    for vid, title, icon in _ENTRIES
]
