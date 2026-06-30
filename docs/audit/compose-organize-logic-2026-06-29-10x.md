# 合成与整理逻辑十轮复审

日期：2026-06-29

目标：把 `合成`、`合成+整理`、`自动归档`、外部 TIF 整理几个概念讲清楚，避免后续维护者把“激活编号”误解成无条件自动选择 JPG，也避免漏掉“自动归档开时允许自动取当前编号 JPG”的快速拍摄场景。

## 最终状态机

| 是否激活编号 | 是否手选 JPG | 是否有已有 TIFF | 用户动作 | 正确行为 |
|--------------|--------------|-----------------|----------|----------|
| 有 A | 有，至少 2 张 | 无 | `合成` | 只合成选中的 JPG，按 A 的下一个成果序号命名。 |
| 有 A | 有，至少 2 张 | 无 | `合成+整理` | 只合成选中的 JPG，然后等 ZIP worker 完成后再提示完成。 |
| 有 A | 无 | 无 | `合成` / `合成+整理`，自动归档关 | 不合成；提示先选中 JPG。 |
| 有 A | 无 | 无 | `合成` / `合成+整理`，自动归档开 | 自动取 A 下未占用 JPG，按 A 的下一个成果序号命名。 |
| 无 | 有，至少 2 张 | 无 | `合成` / `合成+整理` | 弹“合成输出”，用户选择目标编号或自由输出名。 |
| 无 | 有，至少 2 张 | 无 | 弹窗选择“归属到 B” | 写入 JPG 归属到 B，按 B 的下一个成果序号命名。 |
| 无 | 有，至少 2 张 | 无 | 弹窗选择“自由输出名 X” | 生成 `X.tif`；若整理，ZIP 为 `X.zip`。 |
| 任意 | 只有 1 张 JPG | 无 | `合成` | 不合成；Helicon 合成至少需要 2 张 JPG。 |
| 任意 | 有 JPG | 有 1 个 TIFF | `整理` | 直接整理已有 TIFF；可允许 1 张 JPG。 |
| 有 A | 不看当前勾选 | 之后出现外部 TIF | `自动归档` 开启 | 用 A 下未占用 JPG 与外部 TIF 整理归档；不运行 Helicon。 |
| 无 | 有手选 JPG | 之后出现外部 TIF | `自动归档` 开启 | 用手选 JPG 与外部 TIF 整理；不要求激活编号。 |
| 无 | 无手选 JPG | 之后出现外部 TIF | `自动归档` 开启 | 不整理；提示先选中对应 JPG。 |
| 有 A | 合成刚成功的一组 JPG | 新合成 TIFF | `自动归档` 开启 | 自动把刚合成这组源 JPG 打包 ZIP 并移入 `results/`。 |

## 十轮复审结果

| 轮次 | 检查对象 | 代码/文档证据 | 结论 |
|------|----------|---------------|------|
| 1 | 主工具栏按钮 | `app/widgets/monitor_panel.py`：`合成` tooltip 说明手选优先，自动归档开时可取激活编号 JPG。 | 文案已明确两个入口。 |
| 2 | 自动归档按钮 | `app/widgets/monitor_panel.py`：按钮文字为 `自动归档`，tooltip 说明“激活编号可自动取 JPG、合成后整理、外部 TIF 自动整理”。 | 自动开关是显式许可，不是隐藏猜测。 |
| 3 | 已激活但未选 JPG | `WorkbenchView._resolve_implicit_compose_target`：自动归档关返回 `None`；自动归档开返回 active UID target。 | 激活编号 + 自动归档才允许自动选择 JPG。 |
| 4 | 已激活且已选 JPG | `_resolve_implicit_compose_target` 返回 `_SelectedComposeTarget(uid=active_uid, assign_to_uid=True)`。 | 选中的 JPG 按激活编号归属和命名。 |
| 5 | 未激活且已选 JPG | `_prompt_selected_compose_target` 弹“合成输出”。 | 不再拦截为“请先激活编号”。 |
| 6 | 目标编号确认 | `_prompt_selected_compose_target` 对 UID 路径返回 `assign_to_uid=True`。 | 即使 UID 来自默认填充值，也会写入归属。 |
| 7 | 自由输出名 | `_SelectedComposeTarget(uid=ADHOC_GROUPING_UID, output_name=...)`；文档要求 TIF/ZIP 同 stem。 | 无编号时不静默生成 `1.tif` 作为手选输出。 |
| 8 | 数量门槛 | `_build_implicit_group` 小于 2 张返回 `None`；`_organise_jpgs_with_tiff(... allow_single_jpg=True)`。 | 合成至少 2 张；已有 TIFF 整理可 1 张。 |
| 9 | 外部 TIF 自动整理 | `_maybe_auto_process_new_tiff` 只处理 `_pending_tiff_paths` 里新出现的 TIF，并调用 `_organise_jpgs_with_tiff`。 | 有激活编号时用该编号 JPG；无激活但已手选 JPG 时用手选 JPG；不调用 Helicon。 |
| 10 | 完成提示 | `_implicit_compose_done` 和 `_maybe_auto_process_new_tiff` 都通过 `on_complete` 等归档 worker 回调。 | 不再启动 worker 就提示完成。 |

