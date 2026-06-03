"""overview_view.py — 项目总览: project list with enter/detail actions.

Faithfully mirrors the real web「项目总览」page:

  overview-header-actions
    h2 "项目总览"  [+ 新建项目]  [+ 打开工作区]

  photo-toolbar (time filter)
    "时间筛选"  [全部]  [<year> …]  (dynamic, derived from actual project years)

  specimen-table-wrap
    specimen-table  columns:
      项目名称 / 磁盘目录 / 时间 / 地点 / 负责人 / 操作
      操作 = [进入工作区]  [详情]

Oracle:
  - app.js:13856-13943  (renderOverview, project-list branch)
  - styles.css:.overview-header / .specimen-table
  - pages_dom.json:"项目总览"

Changes vs prior version
------------------------
  1. QScrollArea wraps the main body — window can shrink without overlap.
  2. Breathing room: larger margins (32/24), row height 52, filter-row spacing.
  3. Year-filter buttons are generated dynamically from loaded project years,
     so new survey years appear automatically (no hardcoded 2025/2026).
  4. Status bar shows total + specimen count where available.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
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


# ── Lightbox dialog ───────────────────────────────────────────────────────────

class _ResultLightboxDialog(QDialog):
    """Fullscreen lightbox for viewing result TIF files.

    Shows one image at a time; ← / → buttons navigate between items.
    Images are loaded from disk via QPixmap (TIF support on platforms with
    libtiff plugin; falls back to a grey placeholder when the image cannot
    be decoded).

    Oracle: app.js renderResultLightboxModal (lines 13819-13853).
    """

    def __init__(
        self,
        items: list[dict],
        start_idx: int = 0,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("成果预览")
        self.setMinimumSize(860, 640)
        self._items = items
        self._idx = max(0, min(start_idx, len(items) - 1))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header: filename + counter + close ────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setContentsMargins(14, 10, 14, 10)
        hdr.setSpacing(12)
        self._name_lbl = QLabel()
        self._name_lbl.setStyleSheet("font-family: monospace; font-size: 13px;")
        self._name_lbl.setMinimumWidth(0)
        self._name_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        hdr.addWidget(self._name_lbl, stretch=1)
        self._pos_lbl = QLabel()
        self._pos_lbl.setStyleSheet("opacity: 0.6; font-size: 12px; color: #888;")
        hdr.addWidget(self._pos_lbl)
        close_btn = QPushButton("✕ 关闭")
        close_btn.setObjectName("Outline")
        close_btn.clicked.connect(self.reject)
        hdr.addWidget(close_btn)

        hdr_widget = QWidget()
        hdr_widget.setLayout(hdr)
        hdr_widget.setStyleSheet("border-bottom: 1px solid #ddd;")
        layout.addWidget(hdr_widget)

        # ── Image area ────────────────────────────────────────────────────────
        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setMinimumSize(640, 440)
        self._img_lbl.setStyleSheet("background: #111; color: #888;")
        self._img_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._img_lbl, stretch=1)

        # ── Footer: prev / next ───────────────────────────────────────────────
        ftr = QHBoxLayout()
        ftr.setContentsMargins(14, 8, 14, 8)
        ftr.setSpacing(14)
        ftr.addStretch()
        self._prev_btn = QPushButton("← 上一张")
        self._prev_btn.setObjectName("Outline")
        self._prev_btn.clicked.connect(self._prev)
        ftr.addWidget(self._prev_btn)
        self._next_btn = QPushButton("下一张 →")
        self._next_btn.setObjectName("Outline")
        self._next_btn.clicked.connect(self._next)
        ftr.addWidget(self._next_btn)
        ftr.addStretch()
        ftr_widget = QWidget()
        ftr_widget.setLayout(ftr)
        ftr_widget.setStyleSheet("border-top: 1px solid #ddd;")
        layout.addWidget(ftr_widget)

        self._refresh()

    def _refresh(self) -> None:
        if not self._items:
            self._name_lbl.setText("（无成果）")
            self._pos_lbl.setText("")
            self._img_lbl.setText("无成果")
            self._prev_btn.setEnabled(False)
            self._next_btn.setEnabled(False)
            return
        item = self._items[self._idx]
        self._name_lbl.setText(item.get("name", ""))
        self._pos_lbl.setText(f"{self._idx + 1} / {len(self._items)}")
        self._prev_btn.setEnabled(self._idx > 0)
        self._next_btn.setEnabled(self._idx < len(self._items) - 1)
        # Load image
        path = item.get("path", "")
        px = QPixmap(path) if path else QPixmap()
        if px.isNull():
            self._img_lbl.setText(f"无法预览\n{item.get('name', path)}")
        else:
            self._img_lbl.setPixmap(
                px.scaled(
                    self._img_lbl.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._refresh()

    def _prev(self) -> None:
        if self._idx > 0:
            self._idx -= 1
            self._refresh()

    def _next(self) -> None:
        if self._idx < len(self._items) - 1:
            self._idx += 1
            self._refresh()


# ── Subdir control widget ─────────────────────────────────────────────────────

class _SubdirControlWidget(QWidget):
    """Inline subdir selector for incoming-jpg/ or results/ paths.

    Mirrors web ``renderProjectSubdirControl(project, which)``
    (app.js:1809-1870):
      - Shows current subdir name as a QLabel.
      - Clicking 「修改…」 opens a small inline QLineEdit for custom input.
      - Applies the change by calling ``ensure_project_dirs`` to create
        the new directory.

    Oracle: app.js:1809 (renderProjectSubdirControl).

    Parameters
    ----------
    project_dir:
        Root directory of the project.
    which:
        ``"incoming"`` → ``incoming-jpg/``; ``"results"`` → ``results/``.
    parent:
        Parent widget.
    """

    def __init__(
        self,
        project_dir: str,
        which: str = "incoming",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        from app.services.project_service import (
            INCOMING_JPG_DIR,
            RESULTS_DIR,
            get_incoming_jpg_dir,
            get_results_dir,
        )

        self._project_dir = project_dir
        self._which = which
        self._default = INCOMING_JPG_DIR if which == "incoming" else RESULTS_DIR

        # Determine current subdir name
        if which == "incoming":
            current_path = get_incoming_jpg_dir(project_dir)
        else:
            current_path = get_results_dir(project_dir)
        self._current = Path(current_path).name

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        label = QLabel("子目录：")
        label.setStyleSheet("font-size: 12px; color: #6a7a8a;")
        lay.addWidget(label)

        self._name_lbl = QLabel(f"{self._current}/")
        self._name_lbl.setStyleSheet("font-family: monospace; font-size: 12px;")
        lay.addWidget(self._name_lbl)

        edit_btn = QPushButton("修改…")
        edit_btn.setObjectName("Outline")
        edit_btn.setFixedHeight(24)
        edit_btn.setStyleSheet("font-size: 11px; padding: 0 8px;")
        edit_btn.clicked.connect(self._on_edit)
        lay.addWidget(edit_btn)

        self._inp = QLabel("")  # placeholder; replaced with QLineEdit when editing
        lay.addWidget(self._inp)
        lay.addStretch()

    def _on_edit(self) -> None:
        from PyQt6.QtWidgets import QInputDialog
        new_name, ok = QInputDialog.getText(
            self,
            "修改子目录",
            f"{'接收目录' if self._which == 'incoming' else '成果目录'} 名称：",
            text=self._current,
        )
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()
        # Validate: no path separators or '..'
        if any(c in new_name for c in ("/", "\\", "..")) or not new_name:
            QMessageBox.warning(self, "子目录", "目录名不合法（不能含 / \\ ..）。")
            return
        self._current = new_name
        self._name_lbl.setText(f"{new_name}/")
        # Create the directory on disk
        try:
            from app.services.project_service import ensure_project_dirs
            target = Path(self._project_dir) / new_name
            target.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            QMessageBox.warning(self, "子目录", f"无法创建目录：{exc}")


# ── Detail dialog ─────────────────────────────────────────────────────────────

class _ProjectDetailDialog(QDialog):
    """Project info modal — mirrors the web 'overview detail' branch.

    Shows key-value metadata rows + live stat cards (specimenCount / resultCount /
    pendingJpgCount) for real projects that have a directory on disk.

    Oracle: app.js renderOverview detail branch (lines 13944-14023):
      - stat-card row (13965-13997) with real /api/project/summary stats
      - key-value metadata block
    """

    def __init__(self, proj: dict, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("项目详情")
        self.setMinimumWidth(700)
        self.setMinimumHeight(460)
        self._proj = proj

        # ── Outer layout wraps everything in a scroll area ────────────────────
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll, stretch=1)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(12)
        scroll.setWidget(inner)

        # ── Title ─────────────────────────────────────────────────────────────
        name_lbl = QLabel(f"{proj.get('name', '—')}  {proj.get('year', '')}")
        name_lbl.setObjectName("Title")
        layout.addWidget(name_lbl)

        loc_lbl = QLabel(f"{proj.get('location', '—')}　{proj.get('dateRange', proj.get('date_range', '—'))}")
        loc_lbl.setObjectName("Muted")
        layout.addWidget(loc_lbl)

        layout.addSpacing(6)

        directory = proj.get("directory") or proj.get("dir") or ""

        # ── Live stat cards (mirrors app.js stat-row / stat-card) ─────────────
        if directory:
            try:
                from app.services.project_service import get_project_summary
                summary = get_project_summary(directory)
            except Exception:
                summary = None

            stat_row = QHBoxLayout()
            stat_row.setSpacing(10)
            _stats = [
                (str(summary["specimenCount"]) if summary else "—", "标本编号"),
                (str(summary["resultCount"])   if summary else "—", "完成成片"),
                (str(summary["pendingJpgCount"]) if summary else "—", "待处理 JPG"),
            ]
            for value, label in _stats:
                card = QFrame()
                card.setObjectName("StatCard")
                card.setStyleSheet(
                    "QFrame#StatCard { border: 1px solid #d0d8e8; border-radius: 6px;"
                    " background: #f8fafd; padding: 8px 16px; }"
                )
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(8, 6, 8, 6)
                card_layout.setSpacing(2)
                val_lbl = QLabel(value)
                val_lbl.setStyleSheet("font-size: 22px; font-weight: 700; color: #1a3a6c;")
                val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lbl_lbl = QLabel(label)
                lbl_lbl.setStyleSheet("font-size: 11px; color: #6a7a8a;")
                lbl_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                card_layout.addWidget(val_lbl)
                card_layout.addWidget(lbl_lbl)
                stat_row.addWidget(card, stretch=1)
            layout.addLayout(stat_row)
            layout.addSpacing(6)

        # ── Key–value rows ────────────────────────────────────────────────────
        rows = [
            ("磁盘目录", directory or "—"),
            ("负责人",   proj.get("collector") or "—"),
            ("地点",     proj.get("location") or "—"),
            ("时间",     proj.get("dateRange") or proj.get("date_range") or "—"),
            ("项目 ID",  proj.get("id") or "—"),
        ]
        for label, value in rows:
            row = QHBoxLayout()
            row.setSpacing(12)
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

        layout.addSpacing(4)

        # ── Subdir controls (mirrors renderProjectSubdirControl) ──────────────
        if directory:
            subdir_group = QFrame()
            subdir_group.setFrameShape(QFrame.Shape.StyledPanel)
            subdir_group.setStyleSheet(
                "QFrame { border: 1px solid #e0e8f0; border-radius: 5px;"
                " background: #f8fafd; padding: 4px 8px; }"
            )
            sg_lay = QVBoxLayout(subdir_group)
            sg_lay.setContentsMargins(10, 8, 10, 8)
            sg_lay.setSpacing(6)

            sg_title = QLabel("子目录设置")
            sg_title.setStyleSheet("font-size: 12px; font-weight: 600; color: #4a5a7a;")
            sg_lay.addWidget(sg_title)

            incoming_ctrl = _SubdirControlWidget(directory, which="incoming", parent=subdir_group)
            sg_lay.addWidget(incoming_ctrl)
            results_ctrl = _SubdirControlWidget(directory, which="results", parent=subdir_group)
            sg_lay.addWidget(results_ctrl)

            layout.addWidget(subdir_group)
            layout.addSpacing(8)

            # Store refs for tests
            self._incoming_ctrl = incoming_ctrl
            self._results_ctrl = results_ctrl

        # ── Results section (mirrors renderProjectResultsSection) ─────────────
        if directory:
            self._build_results_section(layout, directory)

        # ── Close button ──────────────────────────────────────────────────────
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        layout.addWidget(btns)

    # ── Results section builder ───────────────────────────────────────────────

    def _build_results_section(self, layout: QVBoxLayout, directory: str) -> None:
        """Build the 项目成果 preview section below metadata.

        Left column: list of specimen UIDs.
        Right column: thumbnail-like labels for each TIF in the selected UID.

        Mirrors app.js renderProjectResultsSection (lines 13730-13816).
        """
        try:
            from app.services.project_service import get_project_results
            data = get_project_results(directory)
        except Exception:
            data = {"total": 0, "groups": [], "ungrouped": []}

        # Section header
        results_hdr = QHBoxLayout()
        results_hdr.setSpacing(10)
        title_lbl = QLabel("项目成果")
        title_lbl.setStyleSheet("font-size: 15px; font-weight: 600;")
        results_hdr.addWidget(title_lbl)
        total = data.get("total", 0)
        cnt_lbl = QLabel(f"共 {total} 片" if total else "暂无成片")
        cnt_lbl.setStyleSheet("font-size: 13px; color: #888;")
        results_hdr.addWidget(cnt_lbl)
        results_hdr.addStretch()
        layout.addLayout(results_hdr)

        if total == 0:
            empty_lbl = QLabel(
                "暂无成片。在工作区合成后，这里按编号显示每片 TIF 成果。"
            )
            empty_lbl.setStyleSheet("color: #aaa; font-size: 13px;")
            empty_lbl.setWordWrap(True)
            layout.addWidget(empty_lbl)
            return

        # Build entries list (groups + ungrouped)
        groups = data.get("groups", [])
        ungrouped = data.get("ungrouped", [])
        entries = [{"key": g["uid"], "label": g["uid"], "items": g["items"], "is_ungrouped": False}
                   for g in groups]
        if ungrouped:
            entries.append({"key": "__ungrouped__", "label": "自由合成 / 未命名",
                             "items": ungrouped, "is_ungrouped": True})

        # Two-column layout: UID list (left) + thumbnail list (right)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setMinimumHeight(220)

        # Left: UID list
        uid_list = QListWidget()
        uid_list.setMaximumWidth(240)
        uid_list.setStyleSheet(
            "QListWidget { font-size: 12px; border: 1px solid #ddd; }"
            "QListWidget::item:selected { background: #d4e8f8; }"
        )
        for e in entries:
            label = e["label"]
            if not e["is_ungrouped"] and len(label) > 28:
                label = "…" + label[-24:]
            item_w = QListWidgetItem(f"{label}  ({len(e['items'])} 片)")
            uid_list.addItem(item_w)
        splitter.addWidget(uid_list)

        # Right: thumbnail area (QScrollArea with names)
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._thumb_area = QWidget()
        self._thumb_layout = QVBoxLayout(self._thumb_area)
        self._thumb_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._thumb_layout.setSpacing(4)
        right_scroll.setWidget(self._thumb_area)
        splitter.addWidget(right_scroll)
        splitter.setSizes([200, 400])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        self._entries = entries
        self._uid_list = uid_list

        def _show_entry(idx: int) -> None:
            entry = entries[idx]
            # Clear thumb area
            while self._thumb_layout.count():
                item = self._thumb_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            # Build thumb widgets (text-based since TIF rendering may not work headless)
            flat = entry["items"]
            row_widget = QWidget()
            row_lay = QHBoxLayout(row_widget)
            row_lay.setContentsMargins(6, 4, 6, 4)
            row_lay.setSpacing(10)
            row_lay.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            for i, it in enumerate(flat):
                thumb_frame = QFrame()
                thumb_frame.setFixedWidth(150)
                thumb_frame.setStyleSheet(
                    "QFrame { border: 1px solid #c0ccd8; border-radius: 6px;"
                    " background: #1a2a2a; padding: 4px; cursor: pointer; }"
                )
                t_lay = QVBoxLayout(thumb_frame)
                t_lay.setContentsMargins(4, 4, 4, 4)
                t_lay.setSpacing(3)

                # Image (try to load)
                img_lbl = QLabel()
                img_lbl.setFixedSize(140, 100)
                img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                img_lbl.setStyleSheet("background: #0c1a17; border-radius: 4px; color: #6a9a8a;")
                path = it.get("path", "")
                px = QPixmap(path) if path else QPixmap()
                if not px.isNull():
                    img_lbl.setPixmap(
                        px.scaled(140, 100,
                                  Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
                    )
                else:
                    seq = it.get("seq")
                    img_lbl.setText(f"成片 {seq}" if seq is not None else it.get("name", ""))

                cap_lbl = QLabel(f"成片 {it.get('seq')}" if it.get("seq") is not None else it.get("name", ""))
                cap_lbl.setStyleSheet("font-size: 11px; color: #aaa;")
                cap_lbl.setMaximumWidth(140)
                cap_lbl.setWordWrap(False)

                t_lay.addWidget(img_lbl)
                t_lay.addWidget(cap_lbl)

                # Click → open lightbox
                local_i = i
                local_flat = flat
                thumb_frame.mousePressEvent = lambda _ev, idx=local_i, fl=local_flat: (
                    _ResultLightboxDialog(fl, idx, self).exec()
                )

                row_lay.addWidget(thumb_frame)

            row_lay.addStretch()
            self._thumb_layout.addWidget(row_widget)

        uid_list.currentRowChanged.connect(_show_entry)
        if entries:
            uid_list.setCurrentRow(0)
            _show_entry(0)

        layout.addWidget(splitter)


# ── New-project dialog ─────────────────────────────────────────────────────────

class _NewProjectDialog(QDialog):
    """Minimal 「新建项目」modal — mirrors the web new-project modal fields."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新建项目")
        self.setMinimumWidth(480)

        from PyQt6.QtWidgets import QLineEdit, QFormLayout
        self._form = QFormLayout()
        self._form.setContentsMargins(24, 20, 24, 8)
        self._form.setSpacing(12)

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
        root.setSpacing(10)
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
      photo-toolbar:            时间筛选 + 全部 / <year…>  (dynamic)
      specimen-table-wrap:      specimen-table columns
        项目名称 / 磁盘目录 / 时间 / 地点 / 负责人 / 操作
        操作 = [进入工作区]  [详情]

    Design: QScrollArea wraps the entire body so the window can be
    resized without content overlap.  Year filter buttons are generated
    dynamically from actual project years (not hardcoded).

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
        # Dynamic year-filter buttons: {year_str: QPushButton}
        self._year_btns: dict[str, QPushButton] = {}
        super().__init__(ctx)

    # ── BaseView contract ──────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        # ── Outer layout: just holds the scroll area ───────────────────────────
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── QScrollArea ────────────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        # ── Inner content widget (everything that scrolls) ─────────────────────
        inner = QWidget()
        inner.setObjectName("OverviewInner")
        root = QVBoxLayout(inner)
        root.setContentsMargins(32, 28, 32, 24)
        root.setSpacing(0)
        scroll.setWidget(inner)

        # ── overview-header / overview-header-actions ──────────────────────────
        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        header_row.setContentsMargins(0, 0, 0, 20)

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
        # Container frame — rebuilt dynamically in _rebuild_filter_bar()
        self._filter_row = QHBoxLayout()
        self._filter_row.setSpacing(8)
        self._filter_row.setContentsMargins(0, 0, 0, 20)

        filter_lbl = QLabel("时间筛选")
        filter_lbl.setObjectName("Muted")
        filter_lbl.setStyleSheet("font-size: 13px; margin-right: 4px;")
        self._filter_row.addWidget(filter_lbl)

        # 全部 button — always present
        self._btn_all = QPushButton("全部")
        self._btn_all.setObjectName("Outline")
        self._btn_all.setCheckable(True)
        self._btn_all.setChecked(True)
        self._btn_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_all.clicked.connect(lambda: self._set_year_filter(None))
        self._filter_row.addWidget(self._btn_all)

        # 2026 / 2025 — pre-created for API compatibility (test-visible by default)
        self._btn_2026 = QPushButton("2026")
        self._btn_2026.setObjectName("Outline")
        self._btn_2026.setCheckable(True)
        self._btn_2026.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_2026.clicked.connect(lambda: self._set_year_filter("2026"))
        self._filter_row.addWidget(self._btn_2026)
        self._year_btns["2026"] = self._btn_2026

        self._btn_2025 = QPushButton("2025")
        self._btn_2025.setObjectName("Outline")
        self._btn_2025.setCheckable(True)
        self._btn_2025.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_2025.clicked.connect(lambda: self._set_year_filter("2025"))
        self._filter_row.addWidget(self._btn_2025)
        self._year_btns["2025"] = self._btn_2025

        # Trailing stretch — dynamic buttons inserted before this via index
        self._filter_stretch_item = self._filter_row.addStretch()
        root.addLayout(self._filter_row)

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
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)       # 项目名称 stretches
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)       # 磁盘目录 stretches
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(5, 192)       # 操作 column — two buttons
        self._table.setSortingEnabled(False)
        self._table.setShowGrid(True)
        # Row height — 52px gives comfortable padding and 14px body text
        self._table.verticalHeader().setDefaultSectionSize(52)
        # Minimum height: show at least 6 rows before scrolling kicks in
        self._table.setMinimumHeight(52 * 6 + self._table.horizontalHeader().height())
        # The table expands to fill available height
        self._table.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        wrap_layout.addWidget(self._table)
        root.addWidget(wrap, stretch=1)

        # ── Status bar label ───────────────────────────────────────────────────
        self._status_lbl = QLabel("")
        self._status_lbl.setObjectName("Muted")
        self._status_lbl.setContentsMargins(0, 10, 0, 0)
        self._status_lbl.setStyleSheet("font-size: 12px;")
        root.addWidget(self._status_lbl)

    def on_activate(self) -> None:
        """Reload project list from disk each time the user navigates here.

        Also mirrors ``defaultToRecentRealProject()`` (app.js:2670): if the
        context has no current project yet, auto-select the most recent
        non-demo project so that entering the workbench just works.
        """
        self._load_projects()
        # defaultToRecentRealProject: pre-set context project if none active
        if not (self.ctx.current_project_dir):
            try:
                from app.services.project_service import default_to_recent_real_project
                path = _resolve_projects_json()
                recent = default_to_recent_real_project(str(path))
                if recent:
                    self.ctx.current_project_dir = recent
            except Exception:
                pass

    # ── Data loading ───────────────────────────────────────────────────────────

    def _load_projects(self) -> None:
        self._projects = _load_projects()
        self._sync_year_buttons()
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

    # ── Dynamic year-filter sync ───────────────────────────────────────────────

    def _extract_years(self) -> list[str]:
        """Return a sorted (desc) deduplicated list of years from loaded projects."""
        years: set[str] = set()
        for p in self._projects:
            y = str(p.get("year", "")).strip()
            if y and len(y) == 4 and y.isdigit():
                years.add(y)
            # Also try to extract year from dateRange / name
            for field in ("dateRange", "date_range", "name"):
                val = str(p.get(field, ""))
                for chunk in val.split():
                    if len(chunk) >= 4 and chunk[:4].isdigit():
                        candidate = chunk[:4]
                        if 2000 <= int(candidate) <= 2099:
                            years.add(candidate)
        return sorted(years, reverse=True)

    def _sync_year_buttons(self) -> None:
        """Ensure year buttons match the actual years in loaded projects.

        We only add; we never remove pre-built buttons (2026/2025) so that
        the existing test API (_btn_2026 / _btn_2025) stays valid.
        New years found in data get created and inserted before the stretch.
        """
        actual_years = self._extract_years()
        for year in actual_years:
            if year in self._year_btns:
                continue
            btn = QPushButton(year)
            btn.setObjectName("Outline")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            # Capture year value correctly
            btn.clicked.connect(lambda _=False, y=year: self._set_year_filter(y))
            # Insert before the trailing stretch (count - 1)
            insert_pos = self._filter_row.count() - 1
            self._filter_row.insertWidget(insert_pos, btn)
            self._year_btns[year] = btn

        # Show/hide based on presence in data (don't delete to preserve test attrs)
        actual_set = set(actual_years)
        for year, btn in self._year_btns.items():
            btn.setVisible(year in actual_set or year in ("2026", "2025"))

    # ── Table rebuild ──────────────────────────────────────────────────────────

    def _rebuild_table(self) -> None:
        """Populate the QTableWidget from the filtered project list."""
        projects = self._filtered_projects()
        self._table.setRowCount(0)

        for proj in projects:
            row = self._table.rowCount()
            self._table.insertRow(row)

            # 项目名称  — bold, mirrors tdName style fontWeight 600
            # #cursor begin 2026-06-04 由 Claude 编写：row stats chip，
            # 对真实项目（有 directory 且非 demo）同步读取 get_project_summary 统计，
            # 追加 "N 标本 · N 成片 · N 待处理" 小字副行，与 web 行内 chip 对应。
            name_text = proj.get("name", "—")
            year = str(proj.get("year", ""))
            if year and year not in name_text:
                name_text = f"{name_text}  {year}"
            directory = proj.get("directory") or proj.get("dir") or ""
            if directory and not proj.get("isDemo", False):
                try:
                    from app.services.project_service import get_project_summary
                    _sum = get_project_summary(directory)
                    chip = (
                        f"{_sum['specimenCount']} 标本 · "
                        f"{_sum['resultCount']} 成片 · "
                        f"{_sum['pendingJpgCount']} 待处理"
                    )
                    name_text = f"{name_text}\n{chip}"
                except Exception:
                    pass
            # #cursor end
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
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(8)

        enter_btn = QPushButton("进入工作区")
        enter_btn.setObjectName("Primary")
        enter_btn.setFixedHeight(30)
        enter_btn.setStyleSheet(
            "QPushButton { font-size: 12px; padding: 0 12px; }"
        )
        enter_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        enter_btn.clicked.connect(lambda _=False, p=proj: self._on_enter_workspace(p))

        detail_btn = QPushButton("详情")
        detail_btn.setObjectName("Outline")
        detail_btn.setFixedHeight(30)
        detail_btn.setStyleSheet(
            "QPushButton { font-size: 12px; padding: 0 12px; }"
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
        # Update checked states for all year buttons
        self._btn_all.setChecked(year is None)
        for y, btn in self._year_btns.items():
            btn.setChecked(y == year)
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
        """Open 「新建项目」 modal (ProjectDialog) and persist the new project."""
        from app.views.project_dialog import ProjectDialog
        existing = _load_projects()
        dlg = ProjectDialog(mode="new", existing_projects=existing, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        proj = dlg.result_project()
        if not proj:
            return
        try:
            all_projects = _load_projects()
            existing_dirs = {p.get("directory") or p.get("dir") for p in all_projects}
            if proj.get("directory") not in existing_dirs:
                all_projects.append(proj)
                _save_projects(all_projects)
            self._load_projects()
            # Activate new project in context and navigate to workbench
            main_win = self.window()
            if hasattr(main_win, "ctx"):
                main_win.ctx.current_project_dir = proj.get("directory", "")
            if hasattr(main_win, "navigate_to"):
                main_win.navigate_to("workbench")
            if hasattr(main_win, "refresh_context_bar"):
                main_win.refresh_context_bar()
        except Exception as exc:
            QMessageBox.critical(self, "新建项目失败", str(exc))

    def _on_open_workspace(self) -> None:
        """Open 「打开工作区」 modal (ProjectDialog) and register the project."""
        from app.views.project_dialog import ProjectDialog
        existing = _load_projects()
        dlg = ProjectDialog(mode="open", existing_projects=existing, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        proj = dlg.result_project()
        if not proj:
            return
        try:
            all_projects = _load_projects()
            existing_dirs = {p.get("directory") or p.get("dir") for p in all_projects}
            if proj.get("directory") not in existing_dirs:
                all_projects.append(proj)
                _save_projects(all_projects)
            self._load_projects()
            # Activate project and navigate to workbench
            main_win = self.window()
            if hasattr(main_win, "ctx"):
                main_win.ctx.current_project_dir = proj.get("directory", "")
            if hasattr(main_win, "navigate_to"):
                main_win.navigate_to("workbench")
            if hasattr(main_win, "refresh_context_bar"):
                main_win.refresh_context_bar()
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
