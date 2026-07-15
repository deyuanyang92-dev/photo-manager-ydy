# 大规模性能架构：项目树 + 照片监控（2026-07-15）

> 用户确认的真实规模：**几千个项目起步**，每项目多断面/工作区，**总照片数百万级**，
> 团队长期使用。硬指标：**软件打开、加载不卡顿**。UI 冻结红线仍适用——树/监控的
> 视觉与交互不能因为内部换成虚拟化而跑样。

## 问题（codex 回归 + 自查确认）

| 位置 | 问题 | 实测 |
|---|---|---|
| `project_tree_view.py` on_activate | 打开时对每个项目同步 `scan_tree` + 递归建全部 `QTreeWidgetItem`，无懒展开 | 几千项目不可用 |
| `project_tree_service.py` | 内存 TTL 仅 2 秒且硬编码，冷启动每次全扫；无磁盘持久化 | 每次开软件重扫 |
| `monitor_service._list_pending_jpg_entries` | `os.listdir` + 每文件 2 次 `os.stat`（isfile + build_entry），全量重扫无增量 | 1万文件 **12.47 秒** |
| `monitor_panel._sync_cards` | 每张照片建一个重量级 `_FileCard(QFrame)`（含缩略图 QLabel+布局+5 信号），无视口虚拟化 | 2000 张 **1.13 秒** 建控件 |

已有但没用上的基础设施：`workspace_index_cache` 表（每工作区统计，零调用者）、
`thumbnail_disk_cache`（可仿写磁盘缓存 pattern）、`uid_grouped_grid`（QListView+model
虚拟化的现成范例）。

## 用户已拍板的方向

- **项目树 = 方案 A**：秒开顶层项目列表；点开某项目才在**后台线程**扫它下一层；
  扫过的结果**落盘缓存**（关软件重开也认），缓存没过期不重扫。
- **三个参数进设置页可调**：`缓存有效期`、`扫描深度 max_depth`、`自动扫描开关`。

## 分阶段实现

### 阶段 1（本次落地）——服务层，纯逻辑，可测，零 UI 风险

**1a. `monitor_service` 扫描 os.scandir 化 + 输出不变**
`_list_pending_jpg_entries` / `_list_tiff_entries` 用 `os.scandir` 替 `os.listdir`——
`de.is_file()` 用 readdir 带回的 d_type（不触发 syscall），`de.stat()` 只 stat 一次，
把每文件 **2 次 stat（isfile + build_entry）压到 1 次**。用户真实环境是 WSL2 访问
/mnt/n 的 drvfs，每次 stat 跨 WSL/Windows 边界极慢，2→1 在那里接近 2x（本机 ext4 约
1.2x）。**输出 FileEntry 列表逐字段不变**（对拍测试锁住）。

**1b. `project_tree_service` 扫描缓存：可配置 TTL + 磁盘持久化**
- `_cache_get` 的 TTL 从硬编码 `2.0` 改为读 `AppSettings.project_scan_cache_ttl_seconds`
  （缺省回落一个合理默认；纯读、无副作用）。
- 新增 `app/utils/project_scan_disk_cache.py`（仿 `thumbnail_disk_cache`）：把 `scan_tree`
  结果按 `(resolved_dir, 目录 mtime_ns, max_depth)` 存 JSON 到 `data/cache/project_scan/`，
  带 TTL；命中且未过期直接返回，不碰磁盘。mtime 变化自动失效（key 里含 mtime）。

**1c. 三个设置项 + 设置页控件**
`AppSettings` 加三个 property（照抄 `jxl_concurrency` 的 int-clamp / `performance_mode`
的 bool idiom）：
- `project_scan_cache_ttl_seconds`（int，默认 300，范围 [0, 86400]，0=每次都扫）
- `project_scan_max_depth`（int，默认 6，范围 [1, 12]）
- `project_tree_auto_scan_enabled`（bool，默认 True）

设置页「项目树」分组加一个 QSpinBox + 一个 QSpinBox + 一个 QCheckBox（照抄
`settings_tabs.py` 现有 spin/checkbox 的 create+wire+persist pattern，标签走 `tr()`）。
`_register_projects_from_scan` 的 `max_depth` 默认改为读该设置；`on_activate` 里的
自动扫描/恢复受 `project_tree_auto_scan_enabled` 控制。

### 阶段 2（下一轮单独做，本 spec 记录不赶工）——UI 虚拟化

**2a. 项目树懒展开**：`_build_item` 只建顶层项目节点，给每个可展开节点挂一个占位
子项；`itemExpanded` 信号触发时才在后台线程扫那一层、替换占位。需保持树的视觉/右键
菜单/双击进入行为逐一不变。

**2b. 监控面板 QWidget-per-card → QListView+model**：照 `uid_grouped_grid` 的
`QAbstractListModel + QStyledItemDelegate` 范式重写监控网格，只渲染视口内的格子。
必须逐一保留：勾选态 ✓、拖拽 QDrag(setUrls)、右键菜单（归属/取消归属/合成/整理/删除/
复制路径/在文件夹显示）、归属标签、活动组高亮、状态角标。phase badge 不在卡片上
（在 `MonitorPanel` 批次条），不受影响。

**为什么阶段 2 单独做**：`monitor_panel.py` / `project_tree_view.py` 都是数千行的
god-file，虚拟化改动面大、易踩 UI 冻结红线，值得单独一轮 TDD 认真做，而不是在长会话
末尾赶工。阶段 1 已经把两个"慢"里的服务层大头（12.47 秒那条）拿掉。

## 测试红线

- 1a：`_list_*_entries` 对拍测试——同一目录 scandir 版与旧 listdir 版输出的 FileEntry
  列表（name/path/kind/size/mtime/detail）逐字段相等。
- 1b：磁盘缓存命中/未命中/mtime 失效/TTL 过期四条路径各一测试；TTL=0 时每次都扫。
- 1c：三个设置 property 的 get/set/clamp/默认值测试；设置页控件 load/save 往返测试。
- 阶段 2 的红线（懒展开不改视觉、虚拟化保留全部交互）留到那一轮的 spec。

## 非目标（YAGNI）

- 不引入外部数据库/索引服务（codex 早前的 12 表 M1-M5 方案已被用户否决，见记忆
  `data-mgmt-lightweight-not-codex-spec`）。
- 不做跨机器共享索引。
- 不在本轮碰 `workspace_index_cache` 的接线（它是阶段 2 汇总面板加速的料，另算）。
