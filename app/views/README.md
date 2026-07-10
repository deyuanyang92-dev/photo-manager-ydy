# app/views — 页面（BaseView）

**放什么：** 注册进 `registry.py` 的整页；`on_activate()` 入口逻辑。

**不放什么：** 可复用卡片（→ `app/widgets/`）、无 UI 的业务规则（→ `app/services/`）。

**子域锚点：** `project/`、`workbench/`、`tax/`、`label/`、`collab/`（见各子目录 README）。

页面之间 **不互相 import**；共享状态走 `AppContext` handoff。
