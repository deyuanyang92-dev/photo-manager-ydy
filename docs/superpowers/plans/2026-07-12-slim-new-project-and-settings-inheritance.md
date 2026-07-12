# 新建项目精简 + 项目设置继承闭环 —— 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新建项目只建一个空项目目录；项目级设置（采集人/地区代码/坐标/拍摄场地）填一次，
下面任意层的采样点自动继承到工作台右栏；拍摄途中改人可写回工作区默认。

**Spec:** `docs/specs/2026-07-12-slim-new-project-and-settings-inheritance.md`（逐段用户拍板）

**Architecture:** 服务层（`project_scaffold_service` / `project_settings_service`）几乎不动 ——
`create_survey_project(sites=[])` 本来就只建项目根，`get_effective()` 本来就沿目录树继承。
真正缺的是 **UI 入口**：项目根是容器（非工作区），而设置抽屉只挂在工作台上，导致项目级设置
无处可填。补上「右键项目 → 项目设置」（用一个把 `ctx` 指向项目根的轻量代理），砍掉的对话框
字段才有去处。

**Tech Stack:** PyQt6 / pytest + pytest-qt / SQLite（每工作区一个 `_data/project.db`）

## Global Constraints

- **§7 旧代码不删**：替换既有逻辑时，旧行用 `#` 注释保留在原处，并写清本次需求/场景，供日后恢复。
- **项目根永远不建 `incoming-jpg/` / `results/`** —— 它是容器，照片不得堆在项目根（红线）。
- **跨工作区开库一律 `open_project_db_private()` + `finally: close()`** —— 缓存连接会持有文件锁，
  Windows 上导致项目文件夹删不掉/移不动（历史 bug）。
- **`metadata_panel._auto_fields` 保护语义不得回退**：用户手改过的字段，任何自动来源都不许覆盖。
- UI 文案中文优先，包 `tr()`。提交走 Conventional Commits，中文主题。
- **测试单文件跑**：`pytest tests/<file> -v`（全量跑会挂，见 CLAUDE.md）。Qt 测试加
  `QT_QPA_PLATFORM=offscreen`。

---

### Task 1: 红线守卫 —— 空项目只建容器

**Files:**
- Test: `tests/test_project_scaffold_service.py`（已有 5 个测试，追加）

**Interfaces:**
- Consumes: `project_scaffold_service.create_survey_project(parent_dir, *, name, sites, ...)`（不改）
- Produces: 无（纯守卫测试）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_project_scaffold_service.py`：

```python
def test_empty_project_creates_container_only(tmp_path):
    """sites=[] → 只建项目根容器；照片目录一个都不许有（红线）。

    需求(2026-07-12): 新建项目只建一个空项目目录, 断面/采样点之后再加。
    """
    res = create_survey_project(str(tmp_path), name="江苏盐城2026", sites=[])
    root = Path(res["root"])
    assert root.is_dir()
    assert (root / "_data" / "region.json").is_file()   # 「这是容器」标记
    assert (root / "_data" / "project.db").is_file()    # 设置锚点
    assert res["sites"] == []
    # 红线: 项目根不是拍照工作区
    assert not (root / "incoming-jpg").exists()
    assert not (root / "results").exists()
```

- [ ] **Step 2: 跑测试**

Run: `pytest tests/test_project_scaffold_service.py::test_empty_project_creates_container_only -v`
Expected: **PASS**（服务层本来就支持；这是回归守卫，不是新功能）。若 FAIL 说明服务层有问题，
先修服务层再往下走。

- [ ] **Step 3: 提交**

```bash
git add tests/test_project_scaffold_service.py
git commit -m "test(scaffold): 守卫 sites=[] 只建项目容器, 项目根不得有 incoming-jpg/results"
```

---

### Task 2: 新建项目对话框砍到 2 个字段

**Files:**
- Modify: `app/widgets/new_survey_project_dialog.py`
- Test: `tests/test_new_survey_project_dialog.py`（**新建**）

**Interfaces:**
- Produces: `NewSurveyProjectDialog.values() -> {"parent_dir": str, "name": str}`
  （**不再返回** `sites` / `meta` / `collector` / `province`）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_new_survey_project_dialog.py`：

```python
"""新建项目对话框 —— 精简为 2 个字段(需求 2026-07-12)。

旧版一次问 6 个字段 + 采样点列表, 因为项目根设置当时没有别的 UI 可填(见 spec §1.2)。
补上「右键项目 → 项目设置」后, 这里只留最少必填项。
"""
import pytest
from PyQt6.QtWidgets import QDialog

from app.widgets.new_survey_project_dialog import NewSurveyProjectDialog


def test_values_only_name_and_parent_dir(qtbot, tmp_path):
    dlg = NewSurveyProjectDialog(default_parent_dir=str(tmp_path))
    qtbot.addWidget(dlg)
    dlg._name.setText("江苏盐城2026")

    vals = dlg.values()

    assert vals == {"parent_dir": str(tmp_path), "name": "江苏盐城2026"}


def test_removed_fields_are_gone(qtbot, tmp_path):
    """采样点多行框和 4 个元数据字段不再存在 —— 它们改由项目设置抽屉填。"""
    dlg = NewSurveyProjectDialog(default_parent_dir=str(tmp_path))
    qtbot.addWidget(dlg)

    for attr in ("_sites", "_location", "_year", "_collector", "_province"):
        assert not hasattr(dlg, attr), f"{attr} 应已移除(§7 注释保留)"


def test_rejects_empty_name(qtbot, tmp_path):
    dlg = NewSurveyProjectDialog(default_parent_dir=str(tmp_path))
    qtbot.addWidget(dlg)
    dlg._name.setText("")

    dlg._try_accept()

    assert dlg.result() != QDialog.DialogCode.Accepted
    assert dlg._err.isVisible()


def test_rejects_existing_non_empty_dir(qtbot, tmp_path):
    (tmp_path / "已存在").mkdir()
    (tmp_path / "已存在" / "x.txt").write_text("x", encoding="utf-8")
    dlg = NewSurveyProjectDialog(default_parent_dir=str(tmp_path))
    qtbot.addWidget(dlg)
    dlg._name.setText("已存在")

    dlg._try_accept()

    assert dlg.result() != QDialog.DialogCode.Accepted
```

