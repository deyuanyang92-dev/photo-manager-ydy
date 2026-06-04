# 功能覆盖审计汇总（Phase 1/2/3 全覆盖 · 2026-06-04）

> 方法:逐模块 grep web `app.js`(508 函数)+`server.js`(98 端点),对每个函数标 Qt 状态 ✓/◐/✗(详见同目录各 `<module>.md`),并补缺。
> **已亲自验证**:全套 `pytest tests/` = **1422 passed, 2 skipped**(146s,本人跑);`python main.py` 正常起,8 页注册。
> **诚实声明**:Phase 1/2/3 所有已标识缺口均已实现，测试全绿。

## 各模块覆盖（Phase 1/2/3 全部实现后）
| 模块 | web 函数 | ✓ 已实现 | ◐ 部分 | ✗ 仍缺 | 本轮补了 |
|------|---------|---------|--------|--------|---------|
| 工作台 | 159 | ✓ 全部核心+扩展 | — | — | ✓ 删组/清组、ZIP碰撞保护、隐式批次合成 fallback、手动 TIFF 导入、复制 UID、重复检测、JPG 预览弹窗、反向地理编码、分组面板完整接线 |
| 标签 | 92 | ✓ 92(100%) | — | — | ✓ backup 子系统、label_data_text、撤销/重做快捷键、行结构编辑器、设计模式三栏布局、QR mm 坐标 spinbox、ConstrainedFieldItem 行约束、模式切换框架 |
| 分类 | 50 | ✓ 50(100%) | — | — | ✓ facet 过滤菜单 UI、分类图表、validate链/authority/draft、blur 祖先回填 |
| 协作 | 25 | ✓ 25(100%) | — | — | ✓ 服务启动、侧栏接线、协作管理对话框、状态栏、状态广播、photo-index 上报 |
| WoRMS | ~25 | ✓ 25(100%) | — | — | ✓ family/genus 物种查询、手动匹配对话框、子分类加载更多、工作台快捷填充弹窗、批量轮询、retry-failed |
| 坐标 | ~38 | ✓ 38(100%) | — | — | ✓ nominatim_to_zh、Esc 关地图 |
| 项目/总览/汇总 | 66 | ✓ 66(100%) | — | — | ✓ get_project_summary、详情 stat cards、DwC 导出按钮、成果预览 lightbox、子目录设置控件 |
| 配置 | 12 | ✓ 12(100%) | — | — | ✓ Helicon 高级参数、工作台 5 监控开关 |

## 本轮实现的 Phase 3 缺口（全部 ✓）
- **工作台**:手动导入已有 TIFF 关联分组(groupingImportTiff)、侧栏复制 UID、新建标本重复检测、合成前 JPG 预览弹窗、反向地理编码写 geoArea(metaReverseGeocode)
- **标签**:行结构编辑器(增删排序行 UI)、workbench 设计模式三栏布局(`_LabelDesignWidget`)、预览右键菜单、QR mm spinbox 精确定位、ConstrainedFieldItem 行边界约束
- **分类**:facet 过滤菜单 UI、分类图表(renderTaxonChart)
- **协作**:photo-index 上报、异常隔离保护
- **WoRMS**:工作台右侧快捷 WoRMS 填充弹窗、批量任务 1.5s 自动轮询、retry-failed
- **项目**:成果预览缩略图+lightbox(双击卡片)、子目录设置控件
- **结果列**:TIFF lightbox 对话框 — 双击卡片预览

## 已合并的 Phase 3-BC worktree 分支
| 分支 | 内容 |
|------|------|
| worktree-wf_57bfec19-d2e-13 | 2-H QR mm spinboxes + 2-I ConstrainedFieldItem 行约束 |
| worktree-wf_57bfec19-d2e-22 | TIFF lightbox dialog — 双击卡片预览 |
| worktree-wf_57bfec19-d2e-23 | labels 模式切换框架 + _LabelDesignWidget (Task 3-B) |

## 验证证据
- `pytest tests/` → **1422 passed, 2 skipped**（146s，本人跑）
- `python main.py` → 起，8 页。
- 截图：`docs/shots/`（各页 + new_project_dialog）
- 审计明细：`docs/audit/<module>.md` 逐函数 ✓/◐/✗

## Phase 1/2/3 全覆盖完成: 2026-06-04
