"""project_station_import_dialog.py — 导入项目站位总表（断面路由分发，新增）.

一个调查只有一张站位总表：含「断面」列 + 地区/样地/站位/经纬度列。在调查根目录
导入一次，每一行按「断面」列的值路由到对应的子文件夹工作区的 collection_records。

  选文件 → 列映射（断面[必填]/站位/经度/纬度/可选地区·样地·站位说明·经纬合一）
  + 源坐标系 → 预览分发（每个断面命中哪个子文件夹 / 哪些断面无对应文件夹被跳过）
  → 确认分发（写入各工作区）。

纯叠加：复用 coord_import_dialog 的暗色 QSS 习语与列映射结构，不改任何既有控件。
后端逻辑全部委托给 project_station_import_service（preview_distribution / distribute）。
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.services import coord_import_service as cis
from app.services import project_station_import_service as psis
from app.utils import ui

_NONE = "—无—"

# 列映射目标字段（断面在前，必填）。其余字段复用 coord_import 的语义。
_FIELD_LABELS: list[tuple[str, str]] = [
    ("transect", "断面（路由列，必填）"),
    ("station", "站位"),
    ("lon", "经度"),
    ("lat", "纬度"),
    ("province", "地区（可选）"),
    ("site", "样地/采集地（可选）"),
    ("station_label", "站位说明（可选）"),
    ("lonlat", "经纬度(合一列，可选)"),
]

# 自动猜测命中词（断面用 coord_import 没有的词；其余沿用其习惯）。
_GUESS_HINTS: dict[str, list[str]] = {
    "transect": ["断面", "transect", "section", "线"],
    "station": ["站位", "站点", "station"],
    "lon": ["经度", "经", "lon", "lng", "longitude", "x"],
    "lat": ["纬度", "纬", "lat", "latitude", "y"],
    "province": ["地区", "省", "province", "prov"],
    "site": ["样地", "采集地", "site"],
    "station_label": ["说明", "名称", "label", "name"],
    "lonlat": ["坐标", "经纬", "coord"],
}


def _theme():
    try:
        from app.config.theme import TOKENS
        return TOKENS.get
    except Exception:  # pragma: no cover
        return lambda k, d=None: d


class ProjectStationImportDialog(QDialog):
    """导入项目站位总表 → 按断面路由分发到各子文件夹工作区。"""

    def __init__(self, root_dir: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._root_dir = root_dir
        self._headers: list[str] = []
        self._rows: list[dict] = []
        self._field_combos: dict[str, QComboBox] = {}
        self._plan: Optional[dict] = None
        self.setWindowTitle("导入项目站位总表")
        self.resize(820, 620)
        self._build_ui()
        self._apply_style()
        ui.center_on(self, parent)

    # ── UI ──────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)

        title = QLabel("导入项目站位总表（断面路由分发）")
        title.setObjectName("PaneTitle")
        v.addWidget(title)

        root_lbl = QLabel(f"调查根目录：{self._root_dir}")
        root_lbl.setObjectName("Mono")
        root_lbl.setWordWrap(True)
        v.addWidget(root_lbl)

        top = QHBoxLayout()
        self._file_lbl = QLabel("未选择文件")
        self._file_lbl.setObjectName("Muted")
        btn_file = QPushButton("选择文件…")
        btn_file.clicked.connect(self._pick_file)
        top.addWidget(btn_file)
        top.addWidget(self._file_lbl, 1)
        v.addLayout(top)

        hint = QLabel(
            "总表须含「断面」列：每行按断面值路由到同名子文件夹工作区。"
            "无对应子文件夹的断面会被跳过（其行会列出，绝不静默丢弃）。"
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        v.addWidget(hint)

        form = QFormLayout()
        for key, label in _FIELD_LABELS:
            combo = QComboBox()
            combo.addItem(_NONE)
            self._field_combos[key] = combo
            form.addRow(label, combo)
        self._coord_sys = QComboBox()
        self._coord_sys.addItems(cis.COORD_SYSTEMS)
        form.addRow("源坐标系", self._coord_sys)
        v.addLayout(form)

        bar = QHBoxLayout()
        self._btn_preview = QPushButton("预览分发")
        self._btn_preview.clicked.connect(self._on_preview)
        bar.addWidget(self._btn_preview)
        self._summary = QLabel("")
        self._summary.setObjectName("Muted")
        bar.addWidget(self._summary, 1)
        v.addLayout(bar)

        self._result_list = QListWidget()
        self._result_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        v.addWidget(self._result_list, 1)

        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        self._btn_distribute = QPushButton("确认分发")
        self._btn_distribute.setObjectName("Primary")
        self._btn_distribute.setEnabled(False)
        self._btn_distribute.clicked.connect(self._on_distribute)
        actions.addWidget(cancel)
        actions.addWidget(self._btn_distribute)
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
            f"QLabel#Mono{{color:{muted};font-family:monospace;font-size:11px;}}"
            f"QComboBox,QLineEdit{{background:{panel};color:{text};border:1px solid {border};"
            f"border-radius:5px;padding:4px 8px;font-size:13px;}}"
            f"QPushButton{{background:{panel};color:{text};border:1px solid {border};"
            f"border-radius:5px;padding:5px 12px;font-size:13px;}}"
            f"QPushButton:hover{{background:{border};}}"
            f"QPushButton#Primary{{background:{accent};color:{accent_fg};border:1px solid {accent};}}"
            f"QPushButton:disabled{{color:{muted};}}"
            f"QListWidget{{background:{bg};color:{text};border:1px solid {border};"
            f"border-radius:6px;font-size:13px;}}"
            f"QListWidget::item{{padding:4px 2px;}}"
        )

    # ── 逻辑（可单测）────────────────────────────────────────────────────────
    def load_file(self, path: str) -> None:
        self._headers, self._rows = cis.read_table(path)
        self._file_lbl.setText(path)
        for key, combo in self._field_combos.items():
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(_NONE)
            combo.addItems(self._headers)
            guess = self._guess_header(key)
            if guess:
                combo.setCurrentText(guess)
            combo.blockSignals(False)
        self._plan = None
        self._btn_distribute.setEnabled(False)

    def _guess_header(self, key: str) -> Optional[str]:
        hints = _GUESS_HINTS.get(key, [])
        for h in self._headers:
            hl = h.lower()
            if any(hint.lower() in hl for hint in hints):
                return h
        return None

    def current_mapping(self) -> dict:
        out = {}
        for key, combo in self._field_combos.items():
            txt = combo.currentText()
            if txt and txt != _NONE:
                out[key] = txt
        return out

    def set_mapping(self, mapping: dict) -> None:
        for key, col in mapping.items():
            combo = self._field_combos.get(key)
            if combo is not None:
                if combo.findText(col) < 0:
                    combo.addItem(col)
                combo.setCurrentText(col)

    def preview(self) -> dict:
        mapping = self.current_mapping()
        return psis.preview_distribution(
            self._root_dir, self._rows, mapping,
            coord_system=self._coord_sys.currentText(),
        )

    def distribute(self) -> dict:
        return psis.distribute(self._root_dir, self._plan)

    # ── UI 事件 ─────────────────────────────────────────────────────────────
    def _pick_file(self) -> None:
        path = ui.get_open_file_name(
            self, "选择站位总表", "",
            "表格 (*.xlsx *.xlsm *.csv *.txt);;所有文件 (*)",
        )
        if path:
            self.load_file(path)

    def _on_preview(self) -> None:
        if not self._rows:
            ui.warn(self, "预览分发", "请先选择站位总表文件。")
            return
        if "transect" not in self.current_mapping():
            ui.warn(self, "预览分发", "请先指定「断面」路由列。")
            return
        try:
            plan = self.preview()
        except Exception as exc:  # noqa: BLE001
            ui.warn(self, "预览分发失败", str(exc))
            return
        self._plan = plan
        self._render_preview(plan)
        self._btn_distribute.setEnabled(plan["totals"]["matched_transects"] > 0)

    def _render_preview(self, plan: dict) -> None:
        self._result_list.clear()
        for transect, info in plan.get("matched", {}).items():
            n = len(info.get("rows", []))
            self._result_list.addItem(
                f"✅ {transect} ← {n} 行 → {info.get('rel', '')}"
            )
        for transect, info in plan.get("unmatched", {}).items():
            n = len(info.get("rows", []))
            self._result_list.addItem(
                f"⚠️ {transect} ← {n} 行（无此文件夹，跳过）"
            )
        t = plan.get("totals", {})
        self._summary.setText(
            f"可写 {t.get('ok', 0)} 行 / 共 {t.get('rows', 0)} 行，"
            f"解析失败 {t.get('errors', 0)} 行；"
            f"命中断面 {t.get('matched_transects', 0)}，"
            f"未命中 {t.get('unmatched_transects', 0)}。"
        )

    def _on_distribute(self) -> None:
        if not self._plan:
            ui.warn(self, "确认分发", "请先预览分发。")
            return
        try:
            result = self.distribute()
        except Exception as exc:  # noqa: BLE001
            ui.warn(self, "分发失败", str(exc))
            return
        lines = [
            f"已写入 {result['written']} 行；"
            f"跳过未命中 {result['skipped_unmatched_rows']} 行。",
        ]
        targets = result.get("targets", {})
        if targets:
            lines.append("")
            for transect, info in targets.items():
                created = "（新建工作区）" if info.get("db_created") else ""
                lines.append(f"  • {transect}：{info['written']} 行 → "
                             f"{info['path']} {created}")
        unmatched = result.get("unmatched_transects", [])
        if unmatched:
            lines.append("")
            lines.append("未命中断面：" + "、".join(str(u) for u in unmatched))
        ui.info(self, "分发完成", "\n".join(lines))
        self.accept()
