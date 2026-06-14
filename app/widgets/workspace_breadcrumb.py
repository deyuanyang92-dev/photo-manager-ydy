"""workspace_breadcrumb.py — 顶栏工作区路径条（OM 风格）.

显示「根 / 断面A / ◀ 📁 B2 ▾ ▶」：
  - 祖先段可点 → 跳项目树页（远跳/换断面走树）。
  - 叶子下拉  → 同级站位菜单（📷 = 已是工作区）+ 末尾「+ 新建断面…」
                （在当前工作区父目录下建新同级目录并进入，名字预填 YYYYMMDD(）。
  - ◀ ▶      → 访问历史后退/前进（浏览器式）—— 野外跨断面来回。
                走 project_service.enter_workspace（与项目树同一统一入口，含盘未挂载
                守护），首/末端禁用对应箭头，不回绕；中途回退后再切新工作区 → 截断前向分支。

同级 = 同父目录下的子目录，过滤点号目录与 RESERVED_DIR_NAMES（工作区内部结构）。
同级步进已退役：切同级走 ▾ 下拉点选。根即工作区（chain==1）只要有历史也能 ◀▶。
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path
from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPushButton,
    QToolButton,
    QWidget,
)

from app.config import icons
from app.config.i18n import tr
from app.services.project_tree_service import RESERVED_DIR_NAMES

# >3 级时折叠中间层为 「…」，保持顶栏一行放得下：根 / … / 父 / 叶
_MAX_SEGMENTS = 3

# 新建断面名字禁含的字符（与 project_tree_view._new_subfolder 一致）
_BAD_NAME_CHARS = ("/", "\\", "..")


def breadcrumb_chain(
    root: Optional[str], workspace: Optional[str]
) -> List[Tuple[str, str]]:
    """根→当前工作区的 (name, path) 链；不在根下→只剩叶子；无工作区→空."""
    if not workspace:
        return []
    ws = Path(workspace).resolve()
    if root:
        rootp = Path(root).resolve()
        try:
            rel = ws.relative_to(rootp)
        except ValueError:
            return [(ws.name, str(ws))]
        chain: List[Tuple[str, str]] = [(rootp.name, str(rootp))]
        cur = rootp
        for part in rel.parts:
            cur = cur / part
            chain.append((part, str(cur)))
        return chain
    return [(ws.name, str(ws))]


def sibling_dirs(workspace: str) -> List[str]:
    """同父目录下的同级目录（含自身），过滤文件/点号/保留目录，按名排序."""
    ws = Path(workspace).resolve()
    parent = ws.parent
    if parent == ws:  # filesystem root
        return [str(ws)]
    out: List[str] = []
    try:
        entries = sorted(os.scandir(parent), key=lambda e: e.name)
    except OSError:
        return [str(ws)]
    for entry in entries:
        name = entry.name
        if name.startswith(".") or name in RESERVED_DIR_NAMES:
            continue
        try:
            if not entry.is_dir():
                continue
        except OSError:
            continue
        out.append(str(Path(entry.path).resolve()))
    if str(ws) not in out:
        out.append(str(ws))
        out.sort()
    return out


class WorkspaceBreadcrumb(QWidget):
    """顶栏路径条：父链可见 + 📁 叶子 + ◀▶ 访问历史 + ▾ 同级/新建断面."""

    workspace_changed = pyqtSignal(str)   # 切换成功后的新工作区路径
    navigate_requested = pyqtSignal(str)  # 远跳目标 view_id

    def __init__(self, ctx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self.setObjectName("WorkspaceBreadcrumb")
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(2)
        # 测试与外部按属性访问；refresh() 重建后保持指向最新控件
        self._placeholder_btn: Optional[QPushButton] = None
        self._segment_btns: List[QPushButton] = []
        self._leaf_btn: Optional[QPushButton] = None
        self._btn_prev: Optional[QToolButton] = None
        self._btn_next: Optional[QToolButton] = None
        self._siblings: List[str] = []
        self._sib_index: int = -1
        self._collapsed = False
        # 访问历史（会话级，不持久化）。refresh 的外部检测 + _switch_to 显式记录；
        # _history_step 仅移动指针不记录，故回退/前进不再入历史。
        self._history: List[str] = []
        self._history_pos: int = -1
        self.refresh()

    # ── 状态读取 ─────────────────────────────────────────────────────────

    def _chain(self) -> List[Tuple[str, str]]:
        ws = getattr(self._ctx, "current_project_dir", None)
        root = getattr(self._ctx, "current_project_root", None)
        return breadcrumb_chain(root, ws)

    def text(self) -> str:
        """当前显示串（含折叠），兼容旧 _project_switcher.text() 断言."""
        if self._placeholder_btn is not None:
            return self._placeholder_btn.text()
        parts: List[str] = [btn.text() for btn in self._segment_btns]
        if self._collapsed and parts:
            parts.insert(1, "…")
        if self._leaf_btn is not None:
            parts.append(self._leaf_btn.text())
        return " / ".join(parts)

    # ── 访问历史 ─────────────────────────────────────────────────────────

    def _current_history_entry(self) -> Optional[str]:
        if self._history and 0 <= self._history_pos < len(self._history):
            return self._history[self._history_pos]
        return None

    def _record_history(self, path: str) -> None:
        """记录一次工作区访问：与指针相同则 no-op；否则截断前向分支后追加."""
        resolved = str(Path(path).resolve())
        if self._current_history_entry() == resolved:
            return
        if 0 <= self._history_pos < len(self._history) - 1:
            # 中途回退后又切新路径 → 丢弃指针之后的历史（浏览器同款分支截断）
            self._history = self._history[: self._history_pos + 1]
        self._history.append(resolved)
        self._history_pos = len(self._history) - 1

    # ── 重建 ─────────────────────────────────────────────────────────────

    def _clear(self) -> None:
        while self._lay.count():
            it = self._lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        self._placeholder_btn = None
        self._segment_btns = []
        self._leaf_btn = None
        self._btn_prev = None
        self._btn_next = None
        self._collapsed = False

    def refresh(self) -> None:
        # 外部切换检测：ctx.current 与历史指针不一致 → 视为外部进入（项目树/别处），
        # 入历史。内部 _switch_to / _history_step 已先行同步指针，此处为 no-op。
        ws = getattr(self._ctx, "current_project_dir", None)
        if ws:
            resolved = str(Path(ws).resolve())
            if self._current_history_entry() != resolved:
                self._record_history(resolved)
        self._clear()
        chain = self._chain()
        if not chain:
            self._build_placeholder()
            return
        self._build_chain(chain)

    def _build_placeholder(self) -> None:
        btn = QPushButton(tr("（未选）"))
        btn.setObjectName("ProjectSwitcher")
        btn.setToolTip(tr("切换当前工作区项目"))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        icons.set_button_icon(btn, "mdi6.folder-outline",
                              color=icons.TONE_MUTED, size=15)
        btn.clicked.connect(lambda: self.navigate_requested.emit("overview"))
        self._lay.addWidget(btn)
        self._placeholder_btn = btn

    def _build_chain(self, chain: List[Tuple[str, str]]) -> None:
        # 折叠：根 / … / 父 / 叶（中间层只在项目树里看）
        display: List[Tuple[str, str]] = list(chain[:-1])
        collapsed = False
        if len(display) > _MAX_SEGMENTS - 1:
            display = [display[0], display[-1]]
            collapsed = True
        self._collapsed = collapsed

        for i, (name, path) in enumerate(display):
            btn = QPushButton(name)
            btn.setObjectName("CrumbSeg")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(path)
            btn.clicked.connect(
                lambda _=False: self.navigate_requested.emit("project_tree"))
            self._lay.addWidget(btn)
            self._segment_btns.append(btn)
            if collapsed and i == 0:
                ell = QLabel("/ … ")
                ell.setObjectName("CrumbSep")
                self._lay.addWidget(ell)
            else:
                sep = QLabel("/")
                sep.setObjectName("CrumbSep")
                self._lay.addWidget(sep)

        leaf_name, leaf_path = chain[-1]
        self._siblings = sibling_dirs(leaf_path) if len(chain) > 1 else [leaf_path]
        try:
            self._sib_index = self._siblings.index(str(Path(leaf_path).resolve()))
        except ValueError:
            self._sib_index = -1

        prev_btn = QToolButton()
        prev_btn.setObjectName("CrumbArrow")
        prev_btn.setText("◀")
        prev_btn.setToolTip(tr("后退（访问历史）"))
        prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        prev_btn.clicked.connect(lambda: self._history_step(-1))
        self._lay.addWidget(prev_btn)
        self._btn_prev = prev_btn

        leaf = QPushButton(f"📁 {leaf_name} ▾")
        leaf.setObjectName("CrumbLeaf")
        leaf.setToolTip(leaf_path + "\n" + tr("点击列出同级站位 / 新建断面"))
        leaf.setCursor(Qt.CursorShape.PointingHandCursor)
        _fnt = leaf.font()
        _fnt.setBold(True)
        leaf.setFont(_fnt)
        leaf.clicked.connect(self._show_sibling_menu)
        self._lay.addWidget(leaf)
        self._leaf_btn = leaf

        next_btn = QToolButton()
        next_btn.setObjectName("CrumbArrow")
        next_btn.setText("▶")
        next_btn.setToolTip(tr("前进（访问历史）"))
        next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        next_btn.clicked.connect(lambda: self._history_step(+1))
        self._lay.addWidget(next_btn)
        self._btn_next = next_btn

        # ◀▶ 由访问历史驱动（非同级）；根即工作区只要有历史也能走
        prev_btn.setEnabled(self._history_pos > 0)
        next_btn.setEnabled(0 <= self._history_pos < len(self._history) - 1)

    # ── 切换 ─────────────────────────────────────────────────────────────

    def _enter(self, path: str, root_override: Optional[str] = None) -> Optional[str]:
        """统一进入入口（盘未挂载守护）；返回 resolved 路径，失败返回 None."""
        from app.services import project_service
        from app.services.project_paths import ProjectUnavailableError
        root = root_override if root_override is not None else getattr(
            self._ctx, "current_project_root", None
        )
        try:
            resolved = project_service.enter_workspace(
                self._ctx,
                path,
                root=root,
                projects_json_path=
                project_service.default_user_projects_json_path(),
            )
        except ProjectUnavailableError:
            from app.utils import ui
            ui.warn(self, tr("盘未连接"),
                    tr("该目录所在磁盘未挂载或路径不可用：") + f"\n{path}")
            return None
        return resolved

    def _switch_to(self, path: str) -> None:
        """切到指定工作区（下拉点选 / 新建断面 / 外部）→ 记入访问历史."""
        cur = getattr(self._ctx, "current_project_dir", None)
        if cur and str(Path(cur).resolve()) == str(Path(path).resolve()):
            return
        resolved = self._enter(path)
        if resolved is None:
            return
        self._record_history(resolved)
        self.refresh()
        self.workspace_changed.emit(resolved)

    def _switch_to_recent(self, path: str, root: Optional[str]) -> None:
        """切到最近工作区；历史记录携带 root，跨调查区域时必须恢复它."""
        cur = getattr(self._ctx, "current_project_dir", None)
        if cur and str(Path(cur).resolve()) == str(Path(path).resolve()):
            return
        resolved = self._enter(path, root_override=root)
        if resolved is None:
            return
        self._record_history(resolved)
        self.refresh()
        self.workspace_changed.emit(resolved)

    def _history_step(self, delta: int) -> None:
        """访问历史后退/前进：仅移动指针，不再入历史（不截断、不追加）."""
        target = self._history_pos + delta
        if not (0 <= target < len(self._history)):
            return
        path = self._history[target]
        cur = getattr(self._ctx, "current_project_dir", None)
        if cur and str(Path(cur).resolve()) == str(Path(path).resolve()):
            self._history_pos = target
            self.refresh()
            return
        resolved = self._enter(path)
        if resolved is None:
            return
        self._history_pos = target
        self.refresh()
        self.workspace_changed.emit(resolved)

    # ── ▾ 同级菜单 + 新建断面 ────────────────────────────────────────────

    def _build_sibling_menu(self) -> QMenu:
        menu = QMenu(self)
        from app.services.project_tree_service import is_workspace
        for path in self._siblings:
            name = os.path.basename(path)
            label = f"📷 {name}" if is_workspace(path) else f"📁 {name}"
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(path == self._siblings[self._sib_index]
                           if self._sib_index >= 0 else False)
            act.triggered.connect(
                lambda _=False, p=path: self._switch_to(p))
        menu.addSeparator()
        self._add_recent_menu(menu)
        menu.addSeparator()
        new_act = menu.addAction(f"➕ {tr('新建断面…')}")
        new_act.triggered.connect(self._on_new_section)
        return menu

    def _recent_workspaces(self, limit: int = 10) -> list[dict]:
        from app.services import project_service
        projects = project_service.list_projects(
            project_service.default_user_projects_json_path()
        )
        current = str(Path(getattr(self._ctx, "current_project_dir", "") or "").resolve())
        out: list[dict] = []
        seen: set[str] = set()
        for item in reversed(projects):
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
            name = str(item.get("name") or Path(resolved).name)
            root = item.get("root")
            out.append({"name": name, "directory": resolved, "root": str(root) if root else None})
            if len(out) >= limit:
                break
        return out

    def _add_recent_menu(self, menu: QMenu) -> None:
        recent = self._recent_workspaces()
        recent_menu = menu.addMenu("最近工作区")
        recent_menu.setEnabled(bool(recent))
        for item in recent:
            label = f"🕘 {item['name']}"
            act = recent_menu.addAction(label)
            act.setToolTip(item["directory"])
            act.triggered.connect(
                lambda _=False, p=item["directory"], r=item.get("root"):
                self._switch_to_recent(p, r)
            )

    def _show_sibling_menu(self) -> None:
        if self._leaf_btn is None:
            return
        menu = self._build_sibling_menu()
        menu.exec(self._leaf_btn.mapToGlobal(
            self._leaf_btn.rect().bottomLeft()))

    def _new_section_parent(self) -> Optional[Path]:
        """新建断面的父目录 = 当前工作区的父目录（= 新同级）."""
        ws = getattr(self._ctx, "current_project_dir", None)
        if not ws:
            return None
        return Path(ws).resolve().parent

    def _default_section_name(self) -> str:
        """预填「YYYYMMDD(」—— 用户续填地点后合上括号."""
        return f"{datetime.date.today().strftime('%Y%m%d')}("

    def _on_new_section(self) -> None:
        if self._new_section_parent() is None:
            return
        name, ok = QInputDialog.getText(
            self, tr("新建断面"),
            tr("文件夹名（如 20260612(草埔村)）："),
            text=self._default_section_name(),
        )
        name = (name or "").strip()
        if not ok or not name:
            return
        self.create_and_enter_section(name)

    def create_and_enter_section(self, name: str) -> Optional[str]:
        """在当前工作区父目录下建新同级目录并进入。名字非法/无法建 → 返回 None."""
        name = (name or "").strip()
        if not name or any(c in name for c in _BAD_NAME_CHARS):
            return None
        parent = self._new_section_parent()
        if parent is None:
            return None
        target = parent / name
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            from app.utils import ui
            ui.warn(self, tr("新建断面"), tr("无法创建：") + f" {exc}")
            return None
        self._switch_to(str(target))
        return str(target.resolve()) if target.exists() else None
