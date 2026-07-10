# 目录职责边界（结构契约）

**目的：** 新文件有固定落位，避免 `app/services` 等目录无限扁平化。

**运行时行为不变** — 本文只约束「放哪里、为什么」，不规定重构时间表。迁移步骤见 [`../migration-checklist.md`](../migration-checklist.md)。

---

## 根目录

| 路径 | 职责 | 不放什么 |
|------|------|----------|
| `main.py` | 进程入口、Qt 启动 | 业务逻辑 |
| `tests/` | pytest / pytest-qt | 可执行脚本 |
| `scripts/` | 构建、回归、维护 CLI | 页面 UI |
| `docs/` | 规范、ADR、审计、截图脚本 | 运行时配置 |
| `resources/` | i18n、图标、打包底图 | 用户项目数据 |
| `data/` | 应用级静态 JSON、本地缓存 | 标本 SQLite（在 `<project>/_data/`） |
| `tmp/` | 本地临时输出（git 忽略） | 任何需版本化的代码 |
| `artifacts/` | 构建/测试产物（git 忽略） | 源码 |

---

## `app/` 顶层

| 路径 | 职责 |
|------|------|
| `app_context.py` | DI 容器：settings、当前项目、会话 handoff |
| `main_window.py` | 壳：顶栏导航、栈、状态栏 |
| `config/` | 主题、i18n、字段注册表、settings（无业务编排） |
| `db/` | schema、连接、迁移 |
| `models/` | 持久化对象（若有） |
| `services/` | **业务规则与编排**（Qt-free 优先） |
| `utils/` | 通用工具（命名、标签数学、路径、UI 对话框） |
| `views/` | **整页**（`BaseView` 子类） |
| `widgets/` | **可复用面板**（多页共用） |
| `workers/` | 后台线程 / 长任务 |
| `api-gateway/` | **预留**；当前无实现，见 [`../../app/api-gateway/README.md`](../../app/api-gateway/README.md) |

**views 与 widgets 分界：** 能独立注册进 `registry.py` 的是 view；被多个 view 拼装的卡片/对话框是 widget。

---

## `app/services/` 子域（渐进迁入）

| 子目录 | 归属模块 | 典型文件（当前仍在扁平层） |
|--------|----------|---------------------------|
| `project/` | 项目树、目录、设置、汇总 | `project_service.py`, `project_tree_service.py`, `cross_workspace_query_service.py` |
| `specimen/` | 标本、筛选、命名、导入导出 | `specimen_filter_service.py`, `naming_field_catalog.py`, `edit_lock_service.py` |
| `taxonomy/` | 分类、WoRMS、物种名录 | `taxonomy_service.py`, `taxon_inventory_service.py`, `worms_service.py` |
| `label/` | 标签模板、打印、排版 | `label_service.py`, `label_design_schema.py` |
| `collab/` | 协作 HTTP、mDNS、同步 | `collab_service.py`, `collab_api.py`, `collab_types.py` |
| *(扁平层保留)* | 跨域或尚未归类 | `archive_service.py`, `photo_asset_service.py`, `update_service.py` |

**规则：** 新 service 优先进子域；旧路径用同级 shim 或 `__init__.py` 再导出，禁止一次性改全库 import。

---

## `app/views/` / `app/widgets/` 子域（命名对齐）

| 子域 | views | widgets |
|------|-------|---------|
| `project/` | `project_tree_view`, `overview_view`, `summary_view` | `project_card`, `survey_overview_*` |
| `workbench/` | `workbench_view` | `monitor_panel`, `naming_panel`, `*_card_panel` |
| `tax/` | `taxonomy_view`, `worms_view`, `data_filter_view` | `specimen_filter_panel`, `taxon_*` |
| `label/` | `labels_view` | `label_*` |
| `collab/` | *(Collab 内嵌工作台)* | `collab_*` |

当前文件仍在扁平目录；子域文件夹仅作文档与后续迁移锚点。

---

## `docs/` 分组

| 路径 | 用途 |
|------|------|
| `PROJECT_MEMORY.md` | **用户重复要求** — 改核心流程前必读 |
| `specs/` | **可执行实现规格**（TDD 输入） |
| `adr/` | 已接受架构决策 |
| `audit/` | 模块审计、复盘 |
| `architecture/` | 目录边界、迁移清单（本文） |
| `superpowers/` | 历史计划 / jury 记录 — **非当前真相源** |
| `shots/` | 对比截图与抓取脚本 |

---

## `data/` 边界

- **进 repo：** 小体积 JSON 配置、taxonomy 索引模板。
- **不进 repo：** `data/cache/`（缩略图等）、大体积离线库 → 本地生成或外部包/LFS。
- 用户标本库路径由 `AppSettings` / 项目目录决定，不在 `data/` 下。

---

## 权威行为 vs 结构

| 类型 | 权威来源 |
|------|----------|
| 编号语义 | `docs/PROJECT_MEMORY.md` + `app/utils/naming.py` |
| 页面注册 | `app/views/registry.py` |
| 字段标签 | `app/services/naming_field_catalog.py` |
| 目录放哪 | **本文** + 各子目录 `README.md` |
