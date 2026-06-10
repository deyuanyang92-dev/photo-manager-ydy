"""万年历日历控件 —— QCalendarWidget 子类，格子内显示农历 / 节气 / 节日。

中国版万年历样式：
- 主行公历日，副行农历日名；初一显示月名（如「二月」「闰六月」）
- 节日（春节/中秋/国庆…）副行红色，节气（清明/冬至…）副行主题色
- 周末数字红色；今天描边高亮；选中实底
- 中文表头（周一…周日），周一为每周第一天

颜色全部取自 ``app/config/theme.py::TOKENS``，paint 时实时读取 ——
``apply_theme()`` 原地更新 TOKENS，因此自动跟随主题切换。

历法核心在 ``app/utils/chinese_calendar.py``（纯 Python，已天文历算校验）。
"""
from __future__ import annotations

from datetime import date

from PyQt6.QtCore import QDate, QLocale, QRect, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QTextCharFormat

from PyQt6.QtWidgets import QCalendarWidget, QDateEdit

from app.config.theme import TOKENS
from app.utils.chinese_calendar import day_info


def _qcolor(token: str, fallback: str = "#888888") -> QColor:
    """token → QColor；QColor 不认 rgba() 字符串时退回 fallback。"""
    c = QColor(TOKENS.get(token, fallback))
    return c if c.isValid() else QColor(fallback)


class LunarCalendarWidget(QCalendarWidget):
    """中国万年历：QDateEdit 弹窗即插即用（见 install_lunar_popup）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLocale(QLocale(QLocale.Language.Chinese, QLocale.Country.China))
        self.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.setVerticalHeaderFormat(
            QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.setHorizontalHeaderFormat(
            QCalendarWidget.HorizontalHeaderFormat.ShortDayNames)
        self.setGridVisible(False)
        # 两行内容（公历日 + 农历/节日）需要比默认更高的格子
        self.setMinimumSize(430, 340)
        self._apply_header_formats()

    # -- 主题 -----------------------------------------------------------------

    def showEvent(self, event):
        # 弹出时按当前 TOKENS 重设表头色 —— 跟随主题切换
        self._apply_header_formats()
        super().showEvent(event)

    def _apply_header_formats(self) -> None:
        weekday_fmt = QTextCharFormat()
        weekday_fmt.setForeground(QBrush(_qcolor("text_soft")))
        weekend_fmt = QTextCharFormat()
        weekend_fmt.setForeground(QBrush(_qcolor("danger")))
        for dow in (Qt.DayOfWeek.Monday, Qt.DayOfWeek.Tuesday,
                    Qt.DayOfWeek.Wednesday, Qt.DayOfWeek.Thursday,
                    Qt.DayOfWeek.Friday):
            self.setWeekdayTextFormat(dow, weekday_fmt)
        for dow in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
            self.setWeekdayTextFormat(dow, weekend_fmt)

    # -- 格子绘制 -------------------------------------------------------------

    def paintCell(self, painter: QPainter, rect: QRect, qdate: QDate) -> None:  # noqa: N802
        info = day_info(date(qdate.year(), qdate.month(), qdate.day()))
        selected = qdate == self.selectedDate()
        is_today = qdate == QDate.currentDate()
        in_month = (qdate.month() == self.monthShown()
                    and qdate.year() == self.yearShown())
        weekend = qdate.dayOfWeek() >= 6

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        cell = rect.adjusted(2, 2, -2, -2)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_qcolor("accent"))
            painter.drawRoundedRect(cell, 6, 6)
        elif is_today:
            fill = _qcolor("accent")
            fill.setAlphaF(0.12)
            pen = QPen(_qcolor("accent"))
            pen.setWidthF(1.2)
            painter.setPen(pen)
            painter.setBrush(fill)
            painter.drawRoundedRect(cell, 6, 6)

        if selected:
            num_color = QColor(255, 255, 255)
            sub_color = QColor(255, 255, 255, 220)
        elif not in_month:
            num_color = sub_color = _qcolor("muted_dim")
        else:
            num_color = _qcolor("danger") if weekend else _qcolor("text")
            sub_color = {
                "festival": _qcolor("danger"),
                "term": _qcolor("accent"),
                "month": _qcolor("text_soft"),
            }.get(info.kind, _qcolor("muted"))

        base_pt = self.font().pointSizeF()
        if base_pt <= 0:
            base_pt = 9.0
        num_font = QFont(self.font())
        num_font.setBold(selected or is_today)
        sub_font = QFont(self.font())
        sub_font.setPointSizeF(max(7.0, base_pt - 2.5))

        h = rect.height()
        num_rect = QRect(rect.x(), rect.y() + int(h * 0.06),
                         rect.width(), int(h * 0.50))
        sub_rect = QRect(rect.x(), rect.y() + int(h * 0.56),
                         rect.width(), int(h * 0.38))
        center = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter

        painter.setFont(num_font)
        painter.setPen(num_color)
        painter.drawText(num_rect, center, str(qdate.day()))

        if info.sub_text:
            painter.setFont(sub_font)
            painter.setPen(sub_color)
            painter.drawText(sub_rect, center, info.sub_text[:4])

        painter.restore()


def install_lunar_popup(edit: QDateEdit) -> LunarCalendarWidget:
    """把 QDateEdit 的日历弹窗换成万年历，返回该日历控件。"""
    cal = LunarCalendarWidget(edit)
    edit.setCalendarPopup(True)
    edit.setCalendarWidget(cal)
    return cal
