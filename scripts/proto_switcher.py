#!/usr/bin/env python3
"""proto_switcher.py — 「进入工作区」的 5 种方案，可点可比（用户 2026-07-13 要求）。

运行（WSL）:
    cd /mnt/e/photo-manager-ydy && QT_QPA_PLATFORM=xcb python3 scripts/proto_switcher.py

顶部切 1~5 就换一种方案，用**你的真实项目数据**（data/user_projects.json）。
没有数据时自动造 40 个项目 × 若干断面的假数据，专门用来试「项目多了会怎样」。

这是**独立原型**，不改主程序。选定后我再把它落进 workspace_breadcrumb。

五种方案:
  1 MRU 下拉      —— 最近优先 + 搜索框 + 每条显示「上次进入 · 标本数」（改动最小）
  2 命令面板      —— Ctrl+K 全局唤起，键盘打字直达（拼音首字母也行），完全不用鼠标
  3 固定收藏栏    —— 常用工作区钉在顶栏上，一直看得见，一键直达
  4 四层选位器    —— 磁盘 / 项目 / 文件夹 / 拍摄目录 逐层选（OM Capture 式）
  5 启动卡片墙    —— 项目卡片（封面 + 统计），点卡进层，最后点断面进入
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

from PyQt6.QtCore import QEvent, QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QKeySequence, QPainter, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

# ── 数据模型（原型自带，落地时用真实的 project_service） ─────────────────────


@dataclass
class Node:
    name: str
    path: str
    kind: str                    # "project" | "folder" | "shoot"
    parent: str = ""
    specimens: int = 0
    photos: int = 0
    last_open: float = 0.0       # epoch 秒；0 = 从没进过
    pinned: bool = False
    alive: bool = True           # 路径还在不在（盘拔了就是 False）
    children: list = field(default_factory=list)


def _ago(ts: float) -> str:
    if not ts:
        return "从未进入"
    d = time.time() - ts
    if d < 3600:
        return f"{int(d // 60)} 分钟前"
    if d < 86400:
        return f"{int(d // 3600)} 小时前"
    if d < 86400 * 7:
        return f"{int(d // 86400)} 天前"
    return time.strftime("%m-%d", time.localtime(ts))


def _pinyin_initials(text: str) -> str:
    """极简拼音首字母（原型够用；落地会换成 pypinyin）。"""
    table = {
        "航": "h", "次": "c", "断": "d", "面": "m", "潮": "c", "间": "j", "带": "d",
        "专": "z", "项": "x", "北": "b", "方": "f", "多": "d", "样": "y", "性": "x",
        "调": "d", "查": "c", "江": "j", "苏": "s", "盐": "y", "城": "c", "广": "g",
        "西": "x", "合": "h", "浦": "p", "南": "n", "海": "h", "站": "z", "位": "w",
    }
    out = []
    for ch in text:
        if ch in table:
            out.append(table[ch])
        elif ch.isascii():
            out.append(ch.lower())
    return "".join(out)


def _match(node: Node, q: str) -> bool:
    if not q:
        return True
    q = q.lower().strip()
    hay = f"{node.name} {node.parent} {node.path}".lower()
    return q in hay or q in _pinyin_initials(node.name)


def load_nodes() -> list[Node]:
    """真实数据优先；没有就造 40 个项目的假数据（试大规模）。"""
    nodes: list[Node] = []
    real = Path(__file__).resolve().parents[1] / "data" / "user_projects.json"
    try:
        entries = json.loads(real.read_text(encoding="utf-8")).get("projects", [])
    except Exception:
        entries = []

    now = time.time()
    for i, e in enumerate(entries):
        d = e.get("directory") or e.get("dir") or ""
        if not d:
            continue
        p = Path(d)
        nodes.append(Node(
            name=e.get("name") or p.name,
            path=d,
            kind="shoot" if (p / "_data" / "project.db").exists() else "project",
            parent=p.parent.name,
            specimens=random.randint(0, 400),
            photos=random.randint(0, 3000),
            last_open=now - random.randint(60, 86400 * 30),
            alive=p.exists(),
        ))

    if len(nodes) >= 8:
        return nodes

    # ── 假数据：40 个项目 × 每个 3~6 个断面 —— 专门用来试「项目多了会怎样」──
    proj_names = [
        "北方多样性调查", "潮间带专项", "南海断面2025", "航次2026", "江苏盐城-2026",
        "广西合浦调查", "东海底栖普查", "黄河口湿地", "渤海湾监测", "珠江口专项",
    ]
    nodes = []
    for pi in range(40):
        base = proj_names[pi % len(proj_names)]
        pname = base if pi < len(proj_names) else f"{base}-{pi // len(proj_names) + 1}"
        proot = f"/data/{pname}"
        proj = Node(name=pname, path=proot, kind="project", parent="data",
                    last_open=now - random.randint(3600, 86400 * 60))
        for si in range(random.randint(3, 6)):
            sec = f"断面{chr(65 + si)}"
            shoot = Node(
                name=sec, path=f"{proot}/{sec}", kind="shoot", parent=pname,
                specimens=random.randint(0, 500), photos=random.randint(0, 4000),
                last_open=now - random.randint(60, 86400 * 40),
                pinned=(pi < 2 and si == 0),
                alive=not (pi == 3 and si == 1),   # 造一个「盘拔了」的死路径
            )
            proj.children.append(shoot)
            nodes.append(shoot)
        nodes.append(proj)
    return nodes


NODES = load_nodes()
SHOOTS = [n for n in NODES if n.kind == "shoot"]
PROJECTS = [n for n in NODES if n.kind == "project"]


def mru(items: list[Node]) -> list[Node]:
    return sorted(items, key=lambda n: (-n.pinned, -n.last_open))


# ── 公共：进入动作 ───────────────────────────────────────────────────────────

class Bus:
    """原型里的「进入工作区」"""
    log: QLabel | None = None

    @classmethod
    def enter(cls, node: Node) -> None:
        node.last_open = time.time()      # ← 真正的 MRU：进一次就顶到最前
        if cls.log is not None:
            state = "" if node.alive else "  ⚠ 路径不存在（盘未连接）"
            cls.log.setText(f"▶ 进入「{node.name}」  {node.path}{state}")


# ── 方案 1：MRU 下拉 ─────────────────────────────────────────────────────────

class Plan1(QWidget):
    """最近优先 + 搜索 + 每条显示「上次进入 · 标本数」。改动最小的一版。"""

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self._btn = QPushButton("📁 " + (mru(SHOOTS)[0].name if SHOOTS else "选择工作区") + "  ▾")
        self._btn.setMinimumWidth(260)
        self._btn.setStyleSheet("padding:8px 14px;font-weight:600;")
        self._btn.clicked.connect(self._popup)
        bar.addWidget(self._btn)
        bar.addStretch(1)
        lay.addLayout(bar)
        lay.addWidget(QLabel(
            "点按钮 → 最近用过的排最前（真 MRU：进过就顶到第一）。\n"
            "带搜索框，能打拼音首字母（试试 hc / dm）。死路径标灰并给「重新定位」。"
        ))
        lay.addStretch(1)

    def _popup(self):
        dlg = _RecentPopup(self._btn)
        dlg.chosen.connect(self._on_chosen)
        dlg.exec()

    def _on_chosen(self, node: Node):
        Bus.enter(node)
        self._btn.setText(f"📁 {node.name}  ▾")


class _RecentPopup(QDialog):
    chosen = pyqtSignal(object)

    def __init__(self, anchor: QWidget):
        super().__init__(anchor.window())
        self.setWindowFlags(Qt.WindowType.Popup)
        self.setFixedWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索工作区…（可打拼音首字母 hc / dm）")
        self._search.textChanged.connect(self._refill)
        lay.addWidget(self._search)
        self._list = QListWidget()
        self._list.setStyleSheet("QListWidget::item{padding:6px;}")
        self._list.itemActivated.connect(self._pick)
        self._list.itemClicked.connect(self._pick)
        lay.addWidget(self._list)
        self._refill("")
        g = anchor.mapToGlobal(QPoint(0, anchor.height() + 4))
        self.move(g)
        self.resize(460, 420)

    def _refill(self, q: str):
        self._list.clear()
        for n in mru(SHOOTS):
            if not _match(n, q):
                continue
            star = "★ " if n.pinned else ""
            if n.alive:
                text = f"{star}{n.name}      {n.parent} · {_ago(n.last_open)} · {n.specimens} 标本"
            else:
                text = f"{star}{n.name}      ⚠ 路径不存在 — 双击可「重新定位…」"
            it = QListWidgetItem(text)
            it.setData(Qt.ItemDataRole.UserRole, n)
            if not n.alive:
                it.setForeground(QColor("#9aa4a7"))
            self._list.addItem(it)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _pick(self, item: QListWidgetItem):
        self.chosen.emit(item.data(Qt.ItemDataRole.UserRole))
        self.accept()


# ── 方案 2：命令面板（Ctrl+K） ───────────────────────────────────────────────

class Plan2(QWidget):
    """键盘流：Ctrl+K 唤起，打字直达，回车进入。手不离键盘。"""

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        big = QLabel("按  Ctrl + K   随时唤起")
        big.setStyleSheet("font-size:22px;font-weight:800;color:#0e9384;")
        big.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addStretch(1)
        lay.addWidget(big)
        tip = QLabel(
            "打字即搜（名字 / 路径 / 拼音首字母），↑↓ 选，回车进入，Esc 关。\n"
            "项目再多也不怕 —— 不用找，直接打。\n\n"
            "（也可以点这里的按钮试）"
        )
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(tip)
        btn = QPushButton("打开命令面板")
        btn.setFixedWidth(180)
        btn.clicked.connect(self._open)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(2)
        self._sc = QShortcut(QKeySequence("Ctrl+K"), self)
        self._sc.activated.connect(self._open)

    def _open(self):
        dlg = _Palette(self.window())
        dlg.exec()


class _Palette(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setFixedSize(620, 420)
        self.setStyleSheet(
            "QDialog{background:#ffffff;border:1px solid #cfd9db;border-radius:12px;}"
            "QLineEdit{border:none;border-bottom:1px solid #e2e8ea;padding:14px;font-size:17px;}"
            "QListWidget{border:none;font-size:14px;}"
            "QListWidget::item{padding:8px 10px;}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._inp = QLineEdit()
        self._inp.setPlaceholderText("去哪个工作区？打名字 / 拼音首字母…")
        self._inp.textChanged.connect(self._refill)
        self._inp.returnPressed.connect(self._go)
        lay.addWidget(self._inp)
        self._list = QListWidget()
        self._list.itemClicked.connect(lambda _i: self._go())
        lay.addWidget(self._list)
        self._refill("")
        self._inp.setFocus()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            row = self._list.currentRow() + (1 if e.key() == Qt.Key.Key_Down else -1)
            self._list.setCurrentRow(max(0, min(row, self._list.count() - 1)))
            return
        super().keyPressEvent(e)

    def _refill(self, q: str):
        self._list.clear()
        for n in mru(SHOOTS):
            if not _match(n, q):
                continue
            mark = "★" if n.pinned else ("⚠" if not n.alive else "📷")
            it = QListWidgetItem(f"{mark}  {n.name}    ·  {n.parent}  ·  {_ago(n.last_open)}  ·  {n.specimens} 标本")
            it.setData(Qt.ItemDataRole.UserRole, n)
            self._list.addItem(it)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _go(self):
        it = self._list.currentItem()
        if it:
            Bus.enter(it.data(Qt.ItemDataRole.UserRole))
        self.accept()


# ── 方案 3：固定收藏栏 ───────────────────────────────────────────────────────

class Plan3(QWidget):
    """常用的钉在顶栏，一直看得见，一键直达；右键可取消固定。"""

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        self._bar = QHBoxLayout()
        self._rebuild()
        lay.addLayout(self._bar)
        lay.addWidget(QLabel(
            "★ 固定的工作区常驻顶栏（一直看得见，点一下就进）。\n"
            "右键任意一个可取消固定；点「＋」从全部工作区里挑要固定的。\n"
            "适合：长期只在少数几个断面之间来回切。"
        ))
        lay.addStretch(1)

    def _rebuild(self):
        while self._bar.count():
            it = self._bar.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        pinned = [n for n in SHOOTS if n.pinned] or mru(SHOOTS)[:3]
        for n in pinned:
            b = QPushButton(f"★ {n.name}\n{n.specimens} 标本 · {_ago(n.last_open)}")
            b.setStyleSheet(
                "QPushButton{padding:8px 14px;border:1.5px solid #0e9384;border-radius:8px;"
                "background:#e6f5f2;color:#0e9384;font-weight:700;text-align:left;}"
                "QPushButton:hover{background:#d6efea;}"
            )
            b.clicked.connect(lambda _c=False, node=n: (Bus.enter(node), self._rebuild()))
            b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda _p, node=n: self._unpin(node))
            self._bar.addWidget(b)
        add = QPushButton("＋")
        add.setFixedSize(44, 52)
        add.setStyleSheet("QPushButton{border:1.5px dashed #9fd4cc;border-radius:8px;color:#0e9384;font-size:18px;}")
        add.clicked.connect(self._pick_pin)
        self._bar.addWidget(add)
        self._bar.addStretch(1)

    def _unpin(self, node: Node):
        node.pinned = False
        self._rebuild()

    def _pick_pin(self):
        dlg = _RecentPopup(self)
        dlg.chosen.connect(lambda n: (setattr(n, "pinned", True), self._rebuild()))
        dlg.exec()


# ── 方案 4：四层选位器 ───────────────────────────────────────────────────────

class Plan4(QWidget):
    """磁盘 / 项目 / 文件夹 / 拍摄目录，逐层选。层级永远摊开可见。"""

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        card = QFrame()
        card.setStyleSheet("QFrame{background:#f7f9f9;border:1px solid #dfe6e8;border-radius:12px;}")
        cl = QVBoxLayout(card)
        self._proj = PROJECTS[0] if PROJECTS else None
        self._rows: dict[str, QPushButton] = {}
        for label in ("保存位置", "项目", "拍摄目录"):
            row = QHBoxLayout()
            lb = QLabel(label)
            lb.setFixedWidth(70)
            lb.setStyleSheet("color:#66777b;")
            row.addWidget(lb)
            prev = QToolButton(); prev.setText("◀"); prev.setFixedSize(30, 30)
            nxt = QToolButton(); nxt.setText("▶"); nxt.setFixedSize(30, 30)
            sel = QPushButton("—")
            sel.setStyleSheet("QPushButton{text-align:left;padding:7px 12px;border:1.5px solid #cfd9db;border-radius:7px;background:#fff;}")
            plus = QToolButton(); plus.setText("＋"); plus.setFixedSize(30, 30)
            plus.setStyleSheet("QToolButton{background:#0e9384;color:#fff;border-radius:6px;font-weight:800;}")
            gear = QToolButton(); gear.setText("⚙"); gear.setFixedSize(30, 30)
            row.addWidget(prev); row.addWidget(sel, 1); row.addWidget(nxt)
            row.addWidget(plus); row.addWidget(gear)
            cl.addLayout(row)
            self._rows[label] = sel
        self._rows["保存位置"].setText("💾  /data   ·  剩 1.2 TB")
        self._rows["项目"].setText(f"🗂  {self._proj.name if self._proj else '—'}")
        self._rows["项目"].clicked.connect(self._pick_project)
        shoots = self._proj.children if self._proj else []
        self._rows["拍摄目录"].setText(f"📷  {shoots[0].name}" if shoots else "—")
        self._rows["拍摄目录"].clicked.connect(self._pick_shoot)
        go = QPushButton("进入拍照")
        go.setStyleSheet("QPushButton{background:#0e9384;color:#fff;font-weight:800;padding:10px;border-radius:8px;}")
        go.clicked.connect(self._go)
        cl.addWidget(go)
        lay.addWidget(card)
        lay.addWidget(QLabel(
            "每行：◀▶ 切同级 · 点名字看本层全部 · ＋ 在这层新建 · ⚙ 改这层资料。\n"
            "上层一变，下层跟着刷新。层级永远摊开可见 —— 最不容易迷路。"
        ))
        lay.addStretch(1)

    def _pick_project(self):
        m = QMenu(self)
        for p in mru(PROJECTS)[:30]:
            a = m.addAction(f"{p.name}   · {len(p.children)} 个拍摄目录 · {_ago(p.last_open)}")
            a.triggered.connect(lambda _c=False, node=p: self._set_project(node))
        m.exec(self._rows["项目"].mapToGlobal(QPoint(0, self._rows["项目"].height())))

    def _set_project(self, p: Node):
        self._proj = p
        self._rows["项目"].setText(f"🗂  {p.name}")
        self._rows["拍摄目录"].setText(f"📷  {p.children[0].name}" if p.children else "—")

    def _pick_shoot(self):
        if not self._proj:
            return
        m = QMenu(self)
        for s in mru(self._proj.children):
            a = m.addAction(f"{s.name}   · {s.specimens} 标本 · {_ago(s.last_open)}")
            a.triggered.connect(lambda _c=False, node=s: self._rows["拍摄目录"].setText(f"📷  {node.name}"))
        m.exec(self._rows["拍摄目录"].mapToGlobal(QPoint(0, self._rows["拍摄目录"].height())))

    def _go(self):
        name = self._rows["拍摄目录"].text().replace("📷", "").strip()
        for s in SHOOTS:
            if s.name == name:
                Bus.enter(s)
                return


# ── 方案 5：启动卡片墙 ───────────────────────────────────────────────────────

class Plan5(QWidget):
    """项目卡片（封面 + 统计），点卡展开断面，点断面进入。"""

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        self._crumb = QLabel("我的项目")
        self._crumb.setStyleSheet("font-size:15px;font-weight:700;")
        lay.addWidget(self._crumb)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;}")
        self._host = QWidget()
        self._grid = QGridLayout(self._host)
        self._grid.setSpacing(14)
        scroll.setWidget(self._host)
        lay.addWidget(scroll, 1)
        self._show_projects()

    def _clear(self):
        while self._grid.count():
            it = self._grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def _card(self, title: str, sub: str, hue: int) -> QFrame:
        f = QFrame()
        f.setFixedSize(210, 150)
        f.setStyleSheet(
            f"QFrame{{background:#fff;border:1px solid #dfe6e8;border-radius:12px;}}"
            f"QFrame:hover{{border:1.5px solid #0e9384;}}"
        )
        v = QVBoxLayout(f)
        v.setContentsMargins(0, 0, 0, 0)
        cover = QLabel()
        pm = QPixmap(208, 82)
        pm.fill(QColor.fromHsv(hue % 360, 60, 200))
        cover.setPixmap(pm)
        v.addWidget(cover)
        t = QLabel(title)
        t.setStyleSheet("font-weight:700;padding:6px 10px 0 10px;")
        v.addWidget(t)
        s = QLabel(sub)
        s.setStyleSheet("color:#66777b;font-size:12px;padding:0 10px 8px 10px;")
        v.addWidget(s)
        return f

    def _show_projects(self):
        self._clear()
        self._crumb.setText("我的项目  —— 点一张卡进入它的断面")
        for i, p in enumerate(mru(PROJECTS)[:24]):
            c = self._card(
                f"🗂 {p.name}",
                f"{len(p.children)} 个拍摄目录 · {_ago(p.last_open)}",
                i * 37,
            )
            c.mousePressEvent = lambda _e, node=p: self._show_shoots(node)
            self._grid.addWidget(c, i // 4, i % 4)

    def _show_shoots(self, p: Node):
        self._clear()
        self._crumb.setText(f"我的项目  ›  {p.name}   —— 点断面直接进入拍照")
        back = self._card("← 返回全部项目", "", 0)
        back.mousePressEvent = lambda _e: self._show_projects()
        self._grid.addWidget(back, 0, 0)
        for i, s in enumerate(mru(p.children), start=1):
            sub = f"{s.specimens} 标本 · {_ago(s.last_open)}" if s.alive else "⚠ 路径不存在"
            c = self._card(f"📷 {s.name}", sub, i * 53)
            c.mousePressEvent = lambda _e, node=s: Bus.enter(node)
            self._grid.addWidget(c, i // 4, i % 4)


# ── 外壳 ─────────────────────────────────────────────────────────────────────

PLANS = [
    ("方案 1 · MRU 下拉", "最近优先 + 搜索 + 「上次进入·标本数」。改动最小，立刻能落地。", Plan1),
    ("方案 2 · 命令面板 Ctrl+K", "打字直达，键盘流。项目再多也不用找。", Plan2),
    ("方案 3 · 固定收藏栏", "常用的钉在顶栏一直看得见，一键直达。", Plan3),
    ("方案 4 · 四层选位器", "磁盘/项目/拍摄目录逐层选，层级永远摊开。", Plan4),
    ("方案 5 · 启动卡片墙", "项目卡片 + 封面统计，看着照片选。", Plan5),
]


class Demo(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("进入工作区 —— 5 种方案对比（原型）")
        self.resize(1080, 700)
        root = QWidget()
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)

        tabs = QHBoxLayout()
        self._stack = QStackedWidget()
        for i, (title, desc, cls) in enumerate(PLANS):
            b = QPushButton(title)
            b.setCheckable(True)
            b.setChecked(i == 0)
            b.setStyleSheet(
                "QPushButton{padding:8px 14px;border:1px solid #dfe6e8;border-radius:8px;background:#fff;}"
                "QPushButton:checked{background:#0e9384;color:#fff;font-weight:700;border-color:#0e9384;}"
            )
            b.clicked.connect(lambda _c=False, idx=i: self._switch(idx))
            tabs.addWidget(b)
            self._buttons = getattr(self, "_buttons", [])
            self._buttons.append(b)
            self._stack.addWidget(cls())
        tabs.addStretch(1)
        lay.addLayout(tabs)

        self._desc = QLabel(PLANS[0][1])
        self._desc.setStyleSheet("color:#66777b;padding:4px 2px 8px 2px;")
        lay.addWidget(self._desc)

        box = QFrame()
        box.setStyleSheet("QFrame{background:#f7f9f9;border:1px solid #e2e8ea;border-radius:12px;}")
        bl = QVBoxLayout(box)
        bl.addWidget(self._stack)
        lay.addWidget(box, 1)

        Bus.log = QLabel("（还没进入任何工作区）")
        Bus.log.setStyleSheet(
            "background:#1d2b2e;color:#5eead4;padding:10px 14px;border-radius:8px;font-family:monospace;"
        )
        lay.addWidget(Bus.log)

        info = QLabel(
            f"数据：{len(PROJECTS)} 个项目 / {len(SHOOTS)} 个拍摄目录"
            "　·　每次「进入」都会更新最近时间（这就是真 MRU —— 现在的软件没有）"
            "　·　列表里混了一个「盘拔了」的死路径，看各方案怎么处理"
        )
        info.setStyleSheet("color:#8a999d;font-size:12px;")
        lay.addWidget(info)

    def _switch(self, idx: int):
        self._stack.setCurrentIndex(idx)
        self._desc.setText(PLANS[idx][1])
        for i, b in enumerate(self._buttons):
            b.setChecked(i == idx)


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    app = QApplication(sys.argv)
    app.setStyleSheet('* { font-family: "Microsoft YaHei", "Noto Sans CJK SC", sans-serif; }')
    w = Demo()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
