"""万年历核心逻辑测试 — app/utils/chinese_calendar.py（纯 Python，无 Qt）。

数据正确性基准：
- 春节/中秋等农历节日的公历日期为公开历史事实
- 闰月年份（2020闰四月、2023闰二月、2025闰六月）为公开事实
- 节气日期（清明=法定假日）为公开事实
表本身另有 lunardate 库 1900–2100 全量交叉校验（见 scripts/，不进 CI）。
"""
from datetime import date

import pytest

from app.utils.chinese_calendar import (
    LUNAR_EPOCH,
    day_info,
    festivals_on,
    ganzhi_year,
    leap_month,
    lunar_day_name,
    lunar_month_days,
    lunar_month_name,
    solar_term_on,
    solar_to_lunar,
    zodiac,
)


# ---------------------------------------------------------------- 农历换算

# 春节（正月初一）历史公历日期
SPRING_FESTIVALS = [
    (1900, date(1900, 1, 31)),   # 表纪元
    (1950, date(1950, 2, 17)),
    (1980, date(1980, 2, 16)),
    (2000, date(2000, 2, 5)),
    (2008, date(2008, 2, 7)),
    (2020, date(2020, 1, 25)),
    (2023, date(2023, 1, 22)),
    (2024, date(2024, 2, 10)),
    (2025, date(2025, 1, 29)),
    (2026, date(2026, 2, 17)),
]


@pytest.mark.parametrize("lunar_year,solar", SPRING_FESTIVALS)
def test_spring_festival_dates(lunar_year, solar):
    ld = solar_to_lunar(solar)
    assert (ld.year, ld.month, ld.day, ld.is_leap) == (lunar_year, 1, 1, False)


def test_mid_autumn():
    # 2024-09-17 / 2025-10-06 = 八月十五
    for solar, year in [(date(2024, 9, 17), 2024), (date(2025, 10, 6), 2025)]:
        ld = solar_to_lunar(solar)
        assert (ld.year, ld.month, ld.day) == (year, 8, 15)


def test_leap_months():
    assert leap_month(2020) == 4   # 闰四月
    assert leap_month(2023) == 2   # 闰二月
    assert leap_month(2025) == 6   # 闰六月
    assert leap_month(2026) == 0   # 无闰月
    assert leap_month(2024) == 0


def test_day_before_spring_festival_is_lunar_new_years_eve():
    # 2026-02-16 = 2025农历年腊月最后一天
    ld = solar_to_lunar(date(2026, 2, 16))
    assert ld.year == 2025 and ld.month == 12
    assert ld.day == lunar_month_days(2025, 12)


def test_before_epoch_returns_none():
    assert solar_to_lunar(date(1900, 1, 15)) is None
    assert solar_to_lunar(LUNAR_EPOCH) is not None


def test_range_boundary_no_crash():
    assert solar_to_lunar(date(2100, 12, 31)) is not None


# ---------------------------------------------------------------- 名称

def test_lunar_day_names():
    assert lunar_day_name(1) == "初一"
    assert lunar_day_name(10) == "初十"
    assert lunar_day_name(11) == "十一"
    assert lunar_day_name(20) == "二十"
    assert lunar_day_name(21) == "廿一"
    assert lunar_day_name(30) == "三十"


def test_lunar_month_names():
    assert lunar_month_name(1) == "正月"
    assert lunar_month_name(11) == "冬月"
    assert lunar_month_name(12) == "腊月"
    assert lunar_month_name(4, is_leap=True) == "闰四月"


def test_ganzhi_zodiac():
    assert ganzhi_year(1900) == "庚子"
    assert zodiac(1900) == "鼠"
    assert ganzhi_year(2024) == "甲辰"
    assert zodiac(2024) == "龙"
    assert ganzhi_year(2026) == "丙午"
    assert zodiac(2026) == "马"


# ---------------------------------------------------------------- 节气

def test_qingming():
    # 清明为法定假日，日期公开：2024/2025 均为 4 月 4 日
    assert solar_term_on(date(2024, 4, 4)) == "清明"
    assert solar_term_on(date(2025, 4, 4)) == "清明"
    assert solar_term_on(date(2024, 4, 10)) is None


def test_winter_solstice_2024():
    assert solar_term_on(date(2024, 12, 21)) == "冬至"


def test_two_terms_every_month():
    """不变量：2000–2030 每个公历月恰好 2 个节气，且落在常识窗口内。"""
    for year in range(2000, 2031):
        for month in range(1, 13):
            days = [
                d for d in range(1, 32)
                if _safe_term(year, month, d) is not None
            ]
            assert len(days) == 2, f"{year}-{month}: {days}"
            assert 1 <= days[0] <= 12 and 15 <= days[1] <= 26


def _safe_term(y, m, d):
    try:
        return solar_term_on(date(y, m, d))
    except ValueError:
        return None


# ---------------------------------------------------------------- 节日

def test_solar_festivals():
    assert "元旦" in festivals_on(date(2026, 1, 1))
    assert "儿童节" in festivals_on(date(2026, 6, 1))
    assert "国庆节" in festivals_on(date(2025, 10, 1))


def test_lunar_festivals():
    assert "春节" in festivals_on(date(2026, 2, 17))
    assert "中秋节" in festivals_on(date(2025, 10, 6))
    assert "除夕" in festivals_on(date(2026, 2, 16))
    assert "端午节" in festivals_on(date(2025, 5, 31))   # 2025端午 = 5月31日


def test_weekday_festivals():
    assert "母亲节" in festivals_on(date(2026, 5, 10))   # 5月第2个周日
    assert "母亲节" not in festivals_on(date(2026, 5, 3))
    assert "父亲节" in festivals_on(date(2026, 6, 21))   # 6月第3个周日


# ---------------------------------------------------------------- day_info 汇总

def test_day_info_priority_festival_over_term_over_day():
    # 节日优先
    info = day_info(date(2026, 2, 17))
    assert info.sub_text == "春节" and info.kind == "festival"
    # 节气次之
    info = day_info(date(2024, 4, 4))
    assert info.sub_text == "清明" and info.kind == "term"
    # 初一显示月名
    info = day_info(date(2026, 3, 19))  # 2026年二月初一
    assert info.kind == "month" and info.sub_text == "二月"
    # 普通日显示农历日名
    info = day_info(date(2026, 2, 20))  # 正月初四
    assert info.sub_text == "初四" and info.kind == "day"


def test_day_info_before_epoch_blank():
    info = day_info(date(1900, 1, 15))
    assert info.lunar is None
    assert info.sub_text == "" and info.kind == "none"


def test_day_info_tooltip_contains_ganzhi():
    info = day_info(date(2026, 2, 17))
    assert "丙午" in info.tooltip and "马" in info.tooltip
