"""PROTOTYPE — three interactive PyQt6 variants for capture-directory navigation.

Run: python3 docs/design/workspace_navigator_prototype.py
No filesystem writes or application settings are performed.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel,
    QMainWindow, QMenu, QMessageBox, QPushButton, QSizePolicy, QToolButton,
    QVBoxLayout, QWidget,
)


ROOT = "/mnt/n/claude"
DIRS = [
    "ceshi5", "ceshi6", "ceshi7", "photo-platform-ydy-v3",
]
HISTORY = [
    ("广西采集 / 20260610(北港岛)", r"D:\202606广西雷州-海南-广西采集\20260610(北港岛)"),
    ("海南采集 / 20260611(金沙湾)", r"D:\202606广西雷州-海南-广西采集\海南金沙湾-20260611"),
    ("雷州采集 / 20260609(港尾村)", r"D:\202606广西雷州-海南-广西采集\20260609(港尾村)"),
]
VARIANTS = [
    ("A", "参考图优先 · 双层目录导航"),
    ("B", "路径优先 · 单行面包屑"),
    ("C", "复杂项目 · 展开式目录面板"),
]


class Prototype(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.root_path = ROOT
        self.dirs = list(DIRS)
        self.current = self.dirs.index("ceshi7")
        self.variant = 0
        self.setWindowTitle("PROTOTYPE — 采集目录导航器")
        self.resize(1380, 820)
        self._build()
        self.render()

    @property
    def path(self) -> str:
        return str(Path(self.root_path) / self.dirs[self.current])

    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        top = QFrame(objectName="Topbar")
        top_lay = QHBoxLayout(top)
        top_lay.setContentsMargins(20, 8, 20, 8)
        brand = QLabel("↻  标本影像管理", objectName="Brand")
        top_lay.addWidget(brand)
        self.host = QWidget()
        self.host.setMinimumWidth(580)
        self.host.setMaximumWidth(700)
        self.host_lay = QVBoxLayout(self.host)
        self.host_lay.setContentsMargins(0, 0, 0, 0)
        top_lay.addWidget(self.host)
        section = QLabel("↻  照片工作区", objectName="Section")
        section.setAlignment(Qt.AlignmentFlag.AlignCenter)
        section.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        top_lay.addWidget(section, 1)
        top_lay.addWidget(QPushButton("⚙"))
        top_lay.addWidget(QPushButton("Helicon"))
        root.addWidget(top)

        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(28, 18, 28, 18)
        title = QHBoxLayout()
        title.addWidget(QLabel("拍照工作台", objectName="Title"))
        self.badge = QLabel(objectName="Badge")
        title.addWidget(self.badge)
        title.addWidget(QLabel("Helicon OK", objectName="Badge"))
        title.addStretch()
        body_lay.addLayout(title)
        self.path_label = QLabel(objectName="Path")
        body_lay.addWidget(self.path_label)

        columns = QHBoxLayout()
        for heading, text, width in (
            ("标本编号", "＋ 新增标本唯一编号\n\nGX-BG-B2-DLC005-T95E-20260610", 300),
            ("拍摄队列", "等待目录中新照片", 650),
            ("照片编号", "项目元数据", 300),
        ):
            panel = QFrame(objectName="Panel")
            panel.setMinimumWidth(width if width < 400 else 450)
            p = QVBoxLayout(panel)
            p.addWidget(QLabel(heading, objectName="PanelTitle"))
            content = QLabel(text, objectName="Empty")
            content.setAlignment(Qt.AlignmentFlag.AlignCenter)
            p.addWidget(content, 1)
            columns.addWidget(panel, 2 if width > 400 else 1)
        body_lay.addLayout(columns, 1)

        switch = QHBoxLayout()
        switch.addStretch()
        prev = QPushButton("←")
        prev.clicked.connect(lambda: self.switch_variant(-1))
        self.variant_label = QLabel(objectName="Variant")
        nxt = QPushButton("→")
        nxt.clicked.connect(lambda: self.switch_variant(1))
        switch.addWidget(prev)
        switch.addWidget(self.variant_label)
        switch.addWidget(nxt)
        switch.addStretch()
        body_lay.addLayout(switch)
        root.addWidget(body, 1)

        self.setStyleSheet("""
            QMainWindow, QWidget { background:#f5f8f8; color:#16292d; font:14px 'Microsoft YaHei'; }
            #Topbar { background:white; border-bottom:1px solid #d8e3e4; }
            #Brand { font-size:15px; font-weight:700; padding-right:14px; }
            #Section { color:#008f83; background:#e4f5f2; border-radius:8px; padding:10px; font-weight:700; }
            QPushButton, QToolButton { min-height:32px; padding:0 10px; background:white; border:1px solid #cfddde; border-radius:7px; }
            QPushButton:hover, QToolButton:hover { background:#e7f6f3; border-color:#62aaa3; }
            #Title { font-size:22px; font-weight:800; }
            #Badge { color:#08786f; background:#e0efed; padding:6px 12px; font-weight:700; }
            #Path { margin-top:10px; padding:10px; color:#60797d; background:white; border:1px solid #d8e3e4; border-radius:7px; }
            #Panel { margin-top:14px; padding:12px; background:white; border:1px solid #d8e3e4; border-radius:10px; }
            #PanelTitle { font-size:17px; font-weight:700; }
            #Empty { color:#6f878b; border:1px dashed #d3e1e2; border-radius:8px; }
            #Variant { min-width:280px; padding:8px 18px; color:white; background:#162c30; border-radius:16px; font-weight:700; qproperty-alignment:AlignCenter; }
            #RootLine { color:#6f8589; font-size:12px; }
            #Current { color:#007f75; font-weight:800; text-align:left; }
            #Crumb { color:#60797d; }
            #TreePanel { background:white; border:1px solid #bed2d2; border-radius:8px; }
            #TreeHead { font-weight:800; }
            #TreeCurrent { color:#007f75; background:#e4f5f2; padding:5px; font-weight:700; }
        """)

    def clear_host(self) -> None:
        while self.host_lay.count():
            item = self.host_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def render(self) -> None:
        self.clear_host()
        [self.render_a, self.render_b, self.render_c][self.variant]()
        key, name = VARIANTS[self.variant]
        self.variant_label.setText(f"{key} — {name}")
        self.path_label.setText(f"工作目录　{self.path}　　相机 JPG　incoming-jpg/　　成果　results/")
        self.badge.setText(self.dirs[self.current])

    def arrow(self, text: str, delta: int) -> QToolButton:
        btn = QToolButton()
        btn.setText(text)
        btn.clicked.connect(lambda: self.step(delta))
        return btn

    def project_menu(self, button: QToolButton | QPushButton) -> None:
        menu = QMenu(button)
        menu.addSection(f"当前磁盘发现的潜在项目 · {self.root_path}")
        for i, name in enumerate(self.dirs):
            action = menu.addAction(("●  " if i == self.current else "📁  ") + name)
            action.setToolTip(str(Path(self.root_path) / name))
            action.triggered.connect(lambda _=False, idx=i: self.select_dir(idx))
        menu.addSeparator()
        menu.addSection("历史创建 / 最近打开的项目")
        for label, path in HISTORY:
            action = menu.addAction("◷  " + label)
            action.setToolTip(path)
            action.triggered.connect(lambda _=False, p=path: self.select_history(p))
        button.setMenu(menu)
        if isinstance(button, QToolButton):
            button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

    def disk_menu(self, button: QToolButton | QPushButton) -> None:
        menu = QMenu(button)
        menu.addSection("进入磁盘")
        menu.addAction("💻  Windows (C:)")
        n = menu.addAction("💽  Data (N:)　当前")
        n.setCheckable(True); n.setChecked(True)
        menu.addAction("💽  Data (D:)")
        menu.addSeparator()
        menu.addAction("📂  从系统目录树选择…", self.open_folder)
        button.setMenu(menu)
        if isinstance(button, QToolButton):
            button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

    def folder_actions_menu(self, button: QToolButton) -> None:
        menu = QMenu(button)
        menu.addAction("📁＋  创建子文件夹", self.create_virtual_subdir)
        menu.addAction("📂  打开文件夹", self.open_folder)
        button.setMenu(menu)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

    def render_a(self) -> None:
        disk = QToolButton(); disk.setText("💻 PC　/　💽 Data (N:)　/　📁 claude"); self.disk_menu(disk)
        disk.setObjectName("RootLine"); self.host_lay.addWidget(disk)
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(self.arrow("◀", -1))
        current = QPushButton(self.dirs[self.current], objectName="Current")
        current.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.project_menu(current)
        row.addWidget(current, 1)
        row.addWidget(self.arrow("▶", 1))
        drop = QToolButton(); drop.setText("▼"); self.project_menu(drop); row.addWidget(drop)
        folder = QToolButton(); folder.setText("📂"); self.folder_actions_menu(folder); row.addWidget(folder)
        self.host_lay.addLayout(row)

    def render_b(self) -> None:
        row = QHBoxLayout(); row.setSpacing(4)
        disk = QToolButton(); disk.setText("💻 PC / N:"); disk.setObjectName("Crumb"); self.disk_menu(disk); row.addWidget(disk)
        row.addWidget(QPushButton("claude", objectName="Crumb"))
        current = QPushButton(self.dirs[self.current], objectName="Current")
        current.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.project_menu(current); row.addWidget(current, 1)
        row.addWidget(self.arrow("◀", -1)); row.addWidget(self.arrow("▶", 1))
        drop = QToolButton(); drop.setText("⌄"); self.project_menu(drop); row.addWidget(drop)
        folder = QToolButton(); folder.setText("📂"); self.folder_actions_menu(folder); row.addWidget(folder)
        self.host_lay.addLayout(row)

    def render_c(self) -> None:
        row = QHBoxLayout(); row.setSpacing(4)
        launcher = QPushButton(f"📁  {self.dirs[self.current]}\n　　N: / claude / 当前项目", objectName="Current")
        launcher.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        launcher.setMinimumHeight(48); self.project_menu(launcher); row.addWidget(launcher, 1)
        tree = QToolButton(); tree.setText("目录树 ▼"); self.tree_menu(tree); row.addWidget(tree)
        self.host_lay.addLayout(row)

    def tree_menu(self, button: QToolButton) -> None:
        menu = QMenu(button)
        menu.addSection("💻 此电脑")
        menu.addAction("　Data (D:)").setEnabled(False)
        menu.addSection("　└─ N: / claude")
        for i, name in enumerate(self.dirs):
            action = menu.addAction(("　　● " if i == self.current else "　　📁 ") + name)
            action.triggered.connect(lambda _=False, idx=i: self.select_dir(idx))
        menu.addSeparator(); menu.addAction("＋ 创建子文件夹…", self.create_virtual_subdir)
        menu.addAction("📂 打开文件夹…", self.open_folder)
        button.setMenu(menu); button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

    def step(self, delta: int) -> None:
        self.current = (self.current + delta) % len(self.dirs)
        self.render()

    def select_dir(self, index: int) -> None:
        self.current = index
        self.render()

    def switch_variant(self, delta: int) -> None:
        self.variant = (self.variant + delta) % len(VARIANTS)
        self.render()

    def select_history(self, path: str) -> None:
        p = Path(path.replace("\\", "/"))
        self.root_path = str(p.parent)
        self.dirs = [p.name]
        self.current = 0
        self.render()

    def create_virtual_subdir(self) -> None:
        name, ok = QInputDialog.getText(
            self, "创建子文件夹（原型）", f"在 {self.path} 下创建：", text="20260621(地点)"
        )
        if ok and name.strip():
            self.root_path = self.path
            self.dirs = [name.strip()]
            self.current = 0
            self.render()
            QMessageBox.information(self, "原型", "已模拟进入新子目录；没有写入磁盘。")

    def open_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "进入磁盘并选择项目目录", self.root_path)
        if chosen:
            p = Path(chosen)
            self.root_path = str(p.parent)
            self.dirs = [p.name]
            self.current = 0
            self.render()


def smoke() -> int:
    app = QApplication.instance() or QApplication([])
    win = Prototype()
    assert win.path.endswith("ceshi7")
    win.step(-1)
    assert win.path.endswith("ceshi6")
    win.switch_variant(1)
    assert win.variant == 1
    print("prototype smoke: OK")
    return 0


if __name__ == "__main__":
    app = QApplication(sys.argv)
    if "--smoke" in sys.argv:
        raise SystemExit(smoke())
    window = Prototype()
    window.show()
    raise SystemExit(app.exec())
