"""workspace_breadcrumb.py — 顶栏工作区面包屑（EOS Utility 式目录显示）.

显示「根 / 断面A / ◀ B2站 ▾ ▶」：
  - 祖先段可点 → 跳项目树页（远跳/换断面走树）。
  - 叶子下拉  → 同级站位菜单（📷 = 已是工作区）。
  - ◀ ▶      → 直接切上/下一个同级站位 —— 野外拍完 B1 一键进 B2。
                走 project_service.enter_workspace（与项目树同一统一入口，
                含盘未挂载守护），首/末端禁用对应箭头，不回绕。

同级 = 同父目录下的子目录，过滤点号目录与 RESERVED_DIR_NAMES（工作区内部结构）。
根即工作区时两箭头禁用：横跳出项目根属于换项目，必须走项目树，不给一键误跳。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
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
    """顶栏面包屑：父链可见 + ◀▶ 一键切同级站位."""

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
        prev_btn.setToolTip(tr("上一站位"))
        prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        prev_btn.clicked.connect(lambda: self._step(-1))
        self._lay.addWidget(prev_btn)
        self._btn_prev = prev_btn

        leaf = QPushButton(f"{leaf_name} ▾")
        leaf.setObjectName("CrumbLeaf")
        leaf.setToolTip(leaf_path + "\n" + tr("点击列出同级站位"))
        leaf.setCursor(Qt.CursorShape.PointingHandCursor)
        leaf.clicked.connect(self._show_sibling_menu)
        self._lay.addWidget(leaf)
        self._leaf_btn = leaf

        next_btn = QToolButton()
        next_btn.setObjectName("CrumbArrow")
        next_btn.setText("▶")
        next_btn.setToolTip(tr("下一站位"))
        next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        next_btn.clicked.connect(lambda: self._step(+1))
        self._lay.addWidget(next_btn)
        self._btn_next = next_btn

        # 根即工作区（chain==1）→ 没有可横跳的同级；首/末端禁对应箭头，不回绕
        can_move = len(chain) > 1 and self._sib_index >= 0
        prev_btn.setEnabled(can_move and self._sib_index > 0)
        next_btn.setEnabled(can_move and self._sib_index < len(self._siblings) - 1)

    # ── 切换 ─────────────────────────────────────────────────────────────

    def _step(self, delta: int) -> None:
        if self._sib_index < 0:
            return
        target = self._sib_index + delta
        if not (0 <= target < len(self._siblings)):
            return
        self._switch_to(self._siblings[target])

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
        return menu

    def _show_sibling_menu(self) -> None:
        if self._leaf_btn is None:
            return
        menu = self._build_sibling_menu()
        menu.exec(self._leaf_btn.mapToGlobal(
            self._leaf_btn.rect().bottomLeft()))

    def _switch_to(self, path: str) -> None:
        cur = getattr(self._ctx, "current_project_dir", None)
        if cur and str(Path(cur).resolve()) == str(Path(path).resolve()):
            return
        from app.services import project_service
        from app.services.project_paths import ProjectUnavailableError
        root = getattr(self._ctx, "current_project_root", None)
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
            return
        self.refresh()
        self.workspace_changed.emit(resolved)
