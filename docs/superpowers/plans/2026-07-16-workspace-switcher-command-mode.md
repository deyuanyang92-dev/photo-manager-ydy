# 顶栏项目入口第 12 版「智能指挥台」实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给顶栏工作区切换器新增第 12 个模式 `command`（智能指挥台）——一个上下文自适应弹层，一处同时满足回上次 / 同项目切采样点 / 新建即进 / 搜索四职能。

**Architecture:** 复杂度隔离进新文件 `app/widgets/workspace_command_popup.py` 的 `WorkspaceCommandPopup(QWidget)`（Qt.Popup 无边框浮层，纯 UI + 信号，headless 可测）。`workspace_breadcrumb.py` 只负责注册模式、造顶栏按钮、喂数据、连信号到既有的进入/新建动作。旧 01–11 模式一行不改。

**Tech Stack:** PyQt6，pytest-qt（`QT_QPA_PLATFORM=offscreen`），既有 `project_service` / `workspace_index_service`。

## Global Constraints

- UI 文案中文优先，全部经 `tr()`（`app/config/i18n.py`）。
- 只加不改：`_SWITCHER_MODES` 01–11 与其 builder 保持字节不变；新模式纯追加。遵守
  `keep-old-code-commented-not-deleted`——替换既有行时旧行以 `#` 注释保留。
- 每处改动留注释：场景 + 理由 + 模型署名（`annotate-every-change`）。
- 测试跑单文件：`QT_QPA_PLATFORM=offscreen pytest tests/<file> -v`，**不跑全量**。
- 无新增强依赖：拼音搜索若无库则降级子串匹配。
- Conventional Commits，中文主题。分支：直接在 `main`（`push-to-main-no-branches`）。
- 读路径不得写子库：provider 只读 `workspace_index_service` 缓存/实时扫，不迁移不改 `updated_at`。

---

### Task 1: 注册 `command` 模式（白名单 + 模式表）

**Files:**
- Modify: `app/config/settings.py:22-26`（`_WORKSPACE_SWITCHER_MODES` 集合）
- Modify: `app/widgets/workspace_breadcrumb.py:42-54`（`_SWITCHER_MODES` 元组）
- Test: `tests/test_workspace_switcher_modes.py`

**Interfaces:**
- Produces: 字符串常量 `"command"` 成为合法 switcher 模式；`_SWITCHER_MODES` 末项
  `("command", "12  智能指挥台")`。

- [ ] **Step 1: Write the failing test**

在 `tests/test_workspace_switcher_modes.py` 追加：

```python
def test_command_mode_is_registered_and_persists(tmp_path, qtbot, app_settings_factory):
    # command 必须进白名单、可被选中并持久化
    from app.config.settings import _WORKSPACE_SWITCHER_MODES
    from app.widgets.workspace_breadcrumb import _SWITCHER_MODES, _SWITCHER_MODE_VALUES
    assert "command" in _WORKSPACE_SWITCHER_MODES
    assert "command" in _SWITCHER_MODE_VALUES
    assert _SWITCHER_MODES[-1][0] == "command"           # 第 12 个，排在最后
    assert _SWITCHER_MODES[-1][1].startswith("12")       # 标签以 12 起头
```

（若该测试文件用不同 fixture 命名，沿用文件里既有工作区/settings fixture；只需断言上面四条常量事实，不依赖 fixture。）

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_workspace_switcher_modes.py::test_command_mode_is_registered_and_persists -v`
Expected: FAIL —— `assert "command" in _WORKSPACE_SWITCHER_MODES`（尚未加）。

- [ ] **Step 3: Write minimal implementation**

`app/config/settings.py`，把集合改为（保留旧行注释）：

```python
_WORKSPACE_SWITCHER_MODES = {
    "classic", "navigator", "triple", "om_capture", "dual",
    "breadcrumb", "omnibox", "history", "scenes", "instrument",
    # "locator",  # §7 旧: 11 模式止于此
    "locator",
    "command",   # 第 12 版 智能指挥台 (Opus 2026-07-16)
}
```

`app/widgets/workspace_breadcrumb.py` 的 `_SWITCHER_MODES` 末尾追加一行（`locator` 行后）：

```python
    ("locator", "11  定位台"),
    ("command", "12  智能指挥台"),   # 第 12 版 上下文自适应 (Opus 2026-07-16)
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_workspace_switcher_modes.py::test_command_mode_is_registered_and_persists -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/config/settings.py app/widgets/workspace_breadcrumb.py tests/test_workspace_switcher_modes.py
git commit -m "feat(topbar): 注册项目入口第12版 command 模式（仅登记，占位）"
```

---

### Task 2: `SwitchRow` 数据类型 + 富数据 provider

**Files:**
- Create: `app/widgets/workspace_command_popup.py`（先只放 `SwitchRow`）
- Modify: `app/widgets/workspace_breadcrumb.py`（新增 provider 方法）
- Test: `tests/test_workspace_command_popup.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass
  class SwitchRow:
      kind: str                     # "project" | "station"
      label: str                    # 叶名（采样点名 或 项目名）
      full_label: str               # "盐城2026 › 日出海湾"
      path: str                     # resolved 目录
      root: str | None              # 所属项目根 resolved（project 行 root==path）
      is_current: bool
      exists: bool                  # False = 死路径
      specimen_count: int | None
      last_opened: float | None     # epoch 秒
      pinned: bool = False
  ```
- Produces（breadcrumb 上）：
  - `command_current_project_rows() -> list[SwitchRow]`（当前项目下各采样点，态1 主区）
  - `command_recent_rows(limit: int = 5) -> list[SwitchRow]`（最近工作区，含死路径，态1/2）
  - `command_all_project_rows() -> list[SwitchRow]`（全部项目聚合，态2 + 搜索源）

- [ ] **Step 1: Write the failing test**

Create `tests/test_workspace_command_popup.py`：

```python
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from app.widgets.workspace_command_popup import SwitchRow