- [ ] **Step 2: 跑测试确认失败**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_new_survey_project_dialog.py -v`
Expected: FAIL —— `test_values_only_name_and_parent_dir` 报 `values()` 多返回了 sites/meta/…；
`test_removed_fields_are_gone` 报 `_sites` 仍存在。

- [ ] **Step 3: 改对话框**

`app/widgets/new_survey_project_dialog.py`：

模块 docstring 顶部追加本次变更说明（保留原 docstring，在其后加）：

```python
"""...(原 docstring 保留)...

§7 2026-07-12 变更 —— 精简为 2 个字段:
  旧场景(本文件原设计): 一个对话框问完 项目名/位置/地区/年份/采集人/地区代码 + 采样点列表,
    一次建好「项目 + N 个采样点」。之所以塞这么满, 是因为项目根是容器(非工作区), 而设置抽屉
    只挂在工作台上 —— 这个对话框是设置项目级默认值的**唯一机会**。
  新场景(用户 2026-07-12): "只建立一个项目目录, 后续点击这个目录, 也可以建立子目录"。
    项目树补了「右键项目 → 项目设置」入口后, 那 4 个字段和采样点列表都有了事后填的地方,
    这里只留 项目名称 + 建在哪里。
  恢复旧行为: 反注释下面 4 个字段 + 采样点框 + values() 中对应分支即可; 服务层
    create_survey_project(sites=[...]) 一直支持旧行为, 未改。
  详见 docs/specs/2026-07-12-slim-new-project-and-settings-inheritance.md
"""
```

`__init__` 里，`form.addRow("建在哪里 *", dir_wrap)` 之后的 4 个字段整段注释掉：

```python
        # §7 旧字段(2026-07-12 移入「项目设置」抽屉, 见本文件顶部说明) ────────────
        # self._location = QLineEdit()
        # self._location.setPlaceholderText("如：江苏盐城")
        # form.addRow("地区/位置", self._location)
        # self._year = QLineEdit()
        # self._year.setPlaceholderText("如：2026")
        # form.addRow("年份", self._year)
        # self._collector = QLineEdit()
        # self._collector.setPlaceholderText("整个项目共用，采样点自动继承")
        # form.addRow("采集人", self._collector)
        # self._province = QLineEdit()
        # self._province.setPlaceholderText("编号里的地区段，如 JSYC（可留空）")
        # form.addRow("地区代码", self._province)
        root.addLayout(form)

        # §7 旧「采样点(一行一个)」多行框 —— 采样点改为建完项目后在树里用
        #    「+ 新建子目录」自由添加(任意层), 不再在此一次问完。
        # tip = QLabel("采样点（一行一个）——每个点就是一个可以直接进入拍照的工作区")
        # tip.setObjectName("MutedSmall")
        # root.addWidget(tip)
        # self._sites = QPlainTextEdit()
        # self._sites.setPlaceholderText("日出海湾\n月亮湾")
        # self._sites.setFixedHeight(96)
        # root.addWidget(self._sites)
```

信号连接里去掉 `self._sites.textChanged`（注释保留）：

```python
        self._name.textChanged.connect(self._refresh_preview)
        # self._sites.textChanged.connect(self._refresh_preview)   # §7 采样点框已移除
        self._refresh_preview()
```

`site_names()` 改为恒返回空（保留方法，调用方可能还在引用）：

```python
    def site_names(self) -> list[str]:
        """§7 采样点已移出本对话框(2026-07-12) —— 恒为空; 采样点在项目树里建。

        旧实现(恢复时反注释):
        # return [line.strip() for line in self._sites.toPlainText().splitlines()
        #         if line.strip()]
        """
        return []
```

`_refresh_preview()` 改文案：

```python
    def _refresh_preview(self) -> None:
        name = self._name.text().strip() or "（项目名）"
        self._preview.setText(
            f"将创建：{name}/\n"
            "    （空项目；进去后再建断面 / 采样点）"
        )
        # §7 旧预览(列出采样点树), 恢复时反注释:
        # sites = self.site_names()
        # lines = [f"将创建：{name}/"]
        # for s in sites[:6]:
        #     lines.append(f"    ├ {s}/    （可直接进入拍照）")
        # ...
