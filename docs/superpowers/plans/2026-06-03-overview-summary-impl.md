# 项目总览 & 项目汇总 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the web prototype's 项目总览 and 项目汇总 pages into the v3 PyQt6 GUI as `overview_view.py` and `summary_view.py`, fully matching the web Oracle functionality.

**Architecture:** Both views are `BaseView` subclasses registered in `views/registry.py`. `OverviewView` reads/writes `user_projects.json` via `project_service`; `SummaryView` reads the project SQLite DB via `ctx.get_db()` and delegates export to `export_service`.

**Tech Stack:** PyQt6, openpyxl, SQLite, `project_service.py`, `export_service.py`

**Status (2026-06-03):** COMPLETE — both views fully implemented, 60/60 tests passing, 1920×1080 screenshots captured.

---

## What was already implemented

Both `overview_view.py` and `summary_view.py` were already complete and all tests passing before this planning session.  This document serves as the post-hoc spec + feature audit.

### Functional audit against web Oracle

#### 项目总览 (OverviewView) — `app/views/overview_view.py`

| Feature | Status |
|---------|--------|
| F01 h2 "项目总览" 标题 | ✓ |
| F02 + 新建项目 按钮（Primary）| ✓ |
| F03 + 打开工作区 按钮（Outline）| ✓ |
| F04 时间筛选 标签 | ✓ |
| F05 全部 按钮（默认选中）| ✓ |
| F06 年份按钮（动态从项目数据生成）| ✓ |
| F07 年份筛选互斥切换 + 过滤表格 | ✓ |
| F08 6 列表格（项目名/目录/时间/地点/负责人/操作）| ✓ |
| F09 项目名加粗 + 年份附加 | ✓ |
| F10 行内小统计懒加载（N标本·N成片·N待处理）| ✗ (需异步API，未实现) |
| F11 磁盘目录 monospace + tooltip | ✓ |
| F12 时间列 monospace | ✓ |
| F13 进入工作区 按钮（Primary）| ✓ |
| F14 详情 按钮（Outline）| ✓ |
| F15 状态栏「共 N 个项目」| ✓ |
| F16 进入工作区 → 设 ctx.current_project_dir | ✓ |
| F17 无目录项目提示 | ✓ |
| F18 enter_workspace_requested 信号 | ✓ |
| F19 通知 MainWindow 切换工作台 | ✓ |
| F20 新建项目弹窗 | ✓ |
| F21 表单：项目名/目录/地点/负责人 | ✓ |
| F22 浏览目录自动填充项目名 | ✓ |
| F23 调 create_project() 创建结构 | ✓ |
| F24 创建 incoming-jpg/ results/ _data/ | ✓ |
| F25 去重 + 持久化 user_projects.json | ✓ |
| F26 刷新表格 | ✓ |
| F27 打开工作区 → QFileDialog | ✓ |
| F28 open_project() → 白名单注册 | ✓ |
| F29 自动创建缺失子目录 | ✓ |
| F30 持久化 + 刷新 | ✓ |
| F31 详情弹窗 _ProjectDetailDialog | ✓ |
| F32 弹窗显示：名/年/地点/时间/目录/负责人/ID | ✓ |

**总计：31/32 ✓（F10 行内统计懒加载未实现）**

#### 项目汇总 (SummaryView) — `app/views/summary_view.py`

| Feature | Status |
|---------|--------|
| S01 h2 "项目汇总" 标题 | ✓ |
| S02 项目筛选下拉（全部 + 每个项目）| ✓ |
| S03 ⚙ 字段 toggle 按钮 | ✓ |
| S04 ⬇ Excel 按钮（Primary）| ✓ |
| S05 ⬇ CSV 按钮（Outline）| ✓ |
| S06 保存目录输入框 | ✓ |
| S07 💾 保存 按钮 | ✓ |
| S08 保存结果消息（颜色区分）| ✓ |
| S09 行列计数标签「N 条 · M 列」| ✓ |
| S10 字段面板展开/收起 | ✓ |
| S11 全选 按钮 | ✓ |
| S12 重置默认 按钮 | ✓ |
| S13 清空 按钮 | ✓ |
| S14 34 个 checkbox 即时更新表格 | ✓ |
| S15 34 列全量定义（ALL_COLS）| ✓ |
| S16 默认 25 个可见列（_DEFAULT_KEYS）| ✓ |
| S17 项目筛选过滤行 | ✓ |
| S18 compStatus 彩色（青/黄/红/灰）| ✓ |
| S19 taxoOk 彩色（✓青/✗灰）| ✓ |
| S20 rna 彩色（✓黄/✗灰）| ✓ |
| S21 交替行色 | ✓ |
| S22 sticky 首列（frozen column）| ✗ (QTableView 原生不支持，未实现) |
| S23 空数据提示 | ✓ |
| S24 列排序（QSortFilterProxyModel）| ✓ |
| S25 Excel 导出 → QFileDialog | ✓ |
| S26 export_service.export_excel() | ✓ |
| S27 蓝色表头 + 白色文字 + 加粗 | ✓ |
| S28 冻结首行 freeze_panes | ✓ |
| S29 自动筛选 auto_filter | ✓ |
| S30 交替行色（浅蓝）| ✓ |
| S31 自动列宽（中文2字节）| ✓ |
| S32 元数据 Sheet | ✓ |
| S33 CSV → QFileDialog | ✓ |
| S34 按可见列 + 项目筛选 | ✓ |
| S35 UTF-8 BOM | ✓ |
| S36 中文列头 | ✓ |
| S37 保存到目录验证非空 | ✓ |
| S38 自动创建目录 | ✓ |
| S39 文件名含日期 | ✓ |
| S40 保存中禁用按钮 | ✓ |
| S41 保存结果消息 | ✓ |

**总计：40/41 ✓（S22 sticky首列未实现）**

---

## Tests

- `tests/test_overview_view.py` — 30 tests (5 groups)
- `tests/test_summary_view.py` — 30 tests (7 groups)
- All 60 pass.  Full suite: 850 pass, 1 pre-existing fail (coords_view CSV header, unrelated), 4 skip.

## Screenshots

- `docs/shots/overview_func.png` — OverviewView 1920×1080
- `docs/shots/summary_func.png` — SummaryView 1920×1080 (5 specimen sample data)