def test_switchrow_holds_all_fields():
    r = SwitchRow(
        kind="station", label="日出海湾", full_label="盐城2026 › 日出海湾",
        path="/p/盐城2026/日出海湾", root="/p/盐城2026", is_current=True,
        exists=True, specimen_count=42, last_opened=1000.0, pinned=False,
    )
    assert r.kind == "station"
    assert r.is_current and r.exists
    assert r.specimen_count == 42
    assert r.pinned is False          # 默认值
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_workspace_command_popup.py::test_switchrow_holds_all_fields -v`
Expected: FAIL —— `ModuleNotFoundError: app.widgets.workspace_command_popup`。

- [ ] **Step 3: Write minimal implementation**

Create `app/widgets/workspace_command_popup.py`：

```python
"""workspace_command_popup.py — 顶栏第 12 版「智能指挥台」浮层.

上下文自适应弹层：在项目内→突出同项目采样点；未进项目→突出继续上次+最近项目；
打字→全局搜索盖一切。纯 UI + 信号，数据由 WorkspaceBreadcrumb 注入。(Opus 2026-07-16)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SwitchRow:
    kind: str
    label: str
    full_label: str
    path: str
    root: Optional[str]
    is_current: bool
    exists: bool
    specimen_count: Optional[int]
    last_opened: Optional[float]
    pinned: bool = False
```

在 `workspace_breadcrumb.py` 顶部 import 处加：

```python
from app.widgets.workspace_command_popup import SwitchRow, WorkspaceCommandPopup  # noqa: F401
```

（`WorkspaceCommandPopup` 在 Task 3 建；本步先只需 `SwitchRow` 可导入，import 行可暂时只导 `SwitchRow`，Task 3 再补类名。）

新增三个 provider 方法（放在 `_recent_workspaces` 附近）：

```python
    # ── 第 12 版 command 数据 provider (Opus 2026-07-16) ──────────────────
    def _specimen_count(self, directory: str) -> Optional[int]:
        """读缓存标本数；缺失回退实时扫；任何异常→None（不阻塞渲染）。"""
        try:
            from app.services import workspace_index_service
            stats = workspace_index_service.read_stats(directory)  # 见下方注
            if stats and stats.get("specimen_count") is not None:
                return int(stats["specimen_count"])
        except Exception:
            pass
        return None

    def command_recent_rows(self, limit: int = 5) -> list["SwitchRow"]:
        from app.services import project_service
        projects = project_service.list_projects(
            project_service.default_user_projects_json_path()
        )
        current = str(Path(getattr(self._ctx, "current_project_dir", "") or "").resolve())
        pins = self._pinned_paths()
        ordered = sorted(reversed(projects),
                         key=lambda p: p.get("lastOpenedAt") or 0, reverse=True)
        out: list[SwitchRow] = []
        seen: set[str] = set()
        for item in ordered:
            if item.get("isProjectRoot"):
                continue
            path = str(item.get("directory") or item.get("dir") or "")
            if not path:
                continue
            try:
                resolved = str(Path(path).resolve())
            except OSError:
                resolved = path
            if resolved == current or resolved in seen:
                continue
            seen.add(resolved)
            root = item.get("root")
            root_r = str(Path(str(root)).resolve()) if root else None
            name = str(item.get("name") or Path(resolved).name)
            full = self._full_label(root_r, resolved, name)
            out.append(SwitchRow(
                kind="station", label=name, full_label=full, path=resolved,
                root=root_r, is_current=False, exists=Path(resolved).exists(),
                specimen_count=self._specimen_count(resolved),
                last_opened=item.get("lastOpenedAt"), pinned=resolved in pins,
            ))
            if len(out) >= limit:
                break
        return out

    def command_current_project_rows(self) -> list["SwitchRow"]:
        """当前项目根下的采样点（is_workspace 的直接/浅层子目录）。"""
        from app.services import project_tree_service
        root = self._project_root_for_child()
        if not root:
            return []
        current = str(Path(getattr(self._ctx, "current_project_dir", "") or "").resolve())
        pins = self._pinned_paths()
        rows: list[SwitchRow] = []
        for ws in project_tree_service.iter_workspaces(root):   # 见下方注
            resolved = str(Path(ws).resolve())
            name = Path(resolved).name
            rows.append(SwitchRow(
                kind="station", label=name,
                full_label=self._full_label(str(Path(root).resolve()), resolved, name),
                path=resolved, root=str(Path(root).resolve()),
                is_current=(resolved == current), exists=True,
                specimen_count=self._specimen_count(resolved),
                last_opened=None, pinned=resolved in pins,
            ))
        return rows

    def command_all_project_rows(self) -> list["SwitchRow"]:
        from app.services import project_service
        projects = project_service.list_projects(
            project_service.default_user_projects_json_path()
        )
        pins = self._pinned_paths()
        rows: list[SwitchRow] = []
        seen: set[str] = set()
        for item in projects:
            if not item.get("isProjectRoot"):
                continue
            path = str(item.get("directory") or item.get("dir") or "")
            if not path:
                continue
            resolved = str(Path(path).resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            name = str(item.get("name") or Path(resolved).name)
            rows.append(SwitchRow(
                kind="project", label=name, full_label=name, path=resolved,
                root=resolved, is_current=False, exists=Path(resolved).exists(),
                specimen_count=None, last_opened=item.get("lastOpenedAt"),
                pinned=resolved in pins,
            ))
        return rows

    def _full_label(self, root: Optional[str], path: str, leaf: str) -> str:
        if not root:
            return leaf
        try:
            rel = Path(path).resolve().relative_to(Path(root).resolve())
            parts = [Path(root).name, *rel.parts] if rel.parts else [Path(root).name]
            return " › ".join(parts)
        except ValueError:
            return leaf

    def _pinned_paths(self) -> set[str]:
        settings = getattr(self._ctx, "settings", None)
        raw = getattr(settings, "switcher_pinned_workspaces", None) if settings else None
        return set(raw or [])
```

**实现注（供 Task 执行者核对，若签名不符照实调整并在本行注明）：**
- `workspace_index_service.read_stats(directory)`：若该函数名不同，用文件里既有的
  「读单工作区缓存 stats」只读 API（`app/services/workspace_index_service.py` §22 有
  `specimen_count` 字段）；务必走**只读**路径，不触发迁移/写回。
- `project_tree_service.iter_workspaces(root)`：若无同名函数，用既有 `is_workspace` +
  目录扫描组合（`app/services/project_tree_service.py`）列出 root 下的工作区目录。

`_pinned_paths` 依赖 `settings.switcher_pinned_workspaces`——在 Task 5 落地；此处
`getattr` 兜底空集合，Task 2 不需要该属性存在。

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_workspace_command_popup.py::test_switchrow_holds_all_fields -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/widgets/workspace_command_popup.py app/widgets/workspace_breadcrumb.py tests/test_workspace_command_popup.py
git commit -m "feat(topbar): command 模式 SwitchRow 类型 + 富数据 provider"
```

---

### Task 3: 浮层三态渲染（态1/态2，暂无搜索）

**Files:**
- Modify: `app/widgets/workspace_command_popup.py`（加 `WorkspaceCommandPopup`）
- Test: `tests/test_workspace_command_popup.py`

**Interfaces:**
- Consumes: `SwitchRow`（Task 2）。
- Produces:
  ```python
  class WorkspaceCommandPopup(QWidget):
      entered = pyqtSignal(str, object)      # (path, root|None)
      new_project = pyqtSignal()
      new_station = pyqtSignal(str)          # root
      browse_all = pyqtSignal()
      relocate = pyqtSignal(str)             # dead path
      toggle_pin = pyqtSignal(str)           # path
      def set_data(self, *, current_root: str | None,
                   stations: list[SwitchRow], recents: list[SwitchRow],
                   all_projects: list[SwitchRow]) -> None: ...
      def visible_rows(self) -> list[SwitchRow]: ...   # 测试内省当前渲染的行
  ```
- 态选择：`current_root` 非空 → 态1（stations 为主 + recents 一段）；为空 → 态2
  （recents 首行「继续上次」+ all_projects）。

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_workspace_command_popup.py`：

```python
import pytest
from PyQt6.QtWidgets import QApplication
from app.widgets.workspace_command_popup import SwitchRow, WorkspaceCommandPopup


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _row(kind, label, path, root, current=False, exists=True, n=0):
    return SwitchRow(kind=kind, label=label, full_label=label, path=path, root=root,
                     is_current=current, exists=exists, specimen_count=n,
                     last_opened=None, pinned=False)


def test_state1_shows_sibling_stations(qapp):
    pop = WorkspaceCommandPopup()
    pop.set_data(
        current_root="/p/盐城2026",
        stations=[_row("station", "日出海湾", "/p/盐城2026/日出海湾", "/p/盐城2026", current=True, n=42),
                  _row("station", "月亮湾", "/p/盐城2026/月亮湾", "/p/盐城2026", n=0)],
        recents=[_row("station", "北港岛", "/q/黄海/北港岛", "/q/黄海", n=17)],
        all_projects=[],
    )
    labels = [r.label for r in pop.visible_rows()]
    assert "日出海湾" in labels and "月亮湾" in labels     # 主区=同项目采样点
    assert "北港岛" in labels                              # 最近段也在


def test_state2_no_project_shows_continue_last(qapp):
    pop = WorkspaceCommandPopup()
    pop.set_data(
        current_root=None,
        stations=[],
        recents=[_row("station", "日出海湾", "/p/盐城2026/日出海湾", "/p/盐城2026", n=42)],
        all_projects=[_row("project", "盐城2026", "/p/盐城2026", "/p/盐城2026")],
    )
    rows = pop.visible_rows()
    assert rows and rows[0].label == "日出海湾"            # 首行=继续上次
    assert any(r.kind == "project" for r in rows)          # 最近项目段


def test_enter_row_emits_entered(qapp, qtbot):
    pop = WorkspaceCommandPopup()
    pop.set_data(current_root="/p/盐城2026",
                 stations=[_row("station", "月亮湾", "/p/盐城2026/月亮湾", "/p/盐城2026")],
                 recents=[], all_projects=[])
    with qtbot.waitSignal(pop.entered, timeout=500) as sig:
        pop.activate_row(pop.visible_rows()[0])            # 测试用激活入口
    assert sig.args[0] == "/p/盐城2026/月亮湾"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_workspace_command_popup.py -v -k state1 or state2 or enter_row`
Expected: FAIL —— `ImportError: cannot import name 'WorkspaceCommandPopup'`。

- [ ] **Step 3: Write minimal implementation**

在 `workspace_command_popup.py` 追加（`SwitchRow` 之后）：

```python
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from app.config.i18n import tr


class WorkspaceCommandPopup(QWidget):
    entered = pyqtSignal(str, object)
    new_project = pyqtSignal()
    new_station = pyqtSignal(str)
    browse_all = pyqtSignal()
    relocate = pyqtSignal(str)
    toggle_pin = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("WorkspaceCommandPopup")
        self._current_root: str | None = None
        self._stations: list[SwitchRow] = []
        self._recents: list[SwitchRow] = []
        self._all: list[SwitchRow] = []
        self._query: str = ""
        self._rows: list[SwitchRow] = []       # 当前渲染的行（含分区顺序）
        self._selected: int = -1
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(8, 8, 8, 8)
        self._search = QLineEdit(self)
        self._search.setPlaceholderText(tr("搜索项目或采样点"))
        self._search.textChanged.connect(self._on_search)
        self._outer.addWidget(self._search)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._body = QWidget(self)
        self._body_lay = QVBoxLayout(self._body)
        self._scroll.setWidget(self._body)
        self._outer.addWidget(self._scroll, 1)

    # ── 数据注入 ──
    def set_data(self, *, current_root, stations, recents, all_projects):
        self._current_root = current_root
        self._stations = list(stations)
        self._recents = list(recents)
        self._all = list(all_projects)
        self._render()

    def visible_rows(self) -> list["SwitchRow"]:
        return list(self._rows)

    # ── 渲染 ──
    def _compute_rows(self) -> list["SwitchRow"]:
        if self._query.strip():
            return self._search_rows(self._query.strip())      # Task 4 覆盖态3
        if self._current_root:
            return [*self._stations, *self._recents[:3]]        # 态1
        # 态2：首行=继续上次（recents[0]），其余最近项目
        head = self._recents[:1]
        return [*head, *self._all]

    def _search_rows(self, q: str) -> list["SwitchRow"]:
        # Task 4 会替换为项目/采样点分组过滤；先给个占位实现（子串）
        pool = {r.path: r for r in [*self._stations, *self._recents, *self._all]}
        ql = q.lower()
        return [r for r in pool.values()
                if ql in r.label.lower() or ql in r.full_label.lower()]

    def _render(self) -> None:
        while self._body_lay.count():
            it = self._body_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        self._rows = self._compute_rows()
        self._selected = 0 if self._rows else -1
        for idx, row in enumerate(self._rows):
            self._body_lay.addWidget(self._make_row_widget(idx, row))
        # 底部常驻动作
        actions = QFrame(self._body)
        alay = QVBoxLayout(actions)
        new_p = QPushButton(tr("＋新建项目"), actions)
        new_p.clicked.connect(lambda: self.new_project.emit())
        alay.addWidget(new_p)
        if self._current_root:
            new_s = QPushButton(tr("＋给当前项目加采样点"), actions)
            new_s.clicked.connect(lambda: self.new_station.emit(self._current_root))
            alay.addWidget(new_s)
        browse = QPushButton(tr("全部项目…"), actions)
        browse.clicked.connect(lambda: self.browse_all.emit())
        alay.addWidget(browse)
        self._body_lay.addWidget(actions)

    def _make_row_widget(self, idx: int, row: "SwitchRow") -> QWidget:
        w = QFrame(self._body)
        w.setObjectName("CommandRowCurrent" if row.is_current else "CommandRow")
        lay = QVBoxLayout(w)
        dot = "●" if row.is_current else ("⊘" if not row.exists else "○")
        bits = [f"{dot} {row.label}"]
        if row.specimen_count is not None:
            bits.append(f"{row.specimen_count}标本")
        label = QLabel("  ·  ".join(bits), w)
        if not row.exists:
            label.setEnabled(False)          # 死路径标灰
        lay.addWidget(label)
        if row.exists:
            w.mousePressEvent = lambda _e, r=row: self.activate_row(r)   # noqa: E731
        else:
            reloc = QPushButton(tr("重新定位…"), w)
            reloc.clicked.connect(lambda _c=False, p=row.path: self.relocate.emit(p))
            lay.addWidget(reloc)
        return w

    def activate_row(self, row: "SwitchRow") -> None:
        self.entered.emit(row.path, row.root)

    # ── 搜索 ──
    def _on_search(self, text: str) -> None:
        self._query = text or ""
        self._render()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_workspace_command_popup.py -v`
Expected: PASS（4 个测试全绿）

- [ ] **Step 5: Commit**

```bash
git add app/widgets/workspace_command_popup.py tests/test_workspace_command_popup.py
git commit -m "feat(topbar): command 浮层态1/态2 渲染 + 进入信号"
```

---

### Task 4: 搜索态3（项目/采样点分组）+ 死路径可见

**Files:**
- Modify: `app/widgets/workspace_command_popup.py`（`_search_rows` 正式实现）
- Test: `tests/test_workspace_command_popup.py`

**Interfaces:**
- Consumes: `set_data` 已注入的 stations/recents/all。
- Produces: `_search_rows(q)` 返回「项目匹配在前、采样点匹配在后」的去重列表；
  死路径行 `exists=False` 在态1/态2 中**不被过滤**，渲染为标灰 + 重新定位。

- [ ] **Step 1: Write the failing test**

追加：

```python
def test_search_filters_and_groups(qapp):
    pop = WorkspaceCommandPopup()
    pop.set_data(
        current_root=None, stations=[],
        recents=[_row("station", "红礁", "/q/黄海/红礁", "/q/黄海", n=12)],
        all_projects=[_row("project", "黄海航次", "/q/黄海", "/q/黄海"),
                      _row("project", "盐城2026", "/p/盐城2026", "/p/盐城2026")],
    )
    pop._search.setText("黄海")
    labels = [r.label for r in pop.visible_rows()]
    assert "黄海航次" in labels and "红礁" in labels        # 项目名 + 采样点路径均命中
    assert "盐城2026" not in labels                         # 非匹配不出现
    # 分组顺序：项目在采样点前
    kinds = [r.kind for r in pop.visible_rows()]
    assert kinds.index("project") < kinds.index("station")


def test_dead_path_shown_not_filtered(qapp):
    pop = WorkspaceCommandPopup()
    pop.set_data(
        current_root="/p/盐城2026",
        stations=[_row("station", "月亮湾", "/p/盐城2026/月亮湾", "/p/盐城2026")],
        recents=[_row("station", "断了的盘", "/dead/x", "/dead", exists=False)],
        all_projects=[],
    )
    rows = pop.visible_rows()
    assert any((not r.exists) and r.label == "断了的盘" for r in rows)   # 死路径仍在
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_workspace_command_popup.py::test_search_filters_and_groups -v`
Expected: FAIL —— 占位 `_search_rows` 未分组，`kinds.index` 断言失败。

- [ ] **Step 3: Write minimal implementation**

替换 `_search_rows`（旧实现整段以 `#` 注释保留在上方，§7 规矩）：

```python
    def _search_rows(self, q: str) -> list["SwitchRow"]:
        ql = q.lower()

        def hit(r: "SwitchRow") -> bool:
            return (ql in r.label.lower()
                    or ql in r.full_label.lower()
                    or ql in r.path.lower())

        projects, stations, seen = [], [], set()
        for r in self._all:
            if r.path not in seen and hit(r):
                seen.add(r.path); projects.append(r)
        for r in [*self._stations, *self._recents]:
            if r.path not in seen and hit(r):
                seen.add(r.path); stations.append(r)
        return [*projects, *stations]      # 项目组在前，采样点组在后
```

死路径在态1/态2 已不过滤（`_compute_rows` 原样带 `recents`，`_make_row_widget` 按
`exists` 渲染）——`test_dead_path_shown_not_filtered` 应已随 Task 3 通过；若未过，
确认 `_compute_rows` 态1 分支为 `[*self._stations, *self._recents[:3]]`，不含 exists 过滤。

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_workspace_command_popup.py -v`
Expected: PASS（全绿）

- [ ] **Step 5: Commit**

```bash
git add app/widgets/workspace_command_popup.py tests/test_workspace_command_popup.py
git commit -m "feat(topbar): command 搜索态3 项目/采样点分组 + 死路径可见"
```

---

### Task 5: 键盘导航 + ★收藏持久

**Files:**
- Modify: `app/widgets/workspace_command_popup.py`（keyPressEvent + pin 交互）
- Modify: `app/config/settings.py`（`switcher_pinned_workspaces` 属性）
- Test: `tests/test_workspace_command_popup.py`, `tests/test_workspace_switcher_modes.py`

**Interfaces:**
- Consumes: `toggle_pin` 信号（Task 3 已声明）。
- Produces: `AppSettings.switcher_pinned_workspaces`（`list[str]` getter/setter，QSettings
  持久）；popup `keyPressEvent` 支持 ↑↓ 选、Enter 进当前选中、Esc 关。

- [ ] **Step 1: Write the failing test**

`tests/test_workspace_command_popup.py`：

```python
from PyQt6.QtCore import Qt as _Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtCore import QEvent


def _key(popup, key):
    ev = QKeyEvent(QEvent.Type.KeyPress, key, _Qt.KeyboardModifier.NoModifier)
    popup.keyPressEvent(ev)


def test_arrow_enter_navigates(qapp, qtbot):
    pop = WorkspaceCommandPopup()
    pop.set_data(current_root="/p/盐城2026",
                 stations=[_row("station", "日出海湾", "/p/盐城2026/日出海湾", "/p/盐城2026", current=True),
                           _row("station", "月亮湾", "/p/盐城2026/月亮湾", "/p/盐城2026")],
                 recents=[], all_projects=[])
    _key(pop, _Qt.Key.Key_Down)            # 选到第 2 行
    with qtbot.waitSignal(pop.entered, timeout=500) as sig:
        _key(pop, _Qt.Key.Key_Enter)
    assert sig.args[0] == "/p/盐城2026/月亮湾"
```

`tests/test_workspace_switcher_modes.py`：

```python
def test_pinned_workspaces_setting_roundtrip(app_settings_factory):
    s = app_settings_factory()               # 文件里既有的 AppSettings 工厂
    s.switcher_pinned_workspaces = ["/p/盐城2026/月亮湾"]
    assert s.switcher_pinned_workspaces == ["/p/盐城2026/月亮湾"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_workspace_command_popup.py::test_arrow_enter_navigates tests/test_workspace_switcher_modes.py::test_pinned_workspaces_setting_roundtrip -v`
Expected: FAIL —— `keyPressEvent` 不改选中 / `AppSettings` 无 `switcher_pinned_workspaces`。

- [ ] **Step 3: Write minimal implementation**

`workspace_command_popup.py` 加方法：

```python
    def keyPressEvent(self, event) -> None:   # noqa: N802 (Qt 命名)
        from PyQt6.QtCore import Qt as _Qt
        key = event.key()
        rows = self._rows
        if key in (_Qt.Key.Key_Down, _Qt.Key.Key_Up) and rows:
            step = 1 if key == _Qt.Key.Key_Down else -1
            self._selected = max(0, min(len(rows) - 1, self._selected + step))
            event.accept(); return
        if key in (_Qt.Key.Key_Return, _Qt.Key.Key_Enter) and rows:
            if 0 <= self._selected < len(rows) and rows[self._selected].exists:
                self.activate_row(rows[self._selected])
            event.accept(); return
        if key == _Qt.Key.Key_Escape:
            self.close(); event.accept(); return
        super().keyPressEvent(event)
```

`app/config/settings.py`，`AppSettings` 内加属性（照文件里既有 property 风格）：

```python
    # 第 12 版 command 收藏置顶工作区 (Opus 2026-07-16)
    @property
    def switcher_pinned_workspaces(self) -> list[str]:
        raw = self._qs.value("ui/switcher_pinned_workspaces", [])
        if isinstance(raw, str):
            raw = [raw] if raw else []
        return [str(x) for x in (raw or [])]

    @switcher_pinned_workspaces.setter
    def switcher_pinned_workspaces(self, value: list[str]) -> None:
        self._qs.setValue("ui/switcher_pinned_workspaces", list(value or []))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_workspace_command_popup.py tests/test_workspace_switcher_modes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/widgets/workspace_command_popup.py app/config/settings.py tests/test_workspace_command_popup.py tests/test_workspace_switcher_modes.py
git commit -m "feat(topbar): command 键盘导航(↑↓/Enter/Esc) + ★收藏置顶持久"
```

---

### Task 6: 接入顶栏 —— `command` builder + 信号连线

**Files:**
- Modify: `app/widgets/workspace_breadcrumb.py`（`refresh` 分派 + `_build_command_variant`）
- Test: `tests/test_workspace_switcher_modes.py`

**Interfaces:**
- Consumes: `WorkspaceCommandPopup`（Task 3）、三个 provider（Task 2）、进入动作
  `_switch_to_recent`。
- Produces: 顶栏 `command` 模式渲染一个 `WorkspaceCommandLocation` 按钮，点击弹出
  已注数据的 `WorkspaceCommandPopup`。

- [ ] **Step 1: Write the failing test**

```python
def test_command_mode_builds_button_and_popup(qtbot, breadcrumb_in_project):
    # breadcrumb_in_project: 文件里既有的「ctx 处于某项目内」的 breadcrumb fixture
    bc = breadcrumb_in_project
    bc._mode_override = "command"
    bc.refresh()
    btn = bc.findChild(object, "WorkspaceCommandLocation")
    assert btn is not None                       # 顶栏造出 command 按钮
    popup = bc._open_command_popup()             # 直接调打开入口取 popup
    labels = [r.label for r in popup.visible_rows()]
    assert labels                                # 弹层已注入数据、非空
```

（若无 `breadcrumb_in_project` fixture，用文件里既有构造 breadcrumb 的方式，
把 `ctx.current_project_dir` 设为一个含 `_data/project.db` 的临时工作区。）

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_workspace_switcher_modes.py::test_command_mode_builds_button_and_popup -v`
Expected: FAIL —— `command` 未在 `refresh` 分派，无 `WorkspaceCommandLocation` 按钮。

- [ ] **Step 3: Write minimal implementation**

`workspace_breadcrumb.py`。先补 Task 2 里 import 行为完整：

```python
from app.widgets.workspace_command_popup import SwitchRow, WorkspaceCommandPopup
```

`refresh()` 里模式分派处（`:393` 的 `if mode in {classic,navigator,locator}` 之前）加：

```python
            if mode == "command":                       # 第 12 版 (Opus 2026-07-16)
                self._build_command_variant(chain)
                self._add_style_button()
                return
```

（注：`command` 自行 `return` 前调 `_add_style_button`，与文末统一调用二选一——
为避免重复添加样式按钮，command 分支 return 前调用后直接 return；确认 `refresh` 尾部
`self._add_style_button()` 不会对 command 再调一次。若结构上不便，改为 `command`
不 return、只 `self._build_command_variant(chain)` 然后走到统一 `_add_style_button()`。）

新增 builder + 打开入口：

```python
    def _build_command_variant(self, chain) -> None:
        compact = self._compact_chain_text(chain) if chain else tr("新建或打开项目")
        button = self._variant_location_button(
            text=compact, object_name="WorkspaceCommandLocation",
            icon_name="mdi6.compass-outline",
        )
        # _variant_location_button 默认挂 InstantPopup 菜单；command 改为点击开自绘浮层
        button.setMenu(None)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.DelayedPopup)
        button.clicked.connect(lambda _c=False, b=button: self._open_command_popup(b))
        self._lay.addWidget(button, 1)
        self._leaf_btn = button

    def _open_command_popup(self, button=None) -> "WorkspaceCommandPopup":
        pop = WorkspaceCommandPopup(self)
        root = self._project_root_for_child()
        root_r = str(Path(root).resolve()) if root else None
        pop.set_data(
            current_root=root_r,
            stations=self.command_current_project_rows(),
            recents=self.command_recent_rows(),
            all_projects=self.command_all_project_rows(),
        )
        pop.entered.connect(self._switch_to_recent)
        pop.new_project.connect(lambda: self.new_survey_project_requested.emit())
        pop.new_station.connect(lambda _r: self._emit_workspace_creation())
        pop.browse_all.connect(lambda: self.open_workspace_requested.emit())
        pop.relocate.connect(self._relocate_dead_path)      # 见下
        pop.toggle_pin.connect(self._toggle_pin)            # 见下
        if button is not None:
            pop.move(button.mapToGlobal(button.rect().bottomLeft()))
            pop.show()
            pop.setFocus()
        return pop

    def _relocate_dead_path(self, path: str) -> None:
        # 复用既有「打开已有工作区」入口让用户重指目录（最小实现）。
        self.open_workspace_requested.emit()

    def _toggle_pin(self, path: str) -> None:
        settings = getattr(self._ctx, "settings", None)
        if settings is None:
            return
        pins = list(getattr(settings, "switcher_pinned_workspaces", []) or [])
        resolved = str(Path(path).resolve())
        if resolved in pins:
            pins.remove(resolved)
        else:
            pins.insert(0, resolved)
        settings.switcher_pinned_workspaces = pins
```

**实现注：** `new_survey_project_requested` / `_emit_workspace_creation` /
`open_workspace_requested` 均为文件里既有信号/方法（见 `triple`/`om_capture` 分支
`:494/497/526`）；若名不符照既有实名连接。`Ctrl+K` 全局唤起在 `MainWindow` 加
`QShortcut` 调 `breadcrumb._open_command_popup()`——列为可选跟进，不阻塞本 Task。

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_workspace_switcher_modes.py -v`
Expected: PASS（含 01–11 回归全绿）

- [ ] **Step 5: 回归 + Commit**

```bash
QT_QPA_PLATFORM=offscreen pytest tests/test_workspace_switcher_modes.py tests/test_workspace_command_popup.py tests/test_workspace_breadcrumb.py -v
git add app/widgets/workspace_breadcrumb.py tests/test_workspace_switcher_modes.py
git commit -m "feat(topbar): 接入第12版 command 智能指挥台（builder+信号连线）"
```

---

## Self-Review

**1. Spec coverage（spec §→task）：**
- §3 态1/2/3 → Task 3（态1/2）+ Task 4（态3）✅
- §4 行信息（状态点/死路径/标本数/时间/★）→ Task 2（数据）+ Task 3（渲染）+ Task 4（死路径）+ Task 5（★）✅
- §5 键盘 → Task 5 ✅（Ctrl+K 列为 Task 6 可选跟进，spec §5 已含）
- §6 不堆砌（态选择逻辑）→ Task 3 `_compute_rows` ✅
- §7 实现落点（注册/复用/只加不改/★持久）→ Task 1/2/6 + Task 5 ✅
- §8 测试 1–6 → Task 1(契约1) / Task 3(2,3) / Task 4(4,死路径5) / Task 6(回归6) ✅
- §9 YAGNI → 计划未含缩略图墙/无限层/拖拽排序 ✅

**2. Placeholder scan：** 无 TBD/TODO；provider 里两处外部 API（`read_stats`/
`iter_workspaces`）标了「实现注」要执行者核实真实签名——非占位，是显式的核对指令。

**3. Type consistency：** `SwitchRow` 字段在 Task 2 定义，Task 3–6 一致引用；
`entered = pyqtSignal(str, object)` 与 `_switch_to_recent(path, root)` 参数匹配；
`switcher_pinned_workspaces: list[str]` Task 5 定义、Task 6 `_toggle_pin` 消费一致。

**风险提示（执行者必读）：**
- `QMenu` 与自绘 `QWidget(Qt.Popup)` 二选一：本计划用自绘浮层（搜索+键盘更顺、更好测），
  与旧 11 模式的 QMenu 并存，不改旧模式。
- 全量 pytest 会挂（`workbench-test-timer-leak-hang`）——只跑上述单文件。
- 两个外部 provider API 名若不符，按「实现注」用既有只读 API 替换，保持**读不写子库**红线。
```
