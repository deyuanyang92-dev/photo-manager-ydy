"""theme.py — Publication-grade design token system.

Tokens are derived from the web prototype's confirmed :root CSS variables
(deep-teal scientific-tool palette) and refined into a layered Qt design
system: precise elevation, considered whitespace, subtle gradients and
inner strokes, focus/hover states, and a 5-state attribution badge palette.

Public API (kept stable — referenced elsewhere):
  TOKENS                 design token dict
  build_qss()            -> full QSS string
  build_theme_qss_file() -> writes resources/theme.qss, returns its Path
  load_fonts(app)        -> registers bundled fonts (graceful fallback)
  FONT_SANS / FONT_SERIF / FONT_MONO  resolved family stacks
"""

from __future__ import annotations

from pathlib import Path

# ── Design tokens (mirror CSS :root variables, refined) ────────────────────

TOKENS: dict[str, str] = {
    # Backgrounds
    "bg": "#08161b",
    "bg_raised": "#0a1d23",
    "panel": "#10242a",
    "panel_2": "#0b2025",
    "panel_inset": "#0c2127",
    "modal_surface": "#10282e",
    "modal_surface_raised": "#143139",

    # Text
    "text": "#edf2ee",
    "text_soft": "#cfe0db",
    "muted": "#87a2a1",
    "muted_dim": "#5f7d7a",

    # Accent (teal/cyan)
    "accent": "#29b9ab",
    "accent_hover": "#31d4c4",
    "accent_pressed": "#1f9288",
    "accent_soft": "rgba(41,185,171,0.14)",
    "accent_glow": "rgba(41,185,171,0.30)",

    # Semantic
    "warn": "#f1bd57",
    "warn_soft": "rgba(241,189,87,0.13)",
    "success": "#36c98f",
    "success_soft": "rgba(54,201,143,0.14)",
    "danger": "#e66e63",
    "danger_soft": "rgba(230,110,99,0.13)",
    "info": "#4a90d9",

    # Borders
    "border": "rgba(145, 182, 181, 0.13)",
    "border_medium": "rgba(145, 182, 181, 0.22)",
    "border_strong": "rgba(152, 205, 198, 0.38)",

    # Nav / topbar
    "nav_bg": "#091e24",
    "nav_selected_bg": "#12313a",
    "nav_selected_border": "#29b9ab",
    "topbar_bg": "#081a20",
    "topbar_border": "rgba(145, 182, 181, 0.10)",
    "contextbar_bg": "#0a1f25",
    "nav_segment_text": "#9fbab8",
    "nav_segment_hover_bg": "rgba(41,185,171,0.08)",

    # Status bar
    "statusbar_bg": "#091e24",

    # Input
    "input_bg": "#12272d",
    "input_bg_auto": "#0d1e23",
    "input_border": "#223d43",
    "input_focus_border": "#29b9ab",

    # Scrollbar
    "scrollbar_bg": "transparent",
    "scrollbar_handle": "#1e3d47",
    "scrollbar_handle_hover": "#29b9ab",

    # Typography scale
    "font_xs": "10px",
    "font_sm": "11px",
    "font_body": "13px",
    "font_md": "14px",
    "font_lg": "16px",
    "font_title": "22px",

    # Radius / spacing
    "radius": "10px",
    "radius_lg": "14px",
    "radius_sm": "6px",
}

# ── Font stacks (web-parity fallback when bundled fonts absent) ────────────

_SANS_FONTS = (
    "Noto Sans SC", "Source Han Sans SC", "Noto Sans CJK SC",
    "Microsoft YaHei", "PingFang SC", "Segoe UI", "sans-serif",
)
_SERIF_FONTS = (
    "Noto Serif SC", "Source Han Serif SC", "Songti SC",
    "SimSun", "Georgia", "serif",
)
_MONO_FONTS = (
    "JetBrains Mono", "Cascadia Code", "SF Mono", "Consolas",
    "DejaVu Sans Mono", "monospace",
)

# Resolved at import; load_fonts() may prepend bundled families.
FONT_SANS = _SANS_FONTS
FONT_SERIF = _SERIF_FONTS
FONT_MONO = _MONO_FONTS


