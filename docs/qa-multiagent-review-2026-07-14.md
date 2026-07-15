# 多 Agent 代码审查 + UI 一致性 + 死按键扫描（2026-07-14）

> 本文件是一次性审查记录，不是持续维护的 gap list。发起方式：用户要求"多agent"对当前未提交改动做代码审查+前端局部美化+死按键排查。审查者 = Claude Sonnet 5 多 agent workflow（约 130 个 subagent，两轮 review + 两轮补跑，因中途 API 403 中断过一次）。

## 范围

- 仓库 `git diff HEAD` 显示 112 个文件改动，但其中 **82 个是纯 CRLF 换行符翻转噪音**（未处理，见文末"遗留问题"）。
- 真实逻辑改动：**30 个文件，+4569/-760 行**，本次 bug 审查覆盖其中 14 个源码文件。
- 死按键扫描覆盖**全仓库 96 个含按钮控件的文件、782 处 `.connect()` 调用点**（不限于本次 diff）。

---

## 一、Bug 清单（26 条，已去重）

| 文件:行 | 等级 | 类别 | 说明 |
|---|---|---|---|
| `new_survey_project_dialog.py:181` | 🔴 CONFIRMED | correctness | 默认层级行名为空（只有占位符），对话框自己的校验就拒绝自己的默认状态，**导致 5 个自身测试失败**（已用 pytest 复核，非本次修复引入） |
| `new_survey_project_dialog.py:549` | 🔴 CONFIRMED | validation-gap | 非"追加"模式下从未检查层级是否为空，用户删空层级后仍可静默建出零工作区的空项目 |
| `project_scaffold_service.py:292` | 🔴 CONFIRMED（两路独立找到） | correctness | `create_survey_project` 缺少 `append_survey_structure` 已有的整棵树冲突预检查；多级命名冲突会留下半成品目录+已注册的 project.db，且后续用同名重建会被"目录非空"卡死，需手动清理 |
| `monitor_panel.py:1579` | 🔴 CONFIRMED | correctness | 新的"静默缩略图批次"遮罩在"卡片已被回收"分支下不清理 pending 集合，超时处理器会无限重排 → 监控网格可能被永久遮住 |
| `project_tree_view.py:2808` | 🟡 CONFIRMED | correctness | 最近项目分组用 `str(path).casefold()` 做 key，在 Linux/macOS 大小写敏感文件系统上会把不同路径误合并成一个节点 |
| `main.py:351` | 🟠 PLAUSIBLE | correctness | 原生 Windows 启动快速路径（避免读取还原几何+避免 raise/activate，防止启动卡顿/闪屏）被删除；看起来是这轮"记住用户手动调整的窗口大小"需求的必然取舍，但没有等价的新护栏，建议真机（尤其低配 Windows）验证一下启动体验 |
| `workspace_breadcrumb.py:713` | 🟠 PLAUSIBLE | correctness | 默认（classic）顶栏不再接 ◀▶ 历史前进后退按钮；`_history_step` 逻辑还在但只有隐藏的"设计变体"菜单能摸到。同 diff 里有配套测试证明是有意为之，但功能确实从默认界面消失了 |
| `collab_view.py:2163` | 🟡 CONFIRMED（两路独立找到） | dead-code | `_on_setup_wizard` 已无调用者（按钮改接 `_begin_team_edit` 了），方法本身成死代码 |
| `main_window.py:1552` | 🟢 CONFIRMED | dead-path | `restore_state` 里 `defer_initial_view=False` 分支没有任何生产调用者，只被测试跑到 |
| `monitor_panel.py:1732` | 🟢 CONFIRMED | reuse | "静默首屏缩略图"机制和同一 diff 里 `uid_grouped_grid.py` 新加的机制几乎一模一样，两边各写一份 |
| `project_scaffold_service.py:92` | 🟢 CONFIRMED | reuse | 新的 `find_project_root` 重新实现了 `project_tree_service.is_region()` 已有的项目根标记检查 |
| `workspace_breadcrumb.py:1046` | 🟢 CONFIRMED | reuse | "当前项目/工作区"标签解析逻辑在同一个类里被复制了至少 4 遍 |
| `workspace_breadcrumb.py:1071` | 🟢 CONFIRMED | reuse | "最近工作区"取值-过滤-截断逻辑复制了 3 遍以上 |
| `workspace_breadcrumb.py:788` | 🟢 CONFIRMED | duplication | 3 个 panel builder 各自重新推导 project/folder 值，其实已有 `_variant_values` helper 可以共用 |
| `project_scaffold_service.py:150` | 🟢 CONFIRMED | duplication | `append_survey_structure` 和 `create_survey_project` 的 `create_nodes` 闭包逐字节重复 |
| `new_survey_project_dialog.py:532` | 🟡 CONFIRMED | duplication | 对话框自己重新实现了一套校验规则，但只做浅层检查，和服务端 `check_conflicts`（递归检查）口径可能对不上 |
| `monitor_panel.py:670` | 🟢 CONFIRMED | duplication | "遮罩直到首批缩略图解码完"这套状态机在 `monitor_panel` 和 `uid_grouped_grid` 各写一份，无共享 |
| `project_tree_view.py:1909` | 🟢 CONFIRMED | efficiency | 每次打开项目树页面，`on_activate()` 把整套跨工作区汇总刷新跑了两遍 |
| `workspace_breadcrumb.py:3478` | 🟢 CONFIRMED | efficiency | 每次顶栏刷新（每次切页都触发）都无条件重建一个 11 项的设计选择菜单 |
| `workspace_breadcrumb.py:42` / `:411` | 🟡 CONFIRMED | 复杂度/altitude | 顶栏定位切换器的 **11 套设计对比原型**全部随生产代码一起发布（+约1300行），包括一个可在正式环境切换的"切换设计"菜单 |
| `project_scaffold_service.py:289` | 🟢 CONFIRMED | 违反"旧代码保留"约定 | 新建项目的按站点建目录逻辑整段替换，旧实现被删而非注释保留 |
| `main.py:332` | 🟢 CONFIRMED | 违反"旧代码保留"约定 | 窗口定位策略重写，旧实现被删而非注释保留 |
| `collab_view.py:1150` | 🟢 CONFIRMED | 违反"旧代码保留"约定 | 团队码按钮文案逻辑被折叠进新方法，旧行没有注释保留 |
| `uid_grouped_grid.py:654` | 🟢 CONFIRMED | i18n | 新增中文字符串没走 `tr()`，同文件其它文案都走了 |

