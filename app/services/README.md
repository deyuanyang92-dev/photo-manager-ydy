# app/services — 业务编排层

**放什么：** 文件规则、DB 读写编排、跨模块查询、导出管道。优先 **Qt-free**，便于 pytest。

**不放什么：** QWidget、页面布局、纯数学/字符串工具（→ `app/utils/`）。

**子域目录**（`project/`、`specimen/`、`taxonomy/`、`label/`、`collab/`）为渐进迁移锚点；当前多数模块仍在**本层扁平 `.py`**，import 路径 `app.services.<module>` 保持有效。

详见 [`docs/architecture/directory-boundaries.md`](../../docs/architecture/directory-boundaries.md)。