def _font_family(fonts: tuple[str, ...]) -> str:
    return ", ".join(f'"{f}"' if " " in f else f for f in fonts)


def load_fonts(app) -> dict[str, bool]:
    """Register any TTF/OTF files in resources/fonts/ with the font DB.

    Returns a small map noting which logical families were bundled.  Always
    safe to call — when no font files are present, the web-parity fallback
    stacks (Noto Sans/Serif SC, JetBrains Mono, system) are used instead.
    """
    result = {"sans": False, "serif": False, "mono": False}
    try:
        from PyQt6.QtGui import QFontDatabase
    except Exception:
        return result

    fonts_dir = Path(__file__).parent.parent.parent / "resources" / "fonts"
    if not fonts_dir.is_dir():
        return result

    for f in sorted(fonts_dir.glob("*")):
        if f.suffix.lower() not in (".ttf", ".otf", ".ttc"):
            continue
        try:
            fid = QFontDatabase.addApplicationFont(str(f))
        except Exception:
            continue
        if fid < 0:
            continue
        fams = QFontDatabase.applicationFontFamilies(fid)
        low = f.name.lower()
        if "mono" in low:
            result["mono"] = True
        elif "serif" in low or "song" in low:
            result["serif"] = True
        else:
            result["sans"] = bool(fams) or result["sans"]
    return result


