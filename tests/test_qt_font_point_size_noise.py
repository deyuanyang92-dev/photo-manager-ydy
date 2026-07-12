"""回归：QFont::setPointSize 噪声(Point size <= 0 (-1))。

真凶不在本项目代码里 —— Qt 6.10 的 windows11 样式碰上我们 QSS 的像素字号
(`QWidget { font-size: 13px }`)，会在 Qt C++ 内部把 pointSize()(px 定义的字体恒为
-1)回填给 setPointSize()，每建一个 QComboBox / QCalendarWidget / 带日历弹窗的
QDateEdit 就刷几条，一次启动几十上百条。

这里锁两件事：
1. `main._is_qt_noise` 把这条已知噪声挡在日志之外(别的告警照旧要进日志)。
2. `theme.safe_point_size` —— 全项目读 QFont 点值的唯一入口 —— 在 pointSize()
   返回 -1(像素字号字体)时，永远给出 > 0 的值，也就绝不会把 <=0 传回
   setPointSize()/setPointSizeF()。
"""
import logging

import pytest
from PyQt6.QtCore import QtMsgType, qInstallMessageHandler
from PyQt6.QtGui import QFont

NOISE = "QFont::setPointSize: Point size <= 0 (-1), must be greater than 0"


# ── 1. 日志桥：已知噪声不落盘 ────────────────────────────────────────────────

def _install_capture(monkeypatch):
    import main

    installed = []
    monkeypatch.setattr(main, "_QT_MESSAGE_HANDLER", None)
    monkeypatch.setattr(main, "_QT_PREVIOUS_MESSAGE_HANDLER", None)
    monkeypatch.delenv("SPECIMEN_QT_VERBOSE", raising=False)

    def _install(handler):
        installed.append(handler)
        return None

    main._install_qt_message_handler(_install)
    return main, installed[0]


def test_setpointsize_noise_is_not_logged(monkeypatch, caplog):
    _main, handler = _install_capture(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="qt"):
        handler(QtMsgType.QtWarningMsg, None, NOISE)

    assert "setPointSize" not in caplog.text


def test_setpointsizef_noise_is_not_logged(monkeypatch, caplog):
    _main, handler = _install_capture(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="qt"):
        handler(
            QtMsgType.QtWarningMsg,
            None,
            "QFont::setPointSizeF: Point size <= 0 (-1.000000), must be greater than 0",
        )

    assert "setPointSizeF" not in caplog.text


def test_other_qt_warnings_still_logged(monkeypatch, caplog):
    """噪声过滤必须是外科手术式的 —— 别的 Qt 告警照旧要进日志。"""
    _main, handler = _install_capture(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="qt"):
        handler(QtMsgType.QtWarningMsg, None, "QFileSystemWatcher::removePaths: list is empty")

    assert "removePaths" in caplog.text


def test_noise_not_forwarded_to_previous_handler(monkeypatch):
    """噪声也不该转发给上一个 handler(否则 stderr 照样刷屏)。"""
    import main

    forwarded = []
    monkeypatch.setattr(main, "_QT_MESSAGE_HANDLER", None)
    monkeypatch.setattr(main, "_QT_PREVIOUS_MESSAGE_HANDLER", None)
    monkeypatch.delenv("SPECIMEN_QT_VERBOSE", raising=False)

    installed = []

    def _install(handler):
        installed.append(handler)
        return lambda *args: forwarded.append(args)  # 冒充「上一个 handler」

    main._install_qt_message_handler(_install)
    installed[0](QtMsgType.QtWarningMsg, None, NOISE)
    assert forwarded == []

    installed[0](QtMsgType.QtWarningMsg, None, "real warning")
    assert len(forwarded) == 1


def test_verbose_env_lets_the_noise_through(monkeypatch, caplog):
    """排查我们自己代码真传了 <=0 时，SPECIMEN_QT_VERBOSE=1 要能放行。"""
    import main

    monkeypatch.setattr(main, "_QT_MESSAGE_HANDLER", None)
    monkeypatch.setattr(main, "_QT_PREVIOUS_MESSAGE_HANDLER", None)
    monkeypatch.setenv("SPECIMEN_QT_VERBOSE", "1")

    installed = []
    main._install_qt_message_handler(lambda h: installed.append(h))
    with caplog.at_level(logging.WARNING, logger="qt"):
        installed[0](QtMsgType.QtWarningMsg, None, NOISE)

    assert "setPointSize" in caplog.text


# ── 2. safe_point_size：px 字体(pointSize() == -1)绝不回填 <=0 ────────────────

def test_pixel_font_really_reports_minus_one(qapp):
    """前提事实(Qt 官方行为)：setPixelSize 定义的字体，pointSize() == -1。"""
    f = QFont()
    f.setPixelSize(13)
    assert f.pointSize() == -1
    assert f.pointSizeF() == -1.0


def test_safe_point_size_falls_back_on_pixel_font(qapp):
    from app.config.theme import safe_point_size

    f = QFont()
    f.setPixelSize(13)  # QSS `font-size: 13px` 的等价物

    assert safe_point_size(f, 10.0) == 10.0
    assert safe_point_size(f, 9.0) == 9.0


def test_safe_point_size_keeps_a_real_point_size(qapp):
    from app.config.theme import safe_point_size

    f = QFont()
    f.setPointSizeF(11.5)
    assert safe_point_size(f, 10.0) == pytest.approx(11.5)


def test_safe_point_size_result_is_always_settable(qapp):
    """真正的不变量：拿 safe_point_size 的结果回填 setPointSize(F) 不会触发 Qt 告警。"""
    from app.config.theme import safe_point_size

    seen = []
    old = qInstallMessageHandler(lambda t, c, m: seen.append(m))
    try:
        for fallback in (9.0, 10.0):
            px_font = QFont()
            px_font.setPixelSize(13)
            base = safe_point_size(px_font, fallback)
            assert base > 0

            out = QFont(px_font)
            out.setPointSizeF(base)                 # theme.apply_default_font 的写法
            out.setPointSizeF(max(7.0, base - 2.5))  # lunar_calendar_widget 的写法
            out.setPointSize(int(base))
    finally:
        qInstallMessageHandler(old)

    assert [m for m in seen if "Point size <= 0" in m] == []


def test_apply_default_font_emits_no_point_size_warning(qapp):
    """即便应用默认字体是像素字号，apply_default_font 也不许刷出告警。"""
    from app.config import theme

    px_font = QFont(qapp.font())
    px_font.setPixelSize(13)
    original = QFont(qapp.font())
    qapp.setFont(px_font)

    seen = []
    old = qInstallMessageHandler(lambda t, c, m: seen.append(m))
    try:
        theme.apply_default_font(qapp)
    finally:
        qInstallMessageHandler(old)
        qapp.setFont(original)

    assert [m for m in seen if "Point size <= 0" in m] == []
