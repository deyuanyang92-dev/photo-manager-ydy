"""label_step2_templates.py — Step 2「选择模版」section of the Label Print page.

Mirrors web ``renderLabelStep2`` + ``renderBucketColumn`` (app.js:14819-14933):
one column per bucket (样品瓶 always; RNAlater 组织管 only when R-prefix specimens
are selected), each with a header (icon + title + 导入JSON / 模板管理) and a grid
of template cards.  Every card shows a **live preview** of that template — this
is the web behavior the dropdown port had lost.

Clicking a card selects that template (persisted via ``LabelTemplateLibrary``);
the 编辑 button on a built-in clones it to a custom record and opens the designer;
the 管理 button on a custom opens the designer for that record.

Template / dims resolution reuse ``label_service.resolve_template`` /
``resolve_dims``; preview rendering reuses ``label_render.render_label_preview``.

Signals
-------
config_changed()  — active template changed (chosen / edited) → rebuild jobs.
"""
from __future__ import annotations

import copy
import json
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.services.label_service import (
    BUILTIN_TEMPLATES,
    LabelTemplateLibrary,
    id_from_key,
    is_library_key,
    key_from_id,
    resolve_dims,
    resolve_template,
)
from app.services.label_service import LabelService
from app.utils import ui
from app.utils.label_core import normalize_template, specimen_to_label_data


_BUCKET_META = {
    "sample": {"icon": "🧪", "title": "样品瓶"},
    "tissue": {"icon": "🧬", "title": "RNAlater 组织管"},
}

_CARD_W, _CARD_H = 260, 146  # preview box (px)
_CARD_MAX_W = 370


_DEMO_SPECIMEN = {
    "province": "FJ", "site": "XM", "station": "B2",
    "id": "DLC004", "storage": "T95E",
    "collectionDate": "20260602", "photoDate": "20260602",
    "species": "多毛类 sp.04", "latin": "Polychaeta sp.",
    "collector": "杨德援", "photographer": "钟珅",
    "family": "Polynoidae", "region": "福建·厦门",
    "lon": "118.18", "lat": "24.48", "geoArea": "东海",
    "photoNotes": "",
}


def _starter_custom_template(bucket: str) -> dict:
    """Domain starter for a new user template.

    Web used the active/default template as the custom starting point. For this
    desktop workflow, a new custom template should start from the actual lab
    information model: stable specimen identity, preservation/date, taxonomy,
    locality, collector, and a QR pointing at the canonical uniqueId.
    """
    if bucket == "tissue":
        return {
            "name": "RNAlater 自定义模板",
            "desc": "组织管：编号、保存方式、日期、RNA保存液、唯一编号QR",
            "flavor": "tissue",
            "minSize": {"w": 30, "h": 15},
            "lineHeight": 1.12,
            "rows": [
                {"fields": [{"key": "headerId", "style": "bold", "size": 7}],
                 "size": 7, "style": "bold", "wrap": False},
                {"fields": [{"key": "storage", "size": 6},
                            {"key": "shortDate", "size": 6}],
                 "size": 6, "sep": " · ", "wrap": False},
                {"fields": [{"key": "rnaPreservative", "size": 5}],
                 "size": 5, "wrap": False},
            ],
            "qr": {"content": "uniqueId", "position": "right", "sizePct": 0.52, "ecc": "Q"},
            "elements": [],
        }
    return {
        "name": "样品瓶自定义模板",
        "desc": "标本瓶：编号、保存、日期、物种、地点、坐标、采集人、唯一编号QR",
        "minSize": {"w": 60, "h": 40},
        "lineHeight": 1.2,
        "rows": [
            {"fields": [{"key": "headerId", "style": "bold", "size": 10}],
             "size": 10, "style": "bold", "wrap": False},
            {"fields": [{"key": "storage", "size": 8},
                        {"key": "shortDate", "size": 8}],
             "size": 8, "sep": " · ", "wrap": False},
            {"fields": [{"key": "speciesName", "style": "bold", "size": 8}],
             "size": 8, "style": "bold", "wrap": False},
            {"fields": [{"key": "latin", "style": "italic", "size": 7}],
             "size": 7, "style": "italic", "wrap": False},
            {"fields": [{"key": "family", "size": 7}],
             "prefix": "科: ", "size": 7, "wrap": False},
            {"fields": [{"key": "region", "size": 7}],
             "size": 7, "wrap": False},
            {"fields": [{"key": "lon", "size": 6}, {"key": "lat", "size": 6}],
             "size": 6, "sep": ", ", "wrap": False},
            {"fields": [{"key": "collectorLabel", "size": 7}],
             "size": 7, "wrap": False},
        ],
        "qr": {"content": "uniqueId", "position": "right", "sizePct": 0.34, "ecc": "Q"},
        "elements": [],
    }