```

`_try_accept()` 删掉采样点校验（注释保留），保留 名称/上级目录/同名非空 三条校验。

`values()`：

```python
    def values(self) -> dict:
        # §7 旧返回(恢复时反注释): sites / meta / collector / province 一并返回,
        #    调用方转手喂给 create_survey_project。现在这些改由项目设置抽屉填。
        # return {
        #     "parent_dir": ..., "name": ..., "sites": self.site_names(),
        #     "meta": {"location": ..., "year": ...},
        #     "collector": ..., "province": ...,
        # }
        return {
            "parent_dir": self._dir.text().strip(),
            "name": self._name.text().strip(),
        }
```

顶部 import 里 `QPlainTextEdit` 现在没用了 —— **保留 import 并加 `# noqa: F401  §7 采样点框恢复时要用`**
（CI 只卡 F821，F401 不进门禁）。

- [ ] **Step 4: 跑测试确认通过**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_new_survey_project_dialog.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add app/widgets/new_survey_project_dialog.py tests/test_new_survey_project_dialog.py
git commit -m "feat(project): 新建项目对话框砍到「项目名 + 建在哪里」两项; 旧字段 §7 注释保留"
```

---

### Task 3: 两个入口的落点改为项目树

**Files:**
- Modify: `app/main_window.py:1063-1136`（`_on_new_survey_project`）
- Modify: `app/views/project_tree_view.py:4460-4536`（`_new_region`）
- Modify: `app/widgets/workspace_breadcrumb.py`（菜单文案「新建项目（含采样点）…」→「新建项目…」）
- Test: `tests/test_main_window_new_project.py`（**新建**）

**Interfaces:**
- Consumes: `NewSurveyProjectDialog.values() -> {"parent_dir", "name"}`（Task 2）
- Consumes: `create_survey_project(parent_dir, *, name, sites=[]) -> {"root": str, "sites": []}`
- Produces: 建完后 `ctx.settings.project_tree_root == 新项目根`，且当前页 == `project_tree`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_main_window_new_project.py`：

```python
"""建完空项目后的落点(需求 2026-07-12)。

项目根是容器(非工作区), 进不去工作台 —— 所以建完必须落到项目树, 让用户在那里
「+ 新建子目录」加断面, 并「右键项目 → 项目设置」填项目级默认值。
旧行为(§7 注释保留): 建完直接 enter_workspace(第一个采样点) → 跳工作台。
"""
from pathlib import Path

from PyQt6.QtWidgets import QDialog

from app.app_context import AppContext
from app.main_window import MainWindow


def test_new_project_lands_on_project_tree(qtbot, tmp_path, monkeypatch):
    ctx = AppContext()
    win = MainWindow(ctx)
    qtbot.addWidget(win)

    class _FakeDlg:
        def __init__(self, *a, **kw):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def values(self):
            return {"parent_dir": str(tmp_path), "name": "江苏盐城2026"}

    monkeypatch.setattr(
        "app.widgets.new_survey_project_dialog.NewSurveyProjectDialog", _FakeDlg
    )
    monkeypatch.setattr("app.utils.ui.info", lambda *a, **kw: None)

    win._on_new_survey_project()

    root = tmp_path / "江苏盐城2026"
    assert root.is_dir()
    assert not (root / "incoming-jpg").exists()          # 红线: 容器不放照片
    assert ctx.settings.project_tree_root == str(root)
    assert win.current_view_id() == "project_tree"       # 落到项目树, 不是工作台
    assert ctx.current_project_dir is None               # 没有采样点, 没进任何工作区
```

> 若 `MainWindow` 没有 `current_view_id()`，用 `win._stack.currentWidget().view_id` 断言，
> 并在实现步骤里补一个 `current_view_id()` 便捷方法（其它测试也会用到）。

