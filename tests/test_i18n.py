"""test_i18n.py — runtime translation core.

tr(中文源串) → 当前语言译文；缺失回退中文（永不半坏 UI）。
中文为源 key：zh 直接返回；en 查 catalog。语言可切，重启生效。
"""
from __future__ import annotations

import pytest

from app.config import i18n


@pytest.fixture(autouse=True)
def _reset_lang():
    yield
    i18n.set_language("zh")        # never leak language state across tests


def test_zh_returns_source_verbatim():
    i18n.set_language("zh")
    assert i18n.tr("选择文件") == "选择文件"
    assert i18n.current_language() == "zh"


def test_en_lookup(monkeypatch):
    monkeypatch.setitem(i18n._CATALOGS, "en", {"选择文件": "Select file"})
    i18n.set_language("en")
    assert i18n.tr("选择文件") == "Select file"


def test_en_missing_key_falls_back_to_source(monkeypatch):
    monkeypatch.setitem(i18n._CATALOGS, "en", {"选择文件": "Select file"})
    i18n.set_language("en")
    assert i18n.tr("尚未翻译的串") == "尚未翻译的串"   # graceful fallback, not blank


def test_unsupported_language_falls_back_to_zh():
    i18n.set_language("fr")
    assert i18n.current_language() == "zh"


def test_empty_string_is_safe():
    i18n.set_language("en")
    assert i18n.tr("") == ""


def test_real_en_catalog_loads_and_has_entries():
    """The shipped resources/i18n/en.json must load and cover core strings."""
    i18n.set_language("en")
    cat = i18n._load_catalog("en")
    assert isinstance(cat, dict)
    assert cat, "en.json must ship with at least the round-1 strings"
    # a string we know is converted in round 1
    assert i18n.tr("取消") != "取消"
