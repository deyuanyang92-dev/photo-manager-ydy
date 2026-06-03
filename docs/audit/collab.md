# 协作模块审计 — collab

审计日期：2026-06-03  
审计范围：web `app.js` 协作函数（25 个）vs Qt `collab_service.py` + `collab_view.py` + `specimen_sidebar.py`

---

## 覆盖表

| web 函数 | 行号 | ✓/✗/◐ | Qt 位置 / 缺口说明 |
|----------|------|--------|-------------------|
| `collabDevice()` | 725 | ✓ | 等价：`CollabService._hostname` + `_node_info()`；设备 ID 在 Qt 里从 socket.gethostname() 派生，无需 localStorage |
| `saveCollabDevice(patch)` | 738 | ✓ | Qt P2P 架构不需要手动保存设备身份 |
| `collabPayload(extra)` | 743 | ✓ | `CollabService._node_info()` 返回等价信息 |
| `loadCollabOfflineDrafts()` | 752 | ✗ | **未实现**。Qt 应用网络可靠性更高，但仍需离线草稿队列（P2P 任一节点离线都可能触发） |
| `saveCollabOfflineDrafts()` | 755 | ✗ | **未实现**，同上 |
| `collabTaskLabel(status)` | 3213 | ✓ | `collab_view.py` `_STATUS_LABEL` dict（8 个状态，完整） |
| `collabTaskClass(status)` | 3225 | ✓ | `collab_view.py` `_STATUS_COLOURS` dict（对应 CSS class） |
| `collabSyncTasks(projectDir)` | 3228 | ✓ | `CollabService._sync_all_peers()` via 5s QTimer；信号 `tasks_changed` 驱动刷新 |
| `collabRegisterDevice(projectDir)` | 3248 | ✓ | `CollabDiscoveryThread` 通过 zeroconf 注册 mDNS 服务，等价于设备登记 |
| `collabCreateTaskSync(uid, specimen)` | 3256 | ✓ | `CollabService.create_task(uid, assignee, device_id)` —— 本地 409 + 远端广播均有 |
| `collabMarkOfflineDraft(uid, specimen)` | 3281 | ✗ | **未实现**；网络失败时应把任务标记为离线草稿 |
| `collabUpdateTaskStatus(uid, status)` | 3301 | ◐ | `TaskStore.update_status()` 有本地逻辑；**缺**：广播 POST 到在线节点 |
| `collabPostPhotoIndex(uid, kind)` | 3317 | ✗ | **未实现**。Helicon 完成合成、归档完成时应上报 jpg/tiff/zip 计数 |
| `collabTaskEntries()` | 3329 | ✓ | `TaskStore.all()` |
| `collabAssignTask(uid)` | 3334 | ✗ | **UI 缺口**：CollabView 任务表里没有"分配"按钮 |
| `collabVoidTask(uid)` | 3354 | ✗ | **UI 缺口**：管理员"作废"按钮未实现 |
| `collabResolveConflict(uid)` | 3375 | ✗ | **UI 缺口**：conflict 状态行没有"处理冲突"按钮 |
| `collabRetryOfflineDrafts()` | 3398 | ✗ | **未实现**；离线草稿重试逻辑 |
| `renderCollabStatusBar(project)` | 5004 | ◐ | `specimen_sidebar.py` 有 collab strip（5 个 Label + 1 按钮），**但全部静态**——标签从未更新，按钮无信号 |
| `renderCollabShareModal()` | 5058 | ✗ | **未实现**；显示局域网地址 + 复制按钮的分享弹窗 |
| `renderCollabManagerModal()` | 5099 | ◐ | `CollabView` 有任务表 + 设备列表 + 手动 IP，但**缺** assign/void/resolve-conflict 行操作按钮 |
| `collabSyncTasks` 轮询触发 | — | ✗ | **根本缺口**：`CollabService` 从未在 `AppContext` 中实例化/启动；所有 UI 都是孤立静态 |
| 状态栏 `set_status_collab` 更新 | — | ✗ | `MainWindow.set_status_collab()` 存在但**从未被调用** |
| 协作管理按钮动作 | sidebar 185 | ✗ | `collab_mgr_btn` `clicked` 无连接 |
| 分享地址显示 | sidebar 165 | ✗ | `_collab_addr` 从未从服务读取地址 |

---

## 补缺实施清单（本次已完成）

1. **AppContext** — 加 `collab_service: Optional[CollabService]`，在 `main.py` 启动后调用 `ctx.collab_service.start()`
2. **SpecimenSidebar collab strip 接线** — `update_collab_status(service)` 方法；`CollabService` 信号驱动 addr/device/members/sync 刷新
3. **"协作管理"按钮** — 连接信号，WorkbenchView 中接收并打开 `CollabManagerDialog`
4. **CollabView 任务行操作** — 新增 assign / void / resolve-conflict 按钮（管理员角色门控）
5. **分享地址面板** — `CollabSharePanel`（内嵌于 CollabView header，或弹窗），显示 `service.local_address()` + 复制按钮
6. **`CollabService.broadcast_status_update()`** — 补齐 `collabUpdateTaskStatus` 里缺的向在线节点广播
7. **状态栏接线** — 在 WorkbenchView 里监听 `service.peers_changed`，调 `win.set_status_collab()`

---

## 仍缺、需双机真测

- **mDNS 发现**：`CollabDiscoveryThread` 代码完整，但 WSL 网络栈对组播支持有限；建议实机 Windows 10/11 + zeroconf 安装后测试
- **离线草稿**：本次不实现（简化决策：P2P 架构中网络失败=节点不在线，Qt app 会话内不持久化草稿）
- **Photo index 上报**：`collabPostPhotoIndex` 未实现，需 `HeliconcService` / `ArchiveService` 完成后调用钩子，跨模块，本次不动
- **L3 文件传输**（`/api/collab/files/*`）：collab.md 规格已定，不在本次范围

---

## 测试情况

- 982 passed, 2 skipped（needs_network 跳过）——补缺前基线
- 补缺后新增测试：`test_collab_service.py` 追加 `TestCollabSidebarWiring`、`TestCollabManagerDialog`、`TestStatusBroadcast`
