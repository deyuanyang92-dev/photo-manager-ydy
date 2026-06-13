"""theme.py — Multi-theme design token system.

Public API (kept stable — referenced elsewhere):
  TOKENS                 design token dict (active theme, updated in-place)
  THEMES                 registry: key -> token dict
  THEME_NAMES            registry: key -> Chinese display name
  apply_theme(name)      -> switch TOKENS + return new QSS string
  build_qss()            -> full QSS string from current TOKENS
  build_theme_qss_file() -> writes resources/theme.qss, returns its Path
  load_fonts(app)        -> registers bundled fonts (graceful fallback)
  FONT_SANS / FONT_SERIF / FONT_MONO  resolved family stacks
"""

from __future__ import annotations

from pathlib import Path

# ── Shared typography / radius tokens (same across all themes) ─────────────

_TYPO: dict[str, str] = {
    "font_xs": "11px",
    "font_sm": "12px",
    "font_body": "13px",
    "font_md": "15px",
    "font_lg": "18px",
    "font_title": "20px",
    "radius": "8px",
    "radius_lg": "10px",
    "radius_sm": "6px",
    "radius_pill": "999px",
}

# Base font sizes in px at scale 1.0.  _TYPO mirrors these as strings for
# back-compat; the *active* sizes are recomputed from these by _scaled_typo()
# and re-applied over the theme defaults whenever 字体大小 changes in 设置.
_FONT_BASE: dict[str, int] = {
    "font_xs": 11, "font_sm": 12, "font_body": 13,
    "font_md": 15, "font_lg": 18, "font_title": 20,
}
_FONT_SCALE: float = 1.0          # 全局字体大小倍率，由 set_typography() 更新
_FONT_FAMILY: str = ""            # 用户指定字体，空=用 CJK-first FONT_SANS 栈


def set_typography(scale=None, family=None) -> None:
    """Set the global font-size scale and/or family override used by build_qss().

    Call before apply_theme() (or re-apply the theme afterwards).  ``scale`` is
    clamped to 0.7–1.6; ``family=""`` clears the override (back to the CJK-first
    FONT_SANS stack).  ``None`` for either arg leaves it unchanged.
    """
    global _FONT_SCALE, _FONT_FAMILY
    if scale is not None:
        try:
            _FONT_SCALE = max(0.7, min(1.6, float(scale)))
        except (TypeError, ValueError):
            pass
    if family is not None:
        _FONT_FAMILY = str(family).strip()


def _scaled_typo() -> dict[str, str]:
    """Current font_* px tokens with _FONT_SCALE applied."""
    return {k: f"{max(1, round(v * _FONT_SCALE))}px" for k, v in _FONT_BASE.items()}

# ── Theme 1: 经典浅色 — Windows/Office conventional light ──────────────────

THEME_CLASSIC_LIGHT: dict[str, str] = {
    "bg": "#f4f6f8",
    "bg_grad_top": "#f7f9fb",
    "bg_grad_bottom": "#eef2f5",
    "bg_raised": "#ffffff",
    "panel": "#ffffff",
    "panel_top": "#ffffff",
    "panel_bottom": "#fbfcfd",
    "panel_2": "#f8fafc",
    "panel_2_top": "#ffffff",
    "panel_2_bottom": "#f3f6f8",
    "panel_inset": "#eef2f6",
    "modal_surface": "#ffffff",
    "modal_surface_raised": "#f5f7fa",
    "text": "#17212b",
    "text_soft": "#334155",
    "muted": "#64748b",
    "muted_dim": "#94a3b8",
    "accent": "#0f766e",
    "accent_top": "#14b8a6",
    "accent_bottom": "#0f766e",
    "accent_hover": "#0d9488",
    "accent_pressed": "#115e59",
    "accent_soft": "rgba(15,118,110,0.11)",
    "accent_softer": "rgba(15,118,110,0.06)",
    "accent_glow": "rgba(15,118,110,0.24)",
    "edge_highlight": "rgba(255,255,255,0.72)",
    "edge_highlight_soft": "rgba(255,255,255,0.45)",
    "warn": "#b45309",
    "warn_soft": "rgba(180,83,9,0.11)",
    "success": "#15803d",
    "success_soft": "rgba(21,128,61,0.11)",
    "danger": "#b42318",
    "danger_soft": "rgba(180,35,24,0.10)",
    "info": "#2563eb",
    "info_soft": "rgba(37,99,235,0.10)",
    "border": "rgba(15,23,42,0.08)",
    "border_medium": "rgba(15,23,42,0.13)",
    "border_strong": "rgba(15,23,42,0.22)",
    "nav_bg": "#eef2f6",
    "nav_selected_bg": "#e6f5f3",
    "nav_selected_border": "#0f766e",
    "topbar_top": "#ffffff",
    "topbar_bottom": "#ffffff",
    "topbar_border": "rgba(15,23,42,0.08)",
    "contextbar_bg": "#f8fafc",
    "nav_segment_text": "#475569",
    "nav_segment_hover_bg": "rgba(15,118,110,0.06)",
    "statusbar_bg": "#edf2f7",
    "input_bg": "#ffffff",
    "input_bg_auto": "#f8fafc",
    "input_border": "rgba(15,23,42,0.14)",
    "input_focus_border": "#0f766e",
    "scrollbar_bg": "transparent",
    "scrollbar_handle": "#cbd5e1",
    "scrollbar_handle_hover": "#0f766e",
    **_TYPO,
}

# ── Theme 2: 暖灰浅色 — macOS Finder warm light ────────────────────────────

THEME_WARM_GRAY: dict[str, str] = {
    "bg": "#ececec",
    "bg_grad_top": "#f2f2f2",
    "bg_grad_bottom": "#e5e5e5",
    "bg_raised": "#ffffff",
    "panel": "#ffffff",
    "panel_top": "#ffffff",
    "panel_bottom": "#f8f8f6",
    "panel_2": "#f5f5f3",
    "panel_2_top": "#f9f9f7",
    "panel_2_bottom": "#f0f0ee",
    "panel_inset": "#ebebea",
    "modal_surface": "#ffffff",
    "modal_surface_raised": "#f0f0ee",
    "text": "#1d1d1f",
    "text_soft": "#3d3d3f",
    "muted": "#6e6e73",
    "muted_dim": "#98989d",
    "accent": "#007aff",
    "accent_top": "#1a8dff",
    "accent_bottom": "#0068d9",
    "accent_hover": "#0a84ff",
    "accent_pressed": "#0060d0",
    "accent_soft": "rgba(0,122,255,0.10)",
    "accent_softer": "rgba(0,122,255,0.06)",
    "accent_glow": "rgba(0,122,255,0.22)",
    "edge_highlight": "rgba(0,0,0,0.04)",
    "edge_highlight_soft": "rgba(0,0,0,0.02)",
    "warn": "#ff9500",
    "warn_soft": "rgba(255,149,0,0.12)",
    "success": "#34c759",
    "success_soft": "rgba(52,199,89,0.12)",
    "danger": "#ff3b30",
    "danger_soft": "rgba(255,59,48,0.12)",
    "info": "#007aff",
    "info_soft": "rgba(0,122,255,0.12)",
    "border": "rgba(0,0,0,0.08)",
    "border_medium": "rgba(0,0,0,0.14)",
    "border_strong": "rgba(0,0,0,0.24)",
    "nav_bg": "#f2f2f0",
    "nav_selected_bg": "#e8f3ff",
    "nav_selected_border": "#007aff",
    "topbar_top": "#f8f8f6",
    "topbar_bottom": "#eeeeec",
    "topbar_border": "rgba(0,0,0,0.08)",
    "contextbar_bg": "#f2f2f0",
    "nav_segment_text": "#555555",
    "nav_segment_hover_bg": "rgba(0,122,255,0.06)",
    "statusbar_bg": "#e5e5e3",
    "input_bg": "#ffffff",
    "input_bg_auto": "#f5f5f3",
    "input_border": "rgba(0,0,0,0.16)",
    "input_focus_border": "#007aff",
    "scrollbar_bg": "transparent",
    "scrollbar_handle": "#c8c8c8",
    "scrollbar_handle_hover": "#007aff",
    **_TYPO,
}

# ── Theme 3: 实验室清爽 — brighter bench UI with blue scientific accent ─────

