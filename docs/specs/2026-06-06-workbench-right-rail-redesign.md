# Spec — 工作台右栏照搬 web（三可折叠卡 + 拖宽/收起）

**Date:** 2026-06-06
**View:** `app/views/workbench_view.py` (主界面 工作台)
**Oracle:** `prototype-photo-gui/app.js` `renderRightPanel` (9073), `renderNamingCard`
(9147), `renderTaxonNotesCard` (9933), `renderMetaCard` (10203), `bindPanelResize`
(9112); state `rightPanelWidth/rightPanelCollapsed/collapsedCards` (472–474),
`TAXON_FIELDS` (818).

## Why

Qt 右栏与 web 偏离：分类字段被塞进元数据卡、整栏不可折叠/拖宽、合成参数(Helicon)
乱入右栏。用户要求"完整照搬 web"。目标是**结构与字段边界忠实于 web 右栏**，非像素级。

## 现状 vs 目标

| web `.right-panel` | Qt 现状 | 目标 |
|---|---|---|
| 整栏可拖宽 280–480、可整体收起 | 固定列 348–460、scroll、不可收起 | 改成可拖宽段 + 收起按钮 |
| 卡1 照片编号（可折叠） | NamingPanel（卡片，无折叠） | 包进 CollapsibleCard |
| 卡2 分类标签（**独立卡**） | ❌ 分类塞在元数据卡 | 新建 TaxonCardPanel |
| 卡3 元数据（采集/坐标/完整度 + ☰字段菜单） | MetadataPanel 混装分类/保存/备注 | 抽出分类，保留其余 |
| 无 Helicon 参数 | ❌ HeliconParamsPanel 在右栏 | 迁入分组工具弹窗 |

## 字段边界（忠实 web）

- **卡1 照片编号** = 现 `NamingPanel` 全部内容（地区/样地/站位/物种缩写/保存方式/序号/
  UID 预览/复制/📌添加侧栏）。不动内容，仅外包折叠头 + 💾保存挪到卡头。
- **卡2 分类标签** `TaxonCardPanel`（新），对齐 `renderTaxonNotesCard`：
  - 字段 = `TAXON_FIELDS`（5 级，sp↔cn↔label）：
    `taxon_group/taxon_group_cn 类群`、`order_name/order_cn 目`、`family/family_cn 科`、
    `genus/genus_cn 属`、`scientific_name/scientific_name_cn 物种`，外加 `identifier 鉴定人`、
    `notes 备注`。
  - 布局 = 三列 `级别 | 拉丁名 | 中名`，每行拉丁名输入接 autocomplete（复用
    `app/widgets/taxonomy_input.TaxonPopup` + `TaxonomyService.search/_candidates_for`）。
  - 上下级校验警告：调 `TaxonomyService.validate_taxonomy_chain`（已存在，taxonomy_service.py:547）；
    不匹配时卡内显示 ⚠ 警告行，字段改对后自动消失（纯展示，无 dismiss）。
  - 卡头 actions：`编辑`按钮（→ 分类编辑 modal，见下）、来源 select（原始库/WoRMS库，
    切 WoRMS 触发 `loadWormsTaxonomyCandidates` 等价加载）、`☰`字段显隐菜单、折叠 ▾/▸。
- **卡3 元数据** = 瘦身后的 `MetadataPanel`，对齐 `renderMetaCard`：保留 采集人/拍摄人/
  鉴定人、经纬度/地理区/拍摄位置、保存方式、备注/拍摄备注、完整度环；**移除 4 级分类小节**
  （已迁卡2）。卡头加 `☰`字段显隐菜单 + 折叠 ▾/▸。

> 注：`identifier 鉴定人` web 在卡2 与卡3 都出现，保持双现（同一字段，两处可编辑）。

## 组件设计

1. **`app/widgets/collapsible_card.py` — `CollapsibleCard(QFrame)`**（新）
   - API：`CollapsibleCard(title: str, *, collapsed=False)`；`body_layout()` 返回内容布局；
     `add_header_action(widget)` 往卡头右侧塞按钮；`set_collapsed(bool)`；信号
     `collapsed_changed = pyqtSignal(bool)`。
   - 卡头：标题 + actions 槽 + ▾/▸ 折叠按钮。折叠时仅留卡头。objectName `PanelCard`
     复用现有 QSS 卡样式。

