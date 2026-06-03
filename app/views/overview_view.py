"""overview_view.py — 项目总览: project list with enter/detail actions.

Faithfully mirrors the real web「项目总览」page:

  overview-header-actions
    h2 "项目总览"  [+ 新建项目]  [+ 打开工作区]

  photo-toolbar (time filter)
    "时间筛选"  [全部]  [2026]  [2025]

  specimen-table-wrap
    specimen-table  columns:
      项目名称 / 磁盘目录 / 时间 / 地点 / 负责人 / 操作
      操作 = [进入工作区]  [详情]

Oracle:
  - app.js:13856-13943  (renderOverview, project-list branch)
  - styles.css:.overview-header / .specimen-table
  - pages_dom.json:"项目总览"
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.views.base_view import BaseView

if TYPE_CHECKING:
    from app.app_context import AppContext


# ── Resolve the shared data directory (mirrors taxonomy_view pattern) ──────────
_HERE = Path(__file__).resolve()
_PROJECT_ROOT = _HERE.parent.parent.parent          # photo-platform-ydy-v3/
_GLOBAL_DATA_DIR = _PROJECT_ROOT / "data"
_USER_PROJECTS_JSON = _GLOBAL_DATA_DIR / "user_projects.json"

# ── Fallback to the web-prototype data dir (same network drive) ───────────────
_WEB_PROTO_DIR = _PROJECT_ROOT.parent / "photo-platform-ydy" / "prototype-photo-gui" / "data"
_WEB_PROJECTS_JSON = _WEB_PROTO_DIR / "user_projects.json"


def _resolve_projects_json() -> Path:
    """Return the user_projects.json path we should read.

    Priority:
      1. ``data/user_projects.json`` alongside this repo (writable app data)
      2. The web-prototype ``data/user_projects.json`` (real working data)
    Falls back to (1) even if it doesn't exist yet (returns the path).
    """
    if _USER_PROJECTS_JSON.exists():
        return _USER_PROJECTS_JSON
    if _WEB_PROJECTS_JSON.exists():
        return _WEB_PROJECTS_JSON
    return _USER_PROJECTS_JSON


def _load_projects() -> list[dict]:
    """Load the project list from user_projects.json.  Returns [] on any error."""
    from app.services.project_service import list_projects
    path = _resolve_projects_json()
    return list_projects(str(path))


def _save_projects(projects: list[dict]) -> None:
    """Persist the project list to the app-local user_projects.json."""
    _GLOBAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    data = {"version": 1, "projects": projects}
    _USER_PROJECTS_JSON.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── Detail dialog ─────────────────────────────────────────────────────────────

class _ProjectDetailDialog(QDialog):
    """Simple project info modal — mirrors the web 'overview detail' branch."""

    def __init__(self, proj: dict, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("项目详情")
        self.setMinimumWidth(480)
        self.setMinimumHeight(280)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        # Title
        name_lbl = QLabel(f"{proj.get('name', '—')}  {proj.get('year', '')}")
        name_lbl.setObjectName("Title")
        layout.addWidget(name_lbl)

        loc_lbl = QLabel(f"{proj.get('location', '—')}　{proj.get('dateRange', proj.get('date_range', '—'))}")
        loc_lbl.setObjectName("Muted")
        layout.addWidget(loc_lbl)

        layout.addSpacing(8)

        # Key–value rows
        rows = [
            ("磁盘目录", proj.get("directory") or proj.get("dir") or "—"),
            ("负责人",   proj.get("collector") or "—"),
            ("地点",     proj.get("location") or "—"),
            ("时间",     proj.get("dateRange") or proj.get("date_range") or "—"),
            ("项目 ID",  proj.get("id") or "—"),
        ]
        for label, value in rows:
            row = QHBoxLayout()
            key_w = QLabel(f"{label}：")
            key_w.setObjectName("Muted")
            key_w.setMinimumWidth(80)
            key_w.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            val_w = QLabel(value)
            val_w.setWordWrap(True)
            if label == "磁盘目录":
                val_w.setStyleSheet("font-family: monospace; font-size: 11px;")
            row.addWidget(key_w)
            row.addWidget(val_w, stretch=1)
            layout.addLayout(row)

        layout.addStretch()

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        layout.addWidget(btns)


# ── New-project dialog ─────────────────────────────────────────────────────────

class _NewProjectDialog(QDialog):
    """Minimal 「新建项目」modal — mirrors the web new-project modal fields."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新建项目")
        self.setMinimumWidth(460)

        from PyQt6.QtWidgets import QLineEdit, QFormLayout
        self._form = QFormLayout()
        self._form.setContentsMargins(20, 16, 20, 8)
        self._form.setSpacing(10)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("例如：厦门潮间带多毛类调查")
        self._form.addRow("项目名称：", self._name_edit)

        self._dir_edit = QLineEdit()
        self._dir_edit.setPlaceholderText("选择或输入磁盘目录")
        dir_row = QHBoxLayout()
        dir_row.setSpacing(6)
        dir_row.addWidget(self._dir_edit, stretch=1)
        browse_btn = QPushButton("浏览…")
        browse_btn.clicked.connect(self._browse)
        dir_row.addWidget(browse_btn)
        dir_widget = QWidget()
        dir_widget.setLayout(dir_row)
        self._form.addRow("磁盘目录：", dir_widget)

        self._location_edit = QLineEdit()
        self._location_edit.setPlaceholderText("省份 / 样地名")
        self._form.addRow("地点：", self._location_edit)

        self._collector_edit = QLineEdit()
        self._collector_edit.setPlaceholderText("负责人姓名")
        self._form.addRow("负责人：", self._collector_edit)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.addLayout(self._form)
        root.addWidget(btns)

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择项目目录")
        if path:
            self._dir_edit.setText(path)
            if not self._name_edit.text():
                self._name_edit.setText(Path(path).name)

    def _on_accept(self) -> None:
        name = self._name_edit.text().strip()
        directory = self._dir_edit.text().strip()
        if not directory:
            QMessageBox.warning(self, "新建项目", "请选择磁盘目录。")
            return
        if not name:
            name = Path(directory).name
        self.accept()

    def result_dict(self) -> dict:
        """Return form values as a project-candidate dict."""
        directory = self._dir_edit.text().strip()
        name = self._name_edit.text().strip() or Path(directory).name
        return {
            "name": name,
            "directory": directory,
            "location": self._location_edit.text().strip(),
            "collector": self._collector_edit.text().strip(),
        }


