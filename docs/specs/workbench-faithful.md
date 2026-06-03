# 工作台忠实还原 SPEC（对照真实 web，逐控件）

> 真实 web 在 `http://127.0.0.1:3000/`（已跑）。styles.css/app.js 是样式与结构真身。**照它 1:1 还原，别自创**。
> 控件清单来自真实 DOM 抓取（2026-06-03）。每一项都要在 PyQt6 出现且功能通。

## 整体布局（workspace-body--three-col）
```
┌─ topbar ────────────────────────────────────────────────────────┐
│ [标本影像管理] 项目▾ | 照片工作区 项目总览 标签打印 WoRMS分类库     │
│   内置分类库 坐标工具 项目汇总 配置 | +新建项目 +打开工作区 智能压缩 ⚙ 🎬Helicon│
├──────────┬──────────────────────┬──────────────────┬────────────┤
│ sidebar  │ ① capture-workbench  │ ② 成果内容        │ ③ right-panel│
│ 标本列表  │ 监控流+分组+动作      │ 合成后/压缩后结果  │ 命名+分类+元数据│
└──────────┴──────────────────────┴──────────────────┴────────────┘
```
真实 class 名（照搬，QSS objectName 对齐）：`topbar/topbar-brand/topbar-project-switcher/topbar-actions`、`sidebar/sidebar-new-specimen-btn/sidebar-search`、`collab-status`、`main-content/workspace-header/workspace-body--three-col`、`capture-workbench/panel-tools/capture-stream-header/capture-stream/capture-main-actions`、`results-column/result-group/result-gallery`、`right-panel/panel-card/naming-fields/naming-preview/taxon-columns(col-latin/col-cn)/meta-score-ring/meta-fields`。

## topbar（逐控件）
- 左：`标本影像管理` 品牌 + 项目切换 `项目名 ▾`。
- 中：**8 个页签**（当前页高亮）：`照片工作区` `项目总览` `标签打印` `WoRMS 分类库` `内置分类库` `坐标工具` `项目汇总` `配置`。
- 右：`+ 新建项目` `+ 打开工作区` `智能压缩` `⚙` `🎬 Helicon`。

## sidebar
`🧬 + 新增标本唯一编号` 按钮 → 搜索框 → `已有标本唯一编号` 标题 + 标本列表（每条:UID mono + 拉丁斜体 + 标签 激活⚡/保存方式/成果数 + 激活/去激活）→ 底部 `collab-status`（分享地址 / 匿名·设备 / 成员 / 同步编号 / 协作管理）。

## ① capture-workbench
- header：`拍照工作台` + 项目 tag + `Helicon OK` 状态。
- `collab-status-bar`（协作内联条）。
- 批次条：`当前照片批次` UID + `压缩组·N合成片` + 状态 `拍摄中⚡`。
- 阶段 pills：`拍摄中 / 已拍完 / 整理中 / 完成`。
- 两个提示：`自动合成 提交后后台执行` / `手动 Helicon 选中本组原片拖入外部软件`。
- 工具行：`⚙ 项目设置` `↻ 刷新目录` `+ 添加照片` `📁 选目录`。
- `▸ 分组工具` 折叠开关 + `拖入所选 JPG + TIFF 补处理`。
- `capture-stream-header`：`刚写入目录 单击可选中` + `全选` `清除` `🗑 删除` `↩ 撤销归属`。
- `capture-stream`：文件卡网格（缩略图 + 角标徽章 raw/stacked/compressed/jpg/archived + 文件名 + 归属 pill）。
- `capture-main-actions`：`⚡ 合成` `合成+整理` `🗜 整理` `⋯ 更多 ▾`；`capture-target-name`（目标标本）。

## ② 成果内容（results-column）—— 之前漏了，必须有
- 列标题 `成果内容`。
- **合成后结果**：`Helicon 输出成片` + `N 项` + `result-gallery`（空态 `暂无合成结果`）。
- **压缩后结果**：`无损压缩归档` + `N 项` + gallery（空态）。

## ③ right-panel（可折叠 panel-resizer/panel-collapse）
**panel-card 照片编号**（naming）：
- header `照片编号` + `💾 保存` + 折叠 `▾`。
- `naming-fields`：`地区` `样地` `站位` `物种拼音缩写编号` `保存方式`（按钮组 `T95E D95E D75E T75E D79 T79 T100`）`成果序号`（auto 虚线）`采集日期` `拍照日期`。
- `拍照备注（可选）` 输入。
- `naming-preview`：UID + 成果编号（mono）。
- `naming-dup-warn`（编号重复警告）。
- `naming-action-row`：`📌 添加到侧栏` 等。
**taxon-card 分类标签**：header + `编辑/原始库/WoRMS库` 切换 + `taxon-columns`（col-latin 拉丁 / col-cn 中文）4 级：`类群 目 科 属 物种`。**中文字段不自动填**。
**备注标签** card。
**Metadata** card：`meta-score-ring`（完整度环）+ `meta-fields`：`鉴定人 采集人 拍摄人 站位经度 站位纬度 采集地理区 拍摄位置` + `📍 地图` 按钮。

## 实现要求
- 用已建后端服务（monitor/grouping/organize/helicon/archive/activation/naming），功能层不改。红线不变（TIFF永不删/删JPG默认关/中文不自动填/激活互斥）。
- 样式精确取自 `styles.css`（间距/圆角/配色/字体），QSS 还原，不靠猜。
- **自验**：实现后开 Qt app 截图 vs 真实 web（127.0.0.1:3000）同区并排，逐控件核对"有没有/位不位/像不像"。
