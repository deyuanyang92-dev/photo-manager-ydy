"""icons.py — qtawesome vector-icon helper (Material Design Icons / Feather).

Premium UIs use crisp monochrome vector icons, not emoji.  This module wraps
qtawesome with the project's teal-on-dark palette and a graceful fallback: if
qtawesome (or its Qt-SVG backend) is unavailable, ``icon()`` returns a null
QIcon and ``glyph()`` returns the supplied text fallback, so the app never
crashes on a machine without the dependency.

Public API
----------
    icon(name, color=None, **kw) -> QIcon
        A themed QIcon for a Material/Feather glyph (e.g. "mdi6.microscope").
    set_button_icon(btn, name, color=None, size=16)
        Apply a themed icon (+ icon size) to a QPushButton in one call.
    available() -> bool
        Whether qtawesome resolved successfully at import time.

Icon names follow qtawesome's "<font>.<glyph>" convention; we standardise on
Material Design Icons 6 ("mdi6.*") with a few Font-Awesome-5 solids ("fa5s.*").
"""
from __future__ import annotations

from typing import Optional

from app.config.theme import TOKENS

# Import qtawesome; degrade gracefully when missing.  NOTE: we must NOT probe
# an icon at import time — qtawesome needs a live QApplication to rasterise, and
# this module is imported while widget classes are defined (before the app
# exists).  Availability is therefore resolved lazily on first real use.
try:  # pragma: no cover - import guard
    import qtawesome as _qta  # type: ignore

    _AVAILABLE = True
except Exception:  # pragma: no cover - fallback path
    _qta = None  # type: ignore
    _AVAILABLE = False


# Default monochrome tones (hex, qtawesome wants concrete colours not rgba()).
_DEFAULT = "#cfe0db"          # text_soft — neutral default
_MUTED = TOKENS["muted"]       # secondary glyphs
_ACCENT = TOKENS["accent"]     # interactive / active
_ACCENT_HOVER = TOKENS["accent_hover"]
_ON_ACCENT = TOKENS["bg"]      # glyph sitting on an accent fill
_DANGER = TOKENS["danger"]
_WARN = TOKENS["warn"]
_SUCCESS = TOKENS["success"]


def available() -> bool:
    """Return True when qtawesome icons can be rendered."""
    return _AVAILABLE


# qtawesome glyph rendering is expensive; the same (name, color) icons are
# requested hundreds of times (e.g. one per monitor card). Cache by spec.
# Tone tokens are snapshotted at import (see TONE_* below), so a given colour
# string always maps to the same glyph — caching changes nothing visually.
_ICON_CACHE: dict = {}
LIGHTWEIGHT_MODE = False


def set_lightweight_mode(enabled: bool) -> None:
    """Use cheap native Qt icons instead of loading all qtawesome fonts."""
    global LIGHTWEIGHT_MODE
    LIGHTWEIGHT_MODE = bool(enabled)


def _native_icon(name: str):
    from PyQt6.QtWidgets import QApplication, QStyle
    from PyQt6.QtGui import QIcon

    app = QApplication.instance()
    if app is None:
        return QIcon()
    key = str(name or "").lower()
    mappings = (
        (("folder-open", "folder-outline", "folder"), QStyle.StandardPixmap.SP_DirIcon),
        (("update", "refresh", "reload"), QStyle.StandardPixmap.SP_BrowserReload),
        (("save",), QStyle.StandardPixmap.SP_DialogSaveButton),
        (("trash", "delete"), QStyle.StandardPixmap.SP_TrashIcon),
        (("arrow-left", "chevron-left"), QStyle.StandardPixmap.SP_ArrowLeft),
        (("arrow-right", "chevron-right"), QStyle.StandardPixmap.SP_ArrowRight),
        (("arrow-up", "chevron-up"), QStyle.StandardPixmap.SP_ArrowUp),
        (("arrow-down", "chevron-down"), QStyle.StandardPixmap.SP_ArrowDown),
        (("close", "cancel"), QStyle.StandardPixmap.SP_DialogCancelButton),
        (("check", "apply"), QStyle.StandardPixmap.SP_DialogApplyButton),
        (("computer", "desktop"), QStyle.StandardPixmap.SP_ComputerIcon),
        (("harddisk", "drive"), QStyle.StandardPixmap.SP_DriveHDIcon),
        (("file", "document"), QStyle.StandardPixmap.SP_FileIcon),
    )
    for needles, standard_pixmap in mappings:
        if any(needle in key for needle in needles):
            return app.style().standardIcon(standard_pixmap)
    return QIcon()


def icon(name: str, color: Optional[str] = None, **kw):
    """Return a themed QIcon for *name*, or a null QIcon when unavailable.

    Parameters
    ----------
    name:
        qtawesome glyph spec, e.g. ``"mdi6.microscope"``.
    color:
        Hex colour for the glyph; defaults to the neutral text tone.  Pass
        an accent/danger/etc. token for emphasis.
    kw:
        Forwarded to ``qtawesome.icon`` (e.g. ``color_active=...``).
    """
    if LIGHTWEIGHT_MODE:
        return _native_icon(name)
    if not _AVAILABLE:
        from PyQt6.QtGui import QIcon
        return QIcon()
    key = (name, color or _DEFAULT, tuple(sorted(kw.items())))
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached
    opts = {"color": color or _DEFAULT}
    opts.update(kw)
    try:
        result = _qta.icon(name, **opts)
    except Exception:
        from PyQt6.QtGui import QIcon
        result = QIcon()
    _ICON_CACHE[key] = result
    return result


def set_button_icon(btn, name: str, color: Optional[str] = None,
                    size: int = 16, **kw) -> None:
    """Apply a themed icon + icon-size to a QAbstractButton in one call."""
    from PyQt6.QtCore import QSize
    btn.setIcon(icon(name, color=color, **kw))
    btn.setIconSize(QSize(size, size))


# ── Semantic palette shortcuts (so callers stay declarative) ────────────────

TONE_DEFAULT = _DEFAULT
TONE_MUTED = _MUTED
TONE_ACCENT = _ACCENT
TONE_ACCENT_HOVER = _ACCENT_HOVER
TONE_ON_ACCENT = _ON_ACCENT
TONE_DANGER = _DANGER
TONE_WARN = _WARN
TONE_SUCCESS = _SUCCESS
