# 用户需求台账 (User Request Log)

> **为什么有这个文件**：用户多次提出的要求（尤其是「大文件重构」）被历任 agent 反复无视——
> 每个会话都被当下的火情（bug / 打包 / 崩溃）挤掉，用户不得不重复第 N 遍。
> 从 2026-07-12 起，**用户提出的每一条要求都必须登记在此**，带日期与状态，
> 直到关闭为止。新会话开工前必须读本文件（与 `PROJECT_MEMORY.md` 同级重要）。
>
> 状态含义：`OPEN` 未做 · `IN-PROGRESS` 在做 · `DONE` 完成并验证 · `DROPPED` 用户明确取消

---

## R-001 · 大文件重构 · 状态：OPEN（用户已重复多次，历任 agent 未执行）

**要求**：拆分超大源文件，降低单文件行数，提升可维护性。用户已就此反复要求多次，
并已另行启动 Fable 模型来做重构，因为 Claude 会话一直不执行。

**当前最大文件**（2026-07-12 实测）：

| 行数 | 文件 |
|---:|---|
| 4962 | `app/views/project_tree_view.py` |
| 2745 | `app/config/theme.py` |
| 2446 | `app/services/collab_service.py` |
| 2423 | `app/views/collab_view.py` |
| 2067 | `app/widgets/monitor_panel.py` |
| 2052 | `app/views/workbench_view.py` |
| 1873 | `app/widgets/naming_panel.py` |
| 1872 | `app/views/labels_view.py` |
| 1712 | `app/widgets/uid_grouped_grid.py` |
| 1681 | `app/widgets/specimen_sidebar.py` |
| 1675 | `app/views/collection_map_view.py` |
| 1625 | `app/main_window.py` |

**为什么一直没做（诚实归因）**：每次会话都被更紧急的问题占满（崩溃 / 卡顿 / 打包），
重构体量大、收益不即时可见，于是永远排在队尾。这是排期问题，不是技术障碍。

**约束**：重构必须行为等价——`docs/architecture/directory-boundaries.md` 定的边界不变；
移动模块要留同名 compat shim（项目既有惯例，见 `cover_pick_service.py`）；UI 外观不得变化。

---

## R-002 · 每条用户要求都要留痕 · 状态：DONE (2026-07-12)

**要求**：用户提的每个要求都要记录下来，否则 agent 根本不知道自己已经让用户重复了多少遍。

**落实**：本文件即台账。新要求追加为 `R-00N` 条目。

---

## R-003 · Windows 卡顿 / 未响应必须修掉 · 状态：IN-PROGRESS (2026-07-12)

**要求**：Windows 上「响应慢、延迟、卡顿、不流畅、未响应」必须深入优化并重新打包。

**已确认根因**（诊断记录，勿重新调研）：
- `collab_service.py` 在 **Qt 主线程**上做同步 `httpx` 请求（`pull_all_specimens_from_session`
  超时 8 s；`_maybe_retry_offline_drafts` 每 15 s 重试；`create_task` 新建标本时 POST 所有 peer），
  一个连不上的 peer 就冻结 UI 数秒。
- `_spawn` 出的 daemon 线程（无 Qt 事件循环）里调用 `QTimer.singleShot` / 主线程 `QSettings`
  → 日志中数百条 `QBasicTimer::start: Timers cannot be started from another thread`。
- `results_column.py` 布局定时器自激振荡（重建→滚动条→列数翻转→重建），每轮同步重解码缩略图。
- `monitor_panel.py` 16 ms 定时器在主线程解码大 TIFF。

**为什么开发时看不见**：WSL2 是 NAT 网络，mDNS 组播出不去 → 发现不到 peer → 阻塞代码根本不执行；
且 `/mnt/*` 上 `QFileSystemWatcher` 收不到 inotify 事件 → 监视面板不重扫。
**只有原生 Windows 才触发。** 见 `PROJECT_MEMORY.md`。

---

## R-004 · 打包 exe 必须真能用 · 状态：IN-PROGRESS (2026-07-12)

**要求**：交付前不许只凭 `--smoke` 退出码就宣称「可以了」。

**已修**：`collab_net.py` — frozen exe 无 stdout，uvicorn `DefaultFormatter` 调
`sys.stdout.isatty()` 崩溃 → `ValueError: Unable to configure formatter 'default'`
（v0.57 / v0.59 包都中招）。修法：`log_config=None`。

**新交付标准**：打包后必须 (1) 真启动 GUI、(2) 逐页点开、(3) py-spy attach 确认主线程无阻塞，
才能说「可以了」。**只给 `dist\` 路径，绝不能让用户点到 `build\` 里的中间产物。**

---

## R-005 · 「项目总览」改成树形结构 · 状态：OPEN（待出设计稿）

**要求**：顶栏「选择工作区 ▾ → 项目总览」现在是平铺表格（`overview_view.py`，view_id `overview`，
页面标题却叫「最近使用」——命名不一致），用户希望改成 tree 结构。

**调研结论**：该页数据源只有 `data/user_projects.json`，**不含父子层级**（`root` 字段目前 3 条记录
全为空），单靠它渲染不出「项目 → 断面 → 采样点」真树。真层级在
`project_tree_service.scan_tree()`（磁盘扫描）和 `project_catalog_service`（根库登记）里。
与「项目树」页（`project_tree_view.py` 的 flat 模式）读的是同一份 JSON，**功能完全重叠**。

**下一步**：出设计意见 + 示例图，不动代码（该区域另有 agent 在改）。
