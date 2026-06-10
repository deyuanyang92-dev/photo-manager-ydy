"""中国万年历核心：农历换算 + 二十四节气 + 节日 + 干支生肖。

纯 Python，无 Qt 依赖 —— 供 ``app/widgets/lunar_calendar_widget.py`` 等 UI 层使用。

农历数据为 1900–2100 年压缩位表（民间万年历通行编码），已用 sxtwl
（天文历算，GB/T 33661-2017）对 1900-01-31 ~ 2100-12-31 逐日交叉校验，
0 不一致（脚本 scripts/verify_lunar_table.py）。注意 lunardate 库在
1933/1954/2057/2060 等朔时刻临界年份与天文历算分歧，其中 1954-11-25
已查证为冬月初一（万年历权威源），本表正确、lunardate 错误。每个表项 17 位：

- bit 0-3   闰月月份（0 = 无闰月）
- bit 4-15  自高位起依次为正月…腊月的大小月（1 = 30 天，0 = 29 天）
- bit 16    闰月天数（1 = 30 天，0 = 29 天）

节气采用 1900 年基准分钟偏移表外推（同源通行算法），对 1900–2100
精度足够（±0 天为主，极端年份 ±1 天）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

# 1900-2100 农历位表
_LUNAR_INFO = (
    0x04bd8, 0x04ae0, 0x0a570, 0x054d5, 0x0d260, 0x0d950, 0x16554, 0x056a0, 0x09ad0, 0x055d2,  # 1900-1909
    0x04ae0, 0x0a5b6, 0x0a4d0, 0x0d250, 0x1d255, 0x0b540, 0x0d6a0, 0x0ada2, 0x095b0, 0x14977,  # 1910-1919
    0x04970, 0x0a4b0, 0x0b4b5, 0x06a50, 0x06d40, 0x1ab54, 0x02b60, 0x09570, 0x052f2, 0x04970,  # 1920-1929
    0x06566, 0x0d4a0, 0x0ea50, 0x16a95, 0x05ad0, 0x02b60, 0x186e3, 0x092e0, 0x1c8d7, 0x0c950,  # 1930-1939
    0x0d4a0, 0x1d8a6, 0x0b550, 0x056a0, 0x1a5b4, 0x025d0, 0x092d0, 0x0d2b2, 0x0a950, 0x0b557,  # 1940-1949
    0x06ca0, 0x0b550, 0x15355, 0x04da0, 0x0a5b0, 0x14573, 0x052b0, 0x0a9a8, 0x0e950, 0x06aa0,  # 1950-1959
    0x0aea6, 0x0ab50, 0x04b60, 0x0aae4, 0x0a570, 0x05260, 0x0f263, 0x0d950, 0x05b57, 0x056a0,  # 1960-1969
    0x096d0, 0x04dd5, 0x04ad0, 0x0a4d0, 0x0d4d4, 0x0d250, 0x0d558, 0x0b540, 0x0b6a0, 0x195a6,  # 1970-1979
    0x095b0, 0x049b0, 0x0a974, 0x0a4b0, 0x0b27a, 0x06a50, 0x06d40, 0x0af46, 0x0ab60, 0x09570,  # 1980-1989
    0x04af5, 0x04970, 0x064b0, 0x074a3, 0x0ea50, 0x06b58, 0x05ac0, 0x0ab60, 0x096d5, 0x092e0,  # 1990-1999
    0x0c960, 0x0d954, 0x0d4a0, 0x0da50, 0x07552, 0x056a0, 0x0abb7, 0x025d0, 0x092d0, 0x0cab5,  # 2000-2009
    0x0a950, 0x0b4a0, 0x0baa4, 0x0ad50, 0x055d9, 0x04ba0, 0x0a5b0, 0x15176, 0x052b0, 0x0a930,  # 2010-2019
    0x07954, 0x06aa0, 0x0ad50, 0x05b52, 0x04b60, 0x0a6e6, 0x0a4e0, 0x0d260, 0x0ea65, 0x0d530,  # 2020-2029
    0x05aa0, 0x076a3, 0x096d0, 0x04afb, 0x04ad0, 0x0a4d0, 0x1d0b6, 0x0d250, 0x0d520, 0x0dd45,  # 2030-2039
    0x0b5a0, 0x056d0, 0x055b2, 0x049b0, 0x0a577, 0x0a4b0, 0x0aa50, 0x1b255, 0x06d20, 0x0ada0,  # 2040-2049
    0x14b63, 0x09370, 0x049f8, 0x04970, 0x064b0, 0x168a6, 0x0ea50, 0x06aa0, 0x1a6c4, 0x0aae0,  # 2050-2059
    0x092e0, 0x0d2e3, 0x0c960, 0x0d557, 0x0d4a0, 0x0da50, 0x05d55, 0x056a0, 0x0a6d0, 0x055d4,  # 2060-2069
    0x052d0, 0x0a9b8, 0x0a950, 0x0b4a0, 0x0b6a6, 0x0ad50, 0x055a0, 0x0aba4, 0x0a5b0, 0x052b0,  # 2070-2079
    0x0b273, 0x06930, 0x07337, 0x06aa0, 0x0ad50, 0x14b55, 0x04b60, 0x0a570, 0x054e4, 0x0d160,  # 2080-2089
    0x0e968, 0x0d520, 0x0daa0, 0x16aa6, 0x056d0, 0x04ae0, 0x0a9d4, 0x0a2d0, 0x0d150, 0x0f252,  # 2090-2099
    0x0d520,                                                                                    # 2100
)

LUNAR_EPOCH = date(1900, 1, 31)   # 1900年正月初一
_MAX_YEAR = 1900 + len(_LUNAR_INFO) - 1

_DAY_NAMES = (
    "初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十",
    "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
    "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十",
)
_MONTH_NAMES = ("正月", "二月", "三月", "四月", "五月", "六月",
                "七月", "八月", "九月", "十月", "冬月", "腊月")

_GAN = "甲乙丙丁戊己庚辛壬癸"
_ZHI = "子丑寅卯辰巳午未申酉戌亥"
_ZODIAC = "鼠牛虎兔龙蛇马羊猴鸡狗猪"

_WEEKDAY_NAMES = ("一", "二", "三", "四", "五", "六", "日")

# 二十四节气：自每年 1 月的小寒起
_TERM_NAMES = (
    "小寒", "大寒", "立春", "雨水", "惊蛰", "春分", "清明", "谷雨",
    "立夏", "小满", "芒种", "夏至", "小暑", "大暑", "立秋", "处暑",
    "白露", "秋分", "寒露", "霜降", "立冬", "小雪", "大雪", "冬至",
)
# 1900-01-06 02:05（北京时）起各节气的分钟偏移
_TERM_OFFSET = (
    0, 21208, 42467, 63836, 85337, 107014, 128867, 150921,
    173149, 195551, 218072, 240693, 263343, 285989, 308563, 331033,
    353350, 375494, 397447, 419210, 440795, 462224, 483532, 504758,
)
_TERM_BASE = datetime(1900, 1, 6, 2, 5)
_MINUTES_PER_YEAR = 525948.76624  # 31556925974.7 ms

# 公历节日
_SOLAR_FESTIVALS = {
    (1, 1): "元旦", (2, 14): "情人节", (3, 8): "妇女节", (3, 12): "植树节",
    (4, 1): "愚人节", (5, 1): "劳动节", (5, 4): "青年节", (6, 1): "儿童节",
    (7, 1): "建党节", (8, 1): "建军节", (9, 10): "教师节", (10, 1): "国庆节",
    (12, 24): "平安夜", (12, 25): "圣诞节",
}
# 农历节日（仅非闰月生效）
_LUNAR_FESTIVALS = {
    (1, 1): "春节", (1, 15): "元宵节", (2, 2): "龙抬头", (5, 5): "端午节",
    (7, 7): "七夕", (7, 15): "中元节", (8, 15): "中秋节", (9, 9): "重阳节",
    (12, 8): "腊八节", (12, 23): "小年",
}


@dataclass(frozen=True)
class LunarDay:
    """一个公历日对应的农历日。"""
    year: int          # 农历年（生肖/干支随此年）
    month: int         # 1-12
    day: int           # 1-30
    is_leap: bool      # 是否闰月


@dataclass(frozen=True)
class DayInfo:
    """日历格子渲染所需的全部信息。"""
    solar: date
    lunar: LunarDay | None
    sub_text: str                  # 格子副行文字：节日 > 节气 > 初一→月名 > 日名
    kind: str                      # festival / term / month / day / none
    festivals: tuple[str, ...]
    solar_term: str | None
    tooltip: str


# ---------------------------------------------------------------- 位表读取

def leap_month(year: int) -> int:
    """该农历年的闰月月份，0 = 无闰月。"""
    return _LUNAR_INFO[year - 1900] & 0xF


def leap_month_days(year: int) -> int:
    """闰月天数；无闰月返回 0。"""
    if not leap_month(year):
        return 0
    return 30 if _LUNAR_INFO[year - 1900] & 0x10000 else 29


def lunar_month_days(year: int, month: int) -> int:
    """正常月（非闰）天数。"""
    return 30 if _LUNAR_INFO[year - 1900] & (0x10000 >> month) else 29


def lunar_year_days(year: int) -> int:
    """整个农历年的总天数。"""
    days = 348  # 12 × 29
    mask = 0x8000
    info = _LUNAR_INFO[year - 1900]
    while mask > 0x8:
        if info & mask:
            days += 1
        mask >>= 1
    return days + leap_month_days(year)


# ---------------------------------------------------------------- 换算

def solar_to_lunar(d: date) -> LunarDay | None:
    """公历→农历。超出 1900-01-31 ~ 2100 年表范围返回 None。"""
    offset = (d - LUNAR_EPOCH).days
    if offset < 0:
        return None
    year = 1900
    while year <= _MAX_YEAR:
        ydays = lunar_year_days(year)
        if offset < ydays:
            break
        offset -= ydays
        year += 1
    else:
        return None

    leap = leap_month(year)
    month = 1
    is_leap = False
    while month <= 12:
        mdays = lunar_month_days(year, month)
        if offset < mdays:
            break
        offset -= mdays
        if month == leap:
            ldays = leap_month_days(year)
            if offset < ldays:
                is_leap = True
                break
            offset -= ldays
        month += 1
    return LunarDay(year, month, offset + 1, is_leap)


# ---------------------------------------------------------------- 名称

def lunar_day_name(day: int) -> str:
    return _DAY_NAMES[day - 1]


def lunar_month_name(month: int, is_leap: bool = False) -> str:
    name = _MONTH_NAMES[month - 1]
    return f"闰{name}" if is_leap else name


def ganzhi_year(lunar_year: int) -> str:
    return _GAN[(lunar_year - 4) % 10] + _ZHI[(lunar_year - 4) % 12]


def zodiac(lunar_year: int) -> str:
    return _ZODIAC[(lunar_year - 4) % 12]


# ---------------------------------------------------------------- 节气

def _term_date(year: int, n: int) -> date:
    """year 年第 n 个节气（0=小寒 … 23=冬至）的公历日期。"""
    minutes = _MINUTES_PER_YEAR * (year - 1900) + _TERM_OFFSET[n]
    return (_TERM_BASE + timedelta(minutes=minutes)).date()


def solar_term_on(d: date) -> str | None:
    """该公历日是节气则返回节气名，否则 None。"""
    if not 1900 <= d.year <= _MAX_YEAR:
        return None
    n = (d.month - 1) * 2
    if _term_date(d.year, n) == d:
        return _TERM_NAMES[n]
    if _term_date(d.year, n + 1) == d:
        return _TERM_NAMES[n + 1]
    return None


# ---------------------------------------------------------------- 节日

def festivals_on(d: date, lunar: LunarDay | None = None) -> tuple[str, ...]:
    """该公历日的全部节日（公历 + 农历 + 按周推算），按重要度排列。"""
    found: list[str] = []

    if lunar is None:
        lunar = solar_to_lunar(d)
    if lunar and not lunar.is_leap:
        name = _LUNAR_FESTIVALS.get((lunar.month, lunar.day))
        if name:
            found.append(name)
        # 除夕 = 腊月最后一天
        if lunar.month == 12 and lunar.day == lunar_month_days(lunar.year, 12):
            found.append("除夕")

    name = _SOLAR_FESTIVALS.get((d.month, d.day))
    if name:
        found.append(name)

    # 按周推算：母亲节 = 5月第2个周日，父亲节 = 6月第3个周日
    if d.weekday() == 6:
        nth = (d.day - 1) // 7 + 1
        if d.month == 5 and nth == 2:
            found.append("母亲节")
        elif d.month == 6 and nth == 3:
            found.append("父亲节")

    return tuple(found)


# ---------------------------------------------------------------- 汇总

def day_info(d: date) -> DayInfo:
    """日历格子一站式信息：副行文字、类别、tooltip。"""
    lunar = solar_to_lunar(d)
    term = solar_term_on(d)
    fests = festivals_on(d, lunar)

    if lunar is None:
        return DayInfo(d, None, "", "none", (), term,
                       d.strftime("%Y-%m-%d") + " 星期" + _WEEKDAY_NAMES[d.weekday()])

    if fests:
        sub, kind = fests[0], "festival"
    elif term:
        sub, kind = term, "term"
    elif lunar.day == 1:
        sub, kind = lunar_month_name(lunar.month, lunar.is_leap), "month"
    else:
        sub, kind = lunar_day_name(lunar.day), "day"

    parts = [
        f"{d.strftime('%Y-%m-%d')} 星期{_WEEKDAY_NAMES[d.weekday()]}",
        (f"农历{ganzhi_year(lunar.year)}年（{zodiac(lunar.year)}年）"
         f"{lunar_month_name(lunar.month, lunar.is_leap)}{lunar_day_name(lunar.day)}"),
    ]
    extra = list(fests)
    if term:
        extra.append(term)
    if extra:
        parts.append(" · ".join(extra))
    return DayInfo(d, lunar, sub, kind, fests, term, "\n".join(parts))