THEME_LAB_LIGHT: dict[str, str] = {
    "bg": "#f7f8fb",
    "bg_grad_top": "#fbfcfe",
    "bg_grad_bottom": "#eef3f8",
    "bg_raised": "#ffffff",
    "panel": "#ffffff",
    "panel_top": "#ffffff",
    "panel_bottom": "#f7fafc",
    "panel_2": "#f2f6fa",
    "panel_2_top": "#f8fbfe",
    "panel_2_bottom": "#edf3f8",
    "panel_inset": "#e8eef5",
    "modal_surface": "#ffffff",
    "modal_surface_raised": "#f3f7fb",
    "text": "#102033",
    "text_soft": "#2c3e50",
    "muted": "#60758a",
    "muted_dim": "#93a4b5",
    "accent": "#2563eb",
    "accent_top": "#3b82f6",
    "accent_bottom": "#1d4ed8",
    "accent_hover": "#1d64f2",
    "accent_pressed": "#1e40af",
    "accent_soft": "rgba(37,99,235,0.11)",
    "accent_softer": "rgba(37,99,235,0.06)",
    "accent_glow": "rgba(37,99,235,0.24)",
    "edge_highlight": "rgba(255,255,255,0.78)",
    "edge_highlight_soft": "rgba(255,255,255,0.50)",
    "warn": "#b7791f",
    "warn_soft": "rgba(183,121,31,0.12)",
    "success": "#047857",
    "success_soft": "rgba(4,120,87,0.11)",
    "danger": "#be123c",
    "danger_soft": "rgba(190,18,60,0.10)",
    "info": "#0284c7",
    "info_soft": "rgba(2,132,199,0.10)",
    "border": "rgba(30,64,105,0.08)",
    "border_medium": "rgba(30,64,105,0.14)",
    "border_strong": "rgba(30,64,105,0.24)",
    "nav_bg": "#eef4fb",
    "nav_selected_bg": "#e7f0ff",
    "nav_selected_border": "#2563eb",
    "topbar_top": "#ffffff",
    "topbar_bottom": "#ffffff",
    "topbar_border": "rgba(30,64,105,0.09)",
    "contextbar_bg": "#f5f8fb",
    "nav_segment_text": "#455b73",
    "nav_segment_hover_bg": "rgba(37,99,235,0.06)",
    "statusbar_bg": "#eaf0f6",
    "input_bg": "#ffffff",
    "input_bg_auto": "#f7fafc",
    "input_border": "rgba(30,64,105,0.15)",
    "input_focus_border": "#2563eb",
    "scrollbar_bg": "transparent",
    "scrollbar_handle": "#c7d3df",
    "scrollbar_handle_hover": "#2563eb",
    **_TYPO,
}

# ── Theme 4: 深色专注 — graphite desktop, lower glare for long sessions ─────

THEME_GRAPHITE_FOCUS: dict[str, str] = {
    "bg": "#111827",
    "bg_grad_top": "#172033",
    "bg_grad_bottom": "#0f1724",
    "bg_raised": "#1f2937",
    "panel": "#1b2432",
    "panel_top": "#243044",
    "panel_bottom": "#182230",
    "panel_2": "#151f2e",
    "panel_2_top": "#1b2738",
    "panel_2_bottom": "#131c29",
    "panel_inset": "#0f1724",
    "modal_surface": "#202b3b",
    "modal_surface_raised": "#2a3648",
    "text": "#edf2f7",
    "text_soft": "#cbd5e1",
    "muted": "#94a3b8",
    "muted_dim": "#64748b",
    "accent": "#22d3ee",
    "accent_top": "#67e8f9",
    "accent_bottom": "#0891b2",
    "accent_hover": "#38bdf8",
    "accent_pressed": "#0e7490",
    "accent_soft": "rgba(34,211,238,0.14)",
    "accent_softer": "rgba(34,211,238,0.08)",
    "accent_glow": "rgba(34,211,238,0.28)",
    "edge_highlight": "rgba(255,255,255,0.055)",
    "edge_highlight_soft": "rgba(255,255,255,0.035)",
    "warn": "#fbbf24",
    "warn_soft": "rgba(251,191,36,0.13)",
    "success": "#34d399",
    "success_soft": "rgba(52,211,153,0.13)",
    "danger": "#fb7185",
    "danger_soft": "rgba(251,113,133,0.13)",
    "info": "#60a5fa",
    "info_soft": "rgba(96,165,250,0.13)",
    "border": "rgba(148,163,184,0.12)",
    "border_medium": "rgba(148,163,184,0.20)",
    "border_strong": "rgba(148,163,184,0.36)",
    "nav_bg": "#0f1724",
    "nav_selected_bg": "#173142",
    "nav_selected_border": "#22d3ee",
    "topbar_top": "#121b2a",
    "topbar_bottom": "#101827",
    "topbar_border": "rgba(148,163,184,0.12)",
    "contextbar_bg": "#111b2a",
    "nav_segment_text": "#a7b4c7",
    "nav_segment_hover_bg": "rgba(34,211,238,0.08)",
    "statusbar_bg": "#0d1522",
    "input_bg": "#101827",
    "input_bg_auto": "#0d1522",
    "input_border": "rgba(148,163,184,0.18)",
    "input_focus_border": "#22d3ee",
    "scrollbar_bg": "transparent",
    "scrollbar_handle": "#334155",
    "scrollbar_handle_hover": "#22d3ee",
    **_TYPO,
}

# ── Theme 5: 深青暗色 — original deep-teal (web oracle palette) ─────────────

THEME_DEEP_TEAL: dict[str, str] = {
    "bg": "#08161b",
    "bg_grad_top": "#0a1a20",
    "bg_grad_bottom": "#071319",
    "bg_raised": "#0c2027",
    "panel": "#10242a",
    "panel_top": "#143037",
    "panel_bottom": "#0f2228",
    "panel_2": "#0b2025",
    "panel_2_top": "#0e2329",
    "panel_2_bottom": "#0a1d22",
    "panel_inset": "#0a1e24",
    "modal_surface": "#10282e",
    "modal_surface_raised": "#143139",
    "text": "#eef3ef",
    "text_soft": "#cfe0db",
    "muted": "#87a2a1",
    "muted_dim": "#5f7d7a",
    "accent": "#29b9ab",
    "accent_top": "#33c8ba",
    "accent_bottom": "#23a99c",
    "accent_hover": "#31d4c4",
    "accent_pressed": "#1f9288",
    "accent_soft": "rgba(41,185,171,0.14)",
    "accent_softer": "rgba(41,185,171,0.08)",
    "accent_glow": "rgba(41,185,171,0.30)",
    "edge_highlight": "rgba(255,255,255,0.045)",
    "edge_highlight_soft": "rgba(255,255,255,0.03)",
    "warn": "#f1bd57",
    "warn_soft": "rgba(241,189,87,0.13)",
    "success": "#36c98f",
    "success_soft": "rgba(54,201,143,0.14)",
    "danger": "#e66e63",
    "danger_soft": "rgba(230,110,99,0.13)",
    "info": "#4a90d9",
    "info_soft": "rgba(74,144,217,0.14)",
    "border": "rgba(145,182,181,0.10)",
    "border_medium": "rgba(145,182,181,0.18)",
    "border_strong": "rgba(152,205,198,0.34)",
    "nav_bg": "#091e24",
    "nav_selected_bg": "#12313a",
    "nav_selected_border": "#29b9ab",
    "topbar_top": "#0a1d23",
    "topbar_bottom": "#071519",
    "topbar_border": "rgba(145,182,181,0.10)",
    "contextbar_bg": "#0a1d23",
    "nav_segment_text": "#9fbab8",
    "nav_segment_hover_bg": "rgba(41,185,171,0.07)",
    "statusbar_bg": "#081a20",
    "input_bg": "#0f2127",
    "input_bg_auto": "#0c1c21",
    "input_border": "rgba(145,182,181,0.16)",
    "input_focus_border": "#29b9ab",
    "scrollbar_bg": "transparent",
    "scrollbar_handle": "#1d3a44",
    "scrollbar_handle_hover": "#29b9ab",
    **_TYPO,
}

# ── Theme 4: 材质深色 — Google Material dark, purple accent ────────────────

THEME_MATERIAL_DARK: dict[str, str] = {
    "bg": "#121212",
    "bg_grad_top": "#161616",
    "bg_grad_bottom": "#0e0e0e",
    "bg_raised": "#1e1e1e",
    "panel": "#1e1e1e",
    "panel_top": "#252525",
    "panel_bottom": "#1a1a1a",
    "panel_2": "#252525",
    "panel_2_top": "#2c2c2c",
    "panel_2_bottom": "#202020",
    "panel_inset": "#181818",
    "modal_surface": "#2c2c2c",
    "modal_surface_raised": "#333333",
    "text": "#e8e8e8",
    "text_soft": "#c0c0c0",
    "muted": "#8a8a8a",
    "muted_dim": "#5a5a5a",
    "accent": "#bb86fc",
    "accent_top": "#cc99ff",
    "accent_bottom": "#a970f0",
    "accent_hover": "#d3a8ff",
    "accent_pressed": "#9960e0",
    "accent_soft": "rgba(187,134,252,0.14)",
    "accent_softer": "rgba(187,134,252,0.08)",
    "accent_glow": "rgba(187,134,252,0.28)",
    "edge_highlight": "rgba(255,255,255,0.05)",
    "edge_highlight_soft": "rgba(255,255,255,0.03)",
    "warn": "#f1bd57",
    "warn_soft": "rgba(241,189,87,0.13)",
    "success": "#03dac6",
    "success_soft": "rgba(3,218,198,0.13)",
    "danger": "#cf6679",
    "danger_soft": "rgba(207,102,121,0.13)",
    "info": "#03a9f4",
    "info_soft": "rgba(3,169,244,0.13)",
    "border": "rgba(255,255,255,0.08)",
    "border_medium": "rgba(255,255,255,0.14)",
    "border_strong": "rgba(255,255,255,0.26)",
    "nav_bg": "#0f0f0f",
    "nav_selected_bg": "#2a1d3d",
    "nav_selected_border": "#bb86fc",
    "topbar_top": "#1a1a1a",
    "topbar_bottom": "#111111",
    "topbar_border": "rgba(255,255,255,0.08)",
    "contextbar_bg": "#161616",
    "nav_segment_text": "#909090",
    "nav_segment_hover_bg": "rgba(187,134,252,0.07)",
    "statusbar_bg": "#0d0d0d",
    "input_bg": "#1c1c1c",
    "input_bg_auto": "#181818",
    "input_border": "rgba(255,255,255,0.12)",
    "input_focus_border": "#bb86fc",
    "scrollbar_bg": "transparent",
    "scrollbar_handle": "#333340",
    "scrollbar_handle_hover": "#bb86fc",
    **_TYPO,
}