# ── Theme colours — resolved from the LIVE active theme (CHROME ONLY) ─────────
# The card "preview" area background is chrome; the printed label rendered into
# it via label_render stays white-paper/black-text under every theme.
_C_BG = "#08161b"
_C_PANEL_2 = "#0c2027"
_C_INPUT_BG = "#0c2027"
_C_CARD_BG = "#ffffff"
_C_PREVIEW_BG = "#0c1e26"
_C_TEXT = "#eef3ef"
_C_TEXT_SOFT = "#cfe0db"
_C_MUTED_DIM = "#5f7d7a"
_C_ACCENT = "#29b9ab"
_C_WARN = "#f1bd57"
_C_SEL_BG = "#0f2f38"
_C_BADGE_BG = "rgba(145,182,181,0.18)"
_C_BADGE_WARN_BG = "rgba(241,189,87,0.20)"
_C_BORDER = "#cbd5e1"
_C_BORDER_HI = "#94a3b8"
_C_BORDER_STRONG = "#64748b"


def _refresh_palette() -> None:
    """Rebind the module `_C_*` chrome colours to the current theme tokens."""
    global _C_BG, _C_PANEL_2, _C_INPUT_BG, _C_CARD_BG, _C_PREVIEW_BG, _C_TEXT, _C_TEXT_SOFT
    global _C_MUTED_DIM, _C_ACCENT, _C_WARN, _C_SEL_BG
    global _C_BADGE_BG, _C_BADGE_WARN_BG, _C_BORDER, _C_BORDER_HI, _C_BORDER_STRONG
    from app.config.theme import TOKENS
    g = TOKENS.get
    _C_BG = g("bg", _C_BG)
    _C_PANEL_2 = g("panel_2", _C_PANEL_2)
    _C_INPUT_BG = g("input_bg", _C_INPUT_BG)
    _C_CARD_BG = g("panel", _C_CARD_BG)
    _C_PREVIEW_BG = g("panel_inset", _C_PREVIEW_BG)
    _C_TEXT = g("text", _C_TEXT)
    _C_TEXT_SOFT = g("text", _C_TEXT_SOFT)
    _C_MUTED_DIM = g("muted_dim", _C_MUTED_DIM)
    _C_ACCENT = g("accent", _C_ACCENT)
    _C_WARN = g("warn", _C_WARN)
    _C_SEL_BG = g("accent_softer", _C_SEL_BG)
    _C_BADGE_BG = g("panel_inset", _C_BADGE_BG)
    _C_BORDER = g("border_medium", _C_BORDER)
    _C_BORDER_HI = g("border_strong", _C_BORDER_HI)
    _C_BORDER_STRONG = g("border_strong", _C_BORDER_STRONG)
    if _is_light_hex(_C_BG):
        _C_CARD_BG = "#ffffff"
        _C_PREVIEW_BG = "#f8fafc"
        _C_SEL_BG = "#e6f5f3"
        _C_BADGE_BG = "#eef2f6"
        _C_BADGE_WARN_BG = "#fff4d6"
        _C_BORDER = "#cbd5e1"
        _C_BORDER_HI = "#94a3b8"
        _C_BORDER_STRONG = "#d1d9e3"