2. **`app/widgets/taxon_card_panel.py` — `TaxonCardPanel(QWidget)`**（新）
   - 内含一个 CollapsibleCard("分类标签")。
   - 三列字段网格 + 行内 TaxonPopup autocomplete；输入即写 Specimen、联动过滤下级
     （mirror app.js 10047 `input` handler）。
   - 校验警告区、来源 select、☰菜单、编辑按钮（开 modal）。
   - 信号：`save_requested`、`taxon_changed`、`open_taxon_edit(sp)`。
   - 字段显隐状态：本地 dict `vis_levels{类群..备注}` + `vis_langs{cn,latin}`，对齐
     `taxonCardCtrl.visLevels/visLangs`（app.js 10003/10013）。

3. **`MetadataPanel` 瘦身**（改 `app/widgets/metadata_panel.py`）
   - 删除 `_section_label("分类（4 级自动补全）")` 段及 `_taxon_group/_order_name/_family/
     _genus/_scientific_name/_worms_quick_btn`（迁卡2）。其余字段、完整度环、保存逻辑不变。
   - 加 ☰字段显隐菜单（采集人/拍摄人/鉴定人/站位经纬度/采集地理区/拍摄位置），对齐
     `metaVisibleFields`（app.js 10229）。
   - **迁移注意**：原 worms 快捷查找/4级自动补全的 save 路径不可丢；taxon 写入仍走
     `_on_save_metadata` 同一持久化（卡2 的 save_requested 接到同一 handler）。

4. **分类编辑 modal**（新，对齐 `openTaxonEditModal` app.js 9933 区 + 1386 `分类编辑/添加 modal`）
   - 一次编辑五级拉丁+中名 + 鉴定人/备注；确定后写回 Specimen 并触发 save。
   - 可放 `app/widgets/taxon_edit_dialog.py`（QDialog）。

5. **右栏改造**（改 `workbench_view.py` 445–474）
   - 三卡：`NamingPanel`(包 CollapsibleCard) / `TaxonCardPanel` / `MetadataPanel`(包
     CollapsibleCard)，纵向堆叠于 scroll。
   - **整栏可拖宽**：右栏作为 outer `QSplitter` 段，min 280 / max 480（对齐 web
     bindPanelResize 280–480）。
   - **整栏可收起**：栏顶"收起命名/展开命名"按钮，收起时仅留窄条（对齐 rightPanelCollapsed）。
   - **折叠态/宽度持久化**：存 settings（对齐 web `workspaceState.collapsedCards/
     rightPanelWidth/rightPanelCollapsed`，app.js 2466）。

6. **Helicon 迁出右栏**（改 `workbench_view.py`）
   - `HeliconParamsPanel` 从 `right_lay` 删除，移入分组工具弹窗 `_build_grouping_dialog`
     （compose 触发处，web 把参数放合成流程 app.js 6698/6881）。
   - `_on_compose_requested` / free compose 仍调 `self._helicon_params.get_params()`，
     仅 parent 变更，**信号与逻辑不变**（红线：合成行为不可改）。

## 不改（红线 / UI 冻结边界外）

- UID 推导、保存方式迁移确认（applyStorageCorrection 等价）、Helicon 合成命令行参数、
  taxonomy 候选/校验算法 — 全部沿用现有 service，**仅重排 UI**。
- 不改其它 7 个页面。

## 测试（TDD，headless `QT_QPA_PLATFORM=offscreen`）

- `tests/test_collapsible_card.py`：折叠/展开切换、collapsed_changed 信号、卡头 action 注入。
- `tests/test_taxon_card_panel.py`：5 级字段 load/读取、autocomplete 候选弹出、
  validate_taxonomy_chain 不匹配显示警告 / 改对消失、来源切换、☰显隐。
- `tests/test_metadata_panel.py`（改）：确认分类字段已移除、其余字段 + 完整度环 + save 不回归。
- `tests/test_workbench_view.py`（改）：右栏含 3 卡、Helicon 不在右栏、栏可收起、
  compose 仍能取到 helicon params（防 Helicon 迁移回归）。
- 契约/不变量：compose 取参不变（红线）；metadata save 仍写 taxon 字段（迁移不丢字段）。

## 验收

右栏视觉与交互对齐 web 右栏截图：三可折叠卡（编号/分类标签/元数据）、可拖宽、可整体收起、
分类卡有三列拉丁/中名 + 校验 + ☰ + 编辑、元数据卡无分类、Helicon 不在右栏且合成正常。