# ── Theme 5: 暗夜紫影 — Dracula purple/pink ────────────────────────────────

THEME_DRACULA: dict[str, str] = {
    "bg": "#282a36",
    "bg_grad_top": "#2d2f3d",
    "bg_grad_bottom": "#232530",
    "bg_raised": "#363844",
    "panel": "#44475a",
    "panel_top": "#4d506a",
    "panel_bottom": "#3d4055",
    "panel_2": "#383a4a",
    "panel_2_top": "#3d4055",
    "panel_2_bottom": "#333545",
    "panel_inset": "#21222c",
    "modal_surface": "#44475a",
    "modal_surface_raised": "#4d506a",
    "text": "#f8f8f2",
    "text_soft": "#e0e0d8",
    "muted": "#a8a8a0",
    "muted_dim": "#7070a0",
    "accent": "#bd93f9",
    "accent_top": "#cc99ff",
    "accent_bottom": "#aa80f0",
    "accent_hover": "#d3a8ff",
    "accent_pressed": "#9966e0",
    "accent_soft": "rgba(189,147,249,0.15)",
    "accent_softer": "rgba(189,147,249,0.08)",
    "accent_glow": "rgba(189,147,249,0.30)",
    "edge_highlight": "rgba(255,255,255,0.04)",
    "edge_highlight_soft": "rgba(255,255,255,0.025)",
    "warn": "#f1fa8c",
    "warn_soft": "rgba(241,250,140,0.13)",
    "success": "#50fa7b",
    "success_soft": "rgba(80,250,123,0.13)",
    "danger": "#ff5555",
    "danger_soft": "rgba(255,85,85,0.13)",
    "info": "#8be9fd",
    "info_soft": "rgba(139,233,253,0.13)",
    "border": "rgba(98,114,164,0.30)",
    "border_medium": "rgba(98,114,164,0.45)",
    "border_strong": "rgba(98,114,164,0.65)",
    "nav_bg": "#21222c",
    "nav_selected_bg": "#44475a",
    "nav_selected_border": "#bd93f9",
    "topbar_top": "#21222c",
    "topbar_bottom": "#1a1b25",
    "topbar_border": "rgba(98,114,164,0.30)",
    "contextbar_bg": "#21222c",
    "nav_segment_text": "#c8c8c0",
    "nav_segment_hover_bg": "rgba(189,147,249,0.08)",
    "statusbar_bg": "#191a24",
    "input_bg": "#21222c",
    "input_bg_auto": "#1a1b24",
    "input_border": "rgba(98,114,164,0.40)",
    "input_focus_border": "#bd93f9",
    "scrollbar_bg": "transparent",
    "scrollbar_handle": "#44475a",
    "scrollbar_handle_hover": "#bd93f9",
    **_TYPO,
}

# ── Theme 6: 北欧极光 — Nord blue-grey polar night ─────────────────────────

THEME_NORD: dict[str, str] = {
    "bg": "#2e3440",
    "bg_grad_top": "#323748",
    "bg_grad_bottom": "#2a3038",
    "bg_raised": "#3b4252",
    "panel": "#3b4252",
    "panel_top": "#414b5e",
    "panel_bottom": "#363f50",
    "panel_2": "#434c5e",
    "panel_2_top": "#4a5468",
    "panel_2_bottom": "#3c4558",
    "panel_inset": "#252c3a",
    "modal_surface": "#434c5e",
    "modal_surface_raised": "#4c5666",
    "text": "#eceff4",
    "text_soft": "#d8dee9",
    "muted": "#9aa3af",
    "muted_dim": "#6e7a88",
    "accent": "#88c0d0",
    "accent_top": "#99cfe0",
    "accent_bottom": "#77b0c0",
    "accent_hover": "#9fd0e0",
    "accent_pressed": "#67a0b0",
    "accent_soft": "rgba(136,192,208,0.14)",
    "accent_softer": "rgba(136,192,208,0.08)",
    "accent_glow": "rgba(136,192,208,0.28)",
    "edge_highlight": "rgba(255,255,255,0.04)",
    "edge_highlight_soft": "rgba(255,255,255,0.025)",
    "warn": "#ebcb8b",
    "warn_soft": "rgba(235,203,139,0.13)",
    "success": "#a3be8c",
    "success_soft": "rgba(163,190,140,0.13)",
    "danger": "#bf616a",
    "danger_soft": "rgba(191,97,106,0.13)",
    "info": "#81a1c1",
    "info_soft": "rgba(129,161,193,0.13)",
    "border": "rgba(216,222,233,0.12)",
    "border_medium": "rgba(216,222,233,0.20)",
    "border_strong": "rgba(216,222,233,0.36)",
    "nav_bg": "#272e3c",
    "nav_selected_bg": "#374155",
    "nav_selected_border": "#88c0d0",
    "topbar_top": "#2a3040",
    "topbar_bottom": "#252b39",
    "topbar_border": "rgba(216,222,233,0.12)",
    "contextbar_bg": "#282f3d",
    "nav_segment_text": "#94a0ad",
    "nav_segment_hover_bg": "rgba(136,192,208,0.08)",
    "statusbar_bg": "#232935",
    "input_bg": "#2e3440",
    "input_bg_auto": "#262d3b",
    "input_border": "rgba(216,222,233,0.16)",
    "input_focus_border": "#88c0d0",
    "scrollbar_bg": "transparent",
    "scrollbar_handle": "#434c5e",
    "scrollbar_handle_hover": "#88c0d0",
    **_TYPO,
}

# ── Theme 7: 原子深色 — Atom One Dark ────────────────────────────────────

THEME_ONE_DARK: dict[str, str] = {
    "bg": "#282c34",
    "bg_grad_top": "#2c3038",
    "bg_grad_bottom": "#242830",
    "bg_raised": "#31353f",
    "panel": "#21252b",
    "panel_top": "#262b32",
    "panel_bottom": "#1e2228",
    "panel_2": "#2c313a",
    "panel_2_top": "#323840",
    "panel_2_bottom": "#282d36",
    "panel_inset": "#181c22",
    "modal_surface": "#31353f",
    "modal_surface_raised": "#383c46",
    "text": "#abb2bf",
    "text_soft": "#9aa0ad",
    "muted": "#6b7280",
    "muted_dim": "#4b5260",
    "accent": "#61afef",
    "accent_top": "#72bcf5",
    "accent_bottom": "#52a0e0",
    "accent_hover": "#7abff8",
    "accent_pressed": "#4090d0",
    "accent_soft": "rgba(97,175,239,0.14)",
    "accent_softer": "rgba(97,175,239,0.08)",
    "accent_glow": "rgba(97,175,239,0.28)",
    "edge_highlight": "rgba(255,255,255,0.04)",
    "edge_highlight_soft": "rgba(255,255,255,0.025)",
    "warn": "#e5c07b",
    "warn_soft": "rgba(229,192,123,0.13)",
    "success": "#98c379",
    "success_soft": "rgba(152,195,121,0.13)",
    "danger": "#e06c75",
    "danger_soft": "rgba(224,108,117,0.13)",
    "info": "#56b6c2",
    "info_soft": "rgba(86,182,194,0.13)",
    "border": "rgba(171,178,191,0.10)",
    "border_medium": "rgba(171,178,191,0.18)",
    "border_strong": "rgba(171,178,191,0.32)",
    "nav_bg": "#1d2026",
    "nav_selected_bg": "#2d3342",
    "nav_selected_border": "#61afef",
    "topbar_top": "#21252b",
    "topbar_bottom": "#1c2028",
    "topbar_border": "rgba(171,178,191,0.10)",
    "contextbar_bg": "#1f2329",
    "nav_segment_text": "#828a9a",
    "nav_segment_hover_bg": "rgba(97,175,239,0.07)",
    "statusbar_bg": "#1a1e24",
    "input_bg": "#1c2026",
    "input_bg_auto": "#181c22",
    "input_border": "rgba(171,178,191,0.14)",
    "input_focus_border": "#61afef",
    "scrollbar_bg": "transparent",
    "scrollbar_handle": "#2e3440",
    "scrollbar_handle_hover": "#61afef",
    **_TYPO,
}

