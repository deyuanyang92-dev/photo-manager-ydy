# tests/unit — 新测试落位（渐进）

**规则：** 2026-07 起新增测试优先放此树；**历史 `tests/test_*.py` 不批量搬迁**。

```
tests/unit/
  services/   # 业务规则、Qt-free
  views/      # pytest-qt 整页
  widgets/    # pytest-qt 组件
  utils/      # 纯工具
```

**运行：**

```bash
QT_QPA_PLATFORM=offscreen pytest tests/unit/ -q
```

迁移旧文件时只改路径，**不断言**。见 `docs/migration-checklist.md` 阶段 1。
