import re


def test_font_family_quotes_real_font_names() -> None:
    from app.config import theme

    css = theme._font_family(("微软雅黑", "Noto Sans CJK SC", "sans-serif"))

    assert '"微软雅黑"' in css
    assert '"Noto Sans CJK SC"' in css
    assert css.endswith("sans-serif")


def test_page_title_uses_same_sans_stack_as_body() -> None:
    from app.config import theme

    qss = theme.apply_theme("classic_light")
    match = re.search(r"QLabel#Title \{([^}]*)\}", qss)

    assert match is not None
    title_rule = match.group(1)
    assert '"Noto Sans CJK SC"' in title_rule
    assert "Noto Serif" not in title_rule
