"""new_survey_project_dialog.py — 新建项目(调查区域 + 若干采样点), 一次填完。

用户 2026-07-12: "我开展一个大项目, 比如江苏盐城2026, 在这个区域设置了 2 个点:
日出海湾、月亮湾 —— 软件应该自动把项目目录和内部两个子目录建好, 我能切换进去。"

    ┌ 新建项目 ──────────────────────────────────┐
    │ 项目名称   [江苏盐城2026        ]  必填      │
    │ 建在哪里   [/mnt/n/调查数据  ] [浏览…]      │
    │ 地区/位置  [江苏盐城]     年份 [2026]       │
    │ 采集人     [张三]   地区代码 [JSYC](可选)    │
    │                                             │
    │ 采样点(一行一个, 每个点就是一个可进入的工作区) │
    │ ┌─────────────────────────────┐             │
    │ │ 日出海湾                     │             │
    │ │ 月亮湾                       │             │
    │ └─────────────────────────────┘             │
    │ 将创建:                                      │
    │   江苏盐城2026/                              │
    │     ├ 日出海湾/  (可直接进入拍照)             │
    │     └ 月亮湾/    (可直接进入拍照)             │
    │                        [取消] [创建]         │
    └─────────────────────────────────────────────┘
项目根只放共享设置(地区/人员, 采样点自动继承), **不放照片**。

(Fable 5, 2026-07-12)
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.utils import ui


class NewSurveyProjectDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None, default_parent_dir: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("新建项目")
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)
        self._name = QLineEdit()
        self._name.setPlaceholderText("如：江苏盐城2026")
        form.addRow("项目名称 *", self._name)

        dir_row = QHBoxLayout()
        self._dir = QLineEdit(default_parent_dir)
        self._dir.setPlaceholderText("项目文件夹建在哪个目录下")
        browse = QPushButton("浏览…")
        browse.setObjectName("Outline")
        browse.clicked.connect(self._pick_dir)
        dir_row.addWidget(self._dir, 1)
        dir_row.addWidget(browse)
        dir_wrap = QWidget()
        dir_wrap.setLayout(dir_row)
        form.addRow("建在哪里 *", dir_wrap)

        self._location = QLineEdit()
        self._location.setPlaceholderText("如：江苏盐城")
        form.addRow("地区/位置", self._location)
        self._year = QLineEdit()
        self._year.setPlaceholderText("如：2026")
        form.addRow("年份", self._year)
        self._collector = QLineEdit()
        self._collector.setPlaceholderText("整个项目共用，采样点自动继承")
        form.addRow("采集人", self._collector)
        self._province = QLineEdit()
        self._province.setPlaceholderText("编号里的地区段，如 JSYC（可留空）")
        form.addRow("地区代码", self._province)
        root.addLayout(form)

        tip = QLabel("采样点（一行一个）——每个点就是一个可以直接进入拍照的工作区")
        tip.setObjectName("MutedSmall")
        root.addWidget(tip)
        self._sites = QPlainTextEdit()
        self._sites.setPlaceholderText("日出海湾\n月亮湾")
        self._sites.setFixedHeight(96)
        root.addWidget(self._sites)

        self._preview = QLabel("")
        self._preview.setObjectName("MutedSmall")
        self._preview.setWordWrap(True)
        root.addWidget(self._preview)

        self._err = QLabel("")
        self._err.setObjectName("UnattributedWarning")
        self._err.hide()
        root.addWidget(self._err)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        ok = btns.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("创建")
        ok.setObjectName("Primary")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self._try_accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        self._name.textChanged.connect(self._refresh_preview)
        self._sites.textChanged.connect(self._refresh_preview)
        self._refresh_preview()

    # ── helpers ──────────────────────────────────────────────────────────────
    def _pick_dir(self) -> None:
        chosen = ui.get_existing_directory(self, "选择项目建在哪个目录下")
        if chosen:
            self._dir.setText(chosen)
            self._refresh_preview()

    def site_names(self) -> list[str]:
        return [
            line.strip()
            for line in self._sites.toPlainText().splitlines()
            if line.strip()
        ]

    def _refresh_preview(self) -> None:
        name = self._name.text().strip() or "（项目名）"
        sites = self.site_names()
        lines = [f"将创建：{name}/"]
        for s in sites[:6]:
            lines.append(f"    ├ {s}/    （可直接进入拍照）")
        if len(sites) > 6:
            lines.append(f"    └ … 另外 {len(sites) - 6} 个点")
        if not sites:
            lines.append("    （还没填采样点：可以先建空项目，之后再加点）")
        self._preview.setText("\n".join(lines))

    def _try_accept(self) -> None:
        name = self._name.text().strip()
        parent = self._dir.text().strip()
        problems = []
        if not name:
            problems.append("请填项目名称")
        if not parent or not Path(parent).is_dir():
            problems.append("请选择一个已存在的上级目录")
        bad = [s for s in self.site_names() if any(c in s for c in ("/", "\\", ".."))]
        if bad:
            problems.append("采样点名称不能包含 / \\ ..：" + "、".join(bad))
        sites = self.site_names()
        if len(set(sites)) != len(sites):
            problems.append("采样点名称有重复")
        if name and parent and (Path(parent) / name).exists() and any(
            (Path(parent) / name).iterdir()
        ):
            problems.append(f"该目录下已存在同名且非空的项目：{name}")
        if problems:
            self._err.setText("⚠ " + "；".join(problems))
            self._err.show()
            return
        self.accept()

    def values(self) -> dict:
        return {
            "parent_dir": self._dir.text().strip(),
            "name": self._name.text().strip(),
            "sites": self.site_names(),
            "meta": {
                "location": self._location.text().strip(),
                "year": self._year.text().strip(),
            },
            "collector": self._collector.text().strip(),
            "province": self._province.text().strip(),
        }
