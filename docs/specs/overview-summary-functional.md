# 项目总览 & 项目汇总 功能规格

> Oracle: app.js (web prototype) + server.js project management endpoints.
> 本文枚举两页完整功能、每条标注 web Oracle 行号，供 v3 GUI 实现对照。

---

## 一、项目总览 (OverviewView)

### Oracle

| 位置 | 功能 |
|------|------|
| `app.js:13856-14026` | `renderOverview()` — 两分支：项目列表 / 项目详情 |
| `app.js:13900-13943` | 项目列表表格渲染 |
| `app.js:13905-13914` | 行内统计（标本数/成片/待处理 JPG）懒加载 |
| `app.js:13944-14023` | 项目详情分支（stat cards + 成果预览 + 标本表）|
| `server.js:2085-2465` | 项目管理端点（user-projects CRUD / project-ensure / enter-workspace）|
| `server.js:2520-2972` | 数据双写逻辑（specimens → SQLite） |

### 功能清单

#### 1.1 顶部操作栏（overview-header-actions）

| # | 功能 | web Oracle | v3 实现位置 |
|---|------|-----------|-------------|
| F01 | `h2 "项目总览"` 标题 | `app.js:13863` | `OverviewView._title_lbl` |
| F02 | `+ 新建项目` 按钮（Primary 样式）| `app.js:13864-13868` | `OverviewView._btn_new` |
| F03 | `+ 打开工作区` 按钮（Outline 样式）| `app.js:13869-13873` | `OverviewView._btn_open` |

#### 1.2 时间筛选栏（photo-toolbar）

| # | 功能 | web Oracle | v3 实现位置 |
|---|------|-----------|-------------|
| F04 | `时间筛选` 标签 | `app.js:13879` | `filter_lbl` |
| F05 | `全部` 按钮（默认选中）| `app.js:13880-13881` | `OverviewView._btn_all` |
| F06 | 年份按钮（动态从项目数据生成，不硬编码）| `app.js:13882-13886` | `OverviewView._sync_year_buttons()` |
| F07 | 年份筛选互斥切换，过滤表格行 | `app.js:13881-13886` | `OverviewView._set_year_filter()` |

#### 1.3 项目列表表格（specimen-table）

| # | 功能 | web Oracle | v3 实现位置 |
|---|------|-----------|-------------|
| F08 | 6 列：项目名称 / 磁盘目录 / 时间 / 地点 / 负责人 / 操作 | `app.js:13894-13897` | `OverviewView._table` (QTableWidget) |
| F09 | 项目名称加粗（font-weight:600）+ 年份附加 | `app.js:13903` | `name_item.setFont(_bold_font())` |
| F10 | 行内小统计（N 标本 · N 成片 · N 待处理）懒加载 | `app.js:13905-13914` | *v3 未实现：真实统计需异步 `/api/project/summary`* |
| F11 | 磁盘目录等宽字体（monospace）+ tooltip | `app.js:13916-13917` | `dir_item.setFont(_mono_font())` |
| F12 | 时间列等宽字体 | `app.js:13918` | `date_item.setFont(_mono_font())` |
| F13 | `进入工作区` 操作按钮（Primary）| `app.js:13922-13929` | `enter_btn` in `_make_action_cell()` |
| F14 | `详情` 操作按钮（Outline）| `app.js:13930-13936` | `detail_btn` in `_make_action_cell()` |
| F15 | 状态栏显示「共 N 个项目」| — | `OverviewView._status_lbl` |

#### 1.4 进入工作区（enterWorkspaceForProject）

| # | 功能 | web Oracle | v3 实现位置 |
|---|------|-----------|-------------|
| F16 | 点击「进入工作区」→ 设 `ctx.current_project_dir` | `app.js:13924-13929` | `OverviewView._on_enter_workspace()` |
| F17 | 无目录项目点击时提示（演示项目警告）| `app.js:13928` | `QMessageBox.information(...)` |
| F18 | 发射 `enter_workspace_requested(directory)` 信号 | 设计新增 | `OverviewView.enter_workspace_requested` pyqtSignal |
| F19 | 通知 MainWindow 切换到 workbench 视图 | — | `main_win.navigate_to("workbench")` |

#### 1.5 新建项目（projectModalOpen）

| # | 功能 | web Oracle | v3 实现位置 |
|---|------|-----------|-------------|
| F20 | 点击「新建项目」→ 打开 `_NewProjectDialog` | `app.js:13865-13867` | `OverviewView._on_new_project()` |
| F21 | 表单字段：项目名称 / 磁盘目录（浏览…）/ 地点 / 负责人 | web modal 字段 | `_NewProjectDialog._form` |
| F22 | 浏览目录 → 自动填充项目名称（目录名）| — | `_NewProjectDialog._browse()` |
| F23 | 确认后调 `create_project()` 创建子目录结构 | `server.js:2085` | `project_service.create_project()` → `ensure_project_dirs()` |
| F24 | 创建 `incoming-jpg/` `results/` `_data/` 子目录 | `server.js:ensureProjectDirs` | `project_service.ensure_project_dirs()` |
| F25 | 去重（同目录不重复加入）→ 持久化 `user_projects.json` | — | `_save_projects()` |
| F26 | 刷新表格 | — | `self._load_projects()` |

