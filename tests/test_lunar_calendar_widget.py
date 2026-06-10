"""test_lunar_calendar_widget.py — LunarCalendarWidget（万年历日历弹窗）冒烟测试。

Runs offscreen (QT_QPA_PLATFORM=offscreen).

Checks:
- LunarCalendarWidget instantiates; locale is Chinese; Monday first.
- 渲染（grab）不崩 —— paintCell 覆盖全月含跨月格子。
- 1900 年 1 月（农历纪元前）渲染不崩。
- install_lunar_popup() 把 QDateEdit 的弹窗换成万年历。
- SummaryView 的两个日期框装上了万年历。
"""
from __future__ import annotations

import os
import sqlite3
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QDate, QLocale, Qt
from PyQt6.QtWidgets import QApplication, QDateEdit

from app.widgets.lunar_calendar_widget import LunarCalendarWidget, install_lunar_popup

_APP: QApplication | None = None


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


def test_instantiates_chinese_locale():
    cal = LunarCalendarWidget()
    assert cal.locale().language() == QLocale.Language.Chinese
    assert cal.firstDayOfWeek() == Qt.DayOfWeek.Monday


def test_render_normal_month_no_crash():
    cal = LunarCalendarWidget()
    cal.setCurrentPage(2026, 2)   # 含春节/除夕/元宵
    pix = cal.grab()
    assert not pix.isNull() and pix.width() > 0


def test_render_pre_epoch_no_crash():
    cal = LunarCalendarWidget()
    cal.setCurrentPage(1900, 1)   # 1900-01-31 前无农历数据 → 副行留空
    pix = cal.grab()
    assert not pix.isNull()


def test_install_lunar_popup():
    edit = QDateEdit()
    cal = install_lunar_popup(edit)
    assert edit.calendarPopup() is True
    assert edit.calendarWidget() is cal
    assert isinstance(edit.calendarWidget(), LunarCalendarWidget)


def test_summary_view_date_edits_use_lunar_calendar():
    from app.views.summary_view import SummaryView

    ctx = MagicMock()
    ctx.has_project = False
    ctx.current_project_dir = None
    ctx.get_db.return_value = None
    ctx.settings = MagicMock()
    view = SummaryView(ctx)
    assert isinstance(view._date_from_edit.calendarWidget(), LunarCalendarWidget)
    assert isinstance(view._date_to_edit.calendarWidget(), LunarCalendarWidget)
