from __future__ import annotations


def test_lightweight_mode_uses_native_icon_without_qtawesome(qapp, monkeypatch):
    from app.config import icons

    monkeypatch.setattr(
        icons._qta,
        "icon",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("qtawesome loaded")),
    )
    icons.set_lightweight_mode(True)
    try:
        result = icons.icon("mdi6.folder-outline")
        assert not result.isNull()
    finally:
        icons.set_lightweight_mode(False)


def test_lightweight_unknown_icon_degrades_to_null(qapp):
    from app.config import icons

    icons.set_lightweight_mode(True)
    try:
        assert icons.icon("mdi6.microscope").isNull()
    finally:
        icons.set_lightweight_mode(False)