#### 1.6 打开工作区（openWorkspaceModal）

| # | 功能 | web Oracle | v3 实现位置 |
|---|------|-----------|-------------|
| F27 | 点击「打开工作区」→ `QFileDialog.getExistingDirectory` | `app.js:13869-13873` | `OverviewView._on_open_workspace()` |
| F28 | 调 `open_project()` → 注册路径到安全白名单 | `server.js:registerAllowedDir` | `project_service.open_project()` → `default_registry.register_root()` |
| F29 | 自动创建缺失子目录 | — | `ensure_project_dirs()` |
| F30 | 持久化 + 刷新表格 | — | `_save_projects()` + `_load_projects()` |

#### 1.7 项目详情弹窗

| # | 功能 | web Oracle | v3 实现位置 |
|---|------|-----------|-------------|
| F31 | 点击「详情」→ `_ProjectDetailDialog` | `app.js:13930-13936` | `OverviewView._on_detail()` |
| F32 | 显示：项目名/年份、地点/时间、磁盘目录（monospace）、负责人、ID | `app.js:13944-14023` | `_ProjectDetailDialog.__init__()` |

---

## 二、项目汇总 (SummaryView)

### Oracle

| 位置 | 功能 |
|------|------|
| `app.js:17841-18148` | `renderSummaryPage()` — 顶部控制栏 + 字段选择面板 + 汇总表格 |
| `app.js:17843-17851` | `SUMMARY_DEFAULT_COLS` — 25 个默认可见列键 |
| `app.js:17892-17927` | `ALL_COLS` — 34 列完整定义（key / label / getter）|
| `app.js:18150-18173` | `exportProjectsExcel()` — 调后端 `/api/export/specimens-excel` |
| `app.js:18175-18268` | `exportSummaryCsv()` — 客户端按可见列生成 CSV |

### 功能清单

#### 2.1 顶部控制栏

| # | 功能 | web Oracle | v3 实现位置 |
|---|------|-----------|-------------|
| S01 | `h2 "项目汇总"` 标题（16px bold）| `app.js:17942-17943` | `SummaryView` title QLabel |
| S02 | 项目筛选下拉（全部项目 + 每个用户项目）| `app.js:17945-17957` | `SummaryView._filter_combo` QComboBox |
| S03 | `⚙ 字段` 按钮（toggle 字段选择面板）| `app.js:17959-17962` | `SummaryView._btn_cols` |
| S04 | `⬇ Excel` 按钮（Primary 样式）+ tooltip | `app.js:17965-17968` | `SummaryView._btn_excel` |
| S05 | `⬇ CSV` 按钮（Outline 样式）+ tooltip | `app.js:17970-17973` | `SummaryView._btn_csv` |
| S06 | 保存目录输入框（placeholder "保存目录，如 N:\research"）| `app.js:17976-17980` | `SummaryView._dir_input` QLineEdit |
| S07 | `💾 保存` 按钮 → 保存 Excel 到指定目录 | `app.js:17982-18005` | `SummaryView._btn_save` |
| S08 | 保存结果消息（成功=青色 / 失败=红色）| `app.js:18007-18009` | `SummaryView._save_msg_lbl` |
| S09 | 行列计数标签（"N 条 · M 列"）| `app.js:18011-18012` | `SummaryView._count_lbl` |

#### 2.2 字段选择面板（⚙ 字段展开）

| # | 功能 | web Oracle | v3 实现位置 |
|---|------|-----------|-------------|
| S10 | 点击「⚙ 字段」展开/收起面板 | `app.js:17959-17962` | `SummaryView._toggle_picker()` |
| S11 | `全选` 按钮 → 全部 34 列勾选 | `app.js:18027-18031` | `_FieldPicker` "全选" btn |
| S12 | `重置默认` 按钮 → 恢复 25 个默认列 | `app.js:18032-18038` | `_FieldPicker` "重置默认" btn |
| S13 | `清空` 按钮 → 仅保留 uid | `app.js:18039-18043` | `_FieldPicker` "清空" btn |
| S14 | 每列一个 checkbox（34 个），勾选/取消即时更新表格 | `app.js:18049-18067` | `_FieldPicker._checks` dict |

#### 2.3 汇总数据表（sticky 首列 + 状态彩色）