图例：🔴 高优先级功能性 bug　🟡 中优先级　🟢 低优先级/代码卫生

**核实说明**：以上均经过独立 verifier agent 复核（CONFIRMED = 直接读代码证实；PLAUSIBLE = 场景真实存在但可能是有意取舍，未跑真机/无法完全排除）。完整推理过程（含引用的代码行）在 workflow 输出里，未收进本文档以控制篇幅。

---

## 二、UI 一致性审查（16 条确认，15 条已自动修复）

范围：本次 diff 涉及的界面文件（`theme.py`、`theme.qss`、`main_window.py`、`collab_view.py`、`project_tree_view.py`、`monitor_panel.py`、`new_survey_project_dialog.py`、`uid_grouped_grid.py`、`workspace_breadcrumb.py`）。**约束：不改布局/流程，只做颜色/间距/i18n/交互状态的局部一致性修补**（符合项目 UI 冻结规则）。

### 已自动修复（8 个文件）

| 文件 | 修了什么 |
|---|---|
| `app/config/theme.py` | 3 处硬编码颜色/圆角值 → 改用已有 design token（font_md/font_xs/radius_sm），全部注释保留旧值 |
| `app/widgets/workspace_breadcrumb.py` | 1 处内联 `setStyleSheet` 重复值（已被 objectName 规则覆盖）→ 删除内联样式（旧行注释保留）；2 处新增历史导航按钮补齐 hover/disabled QSS |
| `app/main_window.py` | 4 处新增对话框文案 f-string → 改用 `tr().format()` |
| `app/widgets/uid_grouped_grid.py` | 1 处遗漏 `tr()` 的加载态文案 |
| `app/widgets/monitor_panel.py` | 1 处遗漏 `tr()` 的加载态文案（新增 tr import） |
| `app/views/project_tree_view.py` | 1 处 i18n 补 `tr()`；1 处 `QComboBox#ProjectFacetFilter` 补齐 `:disabled` 态 QSS |
| `app/widgets/new_survey_project_dialog.py` | 2 处 objectName 改用已定义 token（`SoftAction`→`Outline`，`GhostDanger`→`Danger`，两者原本无对应 QSS，静默退化成默认样式） |
| `app/views/collab_view.py` | 11 处团队码编辑 UI 新增文案补 `tr()`（新增 tr import） |

