"""project_tree_view.py — 项目树（新增，计划 glittery-riding-oasis 步骤 3）.

把「项目」当成一棵文件夹树来管理：选一个调查根目录（如 雷州半岛多样性/），
软件展示其下任意层子文件夹（断面a/b/c、厦门/漳州…），任一节点都可「进入工作区」拍照。
已含 _data/project.db 的子文件夹按原样认领；空文件夹进入时由工作区按需补建。

不破坏现有「项目总览」页——这是一个独立的新页。地区/样地/人员沿这棵树向上继承
（见 project_settings_service.get_effective），进入节点时把根记到 ctx.current_project_root。
"""
from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.config.theme import local_font_css
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
        self._btn_newregion = QPushButton("新建调查区域…")
        self._btn_newregion.setObjectName("Primary")
        self._btn_newregion.setToolTip("创建一个新的调查根目录，并在区域层保存负责人等默认信息")
        self._btn_newregion.clicked.connect(self._new_region)
        bar.addWidget(self._btn_newregion)
        self._btn_pick = QPushButton("选择根目录…")
        self._btn_pick.setToolTip("选择已有调查目录，显示其中所有可进入的子工作区")
        self._btn_pick.clicked.connect(self._pick_root)
        bar.addWidget(self._btn_pick)
        self._btn_newsub = QPushButton("新建断面/子节点")
        self._btn_newsub.setToolTip("在当前选中文件夹下新建断面、站位或任意子节点")
        self._btn_newsub.clicked.connect(self._new_subfolder)
        self._btn_newsub.setEnabled(False)
        bar.addWidget(self._btn_newsub)
        root.addLayout(bar)

        # Body splitter: tree | detail
        split = QSplitter(Qt.Orientation.Horizontal)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.itemSelectionChanged.connect(self._update_detail_panel_for_selected_project)
        self._tree.itemDoubleClicked.connect(lambda *_: self._enter_selected())
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_tree_context_menu)
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
        self._empty_state = QLabel(
            "还没有选择调查根目录。\n\n"
            "选择根目录：读取已有文件夹树，不改动原文件。\n"
            "新建调查区域：创建一个区域根目录，后续断面会继承区域设置。"
        )
        self._empty_state.setObjectName("EmptyState")
        self._empty_state.setWordWrap(True)
        dl.addWidget(self._empty_state)
        self._stats_row = QHBoxLayout()
        self._stats_row.setSpacing(8)
        dl.addLayout(self._stats_row)
        self._btn_enter = QPushButton("进入工作区拍照")
        self._btn_enter.setObjectName("Primary")
        self._btn_enter.setToolTip("把选中文件夹作为当前拍照工作区")
        self._btn_enter.clicked.connect(self._enter_selected)
        self._btn_enter.setEnabled(False)
        dl.addWidget(self._btn_enter)
        self._btn_summary = QPushButton("汇总导出…")
        self._btn_summary.setToolTip("从选中文件夹向下汇总标本记录并导出")
        self._btn_summary.setEnabled(False)
        self._btn_summary.clicked.connect(self._open_summary_export)
        dl.addWidget(self._btn_summary)
        self._btn_station_import = QPushButton("导入站位总表…")
        self._btn_station_import.setToolTip("把站位坐标和采集信息导入选中文件夹")
        self._btn_station_import.setEnabled(False)
        self._btn_station_import.clicked.connect(self._open_station_import)
        dl.addWidget(self._btn_station_import)
        dl.addStretch()
        split.addWidget(detail)
        split.setSizes([340, 360])

        root.addWidget(split, 1)

    def _apply_style(self) -> None:
        g = _theme()
        bg, panel, border = g("bg", "#0a1e24"), g("panel_2", "#0e2329"), g("border", "#21424a")
        text, muted, accent = g("text", "#c8dcd6"), g("muted", "#7fa49b"), g("accent", "#4fd1b8")
        border_medium = g("border_medium", border)
        accent_fg = g("accent_fg", "#ffffff")
        _ff = local_font_css()
        self.setStyleSheet(
            f"#{self.view_id}{{{_ff}background:{bg};}}"
            f"QLabel{{color:{text};background:transparent;}}"
            f"QLabel#PaneTitle{{color:{text};font-weight:600;font-size:15px;}}"
            f"QLabel#Muted{{color:{muted};font-size:12px;}}"
            f"QLabel#Mono{{color:{muted};font-family:monospace;font-size:11px;}}"
            f"QLabel#EmptyState{{color:{muted};background:{panel};border:1px dashed {border_medium};"
            f"border-radius:8px;padding:20px 22px;font-size:12px;line-height:1.5;}}"
            f"QPushButton{{background:{panel};color:{text};border:1px solid {border};"
            f"border-radius:5px;padding:5px 12px;font-size:13px;}}"
            f"QPushButton:hover{{background:{border};}}"
            f"QPushButton#Primary{{background:{accent};color:{accent_fg};border:1px solid {accent};}}"
            f"QPushButton:disabled{{background:{panel};color:{muted};border:1px solid {border_medium};}}"
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
            # Restore an EXPLICIT user-chosen root only. Never guess by walking
            # current_project_dir.parent — that produced wrong roots (a drive's
            # project dump dir is not a survey root) and hid the real projects
            # the user already created. With no root, _reload_project_tree shows every
            # recorded project as a flat list instead.
            saved = self.ctx.settings.project_tree_root
            if saved and Path(saved).is_dir():
                self._root = saved
        self._reload_project_tree()

    # ── Data / tree build ─────────────────────────────────────────────────────
    def _reload_project_tree(self) -> None:
        self._tree.clear()
        self._btn_enter.setEnabled(False)
        self._btn_summary.setEnabled(False)
        self._btn_station_import.setEnabled(False)
        if self._root and Path(self._root).is_dir():
            # ── Rooted scan mode (unchanged): one survey root, recursive tree ──
            self._btn_newsub.setEnabled(True)
            self._root_lbl.setText(self._root)
            self._empty_state.hide()
            tree = pts.scan_tree(self._root)
            root_item = self._build_item(tree)
            self._tree.addTopLevelItem(root_item)
            root_item.setExpanded(True)
            self._select_first_item()
            return

        # ── Flat-list mode: every project already recorded in user_projects.json ──
        # No survey root chosen -> recognize the projects the user already created
        # instead of showing a blank tree. Each project is a top-level node feeding
        # the same _build_item, so selection / stats / enter / summary / station-import
        # all keep working unchanged.
        self._btn_newsub.setEnabled(False)
        nodes = self._load_known_projects_nodes()
        if not nodes:
            self._root_lbl.setText("（未选根目录）")
            self._detail_name.setText("选择或创建调查区域")
            self._detail_path.setText("")
            self._empty_state.setText(
                "还没有选择调查根目录，也没有已记录的项目。\n\n"
                "选择根目录：读取已有文件夹树，不改动原文件。\n"
                "新建调查区域：创建一个区域根目录，后续断面会继承区域设置。"
            )
            self._empty_state.show()
            return
        self._root_lbl.setText(f"（全部已建项目 · {len(nodes)}）")
        self._empty_state.hide()
        for node in nodes:
            self._tree.addTopLevelItem(self._build_item(node))
        self._select_first_item()

    def _select_first_item(self) -> None:
        item = self._tree.topLevelItem(0)
        if item is None:
            return
        self._tree.setCurrentItem(item)
        item.setSelected(True)
        self._update_detail_panel_for_selected_project()

    def _load_known_projects_nodes(self) -> list:
        """Synthesize scan_tree-shaped nodes for every project recorded in
        ``user_projects.json``. Used by ``_reload_project_tree`` in flat-list mode (no survey
        root selected) so already-created projects are recognized without the
        user having to pick a common parent folder.

        Skips demo projects; dedupes by resolved directory; most-recent first
        (the json is append-ordered, so the latest-entered project is last and
        lands on top after reverse()). ``has_data`` mirrors ``pts.is_workspace``
        so the 📷/📁 label and all downstream logic stays correct even when a
        drive is unmounted.
        """
        from app.services.project_service import (
            default_user_projects_json_path,
            list_projects,
        )
        try:
            projects = list_projects(default_user_projects_json_path())
        except Exception:
            return []

        seen: set = set()
        nodes: list = []

        def _add_node(directory: str, name: str, *, candidate: bool = False) -> None:
            if not directory:
                return
            try:
                resolved = str(Path(directory).expanduser().resolve())
            except (OSError, ValueError):
                resolved = directory
            if resolved in seen:
                return
            seen.add(resolved)
            nodes.append({
                "name": name or Path(directory).name or "(未命名)",
                "path": directory,
                "has_data": pts.is_workspace(directory),
                "is_candidate": candidate,
                "children": [],
            })

        scan_roots: set[str] = set()
        for p in projects:
            if p.get("isDemo"):
                continue
            directory = p.get("directory") or p.get("dir") or ""
            try:
                scan_roots.add(str(Path(directory).expanduser().resolve().parent))
            except (OSError, ValueError):
                pass
            _add_node(directory, p.get("name") or Path(directory).name)

        has_recorded_projects = bool(nodes)

        for attr in ("current_project_root", "current_project_dir"):
            value = getattr(self.ctx, attr, None)
            if value:
                try:
                    scan_roots.add(str(Path(value).expanduser().resolve().parent))
                except (OSError, ValueError):
                    pass

        if not has_recorded_projects:
            try:
                # Fallback only when the recent-project list is empty. Scanning
                # a broad /mnt workspace on every 项目树 activation is expensive
                # under WSL/drvfs and makes basic navigation feel frozen.
                scan_roots.add(str(Path.cwd().resolve().parent))
            except OSError:
                pass

        # Keep recent projects first, then append discovered legacy/candidate
        # workspaces from nearby roots. Depth 1 is enough for sibling workspaces;
        # depth 2 catches one container folder such as "ceshi/ceshi3".
        nodes.reverse()  # most-recent recorded first
        for root in sorted(scan_roots if not has_recorded_projects else set()):
            try:
                candidates = pts.discover_workspace_candidates(root, max_depth=2)
            except OSError:
                candidates = []
            for c in candidates:
                _add_node(c["path"], c["name"], candidate=True)
        return nodes

    def _build_item(self, node: dict) -> QTreeWidgetItem:
        # Two-level semantics: a node with its own project.db is a 工作区 (where
        # you actually shoot); everything else is a 区域/文件夹 (an inheritance
        # anchor or just a container) — never call them all "项目".
        if node["has_data"]:
            label = f"📷 {node['name']}  ·  工作区"
        elif node.get("is_candidate"):
            label = f"📁 {node['name']}  ·  可导入"
        else:
            label = f"📁 {node['name']}"
        item = QTreeWidgetItem([label])
        item.setData(0, _PATH_ROLE, node["path"])
        for child in node["children"]:
            item.addChild(self._build_item(child))
        return item

    def _selected_path(self) -> Optional[str]:
        items = self._tree.selectedItems()
        if not items:
            return None
        return items[0].data(0, _PATH_ROLE)

    def _show_tree_context_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        if item is None:
            return
        self._tree.setCurrentItem(item)
        item.setSelected(True)
        path = item.data(0, _PATH_ROLE)
        if not path:
            return

        menu = QMenu(self._tree)

        enter_action = menu.addAction("进入工作区拍照")
        enter_action.triggered.connect(self._enter_selected)

        new_child_action = menu.addAction("新建子文件夹")
        new_child_action.triggered.connect(self._new_subfolder)

        menu.addSeparator()
        summary_action = menu.addAction("汇总导出…")
        summary_action.triggered.connect(self._open_summary_export)

        station_action = menu.addAction("导入站位总表…")
        station_action.triggered.connect(self._open_station_import)

        menu.addSeparator()
        open_action = menu.addAction("打开文件夹")
        open_action.triggered.connect(lambda _=False, p=path: self._open_directory(p))

        copy_action = menu.addAction("复制路径")
        copy_action.triggered.connect(
            lambda _=False, p=path: QApplication.clipboard().setText(str(p))
        )

        properties_action = menu.addAction("属性")
        properties_action.triggered.connect(
            lambda _=False, p=path: self._show_path_properties(str(p))
        )

        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _open_directory(self, path: str) -> None:
        directory = Path(path)
        if not directory.exists():
            ui.warn(self, "打开文件夹", f"目录不存在或磁盘未连接：\n{path}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def _show_path_properties(self, path: str) -> None:
        p = Path(path)
        exists = p.exists()
        try:
            child_count = sum(1 for child in p.iterdir() if child.is_dir()) if exists else 0
        except OSError:
            child_count = 0
        try:
            workspace = pts.is_workspace(path)
        except OSError:
            workspace = False
        kind = "工作区" if workspace else "文件夹"
        state = "存在" if exists else "不可访问"
        ui.info(
            self,
            "属性",
            "\n".join([
                f"名称：{p.name or path}",
                f"类型：{kind}",
                f"状态：{state}",
                f"子文件夹：{child_count}",
                f"路径：{path}",
            ]),
        )

    # ── Detail panel ──────────────────────────────────────────────────────────
    def _update_detail_panel_for_selected_project(self) -> None:
        path = self._selected_path()
        if not path:
            self._btn_enter.setEnabled(False)
            self._btn_summary.setEnabled(False)
            self._btn_station_import.setEnabled(False)
            self._btn_newsub.setEnabled(bool(self._root))
            self._empty_state.setText("选择左侧文件夹后，可进入工作区、汇总导出或导入站位表。")
            self._empty_state.show()
            return
        self._empty_state.hide()
        self._btn_newsub.setEnabled(True)
        self._btn_enter.setEnabled(True)
        self._btn_summary.setEnabled(True)
        self._btn_station_import.setEnabled(True)
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

    # ── Cross-workspace tools (append-only launchers) ──────────────────────────
    def _open_summary_export(self) -> None:
        """Open the cross-workspace summary export, rooted at the selected node."""
        path = self._selected_path()
        if not path:
            ui.info(self, "汇总导出", "请先选择一个文件夹。")
            return
        from app.widgets.summary_export_dialog import SummaryExportDialog
        dlg = SummaryExportDialog(ctx=self.ctx, initial_root=path, parent=self)
        dlg.exec()

    def _open_station_import(self) -> None:
        """Open the project station total-table import, rooted at the selected node."""
        path = self._selected_path()
        if not path:
            ui.info(self, "导入站位总表", "请先选择一个文件夹。")
            return
        from app.widgets.project_station_import_dialog import (
            ProjectStationImportDialog,
        )
        dlg = ProjectStationImportDialog(root_dir=path, parent=self)
        dlg.exec()

    # ── Actions ────────────────────────────────────────────────────────────────
    def _pick_root(self) -> None:
        start = self._root or (self.ctx.current_project_dir or "")
        path = ui.get_existing_directory(self, "选择项目根目录", start)
        if not path:
            return
        self._root = str(Path(path).resolve())
        pts.clear_project_tree_cache(self._root)
        self.ctx.settings.project_tree_root = self._root
        self._reload_project_tree()

    def _new_region(self) -> None:
        """Scaffold a 调查区域 root: create the folder, seed region-level
        settings (地区/负责人) as the inheritance anchor, then make it the tree
        root so 断面 created under it auto-inherit (set once, never re-type)."""
        from app.views.project_dialog import ProjectDialog
        dlg = ProjectDialog(mode="new", existing_projects=[], parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        proj = dlg.result_project()
        if not proj:
            return
        directory = proj.get("directory") or proj.get("dir") or ""
        if not directory:
            return
        try:
            from app.services.project_service import (
                default_user_projects_json_path,
                save_project_descriptor,
                seed_region_settings,
            )
            from app.views.overview_view import _load_projects
            seed_region_settings(
                directory,
                collector=proj.get("collector", ""),
                meta={
                    "name": proj.get("name", ""),
                    "location": proj.get("location", ""),
                    "year": proj.get("year", ""),
                    "date_range": proj.get("dateRange", ""),
                    "project_code": proj.get("projectCode", ""),
                },
            )
            save_project_descriptor(
                default_user_projects_json_path(),
                proj,
                existing_projects=_load_projects(),
            )
        except Exception as exc:  # pragma: no cover - defensive
            ui.warn(self, "新建调查区域", f"创建失败：{exc}")
            return
        self._root = str(Path(directory).resolve())
        pts.clear_project_tree_cache(self._root)
        self.ctx.settings.project_tree_root = self._root
        self._reload_project_tree()
        ui.info(
            self,
            "新建调查区域",
            "区域已建。地区/负责人已设在区域层，下面新建的断面会自动继承——"
            "在断面里设省份/样地可覆盖。",
        )

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
        pts.clear_project_tree_cache(self._root or parent)
        self._reload_project_tree()

    def _enter_selected(self) -> None:
        path = self._selected_path()
        if not path:
            return
        # 区域≠工作区: a node with subfolders that isn't yet a workspace is most
        # likely a 调查区域 (inheritance anchor), not where you shoot. Don't
        # forbid — just confirm, so a region doesn't accidentally become a
        # photo workspace.
        items = self._tree.selectedItems()
        item = items[0] if items else None
        if item is not None and item.childCount() > 0 and not pts.is_workspace(path):
            resp = ui.question(
                self,
                "进入工作区",
                f"「{Path(path).name}」下面还有子文件夹，看起来是调查区域。"
                "通常在下层断面里拍照。仍要把这一层当作工作区进入吗？",
            )
            from PyQt6.QtWidgets import QMessageBox
            if resp != QMessageBox.StandardButton.Yes:
                return
        # Single unified entry path: ensures dirs, sets dir + root (bounding the
        # settings-inheritance walk to this survey's tree), and records the node
        # into the recent list so it also shows up in 项目总览.
        from app.services.project_service import (
            default_user_projects_json_path,
            enter_workspace,
        )
        from app.db.db_manager import is_database_locked
        from app.services.project_paths import ProjectUnavailableError
        try:
            enter_workspace(
                self.ctx,
                path,
                root=self._root,
                projects_json_path=default_user_projects_json_path(),
            )
        except ProjectUnavailableError:
            ui.warn(self, "盘未连接",
                    f"该目录所在磁盘未挂载或路径不可用：\n{path}\n\n"
                    "请接回数据盘后再进入。数据仍在盘上，没有丢失。")
            return
        except sqlite3.Error as exc:
            if is_database_locked(exc):
                ui.warn(
                    self,
                    "项目数据库正忙",
                    "当前项目数据库正在被其它操作占用。\n\n"
                    "请稍等几秒后重试；如果一直出现，请关闭其它正在打开该项目的程序窗口，"
                    "或先回到照片工作区停止正在运行的合成/整理任务。",
                )
                return
            raise
        pts.clear_project_tree_cache(self._root or path)
        self.enter_workspace_requested.emit(path)
        main_win = self.window()
        if hasattr(main_win, "refresh_context_bar"):
            main_win.refresh_context_bar()
        if hasattr(main_win, "navigate_to"):
            main_win.navigate_to("workbench")
