# 标本照片工作台 — 桌面版（v3）

把 `photo-platform-ydy/` 的 Web 原型复现为 Windows / macOS / Linux 桌面 GUI（PyQt6）。
功能 100% 照搬 Web 原型，界面美观+顺手，真实数据只读导入零丢失。

- 方案：`/root/.claude/plans/docs-cross-platform-desktop-gui-plan-md-hidden-frog.md`
- 决策记录：`docs/adr/`
- 每模块详细设计：`docs/specs/`（Opus 出，Sonnet 据此 TDD 实现）

## 开发

```bash
pip install -r requirements.txt
python main.py            # 启动空骨架
pytest tests/ -v         # 跑测试
```

## 红线（绝不破）

- **TIFF 永不删**。
- **JPG 删除四前置**全满足才删（ZIP 已生成 + 清单完整 + djxl 可恢复校验通过 + 删除开关开），默认不删。
- 导入现有数据**只读**，原文件一字节不改（sha256 校验）。

## 状态

W0 地基（进行中）：仓库骨架 ✅ → 数据库 schema → 安全导入 → 美观主题 → 工作台样板。
