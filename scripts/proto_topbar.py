#!/usr/bin/env python3
"""proto_topbar.py — 顶栏保留原样，点开 = 四层选位面板（用户 2026-07-13 定稿方向）。

跑（WSL）:
    cd /mnt/e/photo-manager-ydy && QT_QPA_PLATFORM=xcb python3 scripts/proto_topbar.py

融合了什么:
  * 顶栏那一排控件**保留**: ◀  📁 xxx ▾  ▶  ▼  📁▾   （用户: "不如保留这种"）
  * ▾ / 📁▾ 点开 = **四层选位面板**: 保存位置 / 项目 / 子目录 / 工作区
      每行: ◀▶ 切同级 · 点名字看本层全部 · ＋ 就地新建 · ⚙ 改这层资料
  * 每层的下拉里融入前面几个方案的好处:
      - **最近优先(真 MRU)** —— 进过就顶到最前(主程序目前没有这个, 是个 bug)
      - **搜索 + 拼音首字母**(打 hc 出「航次2026」) —— 项目多了也能找
      - 每条显示 **上次进入 · 标本数**, 让人一眼选中
      - **死路径标灰** + 给「重新定位…」, 不再是点了才弹「盘未连接」
      - ★ 收藏置顶
  * 底部大按钮「进入工作区 B2」—— 一步开拍
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


# ── 数据 ─────────────────────────────────────────────────────────────────────

@dataclass
class Node:
    name: str
    kind: str                      # disk | project | folder | shoot
    path: str = ""
    specimens: int = 0
    last_open: float = 0.0
    pinned: bool = False
    alive: bool = True
    children: list = field(default_factory=list)
    free: str = ""                 # 磁盘剩余空间


def _ago(ts: float) -> str:
    if not ts:
        return "从未进入"
    d = time.time() - ts
    if d < 3600:
        return f"{int(d//60)} 分钟前"
    if d < 86400:
        return f"{int(d//3600)} 小时前"
    if d < 86400 * 7:
        return f"{int(d//86400)} 天前"
    return time.strftime("%m-%d", time.localtime(ts))


_PY = {"航": "h", "次": "c", "断": "d", "面": "m", "潮": "c", "间": "j", "带": "d",
       "专": "z", "项": "x", "北": "b", "方": "f", "多": "d", "样": "y", "性": "x",
       "调": "d", "查": "c", "江": "j", "苏": "s", "盐": "y", "城": "c", "广": "g",
       "西": "x", "合": "h", "浦": "p", "南": "n", "海": "h", "东": "d", "黄": "h",
       "河": "h", "口": "k", "湿": "s", "地": "d", "渤": "b", "湾": "w", "监": "j",
       "测": "c", "珠": "z", "底": "d", "栖": "q", "普": "p", "实": "s", "验": "y",
       "室": "s", "共": "g", "享": "x", "组": "z", "标": "b", "本": "b", "照": "z",
       "片": "p", "数": "s", "据": "j"}


def _initials(t: str) -> str:
    return "".join(_PY.get(c, c.lower() if c.isascii() else "") for c in t)


def _match(n: Node, q: str) -> bool:
    if not q:
        return True
    q = q.lower().strip()
    return q in n.name.lower() or q in _initials(n.name) or q in n.path.lower()


def mru(items: list[Node]) -> list[Node]:
    return sorted(items, key=lambda n: (-n.pinned, -n.last_open))


def build_data() -> list[Node]:
    now = time.time()
    disks = [
        Node("E:\\调查数据", "disk", "/data", free="剩 1.2 TB"),
        Node("D:\\标本照片2025", "disk", "/data2", free="剩 340 GB"),
        Node("N:\\实验室共享\\南海组", "disk", "/net", free="网络盘"),
    ]
    names = ["航次2026", "北方多样性调查", "潮间带专项", "江苏盐城-2026", "广西合浦调查",
             "南海断面2025", "东海底栖普查", "黄河口湿地", "渤海湾监测", "珠江口专项"]
    for di, disk in enumerate(disks):
        count = 12 if di == 0 else 6
        for pi in range(count):
            pn = names[pi % len(names)] + ("" if pi < len(names) else f"-{pi//len(names)+1}")
            proj = Node(pn, "project", f"{disk.path}/{pn}", last_open=now - random.randint(3600, 86400 * 50))
            # 有的项目直接挂工作区，有的先有子目录 —— 层级不写死
            for fi in range(random.randint(0, 2)):
                folder = Node(f"子区{chr(65+fi)}", "folder", f"{proj.path}/子区{chr(65+fi)}")
                for si in range(random.randint(2, 4)):
                    folder.children.append(Node(
                        f"断面{chr(65+si)}", "shoot", f"{folder.path}/断面{chr(65+si)}",
                        specimens=random.randint(0, 500),
                        last_open=now - random.randint(60, 86400 * 30),
                    ))
                proj.children.append(folder)
            for si in range(random.randint(1, 3)):
                proj.children.append(Node(
                    f"B{si+1}", "shoot", f"{proj.path}/B{si+1}",
                    specimens=random.randint(0, 400),
                    last_open=now - random.randint(60, 86400 * 20),
                    pinned=(di == 0 and pi == 0 and si == 0),
                    alive=not (di == 0 and pi == 2 and si == 0),   # 一个死路径
                ))
            disk.children.append(proj)
    return disks


DISKS = build_data()


def all_shoots(node: Node) -> list[Node]:
    out = []
    for c in node.children:
        if c.kind == "shoot":
            out.append(c)
        else:
            out.extend(all_shoots(c))
    return out


# ── 通用下拉：搜索 + MRU + 统计 + 死路径 ─────────────────────────────────────

class Picker(QDialog):
    chosen = pyqtSignal(object)

    def __init__(self, anchor: QWidget, items: list[Node], title: str, allow_new: bool = True):
        super().__init__(anchor.window())
        self.setWindowFlags(Qt.WindowType.Popup)
        self._items = items
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        self._search = QLineEdit()
        self._search.setPlaceholderText(f"搜{title}…（可打拼音首字母：hc / dm）")
        self._search.textChanged.connect(self._fill)
        lay.addWidget(self._search)

        self._list = QListWidget()
        self._list.setStyleSheet("QListWidget{border:1px solid #e2e8ea;border-radius:6px;}"
                                 "QListWidget::item{padding:7px 8px;}")
        self._list.itemClicked.connect(self._pick)
        lay.addWidget(self._list)

        if allow_new:
            new = QPushButton(f"＋ 新建{title}")
            new.setStyleSheet("QPushButton{color:#0e9384;font-weight:700;border:1px dashed #9fd4cc;"
                              "border-radius:6px;padding:7px;}")
            new.clicked.connect(self._new)
            lay.addWidget(new)

        self._fill("")
        self.resize(430, 380)
        self.move(anchor.mapToGlobal(QPoint(0, anchor.height() + 4)))
        self._search.setFocus()

    def _fill(self, q: str):
        self._list.clear()
        for n in mru(self._items):
            if not _match(n, q):
                continue
            star = "★ " if n.pinned else ""
            if not n.alive:
                text = f"{star}{n.name}        ⚠ 路径不存在 —— 点击可「重新定位…」"
            elif n.kind == "shoot":
                text = f"{star}{n.name}        {n.specimens} 标本 · {_ago(n.last_open)}"
            elif n.kind == "disk":
                text = f"{n.name}        {len(n.children)} 个项目 · {n.free}"
            else:
                text = f"{star}{n.name}        {len(all_shoots(n))} 个工作区 · {_ago(n.last_open)}"
            it = QListWidgetItem(text)
            it.setData(Qt.ItemDataRole.UserRole, n)
            if not n.alive:
                it.setForeground(QColor("#9aa4a7"))
            self._list.addItem(it)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _pick(self, it: QListWidgetItem):
        self.chosen.emit(it.data(Qt.ItemDataRole.UserRole))
        self.accept()

    def _new(self):
        self.chosen.emit(None)      # None = 用户要新建
        self.accept()


# ── 四层选位面板 ─────────────────────────────────────────────────────────────

class LocationPanel(QDialog):
    entered = pyqtSignal(object)

    def __init__(self, anchor: QWidget, state: dict):
        super().__init__(anchor.window())
        self.setWindowFlags(Qt.WindowType.Popup)
        self.setStyleSheet("QDialog{background:#f7f9f9;border:1px solid #cfd9db;border-radius:12px;}")
        self._s = state
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(4)

        t = QLabel("照片保存设置")
        t.setStyleSheet("font-weight:800;font-size:15px;border:none;")
        lay.addWidget(t)

        # 最近去过（一点就走，不用逐层选）
        chips = QHBoxLayout()
        chips.setSpacing(6)
        lbl = QLabel("最近")
        lbl.setStyleSheet("color:#94a3a7;font-size:11px;border:none;")
        chips.addWidget(lbl)
        recents = mru([s for d in DISKS for s in all_shoots(d)])[:4]
        for r in recents:
            c = QPushButton(f"{r.name} · {_ago(r.last_open)}")
            c.setStyleSheet("QPushButton{border:1px solid #dfe6e8;border-radius:14px;padding:3px 11px;"
                            "font-size:12px;background:#fff;}QPushButton:hover{border-color:#0e9384;color:#0e9384;}")
            c.clicked.connect(lambda _c=False, node=r: self._enter(node))
            chips.addWidget(c)
        chips.addStretch(1)
        lay.addLayout(chips)
        lay.addSpacing(6)

        self._rows: dict[str, QPushButton] = {}
        for key in ("保存位置", "项目", "子目录", "工作区"):
            lay.addLayout(self._make_row(key))

        lay.addSpacing(6)
        foot = QHBoxLayout()
        open_btn = QPushButton("打开文件夹…")
        open_btn.setStyleSheet("QPushButton{border:1px solid #dfe6e8;border-radius:8px;padding:9px 14px;background:#fff;}")
        foot.addWidget(open_btn)
        self._go = QPushButton("进入工作区")
        self._go.setStyleSheet("QPushButton{background:#0e9384;color:#fff;font-weight:800;padding:10px 22px;"
                               "border-radius:8px;}QPushButton:hover{background:#0b7a6e;}")
        self._go.clicked.connect(lambda: self._enter(self._s.get("shoot")))
        foot.addWidget(self._go, 1)
        lay.addLayout(foot)

        self._sync()
        self.adjustSize()
        self.setFixedWidth(560)
        self.move(anchor.mapToGlobal(QPoint(-380, anchor.height() + 6)))

    def _make_row(self, key: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        lb = QLabel(key)
        lb.setFixedWidth(58)
        lb.setStyleSheet("color:#66777b;font-size:12.5px;border:none;")
        row.addWidget(lb)

        prev = QToolButton(); prev.setText("◀"); prev.setFixedSize(30, 32)
        prev.clicked.connect(lambda _c=False, k=key: self._step(k, -1))
        row.addWidget(prev)

        sel = QPushButton("—")
        sel.setStyleSheet("QPushButton{text-align:left;padding:7px 12px;border:1.5px solid #cfd9db;"
                          "border-radius:7px;background:#fff;}QPushButton:hover{border-color:#0e9384;}")
        sel.clicked.connect(lambda _c=False, k=key: self._pick(k))
        row.addWidget(sel, 1)
        self._rows[key] = sel

        nxt = QToolButton(); nxt.setText("▶"); nxt.setFixedSize(30, 32)
        nxt.clicked.connect(lambda _c=False, k=key: self._step(k, +1))
        row.addWidget(nxt)

        if key != "保存位置":
            plus = QToolButton(); plus.setText("＋"); plus.setFixedSize(30, 32)
            plus.setStyleSheet("QToolButton{background:#0e9384;color:#fff;border-radius:6px;font-weight:800;}")
            plus.clicked.connect(lambda _c=False, k=key: self._new(k))
            row.addWidget(plus)
            gear = QToolButton(); gear.setText("⚙"); gear.setFixedSize(30, 32)
            gear.setStyleSheet("QToolButton{border:1px solid #9fd4cc;border-radius:6px;color:#0e9384;}")
            gear.setToolTip(f"改「{key}」这一层的资料（时间/目的/负责人/采集人…），下级默认继承")
            row.addWidget(gear)
        else:
            folder = QToolButton(); folder.setText("📂"); folder.setFixedSize(30, 32)
            row.addWidget(folder)
        return row

    # 当前层的候选列表
    def _options(self, key: str) -> list[Node]:
        if key == "保存位置":
            return DISKS
        if key == "项目":
            d = self._s.get("disk")
            return d.children if d else []
        if key == "子目录":
            p = self._s.get("project")
            return [c for c in p.children if c.kind == "folder"] if p else []
        p = self._s.get("folder") or self._s.get("project")
        return [c for c in p.children if c.kind == "shoot"] if p else []

    _KEYMAP = {"保存位置": "disk", "项目": "project", "子目录": "folder", "工作区": "shoot"}

    def _pick(self, key: str):
        opts = self._options(key)
        dlg = Picker(self._rows[key], opts, key, allow_new=(key != "保存位置"))
        dlg.chosen.connect(lambda node, k=key: self._set(k, node))
        dlg.exec()

    def _step(self, key: str, delta: int):
        opts = mru(self._options(key))
        cur = self._s.get(self._KEYMAP[key])
        if not opts:
            return
        idx = opts.index(cur) if cur in opts else -1
        self._set(key, opts[(idx + delta) % len(opts)])

    def _new(self, key: str):
        self._set(key, None)

    def _set(self, key: str, node):
        slot = self._KEYMAP[key]
        if node is None:                       # 新建
            name = f"新{key}{random.randint(10,99)}"
            parent = {"项目": self._s.get("disk"),
                      "子目录": self._s.get("project"),
                      "工作区": self._s.get("folder") or self._s.get("project")}.get(key)
            kind = {"项目": "project", "子目录": "folder", "工作区": "shoot"}[key]
            node = Node(name, kind, f"{parent.path}/{name}" if parent else name, last_open=time.time())
            if parent:
                parent.children.append(node)
        self._s[slot] = node
        # 下层重置
        order = ["disk", "project", "folder", "shoot"]
        for lower in order[order.index(slot) + 1:]:
            self._s[lower] = None
        # 自动补全下层（能选就先选最近的那个）
        for k in ("项目", "子目录", "工作区"):
            s = self._KEYMAP[k]
            if self._s.get(s) is None:
                opts = mru(self._options(k))
                self._s[s] = opts[0] if opts else None
        self._sync()

    def _sync(self):
        icons = {"保存位置": "💾", "项目": "🗂", "子目录": "📁", "工作区": "📷"}
        for key in self._rows:
            node = self._s.get(self._KEYMAP[key])
            btn = self._rows[key]
            if node is None:
                btn.setText("—  （可留空）" if key == "子目录" else "—")
                continue
            if key == "保存位置":
                extra = f"{len(node.children)} 个项目 · {node.free}"
            elif key == "工作区":
                extra = f"{node.specimens} 标本 · {_ago(node.last_open)}" if node.alive else "⚠ 路径不存在"
            elif key == "项目":
                extra = f"{len(all_shoots(node))} 个工作区"
            else:
                extra = "可留空"
            btn.setText(f"{icons[key]}  {node.name}    ▾            {extra}")
        shoot = self._s.get("shoot")
        self._go.setText(f"进入工作区 {shoot.name}" if shoot else "进入工作区")
        self._go.setEnabled(shoot is not None)

    def _enter(self, node):
        if node is None:
            return
        node.last_open = time.time()
        self.entered.emit(node)
        self.accept()


# ── 顶栏（原样保留那一排控件） ───────────────────────────────────────────────

class TopBar(QWidget):
    def __init__(self, log: QLabel):
        super().__init__()
        self._log = log
        d0 = DISKS[0]
        p0 = d0.children[0]
        s0 = [c for c in p0.children if c.kind == "shoot"][0]
        self._state = {"disk": d0, "project": p0, "folder": None, "shoot": s0}
        self._history: list[Node] = [s0]
        self._hpos = 0

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)

        brand = QLabel("标本影像管理")
        brand.setStyleSheet("font-weight:800;")
        lay.addWidget(brand)

        self._crumb = QLabel("")
        self._crumb.setStyleSheet("color:#8a999d;")
        lay.addWidget(self._crumb)

        self._back = QToolButton(); self._back.setText("◀"); self._back.setFixedSize(32, 32)
        self._back.setToolTip("回上一个去过的工作区（后退）")
        self._back.clicked.connect(lambda: self._hstep(-1))
        lay.addWidget(self._back)

        self._leaf = QPushButton()
        self._leaf.setStyleSheet("QPushButton{border:1.5px solid #0e9384;border-radius:7px;padding:6px 12px;"
                                 "font-weight:700;background:#fff;}")
        self._leaf.setToolTip("换一个工作区（同级列表 + 搜索 + 最近）")
        self._leaf.clicked.connect(self._quick_switch)
        lay.addWidget(self._leaf)

        self._fwd = QToolButton(); self._fwd.setText("▶"); self._fwd.setFixedSize(32, 32)
        self._fwd.setToolTip("前进")
        self._fwd.clicked.connect(lambda: self._hstep(+1))
        lay.addWidget(self._fwd)

        self._dd = QToolButton(); self._dd.setText("▼"); self._dd.setFixedSize(32, 32)
        self._dd.setToolTip("全部项目 / 工作区（搜索 · 最近 · 收藏）")
        self._dd.clicked.connect(self._quick_switch)
        lay.addWidget(self._dd)

        self._folder = QToolButton(); self._folder.setText("📁▾"); self._folder.setFixedSize(40, 32)
        self._folder.setStyleSheet("QToolButton{border:1px solid #9fd4cc;border-radius:6px;color:#0e9384;}")
        self._folder.setToolTip("照片保存设置（选位置 / 新建 / 资料）")
        self._folder.clicked.connect(self._open_panel)
        lay.addWidget(self._folder)

        lay.addStretch(1)
        hint = QLabel("Ctrl+K 快速切换")
        hint.setStyleSheet("color:#9aa4a7;font-size:12px;")
        lay.addWidget(hint)

        QShortcut(QKeySequence("Ctrl+K"), self).activated.connect(self._quick_switch)
        self._sync()

    def _sync(self):
        s = self._state
        parts = [s["disk"].name if s["disk"] else "", s["project"].name if s["project"] else ""]
        if s["folder"]:
            parts.append(s["folder"].name)
        self._crumb.setText("  ›  ".join([p for p in parts if p]) + "  ›")
        shoot = s.get("shoot")
        self._leaf.setText(f"📷 {shoot.name}  ▾" if shoot else "选择工作区 ▾")
        self._back.setEnabled(self._hpos > 0)
        self._fwd.setEnabled(self._hpos < len(self._history) - 1)

    def _quick_switch(self):
        """▾ / ▼ / Ctrl+K —— 纯切换：全部工作区，最近优先，可搜索。"""
        shoots = [s for d in DISKS for s in all_shoots(d)]
        dlg = Picker(self._leaf, shoots, "工作区", allow_new=False)
        dlg.chosen.connect(self._enter)
        dlg.exec()

    def _open_panel(self):
        dlg = LocationPanel(self._folder, self._state)
        dlg.entered.connect(self._enter)
        dlg.exec()
        self._sync()

    def _enter(self, node: Node):
        if node is None:
            return
        node.last_open = time.time()
        self._state["shoot"] = node
        self._history = self._history[: self._hpos + 1] + [node]
        self._hpos = len(self._history) - 1
        self._after_enter(node)

    def _hstep(self, delta: int):
        self._hpos = max(0, min(self._hpos + delta, len(self._history) - 1))
        node = self._history[self._hpos]
        self._state["shoot"] = node
        self._after_enter(node, record=False)

    def _after_enter(self, node: Node, record: bool = True):
        state = "" if node.alive else "   ⚠ 路径不存在 → 应弹「重新定位…」"
        self._log.setText(f"▶ 进入「{node.name}」   {node.path}{state}")
        self._sync()


class Demo(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("顶栏 + 四层选位面板（融合版原型）")
        self.resize(1080, 620)
        root = QWidget()
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)

        log = QLabel("（还没进入任何工作区）")
        log.setStyleSheet("background:#1d2b2e;color:#5eead4;padding:10px 14px;border-radius:8px;"
                          "font-family:monospace;")

        bar = TopBar(log)
        bar.setStyleSheet("QWidget{background:#fff;border:1px solid #dfe6e8;border-radius:10px;}")
        lay.addWidget(bar)

        tip = QLabel(
            "顶栏原样保留 —— 点开的东西全变了：\n\n"
            "  📷 xxx ▾ / ▼ / Ctrl+K   →  纯切换：全部工作区，**最近优先**，可搜索（拼音首字母也行：打 hc / dm）\n"
            "                              每条显示「N 标本 · 上次进入」；死路径标灰 + 「重新定位…」\n"
            "  ◀ ▶                      →  后退 / 前进（回上一个去过的）\n"
            "  📁▾                      →  照片保存设置 = 四层选位面板：\n"
            "                                保存位置 / 项目 / 子目录（可留空） / 工作区\n"
            "                                每行：◀▶ 切同级 · 点名字看本层全部（带搜索+统计）· ＋ 就地新建 · ⚙ 改这层资料\n"
            "                                面板顶部还有「最近」一排，一点就走，不用逐层选\n\n"
            "数据：3 个磁盘 / 24 个项目 / 上百个工作区（试大规模），含 1 个「盘拔了」的死路径。"
        )
        tip.setStyleSheet("color:#44565a;padding:14px;background:#f7f9f9;border:1px solid #e2e8ea;border-radius:10px;")
        lay.addWidget(tip, 1)
        lay.addWidget(log)


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    app = QApplication(sys.argv)
    app.setStyleSheet('* { font-family: "Microsoft YaHei", "Noto Sans CJK SC", sans-serif; font-size: 14px; }')
    w = Demo()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