def _is_light_hex(color: str) -> bool:
    if not isinstance(color, str) or not color.startswith("#") or len(color) < 7:
        return False
    try:
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
    except ValueError:
        return False
    return (0.299 * r + 0.587 * g + 0.114 * b) > 180


def _css() -> str:
    return f"""
QFrame#TmplCard {{
    background-color: {_C_CARD_BG}; border: 1px solid {_C_BORDER};
    border-radius: 8px;
}}
QFrame#TmplCard[selected="true"] {{
    border: 2px solid {_C_ACCENT}; background-color: {_C_SEL_BG};
}}
QFrame#TmplAddCard {{
    background-color: {_C_CARD_BG}; border: 1px dashed {_C_BORDER_STRONG};
    border-radius: 8px;
}}
QFrame#TmplAddCard:hover {{
    border-color: {_C_ACCENT};
}}
QLabel#AddCardPlus {{
    background-color: transparent; color: {_C_ACCENT};
    font-size: 34px; font-weight: 300;
}}
QLabel#AddCardTitle {{
    background-color: transparent; color: {_C_TEXT};
    font-size: 13px; font-weight: 700;
}}
QLabel#AddCardDesc {{
    background-color: transparent; color: {_C_MUTED_DIM}; font-size: 11px;
}}
QFrame#BucketSection {{
    background-color: transparent;
}}
QLabel#CardTitle {{
    background-color: transparent; color: {_C_TEXT}; font-size: 13px; font-weight: 700;
}}
QLabel#CardDesc {{
    background-color: transparent; color: {_C_MUTED_DIM}; font-size: 11px;
}}
QLabel#CardPreview {{
    background-color: {_C_PREVIEW_BG}; border: 1px solid {_C_BORDER_STRONG};
    border-radius: 6px;
}}
QLabel#CardBadge {{
    background-color: {_C_BADGE_BG}; color: {_C_TEXT_SOFT};
    border-radius: 4px; padding: 2px 7px; font-size: 10px;
}}
QLabel#CardBadge[custom="true"] {{
    background-color: {_C_BADGE_WARN_BG}; color: {_C_WARN};
}}
QLabel#SelectedBadge {{
    background-color: {_C_ACCENT}; color: #ffffff;
    border-radius: 4px; padding: 2px 7px; font-size: 10px; font-weight: 700;
}}
QPushButton#CardAction, QPushButton#HeadAction {{
    background-color: transparent; border: 1px solid {_C_BORDER_HI};
    border-radius: 5px; color: {_C_TEXT_SOFT}; padding: 5px 10px; font-size: 11px;
}}
QPushButton#CardAction:hover, QPushButton#HeadAction:hover {{
    border-color: {_C_ACCENT}; color: {_C_ACCENT};
}}
QPushButton#CardChooseBtn {{
    background-color: transparent; border: 1px solid {_C_ACCENT};
    border-radius: 4px; color: {_C_ACCENT}; padding: 2px 8px; font-size: 10px;
}}
QPushButton#CardChooseBtn:hover {{
    background-color: {_C_ACCENT}; color: #ffffff;
}}
"""


