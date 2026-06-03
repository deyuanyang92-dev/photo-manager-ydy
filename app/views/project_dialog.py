"""project_dialog.py — 「新建拍摄项目」and 「打开工作区」dialog.

Mirrors the web oracle:
  - renderProjectModal()     → app.js:10597-10734
  - renderOpenWorkspaceModal() → app.js:10736+
  - commitProject()          → inside renderProjectModal form submit

Fields (新建模式, 7 fields):
  1. 项目名称*     (required)
  2. 项目编号       (optional, default suggestProjectCode)
  3. 目录*          (required, dir-picker)
  4. 采集地点*      (required)
  5. 负责人*        (required)
  6. 开始日期*      (required, default today YYYYMMDD)
  7. 结束日期        (optional)

打开工作区模式: only 目录 (name = dir.name).

On accept:
  - Calls project_service.create_project + ensure_project_dirs
  - Merges extra fields (projectCode/location/collector/year/dateRange/
    incomingJpgSubdir/resultsSubdir) matching web commitProject() shape
  - Returns result via .result_project() → dict (not yet saved)
  - Caller is responsible for persisting to user_projects.json and
    navigating to workbench.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.utils.ui import center_on, get_existing_directory, warn


# ── Helpers ───────────────────────────────────────────────────────────────────

def _today_compact() -> str:
    """Return today as YYYYMMDD string."""
    return date.today().strftime("%Y%m%d")


def suggest_project_code(existing_projects: list[dict], year: str = "") -> str:
    """Mirror web suggestProjectCode().

    Returns "PRJ-<year>-01", "PRJ-<year>-02", … counting non-demo
    projects with the same year.

    Parameters
    ----------
    existing_projects:
        Loaded project list from user_projects.json.
    year:
        Four-digit year string.  Defaults to current year.
    """
    y = year or str(date.today().year)
    count = sum(
        1 for p in existing_projects
        if str(p.get("year", "")) == y and not p.get("isDemo", False)
    ) + 1
    return f"PRJ-{y}-{count:02d}"


def _strip_non_digits(s: str) -> str:
    return re.sub(r"\D", "", s)


# ── Dialog ────────────────────────────────────────────────────────────────────

class ProjectDialog(QDialog):
    """Modal dialog for 「新建拍摄项目」or 「打开工作区」.

    Parameters
    ----------
    mode:
        ``"new"``   — full 7-field new-project form.
        ``"open"``  — simplified dir-only form.
    existing_projects:
        Current list of projects (used to suggest project code).
    parent:
        Parent widget for centering.
    """

    def __init__(
        self,
        mode: str = "new",
        existing_projects: Optional[list[dict]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._mode = mode
        self._existing = existing_projects or []
        self._project: Optional[dict] = None  # populated on accept

        if mode == "new":
            self.setWindowTitle("新建拍摄项目")
        else:
            self.setWindowTitle("打开工作区")

        self.setMinimumWidth(520)
        self._build_ui()
        center_on(self, parent)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 20, 28, 16)
        root.setSpacing(14)

        # Title + intro
        title_lbl = QLabel(
            "新建拍摄项目" if self._mode == "new" else "打开工作区"
        )
        title_lbl.setObjectName("Title")
        title_lbl.setStyleSheet("font-size: 18px; font-weight: 600;")
        root.addWidget(title_lbl)

        if self._mode == "new":
            intro = (
                "项目目录由相机软件、Helicon 和本照片工作区共同使用；"
                "新照片会出现在监控区，确认最终成片后才创建标本唯一编号和成果编号。"
            )
        else:
            intro = (
                "只需指定一个磁盘目录即可打开工作区；"
                "根目录下将自动创建 incoming-jpg/（相机）与 results/（Helicon TIFF 与 ZIP）。"
                "名称留空则用目录名。"
            )
        intro_lbl = QLabel(intro)
        intro_lbl.setWordWrap(True)
        intro_lbl.setObjectName("Muted")
        intro_lbl.setStyleSheet("font-size: 13px;")
        root.addWidget(intro_lbl)

        # Form
        form = QFormLayout()
        form.setSpacing(10)
        form.setContentsMargins(0, 4, 0, 4)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        if self._mode == "open":
            # Open mode: optional name + required dir
            self._name_edit = QLineEdit()
            self._name_edit.setPlaceholderText("留空则用目录名")
            form.addRow("工作区名称（可选）：", self._name_edit)
        else:
            # New mode: project name (required)
            self._name_edit = QLineEdit()
            self._name_edit.setPlaceholderText("例如：厦门潮间带多毛类调查")
            form.addRow("项目名称 *：", self._name_edit)

            # Project code (optional, auto-suggested)
            today_year = str(date.today().year)
            suggested_code = suggest_project_code(self._existing, today_year)
            self._code_edit = QLineEdit()
            self._code_edit.setPlaceholderText(suggested_code)
            form.addRow("项目编号：", self._code_edit)

        # Directory picker (required)
        self._dir_edit = QLineEdit()
        self._dir_edit.setPlaceholderText("选择或输入磁盘目录")
        dir_row = QHBoxLayout()
        dir_row.setSpacing(6)
        dir_row.addWidget(self._dir_edit, stretch=1)
        browse_btn = QPushButton("浏览…")
        browse_btn.setObjectName("Outline")
        browse_btn.clicked.connect(self._browse_dir)
        browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dir_row.addWidget(browse_btn)
        dir_widget = QWidget()
        dir_widget.setLayout(dir_row)
        form.addRow("目录 *：", dir_widget)

        if self._mode == "new":
            # Location (required)
            self._location_edit = QLineEdit()
            self._location_edit.setPlaceholderText("例如：福建 · 厦门 · 一国两制")
            form.addRow("采集地点 *：", self._location_edit)

            # Collector (required)
            self._collector_edit = QLineEdit()
            self._collector_edit.setPlaceholderText("例如：杨德援")
            form.addRow("负责人 *：", self._collector_edit)

            # Start date (required, default today)
            self._start_date_edit = QLineEdit()
            self._start_date_edit.setPlaceholderText("如 20260101")
            self._start_date_edit.setText(_today_compact())
            form.addRow("开始日期 *：", self._start_date_edit)

            # End date (optional)
            self._end_date_edit = QLineEdit()
            self._end_date_edit.setPlaceholderText("如 20260115（可选）")
            form.addRow("结束日期：", self._end_date_edit)

        root.addLayout(form)

        # Buttons
        if self._mode == "new":
            accept_label = "创建并进入照片工作区"
        else:
            accept_label = "打开工作区"

        btn_box = QDialogButtonBox()
        cancel_btn = btn_box.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        cancel_btn.setObjectName("Outline")
        accept_btn = btn_box.addButton(accept_label, QDialogButtonBox.ButtonRole.AcceptRole)
        accept_btn.setObjectName("Primary")
        accept_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_box.rejected.connect(self.reject)
        btn_box.accepted.connect(self._on_accept)
        root.addWidget(btn_box)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _browse_dir(self) -> None:
        path = get_existing_directory(self, "选择项目目录")
        if path:
            self._dir_edit.setText(path)
            # Auto-fill name from dir name if still empty
            if not self._name_edit.text():
                self._name_edit.setText(Path(path).name)

    def _on_accept(self) -> None:
        directory = self._dir_edit.text().strip()
        if not directory:
            warn(self, "新建项目" if self._mode == "new" else "打开工作区",
                 "请选择磁盘目录。")
            return

        if self._mode == "new":
            name = self._name_edit.text().strip()
            if not name:
                warn(self, "新建项目", "请填写项目名称。")
                return
            location = self._location_edit.text().strip()
            if not location:
                warn(self, "新建项目", "请填写采集地点。")
                return
            collector = self._collector_edit.text().strip()
            if not collector:
                warn(self, "新建项目", "请填写负责人。")
                return
            start_raw = self._start_date_edit.text().strip()
            if not start_raw:
                warn(self, "新建项目", "请填写开始日期。")
                return
        else:
            name = self._name_edit.text().strip() or Path(directory).name

        # Build project dict
        try:
            from app.services.project_service import (
                create_project,
                INCOMING_JPG_DIR,
                RESULTS_DIR,
            )
            proj = create_project(name, directory)
        except Exception as exc:
            warn(self, "创建失败", str(exc))
            return

        if self._mode == "new":
            start_digits = _strip_non_digits(start_raw)[:8] or _today_compact()
            end_raw = self._end_date_edit.text().strip()
            end_digits = _strip_non_digits(end_raw)[:8] if end_raw else ""
            year = start_digits[:4]

            # Mirror web commitProject() shape
            today_year = str(date.today().year)
            code_raw = self._code_edit.text().strip()
            project_code = (
                code_raw.upper()
                if code_raw
                else suggest_project_code(self._existing, year or today_year).upper()
            )
            proj.update({
                "projectCode": project_code,
                "location": location,
                "collector": collector,
                "year": year,
                "dateRange": f"{start_digits} ~ {end_digits}" if end_digits else start_digits,
                "incomingJpgSubdir": INCOMING_JPG_DIR,
                "resultsSubdir": RESULTS_DIR,
            })
        else:
            # Open-workspace mode — minimal fields
            from app.services.project_service import INCOMING_JPG_DIR, RESULTS_DIR
            proj.update({
                "name": name,
                "location": "",
                "collector": "",
                "year": str(date.today().year),
                "dateRange": _today_compact(),
                "incomingJpgSubdir": INCOMING_JPG_DIR,
                "resultsSubdir": RESULTS_DIR,
            })

        self._project = proj
        self.accept()

    # ── Result ────────────────────────────────────────────────────────────────

    def result_project(self) -> Optional[dict]:
        """Return the constructed project dict, or None if not accepted."""
        return self._project
