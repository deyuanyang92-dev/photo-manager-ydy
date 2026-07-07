"""Shared styling support for the WoRMS view modules."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QFrame, QLabel

# ── Colour palette — resolved from the LIVE active theme ─────────────────────
# Previously these were hardcoded deep-teal constants, which force-painted the
# whole WoRMS page (search box, 批量验证任务 panel, detail cards) dark
# regardless of the chosen theme → under a light theme they rendered
# dark/broken.  Now they are refreshed from the active theme tokens by
# _refresh_palette(), called at the top of WormsView._setup_ui() and at the
# start of __init__ of every sub-widget / dialog that reads `_C_*` (they may be
# built independently of the main view).  The dark hex below are fallbacks only.
_C_BG       = "#08161b"
_C_PANEL    = "#0f2127"
_C_INPUT    = "#0b2025"   # inset / list / secondary panel bg
_C_BORDER   = "rgba(145, 182, 181, 0.16)"
_C_ACCENT   = "#29b9ab"
_C_ACCENT_H = "#31d4c4"
_C_TEXT     = "#eef3ef"
_C_MUTED    = "#87a2a1"
_C_DIM      = "#5f7d7a"
_C_SUCCESS  = "#36c98f"
_C_DANGER   = "#e66e63"
_C_WARN     = "#f1bd57"
_C_RUNNING  = "#6699ff"  # running-job status colour (no dedicated token → accent)

# Pre-built rgba tint strings (alpha-blended accent/success/danger/warn/border),
# rebuilt from the live tokens by _refresh_palette() so tints track the theme.
_C_ACCENT_06 = "rgba(41,185,171,0.06)"
_C_ACCENT_08 = "rgba(41,185,171,0.08)"
_C_ACCENT_12 = "rgba(41,185,171,0.12)"
_C_ACCENT_15 = "rgba(41,185,171,0.15)"
_C_ACCENT_20 = "rgba(41,185,171,0.20)"
_C_ACCENT_22 = "rgba(41,185,171,0.22)"
_C_ACCENT_28 = "rgba(41,185,171,0.28)"
_C_ACCENT_30 = "rgba(41,185,171,0.30)"
_C_SUCCESS_15 = "rgba(54,201,143,0.15)"
_C_DANGER_15 = "rgba(230,110,99,0.15)"
_C_WARN_25 = "rgba(241,189,87,0.25)"
_C_BORDER_10 = "rgba(145,182,181,0.10)"
_C_BORDER_20 = "rgba(145,182,181,0.20)"
_C_BORDER_25 = "rgba(145,182,181,0.25)"

_MONO      = '"JetBrains Mono", "Cascadia Code", "SF Mono", Consolas, "DejaVu Sans Mono", monospace'
_SERIF     = '"Noto Serif SC", "Source Han Serif SC", "Songti SC", SimSun, Georgia, serif'
_SANS      = '"Noto Sans SC", "Source Han Sans SC", "Microsoft YaHei", "Segoe UI", sans-serif'


def _rgb_tuple(color: str) -> tuple[int, int, int]:
    """Parse a '#rrggbb' or 'rgb(a)(...)' token into an (r, g, b) tuple."""
    s = (color or "").strip()
    if s.startswith("#") and len(s) >= 7:
        try:
            return (int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16))
        except ValueError:
            return (41, 185, 171)
    if s.lower().startswith("rgb"):
        try:
            nums = s[s.index("(") + 1: s.index(")")].split(",")
            return (int(float(nums[0])), int(float(nums[1])), int(float(nums[2])))
        except (ValueError, IndexError):
            return (145, 182, 181)
    return (41, 185, 171)


def _rgba_tint(color: str, alpha: float) -> str:
    r, g, b = _rgb_tuple(color)
    return f"rgba({r},{g},{b},{alpha})"


def _refresh_palette() -> None:
    """Rebind the module `_C_*` colours to the current theme tokens."""
    global _C_BG, _C_PANEL, _C_INPUT, _C_BORDER, _C_ACCENT, _C_ACCENT_H
    global _C_TEXT, _C_MUTED, _C_DIM, _C_SUCCESS, _C_DANGER, _C_WARN, _C_RUNNING
    global _C_ACCENT_06, _C_ACCENT_08, _C_ACCENT_12, _C_ACCENT_15, _C_ACCENT_20
    global _C_ACCENT_22, _C_ACCENT_28, _C_ACCENT_30
    global _C_SUCCESS_15, _C_DANGER_15, _C_WARN_25
    global _C_BORDER_10, _C_BORDER_20, _C_BORDER_25
    from app.config.theme import TOKENS
    g = TOKENS.get
    _C_BG       = g("bg", _C_BG)
    _C_PANEL    = g("panel", _C_PANEL)
    _C_INPUT    = g("input_bg", _C_INPUT)
    _C_BORDER   = g("border", _C_BORDER)
    _C_ACCENT   = g("accent", _C_ACCENT)
    _C_ACCENT_H = g("accent_hover", _C_ACCENT_H)
    _C_TEXT     = g("text", _C_TEXT)
    _C_MUTED    = g("muted", _C_MUTED)
    _C_DIM      = g("muted_dim", _C_DIM)
    _C_SUCCESS  = g("success", _C_SUCCESS)
    _C_DANGER   = g("danger", _C_DANGER)
    _C_WARN     = g("warn", _C_WARN)
    _C_RUNNING  = g("accent", _C_RUNNING)
    # Alpha tints derived from the live base colours
    _C_ACCENT_06 = _rgba_tint(_C_ACCENT, 0.06)
    _C_ACCENT_08 = _rgba_tint(_C_ACCENT, 0.08)
    _C_ACCENT_12 = _rgba_tint(_C_ACCENT, 0.12)
    _C_ACCENT_15 = _rgba_tint(_C_ACCENT, 0.15)
    _C_ACCENT_20 = _rgba_tint(_C_ACCENT, 0.20)
    _C_ACCENT_22 = _rgba_tint(_C_ACCENT, 0.22)
    _C_ACCENT_28 = _rgba_tint(_C_ACCENT, 0.28)
    _C_ACCENT_30 = _rgba_tint(_C_ACCENT, 0.30)
    _C_SUCCESS_15 = _rgba_tint(_C_SUCCESS, 0.15)
    _C_DANGER_15 = _rgba_tint(_C_DANGER, 0.15)
    _C_WARN_25 = _rgba_tint(_C_WARN, 0.25)
    _C_BORDER_10 = _rgba_tint(_C_BORDER, 0.10)
    _C_BORDER_20 = _rgba_tint(_C_BORDER, 0.20)
    _C_BORDER_25 = _rgba_tint(_C_BORDER, 0.25)


_FALLBACK_DATA_DIR = Path.home() / ".photo_workbench" / "data"


# ── Tiny style helpers ─────────────────────────────────────────────────────────

def _label(text: str, *,
           color: str = "",
           size: int = 13,
           bold: bool = False,
           font: str = _SANS,
           wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    if not color:
        color = _C_TEXT
    weight = "700" if bold else "500"
    lbl.setStyleSheet(
        f"color:{color}; font-size:{size}px; font-weight:{weight}; font-family:{font};"
        " background:transparent;"
    )
    if wrap:
        lbl.setWordWrap(True)
    return lbl


def _badge(text: str, kind: str) -> QLabel:
    """Render a worms-badge pill.  kind = 'rank' | 'accepted' | 'unaccepted'."""
    lbl = QLabel(text)
    if kind == "rank":
        lbl.setStyleSheet(
            f"font-size:10px; padding:1px 6px; border-radius:4px; font-weight:600;"
            f" background:{_C_ACCENT_15}; color:{_C_ACCENT};"
        )
    elif kind == "accepted":
        lbl.setStyleSheet(
            f"font-size:10px; padding:1px 6px; border-radius:4px; font-weight:600;"
            f" background:{_C_SUCCESS_15}; color:{_C_SUCCESS};"
        )
    else:  # unaccepted
        lbl.setStyleSheet(
            f"font-size:10px; padding:1px 6px; border-radius:4px; font-weight:600;"
            f" background:{_C_DANGER_15}; color:{_C_DANGER};"
        )
    return lbl


def _divider() -> QFrame:
    """Thin horizontal rule."""
    div = QFrame()
    div.setFrameShape(QFrame.Shape.HLine)
    div.setStyleSheet(f"background:{_C_BORDER_10}; max-height:1px; border:none;")
    return div