class LabelStep2Templates(QWidget):
    """Step 2 — per-bucket template card grid with live previews."""

    config_changed = pyqtSignal()

    def __init__(
        self,
        libs: dict[str, LabelTemplateLibrary],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        _refresh_palette()
        self.setStyleSheet(f"background:{_C_BG}; color:{_C_TEXT};" + _css())
        self._libs = libs
        self._specimens: list[dict] = []
        self._selected_indices: list[int] = []
        self._cards: dict[str, list[dict]] = {}
        self._header_actions: dict[str, dict] = {}
        self._grid_cols = 0
        self._setup_ui()

    # ── UI ─────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(16)

        trow = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("标签模板库")
        title.setStyleSheet(f"color:{_C_TEXT}; font-size:18px; font-weight:800;")
        hint = QLabel("选择一个内置模板直接打印；点编辑会复制为自定义模板，不污染预设。")
        hint.setStyleSheet(f"color:{_C_MUTED_DIM}; font-size:11px;")
        title_box.addWidget(title)
        title_box.addWidget(hint)
        trow.addLayout(title_box)
        trow.addStretch()
        root.addLayout(trow)

        # Columns container (sample | tissue)
        self._cols_row = QHBoxLayout()
        self._cols_row.setSpacing(14)
        root.addLayout(self._cols_row, stretch=1)

        self._empty = QLabel("未选择编号时显示示例标签；选择后自动换成真实编号预览。")
        self._empty.setStyleSheet(f"color:{_C_MUTED_DIM}; font-size:12px;")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._empty)
        self._empty.hide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        cols = self._grid_cols_for_width()
        if self._grid_cols and cols != self._grid_cols:
            self._rebuild()

    # ── Public API ───────────────────────────────────────────────────────────

    def set_data(self, specimens: list[dict], selected_indices: list[int]) -> None:
        self._specimens = list(specimens or [])
        self._selected_indices = list(selected_indices or [])
        self._rebuild()

    def selected_template(self, bucket: str) -> dict:
        return resolve_template(self._libs[bucket])

    # ── Build ──────────────────────────────────────────────────────────────────

    def _buckets(self) -> dict:
        return LabelService.bucket(self._specimens, self._selected_indices)

    def _rebuild(self) -> None:
        # Clear columns
        while self._cols_row.count():
            it = self._cols_row.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
        self._cards = {}
        self._header_actions = {}

        buckets = self._buckets()
        has_selection = bool(self._selected_indices)
        self._empty.setVisible(not has_selection)
        visible_bucket_count = 1 + int(bool(buckets["tissues"]))
        self._grid_cols = self._grid_cols_for_width(visible_bucket_count)

        # Sample column always; tissue only when R-prefix present
        self._cols_row.addWidget(self._build_column("sample", buckets["samples"]), stretch=1)
        if buckets["tissues"]:
            self._cols_row.addWidget(self._build_column("tissue", buckets["tissues"]), stretch=1)

    def _build_column(self, bucket: str, items: list) -> QWidget:
        meta = _BUCKET_META[bucket]
        col = QWidget()
        col.setObjectName("BucketSection")
        cl = QVBoxLayout(col)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(12)

        # Header
        head = QHBoxLayout()
        head.setSpacing(6)
        htext = QVBoxLayout()
        htext.setSpacing(2)
        strong = QLabel(meta["title"])
        strong.setStyleSheet(f"color:{_C_TEXT}; font-size:15px; font-weight:800;")
        sub = QLabel(f"{len(items)} 个待打印标签 · 模板选择会自动保存")
        sub.setStyleSheet(f"color:{_C_MUTED_DIM}; font-size:11px;")
        htext.addWidget(strong)
        htext.addWidget(sub)
        head.addLayout(htext)
        head.addStretch()

        btn_new = QPushButton("自由设计")
        btn_import = QPushButton("导入 JSON")
        btn_manage = QPushButton("模板管理")
        for b, fn in ((btn_new, self._new_custom), (btn_import, self._import_json),
                      (btn_manage, self._manage_menu)):
            b.setObjectName("HeadAction")
            b.clicked.connect(lambda _=False, bk=bucket, f=fn: f(bk))
            head.addWidget(b)
        self._header_actions[bucket] = {
            "new": btn_new,
            "import": btn_import,
            "manage": btn_manage,
        }
        cl.addLayout(head)

        # Card grid
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        cols = max(1, self._grid_cols)
        for i in range(cols):
            grid.setColumnStretch(i, 1)
        cl.addWidget(grid_host)
        cl.addStretch()

        preview_data = _preview_label_data(self._specimens, items)
        dims = resolve_dims(self._libs[bucket])
        cur_key = self._libs[bucket].selected_key()

        self._cards[bucket] = []
        slot = 0
        is_tissue = bucket == "tissue"

        # Built-in cards (filtered by flavor; tissueCustom excluded — web parity)
        for key, tmpl in BUILTIN_TEMPLATES.items():
            if (tmpl.get("flavor") == "tissue") != is_tissue:
                continue
            if key == "tissueCustom":
                continue
            card = self._make_card(
                bucket, key, "builtin", tmpl, _template_display_name(key, tmpl),
                preview_data, dims, selected=(cur_key == key),
            )
            grid.addWidget(
                card["frame"],
                slot // cols,
                slot % cols,
                Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
            )
            self._cards[bucket].append(card)
            slot += 1

        # Custom library cards
        for rec in self._libs[bucket].records():
            rkey = key_from_id(rec["id"])
            tmpl = normalize_template(rec.get("template") or {})
            card = self._make_card(
                bucket, rkey, "custom", tmpl, rec.get("name", "自定义"),
                preview_data, dims, selected=(cur_key == rkey), rec_id=rec["id"],
            )
            grid.addWidget(
                card["frame"],
                slot // cols,
                slot % cols,
                Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
            )
            self._cards[bucket].append(card)
            slot += 1

        # 「自由设计」add-card — free-create entry (web oracle app.js:14961)
        add_card = self._make_add_card(bucket)
        grid.addWidget(
            add_card,
            slot // cols,
            slot % cols,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
        )
        slot += 1

        return col

    def _grid_cols_for_width(self, visible_bucket_count: int | None = None) -> int:
        bucket_count = visible_bucket_count
        if bucket_count is None:
            bucket_count = max(1, len([b for b in self._cards if self._cards.get(b)]))
        available = max(320, self.width() - 44 - 14 * max(0, bucket_count - 1))
        per_bucket = available / max(1, bucket_count)
        if per_bucket >= 1080:
            return 3
        if per_bucket >= 720:
            return 2
        return 1

    def _make_card(
        self, bucket, key, kind, tmpl, name, preview_data, dims,
        selected=False, rec_id=None,
    ) -> dict:
        from app.utils.label_render import render_label_preview

        frame = QFrame()
        frame.setObjectName("TmplCard")
        frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        frame.setProperty("selected", selected)
        frame.setStyleSheet(_card_css(selected))
        frame.setCursor(Qt.CursorShape.PointingHandCursor)
        frame.setMinimumHeight(248)
        frame.setMaximumWidth(_CARD_MAX_W)
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        v = QVBoxLayout(frame)
        v.setContentsMargins(12, 11, 12, 12)
        v.setSpacing(9)

        # Header row: identity + action
        hr = QHBoxLayout()
        hr.setSpacing(8)
        text_box = QVBoxLayout()
        text_box.setSpacing(3)
        nm = QLabel(name)
        nm.setObjectName("CardTitle")
        nm.setStyleSheet(
            f"background-color: transparent; color:{_C_TEXT}; "
            "font-size:13px; font-weight:700;"
        )
        nm.setWordWrap(True)
        desc = QLabel(str(tmpl.get("desc") or "自定义标签排版"))
        desc.setObjectName("CardDesc")
        desc.setStyleSheet(
            f"background-color: transparent; color:{_C_MUTED_DIM}; font-size:11px;"
        )
        desc.setWordWrap(True)
        text_box.addWidget(nm)
        text_box.addWidget(desc)
        hr.addLayout(text_box, stretch=1)
        action = QPushButton("管理" if kind == "custom" else "编辑")
        action.setObjectName("CardAction")
        action.setToolTip(
            "重命名、复制、恢复、删除" if kind == "custom" else "复制为可编辑（不污染预设）"
        )
        if kind == "custom":
            action.clicked.connect(lambda _=False, bk=bucket, rid=rec_id: self._manage_custom(bk, rid))
        else:
            action.clicked.connect(lambda _=False, bk=bucket, k=key: self._edit_builtin(bk, k))
        hr.addWidget(action)
        v.addLayout(hr)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(6)
        badge = QLabel("自定义" if kind == "custom" else "内置模板")
        badge.setObjectName("CardBadge")
        badge.setProperty("custom", kind == "custom")
        meta_row.addWidget(badge)
        size = tmpl.get("minSize") or {}
        size_badge = QLabel(f"{size.get('w', dims.get('w'))}×{size.get('h', dims.get('h'))} mm")
        size_badge.setObjectName("CardBadge")
        meta_row.addWidget(size_badge)
        if selected:
            selected_badge = QLabel("✓ 当前使用")
            selected_badge.setObjectName("SelectedBadge")
            meta_row.addWidget(selected_badge)
        else:
            choose_btn = QPushButton("选用")
            choose_btn.setObjectName("CardChooseBtn")
            choose_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            choose_btn.clicked.connect(
                lambda _=False, bk=bucket, k=key: self._choose(bk, k)
            )
            meta_row.addWidget(choose_btn)
        meta_row.addStretch()
        v.addLayout(meta_row)

        # Live preview
        prev = QLabel()
        prev.setObjectName("CardPreview")
        prev.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prev.setFixedHeight(_CARD_H)
        prev.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        pm = render_label_preview(tmpl, dims, preview_data, _CARD_W, _CARD_H)
        if pm is not None and not pm.isNull():
            prev.setPixmap(pm)
        else:
            prev.setText("—")
        v.addWidget(prev)

        frame.mousePressEvent = lambda _e, bk=bucket, k=key: self._choose(bk, k)  # type: ignore[assignment]
        return {"key": key, "kind": kind, "frame": frame, "preview": prev, "rec_id": rec_id}

    def _make_add_card(self, bucket: str) -> QFrame:
        """Dashed「自由设计」card — free-create entry (web app.js:14961)."""
        frame = QFrame()
        frame.setObjectName("TmplAddCard")
        frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        frame.setCursor(Qt.CursorShape.PointingHandCursor)
        frame.setMinimumHeight(248)
        frame.setMaximumWidth(_CARD_MAX_W)
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        v = QVBoxLayout(frame)
        v.setContentsMargins(12, 11, 12, 12)
        v.setSpacing(6)
        v.addStretch()
        plus = QLabel("＋")
        plus.setObjectName("AddCardPlus")
        plus.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("自由设计")
        title.setObjectName("AddCardTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub = QLabel("像 PPT 一样放置文字、字段、图形和 QR")
        sub.setObjectName("AddCardDesc")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setWordWrap(True)
        v.addWidget(plus)
        v.addWidget(title)
        v.addWidget(sub)
        v.addStretch()
        frame.mousePressEvent = lambda _e, bk=bucket: self._new_custom(bk)  # type: ignore[assignment]
        return frame

    # ── Handlers ────────────────────────────────────────────────────────────────

    def _choose(self, bucket: str, key: str) -> None:
        self._libs[bucket].set_selected_key(key)
        self._rebuild()
        self.config_changed.emit()

    def _edit_builtin(self, bucket: str, key: str) -> None:
        tmpl = BUILTIN_TEMPLATES.get(key)
        if not tmpl:
            return
        lib = self._libs[bucket]
        rec = lib.clone_from_builtin(tmpl, tmpl.get("name", key))
        lib.set_selected_key(key_from_id(rec["id"]))
        self._open_designer(bucket, rec["id"])

    def _new_custom(self, bucket: str) -> None:
        """Create a domain-specific custom template → open designer.

        Web starts from a cloned builtin. We keep the same custom-library
        contract but use a lab starter that already contains the fields this
        specimen workflow usually needs.
        """
        lib = self._libs[bucket]
        tmpl = copy.deepcopy(_starter_custom_template(bucket))
        rec = lib.upsert({
            "name": tmpl.get("name") or "自定义模板",
            "source": "lab-starter",
            "template": tmpl,
        })
        lib.set_selected_key(key_from_id(rec["id"]))
        self._open_designer(bucket, rec["id"])

    def _manage_custom(self, bucket: str, rec_id: Optional[str]) -> None:
        if rec_id:
            self._open_designer(bucket, rec_id)

    def _open_designer(self, bucket: str, rec_id: str) -> None:
        from app.widgets.label_designer_dialog import LabelDesignerDialog

        lib = self._libs[bucket]
        rec = lib.get(rec_id)
        if not rec:
            self._rebuild()
            self.config_changed.emit()
            return
        tmpl = normalize_template(rec.get("template") or {})
        dims = resolve_dims(lib)
        data = _preview_label_data(self._specimens, [])
        bucket_name = _BUCKET_META[bucket]["title"]
        dlg = LabelDesignerDialog(
            tmpl, dims, data, library=lib,
            title=f"标签设计器 — {bucket_name}·{rec.get('name', '')}", parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_key() is None:
            new_tmpl = dlg.edited_template()
            lib.upsert({"id": rec_id, "name": rec.get("name") or "自定义", "template": new_tmpl})
            lib.set_selected_key(key_from_id(rec_id))
            # persist a dimension edited inside the designer as the custom size
            new_dims = dlg.edited_dims()
            if (round(float(new_dims.get("w", 0)), 2) != round(float(dims.get("w", 0)), 2)
                    or round(float(new_dims.get("h", 0)), 2) != round(float(dims.get("h", 0)), 2)):
                lib.set_custom_dims(float(new_dims["w"]), float(new_dims["h"]))
                lib.set_selected_size_key("custom")
        self._rebuild()
        self.config_changed.emit()

    def _import_json(self, bucket: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入模板 JSON", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                tmpl = json.load(fh)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "导入失败", f"无法解析 JSON：\n{exc}")
            return
        lib = self._libs[bucket]
        rec = lib.upsert({"name": tmpl.get("name") or "导入模板", "template": tmpl})
        lib.set_selected_key(key_from_id(rec["id"]))
        self._rebuild()
        self.config_changed.emit()

    def _build_manage_menu(self, bucket: str) -> QMenu:
        """Build the 模板管理 menu acting on the *currently selected* template.

        Mirror web ``renderLabelTemplateContextMenu`` (app.js:15767): a single
        rich menu rather than a per-record submenu list, so it is never empty.
        """
        lib = self._libs[bucket]
        key = lib.selected_key()
        rec_id = id_from_key(key) if is_library_key(key) else None
        rec = lib.get(rec_id) if rec_id else None
        is_custom = rec is not None

        menu = QMenu(self)
        menu.addAction("设为当前模板", lambda: self._choose(bucket, key))

        def _copy_edit() -> None:
            if is_custom and rec_id:
                dup = lib.duplicate(rec_id)
                if dup:
                    lib.set_selected_key(key_from_id(dup["id"]))
                    self._open_designer(bucket, dup["id"])
            else:
                self._edit_builtin(bucket, key)

        menu.addAction("复制为自定义并编辑", _copy_edit)
        menu.addSeparator()

        a_rename = menu.addAction("重命名自定义", lambda: self._rename(bucket, rec_id))
        a_rename.setEnabled(is_custom)
        a_dup = menu.addAction("复制一份", lambda: self._duplicate(bucket, rec_id))
        a_dup.setEnabled(is_custom)
        a_restore = menu.addAction("恢复上一版", lambda: self._restore(bucket, rec_id))
        a_restore.setEnabled(is_custom and lib.latest_backup(rec_id) is not None)
        menu.addSeparator()

        a_exp = menu.addAction(
            "导出当前模板 JSON", lambda: self._export_json(bucket, rec_id)
        )
        a_exp.setEnabled(is_custom)
        menu.addAction("导出本桶模板库 JSON", lambda: self._export_json(bucket, None))
        menu.addAction("导入模板 JSON", lambda: self._import_json(bucket))
        menu.addSeparator()

        a_del = menu.addAction("删除自定义", lambda: self._delete_confirm(bucket, rec_id))
        a_del.setEnabled(is_custom)
        return menu

    def _manage_menu(self, bucket: str) -> None:
        self._build_manage_menu(bucket).exec(self.cursor().pos())

    def _rename(self, bucket: str, rec_id: Optional[str]) -> None:
        if not rec_id:
            return
        lib = self._libs[bucket]
        rec = lib.get(rec_id)
        cur = rec.get("name", "自定义") if rec else "自定义"
        name, ok = QInputDialog.getText(self, "重命名模板", "模板名称：", text=cur)
        if not ok or not name.strip():
            return
        lib.rename(rec_id, name.strip())
        self._rebuild()
        self.config_changed.emit()

    def _restore(self, bucket: str, rec_id: Optional[str]) -> None:
        if not rec_id:
            return
        if self._libs[bucket].restore_backup(rec_id):
            self._rebuild()
            self.config_changed.emit()

    def _delete_confirm(self, bucket: str, rec_id: Optional[str]) -> None:
        if not rec_id:
            return
        rec = self._libs[bucket].get(rec_id)
        name = rec.get("name", "自定义") if rec else "自定义"
        if ui.question(
            self, "删除模板", f"确认删除自定义模板「{name}」？删除前会自动备份。"
        ) != QMessageBox.StandardButton.Yes:
            return
        self._delete(bucket, rec_id)

    def _export_json(self, bucket: str, rec_id: Optional[str]) -> None:
        """Export one template or the whole bucket library to a JSON file.

        Mirror web ``exportLabelTemplateJson`` (app.js:15613).
        """
        lib = self._libs[bucket]
        if rec_id:
            rec = lib.get(rec_id)
            if not rec:
                ui.warn(self, "导出失败", "没有可导出的模板")
                return
            payload = {
                "type": "label-template", "version": 1,
                "bucket": bucket, "template": rec,
            }
            default = f"label-template-{bucket}.json"
        else:
            payload = {
                "type": "label-template-library", "version": 1, "bucket": bucket,
                "library": {"version": 1, "templates": lib.records()},
            }
            default = f"label-template-library-{bucket}.json"
        path = ui.get_save_file_name(self, "导出模板 JSON", default, "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            ui.warn(self, "导出失败", f"无法写入：\n{exc}")
            return

    def _duplicate(self, bucket: str, rec_id: str) -> None:
        self._libs[bucket].duplicate(rec_id)
        self._rebuild()
        self.config_changed.emit()

    def _delete(self, bucket: str, rec_id: str) -> None:
        lib = self._libs[bucket]
        lib.delete(rec_id)
        if is_library_key(lib.selected_key()) and id_from_key(lib.selected_key()) == rec_id:
            from app.services.label_service import DEFAULT_TEMPLATE_KEY
            lib.set_selected_key(DEFAULT_TEMPLATE_KEY[bucket])
        self._rebuild()
        self.config_changed.emit()


def _first_js(specimens: list[dict]) -> dict:
    """Best-effort first specimen as a label-data source for previews."""
    from app.services.label_service import _specimen_to_js_dict
    return _specimen_to_js_dict(specimens[0]) if specimens else {}


def _preview_label_data(specimens: list[dict], items: list) -> dict:
    """Use real selected data when available; otherwise show a concrete demo label."""
    if items:
        data = items[0].get("data") if isinstance(items[0], dict) else None
        if data:
            return data
    if specimens:
        return specimen_to_label_data(_first_js(specimens))
    return specimen_to_label_data(_DEMO_SPECIMEN)


def _template_display_name(key: str, tmpl: dict) -> str:
    labels = {
        "standard": "样品瓶 · 标准信息",
        "compact": "小标签 · 编号 + QR",
        "detailed": "样品瓶 · 详细采集信息",
        "tissueCompact": "RNAlater · 组织管",
        "tissueMini": "RNAlater · 极小管",
    }
    return labels.get(key, tmpl.get("name", key))


def _card_css(selected: bool) -> str:
    border = _C_ACCENT if selected else _C_BORDER
    width = "2px" if selected else "1px"
    bg = _C_SEL_BG if selected else _C_CARD_BG
    return (
        "QFrame#TmplCard {"
        f"background-color: {bg};"
        f"border: {width} solid {border};"
        "border-radius: 8px;"
        "}"
    )