# ── Theme 8: 暖褐复古 — Monokai warm brown/green ──────────────────────────

THEME_MONOKAI: dict[str, str] = {
    "bg": "#272822",
    "bg_grad_top": "#2c2d26",
    "bg_grad_bottom": "#222320",
    "bg_raised": "#2f3029",
    "panel": "#3e3d32",
    "panel_top": "#46453a",
    "panel_bottom": "#38372e",
    "panel_2": "#353430",
    "panel_2_top": "#3c3b36",
    "panel_2_bottom": "#2f2e2a",
    "panel_inset": "#1e1f1a",
    "modal_surface": "#3e3d32",
    "modal_surface_raised": "#46453a",
    "text": "#f8f8f2",
    "text_soft": "#e0e0d8",
    "muted": "#939389",
    "muted_dim": "#66665e",
    "accent": "#a6e22e",
    "accent_top": "#b8f040",
    "accent_bottom": "#90cc28",
    "accent_hover": "#c0f050",
    "accent_pressed": "#80b820",
    "accent_soft": "rgba(166,226,46,0.13)",
    "accent_softer": "rgba(166,226,46,0.07)",
    "accent_glow": "rgba(166,226,46,0.28)",
    "edge_highlight": "rgba(255,255,255,0.04)",
    "edge_highlight_soft": "rgba(255,255,255,0.025)",
    "warn": "#e6db74",
    "warn_soft": "rgba(230,219,116,0.13)",
    "success": "#a6e22e",
    "success_soft": "rgba(166,226,46,0.13)",
    "danger": "#f92672",
    "danger_soft": "rgba(249,38,114,0.13)",
    "info": "#66d9e8",
    "info_soft": "rgba(102,217,232,0.13)",
    "border": "rgba(248,248,242,0.09)",
    "border_medium": "rgba(248,248,242,0.16)",
    "border_strong": "rgba(248,248,242,0.30)",
    "nav_bg": "#1e1f1a",
    "nav_selected_bg": "#3e3d32",
    "nav_selected_border": "#a6e22e",
    "topbar_top": "#272822",
    "topbar_bottom": "#1e1f1a",
    "topbar_border": "rgba(248,248,242,0.09)",
    "contextbar_bg": "#222318",
    "nav_segment_text": "#808078",
    "nav_segment_hover_bg": "rgba(166,226,46,0.07)",
    "statusbar_bg": "#1c1d18",
    "input_bg": "#1e1f1a",
    "input_bg_auto": "#1a1b17",
    "input_border": "rgba(248,248,242,0.13)",
    "input_focus_border": "#a6e22e",
    "scrollbar_bg": "transparent",
    "scrollbar_handle": "#3e3d32",
    "scrollbar_handle_hover": "#a6e22e",
    **_TYPO,
}

# ── Theme 9: 翠绿护眼 — Forest green easy-on-eyes dark ────────────────────

THEME_FOREST: dict[str, str] = {
    "bg": "#1a2416",
    "bg_grad_top": "#1e2a1a",
    "bg_grad_bottom": "#161f13",
    "bg_raised": "#1f2c1a",
    "panel": "#213018",
    "panel_top": "#253818",
    "panel_bottom": "#1e2c16",
    "panel_2": "#1c2914",
    "panel_2_top": "#202e17",
    "panel_2_bottom": "#192511",
    "panel_inset": "#141f10",
    "modal_surface": "#253318",
    "modal_surface_raised": "#2b3c1e",
    "text": "#d4edaa",
    "text_soft": "#b8d890",
    "muted": "#80a870",
    "muted_dim": "#5a7850",
    "accent": "#6fbf4a",
    "accent_top": "#80d058",
    "accent_bottom": "#60a840",
    "accent_hover": "#88d860",
    "accent_pressed": "#509838",
    "accent_soft": "rgba(111,191,74,0.14)",
    "accent_softer": "rgba(111,191,74,0.08)",
    "accent_glow": "rgba(111,191,74,0.28)",
    "edge_highlight": "rgba(255,255,255,0.035)",
    "edge_highlight_soft": "rgba(255,255,255,0.02)",
    "warn": "#d4b040",
    "warn_soft": "rgba(212,176,64,0.13)",
    "success": "#88d860",
    "success_soft": "rgba(136,216,96,0.14)",
    "danger": "#e06060",
    "danger_soft": "rgba(224,96,96,0.13)",
    "info": "#60b8d0",
    "info_soft": "rgba(96,184,208,0.13)",
    "border": "rgba(111,191,74,0.12)",
    "border_medium": "rgba(111,191,74,0.20)",
    "border_strong": "rgba(111,191,74,0.36)",
    "nav_bg": "#141e10",
    "nav_selected_bg": "#1f3015",
    "nav_selected_border": "#6fbf4a",
    "topbar_top": "#192213",
    "topbar_bottom": "#141d0f",
    "topbar_border": "rgba(111,191,74,0.12)",
    "contextbar_bg": "#161f12",
    "nav_segment_text": "#78a860",
    "nav_segment_hover_bg": "rgba(111,191,74,0.07)",
    "statusbar_bg": "#121c0e",
    "input_bg": "#192212",
    "input_bg_auto": "#151d0f",
    "input_border": "rgba(111,191,74,0.16)",
    "input_focus_border": "#6fbf4a",
    "scrollbar_bg": "transparent",
    "scrollbar_handle": "#2a3c20",
    "scrollbar_handle_hover": "#6fbf4a",
    **_TYPO,
}

# ── Theme 10: 高对比 — Pure black, yellow accent, accessibility ───────────

THEME_HIGH_CONTRAST: dict[str, str] = {
    "bg": "#000000",
    "bg_grad_top": "#0a0a0a",
    "bg_grad_bottom": "#000000",
    "bg_raised": "#0d0d0d",
    "panel": "#0d0d0d",
    "panel_top": "#141414",
    "panel_bottom": "#0a0a0a",
    "panel_2": "#111111",
    "panel_2_top": "#181818",
    "panel_2_bottom": "#0d0d0d",
    "panel_inset": "#060606",
    "modal_surface": "#141414",
    "modal_surface_raised": "#1c1c1c",
    "text": "#ffffff",
    "text_soft": "#e0e0e0",
    "muted": "#b0b0b0",
    "muted_dim": "#808080",
    "accent": "#ffff00",
    "accent_top": "#ffff33",
    "accent_bottom": "#dddd00",
    "accent_hover": "#ffff55",
    "accent_pressed": "#cccc00",
    "accent_soft": "rgba(255,255,0,0.15)",
    "accent_softer": "rgba(255,255,0,0.08)",
    "accent_glow": "rgba(255,255,0,0.35)",
    "edge_highlight": "rgba(255,255,255,0.08)",
    "edge_highlight_soft": "rgba(255,255,255,0.04)",
    "warn": "#ff8800",
    "warn_soft": "rgba(255,136,0,0.15)",
    "success": "#00ff00",
    "success_soft": "rgba(0,255,0,0.12)",
    "danger": "#ff4444",
    "danger_soft": "rgba(255,68,68,0.12)",
    "info": "#00ccff",
    "info_soft": "rgba(0,204,255,0.12)",
    "border": "rgba(255,255,255,0.25)",
    "border_medium": "rgba(255,255,255,0.40)",
    "border_strong": "rgba(255,255,255,0.60)",
    "nav_bg": "#000000",
    "nav_selected_bg": "#1a1a00",
    "nav_selected_border": "#ffff00",
    "topbar_top": "#0d0d0d",
    "topbar_bottom": "#000000",
    "topbar_border": "rgba(255,255,255,0.25)",
    "contextbar_bg": "#080808",
    "nav_segment_text": "#c0c0c0",
    "nav_segment_hover_bg": "rgba(255,255,0,0.08)",
    "statusbar_bg": "#000000",
    "input_bg": "#0a0a0a",
    "input_bg_auto": "#060606",
    "input_border": "rgba(255,255,255,0.35)",
    "input_focus_border": "#ffff00",
    "scrollbar_bg": "transparent",
    "scrollbar_handle": "#333333",
    "scrollbar_handle_hover": "#ffff00",
    **_TYPO,
}

# ── Theme registry ─────────────────────────────────────────────────────────

THEMES: dict[str, dict[str, str]] = {
    "classic_light": THEME_CLASSIC_LIGHT,
    "lab_light": THEME_LAB_LIGHT,
    "graphite_focus": THEME_GRAPHITE_FOCUS,
    "warm_gray": THEME_WARM_GRAY,
    "deep_teal": THEME_DEEP_TEAL,
    "material_dark": THEME_MATERIAL_DARK,
    "dracula": THEME_DRACULA,
    "nord": THEME_NORD,
    "one_dark": THEME_ONE_DARK,
    "monokai": THEME_MONOKAI,
    "forest": THEME_FOREST,
    "high_contrast": THEME_HIGH_CONTRAST,
}

