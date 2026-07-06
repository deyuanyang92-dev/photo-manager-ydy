# 标签打印 — 完整功能 SPEC（Opus 出 · Sonnet 全量实现 · Opus 逐功能核验）

> 目标:把 web 标签页**每个功能/逻辑**搬到 Qt,不是控件占位。Oracle=web `app.js`(行号见下)+ `label-core.js`(纯算法,已直译 `app/utils/label_core.py`)。
> 实现者**必读 web 对应代码逐函数还原行为**。活路径:`renderLabels()`(app.js:14351)固定渲染 Step1-4 经典版。

## 数据状态（labelState，app.js:513）
`projectIdx / selectedSpecimens(Set) / sampleTemplate / tissueTemplate / sampleSize / tissueSize / copies / labelEdits(每标本逐字段覆盖) / quickPrintUid / 自定义模板库(per bucket)`。Qt 用 ViewModel/dict 持有,持久化到 QSettings(对应 web localStorage keys:labelSampleTemplateKey/labelSampleSizeKey/labelTissueTemplateKey/labelTissueSizeKey/labelCustomTemplate/labelTissueCustomTemplate/labelSampleTemplateLibrary/labelTissueTemplateLibrary)。

## Step1 选择标本（app.js:14448-14566）
- 项目选择下拉(切项目清空选中)。
- 快捷:**全选 / 仅 RNA(只勾 R 前缀,hasRnaTissue) / 仅样品(只勾非 R) / 清空**——逐个实现,行为对齐 14496-14520。
- 标本网格:每项 checkbox + uniqueId(mono) + 物种名 + **R 前缀加 🧬 角标**(transcriptome)。选中态高亮。
- **双桶汇总**(renderLabelBucketSummary 14412):样品瓶卡(N个 + 默认模板名 + 尺寸) + RNAlater 组织管卡(R 前缀子集 N个;无则空态"选中标本无 R 前缀")。

## Step2 选择模版 + **模板库 CRUD（核心,缺最多;app.js:14569-14900+）**
- **内置模板**(labelTemplates:标准/紧凑/详细)每桶可选。
- **自定义模板库**(per bucket,命名多模板):`readLabelTemplateLibrary/writeLabelTemplateLibrary/normalizeLibraryRecord/upsertLabelTemplateRecord/getLabelTemplateRecord/labelTemplateRecords`(14606-14660+)。功能:**新建自定义 / 命名 / 编辑 / 复制 / 删除 / 选用**;key 格式 `custom:<id>`,id=`createLabelTemplateId()`。
- **导入 JSON**(导入模板)/ **模板管理**(列表管理)/ 每模板**编辑**按钮。
- 旧单自定义模板**迁移**进库(labelTemplateMigration 14586)+ 备份(labelCustomBackup)。
- **纸张尺寸**:8 种(25×10/30×15/40×20/50×30/60×40/70×50/80×60/100×70)+ 自定义,per bucket 记忆(sampleSize/tissueSize)。
- 模板右键菜单(编辑/复制/删除/设默认)。

## Step3 编辑标签内容（WYSIWYG）
- QGraphicsScene 编辑(已有 `label_editor.py`):逐字段 contenteditable→QGraphicsTextItem;QR 自由拖(scene=mm);2mm 安全边距;QUndoStack。
- **labelEdits**:每标本逐字段覆盖值,存 labelState.labelEdits[idx],打印时套用(getLabelData 套 edits)。当前桶切换。
- 字段来自当前模板 fields;实时预览。

## Step4 纸张/份数/输出
- 纸张类型 + 份数(copies)。
- **createLabelPrintJob(bucket)**(app.js,搜 createLabelPrintJob):按桶聚合标本→模板→labelEdits→份数,算 grid 排版(label_core.calculate_grid),产 warnings(QR太小/字体溢出/边距/尺寸不匹配,label_core.validate_print_job)。
- **printLabels(bucket)**:QPrinter 先出 PDF 预览再 QPrintDialog。**两独立按钮:打印样品瓶 / 打印 RNAlater 组织管**,各自 disabled 当 count=0。
- 状态栏:模式名 + 选中 N + 样品 N + RNAlater N + 共 N 张 + 首条 warning。

## 批次打印（A4/A5 标签纸）
- 日常贴标签有两种入口：当前勾选立即打印；或先加入待打印批次，等积累到一张 A4/A5 后统一打印。
- 批次按 bucket 独立保存：样品瓶批次与 RNAlater 组织管批次不混排。切换 bucket 只切换当前批次视图。
- `加入批次` 使用当前勾选编号；样品瓶接收全部勾选，RNA签只接收 R 前缀标本。
- 批次去重且保持加入顺序；当前勾选清空或继续筛选，不影响已加入批次。
- `打印批次` 使用当前 bucket 的模板、标签尺寸、纸张、份数和 A4/A5 拼版参数生成 print job。
- 空批次不能因为“空白手写标签”设置而变成可打印批次；只有批次内已有编号时，才允许在末尾追加空白手写标签。
- `清空` 只清当前 bucket 的批次；切换/重载项目时清空批次，避免跨项目编号错打。

## 打印执行 Module
- `build_printer()` / `paint_jobs()` 保持为无状态底层 Adapter：只负责 QPrinter 页面设置和把 print job 绘制到设备。
- 应用级打印会话必须走 `LabelPrintExecutor` 类：负责 Windows 打印桥接、Qt 打印机选择、默认打印、错误弹窗和打印审计。
- View 层不应再展开“选择打印机 → 构建 QPrinter → paint_jobs → record_print_jobs”的完整流程；只负责构建 print job 和传入会话参数。
- 项目打印设置的默认 `quick_print_mode` 是 `direct`：点击工作台打印、标签页打印或打印批次时，默认直接发送到系统默认/指定打印机。
- 只有用户把 `点击打印` 改成 `dialog` 时，点击打印才打开打印窗口确认。

## 支撑函数（全部照 web 实现/复用 label_core.py）
`bucketSpecimens(R→两桶)` / `specimenToLabelData` / `hasRnaTissue/rnaPreservative` / `getActiveTemplate(bucket)` / `getLabelDims(bucket)` / QR 生成(纠错级 Q) / `uniqueSpecimenIndices`(去重一标本一张)。

## 实现要求
- **逐功能对 web 行为还原**(读 app.js 上述行号 + label-core.js)。接已有 `app/utils/label_core.py`/`app/services/label_service.py`/`app/widgets/label_editor.py`,缺的补。
- 红线:R 前缀双桶;QR 纠错 Q;2mm 边距;一标本一张去重。
- 布局可美化,但**上面每个功能必须真能用**。

## 核验清单（Opus 逐项 ✓,开 live web 3000 对照）
全选/仅RNA/仅样品/清空 · 🧬角标 · 双桶汇总数 · 内置模板选用 · **自定义模板新建/编辑/复制/删除/命名/选用** · 导入JSON · 模板管理 · 8纸张+自定义 · per桶尺寸记忆 · WYSIWYG编辑 · **labelEdits逐字段覆盖** · QR拖动+纠错Q · 2mm边距 · 份数 · createPrintJob排版+warnings · 打印样品瓶/打印RNAlater两按钮 · PDF预览 · 状态栏计数。