def build_qss() -> str:
    """Generate the full publication-grade Qt stylesheet from TOKENS."""
    t = TOKENS
    sans = _font_family(FONT_SANS)
    serif = _font_family(FONT_SERIF)
    mono = _font_family(FONT_MONO)

    return f"""
/* ══════════════════════════════════════════════════════════════════
   Specimen Imaging Workbench — Deep-Teal Publication Theme
   Auto-generated by app/config/theme.py — do not hand-edit
   ══════════════════════════════════════════════════════════════════ */

/* ── Global ─────────────────────────────────────────────────────── */
QWidget {{
    background-color: {t["bg"]};
    color: {t["text"]};
    font-family: {sans};
    font-size: {t["font_body"]};
    selection-background-color: {t["accent"]};
    selection-color: {t["bg"]};
}}
QMainWindow, QWidget#AppShell {{ background-color: {t["bg"]}; }}
QToolTip {{
    background-color: {t["modal_surface_raised"]};
    color: {t["text"]};
    border: 1px solid {t["border_medium"]};
    border-radius: {t["radius_sm"]};
    padding: 5px 9px;
    font-size: {t["font_sm"]};
}}

/* ── Top bar (brand + segmented nav + global actions) ───────────── */
QFrame#TopBar {{
    background-color: {t["topbar_bg"]};
    border: none;
    border-bottom: 1px solid {t["topbar_border"]};
}}
QLabel#BrandWord {{
    font-family: {serif};
    font-size: {t["font_lg"]};
    font-weight: 600;
    color: {t["text"]};
    letter-spacing: 1.2px;
}}

/* Segmented navigation — flat buttons, 2px accent underline when active */
QPushButton#NavSegment {{
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    color: {t["nav_segment_text"]};
    font-size: {t["font_md"]};
    font-weight: 500;
    padding: 9px 16px 7px 16px;
    margin: 0;
    border-radius: 0;
    letter-spacing: 0.5px;
}}
QPushButton#NavSegment:hover {{
    color: {t["text"]};
    background-color: {t["nav_segment_hover_bg"]};
    border-top-left-radius: {t["radius_sm"]};
    border-top-right-radius: {t["radius_sm"]};
}}
QPushButton#NavSegment:checked {{
    color: {t["accent_hover"]};
    border-bottom: 2px solid {t["accent"]};
    font-weight: 600;
}}

/* Icon-only ghost buttons (theme toggle / settings cog) */
QPushButton#IconGhost {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {t["radius_sm"]};
    color: {t["muted"]};
    font-size: {t["font_lg"]};
    padding: 0;
}}
QPushButton#IconGhost:hover {{
    color: {t["accent_hover"]};
    background-color: {t["nav_segment_hover_bg"]};
    border-color: {t["border"]};
}}

/* ── Context bar (project switcher + active badge + quick actions) ─ */
QFrame#ContextBar {{
    background-color: {t["contextbar_bg"]};
    border: none;
    border-bottom: 1px solid {t["topbar_border"]};
}}
QLabel#ContextLabel {{
    color: {t["muted_dim"]};
    font-size: {t["font_sm"]};
    letter-spacing: 0.6px;
}}
QPushButton#ProjectSwitcher {{
    background-color: {t["panel_2"]};
    border: 1px solid {t["border"]};
    border-radius: {t["radius"]};
    padding: 6px 16px;
    color: {t["text"]};
    font-size: {t["font_body"]};
    font-weight: 600;
    text-align: left;
}}
QPushButton#ProjectSwitcher:hover {{
    border-color: {t["accent"]};
    background-color: {t["modal_surface"]};
}}
QLabel#ActiveBadgeOn {{
    background-color: {t["accent"]};
    color: {t["bg"]};
    border-radius: {t["radius_sm"]};
    padding: 4px 12px;
    font-family: {mono};
    font-size: {t["font_sm"]};
    font-weight: 700;
    letter-spacing: 0.5px;
}}
QLabel#ActiveBadgeOff {{
    background-color: {t["panel_inset"]};
    color: {t["muted_dim"]};
    border: 1px solid {t["border"]};
    border-radius: {t["radius_sm"]};
    padding: 3px 12px;
    font-family: {mono};
    font-size: {t["font_sm"]};
    font-weight: 600;
}}

QStackedWidget {{ background-color: {t["bg"]}; border: none; }}

/* ── Workspace header ────────────────────────────────────────────── */
QLabel#WorkspaceTitle {{
    font-family: {serif};
    font-size: {t["font_title"]};
    font-weight: 500;
    color: {t["text"]};
    letter-spacing: 0.4px;
}}
QLabel#TagSea {{
    background-color: {t["accent_soft"]};
    color: {t["accent_hover"]};
    border-radius: {t["radius_sm"]};
    padding: 3px 10px;
    font-size: {t["font_sm"]};
    font-weight: 600;
}}
QLabel#TagWarn {{
    background-color: {t["warn_soft"]};
    color: {t["warn"]};
    border-radius: {t["radius_sm"]};
    padding: 3px 10px;
    font-size: {t["font_sm"]};
    font-weight: 600;
}}
QLabel#TagOk {{
    background-color: {t["success_soft"]};
    color: {t["success"]};
    border-radius: {t["radius_sm"]};
    padding: 3px 10px;
    font-size: {t["font_sm"]};
    font-weight: 600;
}}

/* ── Directory info strip ────────────────────────────────────────── */
QFrame#DirStrip {{
    background-color: {t["panel_2"]};
    border: 1px solid {t["border"]};
    border-radius: {t["radius"]};
}}
QLabel#DirLabel {{ color: {t["muted_dim"]}; font-size: {t["font_sm"]}; }}
QLabel#DirPath {{
    font-family: {mono};
    font-size: {t["font_sm"]};
    color: {t["accent"]};
    background-color: {t["modal_surface"]};
    border-radius: {t["radius_sm"]};
    padding: 2px 7px;
}}

/* ── Panels / cards / sections ───────────────────────────────────── */
QFrame#Panel {{
    background-color: {t["panel"]};
    border: 1px solid {t["border"]};
    border-radius: {t["radius_lg"]};
}}
QFrame#WorkbenchSection {{
    background-color: {t["panel"]};
    border: 1px solid {t["border"]};
    border-radius: {t["radius_lg"]};
}}
QFrame#Card {{
    background-color: {t["panel_2"]};
    border: 1px solid {t["border"]};
    border-radius: {t["radius"]};
}}
QFrame#PanelCard {{
    background-color: {t["panel"]};
    border: 1px solid {t["border"]};
    border-radius: {t["radius_lg"]};
}}
QFrame#BatchIdentBar {{
    background-color: {t["panel_2"]};
    border: 1px solid {t["border_medium"]};
    border-radius: {t["radius"]};
}}
QFrame#Divider {{ background-color: {t["border"]}; max-height: 1px; min-height: 1px; border: none; }}

/* ── Labels ──────────────────────────────────────────────────────── */
QLabel {{ background: transparent; color: {t["text"]}; }}
QLabel#Muted {{ color: {t["muted"]}; }}
QLabel#MutedSmall {{ color: {t["muted_dim"]}; font-size: {t["font_sm"]}; }}
QLabel#Accent {{ color: {t["accent"]}; }}
QLabel#Title {{ font-family: {serif}; font-size: {t["font_title"]}; font-weight: 500; color: {t["text"]}; }}
QLabel#Section {{
    font-size: {t["font_sm"]};
    font-weight: 700;
    color: {t["muted"]};
    letter-spacing: 0.09em;
}}
QLabel#CardTitle {{
    font-size: {t["font_body"]};
    font-weight: 600;
    color: {t["muted"]};
    letter-spacing: 0.06em;
}}
QLabel#Placeholder {{ color: {t["muted"]}; font-size: {t["font_lg"]}; font-style: italic; }}

QLabel#BatchUid {{
    font-family: {mono};
    color: {t["accent_hover"]};
    font-size: {t["font_body"]};
    font-weight: 600;
}}
QLabel#ActivateState {{
    color: {t["muted_dim"]};
    font-size: {t["font_sm"]};
    padding: 2px 8px;
    border-radius: {t["radius_sm"]};
    background-color: {t["panel_inset"]};
}}
QLabel#ActivateStateOn {{
    color: {t["bg"]};
    font-size: {t["font_sm"]};
    font-weight: 600;
    padding: 2px 8px;
    border-radius: {t["radius_sm"]};
    background-color: {t["accent"]};
}}
QLabel#StatValue {{ font-size: {t["font_lg"]}; font-weight: 700; color: {t["text"]}; }}
QLabel#StatLabel {{ font-size: {t["font_sm"]}; color: {t["muted_dim"]}; }}

/* ── Buttons ─────────────────────────────────────────────────────── */
QPushButton {{
    background-color: {t["panel"]};
    color: {t["text_soft"]};
    border: 1px solid {t["border_medium"]};
    border-radius: {t["radius_sm"]};
    padding: 6px 14px;
    font-size: {t["font_body"]};
    font-weight: 500;
}}
QPushButton:hover {{
    background-color: {t["modal_surface"]};
    border-color: {t["border_strong"]};
    color: {t["text"]};
}}
QPushButton:pressed {{ background-color: {t["modal_surface_raised"]}; }}
QPushButton:disabled {{ color: {t["muted_dim"]}; border-color: {t["border"]}; background-color: {t["bg_raised"]}; }}

QPushButton#Primary {{
    background-color: {t["accent"]};
    color: {t["bg"]};
    border: 1px solid {t["accent"]};
    font-weight: 700;
}}
QPushButton#Primary:hover {{ background-color: {t["accent_hover"]}; border-color: {t["accent_hover"]}; }}
QPushButton#Primary:pressed {{ background-color: {t["accent_pressed"]}; }}
QPushButton#Primary:disabled {{ background-color: {t["panel"]}; color: {t["muted_dim"]}; border-color: {t["border"]}; }}

QPushButton#Outline {{
    background-color: transparent;
    color: {t["text_soft"]};
    border: 1px solid {t["border_medium"]};
}}
QPushButton#Outline:hover {{ border-color: {t["accent"]}; color: {t["text"]}; background-color: {t["accent_soft"]}; }}

QPushButton#Ghost {{
    background-color: transparent;
    border: 1px solid transparent;
    color: {t["muted"]};
    padding: 4px 9px;
}}
QPushButton#Ghost:hover {{ color: {t["text"]}; background-color: {t["nav_selected_bg"]}; }}

QPushButton#Danger {{ background-color: transparent; color: {t["danger"]}; border: 1px solid {t["danger"]}; }}
QPushButton#Danger:hover {{ background-color: {t["danger"]}; color: {t["text"]}; }}
QPushButton#Danger:disabled {{ color: {t["muted_dim"]}; border-color: {t["border"]}; }}

QPushButton#Tiny {{ padding: 2px 9px; font-size: {t["font_sm"]}; }}

/* ── Drop target (process selected JPG+TIFF) ─────────────────────── */
QPushButton#DropTarget {{
    background-color: {t["panel_inset"]};
    border: 1px dashed {t["border_strong"]};
    border-radius: {t["radius"]};
    color: {t["accent_hover"]};
    font-weight: 600;
    padding: 9px 14px;
}}
QPushButton#DropTarget:hover {{ border-color: {t["accent"]}; background-color: {t["accent_soft"]}; }}
QPushButton#DropTarget:disabled {{ color: {t["muted_dim"]}; border-color: {t["border"]}; }}

/* ── Inputs ──────────────────────────────────────────────────────── */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {{
    background-color: {t["input_bg"]};
    color: {t["text"]};
    border: 1px solid {t["input_border"]};
    border-radius: {t["radius_sm"]};
    padding: 5px 9px;
    font-size: {t["font_body"]};
    selection-background-color: {t["accent"]};
    selection-color: {t["bg"]};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {{
    border-color: {t["input_focus_border"]};
}}
QLineEdit:disabled {{ color: {t["muted"]}; background-color: {t["bg"]}; }}
QLineEdit#AutoField {{
    background-color: {t["input_bg_auto"]};
    color: {t["muted"]};
    border: 1px dashed {t["input_border"]};
}}
QLineEdit#MonoInput {{ font-family: {mono}; }}

/* ── Preview blocks (naming UID / result-id) ─────────────────────── */
QLabel#PreviewBlock {{
    font-family: {mono};
    font-size: {t["font_body"]};
    color: {t["accent_hover"]};
    background-color: {t["modal_surface"]};
    border: 1px dashed {t["border_strong"]};
    border-radius: {t["radius"]};
    padding: 9px 11px;
}}
QLabel#PreviewEmpty {{
    font-family: {mono};
    font-size: {t["font_body"]};
    color: {t["muted_dim"]};
    background-color: {t["panel_inset"]};
    border: 1px dashed {t["border"]};
    border-radius: {t["radius"]};
    padding: 9px 11px;
}}
QLabel#RnaWarning {{
    color: {t["warn"]};
    background-color: {t["warn_soft"]};
    border: 1px solid rgba(241,189,87,0.34);
    border-radius: {t["radius_sm"]};
    padding: 6px 10px;
    font-size: {t["font_sm"]};
    font-weight: 600;
}}
QLabel#UnattributedWarning {{
    color: {t["warn"]};
    background-color: {t["warn_soft"]};
    border: 1px solid rgba(241,189,87,0.30);
    border-radius: {t["radius_sm"]};
    padding: 5px 10px;
    font-size: {t["font_sm"]};
    font-weight: 600;
}}
QLabel#TargetName {{
    font-family: {mono};
    color: {t["success"]};
    font-size: {t["font_body"]};
    font-weight: 600;
}}

/* ── ComboBox ────────────────────────────────────────────────────── */
QComboBox {{
    background-color: {t["input_bg"]};
    color: {t["text"]};
    border: 1px solid {t["input_border"]};
    border-radius: {t["radius_sm"]};
    padding: 5px 10px;
    font-size: {t["font_body"]};
}}
QComboBox:focus {{ border-color: {t["input_focus_border"]}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background-color: {t["panel"]};
    color: {t["text"]};
    border: 1px solid {t["border_medium"]};
    selection-background-color: {t["nav_selected_bg"]};
    selection-color: {t["accent"]};
    outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; border: none; background: {t["panel_inset"]}; }}

/* ── Scrollbars ──────────────────────────────────────────────────── */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: {t["scrollbar_bg"]}; width: 9px; margin: 0; border: none; }}
QScrollBar::handle:vertical {{ background: {t["scrollbar_handle"]}; border-radius: 4px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {t["scrollbar_handle_hover"]}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: {t["scrollbar_bg"]}; height: 9px; margin: 0; border: none; }}
QScrollBar::handle:horizontal {{ background: {t["scrollbar_handle"]}; border-radius: 4px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: {t["scrollbar_handle_hover"]}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ── Splitter (handles are pure whitespace for an airy, card-gap feel) ─ */
QSplitter#WorkbenchSplitter::handle {{ background: transparent; }}
QSplitter::handle {{ background-color: transparent; }}
QSplitter::handle:hover {{ background-color: {t["accent_soft"]}; border-radius: 2px; }}
QSplitter::handle:horizontal {{ width: 18px; }}
QSplitter::handle:vertical {{ height: 18px; }}

/* ── Specimen list ───────────────────────────────────────────────── */
QListWidget#SpecimenList {{
    background-color: transparent;
    border: 1px solid {t["border"]};
    border-radius: {t["radius"]};
    outline: none;
    padding: 4px;
}}
QListWidget#SpecimenList::item {{
    color: {t["text_soft"]};
    border: 1px solid transparent;
    border-radius: {t["radius_sm"]};
    padding: 8px 10px;
    margin: 1px 0;
}}
QListWidget#SpecimenList::item:hover {{ background-color: {t["bg_raised"]}; }}
QListWidget#SpecimenList::item:selected {{
    background-color: {t["nav_selected_bg"]};
    border: 1px solid {t["accent"]};
    color: {t["text"]};
}}

/* ── Generic list / grid JPG list ────────────────────────────────── */
QListWidget {{
    background-color: {t["panel_inset"]};
    border: 1px solid {t["border"]};
    border-radius: {t["radius_sm"]};
    outline: none;
}}
QListWidget::item {{ padding: 3px 6px; border-radius: 3px; }}
QListWidget::item:selected {{ background-color: {t["nav_selected_bg"]}; color: {t["accent"]}; }}

/* ── Status bar ──────────────────────────────────────────────────── */
QStatusBar {{
    background-color: {t["statusbar_bg"]};
    color: {t["muted"]};
    border-top: 1px solid {t["border"]};
    font-size: {t["font_sm"]};
    padding: 2px 10px;
}}
QStatusBar::item {{ border: none; }}
QLabel#StatusSegment {{ color: {t["muted"]}; padding: 0 10px; font-size: {t["font_sm"]}; }}
QLabel#StatusSegmentAccent {{ color: {t["accent"]}; padding: 0 10px; font-size: {t["font_sm"]}; }}

/* ── Mono label (file paths / UIDs) ──────────────────────────────── */
QLabel#Mono {{
    font-family: {mono};
    font-size: {t["font_sm"]};
    color: {t["accent"]};
    background-color: {t["modal_surface"]};
    border-radius: {t["radius_sm"]};
    padding: 2px 6px;
}}

/* ── Menu ────────────────────────────────────────────────────────── */
QMenu {{
    background-color: {t["panel"]};
    color: {t["text"]};
    border: 1px solid {t["border_medium"]};
    border-radius: {t["radius_sm"]};
    padding: 5px 0;
}}
QMenu::item {{ padding: 6px 20px; }}
QMenu::item:selected {{ background-color: {t["nav_selected_bg"]}; color: {t["accent"]}; }}
QMenu::separator {{ height: 1px; background-color: {t["border"]}; margin: 4px 8px; }}

/* ── CheckBox ────────────────────────────────────────────────────── */
QCheckBox, QRadioButton {{ color: {t["text_soft"]}; spacing: 7px; font-size: {t["font_body"]}; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {t["border_medium"]};
    border-radius: 4px;
    background-color: {t["input_bg"]};
}}
QCheckBox::indicator:checked {{ background-color: {t["accent"]}; border-color: {t["accent"]}; }}

/* ── Table / Tree ────────────────────────────────────────────────── */
QTableWidget, QTableView, QTreeView {{
    background-color: {t["bg"]};
    color: {t["text"]};
    border: 1px solid {t["border"]};
    gridline-color: {t["border"]};
    alternate-background-color: {t["panel_2"]};
    selection-background-color: {t["nav_selected_bg"]};
    selection-color: {t["accent"]};
}}
QHeaderView::section {{
    background-color: {t["panel"]};
    color: {t["muted"]};
    border: none;
    border-bottom: 1px solid {t["border"]};
    padding: 5px 10px;
    font-size: {t["font_sm"]};
    font-weight: 600;
    letter-spacing: 0.4px;
}}
"""


_QSS_OUTPUT = Path(__file__).parent.parent.parent / "resources" / "theme.qss"


def build_theme_qss_file() -> Path:
    """Write theme.qss to resources/ and return its path."""
    _QSS_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    _QSS_OUTPUT.write_text(build_qss(), encoding="utf-8")
    return _QSS_OUTPUT
