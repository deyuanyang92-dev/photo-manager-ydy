"""workspace_command_popup.py — 顶栏第 12 版「智能指挥台」浮层.

上下文自适应弹层：在项目内→突出同项目采样点；未进项目→突出继续上次+最近项目；
打字→全局搜索盖一切。纯 UI + 信号，数据由 WorkspaceBreadcrumb 注入、动作也回调它。

为何独立文件、独立 QWidget(Qt.Popup) 而非复用 QMenu：搜索框 + 键盘上下选 + 分区
渲染在 QMenu 里很别扭；隔离进自绘浮层更好写也 headless 可测。旧 01–11 模式仍用
QMenu，不受影响。(Opus 2026-07-16, 第 12 版 command 模式)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.config.i18n import tr


@dataclass
class SwitchRow:
    """一行可切换目标（项目 或 采样点/工作区）。三态弹层共用同一行类型。"""

    kind: str                       # "project" | "station"
    label: str                      # 叶名（采样点名 或 项目名）
    full_label: str                 # "盐城2026 › 日出海湾"
    path: str                       # resolved 目录
    root: Optional[str]             # 所属项目根 resolved（project 行 root == path）
    is_current: bool
    exists: bool                    # False = 死路径（盘未连接）
    specimen_count: Optional[int]
    last_opened: Optional[float]    # epoch 秒
    pinned: bool = False


class WorkspaceCommandPopup(QWidget):
    """第 12 版智能指挥台浮层。数据经 set_data 注入，动作经信号回传。"""

    entered = pyqtSignal(str, object)     # (path, root|None) → 进入该工作区
    new_project = pyqtSignal()            # ＋新建项目
    new_station = pyqtSignal(str)         # ＋给当前项目加采样点(root)
    browse_all = pyqtSignal()             # 全部项目…
    relocate = pyqtSignal(str)            # 死路径 重新定位…(path)
    toggle_pin = pyqtSignal(str)          # ★收藏/取消(path)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("WorkspaceCommandPopup")
        self._current_root: Optional[str] = None
        self._stations: List[SwitchRow] = []
        self._recents: List[SwitchRow] = []
        self._all: List[SwitchRow] = []
        self._query: str = ""
        self._rows: List[SwitchRow] = []      # 当前渲染的行（含分区顺序）
        self._selected: int = -1

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(8, 8, 8, 8)
        self._search = QLineEdit(self)
        self._search.setObjectName("WorkspaceCommandSearch")
        self._search.setPlaceholderText(tr("搜索项目或采样点"))
        self._search.textChanged.connect(self._on_search)
        self._outer.addWidget(self._search)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._body = QWidget(self)
        self._body_lay = QVBoxLayout(self._body)
        self._body_lay.setContentsMargins(0, 0, 0, 0)
        self._scroll.setWidget(self._body)
        self._outer.addWidget(self._scroll, 1)
        self.setMinimumWidth(360)

    # ── 数据注入 ─────────────────────────────────────────────────────────
    def set_data(
        self,
        *,
        current_root: Optional[str],
        stations: List[SwitchRow],
        recents: List[SwitchRow],
        all_projects: List[SwitchRow],
    ) -> None:
        self._current_root = current_root
        self._stations = list(stations)
        self._recents = list(recents)
        self._all = list(all_projects)
        self._render()

    def visible_rows(self) -> List[SwitchRow]:
        """测试/内省：当前渲染出来的行（分区展开、去重、排序后）。"""
        return list(self._rows)

    # ── 三态计算 ─────────────────────────────────────────────────────────
    def _compute_rows(self) -> List[SwitchRow]:
        if self._query.strip():
            return self._search_rows(self._query.strip())          # 态3
        rows = self._pin_first(self._stations, self._recents, self._all)
        if self._current_root:
            # 态1：主区=同项目采样点；再挂最近去过前 3 行
            return [*rows["stations"], *rows["recents"][:3]]
        # 态2：首行=继续上次(recents[0])；其余=最近项目
        head = rows["recents"][:1]
        return [*head, *rows["all"]]

    @staticmethod
    def _pin_first(stations, recents, all_projects) -> dict:
        """收藏行在各自分区内置顶（★ 优先）。"""
        key = lambda r: (0 if r.pinned else 1)   # noqa: E731 稳定排序仅按 pinned 提前
        return {
            "stations": sorted(stations, key=key),
            "recents": sorted(recents, key=key),
            "all": sorted(all_projects, key=key),
        }

    def _search_rows(self, q: str) -> List[SwitchRow]:
        """态3：项目匹配在前、采样点匹配在后，按 path 去重。"""
        ql = q.lower()

        def hit(r: SwitchRow) -> bool:
            return (ql in r.label.lower()
                    or ql in r.full_label.lower()
                    or ql in r.path.lower())

        projects: List[SwitchRow] = []
        stations: List[SwitchRow] = []
        seen: set[str] = set()
        for r in self._all:
            if r.path not in seen and hit(r):
                seen.add(r.path)
                projects.append(r)
        for r in [*self._stations, *self._recents]:
            if r.path not in seen and hit(r):
                seen.add(r.path)
                stations.append(r)
        return [*projects, *stations]

    # ── 渲染 ─────────────────────────────────────────────────────────────
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
        self._body_lay.addWidget(self._make_action_bar())

    def _make_row_widget(self, idx: int, row: SwitchRow) -> QWidget:
        w = QWidget(self._body)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(2, 1, 2, 1)
        dot = "●" if row.is_current else ("⊘" if not row.exists else "○")
        text = f"{dot} {row.label}"
        if row.specimen_count is not None:
            text += f"　·　{row.specimen_count}标本"
        btn = QPushButton(text, w)
        btn.setObjectName("CommandRowCurrent" if row.is_current else "CommandRow")
        btn.setFlat(True)
        btn.setStyleSheet("text-align:left;")
        if row.exists:
            btn.clicked.connect(lambda _c=False, r=row: self.activate_row(r))
        else:
            btn.setEnabled(False)                      # 死路径标灰
        lay.addWidget(btn, 1)
        if not row.exists:
            reloc = QPushButton(tr("重新定位…"), w)
            reloc.setObjectName("CommandRowRelocate")
            reloc.clicked.connect(lambda _c=False, p=row.path: self.relocate.emit(p))
            lay.addWidget(reloc)
        pin = QPushButton("★" if row.pinned else "☆", w)
        pin.setObjectName("CommandRowPin")
        pin.setFlat(True)
        pin.clicked.connect(lambda _c=False, p=row.path: self.toggle_pin.emit(p))
        lay.addWidget(pin)
        return w

    def _make_action_bar(self) -> QWidget:
        bar = QFrame(self._body)
        bar.setObjectName("CommandActionBar")
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(0, 4, 0, 0)
        new_p = QPushButton(tr("＋新建项目"), bar)
        new_p.setObjectName("CommandNewProject")
        new_p.clicked.connect(lambda _c=False: self.new_project.emit())
        lay.addWidget(new_p)
        if self._current_root:
            new_s = QPushButton(tr("＋给当前项目加采样点"), bar)
            new_s.setObjectName("CommandNewStation")
            new_s.clicked.connect(
                lambda _c=False, r=self._current_root: self.new_station.emit(r)
            )
            lay.addWidget(new_s)
        browse = QPushButton(tr("全部项目…"), bar)
        browse.setObjectName("CommandBrowseAll")
        browse.clicked.connect(lambda _c=False: self.browse_all.emit())
        lay.addWidget(browse)
        return bar

    def activate_row(self, row: SwitchRow) -> None:
        """进入某行（点击 / Enter 共用入口）。"""
        if row.exists:
            self.entered.emit(row.path, row.root)

    # ── 搜索 & 键盘 ──────────────────────────────────────────────────────
    def _on_search(self, text: str) -> None:
        self._query = text or ""
        self._render()

    def keyPressEvent(self, event) -> None:   # noqa: N802 (Qt 命名约定)
        key = event.key()
        rows = self._rows
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and rows:
            step = 1 if key == Qt.Key.Key_Down else -1
            self._selected = max(0, min(len(rows) - 1, self._selected + step))
            event.accept()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and rows:
            if 0 <= self._selected < len(rows):
                self.activate_row(rows[self._selected])
            event.accept()
            return
        if key == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)
