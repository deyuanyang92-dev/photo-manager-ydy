"""Create a survey project and its user-defined directory hierarchy."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QSize, QTimer, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.config import icons
from app.config.i18n import tr
from app.utils import ui


_BAD_NAME_BITS = ("/", "\\", "..")
_NODE_TYPE_ROLE = int(Qt.ItemDataRole.UserRole) + 1


class NewSurveyProjectDialog(QDialog):
    """Create a complete project or append levels below an existing node."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        default_parent_dir: str = "",
        *,
        append_target_dir: str = "",
        project_root: str = "",
    ) -> None:
        super().__init__(parent)
        self._append_mode = bool(str(append_target_dir or "").strip())
        self._project_root = str(project_root or "").strip()
        self.setWindowTitle(tr("追加项目层级") if self._append_mode else tr("新建调查项目"))
        self.setObjectName("NewSurveyProjectDialog")
        self.setMinimumSize(720, 560)
        self._updating_tree = False
        # Claude Code 修改 2026-07-14 — 自动命名已移除, _next_name 无调用方, 其计数器字段随之作废
        # §7 旧: self._new_node_counter = 1

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        heading = QLabel(
            tr("在所选位置下追加层级")
            if self._append_mode else tr("一次填写项目名称和全部层级")
        )
        heading.setObjectName("ProjectBuilderHeading")
        root.addWidget(heading)
        intro = QLabel(
            tr("右键或使用按钮，在所选位置下追加目录。")
            if self._append_mode else
            tr("输入项目名，然后在下方直接填写目录；勾选需要保存照片的末级目录。")
        )
        intro.setObjectName("ProjectBuilderIntro")
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QFormLayout()
        form.setSpacing(8)
        self._name = QLineEdit()
        self._name.setObjectName("ProjectBuilderName")
        self._name.setPlaceholderText(tr("如：广西调查2026"))
        if not self._append_mode:
            form.addRow(tr("项目名称 *"), self._name)
        else:
            project_name = Path(self._project_root or append_target_dir).name
            self._name.setText(project_name)
            self._name.setReadOnly(True)
            form.addRow(tr("所属项目"), self._name)

        dir_row = QHBoxLayout()
        dir_row.setContentsMargins(0, 0, 0, 0)
        initial_dir = append_target_dir if self._append_mode else default_parent_dir
        self._dir = QLineEdit(initial_dir)
        self._dir.setObjectName("ProjectBuilderDirectory")
        self._dir.setPlaceholderText(
            tr("选择已有项目或其中的子目录")
            if self._append_mode else tr("所有项目的上级目录，如 N:\\调查项目")
        )
        if self._append_mode:
            self._dir.setReadOnly(True)
        browse = QPushButton(tr("浏览…"))
        browse.setObjectName("Outline")
        icons.set_button_icon(browse, "mdi6.folder-open-outline", size=15)
        browse.clicked.connect(self._pick_dir)
        dir_row.addWidget(self._dir, 1)
        dir_row.addWidget(browse)
        dir_wrap = QWidget()
        dir_wrap.setLayout(dir_row)
        form.addRow(tr("追加到 *") if self._append_mode else tr("项目保存位置 *"), dir_wrap)
        root.addLayout(form)

        section_row = QHBoxLayout()
        section_title = QLabel(tr("项目目录"))
        section_title.setObjectName("ProjectBuilderSection")
        section_row.addWidget(section_title)
        section_row.addStretch(1)
        self._add_child_btn = QPushButton(tr("＋ 下级目录"))
        # self._add_child_btn.setObjectName("SoftAction")  # §7 旧: SoftAction 仅在 ProjectTreeView 内联样式定义, 不会级联到独立 QDialog, 按钮实际用回默认 QPushButton 样式 (polish: 改用全局已定义的 Outline token, Sonnet 5 multi-agent review)
        self._add_child_btn.setObjectName("Outline")
        self._add_sibling_btn = QPushButton(tr("＋ 同级目录"))
        self._add_sibling_btn.setObjectName("Outline")
        self._remove_btn = QPushButton(tr("移除"))
        # self._remove_btn.setObjectName("GhostDanger")  # §7 旧: GhostDanger 无任何 QSS 匹配, 静默退化成普通按钮样式 (polish: 改用主题已定义的 Danger 破坏性操作 token, Sonnet 5 multi-agent review)
        self._remove_btn.setObjectName("Danger")
        self._add_child_btn.clicked.connect(self._add_child)
        self._add_sibling_btn.clicked.connect(self._add_sibling)
        self._remove_btn.clicked.connect(self._remove_selected)
        section_row.addWidget(self._add_child_btn)
        section_row.addWidget(self._add_sibling_btn)
        section_row.addWidget(self._remove_btn)
        root.addLayout(section_row)

        hint = QLabel(tr("提示：右键目录可新建下级、同级、重命名或设为照片保存目录。"))
        hint.setObjectName("ProjectBuilderHint")
        root.addWidget(hint)

        self._tree = QTreeWidget()
        self._tree.setObjectName("ProjectHierarchyTree")
        self._tree.setColumnCount(2)
        self._tree.setHeaderLabels([tr("目录名称（可直接输入）"), tr("照片保存位置")])
        self._tree.setAlternatingRowColors(False)
        self._tree.setRootIsDecorated(True)
        self._tree.setUniformRowHeights(False)
        self._tree.setIndentation(22)
        self._tree.setMinimumHeight(270)
        header = self._tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._tree.setColumnWidth(1, 190)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_tree_context_menu)
        self._tree.itemSelectionChanged.connect(self._sync_action_state)
        root.addWidget(self._tree, 1)

        root_label = Path(append_target_dir).name if self._append_mode else tr("（项目名）")
        self._project_item = QTreeWidgetItem([
            root_label,
            tr("当前追加位置") if self._append_mode else tr("项目根目录（不存照片）"),
        ])
        self._project_item.setFlags(
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        )
        self._project_item.setIcon(0, icons.icon("mdi6.folder-outline", color=icons.TONE_WARN))
        self._project_item.setSizeHint(0, QSize(0, 38))
        self._project_item.setData(0, _NODE_TYPE_ROLE, "项目")
        self._tree.addTopLevelItem(self._project_item)
        # 2026-07-13 用户反馈"区域2区域2区域1非常怪异": 旧版预填真名字
        # "区域1"/"断面1"——只要用户不改就直接确认, 这两个名字原样落盘成真
        # 目录。改成空名 + placeholder 举例, 靠 _validation_problems() 里
        # 早已存在的"层级名称不能为空"拦住空手确认(见该函数, 未改动)。
        # §7 旧:
        #   if self._append_mode:
        #       area = self._project_item
        #       workspace = self._append_node(
        #           self._project_item, "断面1", node_type="断面", is_workspace=True
        #       )
        #   else:
        #       area = self._append_node(
        #           self._project_item, "区域1", node_type="区域", is_workspace=False
        #       )
        #       workspace = self._append_node(
        #           area, "断面1", node_type="断面", is_workspace=True
        #       )
        # Claude Code 修改 2026-07-14 — 空名默认行会被自身「层级名称不能为空」校验拒绝, 让刚打开的对话框否定自己的默认态; 恢复真实默认名 区域1/断面1 (QLineEdit 仍可点进原地改名)
        # §7 旧:
        #   if self._append_mode:
        #       area = self._project_item
        #       workspace = self._append_node(
        #           self._project_item, "", node_type="断面", is_workspace=True,
        #           placeholder=tr("如：断面201260612（在此拍照）"),
        #       )
        #   else:
        #       area = self._append_node(
        #           self._project_item, "", node_type="区域", is_workspace=False,
        #           placeholder=tr("如：断面A、日出海湾（任意层级，可再套下级）"),
        #       )
        #       workspace = self._append_node(
        #           area, "", node_type="断面", is_workspace=True,
        #           placeholder=tr("如：断面201260612（在此拍照）"),
        #       )
        if self._append_mode:
            area = self._project_item
            workspace = self._append_node(
                self._project_item, "断面1", node_type="断面", is_workspace=True,
                placeholder=tr("如：断面201260612（在此拍照）"),
            )
        else:
            area = self._append_node(
                self._project_item, "区域1", node_type="区域", is_workspace=False,
                placeholder=tr("如：断面A、日出海湾（任意层级，可再套下级）"),
            )
            workspace = self._append_node(
                area, "断面1", node_type="断面", is_workspace=True,
                placeholder=tr("如：断面201260612（在此拍照）"),
            )
        self._project_item.setExpanded(True)
        area.setExpanded(True)
        self._tree.setCurrentItem(workspace)

        self._preview = QLabel("")
        self._preview.setObjectName("ProjectHierarchyPreview")
        self._preview.setWordWrap(True)
        root.addWidget(self._preview)

        self._err = QLabel("")
        self._err.setObjectName("UnattributedWarning")
        self._err.setWordWrap(True)
        self._err.hide()
        root.addWidget(self._err)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        ok = btns.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText(tr("追加层级") if self._append_mode else tr("创建项目与工作区"))
        ok.setObjectName("Primary")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("取消"))
        btns.accepted.connect(self._try_accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        self._name.textChanged.connect(self._on_project_name_changed)
        self._dir.textChanged.connect(self._refresh_preview)
        self._sync_action_state()
        self._refresh_preview()

    def _pick_dir(self) -> None:
        chosen = ui.get_existing_directory(
            self,
            tr("选择要追加层级的项目或子目录")
            if self._append_mode else tr("选择项目保存位置"),
        )
        if chosen:
            self._dir.setText(chosen)
            if self._append_mode:
                self._project_item.setText(0, Path(chosen).name)
                from app.services.project_scaffold_service import find_project_root

                self._project_root = find_project_root(chosen) or ""
                self._name.setText(Path(self._project_root or chosen).name)

    def _append_node(
        self,
        parent: QTreeWidgetItem,
        name: str,
        *,
        node_type: str,
        is_workspace: bool,
        placeholder: str = "",
    ) -> QTreeWidgetItem:
        item = QTreeWidgetItem([name, ""])
        item.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
        )
        item.setIcon(0, icons.icon("mdi6.folder-outline", color=icons.TONE_MUTED))
        item.setSizeHint(0, QSize(0, 40))
        item.setData(0, _NODE_TYPE_ROLE, node_type)
        parent.addChild(item)

        name_edit = QLineEdit(name, self._tree)
        name_edit.setObjectName("ProjectHierarchyName")
        name_edit.setPlaceholderText(placeholder or tr("直接输入名称"))
        name_edit.setClearButtonEnabled(True)
        name_edit.setMinimumHeight(32)
        name_edit.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        name_edit.customContextMenuRequested.connect(
            lambda pos, node=item, edit=name_edit: self._show_item_context_menu(
                node, edit.mapToGlobal(pos)
            )
        )

        def update_name(text: str, node: QTreeWidgetItem = item) -> None:
            node.setText(0, text)
            self._refresh_preview()

        name_edit.textChanged.connect(update_name)
        self._tree.setItemWidget(item, 0, name_edit)

        workspace_check = QCheckBox(tr("照片保存到这里"), self._tree)
        workspace_check.setObjectName("ProjectHierarchyWorkspace")
        workspace_check.setMinimumHeight(32)
        workspace_check.setChecked(is_workspace)
        workspace_check.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        workspace_check.customContextMenuRequested.connect(
            lambda pos, node=item, check=workspace_check: self._show_item_context_menu(
                node, check.mapToGlobal(pos)
            )
        )
        workspace_check.toggled.connect(
            lambda checked, node=item: self._on_workspace_toggled(node, checked)
        )
        self._tree.setItemWidget(item, 1, workspace_check)
        self._refresh_node_icon(item)
        return item

    def _workspace_check(self, item: QTreeWidgetItem) -> Optional[QCheckBox]:
        widget = self._tree.itemWidget(item, 1)
        return widget if isinstance(widget, QCheckBox) else None

    def _name_edit(self, item: QTreeWidgetItem) -> Optional[QLineEdit]:
        widget = self._tree.itemWidget(item, 0)
        return widget if isinstance(widget, QLineEdit) else None

    def _focus_name(self, item: QTreeWidgetItem) -> None:
        edit = self._name_edit(item)
        if edit is None:
            return
        edit.setFocus()
        edit.selectAll()

    def _node_type(self, item: QTreeWidgetItem) -> str:
        return str(item.data(0, _NODE_TYPE_ROLE) or "自定义层级")

    def _is_workspace(self, item: QTreeWidgetItem) -> bool:
        check = self._workspace_check(item)
        return bool(check and check.isChecked())

    def _refresh_node_icon(self, item: QTreeWidgetItem) -> None:
        glyph = "mdi6.camera-outline" if self._is_workspace(item) else "mdi6.folder-outline"
        tone = icons.TONE_ACCENT if self._is_workspace(item) else icons.TONE_MUTED
        item.setIcon(0, icons.icon(glyph, color=tone))

    def _on_workspace_toggled(self, item: QTreeWidgetItem, checked: bool) -> None:
        if self._updating_tree:
            return
        if checked and item.childCount():
            self._updating_tree = True
            check = self._workspace_check(item)
            if check is not None:
                check.setChecked(False)
            self._updating_tree = False
            self._show_error(tr("照片保存目录必须是末级目录；请先移除或调整它的下级目录。"))
        else:
            self._err.hide()
        self._refresh_node_icon(item)
        self._sync_action_state()
        self._refresh_preview()

    @staticmethod
    def _hint_for(is_workspace: bool) -> str:
        """新节点的输入提示——只给例子, 从不预填成品名字.

        2026-07-13: 旧版靠深度硬编码 node_type="区域" if depth==1 else "断面",
        与"层级不固定, 想套几层套几层"(R-006)冲突, 而且用 `_next_name` 生成
        "区域2"/"断面2" 这种真能通过校验、能直接落盘的假名字。改成只给
        placeholder 举例, 名字必须由用户真的输入(`_validation_problems` 里
        "层级名称不能为空" 的检查未改, 天然拦住空手确认)。
        """
        if is_workspace:
            return tr("如：断面201260612（在此拍照）")
        return tr("如：断面A、日出海湾（任意层级，可再套下级）")

    def _add_child(self) -> None:
        parent = self._tree.currentItem() or self._project_item
        if parent is not self._project_item and self._is_workspace(parent):
            check = self._workspace_check(parent)
            if check is not None:
                check.setChecked(False)
        depth = self._depth(parent) + 1
        # §7 旧: node_type = "区域" if depth == 1 else "断面"; name = self._next_name(node_type)
        is_workspace = depth > 1
        item = self._append_node(
            parent, "", node_type="自定义层级", is_workspace=is_workspace,
            placeholder=self._hint_for(is_workspace),
        )
        parent.setExpanded(True)
        self._tree.setCurrentItem(item)
        QTimer.singleShot(0, lambda node=item: self._focus_name(node))
        self._refresh_preview()

    def _add_sibling(self) -> None:
        selected = self._tree.currentItem()
        if selected is None or selected is self._project_item:
            self._add_child()
            return
        parent = selected.parent() or self._project_item
        node_type = self._node_type(selected)
        is_workspace = self._is_workspace(selected)
        item = self._append_node(
            parent,
            "",
            node_type=node_type,
            is_workspace=is_workspace,
            placeholder=self._hint_for(is_workspace),
        )
        self._tree.setCurrentItem(item)
        QTimer.singleShot(0, lambda node=item: self._focus_name(node))
        self._refresh_preview()

    def _build_context_menu(self, item: QTreeWidgetItem) -> QMenu:
        menu = QMenu(self)
        is_root = item is self._project_item

        add_child = menu.addAction(
            tr("新建第一级目录") if is_root else tr("新建下级目录")
        )
        add_child.triggered.connect(
            lambda _checked=False, node=item: self._run_for_item(node, self._add_child)
        )
        if is_root:
            return menu

        add_sibling = menu.addAction(tr("新建同级目录"))
        add_sibling.triggered.connect(
            lambda _checked=False, node=item: self._run_for_item(node, self._add_sibling)
        )
        menu.addSeparator()
        rename = menu.addAction(tr("重命名"))
        rename.triggered.connect(
            lambda _checked=False, node=item: self._run_for_item(
                node, lambda: self._focus_name(node)
            )
        )
        workspace_text = (
            tr("取消照片保存目录") if self._is_workspace(item) else tr("设为照片保存目录")
        )
        workspace = menu.addAction(workspace_text)
        workspace.setEnabled(item.childCount() == 0)
        workspace.triggered.connect(
            lambda _checked=False, node=item: self._toggle_workspace(node)
        )
        menu.addSeparator()
        remove = menu.addAction(tr("从计划中移除"))
        remove.triggered.connect(
            lambda _checked=False, node=item: self._run_for_item(
                node, self._remove_selected
            )
        )
        return menu

    def _run_for_item(self, item: QTreeWidgetItem, callback) -> None:
        self._tree.setCurrentItem(item)
        callback()

    def _toggle_workspace(self, item: QTreeWidgetItem) -> None:
        if item.childCount():
            return
        check = self._workspace_check(item)
        if check is not None:
            check.toggle()

    def _show_item_context_menu(self, item: QTreeWidgetItem, global_pos) -> None:
        self._tree.setCurrentItem(item)
        self._build_context_menu(item).exec(global_pos)

    def _show_tree_context_menu(self, pos) -> None:
        item = self._tree.itemAt(pos) or self._project_item
        self._show_item_context_menu(item, self._tree.viewport().mapToGlobal(pos))

    def _remove_selected(self) -> None:
        selected = self._tree.currentItem()
        if selected is None or selected is self._project_item:
            return
        parent = selected.parent()
        if parent is not None:
            parent.removeChild(selected)
            self._tree.setCurrentItem(parent)
        self._refresh_preview()

    def _depth(self, item: QTreeWidgetItem) -> int:
        depth = 0
        current = item
        while current is not None and current is not self._project_item:
            depth += 1
            current = current.parent()
        return depth

    # Claude Code 修改 2026-07-14 — 死代码: 自动命名移除后无任何调用方(全仓 grep 确认), 按保留旧代码约定整体注释, 不删签名
    # §7 旧:
    #   def _next_name(self, node_type: str) -> str:
    #       self._new_node_counter += 1
    #       prefix = node_type if node_type != "自定义层级" else "层级"
    #       return f"{prefix}{self._new_node_counter}"

    def _sync_action_state(self) -> None:
        selected = self._tree.currentItem()
        is_root = selected is None or selected is self._project_item
        self._add_sibling_btn.setEnabled(not is_root)
        self._remove_btn.setEnabled(not is_root)
        self._add_child_btn.setText(tr("＋ 下级目录"))

    def _on_project_name_changed(self, text: str) -> None:
        if self._append_mode:
            return
        self._project_item.setText(0, text.strip() or tr("（项目名）"))
        self._refresh_preview()

    def _node_dict(self, item: QTreeWidgetItem) -> dict:
        name_edit = self._name_edit(item)
        return {
            "name": (name_edit.text() if name_edit is not None else item.text(0)).strip(),
            "type": self._node_type(item),
            "is_workspace": self._is_workspace(item),
            "children": [self._node_dict(item.child(i)) for i in range(item.childCount())],
        }

    def hierarchy(self) -> list[dict]:
        return [
            self._node_dict(self._project_item.child(i))
            for i in range(self._project_item.childCount())
        ]

    def site_names(self) -> list[str]:
        """Return workspace paths relative to the new project root."""
        out: list[str] = []

        def visit(nodes: list[dict], prefix: tuple[str, ...] = ()) -> None:
            for node in nodes:
                path = (*prefix, node["name"])
                if node["is_workspace"]:
                    out.append("/".join(path))
                visit(node["children"], path)

        visit(self.hierarchy())
        return out

    def _refresh_preview(self) -> None:
        if not hasattr(self, "_preview"):
            return
        project_name = self._name.text().strip() or tr("（项目名）")
        parent = self._dir.text().strip()
        if self._append_mode:
            target = parent or tr("（未选择）")
        else:
            target = str(Path(parent) / project_name) if parent else project_name
        workspace_count = len(self.site_names())
        action = tr("追加到") if self._append_mode else tr("保存到")
        self._preview.setText(
            tr("{action}：{target}    ·    {count} 个照片保存目录").format(
                action=action, target=target, count=workspace_count
            )
        )

    def _show_error(self, text: str) -> None:
        self._err.setText("⚠ " + text)
        self._err.show()

    def _validation_problems(self) -> list[str]:
        problems: list[str] = []
        name = self._name.text().strip()
        parent = self._dir.text().strip()
        if self._append_mode:
            if not parent or not Path(parent).is_dir():
                problems.append(tr("请选择一个已存在的项目或子目录"))
            else:
                from app.services.project_tree_service import is_workspace

                if is_workspace(parent):
                    problems.append(tr("照片保存目录必须是末级目录，不能在其内部追加目录"))
                for node in self.hierarchy():
                    if (Path(parent) / node["name"]).exists():
                        problems.append(tr("目标位置已存在同名目录：{name}").format(name=node["name"]))
            if not self.hierarchy():
                problems.append(tr("请至少添加一个层级"))
        else:
            if not name:
                problems.append(tr("请填写项目名称"))
            if any(bit in name for bit in _BAD_NAME_BITS):
                problems.append(tr("项目名称不能包含 /、\\ 或 .."))
            if not parent or not Path(parent).is_dir():
                problems.append(tr("请选择一个已存在的项目保存位置"))
            if name and parent:
                target = Path(parent) / name
                if target.exists() and any(target.iterdir()):
                    problems.append(tr("已存在同名且非空的项目：{name}").format(name=name))
            # Claude Code 修改 2026-07-14 — 新建分支原先漏查层级为空, 删光所有行会静默创建零工作区项目; 与 append 分支对齐, 补同一检查
            if not self.hierarchy():
                problems.append(tr("请至少添加一个层级"))

        def validate_siblings(nodes: list[dict]) -> None:
            names: set[str] = set()
            for node in nodes:
                node_name = node["name"].strip()
                if not node_name:
                    problems.append(tr("层级名称不能为空"))
                elif any(bit in node_name for bit in _BAD_NAME_BITS):
                    problems.append(tr("层级名称不合法：{name}").format(name=node_name))
                folded = node_name.casefold()
                if folded in names:
                    problems.append(tr("同一层级下名称重复：{name}").format(name=node_name))
                names.add(folded)
                if node["is_workspace"] and node["children"]:
                    problems.append(tr("照片保存目录必须是末级目录：{name}").format(name=node_name))
                validate_siblings(node["children"])

        validate_siblings(self.hierarchy())
        return list(dict.fromkeys(problems))

    def _try_accept(self) -> None:
        problems = self._validation_problems()
        if problems:
            self._show_error("；".join(problems))
            return
        self.accept()

    def values(self) -> dict:
        values = {
            "mode": "append" if self._append_mode else "create",
            "parent_dir": self._dir.text().strip(),
            "name": self._name.text().strip(),
            "structure": self.hierarchy(),
        }
        if self._append_mode:
            values["target_dir"] = self._dir.text().strip()
            values["project_root"] = self._project_root
        return values
