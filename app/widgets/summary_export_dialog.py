"""summary_export_dialog.py — 跨工作区汇总导出对话框（新增，仅在已有 UI 上叠加）.

把一组工作区（各含 ``_data/project.db``）合并导出：标本汇总 Excel / 采集站位汇总
Excel / 质控报告（HTML+Excel）。两种来源：

  - 模式 A「指向文件夹」：选一个调查根目录 → ``discover_workspaces`` 扫出其下所有
    工作区，root=该目录，dirs=扫到的工作区路径。
  - 模式 B「从最近项目勾选」：从 ``user_projects.json`` 列出最近项目，勾选若干 →
    root=它们的公共父目录（commonpath，失败则取第一个的父目录）。

纯叠加：复用 project_tree_view / coord_import_dialog 的暗色 QSS 习语，不改任何既有
控件。所有文件/消息对话框走 app.utils.ui。后端逻辑全部委托给
project_summary_service，本对话框只负责收集 dirs/root 并展示结果。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from app.services import project_service
from app.services import project_summary_service as pss
from app.services.project_tree_service import discover_workspaces
from app.utils import ui

_PATH_ROLE = Qt.ItemDataRole.UserRole


def _theme():
    try:
        from app.config.theme import TOKENS
        return TOKENS.get
    except Exception:  # pragma: no cover
        return lambda k, d=None: d


class SummaryExportDialog(QDialog):
    """跨工作区汇总导出。模式 A 指向文件夹 / 模式 B 勾选最近项目。"""

    def __init__(
        self,
        ctx=None,
        initial_root: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._discovered: list[dict] = []
        self.setWindowTitle("汇总导出")
        self.resize(720, 560)
        self._build_ui()
        self._apply_style()

        if initial_root:
            self._mode_a.setChecked(True)
            self._on_mode_changed()
            self._set_mode_a_root(str(Path(initial_root).resolve()))
        else:
            self._mode_b.setChecked(True)
            self._on_mode_changed()

        ui.center_on(self, parent)

    # ── UI ──────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)

        title = QLabel("跨工作区汇总导出")
        title.setObjectName("PaneTitle")
        v.addWidget(title)

        hint = QLabel(
            "合并多个工作区（各自的 _data/project.db）导出汇总。"
            "选择来源后勾选需要的输出，导出到 root 的 _data/exports/ 目录。"
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        v.addWidget(hint)

        # ── 来源模式 ──────────────────────────────────────────────────────
        self._mode_group = QButtonGroup(self)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(16)
        self._mode_a = QRadioButton("指向文件夹")
        self._mode_b = QRadioButton("从最近项目勾选")
        self._mode_group.addButton(self._mode_a)
        self._mode_group.addButton(self._mode_b)
        self._mode_a.toggled.connect(self._on_mode_changed)
        mode_row.addWidget(self._mode_a)
        mode_row.addWidget(self._mode_b)
        mode_row.addStretch()
        v.addLayout(mode_row)

        # ── 模式 A：目录选择 ────────────────────────────────────────────────
        self._panel_a = QWidget()
        al = QVBoxLayout(self._panel_a)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(6)
        dir_row = QHBoxLayout()
        self._dir_field = QLineEdit()
        self._dir_field.setReadOnly(True)
        self._dir_field.setPlaceholderText("尚未选择调查根目录")
        btn_browse = QPushButton("浏览…")
        btn_browse.clicked.connect(self._pick_dir)
        dir_row.addWidget(self._dir_field, 1)
        dir_row.addWidget(btn_browse)
        al.addLayout(dir_row)
        self._a_list = QListWidget()
        self._a_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        al.addWidget(self._a_list, 1)
        v.addWidget(self._panel_a, 1)

        # ── 模式 B：最近项目勾选 ────────────────────────────────────────────
        self._panel_b = QWidget()
        bl = QVBoxLayout(self._panel_b)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(6)
        bl.addWidget(QLabel("勾选要汇总的项目（root=它们的公共父目录）："))
        self._b_list = QListWidget()
        bl.addWidget(self._b_list, 1)
        v.addWidget(self._panel_b, 1)

        # ── 输出选择 ──────────────────────────────────────────────────────
        out_row = QHBoxLayout()
        out_row.setSpacing(16)
        self._cb_specimen = QCheckBox("标本汇总 Excel")
        self._cb_collection = QCheckBox("采集站位汇总 Excel")
        self._cb_qc = QCheckBox("质控报告 (HTML+Excel)")
        for cb in (self._cb_specimen, self._cb_collection, self._cb_qc):
            cb.setChecked(True)
            out_row.addWidget(cb)
        out_row.addStretch()
        v.addLayout(out_row)

        # ── 动作 ──────────────────────────────────────────────────────────
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton("关闭")
        cancel.clicked.connect(self.reject)
        self._btn_export = QPushButton("导出到 _data/exports/")
        self._btn_export.setObjectName("Primary")
        self._btn_export.setDefault(True)
        self._btn_export.clicked.connect(self._on_export)
        actions.addWidget(cancel)
        actions.addWidget(self._btn_export)
        v.addLayout(actions)

    def _apply_style(self) -> None:
        g = _theme()
        bg, panel, border = g("bg", "#0a1e24"), g("panel_2", "#0e2329"), g("border", "#21424a")
        text, muted, accent = g("text", "#c8dcd6"), g("muted", "#7fa49b"), g("accent", "#4fd1b8")
        accent_fg = g("accent_fg", "#ffffff")
        self.setStyleSheet(
            f"QDialog{{background:{bg};}}"
            f"QLabel{{color:{text};background:transparent;}}"
            f"QLabel#PaneTitle{{color:{text};font-weight:600;font-size:15px;}}"
            f"QLabel#Muted{{color:{muted};font-size:12px;}}"
            f"QRadioButton,QCheckBox{{color:{text};font-size:13px;}}"
            f"QLineEdit{{background:{panel};color:{text};border:1px solid {border};"
            f"border-radius:5px;padding:5px 8px;font-size:13px;}}"
            f"QPushButton{{background:{panel};color:{text};border:1px solid {border};"
            f"border-radius:5px;padding:5px 12px;font-size:13px;}}"
            f"QPushButton:hover{{background:{border};}}"
            f"QPushButton#Primary{{background:{accent};color:{accent_fg};border:1px solid {accent};}}"
            f"QPushButton:disabled{{color:{muted};}}"
            f"QListWidget{{background:{bg};color:{text};border:1px solid {border};"
            f"border-radius:6px;font-size:13px;}}"
            f"QListWidget::item{{padding:4px 2px;}}"
            f"QListWidget::item:selected{{background:{accent};color:{accent_fg};}}"
        )

    # ── 模式切换 ──────────────────────────────────────────────────────────
    def _on_mode_changed(self, *_) -> None:
        is_a = self._mode_a.isChecked()
        self._panel_a.setVisible(is_a)
        self._panel_b.setVisible(not is_a)
        if not is_a and self._b_list.count() == 0:
            self._populate_recent()

    # ── 模式 A ────────────────────────────────────────────────────────────
    def _pick_dir(self) -> None:
        start = self._dir_field.text() or (
            getattr(self.ctx, "current_project_root", "") if self.ctx else ""
        ) or ""
        path = ui.get_existing_directory(self, "选择调查根目录", start)
        if path:
            self._set_mode_a_root(str(Path(path).resolve()))

    def _set_mode_a_root(self, root: str) -> None:
        self._dir_field.setText(root)
        self._a_list.clear()
        try:
            self._discovered = discover_workspaces(root)
        except Exception:
            self._discovered = []
        if not self._discovered:
            item = QListWidgetItem("（该目录下未发现工作区）")
            self._a_list.addItem(item)
            return
        for w in self._discovered:
            item = QListWidgetItem(f"📷 {w['name']}  ·  {w['rel']}")
            item.setData(_PATH_ROLE, w["path"])
            self._a_list.addItem(item)

    # ── 模式 B ────────────────────────────────────────────────────────────
    def _populate_recent(self) -> None:
        self._b_list.clear()
        try:
            projects = project_service.list_projects(
                project_service.default_user_projects_json_path()
            )
        except Exception:
            projects = []
        for p in projects:
            directory = p.get("directory") or p.get("dir") or ""
            if not directory:
                continue
            name = p.get("name") or os.path.basename(directory)
            item = QListWidgetItem(name)
            item.setData(_PATH_ROLE, directory)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self._b_list.addItem(item)

    def _checked_dirs(self) -> list[str]:
        out: list[str] = []
        for i in range(self._b_list.count()):
            item = self._b_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                d = item.data(_PATH_ROLE)
                if d:
                    out.append(d)
        return out

    # ── 收集 dirs/root ────────────────────────────────────────────────────
    def _resolve_dirs_root(self) -> tuple[list[str], str]:
        """Return (dirs, root) for the active mode. Empty dirs → caller warns."""
        if self._mode_a.isChecked():
            root = self._dir_field.text().strip()
            dirs = [w["path"] for w in self._discovered]
            return dirs, root
        # mode B
        dirs = self._checked_dirs()
        if not dirs:
            return [], ""
        root = self._common_root(dirs)
        return dirs, root

    @staticmethod
    def _common_root(dirs: list[str]) -> str:
        resolved = [str(Path(d).resolve()) for d in dirs]
        if len(resolved) == 1:
            return str(Path(resolved[0]).parent)
        try:
            return os.path.commonpath(resolved)
        except ValueError:
            # mixed drives etc. — fall back to the first dir's parent
            return str(Path(resolved[0]).parent)

    # ── 导出 ──────────────────────────────────────────────────────────────
    def _on_export(self) -> None:
        dirs, root = self._resolve_dirs_root()
        if not dirs:
            ui.warn(self, "汇总导出", "请先选择来源：指向一个含工作区的文件夹，"
                                  "或勾选至少一个最近项目。")
            return
        if not root:
            root = str(Path(dirs[0]).resolve())
        if not (self._cb_specimen.isChecked() or self._cb_collection.isChecked()
                or self._cb_qc.isChecked()):
            ui.warn(self, "汇总导出", "请至少勾选一种输出。")
            return

        written: list[str] = []
        errors: list[str] = []

        if self._cb_specimen.isChecked():
            try:
                p = pss.export_specimen_summary(dirs, root)
                written.append(str(p))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"标本汇总：{exc}")
        if self._cb_collection.isChecked():
            try:
                p = pss.export_collection_summary(dirs, root)
                written.append(str(p))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"采集站位汇总：{exc}")
        if self._cb_qc.isChecked():
            try:
                html_p, xlsx_p = pss.export_qc_report(dirs, root)
                written.append(str(html_p))
                written.append(str(xlsx_p))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"质控报告：{exc}")

        lines = []
        if written:
            lines.append("已导出：")
            lines.extend(f"  • {w}" for w in written)
        if errors:
            lines.append("")
            lines.append("失败：")
            lines.extend(f"  • {e}" for e in errors)

        if written and not errors:
            ui.info(self, "汇总导出完成", "\n".join(lines))
            self.accept()
        elif written:
            ui.warn(self, "汇总导出（部分成功）", "\n".join(lines))
        else:
            ui.warn(self, "汇总导出失败", "\n".join(lines) or "没有产生任何文件。")
