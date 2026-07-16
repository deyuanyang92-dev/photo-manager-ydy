"""workspace_breadcrumb.py — 顶栏项目与照片保存位置入口.

顶栏只显示一个明确的当前位置按钮，例如「广西调查2026 › 北海区域 › 断面A」。
按钮弹层提供当前项目、当前照片保存位置、少量最近位置和三种开始场景；完整项目
与工作区列表始终由项目树统一管理，顶栏不复制全量目录树。

访问历史仍保留为会话内部能力，供已有调用兼容，但不再用一组小箭头占据顶栏。
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path
from typing import List, Optional, Tuple

from PyQt6.QtCore import QPoint, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from app.config import icons
from app.config.i18n import tr
from app.services.project_tree_service import RESERVED_DIR_NAMES
# 第 12 版 command 智能指挥台浮层 + 行类型 (Opus 2026-07-16)
from app.widgets.workspace_command_popup import SwitchRow, WorkspaceCommandPopup

# >3 级时折叠中间层为 「…」，保持顶栏一行放得下：根 / … / 父 / 叶
_MAX_SEGMENTS = 3

_SWITCHER_MODE_CLASSIC = "classic"
_SWITCHER_MODE_NAVIGATOR = "navigator"
_SWITCHER_MODE_LOCATOR = "locator"
_SWITCHER_MODES = (
    ("classic", "01  当前设计"),
    ("navigator", "02  导航台设计"),
    ("triple", "03  三功能紧凑栏"),
    ("om_capture", "04  OM Capture 式"),
    ("dual", "05  项目 / 工作区双选择"),
    ("breadcrumb", "06  分段路径"),
    ("omnibox", "07  搜索地址栏"),
    ("history", "08  历史切换器"),
    ("scenes", "09  三场景启动器"),
    ("instrument", "10  双层仪器面板"),
    ("locator", "11  定位台"),
    # 第 12 版：上下文自适应智能指挥台（回上次/同项目切点/新建即进/搜索 一处全干）
    ("command", "12  智能指挥台"),   # (Opus 2026-07-16)
)
_SWITCHER_MODE_VALUES = {key for key, _label in _SWITCHER_MODES}

# 新建文件夹名字禁含的字符（与 project_tree_view._new_subfolder 一致）
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


def project_tree_dirs(root: Optional[str], workspace: str, max_depth: int = 6) -> List[str]:
    """当前项目根目录下所有可进入目录（含 root/current），按树顺序展开."""
    ws = Path(workspace).resolve()
    base = Path(root).resolve() if root else ws.parent
    try:
        ws.relative_to(base)
    except ValueError:
        base = ws.parent

    out: List[str] = []

    def append_project_tree_dirs(p: Path, depth: int) -> None:
        # ``base`` is resolved once above and every descendant comes directly
        # from scandir(base). Re-resolving each node makes Path.realpath issue
        # lstat calls for every path component, which is extremely expensive
        # on WSL mounted drives.
        out.append(str(p))
        if depth >= max_depth:
            return
        try:
            entries = sorted(os.scandir(p), key=lambda e: e.name)
        except OSError:
            return
        for entry in entries:
            name = entry.name
            if name.startswith(".") or name in RESERVED_DIR_NAMES:
                continue
            try:
                if entry.is_dir():
                    append_project_tree_dirs(Path(entry.path), depth + 1)
            except OSError:
                continue

    append_project_tree_dirs(base, 0)
    current = str(ws)
    if current not in out:
        out.append(current)
    return out


def sibling_project_dirs(root: Optional[str], workspace: str) -> List[str]:
    """Directories beside the current project root/workspace, including itself."""
    ws = Path(workspace).resolve()
    anchor = Path(root).resolve() if root else ws
    parent = anchor.parent
    if parent == anchor:
        return [str(anchor)]
    out: List[str] = []
    try:
        entries = sorted(os.scandir(parent), key=lambda e: e.name)
    except OSError:
        return [str(anchor)]
    for entry in entries:
        name = entry.name
        if name.startswith(".") or name in RESERVED_DIR_NAMES:
            continue
        try:
            if entry.is_dir():
                out.append(str(Path(entry.path).resolve()))
        except OSError:
            continue
    current = str(anchor)
    if current not in out:
        out.append(current)
        out.sort()
    return out


class WorkspaceBreadcrumb(QWidget):
    """顶栏位置切换器：一个统一的项目 / 照片保存位置入口."""

    workspace_changed = pyqtSignal(str)   # 切换成功后的新工作区路径
    navigate_requested = pyqtSignal(str)  # 远跳目标 view_id
    new_workspace_requested = pyqtSignal()
    new_project_child_requested = pyqtSignal()
    # 顶栏提供三条开始路径：调查项目、独立工作区、打开已有内容。
    # 它们最终仍写入同一项目目录，并由项目树统一汇总。
    new_survey_project_requested = pyqtSignal()
    open_workspace_requested = pyqtSignal()
    project_search_requested = pyqtSignal(str)

    def __init__(self, ctx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self.setObjectName("WorkspaceBreadcrumb")
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(7)
        # 测试与外部按属性访问；refresh() 重建后保持指向最新控件
        self._placeholder_btn: Optional[QToolButton] = None
        self._segment_btns: List[QPushButton] = []
        self._leaf_btn: Optional[QToolButton] = None
        self._btn_prev: Optional[QToolButton] = None
        self._btn_next: Optional[QToolButton] = None
        self._btn_menu: Optional[QToolButton] = None
        self._btn_folder: Optional[QToolButton] = None
        self._siblings: List[str] = []
        self._peer_dirs: List[str] = []
        self._sib_index: int = -1
        self._collapsed = False
        self._mode_override: Optional[str] = None
        self._style_btn: Optional[QToolButton] = None
        self._omnibox: Optional[QLineEdit] = None
        self._display_text = ""
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
        if self._display_text:
            return self._display_text
        if self._placeholder_btn is not None:
            return self._placeholder_btn.text()
        parts: List[str] = [btn.text() for btn in self._segment_btns]
        if self._collapsed and parts:
            parts.insert(1, "…")
        if self._leaf_btn is not None:
            parts.append(self._leaf_btn.text())
        return " / ".join(parts)

    def _switcher_mode(self) -> str:
        if self._mode_override in _SWITCHER_MODE_VALUES:
            return self._mode_override
        settings = getattr(self._ctx, "settings", None)
        mode = str(getattr(settings, "workspace_switcher_mode", "classic") or "classic")
        if mode in _SWITCHER_MODE_VALUES:
            return mode
        return _SWITCHER_MODE_CLASSIC

    def _set_switcher_mode(self, mode: str) -> None:
        value = mode if mode in _SWITCHER_MODE_VALUES else _SWITCHER_MODE_CLASSIC
        if value == self._switcher_mode():
            return
        self._mode_override = value
        settings = getattr(self._ctx, "settings", None)
        if settings is not None:
            try:
                settings.workspace_switcher_mode = value
                flush = getattr(settings, "flush_to_disk", None)
                if callable(flush):
                    flush()
            except Exception:
                pass
        self.refresh()

    def _create_mode_selector(self, menu: QMenu, parent: QWidget) -> QComboBox:
        selector = QComboBox(parent)
        selector.setObjectName("WorkspaceSwitcherMode")
        selector.setAccessibleName(tr("项目入口界面样式"))
        for key, label in _SWITCHER_MODES:
            selector.addItem(tr(label), key)
        index = selector.findData(self._switcher_mode())
        selector.setCurrentIndex(max(0, index))
        selector.setMinimumWidth(112)

        def apply_mode(_index: int) -> None:
            mode = str(selector.currentData() or _SWITCHER_MODE_CLASSIC)
            if mode == self._switcher_mode():
                return
            menu.close()
            self._set_switcher_mode(mode)

        selector.currentIndexChanged.connect(apply_mode)
        return selector

    @staticmethod
    def _compact_chain_text(chain: List[Tuple[str, str]]) -> str:
        """Return a compact but unambiguous project-to-folder label."""
        names = [name for name, _path in chain]
        if len(names) > _MAX_SEGMENTS:
            names = [names[0], "…", names[-2], names[-1]]
        return " › ".join(names)

    def _configure_location_switcher(
        self,
        button: QToolButton,
        *,
        text: str,
        tooltip: str,
        icon_name: str,
        object_name: str = "WorkspaceLocationSwitcher",
        compatibility_alias: bool = True,
    ) -> None:
        """Apply the shared appearance and unified location menu."""
        button.setObjectName(object_name)
        button.setText(text)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setToolTip(tooltip)
        button.setAccessibleName(tr("当前项目与拍摄目录"))
        button.setAccessibleDescription(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMinimumHeight(32)
        button.setMaximumWidth(420)
        button.setIcon(
            icons.icon(
                icon_name,
                color=icons.TONE_MUTED,
                color_active=icons.TONE_ACCENT_HOVER,
            )
        )
        button.setIconSize(QSize(17, 17))
        # The full catalogue may contain hundreds of thousands of records.
        # Keep normal top-bar refresh O(1); read the bounded recent list only
        # when the user actually opens this menu.
        menu = self._build_workspace_menu(button, populate=False)
        menu.aboutToShow.connect(lambda: self._populate_workspace_menu(menu))
        button.setMenu(menu)
        if compatibility_alias:
            self._btn_menu = button
            self._btn_folder = button

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

    def _clear_breadcrumb_widgets(self) -> None:
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
        self._btn_menu = None
        self._btn_folder = None
        self._style_btn = None
        self._omnibox = None
        self._collapsed = False

    def refresh(self) -> None:
        # 外部切换检测：ctx.current 与历史指针不一致 → 视为外部进入（项目树/别处），
        # 入历史。内部 _switch_to / _history_step 已先行同步指针，此处为 no-op。
        ws = getattr(self._ctx, "current_project_dir", None)
        if ws:
            resolved = str(Path(ws).resolve())
            if self._current_history_entry() != resolved:
                self._record_history(resolved)
        updates_were_enabled = self.updatesEnabled()
        if updates_were_enabled:
            self.setUpdatesEnabled(False)
        try:
            self._clear_breadcrumb_widgets()
            chain = self._chain()
            mode = self._switcher_mode()
            self.setProperty("switcherMode", mode)
            if chain:
                self._display_text = self._compact_chain_text(chain)
            else:
                root = self._project_root_only()
                self._display_text = (
                    f"{Path(root).name} · {tr('尚未选择拍摄位置')}"
                    if root else tr("新建或打开项目")
                )
            if mode in {
                _SWITCHER_MODE_CLASSIC,
                _SWITCHER_MODE_NAVIGATOR,
                _SWITCHER_MODE_LOCATOR,
            }:
                if not chain:
                    self._build_placeholder()
                else:
                    self._build_chain(chain)
            else:
                self._build_mode_variant(mode, chain)
            self._add_style_button()
        finally:
            if updates_were_enabled:
                self.setUpdatesEnabled(True)
                self.updateGeometry()
                self.update()

    def _add_style_button(self) -> None:
        button = QToolButton(self)
        button.setObjectName("WorkspaceSwitcherStyleButton")
        button.setAccessibleName(tr("切换项目入口设计"))
        button.setToolTip(tr("切换项目入口设计"))
        button.setIcon(icons.icon("mdi6.tune-variant", color=icons.TONE_MUTED))
        button.setIconSize(QSize(16, 16))
        button.setFixedSize(30, 30)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(button)
        menu.setObjectName("WorkspaceSwitcherStyleMenu")
        current = self._switcher_mode()
        for key, label in _SWITCHER_MODES:
            action = menu.addAction(tr(label))
            action.setData(key)
            action.setCheckable(True)
            action.setChecked(key == current)
            action.triggered.connect(
                lambda _checked=False, mode=key: self._set_switcher_mode(mode)
            )
        button.setMenu(menu)
        self._lay.addWidget(button)
        self._style_btn = button

    def _variant_values(self, chain: List[Tuple[str, str]]) -> Tuple[str, str]:
        root = self._project_root_for_child()
        workspace = getattr(self._ctx, "current_project_dir", None)
        project = Path(root).name if root else tr("未选择项目")
        if workspace and root:
            try:
                rel = Path(workspace).resolve().relative_to(Path(root).resolve())
                location = " › ".join(rel.parts) if rel.parts else tr("项目本身")
            except ValueError:
                location = Path(workspace).name
        elif workspace:
            location = Path(workspace).name
        elif chain:
            location = chain[-1][0]
        else:
            location = tr("选择拍摄位置")
        return project, location

    def _variant_location_button(
        self,
        *,
        text: str,
        object_name: str,
        icon_name: str = "mdi6.camera-outline",
        compatibility_alias: bool = True,
    ) -> QToolButton:
        button = QToolButton(self)
        self._configure_location_switcher(
            button,
            text=text,
            tooltip=tr("切换项目、拍摄位置或打开已有内容"),
            icon_name=icon_name,
            object_name=object_name,
            compatibility_alias=compatibility_alias,
        )
        return button

    def _emit_workspace_creation(self) -> None:
        if self._project_root_for_child():
            self.new_project_child_requested.emit()
        else:
            self.new_workspace_requested.emit()

    # ── 第 12 版 command 智能指挥台 (Opus 2026-07-16) ─────────────────────
    def _build_command_variant(self, chain: List[Tuple[str, str]]) -> None:
        """顶栏单按钮；点击弹出上下文自适应浮层 WorkspaceCommandPopup。"""
        compact = self._compact_chain_text(chain) if chain else tr("新建或打开项目")
        button = self._variant_location_button(
            text=compact,
            object_name="WorkspaceCommandLocation",
            icon_name="mdi6.compass-outline",
            compatibility_alias=False,
        )
        # _variant_location_button 默认挂 InstantPopup 懒菜单；command 改成点击开自绘浮层
        button.setMenu(None)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.DelayedPopup)
        button.clicked.connect(
            lambda _c=False, b=button: self._open_command_popup(b)
        )
        self._lay.addWidget(button, 1)
        self._leaf_btn = button

    def _open_command_popup(self, button: Optional[QToolButton] = None) -> "WorkspaceCommandPopup":
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
        pop.new_station.connect(lambda _r=None: self._emit_workspace_creation())
        pop.browse_all.connect(lambda: self.open_workspace_requested.emit())
        pop.relocate.connect(self._relocate_dead_path)
        pop.toggle_pin.connect(self._toggle_pin)
        if button is not None:
            pop.move(button.mapToGlobal(button.rect().bottomLeft()))
            pop.show()
            pop.setFocus()
        return pop

    def _relocate_dead_path(self, _path: str) -> None:
        # 最小实现：复用「打开已有项目/工作区」入口让用户重指目录。
        self.open_workspace_requested.emit()

    def _toggle_pin(self, path: str) -> None:
        settings = getattr(self._ctx, "settings", None)
        if settings is None:
            return
        pins = list(getattr(settings, "switcher_pinned_workspaces", []) or [])
        try:
            resolved = str(Path(path).resolve())
        except OSError:
            resolved = path
        if resolved in pins:
            pins.remove(resolved)
        else:
            pins.insert(0, resolved)
        try:
            settings.switcher_pinned_workspaces = pins
            flush = getattr(settings, "flush_to_disk", None)
            if callable(flush):
                flush()
        except Exception:
            pass

    def _pinned_paths(self) -> set:
        settings = getattr(self._ctx, "settings", None)
        raw = getattr(settings, "switcher_pinned_workspaces", None) if settings else None
        return set(raw or [])

    def _command_full_label(self, root: Optional[str], path: str, leaf: str) -> str:
        if not root:
            return leaf
        try:
            rel = Path(path).resolve().relative_to(Path(root).resolve())
            parts = [Path(root).name, *rel.parts] if rel.parts else [Path(root).name]
            return " › ".join(parts)
        except ValueError:
            return leaf

    def _specimen_count(self, directory: str) -> Optional[int]:
        """缓存/实时标本数；**只读**：db 不存在则不触碰、返回 None（守红线）。"""
        try:
            db = Path(directory) / "_data" / "project.db"
            if not db.exists():
                return None
            from app.services import workspace_index_service
            stats = workspace_index_service.compute_workspace_index_stats(directory)
            n = stats.get("specimen_count") if stats else None
            return int(n) if n is not None else None
        except Exception:
            return None

    def command_current_project_rows(self) -> List["SwitchRow"]:
        """当前项目根下的采样点/工作区（态1 主区）。root 为空→空列表（态2）。"""
        from app.services import project_tree_service
        root = self._project_root_for_child()
        if not root:
            return []
        root_r = str(Path(root).resolve())
        current = str(Path(getattr(self._ctx, "current_project_dir", "") or "").resolve())
        pins = self._pinned_paths()
        rows: List[SwitchRow] = []
        try:
            found = project_tree_service.discover_workspaces(root_r)
        except Exception:
            found = []
        for item in found:
            path = str(item.get("path") or "")
            if not path:
                continue
            resolved = str(Path(path).resolve())
            name = str(item.get("name") or Path(resolved).name)
            rows.append(SwitchRow(
                kind="station", label=name,
                full_label=self._command_full_label(root_r, resolved, name),
                path=resolved, root=root_r,
                is_current=(resolved == current), exists=Path(resolved).exists(),
                specimen_count=self._specimen_count(resolved),
                last_opened=None, pinned=resolved in pins,
            ))
        return rows

    def command_recent_rows(self, limit: int = 5) -> List["SwitchRow"]:
        """最近去过的工作区（态1/2）；含死路径（不过滤，仅标灰）。"""
        from app.services import project_service
        projects = project_service.list_projects(
            project_service.default_user_projects_json_path()
        )
        current = str(Path(getattr(self._ctx, "current_project_dir", "") or "").resolve())
        pins = self._pinned_paths()
        ordered = sorted(
            reversed(projects),
            key=lambda p: p.get("lastOpenedAt") or 0,
            reverse=True,
        )
        out: List[SwitchRow] = []
        seen: set = set()
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
            out.append(SwitchRow(
                kind="station", label=name,
                full_label=self._command_full_label(root_r, resolved, name),
                path=resolved, root=root_r, is_current=False,
                exists=Path(resolved).exists(),
                specimen_count=self._specimen_count(resolved),
                last_opened=item.get("lastOpenedAt"), pinned=resolved in pins,
            ))
            if len(out) >= limit:
                break
        return out

    def command_all_project_rows(self) -> List["SwitchRow"]:
        """全部项目（态2 最近项目段 + 搜索源）。"""
        from app.services import project_service
        projects = project_service.list_projects(
            project_service.default_user_projects_json_path()
        )
        pins = self._pinned_paths()
        rows: List[SwitchRow] = []
        seen: set = set()
        for item in projects:
            if not item.get("isProjectRoot"):
                continue
            path = str(item.get("directory") or item.get("dir") or "")
            if not path:
                continue
            try:
                resolved = str(Path(path).resolve())
            except OSError:
                resolved = path
            if resolved in seen:
                continue
            seen.add(resolved)
            name = str(item.get("name") or Path(resolved).name)
            rows.append(SwitchRow(
                kind="project", label=name, full_label=name,
                path=resolved, root=resolved, is_current=False,
                exists=Path(resolved).exists(), specimen_count=None,
                last_opened=item.get("lastOpenedAt"), pinned=resolved in pins,
            ))
        return rows

    def _build_mode_variant(
        self, mode: str, chain: List[Tuple[str, str]]
    ) -> None:
        project, location = self._variant_values(chain)
        compact = self._compact_chain_text(chain) if chain else tr("选择已有")

        # 第 12 版 智能指挥台：单按钮 → 点击弹出自适应浮层 (Opus 2026-07-16)
        if mode == "command":
            self._build_command_variant(chain)
            return

        if mode == "triple":
            location_btn = self._variant_location_button(
                text=compact,
                object_name="WorkspaceTripleLocation",
            )
            project_btn = QPushButton(tr("＋ 项目"), self)
            project_btn.setObjectName("WorkspaceTripleProject")
            workspace_btn = QPushButton(tr("＋ 工作区"), self)
            workspace_btn.setObjectName("WorkspaceTripleWorkspace")
            project_btn.clicked.connect(
                lambda _checked=False: self.new_survey_project_requested.emit()
            )
            workspace_btn.clicked.connect(
                lambda _checked=False: self._emit_workspace_creation()
            )
            self._lay.addWidget(location_btn, 1)
            self._lay.addWidget(project_btn)
            self._lay.addWidget(workspace_btn)
            self._leaf_btn = location_btn
            return

        if mode == "om_capture":
            previous = QToolButton(self)
            previous.setObjectName("WorkspaceOmBack")
            previous.setText("◀")
            previous.setToolTip(tr("上一个访问位置"))
            previous.clicked.connect(lambda: self._history_step(-1))
            previous.setEnabled(self._history_pos > 0)
            location_btn = self._variant_location_button(
                text=location,
                object_name="WorkspaceOmLocation",
            )
            following = QToolButton(self)
            following.setObjectName("WorkspaceOmForward")
            following.setText("▶")
            following.setToolTip(tr("下一个访问位置"))
            following.clicked.connect(lambda: self._history_step(+1))
            following.setEnabled(0 <= self._history_pos < len(self._history) - 1)
            folder = QToolButton(self)
            folder.setObjectName("WorkspaceOmFolder")
            folder.setIcon(icons.icon("mdi6.folder-open-outline", color=icons.TONE_MUTED))
            folder.setToolTip(tr("打开已有项目或工作区"))
            folder.clicked.connect(lambda: self.open_workspace_requested.emit())
            for widget in (previous, location_btn, following, folder):
                self._lay.addWidget(widget)
            self._btn_prev = previous
            self._btn_next = following
            self._leaf_btn = location_btn
            return

        if mode == "dual":
            project_btn = self._variant_location_button(
                text=project,
                object_name="WorkspaceDualProject",
                icon_name="mdi6.folder-outline",
                compatibility_alias=False,
            )
            location_btn = self._variant_location_button(
                text=location,
                object_name="WorkspaceDualWorkspace",
            )
            self._lay.addWidget(project_btn)
            self._lay.addWidget(location_btn, 1)
            self._leaf_btn = location_btn
            return

        if mode == "breadcrumb":
            display_chain = chain[-4:] if chain else [(tr("选择已有"), "")]
            for index, (name, path) in enumerate(display_chain):
                segment = self._variant_location_button(
                    text=name,
                    object_name=(
                        "WorkspaceBreadcrumbSegment"
                        if index == 0 else "WorkspaceBreadcrumbPart"
                    ),
                    icon_name=(
                        "mdi6.folder-outline" if index == 0 else "mdi6.chevron-right"
                    ),
                    compatibility_alias=index == len(display_chain) - 1,
                )
                segment.setToolTip(path or tr("选择项目或工作区"))
                self._lay.addWidget(segment)
                self._segment_btns.append(segment)
                if index == len(display_chain) - 1:
                    self._leaf_btn = segment
            return

        if mode == "omnibox":
            search = QLineEdit(self)
            search.setObjectName("WorkspaceOmnibox")
            search.setText(compact)
            search.setPlaceholderText(tr("输入项目、区域、断面或工作区"))
            search.setClearButtonEnabled(True)
            search.returnPressed.connect(
                lambda: self.project_search_requested.emit(search.text().strip())
            )
            menu_btn = self._variant_location_button(
                text="",
                object_name="WorkspaceOmniboxMenu",
                icon_name="mdi6.chevron-down",
            )
            menu_btn.setFixedWidth(34)
            self._lay.addWidget(search, 1)
            self._lay.addWidget(menu_btn)
            self._omnibox = search
            self._leaf_btn = menu_btn
            return

        if mode == "history":
            previous = QToolButton(self)
            previous.setObjectName("WorkspaceHistoryBack")
            previous.setText("‹")
            previous.clicked.connect(lambda: self._history_step(-1))
            previous.setEnabled(self._history_pos > 0)
            location_btn = self._variant_location_button(
                text=compact,
                object_name="WorkspaceHistoryLocation",
            )
            following = QToolButton(self)
            following.setObjectName("WorkspaceHistoryForward")
            following.setText("›")
            following.clicked.connect(lambda: self._history_step(+1))
            following.setEnabled(0 <= self._history_pos < len(self._history) - 1)
            self._lay.addWidget(previous)
            self._lay.addWidget(location_btn, 1)
            self._lay.addWidget(following)
            self._btn_prev = previous
            self._btn_next = following
            self._leaf_btn = location_btn
            return

        if mode == "scenes":
            new_project = QPushButton(tr("新建项目"), self)
            new_project.setObjectName("WorkspaceSceneNewProject")
            workspace_btn = QPushButton(tr("工作区"), self)
            workspace_btn.setObjectName("WorkspaceSceneWorkspace")
            open_existing = QPushButton(tr("选择已有"), self)
            open_existing.setObjectName("WorkspaceSceneOpen")
            new_project.clicked.connect(
                lambda _checked=False: self.new_survey_project_requested.emit()
            )
            workspace_btn.clicked.connect(
                lambda _checked=False: self._emit_workspace_creation()
            )
            open_existing.clicked.connect(
                lambda _checked=False: self.open_workspace_requested.emit()
            )
            for button in (new_project, workspace_btn, open_existing):
                self._lay.addWidget(button)
            return

        if mode == "instrument":
            host = QWidget(self)
            host.setObjectName("WorkspaceInstrumentPanel")
            stack = QVBoxLayout(host)
            stack.setContentsMargins(8, 2, 8, 2)
            stack.setSpacing(0)
            project_btn = self._variant_location_button(
                text=f"{tr('项目')} · {project}",
                object_name="WorkspaceInstrumentProject",
                icon_name="mdi6.folder-outline",
                compatibility_alias=False,
            )
            location_btn = self._variant_location_button(
                text=f"{tr('拍摄')} · {location}",
                object_name="WorkspaceInstrumentWorkspace",
            )
            project_btn.setMinimumHeight(19)
            location_btn.setMinimumHeight(19)
            stack.addWidget(project_btn)
            stack.addWidget(location_btn)
            self._lay.addWidget(host, 1)
            self._leaf_btn = location_btn
            self._btn_menu = location_btn
            self._btn_folder = location_btn
            return

        self._build_chain(chain) if chain else self._build_placeholder()

    def _project_root_only(self) -> Optional[str]:
        """有当前项目、但尚未选择照片保存位置时返回项目路径."""
        if getattr(self._ctx, "current_project_dir", None):
            return None
        root = getattr(self._ctx, "current_project_root", None)
        if not root:
            root = getattr(getattr(self._ctx, "settings", None), "project_tree_root", None)
        return str(root) if root else None

    def _build_placeholder(self) -> None:
        root = self._project_root_only()
        mode = self._switcher_mode()
        if mode == _SWITCHER_MODE_LOCATOR:
            object_name = "WorkspaceLocatorSwitcher"
        elif mode == _SWITCHER_MODE_NAVIGATOR:
            object_name = "WorkspaceNavigatorSwitcher"
        else:
            object_name = "WorkspaceLocationSwitcher"
        if root:
            text = (
                f"{Path(root).name}  /  {tr('尚未选择拍摄位置')}"
                if mode == _SWITCHER_MODE_LOCATOR
                else f"{Path(root).name} · {tr('尚未选择拍摄位置')}"
            )
            btn = QToolButton()
            self._configure_location_switcher(
                btn,
                text=text,
                tooltip=tr(
                    "项目已打开，但还没有选择照片保存位置。\n"
                    "点击查看项目并选择断面或工作区。"
                ),
                icon_name="mdi6.folder-outline",
                object_name=object_name,
            )
            self._lay.addWidget(btn)
            self._placeholder_btn = btn
            return

        btn = QToolButton()
        self._configure_location_switcher(
            btn,
            text=tr("新建或打开项目"),
            tooltip=tr("新建调查项目、独立工作区，或打开已有项目"),
            icon_name="mdi6.folder-plus-outline",
            object_name=object_name,
        )
        self._lay.addWidget(btn)
        self._placeholder_btn = btn

    def _build_chain(self, chain: List[Tuple[str, str]]) -> None:
        self._collapsed = len(chain) > _MAX_SEGMENTS
        _leaf_name, leaf_path = chain[-1]
        # Directory choices are only needed when the user opens the menu.
        # Building them here recursively scanned the entire project tree on
        # every top-bar refresh (a multi-second operation on WSL/DrvFS).
        current = str(Path(leaf_path).resolve())
        self._siblings = [current]
        self._peer_dirs = []
        self._sib_index = 0

        root_path = chain[0][1]
        relative_folder = self._menu_path_label(leaf_path, root_path)
        tooltip = (
            f"{tr('项目')}：{chain[0][0]}\n"
            f"{tr('拍摄目录')}：{relative_folder}\n"
            + tr("点击切换项目、拍摄目录或打开项目树")
        )
        mode = self._switcher_mode()
        if mode == _SWITCHER_MODE_LOCATOR:
            # 定位台：项目 / 拍摄位置。单层也不写「独立工作区」。
            if len(chain) == 1:
                folder_text = tr("本目录")
            else:
                folder_text = relative_folder.replace(os.sep, " › ").replace("/", " › ")
            full_path = f"{chain[0][0]}  /  {folder_text}"
            object_name = "WorkspaceLocatorSwitcher"
            icon_name = "mdi6.map-marker-radius-outline"
        elif mode == _SWITCHER_MODE_NAVIGATOR:
            target = tr("独立工作区") if len(chain) == 1 else relative_folder
            full_path = f"{chain[0][0]}  /  {target}"
            object_name = "WorkspaceNavigatorSwitcher"
            icon_name = "mdi6.camera-outline"
        else:
            full_path = (
                f"{self._compact_chain_text(chain)} · {tr('独立工作区')}"
                if len(chain) == 1 else self._compact_chain_text(chain)
            )
            object_name = "WorkspaceLocationSwitcher"
            icon_name = "mdi6.camera-outline"
        leaf = QToolButton()
        self._configure_location_switcher(
            leaf,
            text=full_path,
            tooltip=tooltip,
            icon_name=icon_name,
            object_name=object_name,
        )
        self._lay.addWidget(leaf, 1)
        self._leaf_btn = leaf

    def _build_workspace_menu(
        self,
        parent: Optional[QWidget] = None,
        *,
        populate: bool = True,
    ) -> QMenu:
        """Build the single top-bar menu for all project/location actions."""
        menu = QMenu(parent or self)
        menu.setObjectName("WorkspaceLocationMenu")
        if populate:
            self._populate_workspace_menu(menu)
        return menu

    def _populate_workspace_menu(self, menu: QMenu) -> None:
        """Refresh the bounded location panel whenever the menu opens."""
        menu.clear()
        mode = self._switcher_mode()
        if mode == _SWITCHER_MODE_LOCATOR:
            self._add_locator_panel(menu)
        elif mode == _SWITCHER_MODE_NAVIGATOR:
            self._add_navigator_panel(menu)
        else:
            self._add_location_panel(menu)

    def _add_location_panel(self, menu: QMenu) -> QWidget:
        """Embed a bounded project/save-location panel in *menu*."""
        panel = QWidget(menu)
        panel.setObjectName("WorkspaceLocationPanel")
        panel.setMinimumWidth(390)
        panel.setMaximumWidth(430)
        grid = QGridLayout(panel)
        grid.setContentsMargins(16, 14, 16, 14)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(7)

        title = QLabel(tr("项目与照片保存位置"))
        title.setObjectName("WorkspaceLocationTitle")
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        grid.addWidget(title, 0, 0)
        grid.addWidget(self._create_mode_selector(menu, panel), 0, 1)

        subtitle = QLabel(tr("当前拍摄的照片将保存到下面的位置"))
        subtitle.setObjectName("WorkspaceLocationSubtitle")
        grid.addWidget(subtitle, 1, 0, 1, 2)

        row = 2
        search_host = QWidget(panel)
        search_lay = QHBoxLayout(search_host)
        search_lay.setContentsMargins(0, 0, 0, 0)
        search_lay.setSpacing(6)
        search = QLineEdit(search_host)
        search.setObjectName("WorkspaceLocationSearch")
        search.setPlaceholderText(tr("搜索项目、区域、断面或工作区"))
        search.setClearButtonEnabled(True)
        search_btn = QPushButton(tr("搜索"), search_host)
        search_btn.setObjectName("WorkspaceLocationSearchButton")
        search_btn.setIcon(icons.icon("mdi6.magnify", color=icons.TONE_ACCENT))
        search_lay.addWidget(search, 1)
        search_lay.addWidget(search_btn)

        def run_search() -> None:
            query = search.text().strip()
            menu.close()
            self.project_search_requested.emit(query)

        search.returnPressed.connect(run_search)
        search_btn.clicked.connect(run_search)
        grid.addWidget(search_host, row, 0, 1, 2)
        row += 1

        root = self._project_root_for_child()
        workspace = getattr(self._ctx, "current_project_dir", None)
        project_value = Path(root).name if root else tr("未选择项目")
        if workspace and root:
            try:
                rel = Path(workspace).resolve().relative_to(Path(root).resolve())
                folder_value = (
                    " › ".join(rel.parts)
                    if rel.parts else tr("项目本身（可直接拍照）")
                )
            except ValueError:
                folder_value = Path(workspace).name
        elif workspace:
            folder_value = Path(workspace).name
        else:
            folder_value = tr("尚未选择拍摄位置")

        def open_project_tree() -> None:
            menu.close()
            self.navigate_requested.emit("project_tree")

        project_label = QLabel(tr("项目"))
        project_label.setObjectName("WorkspaceLocationFieldLabel")
        project_btn = QPushButton(project_value)
        project_btn.setObjectName("WorkspaceLocationProject")
        project_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        project_btn.setIcon(icons.icon("mdi6.folder-outline", color=icons.TONE_WARN))
        project_btn.setToolTip(str(root or "") + tr("\n点击到项目树中查看或切换项目"))
        project_btn.clicked.connect(open_project_tree)
        grid.addWidget(project_label, row, 0, 1, 2)
        grid.addWidget(project_btn, row + 1, 0, 1, 2)

        folder_label = QLabel(tr("拍摄位置"))
        folder_label.setObjectName("WorkspaceLocationFieldLabel")
        folder_btn = QPushButton(folder_value)
        folder_btn.setObjectName("WorkspaceLocationFolder")
        folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        folder_btn.setIcon(icons.icon("mdi6.camera-outline", color=icons.TONE_ACCENT))
        folder_btn.setToolTip(str(workspace or "") + tr("\n点击到项目树中选择拍摄位置"))
        folder_btn.clicked.connect(open_project_tree)
        grid.addWidget(folder_label, row + 2, 0, 1, 2)
        grid.addWidget(folder_btn, row + 3, 0, 1, 2)

        hint = QLabel(tr("项目树统一汇总全部项目和工作区；这里仅显示当前保存目标。"))
        hint.setObjectName("WorkspaceLocationHint")
        hint.setWordWrap(True)
        grid.addWidget(hint, row + 4, 0, 1, 2)

        recents = [
            item for item in self._recent_workspaces(limit=8)
            if Path(item["directory"]).exists()
        ][:4]
        if recents:
            recent_lbl = QLabel(tr("最近拍摄位置"))
            recent_lbl.setObjectName("WorkspaceLocationSection")
            grid.addWidget(recent_lbl, row + 5, 0, 1, 2)
            chips_host = QWidget(panel)
            chips_grid = QGridLayout(chips_host)
            chips_grid.setContentsMargins(0, 0, 0, 0)
            chips_grid.setHorizontalSpacing(6)
            chips_grid.setVerticalSpacing(6)
            for index, item in enumerate(recents):
                ctx_name = (
                    Path(item["root"]).name
                    if item.get("root") else Path(item["directory"]).parent.name
                )
                label = (
                    f"{ctx_name} › {item['name']}"
                    if ctx_name and ctx_name != item["name"] else item["name"]
                )
                chip = QPushButton()
                chip.setObjectName("WorkspaceRecentChip")
                chip.setCursor(Qt.CursorShape.PointingHandCursor)
                chip.setMaximumWidth(178)
                chip.setText(chip.fontMetrics().elidedText(
                    label, Qt.TextElideMode.ElideMiddle, 158
                ))
                chip.setToolTip(f"{label}\n{item['directory']}")
                chip.clicked.connect(
                    lambda _c=False, path=item["directory"], rt=item.get("root"): (
                        menu.close(), self._switch_to_recent(path, rt)
                    )
                )
                chips_grid.addWidget(chip, index // 2, index % 2)
            grid.addWidget(chips_host, row + 6, 0, 1, 2)
            action_row = row + 7
        else:
            action_row = row + 5

        start_lbl = QLabel(tr("开始拍摄"))
        start_lbl.setObjectName("WorkspaceLocationSection")
        grid.addWidget(start_lbl, action_row, 0, 1, 2)

        new_project = QPushButton(tr("＋ 新建调查项目"))
        new_project.setObjectName("WorkspaceNewSurveyProject")
        new_project.setIcon(icons.icon("mdi6.folder-plus-outline", color=icons.TONE_ACCENT))
        new_project.setToolTip(tr("新建项目，再在项目树中添加区域和断面"))
        new_workspace = QPushButton(tr("＋ 独立工作区"))
        new_workspace.setObjectName("WorkspaceNewStandalone")
        new_workspace.setIcon(icons.icon("mdi6.camera-plus-outline", color=icons.TONE_ACCENT))
        new_workspace.setToolTip(tr("不建立调查层级，直接创建一个可拍照的工作区"))
        append_current = QPushButton(tr("＋ 追加到当前项目…"))
        append_current.setObjectName("WorkspaceAppendCurrent")
        append_current.setIcon(icons.icon("mdi6.file-tree-outline", color=icons.TONE_ACCENT))
        append_current.setToolTip(tr("在当前项目下追加区域、断面或自定义层级"))
        append_current.setEnabled(bool(self._project_root_for_child()))
        open_existing = QPushButton(tr("打开已有项目或工作区"))
        open_existing.setObjectName("WorkspaceOpenExisting")
        open_existing.setIcon(icons.icon("mdi6.folder-open-outline", color=icons.TONE_MUTED))
        manage_all = QPushButton(tr("管理全部项目"))
        manage_all.setObjectName("WorkspaceManageAll")
        manage_all.setIcon(icons.icon("mdi6.file-tree-outline", color=icons.TONE_MUTED))
        grid.addWidget(new_project, action_row + 1, 0)
        grid.addWidget(new_workspace, action_row + 1, 1)
        grid.addWidget(append_current, action_row + 2, 0, 1, 2)
        grid.addWidget(open_existing, action_row + 3, 0, 1, 2)
        grid.addWidget(manage_all, action_row + 4, 0, 1, 2)

        def create_survey_project() -> None:
            menu.close()
            self.new_survey_project_requested.emit()

        def create_workspace() -> None:
            menu.close()
            self.new_workspace_requested.emit()

        def open_workspace() -> None:
            menu.close()
            self.open_workspace_requested.emit()

        def append_to_current() -> None:
            menu.close()
            self.new_project_child_requested.emit()

        new_project.clicked.connect(create_survey_project)
        new_workspace.clicked.connect(create_workspace)
        append_current.clicked.connect(append_to_current)
        open_existing.clicked.connect(open_workspace)
        manage_all.clicked.connect(open_project_tree)

        action = QWidgetAction(menu)
        action.setDefaultWidget(panel)
        menu.addAction(action)
        return panel

    def _add_navigator_panel(self, menu: QMenu) -> QWidget:
        """Build the optional wide navigator without duplicating the project tree."""
        panel = QWidget(menu)
        panel.setObjectName("WorkspaceNavigatorPanel")
        panel.setMinimumWidth(640)
        panel.setMaximumWidth(720)
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(10)
        heading_box = QVBoxLayout()
        heading_box.setSpacing(1)
        title = QLabel(tr("项目与拍摄位置"), panel)
        title.setObjectName("WorkspaceNavigatorTitle")
        subtitle = QLabel(tr("快速进入最近位置，或开始新的拍摄工作"), panel)
        subtitle.setObjectName("WorkspaceNavigatorSubtitle")
        heading_box.addWidget(title)
        heading_box.addWidget(subtitle)
        header.addLayout(heading_box, 1)
        header.addWidget(self._create_mode_selector(menu, panel))
        outer.addLayout(header)

        search_host = QWidget(panel)
        search_lay = QHBoxLayout(search_host)
        search_lay.setContentsMargins(0, 0, 0, 0)
        search_lay.setSpacing(7)
        search = QLineEdit(search_host)
        search.setObjectName("WorkspaceNavigatorSearch")
        search.setPlaceholderText(tr("搜索项目、区域、断面或工作区"))
        search.setClearButtonEnabled(True)
        search_btn = QPushButton(tr("搜索"), search_host)
        search_btn.setObjectName("WorkspaceNavigatorSearchButton")
        search_btn.setIcon(icons.icon("mdi6.magnify", color=icons.TONE_ACCENT))
        search_lay.addWidget(search, 1)
        search_lay.addWidget(search_btn)
        outer.addWidget(search_host)

        def run_search() -> None:
            query = search.text().strip()
            menu.close()
            self.project_search_requested.emit(query)

        search.returnPressed.connect(run_search)
        search_btn.clicked.connect(run_search)

        body = QWidget(panel)
        body.setObjectName("WorkspaceNavigatorBody")
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(14)

        location_col = QVBoxLayout()
        location_col.setSpacing(8)
        current_title = QLabel(tr("当前照片保存位置"), body)
        current_title.setObjectName("WorkspaceNavigatorSection")
        location_col.addWidget(current_title)

        root = self._project_root_for_child()
        workspace = getattr(self._ctx, "current_project_dir", None)
        project_value = Path(root).name if root else tr("尚未选择项目")
        folder_value = tr("尚未选择拍摄位置")
        if workspace and root:
            try:
                rel = Path(workspace).resolve().relative_to(Path(root).resolve())
                folder_value = " › ".join(rel.parts) if rel.parts else tr("项目本身")
            except ValueError:
                folder_value = Path(workspace).name
        elif workspace:
            folder_value = Path(workspace).name

        current = QWidget(body)
        current.setObjectName("WorkspaceNavigatorCurrent")
        current_lay = QVBoxLayout(current)
        current_lay.setContentsMargins(13, 11, 13, 11)
        current_lay.setSpacing(5)
        project_line = QLabel(f"{tr('项目')}  ·  {project_value}", current)
        project_line.setObjectName("WorkspaceNavigatorProject")
        workspace_line = QLabel(f"{tr('拍摄')}  ·  {folder_value}", current)
        workspace_line.setObjectName("WorkspaceNavigatorWorkspace")
        workspace_line.setToolTip(str(workspace or ""))
        current_lay.addWidget(project_line)
        current_lay.addWidget(workspace_line)
        location_col.addWidget(current)

        recent_title = QLabel(tr("最近使用"), body)
        recent_title.setObjectName("WorkspaceNavigatorSection")
        location_col.addWidget(recent_title)
        recents = [
            item for item in self._recent_workspaces(limit=8)
            if Path(item["directory"]).exists()
        ][:5]
        if recents:
            for item in recents:
                context = (
                    Path(item["root"]).name
                    if item.get("root") else Path(item["directory"]).parent.name
                )
                label = (
                    f"{context}  ›  {item['name']}"
                    if context and context != item["name"] else item["name"]
                )
                recent = QPushButton(label, body)
                recent.setObjectName("WorkspaceNavigatorRecent")
                recent.setCursor(Qt.CursorShape.PointingHandCursor)
                recent.setToolTip(item["directory"])
                recent.clicked.connect(
                    lambda _checked=False, path=item["directory"], rt=item.get("root"): (
                        menu.close(), self._switch_to_recent(path, rt)
                    )
                )
                location_col.addWidget(recent)
        else:
            empty = QLabel(tr("还没有最近使用的拍摄位置"), body)
            empty.setObjectName("WorkspaceNavigatorEmpty")
            location_col.addWidget(empty)
        location_col.addStretch(1)
        body_lay.addLayout(location_col, 3)

        action_host = QWidget(body)
        action_host.setObjectName("WorkspaceNavigatorActions")
        action_lay = QVBoxLayout(action_host)
        action_lay.setContentsMargins(13, 11, 13, 11)
        action_lay.setSpacing(8)
        action_title = QLabel(tr("开始"), action_host)
        action_title.setObjectName("WorkspaceNavigatorSection")
        action_lay.addWidget(action_title)

        new_project = QPushButton(tr("＋ 新建调查项目"), action_host)
        new_project.setObjectName("Primary")
        new_workspace = QPushButton(tr("＋ 独立工作区"), action_host)
        new_workspace.setObjectName("Outline")
        append_current = QPushButton(tr("＋ 追加到当前项目"), action_host)
        append_current.setObjectName("Outline")
        append_current.setEnabled(bool(root))
        open_existing = QPushButton(tr("打开已有项目或工作区"), action_host)
        open_existing.setObjectName("Outline")
        for button in (new_project, new_workspace, append_current, open_existing):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            action_lay.addWidget(button)
        action_lay.addStretch(1)
        body_lay.addWidget(action_host, 2)
        outer.addWidget(body)

        def emit_and_close(signal) -> None:
            menu.close()
            signal.emit()

        new_project.clicked.connect(
            lambda _checked=False: emit_and_close(self.new_survey_project_requested)
        )
        new_workspace.clicked.connect(
            lambda _checked=False: emit_and_close(self.new_workspace_requested)
        )
        append_current.clicked.connect(
            lambda _checked=False: emit_and_close(self.new_project_child_requested)
        )
        open_existing.clicked.connect(
            lambda _checked=False: emit_and_close(self.open_workspace_requested)
        )

        action = QWidgetAction(menu)
        action.setDefaultWidget(panel)
        menu.addAction(action)
        return panel

    def _add_locator_panel(self, menu: QMenu) -> QWidget:
        """第 11 种：定位台 — 当前 → 最近 → 同级 → 开始 → 管理全部."""
        panel = QWidget(menu)
        panel.setObjectName("WorkspaceLocatorPanel")
        panel.setMinimumWidth(680)
        panel.setMaximumWidth(760)
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        heading = QVBoxLayout()
        heading.setSpacing(1)
        title = QLabel(tr("项目与拍摄位置"), panel)
        title.setObjectName("WorkspaceLocatorTitle")
        subtitle = QLabel(tr("看清当前归属，切换最近位置，或开始新工作"), panel)
        subtitle.setObjectName("WorkspaceLocatorSubtitle")
        heading.addWidget(title)
        heading.addWidget(subtitle)
        header.addLayout(heading, 1)
        header.addWidget(self._create_mode_selector(menu, panel))
        outer.addLayout(header)

        search_host = QWidget(panel)
        search_lay = QHBoxLayout(search_host)
        search_lay.setContentsMargins(0, 0, 0, 0)
        search_lay.setSpacing(7)
        search = QLineEdit(search_host)
        search.setObjectName("WorkspaceLocatorSearch")
        search.setPlaceholderText(tr("搜索项目、区域、断面或工作区  Ctrl+K"))
        search.setClearButtonEnabled(True)
        search_btn = QPushButton(tr("搜索"), search_host)
        search_btn.setObjectName("WorkspaceLocatorSearchButton")
        search_btn.setIcon(icons.icon("mdi6.magnify", color=icons.TONE_ACCENT))
        search_lay.addWidget(search, 1)
        search_lay.addWidget(search_btn)
        outer.addWidget(search_host)

        def run_search() -> None:
            query = search.text().strip()
            menu.close()
            self.project_search_requested.emit(query)

        search.returnPressed.connect(run_search)
        search_btn.clicked.connect(run_search)

        root = self._project_root_for_child()
        workspace = getattr(self._ctx, "current_project_dir", None)
        project_value = Path(root).name if root else tr("尚未选择项目")
        if workspace and root:
            try:
                rel = Path(workspace).resolve().relative_to(Path(root).resolve())
                folder_value = " › ".join(rel.parts) if rel.parts else tr("项目本身")
            except ValueError:
                folder_value = Path(workspace).name
        elif workspace:
            folder_value = Path(workspace).name
        else:
            folder_value = tr("尚未选择拍摄位置")

        # 1. 当前
        section_current = QLabel(tr("当前"), panel)
        section_current.setObjectName("WorkspaceLocatorSection")
        outer.addWidget(section_current)

        current = QWidget(panel)
        current.setObjectName("WorkspaceLocatorCurrent")
        current_lay = QVBoxLayout(current)
        current_lay.setContentsMargins(12, 10, 12, 10)
        current_lay.setSpacing(4)
        project_line = QLabel(f"{tr('项目')}  ·  {project_value}", current)
        project_line.setObjectName("WorkspaceLocatorProject")
        shoot_line = QLabel(
            f"{tr('拍摄')}  ·  {folder_value}    ● {tr('照片保存到这里')}",
            current,
        )
        shoot_line.setObjectName("WorkspaceLocatorShoot")
        shoot_line.setToolTip(str(workspace or root or ""))
        current_lay.addWidget(project_line)
        current_lay.addWidget(shoot_line)

        current_actions = QHBoxLayout()
        current_actions.setSpacing(8)
        enter_btn = QPushButton(tr("进入照片工作台"), current)
        enter_btn.setObjectName("Primary")
        enter_btn.setEnabled(bool(workspace))
        # Claude Code 修改 2026-07-14 — 主按钮曾只 menu.close() 不进工作台(死按钮)，须真跳 workbench
        # enter_btn.clicked.connect(lambda: menu.close())
        enter_btn.clicked.connect(
            lambda _c=False: (menu.close(), self.navigate_requested.emit("workbench"))
        )
        append_btn = QPushButton(tr("追加层级"), current)
        append_btn.setObjectName("Outline")
        append_btn.setEnabled(bool(root))
        settings_btn = QPushButton(tr("项目设置"), current)
        settings_btn.setObjectName("Outline")
        settings_btn.setEnabled(bool(root))
        manage_quick = QPushButton(tr("打开项目树"), current)
        manage_quick.setObjectName("Outline")
        for btn in (enter_btn, append_btn, settings_btn, manage_quick):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            current_actions.addWidget(btn)
        current_actions.addStretch(1)
        current_lay.addLayout(current_actions)
        outer.addWidget(current)

        def emit_and_close(signal) -> None:
            menu.close()
            signal.emit()

        append_btn.clicked.connect(
            lambda _c=False: emit_and_close(self.new_project_child_requested)
        )
        settings_btn.clicked.connect(
            lambda _c=False: (menu.close(), self.navigate_requested.emit("project_tree"))
        )
        manage_quick.clicked.connect(
            lambda _c=False: (menu.close(), self.navigate_requested.emit("project_tree"))
        )

        # 2. 最近拍摄位置
        section_recent = QLabel(tr("最近拍摄位置"), panel)
        section_recent.setObjectName("WorkspaceLocatorSection")
        outer.addWidget(section_recent)
        recents = [
            item for item in self._recent_workspaces(limit=8)
            if Path(item["directory"]).exists()
        ][:5]
        if recents:
            for item in recents:
                context = (
                    Path(item["root"]).name
                    if item.get("root") else Path(item["directory"]).parent.name
                )
                label = (
                    f"{context}  ›  {item['name']}"
                    if context and context != item["name"] else item["name"]
                )
                row = QPushButton(label, panel)
                row.setObjectName("WorkspaceLocatorRecent")
                row.setCursor(Qt.CursorShape.PointingHandCursor)
                row.setToolTip(item["directory"])
                # row.setStyleSheet("text-align: left; padding: 6px 10px;")  # polish: dead-code dup of QPushButton#WorkspaceLocatorRecent QSS rule (theme.py:1592-1601, resources/theme.qss:253-262) — same objectName already supplies these exact values, matching the WorkspaceLocatorPeer sibling which has no inline stylesheet; Sonnet 5 multi-agent review
                row.clicked.connect(
                    lambda _c=False, path=item["directory"], rt=item.get("root"): (
                        menu.close(), self._switch_to_recent(path, rt)
                    )
                )
                outer.addWidget(row)
        else:
            empty = QLabel(tr("还没有最近使用的拍摄位置"), panel)
            empty.setObjectName("WorkspaceLocatorEmpty")
            outer.addWidget(empty)

        # 3. 当前项目相邻位置（同级）
        section_peers = QLabel(tr("当前项目的相邻位置"), panel)
        section_peers.setObjectName("WorkspaceLocatorSection")
        outer.addWidget(section_peers)
        peer_host = QWidget(panel)
        peer_lay = QHBoxLayout(peer_host)
        peer_lay.setContentsMargins(0, 0, 0, 0)
        peer_lay.setSpacing(6)
        peers: List[str] = []
        if workspace:
            try:
                peers = [
                    p for p in sibling_dirs(workspace)
                    if str(Path(p).resolve()) != str(Path(workspace).resolve())
                ][:6]
            except Exception:
                peers = []
        if peers:
            for path in peers:
                try:
                    label = self._menu_path_label(path, root).replace(os.sep, " › ")
                except Exception:
                    label = Path(path).name
                peer_btn = QPushButton(label, peer_host)
                peer_btn.setObjectName("WorkspaceLocatorPeer")
                peer_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                peer_btn.setToolTip(path)
                peer_btn.clicked.connect(
                    lambda _c=False, p=path: (menu.close(), self._switch_to(p))
                )
                peer_lay.addWidget(peer_btn)
            peer_lay.addStretch(1)
            outer.addWidget(peer_host)
        else:
            peer_empty = QLabel(tr("当前层没有其它相邻位置"), panel)
            peer_empty.setObjectName("WorkspaceLocatorEmpty")
            outer.addWidget(peer_empty)

        # 4. 开始动作
        section_start = QLabel(tr("开始"), panel)
        section_start.setObjectName("WorkspaceLocatorSection")
        outer.addWidget(section_start)
        start_row = QHBoxLayout()
        start_row.setSpacing(8)
        new_project = QPushButton(tr("＋ 新建调查项目"), panel)
        new_project.setObjectName("Primary")
        new_workspace = QPushButton(tr("＋ 独立工作区"), panel)
        new_workspace.setObjectName("Outline")
        open_existing = QPushButton(tr("打开已有项目或工作区"), panel)
        open_existing.setObjectName("Outline")
        for button in (new_project, new_workspace, open_existing):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            start_row.addWidget(button)
        outer.addLayout(start_row)

        manage_all = QPushButton(tr("管理全部项目 →"), panel)
        manage_all.setObjectName("WorkspaceLocatorManageAll")
        manage_all.setCursor(Qt.CursorShape.PointingHandCursor)
        manage_all.setFlat(True)
        outer.addWidget(manage_all)

        new_project.clicked.connect(
            lambda _c=False: emit_and_close(self.new_survey_project_requested)
        )
        new_workspace.clicked.connect(
            lambda _c=False: emit_and_close(self.new_workspace_requested)
        )
        open_existing.clicked.connect(
            lambda _c=False: emit_and_close(self.open_workspace_requested)
        )
        manage_all.clicked.connect(
            lambda _c=False: (menu.close(), self.navigate_requested.emit("project_tree"))
        )

        action = QWidgetAction(menu)
        action.setDefaultWidget(panel)
        menu.addAction(action)
        return panel

    def _popup_projects_menu(self, anchor_btn: QWidget, host_menu: QMenu) -> None:
        """「项目」行下拉: 磁盘上的其他项目, 点了切换（复用 _switch_to_peer_root）。"""
        root = self._project_root_for_child()
        workspace = getattr(self._ctx, "current_project_dir", None) or root
        if not workspace:
            return
        sub = QMenu(anchor_btn)
        for path in sibling_project_dirs(root, workspace):
            act = sub.addAction(icons.icon("mdi6.folder-outline"), Path(path).name)
            is_current = root and Path(path) == Path(root)
            if is_current:
                act.setCheckable(True)
                act.setChecked(True)
            act.triggered.connect(
                lambda _c=False, p=path: (host_menu.close(), self._switch_to_peer_root(p))
            )
        sub.exec(anchor_btn.mapToGlobal(QPoint(0, anchor_btn.height())))

    def _popup_folders_menu(self, anchor_btn: QWidget, host_menu: QMenu) -> None:
        """「保存目录」行下拉: 本项目内全部目录, 点了切换（复用 _switch_to）。"""
        root = self._project_root_for_child()
        workspace = getattr(self._ctx, "current_project_dir", None)
        if not workspace and not root:
            return
        sub = QMenu(anchor_btn)
        base = root or (str(Path(workspace).parent) if workspace else None)
        current = str(Path(workspace).resolve()) if workspace else ""
        for path in project_tree_dirs(root, workspace or root):
            try:
                rel = str(Path(path).resolve().relative_to(Path(base).resolve())) if base else Path(path).name
            except (ValueError, OSError):
                rel = Path(path).name
            label = rel if rel != "." else tr("（项目根）")
            act = sub.addAction(icons.icon("mdi6.folder-outline"), label)
            if current and str(Path(path)) == str(Path(current)):
                act.setCheckable(True)
                act.setChecked(True)
            act.triggered.connect(
                lambda _c=False, p=path: (host_menu.close(), self._switch_to(p))
            )
        sub.exec(anchor_btn.mapToGlobal(QPoint(0, anchor_btn.height())))

    def _project_root_for_child(self) -> Optional[str]:
        """Current project container used by the top-bar child action."""
        root = getattr(self._ctx, "current_project_root", None)
        if not root:
            root = getattr(getattr(self._ctx, "settings", None),
                           "project_tree_root", None)
        return str(root) if root else None

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
        """切到指定工作区（下拉点选 / 新建文件夹 / 外部）→ 记入访问历史."""
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
        """切到最近使用的工作区；历史记录携带 root，跨调查区域时必须恢复它."""
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

    # ── ▾ 同目录菜单 + 新建文件夹 ────────────────────────────────────────

    def _current_workspace_name(self) -> str:
        ws = getattr(self._ctx, "current_project_dir", None)
        return Path(ws).name if ws else ""

    def _workspace_entry_label(self, path: str) -> str:
        """Human-readable menu label for a directory entry."""
        from app.services.project_tree_service import is_workspace, is_workspace_candidate
        name = os.path.basename(path.rstrip("\\/")) or path
        if is_workspace(path):
            return name
        if is_workspace_candidate(path):
            return f"{name} · 可导入"
        return name

    def _build_sibling_menu(self) -> QMenu:
        workspace = getattr(self._ctx, "current_project_dir", None)
        if workspace:
            root = getattr(self._ctx, "current_project_root", None)
            self._siblings = project_tree_dirs(root, workspace)
            self._peer_dirs = sibling_project_dirs(root, workspace)
            current = str(Path(workspace).resolve())
            try:
                self._sib_index = self._siblings.index(current)
            except ValueError:
                self._sib_index = -1
        menu = QMenu(self)
        cur_name = self._current_workspace_name()
        if cur_name:
            ws = getattr(self._ctx, "current_project_dir", None)
            cur = menu.addAction(f"当前：{cur_name}")
            cur.setEnabled(False)
            if ws:
                cur.setToolTip(str(Path(ws).resolve()))
            menu.addSeparator()
        self._add_project_tree_menu(menu)
        self._add_peer_projects_menu(menu)
        self._add_recent_menu(menu)
        menu.addSeparator()
        new_act = menu.addAction(f"➕ {tr('新建文件夹…')}")
        new_act.triggered.connect(self._on_new_section)
        return menu

    def _build_placeholder_menu(self) -> QMenu:
        return self._build_workspace_menu()

    def _add_project_tree_menu(self, menu: QMenu) -> None:
        """Submenu: folders inside the current project root."""
        from app.services.project_tree_service import is_workspace, is_workspace_candidate
        root = getattr(self._ctx, "current_project_root", None)
        tree_menu = menu.addMenu(tr("本项目内"))
        tree_menu.setEnabled(bool(self._siblings))
        if not self._siblings:
            empty = tree_menu.addAction(tr("（无子文件夹）"))
            empty.setEnabled(False)
            return
        for path in self._siblings:
            name = self._menu_path_label(path, root)
            if is_workspace(path):
                label = f"{name} · 工作区"
            elif is_workspace_candidate(path):
                label = f"{name} · 可导入"
            else:
                label = name
            act = tree_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(path == self._siblings[self._sib_index]
                           if self._sib_index >= 0 else False)
            act.setToolTip(path)
            act.triggered.connect(
                lambda _=False, p=path: self._switch_to(p))

    def _add_peer_projects_menu(self, menu: QMenu) -> None:
        """Submenu: other project folders sitting beside the current root on disk."""
        current_root = str(
            Path(getattr(self._ctx, "current_project_root", "") or "").resolve()
        )
        current_ws = str(
            Path(getattr(self._ctx, "current_project_dir", "") or "").resolve()
        )
        entries: list[tuple[str, str]] = []
        seen: set[str] = set()
        for path in self._peer_dirs:
            if current_root and path == current_root:
                continue
            try:
                resolved = str(Path(path).resolve())
            except OSError:
                resolved = path
            if resolved == current_ws or resolved in seen:
                continue
            seen.add(resolved)
            entries.append((self._workspace_entry_label(path), path))

        if not entries:
            return
        entries.sort(key=lambda item: item[0].casefold())
        peer_menu = menu.addMenu(tr("磁盘上的其他项目"))
        for label, path in entries:
            act = peer_menu.addAction(label)
            act.setToolTip(path)
            act.triggered.connect(
                lambda _=False, p=path: self._switch_to_peer_root(p))

    def _switch_to_peer_root(self, path: str) -> None:
        """Switch to a sibling project folder and make it its own root."""
        cur = getattr(self._ctx, "current_project_dir", None)
        if cur and str(Path(cur).resolve()) == str(Path(path).resolve()):
            return
        resolved = self._enter(path, root_override=path)
        if resolved is None:
            return
        self._record_history(resolved)
        self.refresh()
        self.workspace_changed.emit(resolved)

    @staticmethod
    def _menu_path_label(path: str, root: Optional[str]) -> str:
        if not root:
            return os.path.basename(path)
        try:
            rel = os.path.relpath(path, root)
        except ValueError:
            return os.path.basename(path)
        if rel == ".":
            return os.path.basename(os.path.normpath(path))
        return rel

    def _recent_workspaces(self, limit: int = 10) -> list[dict]:
        from app.services import project_service
        projects = project_service.list_projects(
            project_service.default_user_projects_json_path()
        )
        current = str(Path(getattr(self._ctx, "current_project_dir", "") or "").resolve())
        out: list[dict] = []
        seen: set[str] = set()
        # §7 旧: for item in reversed(projects) —— JSON 追加序的倒序, 不是真「最近」。
        #   现在按 lastOpenedAt 降序(record_recent_workspace 每次进入都会刷新它);
        #   老条目没有时间戳 → 按 0 处理, 排在有时间戳的后面(保持旧行为兜底)。
        ordered = sorted(
            reversed(projects),
            key=lambda p: p.get("lastOpenedAt") or 0,
            reverse=True,
        )
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
            name = str(item.get("name") or Path(resolved).name)
            root = item.get("root")
            out.append({"name": name, "directory": resolved, "root": str(root) if root else None})
            if len(out) >= limit:
                break
        return out

    def _add_recent_menu(self, menu: QMenu) -> None:
        recent = self._recent_workspaces()
        recent_menu = menu.addMenu(tr("最近使用"))
        if not recent:
            empty = recent_menu.addAction(tr("（暂无记录）"))
            empty.setEnabled(False)
            return
        for item in recent:
            label = self._recent_label(item)
            act = recent_menu.addAction(label)
            act.setToolTip(item["directory"])
            act.triggered.connect(
                lambda _=False, p=item["directory"], r=item.get("root"):
                self._switch_to_recent(p, r)
            )

    @staticmethod
    def _recent_label(item: dict) -> str:
        name = str(item.get("name") or "")
        directory = str(item.get("directory") or "")
        if "/" in name or "\\" in name:
            return name
        parent = Path(directory).parent.name if directory else ""
        return f"{parent} / {name}" if parent else name

    def _show_sibling_menu(self) -> None:
        if self._leaf_btn is None:
            return
        menu = self._build_sibling_menu()
        menu.exec(self._leaf_btn.mapToGlobal(
            self._leaf_btn.rect().bottomLeft()))

    def _show_placeholder_menu(self) -> None:
        if self._placeholder_btn is None:
            return
        menu = self._build_placeholder_menu()
        menu.exec(self._placeholder_btn.mapToGlobal(
            self._placeholder_btn.rect().bottomLeft()))

    def _new_section_parent(self) -> Optional[Path]:
        """新建文件夹的父目录.

        当前工作区就是项目根时，新建的是根下子目录；在某个子目录里时，
        新建的是当前工作区的同级目录。
        """
        ws = getattr(self._ctx, "current_project_dir", None)
        if not ws:
            return None
        workspace = Path(ws).resolve()
        root = getattr(self._ctx, "current_project_root", None)
        if root:
            root_path = Path(root).resolve()
            if workspace == root_path:
                return root_path
        return workspace.parent

    def _default_section_name(self) -> str:
        """预填「YYYYMMDD(」—— 用户续填地点后合上括号."""
        return f"{datetime.date.today().strftime('%Y%m%d')}("

    def _on_new_section(self) -> None:
        if self._new_section_parent() is None:
            return
        name, ok = QInputDialog.getText(
            self, tr("新建文件夹"),
            tr("文件夹名（如 20260612(草埔村)）："),
            text=self._default_section_name(),
        )
        name = (name or "").strip()
        if not ok or not name:
            return
        self.create_and_enter_section(name)

    def create_and_enter_section(self, name: str) -> Optional[str]:
        """在当前工作区父目录下建新文件夹并进入。名字非法/无法建 → 返回 None."""
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
            ui.warn(self, tr("新建文件夹"), tr("无法创建：") + f" {exc}")
            return None
        self._switch_to(str(target))
        return str(target.resolve()) if target.exists() else None
