"""i18n.py — lightweight runtime translation for the desktop UI.

Dict-based, Chinese-source-as-key:

  * ``tr("选择文件")`` returns the current-language translation, or the Chinese
    source verbatim when no translation exists — so an un-translated string
    degrades to Chinese instead of a blank/garbled label (never a half-broken UI).
  * Chinese (``zh``) is the source language: ``tr`` short-circuits and returns the
    argument untouched.
  * English (``en``) translations live in ``resources/i18n/en.json`` (a shipped
    data file — must be in git, like the WoRMS/taxonomy seed data).

The current language is persisted in settings (``Settings.current_language``).
Views that expose ``retranslate_ui()`` can rebuild or refresh themselves after
``set_language`` so language changes apply immediately.

Mirrors the live-theme pattern (``apply_theme`` / ``current_theme``) but for text.
"""
from __future__ import annotations

import json
from pathlib import Path

#: Languages the UI can switch between.
SUPPORTED = ("zh", "en")

_LANG = "zh"
_CATALOGS: dict[str, dict[str, str]] = {"zh": {}}
_I18N_DIR = Path(__file__).resolve().parents[2] / "resources" / "i18n"


def _load_catalog(lang: str) -> dict[str, str]:
    """Return the (cached) translation dict for *lang*; zh is always empty."""
    if lang in _CATALOGS:
        return _CATALOGS[lang]
    data: dict[str, str] = {}
    if lang != "zh":
        try:
            raw = json.loads((_I18N_DIR / f"{lang}.json").read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = {str(k): str(v) for k, v in raw.items()}
        except Exception:
            data = {}
    _CATALOGS[lang] = data
    return data


def set_language(lang: str | None) -> None:
    """Set the active UI language ("zh"/"en"); unknown values fall back to zh."""
    global _LANG
    _LANG = lang if lang in SUPPORTED else "zh"
    _load_catalog(_LANG)


def current_language() -> str:
    return _LANG


def tr(text: str) -> str:
    """Translate *text* into the active language, falling back to the source."""
    if _LANG == "zh" or not text:
        return text
    return _load_catalog(_LANG).get(text, text)
