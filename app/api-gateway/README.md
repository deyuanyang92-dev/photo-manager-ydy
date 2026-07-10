# API Gateway 目录说明

**状态（2026-07）：** 占位目录，**无运行时代码**。不删除，避免将来网关与 `app/services` 混放。

- 现阶段所有编排由 `app/services`、`app/views`、`app/widgets` 完成。
- 若 **6 个月内仍无网关需求**，可在 ADR 中记录后移除此目录（见 `docs/migration-checklist.md` 阶段 5）。

若要实现网关，在此建立：

- `src/`：对外暴露服务入口（HTTP、IPC、消息总线等）
- `modules/`：按域拆分的适配器/路由实现
- 明确边界文档，避免与 `app/services` 的业务逻辑重叠

若未来确认不会使用网关模式，可直接移除此目录并在架构文档同步说明。
