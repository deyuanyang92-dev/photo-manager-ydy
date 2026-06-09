"""label_step4_output.py — Step 4「输出」section.

Mirrors web ``renderLabelStep4`` (app.js:17232-17272): a summary line of the
counts, two print buttons (样品瓶 / RNAlater 组织管, disabled when their bucket is
empty), an optional warning list, and a hint.

Signals
-------
print_requested(str)  — user asked to print a bucket ("sample" | "tissue").
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


# ── Theme colours — resolved from the LIVE active theme (CHROME ONLY) ─────────
_C_BG = "#08161b"
_C_TEXT = "#eef3ef"
_C_TEXT_SOFT = "#cfe0db"
_C_MUTED_DIM = "#5f7d7a"
_C_ACCENT = "#29b9ab"
_C_ACCENT_HI = "#31d4c4"
_C_ACCENT_BTN_FG = "#08161b"
_C_WARN = "#f1bd57"
_C_DISABLED_BG = "#1d3a44"


def _refresh_palette() -> None:
    """Rebind the module `_C_*` chrome colours to the current theme tokens."""
    global _C_BG, _C_TEXT, _C_TEXT_SOFT, _C_MUTED_DIM, _C_ACCENT
    global _C_ACCENT_HI, _C_ACCENT_BTN_FG, _C_WARN, _C_DISABLED_BG
    from app.config.theme import TOKENS
    g = TOKENS.get
    _C_BG = g("bg", _C_BG)
    _C_TEXT = g("text", _C_TEXT)
    _C_TEXT_SOFT = g("text", _C_TEXT_SOFT)
    _C_MUTED_DIM = g("muted_dim", _C_MUTED_DIM)
    _C_ACCENT = g("accent", _C_ACCENT)
    _C_ACCENT_HI = g("accent_hover", _C_ACCENT_HI)
    # On-accent button label: use the page bg as a contrasting foreground.
    _C_ACCENT_BTN_FG = g("bg", _C_ACCENT_BTN_FG)
    _C_WARN = g("warn", _C_WARN)
    _C_DISABLED_BG = g("panel_2", _C_DISABLED_BG)


def _css_print() -> str:
    return f"""
QPushButton#PrintBtn {{
    background: {_C_ACCENT};
    border: none; border-radius: 5px; color: {_C_ACCENT_BTN_FG}; font-weight: bold;
    padding: 7px 18px; font-size: 13px;
}}
QPushButton#PrintBtn:hover {{ background: {_C_ACCENT_HI}; }}
QPushButton#PrintBtn:disabled {{ background: {_C_DISABLED_BG}; color: {_C_MUTED_DIM}; }}
QPushButton#PrintBtnTissue {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #4a90d9,stop:1 #3a7bc0);
    border: none; border-radius: 5px; color: #06121b; font-weight: bold;
    padding: 7px 18px; font-size: 13px;
}}
QPushButton#PrintBtnTissue:hover {{ background: #5a9fe0; }}
QPushButton#PrintBtnTissue:disabled {{ background: {_C_DISABLED_BG}; color: {_C_MUTED_DIM}; }}
"""


class LabelStep4Output(QWidget):
    """Step 4 — print summary + per-bucket print buttons + warnings."""

    print_requested = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        _refresh_palette()
        self.setStyleSheet(f"background:{_C_BG}; color:{_C_TEXT};" + _css_print())
        self._setup_ui()
        self.set_counts(0, 0, 1)

    # ── UI ─────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 16)
        root.setSpacing(10)

        title = QLabel("输出")
        title.setStyleSheet(f"color:{_C_ACCENT}; font-size:15px; font-weight:bold;")
        root.addWidget(title)

        self._summary = QLabel("")
        self._summary.setStyleSheet(f"color:{_C_TEXT_SOFT}; font-size:12px;")
        root.addWidget(self._summary)

        brow = QHBoxLayout()
        brow.setSpacing(10)
        self._btn_sample = QPushButton("打印样品瓶标签")
        self._btn_sample.setObjectName("PrintBtn")
        self._btn_sample.clicked.connect(lambda: self.print_requested.emit("sample"))
        self._btn_tissue = QPushButton("打印 RNAlater 组织管标签")
        self._btn_tissue.setObjectName("PrintBtnTissue")
        self._btn_tissue.clicked.connect(lambda: self.print_requested.emit("tissue"))
        brow.addWidget(self._btn_sample)
        brow.addWidget(self._btn_tissue)
        brow.addStretch()
        root.addLayout(brow)

        self._warn = QLabel("")
        self._warn.setStyleSheet(f"color:{_C_WARN}; font-size:11px;")
        self._warn.setWordWrap(True)
        root.addWidget(self._warn)
        self._warn.hide()

        hint = QLabel("提示：两个按钮分别触发打印对话框；可在对话框里挑不同打印机 / 纸盘 / 纸张。")
        hint.setStyleSheet(f"color:{_C_MUTED_DIM}; font-size:11px;")
        hint.setWordWrap(True)
        root.addWidget(hint)

    # ── Public API ───────────────────────────────────────────────────────────

    def set_counts(self, sample_n: int, tissue_n: int, copies: int) -> None:
        total = (sample_n + tissue_n) * copies
        self._summary.setText(
            f"样品瓶 {sample_n} · RNAlater 组织管 {tissue_n} · "
            f"每种 {copies} 份 → 总 {total} 张"
        )
        self._btn_sample.setText(f"打印样品瓶标签 ({sample_n})")
        self._btn_tissue.setText(f"打印 RNAlater 组织管标签 ({tissue_n})")
        self._btn_sample.setEnabled(sample_n > 0)
        self._btn_tissue.setEnabled(tissue_n > 0)

    def set_warnings(self, warnings: list[str]) -> None:
        if warnings:
            self._warn.setText("\n".join(warnings))
            self._warn.show()
        else:
            self._warn.setText("")
            self._warn.hide()
