"""project_tree_view.py — 项目树（新增，计划 glittery-riding-oasis 步骤 3）.

把「项目」当成一棵文件夹树来管理：选一个调查根目录（如 雷州半岛多样性/），
软件展示其下任意层子文件夹（断面a/b/c、厦门/漳州…），任一节点都可「进入工作区」拍照。
已含 _data/project.db 的子文件夹按原样认领；空文件夹进入时由工作区按需补建。

不破坏现有「项目总览」页——这是一个独立的新页。地区/样地/人员沿这棵树向上继承
（见 project_settings_service.get_effective），进入节点时把根记到 ctx.current_project_root。
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services import project_tree_service as pts
from app.utils import ui
from app.views.base_view import BaseView

if TYPE_CHECKING:
    from app.app_context import AppContext

_PATH_ROLE = Qt.ItemDataRole.UserRole


def _theme():
    try:
        from app.config.theme import TOKENS
        return TOKENS.get
    except Exception:  # pragma: no cover
        return lambda k, d=None: d


class ProjectTreeView(BaseView):
    """项目树 — 浏览/新建子文件夹，进入任一节点作为拍照工作区."""

    view_id = "project_tree"
    nav_title = "项目树"
    nav_icon = "🌲"

    enter_workspace_requested = pyqtSignal(str)  # carries the chosen node dir

    def __init__(self, ctx: "AppContext") -> None:
        self._root: Optional[str] = None
        super().__init__(ctx)

    # ── UI ──────────────────────────────────────────────────────────────────
    def _setup_ui(self) -> None:
        self._apply_style()
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # Header bar
        bar = QHBoxLayout()
        bar.setSpacing(8)
        title = QLabel("项目树")
        title.setObjectName("PaneTitle")
        bar.addWidget(title)
        self._root_lbl = QLabel("（未选根目录）")
        self._root_lbl.setObjectName("Muted")
        bar.addWidget(self._root_lbl, 1)
        self._btn_pick = QPushButton("选择根目录…")
        self._btn_pick.clicked.connect(self._pick_root)
        bar.addWidget(self._btn_pick)
        self._btn_newsub = QPushButton("新建子文件夹")
        self._btn_newsub.clicked.connect(self._new_subfolder)
        bar.addWidget(self._btn_newsub)
        root.addLayout(bar)

        # Body splitter: tree | detail
        split = QSplitter(Qt.Orientation.Horizontal)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.itemSelectionChanged.connect(self._on_select)
        self._tree.itemDoubleClicked.connect(lambda *_: self._enter_selected())
        split.addWidget(self._tree)

        detail = QWidget()
        dl = QVBoxLayout(detail)
        dl.setContentsMargins(12, 8, 8, 8)
        dl.setSpacing(10)
        self._detail_name = QLabel("选择左侧文件夹查看详情")
        self._detail_name.setObjectName("PaneTitle")
        dl.addWidget(self._detail_name)
        self._detail_path = QLabel("")
        self._detail_path.setObjectName("Mono")
        self._detail_path.setWordWrap(True)
        dl.addWidget(self._detail_path)
        self._stats_row = QHBoxLayout()
        self._stats_row.setSpacing(8)
        dl.addLayout(self._stats_row)
        self._btn_enter = QPushButton("进入工作区拍照")
        self._btn_enter.setObjectName("Primary")
        self._btn_enter.clicked.connect(self._enter_selected)
        self._btn_enter.setEnabled(False)
        dl.addWidget(self._btn_enter)
        dl.addStretch()
        split.addWidget(detail)
        split.setSizes([340, 360])

        root.addWidget(split, 1)

    def _apply_style(self) -> None:
        g = _theme()
        bg, panel, border = g("bg", "#0a1e24"), g("panel_2", "#0e2329"), g("border", "#21424a")
        text, muted, accent = g("text", "#c8dcd6"), g("muted", "#7fa49b"), g("accent", "#4fd1b8")
        accent_fg = g("accent_fg", "#ffffff")
        self.setStyleSheet(
            f"#{self.view_id}{{background:{bg};}}"
            f"QLabel{{color:{text};background:transparent;}}"
            f"QLabel#PaneTitle{{color:{text};font-weight:600;font-size:15px;}}"
            f"QLabel#Muted{{color:{muted};font-size:12px;}}"
            f"QLabel#Mono{{color:{muted};font-family:monospace;font-size:11px;}}"
            f"QPushButton{{background:{panel};color:{text};border:1px solid {border};"
            f"border-radius:5px;padding:5px 12px;font-size:13px;}}"
            f"QPushButton:hover{{background:{border};}}"
            f"QPushButton#Primary{{background:{accent};color:{accent_fg};border:1px solid {accent};}}"
            f"QPushButton:disabled{{color:{muted};}}"
            f"QTreeWidget{{background:{bg};color:{text};border:1px solid {border};"
            f"border-radius:6px;font-size:13px;}}"
            f"QTreeWidget::item{{padding:4px 2px;}}"
            f"QTreeWidget::item:selected{{background:{accent};color:{accent_fg};}}"
            f"QFrame#StatCard{{border:1px solid {border};border-radius:6px;background:{panel};}}"
        )

    # ── BaseView ────────────────────────────────────────────────────────────
    def on_activate(self) -> None:
        self._apply_style()
        if self._root is None:
            saved = self.ctx.settings.project_tree_root
            if not saved and self.ctx.current_project_dir:
                # Default the root to the parent of the active project, so the
                # current survey's siblings (断面) show up without extra clicks.
                saved = str(Path(self.ctx.current_project_dir).resolve().parent)
            if saved and Path(saved).is_dir():
                self._root = saved
        self._reload()

    # ── Data / tree build ─────────────────────────────────────────────────────
    def _reload(self) -> None:
        self._tree.clear()
        self._btn_enter.setEnabled(False)
        if not self._root or not Path(self._root).is_dir():
            self._root_lbl.setText("（未选根目录）")
            return
        self._root_lbl.setText(self._root)
        tree = pts.scan_tree(self._root)
        root_item = self._build_item(tree)
        self._tree.addTopLevelItem(root_item)
        root_item.setExpanded(True)

    def _build_item(self, node: dict) -> QTreeWidgetItem:
        tag = "  ·  已有数据" if node["has_data"] else ""
        item = QTreeWidgetItem([f"📁 {node['name']}{tag}"])
        item.setData(0, _PATH_ROLE, node["path"])
        for child in node["children"]:
            item.addChild(self._build_item(child))
        return item

    def _selected_path(self) -> Optional[str]:
        items = self._tree.selectedItems()
        if not items:
            return None
        return items[0].data(0, _PATH_ROLE)

    # ── Detail panel ──────────────────────────────────────────────────────────
    def _on_select(self) -> None:
        path = self._selected_path()
        if not path:
            self._btn_enter.setEnabled(False)
            return
        self._btn_enter.setEnabled(True)
        self._detail_name.setText(Path(path).name)
        self._detail_path.setText(path)
        self._render_stats(path)

    def _render_stats(self, path: str) -> None:
        while self._stats_row.count():
            it = self._stats_row.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        try:
            from app.services.project_service import get_project_summary
            s = get_project_summary(path)
            cards = [(str(s["specimenCount"]), "标本"),
                     (str(s["resultCount"]), "成片"),
                     (str(s["pendingJpgCount"]), "待处理")]
        except Exception:
            cards = [("—", "标本"), ("—", "成片"), ("—", "待处理")]
        g = _theme()
        accent = g("accent", "#4fd1b8")
        muted = g("muted", "#7fa49b")
        for value, label in cards:
            card = QFrame()
            card.setObjectName("StatCard")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(10, 6, 10, 6)
            cl.setSpacing(2)
            v = QLabel(value)
            v.setStyleSheet(f"color:{accent};font-size:20px;font-weight:700;")
            v.setAlignment(Qt.AlignmentFlag.AlignCenter)
            t = QLabel(label)
            t.setStyleSheet(f"color:{muted};font-size:11px;")
            t.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cl.addWidget(v)
            cl.addWidget(t)
            self._stats_row.addWidget(card, 1)

    # ── Actions ────────────────────────────────────────────────────────────────
    def _pick_root(self) -> None:
        start = self._root or (self.ctx.current_project_dir or "")
        path = ui.get_existing_directory(self, "选择项目根目录", start)
        if not path:
            return
        self._root = str(Path(path).resolve())
        self.ctx.settings.project_tree_root = self._root
        self._reload()

    def _new_subfolder(self) -> None:
        parent = self._selected_path() or self._root
        if not parent:
            ui.info(self, "项目树", "请先选择根目录或一个文件夹。")
            return
        name, ok = QInputDialog.getText(self, "新建子文件夹", "文件夹名称（如 断面a）：")
        name = (name or "").strip()
        if not ok or not name:
            return
        if any(c in name for c in ("/", "\\", "..")):
            ui.warn(self, "项目树", "名称不合法（不能含 / \\ ..）。")
            return
        try:
            (Path(parent) / name).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            ui.warn(self, "项目树", f"无法创建：{exc}")
            return
        self._reload()

    def _enter_selected(self) -> None:
        path = self._selected_path()
        if not path:
            return
        # Lazily ensure the standard workspace layout, then enter it.
        try:
            from app.services.project_service import open_project
            open_project(path)
        except Exception:
            pass
        self.ctx.current_project_dir = path
        # Remember the tree root so settings inheritance walks up to it.
        self.ctx.current_project_root = self._root
        self.enter_workspace_requested.emit(path)
        main_win = self.window()
        if hasattr(main_win, "refresh_context_bar"):
            main_win.refresh_context_bar()
        if hasattr(main_win, "navigate_to"):
            main_win.navigate_to("workbench")
