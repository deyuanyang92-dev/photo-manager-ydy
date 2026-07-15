# 团队共享工作区多主同步（2026-07-15）

> 用户定稿模型（2026-07-15 逐句敲定）：
> **工作区是各自独立可寻址的实体；"我当前在哪个工作区"只是个人光标 / presence，
> 永远不是任何门。** 团队成员不必和对方打开同一个项目/工作区，就能进入并编辑任何
> 一个**被共享**的工作区。对方在 A、做完了 B，我照样进 B 新增——不需要对方在 B、
> 也不需要在线。这就是多主复制（multi-master replication）。

## 边界（用户明确）

- **谁能同步一个工作区** = 团队永久码 + 我在共享勾选清单里勾了它 + 互相信任。
  跟"谁此刻打开着它"完全无关。
- **进入 / 编辑一个工作区** = 对我本地那份副本操作 → LWW 合并回对方的副本。
  对方在不在、在不在线都不影响；离线改，上线补同步。
- **照片 / TIF / ZIP 仍按项目码隔离 + 按需下载**（不动）。只有轻量编号/标本元数据
  走"团队码 + 共享勾选"持续同步。

## 现状（已核实 file:line，80% 已存在）

- `collab_specimen_sync.py`：复制**完整标本行**（~30列，含 raw_json），**双向多主**，
  写本地 project.db，LWW（`collab_updated_at`）+ 时钟偏移守卫。`get_local_specimens
  (project_dir, uid=None)` / `write_specimens_to_local_db(project_dir, ...)` **已经按
  workspace 参数化**——不绑定单一工作区。
- `collab_share_registry.py`：**按工作区目录**勾选共享，存 QSettings
  `collab/shared_project_dirs`；有 UI `collab_share_project_picker.py`。**但目前只驱动
  "发现"，不驱动同步**。
- `collab_peer_trust.py`：信任/屏蔽门控所有同步。
- 同步门（`collab_service.py`）：任务/UID 走团队码即可跨项目；**标本行同步却被
  `_data_sync_allowed` 里的 `_project_matches`（同 project_id 配对）卡死在当前打开的
  单一工作区**——这正是要松的闸。

## 四个缺口

- **(a) 解耦单工作区**：`CollabService` 的标本 push/pull 只认 `self._project_dir`。
  改成遍历**共享工作区集合**（share registry ∪ 对方 advertised sharedProjects 交集）。
  底层 helper 已参数化，主要改 service 层的循环 + 端点参数。
- **(b) 共享清单当同步门**：松掉 `_project_matches`——标本/编号元数据的同步条件改为
  `团队码匹配 AND 目标工作区在双方共享清单里 AND 互相信任`。**照片/文件端点的
  `_require_group_project` 不动**（项目码隔离保留）。隐私控制权从"项目码配对"移到
  "共享勾选清单"。
- **(c) 跨库读+合并守红线**：逐个开共享工作区的 project.db 必须
  `open_project_db_private()` + `finally close`（CLAUDE.md 红线：缓存连接会在 Windows
  上锁住文件夹）。已有 helper 就是这么写的，service 层遍历时同样守。
- **(d) revision 增量**：specimens 加 `collab_rev INTEGER`，每次本地写自增；同步带
  `since_rev` 游标，首连补缺、之后只传 `collab_rev > since_rev` 的行。几十万工作区
  必须（否则每 ~30s 把每个共享工作区整表传一遍会瘫）。是**必须**不是可选。
- **进入本地没有的共享工作区**：用 `project_adopt_service` 在本地物化一份空
  project.db（零迁移）→ 同步标本索引进来 → 可进去新增，改动合并回。照片不跟着来。

## 分阶段（本轮 = 阶段 1，纯服务层可测，不动活线/UI）

**阶段 1（本轮落地，TDD，零 UI / 零活线风险）**
1. **(d) revision 列**：schema.sql specimens 加 `collab_rev INTEGER DEFAULT 0`；
   `write_specimens_to_local_db` 写入时给每行 bump 一个单调递增的本地 rev；
   `get_local_specimens` 支持 `since_rev` 过滤、返回带 `collab_rev`。纯 DB helper，
   temp DB 可测：写→读→只回增量→rev 单调。
2. **(a/c) 多工作区读/合并核心**：新纯函数
   `iter_shared_workspace_specimens(shared_dirs, since_rev_by_ws)` 和
   `merge_shared_workspace_specimens(dir, records)`——遍历一组工作区目录，逐个用私有
   连接读/合并（守红线），返回 `{workspace_id: [records]}` + 新 rev 游标。不碰网络、
   不碰 self._project_dir，temp 多库可测。
3. **workspace_id 稳定标识**：从工作区 `_data` catalog 取 workspace_id（已有），
   给同步记录带上，避免"两个都叫断面1"混淆（用户点的问题 #1）。

**阶段 2（下一轮，碰活线/信任模型/端点/UI）**
- (b) 松 `_project_matches` → 共享清单当门；改 `/api/collab/specimens[/push]` 端点
  接受 `workspaceId` + `sinceRev`，网关从 `_require_group_project` 改共享清单校验。
- 同步周期遍历共享工作区集合。
- 进入未物化的共享工作区 → project_adopt_service 物化 + 首同步。
- collab_view 加"共享工作区 / 编号数 / 负责人"列。
- presence（谁此刻在哪个工作区拍哪个编号）作为轻量附加，挂 `_node_info()`。

## 红线（本轮必须守）

- 照片/TIF/ZIP 同步的 project_id 隔离**一个字节不动**（仅标本元数据走团队+共享）。
- 跨工作区读一律 `open_project_db_private` + `finally close`。
- 同步只写 specimens 表；LWW + 时钟偏移守卫沿用现有规则，不放宽。
- revision 是**本地**单调计数（不是跨机器全局），避免时钟依赖；跨机器仍靠
  `collab_updated_at` 做 LWW，rev 只用来"我这边哪些行对方还没见过"。

## 非目标

- 不做跨机器全局事务/强一致（P2P 局域网多主，最终一致 + LWW 足够）。
- 不引入中心服务器。
- 不在本轮碰照片同步的任何门。