- [ ] **Step 2: 跑测试确认失败**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_main_window_new_project.py -v`
Expected: FAIL —— 旧代码取 `vals["sites"]` → `KeyError: 'sites'`。

- [ ] **Step 3: 改两个入口**

`app/main_window.py::_on_new_survey_project`，docstring 换成新场景说明，函数体：

```python
        vals = dlg.values()
        try:
            # §7 旧调用(恢复时反注释): 一次建好项目 + N 个采样点
            # res = create_survey_project(
            #     vals["parent_dir"], name=vals["name"], sites=vals["sites"],
            #     meta=vals["meta"], collector=vals["collector"],
            #     province=vals["province"],
            # )
            # for site_dir in res["sites"]:
            #     save_project_descriptor(...)
            res = create_survey_project(
                vals["parent_dir"], name=vals["name"], sites=[]
            )
        except (ValueError, FileExistsError, FileNotFoundError) as exc:
            ui.warn(self, tr("新建项目"), str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive
            ui.warn(self, tr("新建项目"), f"创建失败：{exc}")
            return

        # 项目根 = 项目树根。项目根是容器(非工作区), 进不去 —— 落到项目树。
        try:
            self.ctx.settings.project_tree_root = res["root"]
        except Exception:  # noqa: BLE001
            pass
        # §7 旧落点(恢复时反注释): 有采样点则直接进第一个 → 工作台
        # if res["sites"]:
        #     enter_workspace(self.ctx, res["sites"][0], ...)
        #     self.navigate_to("workbench")
        self.navigate_to("project_tree")
        self.refresh_context_bar()
        ui.info(
            self,
            tr("新建项目"),
            f"项目「{vals['name']}」已建好（空项目）。\n\n"
            "· 点「+ 新建子目录」添加断面 / 采样点，双击进去就能拍\n"
            "· 右键项目 →「项目设置」可填采集人、地区代码、默认坐标、拍摄场地，\n"
            "  下面所有采样点自动继承，不用每次重填\n"
            "· 照片只会落在采样点里，不会堆在项目根",
        )
```

`app/views/project_tree_view.py::_new_region` 同样改：`sites=[]`、不再 `enter_workspace`、
建完 `self._root = res["root"]` + `_reload_project_tree()` + 选中新项目 + 同一条提示文案。
旧逻辑 §7 注释保留。

`app/widgets/workspace_breadcrumb.py`：菜单项文案 `新建项目（含采样点）…` → `新建项目…`
（旧文案注释保留）。

- [ ] **Step 4: 跑测试确认通过**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_main_window_new_project.py tests/test_workspace_breadcrumb.py -v`
Expected: 全部 PASS（breadcrumb 是回归，不许挂）

- [ ] **Step 5: 提交**

```bash
git add app/main_window.py app/views/project_tree_view.py app/widgets/workspace_breadcrumb.py tests/test_main_window_new_project.py
git commit -m "feat(project): 建完空项目落到项目树(项目根是容器进不去); 旧「直接进采样点」§7 注释保留"
```

---

### Task 4: 项目根的「项目设置」入口（**本次前提条件**）

**Files:**
- Create: `app/widgets/project_settings_dialog.py`
- Modify: `app/views/project_tree_view.py`（右键菜单加「项目设置…」）
- Test: `tests/test_project_settings_dialog.py`（**新建**）

**Interfaces:**
- Produces:
  - `class RootSettingsCtx` —— `RootSettingsCtx(real_ctx, project_dir: str)`；暴露
    `.current_project_dir -> str`、`.get_db(project_dir=None) -> sqlite3.Connection`、
    `.close() -> None`；其余属性 `__getattr__` 委托给 `real_ctx`。
  - `open_project_settings_dialog(parent, ctx, project_dir: str) -> None`

**背景（为什么要代理）**：`ProjectSettingsDrawer(ctx)` 全程读 `self.ctx.get_db()` /
`self.ctx.current_project_dir`（10+ 处），绑的是**当前工作区**。项目根不是工作区，
必须把 ctx 的这两处指到项目根，其余（`settings` 等全局）照旧委托。
**必须用私有连接 + 关闭**：`ctx.get_db()` 走 `open_project_db()`（**带缓存**），缓存连接会
持有项目根 `_data/project.db` 的文件锁直到退出 → Windows 上项目文件夹删不掉/移不动。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_project_settings_dialog.py`：

```python
"""项目根的「项目设置」入口(需求 2026-07-12)。

项目根是容器(非工作区), 设置抽屉却只挂在工作台上 —— 项目级默认值本来无处可填。
这是把新建项目对话框砍到 2 个字段的**前提条件**(见 spec §3.4)。
"""
import sqlite3
from pathlib import Path

from app.app_context import AppContext
from app.services import project_settings_service as pss
from app.services.project_scaffold_service import create_survey_project
from app.widgets.project_settings_dialog import RootSettingsCtx


def test_root_ctx_points_db_at_project_root(tmp_path):
    res = create_survey_project(str(tmp_path), name="江苏盐城2026", sites=[])
    root = res["root"]
    real = AppContext()

    proxy = RootSettingsCtx(real, root)
    try:
        assert proxy.current_project_dir == root
        db = proxy.get_db()
        assert isinstance(db, sqlite3.Connection)
        pss.save_setting(db, "personnel", {"collector": "张三"})
        db.commit()
    finally:
        proxy.close()

    # 写进的是项目根自己的库
    from app.db.db_manager import open_project_db_private
    check = open_project_db_private(root, create=False)
    try:
        assert pss.load_setting(check, "personnel", {}).get("collector") == "张三"
    finally:
        check.close()


def test_root_ctx_delegates_other_attrs(tmp_path):
    res = create_survey_project(str(tmp_path), name="P", sites=[])
    real = AppContext()
    proxy = RootSettingsCtx(real, res["root"])
    try:
        assert proxy.settings is real.settings      # 全局设置照旧委托
    finally:
        proxy.close()


def test_root_ctx_close_releases_file_lock(tmp_path):
    """红线: 关闭后不得持有文件锁(Windows 上会导致项目文件夹移不动/删不掉)。"""
    res = create_survey_project(str(tmp_path), name="P", sites=[])
    proxy = RootSettingsCtx(AppContext(), res["root"])
    proxy.get_db()
    proxy.close()

    assert proxy._db is None
    # 关完还能再关(幂等), 不抛
    proxy.close()


def test_settings_written_at_root_are_inherited_by_child(tmp_path):
    """闭环: 项目根设一次 → 子目录(断面)自动继承 → 右栏预填。"""
    res = create_survey_project(str(tmp_path), name="江苏盐城2026", sites=[])
    root = Path(res["root"])
    proxy = RootSettingsCtx(AppContext(), str(root))
    try:
        db = proxy.get_db()
        pss.save_setting(db, "personnel", {"collector": "张三", "photographer": "李四"})
        pss.save_setting(db, "code_labels", {"province": "JSYC", "site": "", "stations": {}, "species": {}})
        db.commit()
    finally:
        proxy.close()

    child = root / "断面A"
    child.mkdir()

    prefill = pss.effective_new_specimen_prefill(str(child), root=str(root))
    assert prefill["collector"] == "张三"
    assert prefill["photographer"] == "李四"
    assert prefill["province"] == "JSYC"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_project_settings_dialog.py -v`
Expected: FAIL —— `ModuleNotFoundError: app.widgets.project_settings_dialog`

- [ ] **Step 3: 实现**

新建 `app/widgets/project_settings_dialog.py`：

```python
"""project_settings_dialog.py — 在**项目根**(容器, 非工作区)上编辑项目级设置。

需求场景(用户 2026-07-12): 新建项目只填「项目名 + 建在哪里」, 采集人/地区代码/默认坐标/
拍摄场地等改为事后填 —— 但项目根是容器(_data/region.json, 不是拍照工作区), 而
ProjectSettingsDrawer 只挂在工作台上、工作台又要求当前是工作区, 于是项目根的设置
**根本没有 UI 可以编辑**。这个对话框就是补上那个入口; 没有它, 砍掉的字段就永远设不了。
详见 docs/specs/2026-07-12-slim-new-project-and-settings-inheritance.md §3.4
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import QDialog, QVBoxLayout, QWidget

from app.config.i18n import tr


class RootSettingsCtx:
    """把 ProjectSettingsDrawer 指向任意项目目录的轻量 ctx 代理。

    抽屉全程读 ``self.ctx.get_db()`` / ``self.ctx.current_project_dir``(10+ 处), 绑的是
    **当前工作区**。这里只覆盖这两处指向 *project_dir*, 其余属性(settings / collab_service…)
    一律 __getattr__ 委托给真 ctx。

    **私有连接 + close()**: ctx.get_db() 走 open_project_db() 是**带缓存**的, 缓存连接会
    持有 _data/project.db 的文件锁直到进程退出 —— Windows 上项目文件夹就删不掉/移不动了
    (历史 bug)。故这里用 open_project_db_private, 对话框关闭时立刻 close。
    """

    def __init__(self, real_ctx, project_dir: str) -> None:
        self._real = real_ctx
        self._dir = str(project_dir)
        self._db: Optional[sqlite3.Connection] = None

    @property
    def current_project_dir(self) -> str:
        return self._dir

    @property
    def project_root(self):
        # 项目根自己就是继承链的顶 —— 抽屉里的 get_effective 走到这里为止。
        return self._dir

    def get_db(self, project_dir: Optional[str] = None) -> Optional[sqlite3.Connection]:
        from app.db.db_manager import open_project_db_private
        if self._db is None:
            self._db = open_project_db_private(project_dir or self._dir, create=True)
        return self._db

    def close(self) -> None:
        if self._db is not None:
            try:
                self._db.commit()
            finally:
                self._db.close()
                self._db = None

    def __getattr__(self, name: str):
        # 只在本类没有该属性时触发 —— settings / collab_service / edit_unlocked 等照旧。
        return getattr(self._real, name)


def open_project_settings_dialog(
    parent: Optional[QWidget], ctx, project_dir: str
) -> None:
    """在 *project_dir* 上开设置抽屉(模态)。用完必关库, 不留文件锁。"""
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer

    proxy = RootSettingsCtx(ctx, project_dir)
    dlg = QDialog(parent)
    dlg.setWindowTitle(f"{tr('项目设置')} — {Path(project_dir).name}")
    dlg.setMinimumSize(440, 640)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(0, 0, 0, 0)

    drawer = ProjectSettingsDrawer(proxy, parent=dlg)
    drawer.refresh()
    drawer.show()                      # 抽屉默认 hide(), 嵌进对话框要显式 show
    drawer.closed.connect(dlg.accept)
    lay.addWidget(drawer)

    try:
        dlg.exec()
    finally:
        proxy.close()                  # 红线: 立刻放锁
```

`app/views/project_tree_view.py` 右键菜单，在 `new_child_action` 之后插入：

```python
        menu.addSeparator()
        settings_action = menu.addAction("项目设置…")
        settings_action.triggered.connect(self._open_node_settings)
```

并新增方法：

```python
    def _open_node_settings(self) -> None:
        """右键节点 →「项目设置…」: 在该节点(通常是项目根)的库上开设置抽屉。

        需求(2026-07-12): 项目根是容器, 进不去工作台 -> 抽屉本来打不开 -> 项目级
        采集人/地区代码/默认坐标/拍摄场地无处可填。这是新建项目对话框砍字段的前提。
        """
        path = self._selected_path() or self._root
        if not path:
            ui.info(self, "项目树", "请先选择一个项目或文件夹。")
            return
        from app.widgets.project_settings_dialog import open_project_settings_dialog
        open_project_settings_dialog(self, self.ctx, path)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_project_settings_dialog.py tests/test_project_settings_drawer.py -v`
Expected: 全 PASS（drawer 原有测试是回归，不许挂）

- [ ] **Step 5: 提交**

```bash
git add app/widgets/project_settings_dialog.py app/views/project_tree_view.py tests/test_project_settings_dialog.py
git commit -m "feat(project-tree): 右键项目 →「项目设置」—— 项目根(容器)终于能填项目级默认值"
```

---

### Task 5: 项目树工具栏「+ 新建子目录」按钮

**Files:**
- Modify: `app/views/project_tree_view.py`（工具栏加按钮；复用 `_new_subfolder`，逻辑不改）
- Test: `tests/test_project_tree_view.py`（追加）

**Interfaces:**
- Consumes: `ProjectTreeView._new_subfolder()`（已有，**不改行为**：纯 `mkdir`，不初始化工作区）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_project_tree_view.py`：

```python
def test_new_subfolder_button_exists_and_creates_plain_dir(qtbot, tmp_path, monkeypatch):
    """「+ 新建子目录」建的是**空壳**, 不是工作区(需求 2026-07-12)。

    用户: "然后我可以在目录中, 自由创建子目录" —— 中间层(断面A/)必须能当纯容器,
    只有真正进去拍的那层才初始化为工作区。所以这里绝不能建成 workspace。
    """
    from app.services import project_tree_service as pts

    view = _make_view(qtbot, tmp_path)          # 该文件已有的构造 helper
    assert hasattr(view, "_btn_new_subfolder")  # 工具栏显眼入口, 不再只藏右键菜单

    monkeypatch.setattr(
        "PyQt6.QtWidgets.QInputDialog.getText", lambda *a, **kw: ("断面A", True)
    )
    view._new_subfolder()

    child = tmp_path / "断面A"
    assert child.is_dir()
    assert not pts.is_workspace(str(child))     # 空壳, 不是工作区
    assert not (child / "_data").exists()
```

> `_make_view` 若不存在，按该测试文件既有的构造方式建视图（`ProjectTreeView(ctx)` +
> `view._root = str(tmp_path)`），不要新造 fixture 风格。

- [ ] **Step 2: 跑测试确认失败**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_project_tree_view.py -k new_subfolder_button -v`
Expected: FAIL —— `_btn_new_subfolder` 不存在

- [ ] **Step 3: 实现**

`project_tree_view.py` 工具栏（`_act_new_region` 所在那段附近），加一个实体按钮：

```python
        # 「+ 新建子目录」提到工具栏(2026-07-12): 采样点不再在新建项目时一次问完,
        # 改为建完项目后在树里自由加(任意层) —— 这个入口必须显眼, 不能只藏在右键菜单。
        self._btn_new_subfolder = QPushButton(tr("+ 新建子目录"))
        self._btn_new_subfolder.setObjectName("Outline")
        self._btn_new_subfolder.setToolTip(
            "在选中的节点下新建子目录（断面 / 采样点）；不选则建在项目根下。\n"
            "新建的是空目录，双击进入时才初始化为拍照工作区。"
        )
        self._btn_new_subfolder.clicked.connect(self._new_subfolder)
        toolbar.addWidget(self._btn_new_subfolder)   # ← toolbar 用该文件既有的那个 layout 变量名
```

`_new_subfolder()` **不改**。

- [ ] **Step 4: 跑测试确认通过**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_project_tree_view.py -v`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add app/views/project_tree_view.py tests/test_project_tree_view.py
git commit -m "feat(project-tree): 「+ 新建子目录」提到工具栏(空壳建法不变, 进入时才成工作区)"
```

---

### Task 6: 拍摄场地补进右栏预填

**Files:**
- Modify: `app/services/project_settings_service.py::effective_new_specimen_prefill`
- Modify: `app/views/workbench_view.py:1337-1360`（`apply_autofill` 的字典加 `photo_location`）
- Test: `tests/test_project_settings_effective.py`（追加）

**Interfaces:**
- Produces: `effective_new_specimen_prefill()` 返回值新增键 `"photo_location": str`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_project_settings_effective.py`：

```python
def test_prefill_includes_photo_location(tmp_path):
    """拍摄场地(需求 2026-07-12): project_meta 里存了, 但右栏一直没预填。

    specimens 表本来就有 photo_location 列(schema.sql:388), 抽屉「概览」tab 也能填,
    只是 effective_new_specimen_prefill 没返回它 -> 每个号都要手打一遍。
    """
    from app.db.db_manager import open_project_db_private
    from app.services import project_settings_service as pss

    root = tmp_path / "proj"
    root.mkdir()
    db = open_project_db_private(str(root), create=True)
    try:
        pss.save_setting(db, "project_meta", {"photo_location": "厦门大学海洋生物标本馆"})
        db.commit()
    finally:
        db.close()

    child = root / "断面A"
    child.mkdir()

    prefill = pss.effective_new_specimen_prefill(str(child), root=str(root))

    assert prefill["photo_location"] == "厦门大学海洋生物标本馆"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_project_settings_effective.py -k photo_location -v`
Expected: FAIL —— `KeyError: 'photo_location'`

- [ ] **Step 3: 实现**

`project_settings_service.effective_new_specimen_prefill()`：

```python
    code_labels = get_effective(project_dir, "code_labels", DEFAULT_CODE_LABELS, root=root)
    personnel = get_effective(project_dir, "personnel", DEFAULT_PERSONNEL, root=root)
    capture = get_effective(project_dir, "capture_defaults", DEFAULT_CAPTURE_DEFAULTS, root=root)
    # 拍摄场地(2026-07-12): 一直存在 project_meta 里、抽屉也能填, 但没进预填 ->
    # 每个新号都要手打。它是项目/工作区级的常量(实验室、船上), 正适合继承。
    meta = get_effective(project_dir, "project_meta", DEFAULT_PROJECT_META, root=root)
    return {
        ...
        "geo_area": capture.get("geoArea", "") or "",
        "photo_location": meta.get("photo_location", "") or "",
    }
```

docstring 的 Shape 段同步加 `"photo_location": str`。

`workbench_view.py:1355` 附近那个 `self._metadata.apply_autofill({...})` 的字典里加
`"photo_location": prefill["photo_location"]`（旧行 §7 注释保留）。
`metadata_panel` 若没有 `photo_location` 输入框，在「拍摄人」下方加一行「拍摄场地」
（`_field("photo_location", "拍摄场地", "如：实验室")`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_project_settings_effective.py tests/test_metadata_panel.py -v`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add app/services/project_settings_service.py app/views/workbench_view.py app/widgets/metadata_panel.py tests/test_project_settings_effective.py
git commit -m "feat(workbench): 拍摄场地纳入新号预填(project_meta 沿目录树继承)"
```

---

### Task 7: 拍摄途中换人 —— 轻提示 →（可选）写回工作区默认

**Files:**
- Modify: `app/widgets/metadata_panel.py`（新信号 + 4 个字段的手改钩子）
- Modify: `app/views/workbench_view.py`（接信号，弹轻提示，写回**当前工作区**设置）
- Test: `tests/test_metadata_panel.py`（追加）

**Interfaces:**
- Produces: `MetadataPanel.default_change_suggested = pyqtSignal(str, str)`  # (field, value)
  —— 仅对 `collector` / `photographer` / `identifier` / `photo_location` 发出

**语义（不得破坏的现状）**：手改 → `_on_field_edited` 把字段移出 `_auto_fields` → 此后任何自动来源
都不覆盖它 → `metadata_changed` → 工作台 autosave → 落该标本的 `specimens` 行。**这部分已存在，
本任务只在其上追加一个"要不要也当成后续默认"的询问。**

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_metadata_panel.py`：

```python
def test_manual_person_edit_suggests_new_default(qtbot):
    """拍摄途中换人(需求 2026-07-12): 改了拍摄人, 问一句"以后的新号也用他吗"。

    不做隐式的「会话粘滞」: 改一个号却悄悄影响后面 50 个, 出错无从追查是哪步换的人。
    """
    panel = MetadataPanel()
    qtbot.addWidget(panel)

    with qtbot.waitSignal(panel.default_change_suggested, timeout=500) as sig:
        panel._on_field_edited("photographer", "李四")

    assert sig.args == ["photographer", "李四"]


def test_coordinate_edit_does_not_suggest_default(qtbot):
    """经纬度/地理区是**站位级**数据(采集记录按站位覆盖) —— 弹提示只会误写。"""
    panel = MetadataPanel()
    qtbot.addWidget(panel)

    received = []
    panel.default_change_suggested.connect(lambda f, v: received.append((f, v)))

    panel._on_field_edited("lon", "119.5")
    panel._on_field_edited("lat", "31.2")
    panel._on_field_edited("geo_area", "东海")

    assert received == []


def test_manual_edit_still_protects_field_from_autofill(qtbot):
    """回归红线: 手改过的字段, 任何自动来源都不得覆盖(_auto_fields 语义)。"""
    panel = MetadataPanel()
    qtbot.addWidget(panel)

    panel._on_field_edited("photographer", "李四")
    panel.apply_autofill({"photographer": "张三"}, override_auto=True)

    assert panel._photographer.text() == "李四"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_metadata_panel.py -k suggest -v`
Expected: FAIL —— `MetadataPanel` 没有 `default_change_suggested`

- [ ] **Step 3: 实现**

`metadata_panel.py`：

```python
class MetadataPanel(QWidget):
    ...
    # 拍摄途中换人(2026-07-12): 手改这几个「一批拍摄期间通常不变」的字段后, 由工作台
    # 问一句「以后的新号也用它吗」。经纬度/地理区**不发** —— 那是站位级数据, 该由采集
    # 记录按站位覆盖, 弹提示只会误写成项目默认。
    default_change_suggested = pyqtSignal(str, str)   # (field, value)

_STICKY_CANDIDATES = ("collector", "photographer", "identifier", "photo_location")
```

```python
    def _on_field_edited(self, field: str, value: str) -> None:
        # 用户手动编辑路径（textEdited 只在人工输入时触发，setText 不触发）。
        # 用户一动手 → 该字段不再是「自动」，此后任何自动来源都不得覆盖它。
        self._auto_fields.discard(field)
        self._emit_change(field, value)
        if field in _STICKY_CANDIDATES and value.strip():
            self.default_change_suggested.emit(field, value.strip())
```

`workbench_view.py`（`self._metadata.metadata_changed.connect(...)` 旁边）：

```python
        self._metadata.default_change_suggested.connect(self._on_default_change_suggested)
```

```python
    _STICKY_LABELS = {
        "collector": "采集人", "photographer": "拍摄人",
        "identifier": "鉴定人", "photo_location": "拍摄场地",
    }

    def _on_default_change_suggested(self, field: str, value: str) -> None:
        """手改人员/场地后问一句：以后的新号也用它吗？(需求 2026-07-12)

        选「以后都用」→ 写回**当前工作区**的设置(不写项目根) —— 只影响这个断面,
        不污染整个项目。选「只这一个」/忽略 → 维持现状(只改当前标本, 已落库留痕)。
        """
        # 同一字段同一值不重复问(每敲一个字符都会触发 textEdited)。
        if self._sticky_asked.get(field) == value:
            return
        self._sticky_asked[field] = value
        self._sticky_timer.start(1200)      # 停止输入 1.2s 后才问, 不打断打字
        self._sticky_pending = (field, value)
```

> 防抖是必须的：`textEdited` 每敲一个字符都发一次，直接弹框会在打字途中弹出。
> 用一个 `QTimer(singleShot)`（`self._sticky_timer`）延迟 1.2 s，超时后再弹非模态提示条。
> 提示用工作台已有的提示条/`ui.question`；选「以后都用」时：
> ```python
> db = self.ctx.get_db()
> if field == "photo_location":
>     meta = pss.load_setting(db, "project_meta", pss.DEFAULT_PROJECT_META)
>     meta["photo_location"] = value
>     pss.save_setting(db, "project_meta", meta)
> else:
>     personnel = pss.load_setting(db, "personnel", pss.DEFAULT_PERSONNEL)
>     personnel[field] = value
>     pss.save_setting(db, "personnel", personnel)
> db.commit()
> ```
> 写的是 `ctx.get_db()` = **当前工作区**的库，不是项目根 —— 符合 spec §3.7。

- [ ] **Step 4: 跑测试确认通过**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_metadata_panel.py -v`
Expected: 全 PASS（含 `_auto_fields` 回归）

- [ ] **Step 5: 提交**

```bash
git add app/widgets/metadata_panel.py app/views/workbench_view.py tests/test_metadata_panel.py
git commit -m "feat(workbench): 拍摄途中换人 → 问「以后都用吗」→ 写回当前工作区默认(不动项目根)"
```

---

### Task 8: 顶栏空档态 —— 有项目根、无工作区

**Files:**
- Modify: `app/widgets/workspace_breadcrumb.py:252-270`（`refresh()` 的空 chain 分支）
- Test: `tests/test_workspace_breadcrumb.py`（追加）

**Interfaces:**
- Consumes: `ctx.settings.project_tree_root`（Task 3 建完项目时已设）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_workspace_breadcrumb.py`：

```python
def test_shows_project_when_root_set_but_no_workspace(qtbot, tmp_path):
    """刚建完项目、还没建采样点(需求 2026-07-12)。

    此时没有工作区, 面包屑旧行为退回 `选择工作区 ▾` —— 用户刚建完项目却看不到项目名。
    """
    root = tmp_path / "江苏盐城2026"
    root.mkdir()
    ctx = _Ctx(None, str(root))          # current_project_dir=None, project_root=root
    w = WorkspaceBreadcrumb(ctx)
    qtbot.addWidget(w)

    w.refresh()

    texts = [b.text() for b in w.findChildren(QPushButton)]
    assert any("江苏盐城2026" in t for t in texts)
    assert any("未选采样点" in t for t in texts)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_workspace_breadcrumb.py -k no_workspace -v`
Expected: FAIL —— 只有 `选择工作区 ▾`

- [ ] **Step 3: 实现**

`workspace_breadcrumb.refresh()` 的空 chain 分支：

```python
        chain = self._chain()
        if not chain:
            # 有项目根、没进任何工作区(刚建完空项目) —— 显示项目名, 别让用户以为没建成。
            # (需求 2026-07-12: 新建项目只建容器, 采样点之后在树里加)
            root = getattr(self.ctx, "project_root", None) or getattr(
                self.ctx.settings, "project_tree_root", None
            )
            if root:
                btn = QPushButton(f"📁 {Path(root).name}（{tr('未选采样点')}）▾")
                btn.setMenu(self._build_menu())      # 复用既有菜单构造
                self._lay.addWidget(btn)
                return
            # §7 旧: 无项目根时才是纯空态
            btn = QPushButton(tr("选择工作区 ▾"))
            ...
```

> 实现时以该文件既有的按钮/菜单构造方式为准（`_build_menu` 的真实方法名照抄现有代码），
> 不要新造一套。

- [ ] **Step 4: 跑测试确认通过**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_workspace_breadcrumb.py -v`
Expected: 全 PASS（30+ 个既有测试是回归，不许挂）

- [ ] **Step 5: 提交**

```bash
git add app/widgets/workspace_breadcrumb.py tests/test_workspace_breadcrumb.py
git commit -m "fix(shell): 建完空项目后顶栏显示「项目名（未选采样点）」, 不再退回空态"
```

---

## 收尾：整链回归

- [ ] 按 spec §4 手动走一遍闭环（`QT_QPA_PLATFORM=xcb python main.py`）：
      新建项目（2 字段）→ 落项目树 → 右键项目设置填采集人/地区代码 → +新建子目录 建断面A
      → 双击进入 → 右栏自动带出采集人/地区代码 → 改拍摄人 → 提示「以后都用」→ 建下一个号
      验证新拍摄人已成默认。
- [ ] `python scripts/run_core_regression.py naming` + `workbench` 两个套件绿。
- [ ] `ruff check app tests scripts main.py --select=F821`（CI 门禁）。