THEME_NAMES: dict[str, str] = {
    "classic_light": "当前风格",
    "lab_light": "实验室清爽",
    "graphite_focus": "深色专注",
    "warm_gray": "暖灰浅色",
    "deep_teal": "深青暗色",
    "material_dark": "材质深色",
    "dracula": "暗夜紫影",
    "nord": "北欧极光",
    "one_dark": "原子深色",
    "monokai": "暖褐复古",
    "forest": "翠绿护眼",
    "high_contrast": "高对比",
}

# Active token dict — updated in-place by apply_theme(); icons.py references
# this module-level dict so new icon calls always pick up the current palette.
TOKENS: dict[str, str] = dict(THEME_CLASSIC_LIGHT)

# ── Font stacks (web-parity fallback when bundled fonts absent) ────────────

# Stacks are ordered CJK-first so apply_default_font() picks a Chinese-capable
# family on every OS: Noto Sans CJK SC (Linux), Microsoft YaHei (Windows),
# PingFang SC (macOS). Latin/generic names trail as last-resort fallbacks.
_SANS_FONTS = (
    "Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC",
    "Microsoft YaHei", "微软雅黑", "SimHei", "黑体",
    "PingFang SC", "Hiragino Sans GB", "Heiti SC", "WenQuanYi Micro Hei",
    "Segoe UI", "Arial", "Calibri", "sans-serif",
)
_SERIF_FONTS = (
    "Noto Serif CJK SC", "Noto Serif SC", "Source Han Serif SC", "Songti SC",
    "STSong", "SimSun", "宋体", "NSimSun", "新宋体",
    "FangSong", "仿宋", "KaiTi", "楷体",
    "Times New Roman", "Times", "Georgia", "serif",
)
_MONO_FONTS = (
    "JetBrains Mono", "Cascadia Code", "SF Mono", "Consolas",
    "Courier New", "DejaVu Sans Mono", "monospace",
)

FONT_SANS = _SANS_FONTS
FONT_SERIF = _SERIF_FONTS
FONT_MONO = _MONO_FONTS


def _font_family(fonts: tuple[str, ...]) -> str:
    return ", ".join(f'"{f}"' if " " in f else f for f in fonts)


def local_font_css() -> str:
    """Return ``font-family:...`` CSS for view/widget local stylesheets.

    Ensures CJK-capable font is used even when a view applies its own
    ``setStyleSheet()`` which may shadow the global ``QWidget`` rule.
    """
    sans_stack = ((_FONT_FAMILY,) + FONT_SANS) if _FONT_FAMILY else FONT_SANS
    return f"font-family: {_font_family(sans_stack)};"


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


def apply_default_font(app) -> Optional[str]:
    """Set the app's default font to the first *installed* family in FONT_SANS.

    Qt's default ("Ubuntu" on this distro) lacks CJK glyphs, so initial widget
    layout is measured with the wrong metrics and CJK falls back unpredictably
    — the cause of the startup text-overlap and garbled-glyph reports. Pinning
    the default font to a real installed CJK family up front (before any window
    is built) makes first-paint metrics match the QSS font, killing both. Picks
    per-platform automatically (Noto Sans CJK SC on Linux, Microsoft YaHei on
    Windows, PingFang SC on macOS). Size is left at the system default — the
    QSS ``font-size`` rules still drive on-screen sizing.
    """
    try:
        from PyQt6.QtGui import QFont, QFontDatabase
    except Exception:
        return None
    families = set(QFontDatabase.families())
    chosen = None
    # User override wins when it's actually installed.
    if _FONT_FAMILY and _FONT_FAMILY in families:
        chosen = _FONT_FAMILY
    else:
        for fam in FONT_SANS:
            if fam == "sans-serif":
                break
            if fam in families:
                chosen = fam
                break
    if chosen is None:
        return None
    f = QFont(chosen)
    # Scale the base point size so widgets WITHOUT an explicit QSS font-size
    # (the inline-styled minority) track 字体大小 too. QSS font-size px still wins
    # for styled widgets.
    base_pt = f.pointSizeF()
    if base_pt <= 0:
        base_pt = 10.0
    f.setPointSizeF(base_pt * _FONT_SCALE)
    app.setFont(f)
    return chosen