| # | 功能 | web Oracle | v3 实现位置 |
|---|------|-----------|-------------|
| S15 | 34 列全量定义（镜像 `ALL_COLS`）| `app.js:17892-17927` | `summary_view.ALL_COLS` |
| S16 | 默认 25 个可见列（`_DEFAULT_KEYS`）| `app.js:17843-17851` | `summary_view._DEFAULT_KEYS` |
| S17 | 按 `summaryFilter` 项目筛选 | `app.js:18074-18080` | `SummaryView._filtered_specimens()` |
| S18 | `compStatus` 彩色：已合成=青 / 部分合成=黄 / 待合成=红 / 其他=灰 | `app.js:18121-18125` | `_rebuild_table()` item.setForeground() |
| S19 | `taxoOk` 彩色：✓=青 / ✗=灰 | `app.js:18127` | `_rebuild_table()` |
| S20 | `rna` 彩色：✓=黄 / ✗=灰 | `app.js:18128` | `_rebuild_table()` |
| S21 | 交替行色（奇数行 `#0f2830`，偶数透明）| `app.js:18115` | `setAlternatingRowColors(True)` |
| S22 | sticky 首列（monospace font-size:11px）| `app.js:18120` | QTableView 首列固定（待实现 frozen column）|
| S23 | 空数据提示（"暂无标本记录"）| `app.js:18134-18141` | `_rebuild_table()` 空状态行 |
| S24 | 支持列排序（QSortFilterProxyModel）| — | `SummaryView._proxy` |

#### 2.4 Excel 导出（34 列格式化）

| # | 功能 | web Oracle | v3 实现位置 |
|---|------|-----------|-------------|
| S25 | 点击「⬇ Excel」→ `QFileDialog.getSaveFileName` | `app.js:18152-18173` | `SummaryView._export_excel()` |
| S26 | 调 `export_service.export_excel(specs, path)` | — | `export_service.export_excel()` |
| S27 | 蓝色表头（`#2C5F8A`）+ 白色文字 + 加粗 | `server.js:595-721` | `export_service._apply_header_row()` |
| S28 | 冻结首行（freeze_panes = "A2"）| — | `export_service.export_excel()` |
| S29 | 自动筛选（auto_filter）| — | `export_service.export_excel()` |
| S30 | 交替行色（浅蓝 `#EEF3FA`）| — | `export_service.export_excel()` |
| S31 | 自动列宽（中文字符按 2 字节计算）| — | `export_service._auto_col_widths()` |
| S32 | 元数据 Sheet（导出日期 / 标本数 / 列数）| — | `export_service.export_excel()` |

#### 2.5 CSV 导出（按当前可见列 + 项目筛选）

| # | 功能 | web Oracle | v3 实现位置 |
|---|------|-----------|-------------|
| S33 | 点击「⬇ CSV」→ `QFileDialog.getSaveFileName` | `app.js:18175-18268` | `SummaryView._export_csv()` |
| S34 | 按当前 `_visible_keys` + `_project_filter` 过滤 | `app.js:18181-18183` | `_export_csv()` |
| S35 | UTF-8 BOM（`utf-8-sig`，Excel 可直接打开）| `app.js:18259` | `open(path, encoding="utf-8-sig")` |
| S36 | 中文列头 | `app.js:18235-18246` | `[c["label"] for c in vis_cols]` |

#### 2.6 保存到目录

| # | 功能 | web Oracle | v3 实现位置 |
|---|------|-----------|-------------|
| S37 | `💾 保存` → 验证目录非空 | `app.js:17985-17987` | `SummaryView._save_to_dir()` |
| S38 | 目录不存在时自动创建 | — | `target.mkdir(parents=True, exist_ok=True)` |
| S39 | 文件名 `标本数据_YYYY-MM-DD.xlsx` | `app.js:17996-17997` | `fname = f"标本数据_{date.today()}.xlsx"` |
| S40 | 保存中禁用按钮（防重复点击）| `app.js:17987` | `_btn_save.setEnabled(False)` |
| S41 | 保存结果消息（✓/✗ 颜色提示）| `app.js:17996-17999` | `_save_msg_lbl.setText(...)` |

---

## 三、数据层接口

| 接口 | 调用方 | 说明 |
|------|--------|------|
| `project_service.create_project(name, dir)` | OverviewView._on_new_project | 创建目录结构 + 返回项目 dict |
| `project_service.open_project(dir)` | OverviewView._on_open_workspace | 注册白名单 + 确保目录结构 |
| `project_service.list_projects(json_path)` | OverviewView._load_projects | 读取 user_projects.json |
| `export_service.export_excel(specs, path)` | SummaryView._export_excel / _save_to_dir | openpyxl 34 列格式化 |
| `ctx.get_db()` | SummaryView._load_data | SQLite 查 specimens + grouping |

---

## 四、v3 GUI vs Web Oracle 差异备注

1. **行内小统计（F10）**：web 调 `/api/project/summary` 懒加载；v3 未实现（需异步线程 + DB 查询），当前留空。
2. **项目详情 stat cards（真实数据）**：web 有 stat-card 区；v3 实现为 `_ProjectDetailDialog` 弹窗。
3. **项目详情成果预览区**：web 有 `renderProjectResultsSection()`（缩略图预览）；v3 未实现。
4. **sticky 首列**：web CSS `position:sticky;left:0`；PyQt `QTableView` 原生不支持 frozen column，当前未冻结。
5. **DwC 导出**：web 未在汇总页暴露；v3 `export_service.export_darwin_core()` 已实现，可在未来版本加到 UI。
6. **项目详情标本表**：web 有演示标本列表（`renderSpecimenTable`）；v3 不显示 demo 数据。
