# 结构迁移清单（低风险 → 中风险）

每步完成后跑 **该步列出的 pytest**；失败则 `git checkout -- <files>` 回滚，不要叠步。

---

## 阶段 0 — 已完成 / 维护项（随时）

| 项 | 说明 | 验证 |
|----|------|------|
| 目录边界文档 | `docs/architecture/directory-boundaries.md` | 人工 |
| 子域 README 锚点 | `app/services/{project,specimen,...}/README.md` | 人工 |
| `.gitignore` | `tmp/`、`artifacts/`、根目录日志 | `git status` |
| `api-gateway` | 保留 + `README.md` 声明无代码 | 人工 |

---

## 阶段 1 — 低风险（仅文档 + 测试落位）

| # | 动作 | 回滚 |
|---|------|------|
| 1.1 | 新测试放进 `tests/unit/<layer>/test_*.py` | 移回 `tests/` |
| 1.2 | 旧测试 **不批量迁移**；仅在新功能时遵守新路径 | — |
| 1.3 | `docs/README.md` 索引更新 | 删文件 |

**验证：** `QT_QPA_PLATFORM=offscreen pytest tests/unit/ -q`（有文件时）

---

## 阶段 2 — 低风险（service 子域 · 叶子模块）

**模式：** 实现移到 `app/services/<domain>/<name>.py`，原路径留 shim：

```python
# app/services/edit_lock_service.py  (shim)
from app.services.specimen.edit_lock_service import *  # noqa: F403
```

| # | 文件 | 目标 | 依赖面 | pytest |
|---|------|------|--------|--------|
| 2.1 | `edit_lock_service.py` | `specimen/` | 数据筛选 | ✅ 已迁 + shim |
| 2.2 | `naming_field_catalog.py` | `specimen/` | 命名/筛选标签 | ✅ 已迁 + 兼容入口 |
| 2.3 | `collab_types.py` | `collab/` | 协作类型 | ✅ 已迁 + 兼容入口 |
| 2.4 | `label_design_schema.py` | `label/` | 标签设计器 | ✅ 已迁 + 兼容入口 |

**回滚：** 删除子域内新文件 + 恢复 shim 为完整实现（或 `git checkout` 两步 commit）。

---

## 阶段 3 — 中风险（service 子域 · 有内部依赖）

按 **被 import 次数少 → 多** 顺序；每文件单独 commit。

| 批次 | 文件示例 | 注意 |
|------|----------|------|
| 3a | `survey_overview_service.py`, `cover_pick_service.py` | ✅ 已迁 + 兼容入口 |
| 3b | `specimen_filter_service.py`, `cross_workspace_query_service.py` | 数据汇总 |
| 3c | `collab_api.py`, `collab_file_sync.py` | 协作子系统成组 |
| 3d | `project_service.py` | 核心 · 最后 |

**验证：** `python scripts/run_core_regression.py quick` + 该域 named suite。

---

## 阶段 4 — 中风险（views / widgets）

| # | 动作 | 说明 |
|---|------|------|
| 4.1 | `app/views/project/` 包 + shim | `project_tree_view.py` 等大文件后移 |
| 4.2 | `registry.py` 仍指向 `app.views.project_tree_view` shim | 不改 `LazyViewSpec` 字符串 |
| 4.3 | widgets 同理，`workbench/` 先移小 panel |

**验证：** `test_project_tree_view.py` `test_workbench_view.py`（单文件）

---

## 阶段 5 — 可选 / 需产品确认

| 项 | 选项 |
|----|------|
| `app/api-gateway/` | A) 删除目录 + 更新 ADR；B) 保持占位至真有网关需求 |
| `data/` 大库 | 外部下载脚本 + `data/README.md` 说明 |
| 测试树全量归并 | 按 `tests/unit/` 分批，每批 10–20 文件 |

---

## 禁止（除非用户明确要求）

- 一次性移动 `workbench_view.py` / `project_service.py` / `collab_service.py`
- 改 `registry.py` 的 `view_id` 或 nav 顺序
- 无 shim 的全库 import 替换
- 把 `docs/superpowers/` 历史文档当实现规格