## 操作示例

### 示例 A：正常拍摄，已激活编号

1. 激活 `A`。
2. 选择 4 张 JPG。
3. 点 `合成+整理`。
4. 软件生成 `A-下一个序号-日期.tif`。
5. ZIP worker 完成后，才提示 `合成+整理完成`。

### 示例 B：忘记激活，但已经手选 JPG

1. 不激活任何编号。
2. 选择 4 张 JPG。
3. 点 `合成+整理`。
4. 弹“合成输出”。
5. 用户选“归属到 B”：软件写入这些 JPG 归属 B，并按 B 的下一个序号命名。
6. 用户选“自由输出名 X”：软件生成 `X.tif` 和 `X.zip`。

### 示例 C：已激活但没选 JPG，自动归档关

1. 激活 `A`。
2. 不选择 JPG。
3. 点 `合成` 或 `合成+整理`。
4. 软件只提示先选中 JPG。
5. 软件不能自动拿 A 下未占用 JPG 去合成。

### 示例 C2：已激活且自动归档开，没选 JPG

1. 激活 `A`。
2. 开启 `自动归档`。
3. 不选择 JPG。
4. 点 `合成` 或 `合成+整理`。
5. 软件自动取 A 下未占用 JPG，按 A 的下一个成果序号命名。

### 示例 D：外部 Helicon 已经生成 TIF

1. 激活 `A`。
2. 开启 `自动归档`。
3. 之后目录中出现一个外部 TIF。
4. 软件用 A 下未占用 JPG 和这个外部 TIF 做整理归档。
5. 软件不运行 Helicon，也不读取当前勾选的 JPG。

### 示例 D2：没有激活编号，但手选了对应 JPG

1. 不激活任何编号。
2. 在监控区选中这个外部 TIF 对应的 JPG。
3. 开启 `自动归档`。
4. 之后目录中出现一个外部 TIF。
5. 软件用手选 JPG 和这个外部 TIF 做整理，进入未归属任务或保留 TIF stem。

### 示例 E：自动归档下的合成后整理

1. 开启 `自动归档`。
2. 用户手选 JPG，或在已激活编号下不手选 JPG 直接点 `合成`。
3. 合成成功后，软件自动把该组源 JPG 打包 ZIP 并移入 `results/`。

## 回归锚点

- `tests/test_workbench_view.py::TestImplicitCompose`
- `tests/test_workbench_view.py::TestAutoOrganizeAfterCompose`
- `tests/test_monitor_panel.py::TestAutoCompressToggle`
- `tests/test_settings_view.py`

## 禁止回退

- 禁止恢复“有激活编号且未选 JPG 时，无需自动归档开关就自动处理该编号 JPG”。
- 禁止把外部 TIF 自动整理写成“自动合成”。无激活编号时，只有用户手选 JPG 才能自动整理外部 TIF，不能静默猜 JPG。
- 禁止把自动归档改回“只处理外部 TIF”，它还承担“激活编号 + 未选 JPG 时自动取图”的快速拍摄场景。
- 禁止在归档 worker 还没完成时提示 `合成+整理完成` 或 `外部 TIF 自动整理完成`。
