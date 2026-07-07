"""Shared constants and theme palette for the taxonomy view."""
from __future__ import annotations

from pathlib import Path
from typing import Any

# ── Theme colours — resolved from the LIVE active theme ───────────────────────
# Previously these were hardcoded deep-teal constants, which force-painted the
# whole 内置分类库 page dark regardless of the chosen theme → under a light theme
# the table text / labels (which the theme paints dark) became invisible.  Now
# they are refreshed from the active theme tokens by _refresh_palette(), called
# at the top of _setup_ui() and at the start of every standalone widget/dialog
# defined in this file, so each f-string picks up the live palette.
_C_PANEL = "#10242a"
_C_INPUT = "#061c1e"
_C_TEXT = "#eef3ef"
_C_TEXT_SOFT = "#cfe0db"
_C_MUTED = "#87a2a1"
_C_DIM = "#5f7d7a"
_C_ACCENT = "#29b9ab"
_C_ACCENT_HI = "#31d4c4"
_C_DANGER = "#e66e63"
_C_BORDER = "rgba(145, 182, 181, 0.18)"
_C_ACCENT_SOFT = "rgba(41, 185, 171, 0.10)"
_C_DANGER_SOFT = "rgba(230, 110, 99, 0.10)"


def _refresh_palette() -> None:
    """Rebind the module `_C_*` colours to the current theme tokens."""
    global _C_PANEL, _C_INPUT, _C_TEXT, _C_TEXT_SOFT
    global _C_MUTED, _C_DIM, _C_ACCENT, _C_ACCENT_HI
    global _C_DANGER, _C_BORDER, _C_ACCENT_SOFT, _C_DANGER_SOFT
    from app.config.theme import TOKENS
    g = TOKENS.get
    _C_PANEL = g("panel", _C_PANEL)
    _C_INPUT = g("input_bg", _C_INPUT)
    _C_TEXT = g("text", _C_TEXT)
    _C_TEXT_SOFT = g("text_soft", _C_TEXT_SOFT)
    _C_MUTED = g("muted", _C_MUTED)
    _C_DIM = g("muted_dim", _C_DIM)
    _C_ACCENT = g("accent", _C_ACCENT)
    _C_ACCENT_HI = g("accent_hover", _C_ACCENT_HI)
    _C_DANGER = g("danger", _C_DANGER)
    _C_BORDER = g("border", _C_BORDER)
    _C_ACCENT_SOFT = g("accent_soft", _C_ACCENT_SOFT)
    _C_DANGER_SOFT = g("danger_soft", _C_DANGER_SOFT)


# ── Paths ─────────────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent           # app/views/
_PROJECT_ROOT = _HERE.parent.parent               # photo-platform-ydy-v3/
_DATA_DIR = _PROJECT_ROOT / "data"

_DEFAULT_SEED_PATH = _DATA_DIR / "taxonomy_seed.json"
_DEFAULT_USER_PATH = _DATA_DIR / "user_taxonomy.json"

# Page size mirrors the web default (server.js limit)
_PAGE_SIZE = 50

# ── Column definitions ────────────────────────────────────────────────────────
# (display_label, record_key, show_in_original, show_in_worms)
# These mirror getVisibleTaxonColumns() logic.

_ALL_COLS: list[dict[str, Any]] = [
    {"label": "纲(中)",   "key": "classCn",       "level": "taxonGroup", "lang": "cn"},
    {"label": "纲(拉丁)",  "key": "class",         "level": "taxonGroup", "lang": "latin"},
    {"label": "目(中)",   "key": "orderCn",        "level": "order",     "lang": "cn"},
    {"label": "目(拉丁)",  "key": "order",         "level": "order",     "lang": "latin"},
    {"label": "科(中)",   "key": "familyCn",       "level": "family",    "lang": "cn"},
    {"label": "科(拉丁)",  "key": "family",        "level": "family",    "lang": "latin"},
    {"label": "属(中)",   "key": "genusCn",        "level": "genus",     "lang": "cn"},
    {"label": "属(拉丁)",  "key": "genus",         "level": "genus",     "lang": "latin"},
    {"label": "种(中)",   "key": "speciesCn",      "level": "species",   "lang": "cn"},
    {"label": "种(拉丁)",  "key": "species",       "level": "species",   "lang": "latin"},
]

# Level keys available as column-group chips
_LEVEL_CHIPS: list[tuple[str, str]] = [
    ("order",   "目"),
    ("family",  "科"),
    ("genus",   "属"),
    ("species", "种"),
]
_LANG_CHIPS: list[tuple[str, str]] = [
    ("cn",    "中文"),
    ("latin", "拉丁名"),
]

# ── Column index constants ────────────────────────────────────────────────────
_COL_CHECK = 0   # checkbox column (taxon-th-check)
_COL_NUM   = 1   # row number column (taxon-th-num / #)
# dynamic data columns start at _COL_DATA_START
_COL_DATA_START = 2


# ── Add/Edit dialog ───────────────────────────────────────────────────────────

_DIALOG_FIELDS: list[tuple[str, str, bool]] = [
    ("class",     "纲 / 门（Latin）",   True),
    ("order",     "目（Latin）",         True),
    ("family",    "科（Latin）",         True),
    ("species",   "种（Latin）",         True),
    ("classCn",   "纲中文",              False),
    ("orderCn",   "目中文",              False),
    ("familyCn",  "科中文",              False),
    ("speciesCn", "种中文",              False),
    ("genus",     "属（Latin）",         False),
    ("genusCn",   "属中文",              False),
]