# ── Main overview view ─────────────────────────────────────────────────────────

class OverviewView(BaseView):
    """项目总览 — project list with enter-workspace / detail actions.

    Mirrors app.js:13856-13943 (renderOverview project-list branch):
      overview-header-actions:  h2 + [+ 新建项目] + [+ 打开工作区]
      photo-toolbar:            时间筛选 + 全部 / 2026 / 2025
      specimen-table-wrap:      specimen-table columns
        项目名称 / 磁盘目录 / 时间 / 地点 / 负责人 / 操作
        操作 = [进入工作区]  [详情]

    Signals
    -------
    enter_workspace_requested(str):
        Emitted with the project directory path when the user clicks
        「进入工作区」.  MainWindow connects this to navigate_to("workbench").
    """

    view_id = "overview"
    nav_title = "项目总览"
    nav_icon = "📊"

    # Emitted when user clicks 「进入工作区」; carries the project directory.
    enter_workspace_requested = pyqtSignal(str)

    def __init__(self, ctx: "AppContext") -> None:  # noqa: F821
        self._projects: list[dict] = []
        self._year_filter: Optional[str] = None  # None = "全部"
        super().__init__(ctx)

    # ── BaseView contract ──────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(0)

        # ── overview-header / overview-header-actions ──────────────────────────
        header_row = QHBoxLayout()
        header_row.setSpacing(10)
        header_row.setContentsMargins(0, 0, 0, 16)

        self._title_lbl = QLabel("项目总览")
        self._title_lbl.setObjectName("Title")
        # serif font + 28px — mirrors .overview-header h2
        self._title_lbl.setStyleSheet(
            'font-family: "Noto Serif SC","Source Han Serif SC",SimSun,Georgia,serif;'
            "font-size: 28px; font-weight: 500; letter-spacing: -0.03em;"
        )
        header_row.addWidget(self._title_lbl)
        header_row.addStretch()

        self._btn_new = QPushButton("+ 新建项目")
        self._btn_new.setObjectName("Primary")
        self._btn_new.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_new.clicked.connect(self._on_new_project)
        header_row.addWidget(self._btn_new)

        self._btn_open = QPushButton("+ 打开工作区")
        self._btn_open.setObjectName("Outline")
        self._btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_open.clicked.connect(self._on_open_workspace)
        header_row.addWidget(self._btn_open)

        root.addLayout(header_row)

        # ── photo-toolbar (time filter) ────────────────────────────────────────
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        filter_row.setContentsMargins(0, 0, 0, 18)

        filter_lbl = QLabel("时间筛选")
        filter_lbl.setObjectName("Muted")
        filter_row.addWidget(filter_lbl)

        self._btn_all = QPushButton("全部")
        self._btn_all.setObjectName("Outline")
        self._btn_all.setCheckable(True)
        self._btn_all.setChecked(True)
        self._btn_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_all.clicked.connect(lambda: self._set_year_filter(None))
        filter_row.addWidget(self._btn_all)

        self._btn_2026 = QPushButton("2026")
        self._btn_2026.setObjectName("Outline")
        self._btn_2026.setCheckable(True)
        self._btn_2026.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_2026.clicked.connect(lambda: self._set_year_filter("2026"))
        filter_row.addWidget(self._btn_2026)

        self._btn_2025 = QPushButton("2025")
        self._btn_2025.setObjectName("Outline")
        self._btn_2025.setCheckable(True)
        self._btn_2025.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_2025.clicked.connect(lambda: self._set_year_filter("2025"))
        filter_row.addWidget(self._btn_2025)

        filter_row.addStretch()
        root.addLayout(filter_row)

        # ── specimen-table-wrap ────────────────────────────────────────────────
        wrap = QFrame()
        wrap.setObjectName("Panel")
        wrap.setFrameShape(QFrame.Shape.StyledPanel)
        wrap_layout = QVBoxLayout(wrap)
        wrap_layout.setContentsMargins(0, 0, 0, 0)
        wrap_layout.setSpacing(0)

        # specimen-table columns:
        # 项目名称 / 磁盘目录 / 时间 / 地点 / 负责人 / 操作
        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(
            ["项目名称", "磁盘目录", "时间", "地点", "负责人", "操作"]
        )
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        hdr = self._table.horizontalHeader()
        from PyQt6.QtWidgets import QHeaderView
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)       # 项目名称 stretches
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)       # 磁盘目录 stretches
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(5, 178)       # 操作 column — two buttons
        self._table.setSortingEnabled(False)
        self._table.setShowGrid(True)
        # Row height — mirrors .specimen-table td padding: 11px 14px + font 13px
        self._table.verticalHeader().setDefaultSectionSize(46)

        wrap_layout.addWidget(self._table)
        root.addWidget(wrap, stretch=1)

        # ── Status bar label ───────────────────────────────────────────────────
        self._status_lbl = QLabel("")
        self._status_lbl.setObjectName("Muted")
        self._status_lbl.setContentsMargins(0, 8, 0, 0)
        root.addWidget(self._status_lbl)

    def on_activate(self) -> None:
        """Reload project list from disk each time the user navigates here."""
        self._load_projects()

    # ── Data loading ───────────────────────────────────────────────────────────

    def _load_projects(self) -> None:
        self._projects = _load_projects()
        self._rebuild_table()

    def _filtered_projects(self) -> list[dict]:
        """Apply year filter to the project list."""
        if self._year_filter is None:
            return self._projects
        year = self._year_filter
        result = []
        for p in self._projects:
            # Check year field, name, dateRange, or directory for the year string.
            haystack = " ".join([
                str(p.get("year", "")),
                str(p.get("dateRange", "")),
                str(p.get("date_range", "")),
                str(p.get("name", "")),
                str(p.get("directory", "")),
            ])
            if year in haystack:
                result.append(p)
        return result

    # ── Table rebuild ──────────────────────────────────────────────────────────

    def _rebuild_table(self) -> None:
        """Populate the QTableWidget from the filtered project list."""
        projects = self._filtered_projects()
        self._table.setRowCount(0)

        for proj in projects:
            row = self._table.rowCount()
            self._table.insertRow(row)

            # 项目名称  — bold, mirrors tdName style fontWeight 600
            name_text = proj.get("name", "—")
            year = str(proj.get("year", ""))
            if year and year not in name_text:
                name_text = f"{name_text}  {year}"
            name_item = QTableWidgetItem(name_text)
            name_item.setFont(self._bold_font())
            self._table.setItem(row, 0, name_item)

            # 磁盘目录  — mono, mirrors tdDir class "mono project-dir-cell"
            directory = proj.get("directory") or proj.get("dir") or "—"
            dir_item = QTableWidgetItem(directory)
            dir_item.setToolTip(directory)
            dir_item.setFont(self._mono_font())
            self._table.setItem(row, 1, dir_item)

            # 时间  — mono, mirrors tdDate class "mono"
            date_range = (
                proj.get("dateRange")
                or proj.get("date_range")
                or proj.get("year")
                or "—"
            )
            date_item = QTableWidgetItem(str(date_range))
            date_item.setFont(self._mono_font())
            self._table.setItem(row, 2, date_item)

            # 地点
            loc_item = QTableWidgetItem(proj.get("location") or "—")
            self._table.setItem(row, 3, loc_item)

            # 负责人
            person_item = QTableWidgetItem(proj.get("collector") or "—")
            self._table.setItem(row, 4, person_item)

            # 操作 — [进入工作区] [详情]  — mirrors tdAct / enterWs / detailBtn
            action_widget = self._make_action_cell(proj)
            self._table.setCellWidget(row, 5, action_widget)

        count = len(projects)
        total = len(self._projects)
        if self._year_filter:
            self._status_lbl.setText(
                f"共 {total} 个项目，筛选显示 {count} 个（{self._year_filter} 年）"
            )
        else:
            self._status_lbl.setText(f"共 {total} 个项目")

    def _make_action_cell(self, proj: dict) -> QWidget:
        """Build the 操作 cell with 「进入工作区」 and 「详情」 buttons."""
        cell = QWidget()
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(6)

        enter_btn = QPushButton("进入工作区")
        enter_btn.setObjectName("Primary")
        enter_btn.setFixedHeight(28)
        enter_btn.setStyleSheet(
            "QPushButton { font-size: 12px; padding: 0 10px; }"
        )
        enter_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        enter_btn.clicked.connect(lambda _=False, p=proj: self._on_enter_workspace(p))

        detail_btn = QPushButton("详情")
        detail_btn.setObjectName("Outline")
        detail_btn.setFixedHeight(28)
        detail_btn.setStyleSheet(
            "QPushButton { font-size: 12px; padding: 0 10px; }"
        )
        detail_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        detail_btn.clicked.connect(lambda _=False, p=proj: self._on_detail(p))

        lay.addWidget(enter_btn)
        lay.addWidget(detail_btn)
        lay.addStretch()
        return cell

    # ── Year-filter toggle ─────────────────────────────────────────────────────

    def _set_year_filter(self, year: Optional[str]) -> None:
        self._year_filter = year
        # Update checked states
        self._btn_all.setChecked(year is None)
        self._btn_2026.setChecked(year == "2026")
        self._btn_2025.setChecked(year == "2025")
        self._rebuild_table()

    # ── Action handlers ────────────────────────────────────────────────────────

    def _on_enter_workspace(self, proj: dict) -> None:
        """Set the active project and navigate to the workbench view.

        Mirrors app.js enterWorkspaceForProject(): update ctx.current_project_dir
        then emit a signal so MainWindow can navigate_to("workbench").
        """
        directory = proj.get("directory") or proj.get("dir") or ""
        if not directory:
            QMessageBox.information(
                self, "进入工作区", "该项目没有关联磁盘目录，请先打开或新建一个有目录的项目。"
            )
            return
        # Set active project in context
        self.ctx.current_project_dir = directory
        # Emit signal — MainWindow wires this to navigate_to("workbench")
        self.enter_workspace_requested.emit(directory)
        # Refresh the context bar through MainWindow if possible
        main_win = self.window()
        if hasattr(main_win, "refresh_context_bar"):
            main_win.refresh_context_bar()
        if hasattr(main_win, "navigate_to"):
            main_win.navigate_to("workbench")

    def _on_detail(self, proj: dict) -> None:
        """Show a detail dialog for the project."""
        dlg = _ProjectDetailDialog(proj, parent=self)
        dlg.exec()

    def _on_new_project(self) -> None:
        """Open 「新建项目」 modal and persist the new project."""
        dlg = _NewProjectDialog(parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        candidate = dlg.result_dict()
        directory = candidate.get("directory", "")
        if not directory:
            return
        try:
            from app.services.project_service import create_project
            proj = create_project(candidate["name"], directory)
            proj["location"] = candidate.get("location", "")
            proj["collector"] = candidate.get("collector", "")
            # Persist
            all_projects = _load_projects()
            # Avoid duplicates by directory
            existing_dirs = {p.get("directory") or p.get("dir") for p in all_projects}
            if proj.get("directory") not in existing_dirs:
                all_projects.append(proj)
                _save_projects(all_projects)
            self._load_projects()
        except Exception as exc:
            QMessageBox.critical(self, "新建项目失败", str(exc))

    def _on_open_workspace(self) -> None:
        """Let the user pick an existing directory and register it as a project."""
        directory = QFileDialog.getExistingDirectory(self, "打开工作区目录")
        if not directory:
            return
        try:
            from app.services.project_service import open_project
            proj = open_project(directory)
            proj["name"] = Path(directory).name
            proj["location"] = ""
            proj["collector"] = ""
            all_projects = _load_projects()
            existing_dirs = {p.get("directory") or p.get("dir") for p in all_projects}
            if proj.get("directory") not in existing_dirs:
                all_projects.append(proj)
                _save_projects(all_projects)
            self._load_projects()
        except Exception as exc:
            QMessageBox.critical(self, "打开工作区失败", str(exc))

    # ── Font helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _bold_font():
        from PyQt6.QtGui import QFont
        f = QFont()
        f.setWeight(QFont.Weight.DemiBold)
        return f

    @staticmethod
    def _mono_font():
        from PyQt6.QtGui import QFont
        f = QFont(
            "JetBrains Mono, Fira Code, Consolas, Courier New, monospace"
        )
        f.setPointSize(10)
        return f