全部改动遵循"旧代码注释保留 + 署名标注"约定（`# polish: ... Sonnet 5 multi-agent review`）。

### 已核实，未修复（需要你拍板 1 条）

- **`workspace_breadcrumb.py:617`**（scenes 模式按钮缺 QSS）：verifier 发现这套按钮属于"11 套设计对比原型"里的一个独立实验变体（`09 三场景启动器`），和另一个已加色的变体（classic 模式菜单）是两套互斥设计，把配色抄过去属于跨变体的设计决策，不算"补齐同一模式内已建立的模式"——按项目"设计评审在前"规则，这个不该我自动改，留给你决定。

### 验证

- `py_compile` + `ruff --select=F821`：8 个文件全部通过。
- 抽测相关测试：`test_workspace_breadcrumb.py`（43 passed）、`test_collab_view.py`（27 passed）、`test_uid_grouped_grid.py`（34 passed）、`test_monitor_panel_thumb_worker.py`（6 passed）、`test_project_tree_view.py`（65 passed，首次跑撞到已知的 Qt 事件循环偶发 coredump，重跑即过，和本次改动无关）。
- `test_new_survey_project_dialog.py` 5 个失败 + `test_main_window.py` 1 个失败：**核实为既有 bug**（分别对应上表的"默认层级名为空"和一个无关的截图设置迁移问题），不是这次自动修复引入的——已用 `git diff` 逐行核对，本次改动只涉及 objectName/tr() 替换，未碰校验逻辑。

---

## 三、死按键扫描（96 文件全覆盖，11 个确认死按键）

### 高优先级 —— 用户看得到、点得了、但没反应

| 文件:行 | 控件 | 问题 |
|---|---|---|
| `workspace_breadcrumb.py:1232` | **"进入照片工作台"**（Primary 主按钮，工作区激活时可点） | 唯一的 click 连接只是 `menu.close()`，同一段代码里的兄弟按钮（追加/设置/管理）都有真实跳转，就它没有——点了等于白点，只是把下拉菜单关掉 |
| `taxon_card_panel.py:176` | "来源"下拉框（原始库/WoRMS库） | 有 tooltip、可交互，但从未被读取（没有 `currentIndexChanged`/`currentText()` 调用），选哪个都不影响任何数据流 |

### 低优先级 —— 当前不可见/未接入生产界面（代码卫生问题，不影响现在的使用体验）

| 文件:行 | 控件 | 状态 |
|---|---|---|
| `main_window.py:337` | "归档" `QAction`（`self._btn_compress`） | 从未加入任何菜单/工具栏，用户根本看不到它，纯遗留代码 |
| `main_window.py:547,549` | `_btn_compose` / `_btn_organize` | 创建后立刻 `.hide()`，从未 show 过，旁边注释说"会被读取"但实际读取代码不存在 |
| `workbench_dashboard.py:58,62,66,70,74,78` | `_WorkflowDashboard` 整个类的 6 个按钮 | **整个类从未被实例化**（repo 里唯一引用是一处被注释掉的 import），这是一整块被替换下来但没删的旧组件 |

**说明**：这次没有修复任何一个死按键——接线涉及具体业务逻辑（比如"进入照片工作台"到底该跳去哪、来源下拉框该不该影响 WoRMS 填充流程），不是局部美化范畴，按规矩需要你先定设计再动手。已核实排除 1 个误报（`monitor_panel.py` 的"选目录"菜单项确实永久禁用，但带 tooltip 主动说明"请从项目树切换工作目录"，是有意为之的设计，不算死按键）。

---

## 四、遗留问题：CRLF 噪音

82 个文件（`git diff HEAD` 里显示但 `git diff HEAD -b` 一比对就消失）纯粹是换行符从 LF 被翻成 CRLF，和历史记忆里的 "parallel-codex-editing-hazard" 一致。本次审查/修复**完全没碰这些文件**。要不要统一换行符、要不要顺手在这次一起提交，等你决定。
