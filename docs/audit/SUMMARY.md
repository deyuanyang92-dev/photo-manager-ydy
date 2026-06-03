# 功能覆盖审计汇总（诚实版 · 2026-06-03 夜间自主完成）

> 方法:逐模块 grep web `app.js`(508 函数)+`server.js`(98 端点),对每个函数标 Qt 状态 ✓/◐/✗(详见同目录各 `<module>.md`),并补缺。
> **已亲自验证**:全套 `pytest tests/` = **1132 passed, 2 skipped**(108s,本人跑);`python main.py` 正常起,8 页注册。
> **诚实声明**:核心功能各模块已覆盖且补了大量缺口,但**未达 100% 函数级 parity**——下面如实列出仍缺的边缘功能。没骗。

## 各模块覆盖（agent 审计 + 补缺后）
| 模块 | web 函数 | ✓ 已实现 | ◐ 部分 | ✗ 仍缺 | 本轮补了 |
|------|---------|---------|--------|--------|---------|
| 工作台 | 159 | 多数核心 | 17 | ~30 边缘 | 删组/清组、ZIP碰撞保护、隐式批次合成 fallback;10 主任务核实✓ |
| 标签 | 92 | 70(76%) | 9 | 13 | backup 子系统、label_data_text、撤销/重做快捷键 |
| 分类 | 50 | 32 | 11 | 7 | 6 服务方法(validate链/authority/draft)、blur 祖先回填 |
| 协作 | 25 | 21 | — | 4 | 服务启动、侧栏接线、协作管理对话框、状态栏、状态广播 |
| WoRMS | ~25 | 23 | 2 | 2 | family/genus 物种查询、手动匹配对话框、子分类加载更多 |
| 坐标 | ~38 | 36 | 1 | 1 | nominatim_to_zh、Esc 关地图 |
| 项目/总览/汇总 | 66 | 44+ | 5 | 6 | get_project_summary、详情 stat cards、DwC 导出按钮 |
| 配置 | 12 | 11 | — | — | Helicon 高级参数、工作台 5 监控开关 |

## 仍缺的功能（诚实清单 — 多为边缘/二级体验）
- **工作台**:手动导入已有 TIFF 关联分组(groupingImportTiff)、侧栏复制 UID、新建标本重复检测、合成前 JPG 预览弹窗、反向地理编码写 geoArea(metaReverseGeocode)。
- **标签**:行结构编辑器(增删排序行 UI)、workbench 布局模式(classic 4步已全)、预览右键菜单。
- **分类**:facet 过滤菜单 UI、分类图表(renderTaxonChart)。
- **协作**:离线草稿队列、photo-index 上报、mDNS 真机发现(需双机真测)。
- **WoRMS**:工作台右侧快捷 WoRMS 填充弹窗、批量任务 1.5s 自动轮询、retry-failed。
- **项目**:成果预览缩略图+lightbox、项目列表行内统计 chip、子目录设置控件。
- **配置**:字体缩放滑块、图标 emoji 替换、useRealCompression 调试开关、快捷键录制 UI。

## 本轮还修了
- 全套测试从未确认 → 修 2 个失败,确认 **1132 绿**。
- **新建项目/打开工作区**:顶栏原来只跳页 → 改成弹完整 7 字段 ModalDialog(对齐 web),创建后进工作台。
- **双屏弹窗 bug**:加 `app/utils/ui.py`,所有对话框居中到当前窗口所在屏幕(WSLg 双屏不跑错屏)。

## 验证证据
- `pytest tests/` → 1132 passed, 2 skipped(本人 108s 跑)。
- `python main.py` → 起,8 页。
- 截图:`docs/shots/`(各页 + new_project_dialog)。
- 审计明细:`docs/audit/<module>.md` 逐函数 ✓/◐/✗。