def build_qss() -> str:
    """Generate the full Qt stylesheet from the active TOKENS dict."""
    t = TOKENS
    # User-chosen family (if any) leads the stack so it wins, with the CJK-first
    # fallbacks still trailing for glyph coverage.
    sans_stack = ((_FONT_FAMILY,) + FONT_SANS) if _FONT_FAMILY else FONT_SANS
    sans = _font_family(sans_stack)
    serif = _font_family(FONT_SERIF)
    mono = _font_family(FONT_MONO)

    # Gradient shorthands (reused across many rules). In performance mode the
    # large surface gradients (canvas/panel/topbar) collapse to flat solids —
    # gradient fills over large repainted areas are costly on remote desktops.
    # Small accent-button gradients stay (negligible cost, affordance signal).
    from app.config import effects as _fx
    perf = _fx.PERFORMANCE_MODE
    canvas_grad = t['bg_grad_top'] if perf else (
        f"qlineargradient(x1:0, y1:0, x2:0, y2:1,"
        f" stop:0 {t['bg_grad_top']}, stop:1 {t['bg_grad_bottom']})"
    )
    panel_grad = t['panel'] if perf else (
        f"qlineargradient(x1:0, y1:0, x2:0, y2:1,"
        f" stop:0 {t['panel_top']}, stop:0.06 {t['panel']}, stop:1 {t['panel_bottom']})"
    )
    panel2_grad = t['panel_2_top'] if perf else (
        f"qlineargradient(x1:0, y1:0, x2:0, y2:1,"
        f" stop:0 {t['panel_2_top']}, stop:1 {t['panel_2_bottom']})"
    )
    accent_grad = (
        f"qlineargradient(x1:0, y1:0, x2:0, y2:1,"
        f" stop:0 {t['accent_top']}, stop:1 {t['accent_bottom']})"
    )
    accent_grad_hover = (
        f"qlineargradient(x1:0, y1:0, x2:0, y2:1,"
        f" stop:0 {t['accent_hover']}, stop:1 {t['accent']})"
    )
    topbar_grad = t['topbar_top'] if perf else (
        f"qlineargradient(x1:0, y1:0, x2:0, y2:1,"
        f" stop:0 {t['topbar_top']}, stop:1 {t['topbar_bottom']})"
    )

    return f"""
/* ══════════════════════════════════════════════════════════════════
   Specimen Imaging Workbench — Theme System
   Auto-generated by app/config/theme.py — do not hand-edit
   Rhythm: 4/8/12/16/20/24 spacing · 8/12/14 radius · 11/12/13/15/18/22 type
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
QMainWindow {{ background-color: {t["bg"]}; }}
QWidget#AppShell {{ background: {canvas_grad}; }}
QToolTip {{
    background-color: {t["modal_surface_raised"]};
    color: {t["text"]};
    border: 1px solid {t["border_medium"]};
    border-radius: {t["radius_sm"]};
    padding: 6px 10px;
    font-size: {t["font_sm"]};
}}

/* ── Top bar (brand + segmented nav + global actions) ───────────── */
QFrame#TopBar {{
    background: {t["topbar_top"]};
    border: none;
    border-bottom: 1px solid {t["topbar_border"]};
}}
QFrame#TopBarDivider {{ background: {t["border_medium"]}; border: none; }}
QLabel#BrandWord {{
    font-family: {serif};
    font-size: {t["font_body"]};
    font-weight: 600;
    color: {t["text"]};
    letter-spacing: 0.2px;
}}
QLabel#BrandMark {{ color: {t["accent"]}; }}

/* Segmented navigation — flat buttons, 2px accent underline when active */
QPushButton#NavSegment {{
    background: transparent;
    border: 1px solid transparent;
    color: {t["nav_segment_text"]};
    font-size: {t["font_sm"]};
    font-weight: 500;
    padding: 7px 9px;
    margin: 0 2px;
    border-radius: {t["radius_sm"]};
    letter-spacing: 0;
}}
QPushButton#NavSegment:hover {{
    color: {t["text"]};
    background-color: {t["nav_segment_hover_bg"]};
    border-color: {t["border"]};
}}
QPushButton#NavSegment:checked {{
    color: {t["accent_hover"]};
    background-color: {t["nav_selected_bg"]};
    border-color: transparent;
    font-weight: 600;
}}

/* Icon-only ghost buttons (theme toggle / settings cog) */
QPushButton#IconGhost {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {t["radius_sm"]};
    color: {t["muted"]};
    padding: 0;
}}
QPushButton#IconGhost:hover {{
    background-color: {t["nav_segment_hover_bg"]};
    border-color: {t["border"]};
}}
QToolButton#ScreenshotTool {{
    background-color: {t["panel"]};
    color: {t["text_soft"]};
    border: 1px solid {t["border_medium"]};
    border-radius: {t["radius_sm"]};
    padding: 0 8px;
    font-size: {t["font_sm"]};
    font-weight: 600;
}}
QToolButton#ScreenshotTool:hover {{
    border-color: {t["accent"]};
    color: {t["text"]};
    background-color: {t["accent_softer"]};
}}
QToolButton#ScreenshotTool::menu-button {{
    border: none;
    width: 14px;
}}

/* ── Context bar (project switcher + active badge + quick actions) ─ */
QFrame#ContextBar {{
    background-color: {t["contextbar_bg"]};
    border: none;
    border-bottom: 1px solid {t["topbar_border"]};
}}
QLabel#ContextLabel {{
    color: {t["muted_dim"]};
    font-size: {t["font_xs"]};
    font-weight: 600;
    letter-spacing: 0.08em;
}}
QPushButton#ProjectSwitcher {{
    background-color: {t["panel_2"]};
    border: 1px solid {t["border"]};
    border-radius: {t["radius_sm"]};
    padding: 6px 12px;
    color: {t["text"]};
    font-size: {t["font_sm"]};
    font-weight: 600;
    text-align: left;
}}
QPushButton#ProjectSwitcher:hover {{
    border-color: {t["accent"]};
    background-color: {t["modal_surface"]};
}}
/* 工作区面包屑（EOS Utility 式）：祖先段扁平、叶子似旧 switcher、◀▶ 紧凑 */
QPushButton#CrumbSeg {{
    background: transparent;
    border: none;
    padding: 6px 4px;
    color: {t["muted"]};
    font-size: {t["font_sm"]};
}}
QPushButton#CrumbSeg:hover {{
    color: {t["accent"]};
}}
QLabel#CrumbSep {{
    color: {t["muted_dim"]};
    font-size: {t["font_sm"]};
    padding: 0 1px;
}}
QPushButton#CrumbLeaf {{
    background-color: {t["panel_2"]};
    border: 1px solid {t["border"]};
    border-radius: {t["radius_sm"]};
    padding: 6px 10px;
    color: {t["text"]};
    font-size: {t["font_sm"]};
    font-weight: 600;
    text-align: left;
}}
QPushButton#CrumbLeaf:hover {{
    border-color: {t["accent"]};
    background-color: {t["modal_surface"]};
}}
QToolButton#CrumbArrow {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {t["radius_sm"]};
    padding: 4px 3px;
    color: {t["muted"]};
    font-size: {t["font_xs"]};
}}
QToolButton#CrumbArrow:hover {{
    color: {t["accent"]};
    border-color: {t["border"]};
}}
QToolButton#CrumbArrow:disabled {{
    color: {t["muted_dim"]};
}}
QLabel#ActiveBadgeOn {{
    background: {accent_grad};
    color: {t["bg"]};
    border-radius: {t["radius_pill"]};
    padding: 5px 14px;
    font-family: {mono};
    font-size: {t["font_sm"]};
    font-weight: 700;
    letter-spacing: 0.5px;
}}
QLabel#ActiveBadgeOff {{
    background-color: {t["panel_inset"]};
    color: {t["muted_dim"]};
    border: 1px solid {t["border"]};
    border-radius: {t["radius_pill"]};
    padding: 4px 14px;
    font-family: {mono};
    font-size: {t["font_sm"]};
    font-weight: 600;
}}

QStackedWidget {{ background: transparent; border: none; }}

/* ── Workspace header ────────────────────────────────────────────── */
QLabel#WorkspaceTitle {{
    font-family: {sans};
    font-size: {t["font_lg"]};
    font-weight: 600;
    color: {t["text"]};
    letter-spacing: 0;
}}
QLabel#TagSea, QLabel#TagWarn, QLabel#TagOk {{
    border-radius: {t["radius_pill"]};
    padding: 4px 12px;
    font-size: {t["font_xs"]};
    font-weight: 600;
    letter-spacing: 0.3px;
}}
QLabel#TagSea {{ background-color: {t["accent_soft"]}; color: {t["accent_hover"]}; }}
QLabel#TagWarn {{ background-color: {t["warn_soft"]}; color: {t["warn"]}; }}
QLabel#TagOk {{ background-color: {t["success_soft"]}; color: {t["success"]}; }}

/* ── Directory info strip ────────────────────────────────────────── */
QFrame#DirStrip {{
    background: {panel2_grad};
    border: 1px solid {t["border"]};
    border-radius: {t["radius"]};
}}
QLabel#DirLabel {{
    color: {t["muted_dim"]};
    font-size: {t["font_xs"]};
    font-weight: 600;
    letter-spacing: 0.04em;
}}
QLabel#DirPath {{
    font-family: {mono};
    font-size: {t["font_xs"]};
    color: {t["muted"]};
    background-color: transparent;
    border-radius: {t["radius_sm"]};
    padding: 2px 0;
}}

/* ── Panels / cards / sections ───────────────────────────────────── */
/* Cards: vertical surface gradient + faint hairline + 1px inner top lip. */
QFrame#Panel, QFrame#WorkbenchSection, QFrame#PanelCard {{
    background: {panel_grad};
    border: 1px solid {t["border"]};
    border-radius: {t["radius_lg"]};
}}
QFrame#Card {{
    background: {t["panel_2"]};
    border: 1px solid {t["border"]};
    border-radius: {t["radius"]};
}}
QFrame#Card:hover {{ border-color: {t["border_medium"]}; }}
QFrame#NamingGroup {{
    background: {t["panel_2"]};
    border: 1px solid {t["border"]};
    border-radius: {t["radius"]};
}}
QFrame#NamingPreviewGroup {{
    background: {t["accent_softer"]};
    border: 1px solid {t["accent_glow"]};
    border-radius: {t["radius"]};
}}
QLabel#NamingGroupTitle {{
    color: {t["muted"]};
    font-size: {t["font_xs"]};
    font-weight: 700;
    letter-spacing: 0.06em;
}}
QLabel#CompactFieldLabel {{
    color: {t["text_soft"]};
    font-size: {t["font_xs"]};
    font-weight: 600;
}}
QToolButton#CompactIconButton {{
    background: transparent;
    border: 1px solid {t["border"]};
    border-radius: {t["radius_sm"]};
    padding: 3px;
}}
QToolButton#CompactIconButton:hover {{
    background: {t["accent_softer"]};
    border-color: {t["accent"]};
}}
QFrame#BatchIdentBar {{
    background: {panel2_grad};
    border: 1px solid {t["border_medium"]};
    border-top: 1px solid {t["edge_highlight"]};
    border-radius: {t["radius"]};
}}
QFrame#Divider {{ background-color: {t["border"]}; max-height: 1px; min-height: 1px; border: none; }}

/* ── Project settings drawer (right-edge overlay) + backdrop scrim ─── */
QWidget#SettingsDrawer {{
    background: {panel_grad};
    border-left: 1px solid {t["border"]};
}}
QWidget#DrawerScrim {{ background: rgba(4, 11, 14, 0.55); }}

/* ── Labels ──────────────────────────────────────────────────────── */
QLabel {{ background: transparent; color: {t["text"]}; }}
QLabel#Muted {{ color: {t["muted"]}; }}
QLabel#MutedSmall {{ color: {t["muted_dim"]}; font-size: {t["font_xs"]}; }}
QLabel#Accent {{ color: {t["accent"]}; }}
QLabel#Title {{ font-family: {serif}; font-size: {t["font_title"]}; font-weight: 600; color: {t["text"]}; }}
QLabel#Section, QLabel#CardTitle {{
    font-size: {t["font_xs"]};
    font-weight: 700;
    color: {t["muted"]};
    letter-spacing: 0.1em;
}}
QLabel#Placeholder {{ color: {t["muted"]}; font-size: {t["font_md"]}; }}

QLabel#BatchUid {{
    font-family: {mono};
    color: {t["accent_hover"]};
    font-size: {t["font_body"]};
    font-weight: 600;
}}
QLabel#ActivateState {{
    color: {t["muted_dim"]};
    font-size: {t["font_xs"]};
    padding: 3px 11px;
    border-radius: {t["radius_pill"]};
    background-color: {t["panel_inset"]};
    border: 1px solid {t["border"]};
}}
QLabel#ActivateStateOn {{
    color: {t["accent_hover"]};
    font-size: {t["font_xs"]};
    font-weight: 600;
    padding: 3px 11px;
    border-radius: {t["radius_pill"]};
    background-color: {t["accent_soft"]};
    border: 1px solid {t["accent_glow"]};
}}
QLabel#StatValue {{ font-size: {t["font_title"]}; font-weight: 700; color: {t["text"]}; }}
QLabel#StatLabel {{ font-size: {t["font_xs"]}; color: {t["muted_dim"]}; letter-spacing: 0.03em; }}

/* ── 5-state attribution chips ─────────────────────────────────── */
QLabel#ChipRaw, QLabel#ChipAttributed, QLabel#ChipComposed,
QLabel#ChipArchived, QLabel#ChipTiff, QLabel#ChipUnattributed {{
    border-radius: {t["radius_pill"]};
    font-size: {t["font_xs"]};
    font-weight: 600;
    padding: 2px 10px;
    letter-spacing: 0.2px;
}}
QLabel#ChipRaw {{ background-color: {t["warn_soft"]}; color: {t["warn"]}; }}
QLabel#ChipAttributed {{ background-color: {t["success_soft"]}; color: {t["success"]}; }}
QLabel#ChipComposed {{ background-color: {t["info_soft"]}; color: {t["info"]}; }}
QLabel#ChipArchived {{ background-color: rgba(135,162,161,0.16); color: {t["muted"]}; }}
QLabel#ChipTiff {{ background-color: {t["success_soft"]}; color: {t["success"]}; }}
QLabel#ChipUnattributed {{ background-color: {t["danger_soft"]}; color: {t["danger"]}; }}

/* ── Buttons (height 32-34, unified radius, micro-gradient) ───────── */
QPushButton {{
    background-color: {t["bg_raised"]};
    color: {t["text_soft"]};
    border: 1px solid {t["border_medium"]};
    border-radius: {t["radius_sm"]};
    padding: 6px 12px;
    font-size: {t["font_sm"]};
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
    background: {accent_grad};
    color: {t["bg"]};
    border: 1px solid {t["accent_bottom"]};
    border-top: 1px solid {t["accent_top"]};
    font-weight: 700;
}}
QPushButton#Primary:hover {{ background: {accent_grad_hover}; border-color: {t["accent_hover"]}; }}
QPushButton#Primary:pressed {{ background-color: {t["accent_pressed"]}; }}
QPushButton#Primary:disabled {{ background: {t["panel"]}; color: {t["muted_dim"]}; border-color: {t["border"]}; }}

QPushButton#Outline {{
    background-color: {t["panel"]};
    color: {t["text_soft"]};
    border: 1px solid {t["border_medium"]};
}}
QPushButton#Outline:hover {{ border-color: {t["accent"]}; color: {t["text"]}; background-color: {t["accent_softer"]}; }}

QPushButton#Ghost {{
    background-color: transparent;
    border: 1px solid transparent;
    color: {t["muted"]};
    padding: 6px 10px;
}}
QPushButton#Ghost:hover {{ color: {t["text"]}; background-color: {t["nav_selected_bg"]}; }}

QPushButton#Danger {{ background-color: transparent; color: {t["danger"]}; border: 1px solid rgba(230,110,99,0.45); }}
QPushButton#Danger:hover {{ background-color: {t["danger"]}; color: {t["bg"]}; border-color: {t["danger"]}; }}
QPushButton#Danger:disabled {{ color: {t["muted_dim"]}; border-color: {t["border"]}; background: transparent; }}

/* ── Phase pills ─────────────────────────────────────────────────── */
QPushButton#PhasePill, QPushButton#PhasePillActive {{
    border-radius: {t["radius_pill"]};
    padding: 2px 12px;
    font-size: {t["font_xs"]};
    font-weight: 600;
}}
QPushButton#PhasePill {{
    background-color: transparent;
    color: {t["muted"]};
    border: 1px solid {t["border_medium"]};
}}
QPushButton#PhasePill:hover {{ color: {t["text_soft"]}; border-color: {t["border_strong"]}; }}
QPushButton#PhasePill:checked, QPushButton#PhasePillActive:checked, QPushButton#PhasePillActive {{
    background-color: {t["accent_soft"]};
    color: {t["accent"]};
    border: 1px solid {t["accent"]};
}}

/* ── Sidebar per-编号 phase dots ─────────────────────────────────────
   4 round dots under each specimen UID = 拍摄中/已拍完/整理中/完成.
   Always show the phase colour as a ring; the current phase fills.
   Colours reuse per-theme semantic tokens so they track the active theme:
   蓝 info=shooting · 青 accent=shot_done · 橙 warn=organizing · 绿 success=done. */
QPushButton#PhaseDotShooting, QPushButton#PhaseDotShotDone,
QPushButton#PhaseDotOrganizing, QPushButton#PhaseDotDone {{
    min-width: 13px; max-width: 13px;
    min-height: 13px; max-height: 13px;
    padding: 0px;
    border-radius: 7px;
    background-color: transparent;
}}
QPushButton#PhaseDotShooting   {{ border: 2px solid {t["info"]}; }}
QPushButton#PhaseDotShotDone   {{ border: 2px solid {t["accent"]}; }}
QPushButton#PhaseDotOrganizing {{ border: 2px solid {t["warn"]}; }}
QPushButton#PhaseDotDone       {{ border: 2px solid {t["success"]}; }}
QPushButton#PhaseDotShooting:hover   {{ background-color: {t["info_soft"]}; }}
QPushButton#PhaseDotShotDone:hover   {{ background-color: {t["accent_soft"]}; }}
QPushButton#PhaseDotOrganizing:hover {{ background-color: {t["warn_soft"]}; }}
QPushButton#PhaseDotDone:hover       {{ background-color: {t["success_soft"]}; }}
QPushButton#PhaseDotShooting:checked   {{ background-color: {t["info"]}; }}
QPushButton#PhaseDotShotDone:checked   {{ background-color: {t["accent"]}; }}
QPushButton#PhaseDotOrganizing:checked {{ background-color: {t["warn"]}; }}
QPushButton#PhaseDotDone:checked       {{ background-color: {t["success"]}; }}

QPushButton#Tiny {{ padding: 3px 11px; font-size: {t["font_xs"]}; border-radius: {t["radius_sm"]}; }}

/* ── Sidebar collab-status strip ────────────────────────────────────── */
QFrame#CollabStrip {{
    background-color: {t["panel_2"]};
    border: 1px solid {t["border"]};
    border-top: 1px solid {t["edge_highlight_soft"]};
    border-radius: {t["radius_sm"]};
    margin-top: 8px;
}}

/* ── Storage-mode button group ─────────────────────────────────────── */
QPushButton#StorageBtn {{
    background-color: {t["panel_inset"]};
    color: {t["muted"]};
    border: 1px solid {t["border"]};
    border-radius: {t["radius_pill"]};
    padding: 3px 10px;
    font-size: {t["font_xs"]};
    font-weight: 600;
    font-family: {mono};
}}
QPushButton#StorageBtn:hover {{
    border-color: {t["accent"]};
    color: {t["text"]};
    background-color: {t["accent_softer"]};
}}
QPushButton#StorageBtn:checked {{
    background: {accent_grad};
    color: {t["bg"]};
    border-color: {t["accent_bottom"]};
    font-weight: 700;
}}

/* ── Drop target ─────────────────────────────────────────────────── */
QPushButton#DropTarget {{
    background-color: {t["panel_inset"]};
    border: 1px dashed {t["border_strong"]};
    border-radius: {t["radius"]};
    color: {t["accent_hover"]};
    font-weight: 600;
    padding: 10px 14px;
}}
QPushButton#DropTarget:hover {{ border-color: {t["accent"]}; background-color: {t["accent_soft"]}; }}
QPushButton#DropTarget:disabled {{ color: {t["muted_dim"]}; border-color: {t["border"]}; }}

/* ── Inputs ──────────────────────────────────────────────────────── */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {{
    background-color: {t["input_bg"]};
    color: {t["text"]};
    border: 1px solid {t["input_border"]};
    border-radius: {t["radius_sm"]};
    padding: 6px 9px;
    font-size: {t["font_body"]};
    selection-background-color: {t["accent"]};
    selection-color: {t["bg"]};
}}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover {{ border-color: {t["border_medium"]}; }}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {{
    border-color: {t["input_focus_border"]};
}}
QLineEdit:disabled {{ color: {t["muted"]}; background-color: {t["bg"]}; }}
QLineEdit#AutoField {{
    background-color: {t["input_bg_auto"]};
    color: {t["muted"]};
    border: 1px solid {t["input_border"]};
}}
QLineEdit#MonoInput {{ font-family: {mono}; }}
QLineEdit#StorageDisplay {{
    background-color: {t["input_bg_auto"]};
    color: {t["text"]};
    border: 1px solid {t["input_border"]};
}}
QLineEdit#StorageDisplay:focus {{ border-color: {t["input_border"]}; }}

/* ── Preview blocks ──────────────────────────────────────────────── */
QLabel#PreviewBlock {{
    font-family: {mono};
    font-size: {t["font_body"]};
    color: {t["accent_hover"]};
    background-color: {t["modal_surface"]};
    border: 1px solid {t["accent_glow"]};
    border-radius: {t["radius_sm"]};
    padding: 10px 12px;
}}
QLabel#PreviewEmpty {{
    font-family: {mono};
    font-size: {t["font_body"]};
    color: {t["muted_dim"]};
    background-color: {t["panel_inset"]};
    border: 1px dashed {t["border"]};
    border-radius: {t["radius_sm"]};
    padding: 10px 12px;
}}
QLabel#RnaWarning {{
    color: {t["warn"]};
    background-color: {t["warn_soft"]};
    border: 1px solid rgba(241,189,87,0.30);
    border-radius: {t["radius_sm"]};
    padding: 8px 11px;
    font-size: {t["font_sm"]};
    font-weight: 600;
}}
QLabel#UnattributedWarning {{
    color: {t["warn"]};
    background-color: {t["warn_soft"]};
    border: 1px solid rgba(241,189,87,0.26);
    border-radius: {t["radius_sm"]};
    padding: 7px 11px;
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
QComboBox:hover {{ border-color: {t["border_medium"]}; }}
QComboBox:focus {{ border-color: {t["input_focus_border"]}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background-color: {t["panel"]};
    color: {t["text"]};
    border: 1px solid {t["border_medium"]};
    border-radius: {t["radius_sm"]};
    selection-background-color: {t["nav_selected_bg"]};
    selection-color: {t["accent"]};
    padding: 4px;
    outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; border: none; background: {t["panel_inset"]}; }}

/* ── Scrollbars ──────────────────────────────────────────────────── */
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: {t["scrollbar_bg"]}; width: 10px; margin: 0; border: none; }}
QScrollBar::handle:vertical {{ background: {t["scrollbar_handle"]}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {t["scrollbar_handle_hover"]}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: {t["scrollbar_bg"]}; height: 10px; margin: 0; border: none; }}
QScrollBar::handle:horizontal {{ background: {t["scrollbar_handle"]}; border-radius: 5px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: {t["scrollbar_handle_hover"]}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* Top-bar nav scroll: slim horizontal bar so overflow scrolling stays unobtrusive. */
QScrollArea#NavScroll QScrollBar:horizontal {{ height: 5px; background: transparent; margin: 0; }}
QScrollArea#NavScroll QScrollBar::handle:horizontal {{ background: {t["border_strong"]}; border-radius: 2px; min-width: 28px; }}
QScrollArea#NavScroll QScrollBar::handle:horizontal:hover {{ background: {t["scrollbar_handle_hover"]}; }}

/* ── Splitter ────────────────────────────────────────────────────── */
QSplitter#WorkbenchSplitter::handle {{ background: transparent; }}
QSplitter::handle {{ background-color: transparent; }}
QSplitter::handle:hover {{ background-color: {t["accent_softer"]}; border-radius: 2px; }}
QSplitter::handle:horizontal {{ width: 12px; }}
QSplitter::handle:vertical {{ height: 12px; }}

/* ── Right-rail tabs ──────────────────────────────────────────────── */
QTabWidget#RightRailTabs::pane {{
    border: 1px solid {t["border"]};
    border-radius: {t["radius_lg"]};
    background: {t["panel_2"]};
    top: -1px;
}}
QTabWidget#RightRailTabs > QTabBar {{ qproperty-drawBase: 0; }}
QTabWidget#RightRailTabs QTabBar::tab {{
    color: {t["muted"]};
    background: transparent;
    border: 1px solid transparent;
    border-top-left-radius: {t["radius_sm"]};
    border-top-right-radius: {t["radius_sm"]};
    padding: 7px 16px;
    margin-right: 4px;
    font-size: {t["font_body"]};
}}
QTabWidget#RightRailTabs QTabBar::tab:hover {{ color: {t["text_soft"]}; }}
QTabWidget#RightRailTabs QTabBar::tab:selected {{
    color: {t["accent"]};
    background: {t["panel_2"]};
    border-color: {t["border"]};
    border-bottom-color: {t["panel_2"]};
    font-weight: 600;
}}

/* ── Specimen list ───────────────────────────────────────────────── */
QListWidget#SpecimenList {{
    background-color: transparent;
    border: 1px solid {t["border"]};
    border-radius: {t["radius"]};
    alternate-background-color: transparent;
    outline: none;
    padding: 5px;
}}
QListWidget#SpecimenList::item {{
    color: {t["text_soft"]};
    border: 1px solid transparent;
    border-radius: {t["radius_sm"]};
    padding: 9px 11px;
    margin: 2px 0;
}}
QListWidget#SpecimenList::item:hover {{ background-color: {t["bg_raised"]}; }}
QListWidget#SpecimenList::item:selected {{
    background-color: {t["nav_selected_bg"]};
    border: 1px solid {t["accent_glow"]};
    color: {t["text"]};
}}

/* ── Generic list ────────────────────────────────────────────────── */
QListWidget {{
    background-color: {t["panel"]};
    border: 1px solid {t["border"]};
    border-radius: {t["radius_sm"]};
    alternate-background-color: {t["panel_2"]};
    outline: none;
    padding: 3px;
}}
QListWidget::item {{ padding: 4px 8px; border-radius: {t["radius_sm"]}; color: {t["text_soft"]}; }}
QListWidget::item:hover {{ background-color: {t["bg_raised"]}; }}
QListWidget::item:selected {{ background-color: {t["nav_selected_bg"]}; color: {t["accent"]}; }}

/* ── Status bar ──────────────────────────────────────────────────── */
QStatusBar {{
    background-color: {t["statusbar_bg"]};
    color: {t["muted"]};
    border-top: 1px solid {t["border"]};
    font-size: {t["font_xs"]};
    padding: 2px 10px;
}}
QStatusBar::item {{ border: none; }}
QLabel#StatusSegment {{ color: {t["muted"]}; padding: 0 10px; font-size: {t["font_xs"]}; }}
QLabel#StatusSegmentAccent {{ color: {t["accent"]}; padding: 0 10px; font-size: {t["font_xs"]}; }}

/* ── Mono label (file paths / UIDs) ──────────────────────────────── */
QLabel#Mono {{
    font-family: {mono};
    font-size: {t["font_xs"]};
    color: {t["accent"]};
    background-color: {t["modal_surface"]};
    border-radius: {t["radius_sm"]};
    padding: 3px 8px;
}}

/* ── Menu ────────────────────────────────────────────────────────── */
QMenu {{
    background-color: {t["panel"]};
    color: {t["text"]};
    border: 1px solid {t["border_medium"]};
    border-radius: {t["radius_sm"]};
    padding: 6px;
}}
QMenu::item {{ padding: 7px 22px; border-radius: {t["radius_sm"]}; }}
QMenu::item:selected {{ background-color: {t["nav_selected_bg"]}; color: {t["accent"]}; }}
QMenu::separator {{ height: 1px; background-color: {t["border"]}; margin: 5px 8px; }}

/* ── CheckBox ────────────────────────────────────────────────────── */
QCheckBox, QRadioButton {{ color: {t["text_soft"]}; spacing: 8px; font-size: {t["font_body"]}; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {t["border_medium"]};
    border-radius: 5px;
    background-color: {t["input_bg"]};
}}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {t["accent"]}; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background-color: {t["accent"]}; border-color: {t["accent"]}; }}

/* ── Table / Tree ────────────────────────────────────────────────── */
QTableWidget, QTableView, QTreeView {{
    background-color: {t["panel"]};
    color: {t["text"]};
    border: 1px solid {t["border"]};
    border-radius: {t["radius_sm"]};
    gridline-color: {t["border"]};
    alternate-background-color: {t["panel_2"]};
    selection-background-color: {t["nav_selected_bg"]};
    selection-color: {t["accent"]};
}}
QHeaderView::section {{
    background-color: {t["panel_2"]};
    color: {t["muted"]};
    border: none;
    border-bottom: 1px solid {t["border"]};
    padding: 6px 11px;
    font-size: {t["font_xs"]};
    font-weight: 700;
    letter-spacing: 0.06em;
}}

/* ── Slider ──────────────────────────────────────────────────── */
QSlider::groove:horizontal {{
    height: 4px;
    background: {t["border_medium"]};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {t["accent"]};
    border-radius: 2px;
    height: 4px;
}}
QSlider::handle:horizontal {{
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
    background: {t["accent"]};
    border: 2px solid {t["panel"]};
}}
QSlider::handle:horizontal:hover {{
    background: {t["accent_hover"]};
}}
QSlider::handle:horizontal:pressed {{
    background: {t["accent_pressed"]};
}}
"""


_QSS_OUTPUT = Path(__file__).parent.parent.parent / "resources" / "theme.qss"


def apply_theme(name: str) -> str:
    """Switch TOKENS to the named theme and return the new QSS string.

    Updates TOKENS in-place so icons.py and any other module that holds a
    reference to TOKENS picks up the new palette on its next call.
    """
    tokens = THEMES.get(name, THEME_CLASSIC_LIGHT)
    TOKENS.update(tokens)
    TOKENS.update(_scaled_typo())   # re-apply current 字体大小 over theme defaults
    return build_qss()


def build_theme_qss_file(name: str = "classic_light") -> Path:
    """Apply theme, write theme.qss to resources/, return its path."""
    qss = apply_theme(name)
    _QSS_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    _QSS_OUTPUT.write_text(qss, encoding="utf-8")
    return _QSS_OUTPUT
