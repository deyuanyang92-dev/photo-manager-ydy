"""开发期校验脚本：用 sxtwl（天文历算）逐日核对 chinese_calendar 的农历位表。

不进 CI（sxtwl 非运行时依赖）。用法::

    pip install sxtwl
    python scripts/verify_lunar_table.py

历史背景：位表最初手写，曾有 3 个错误表项（1933/2057/2060），由本脚本
发现并按 sxtwl 修正。lunardate 库在 1933/1954/2057/2060 等朔时刻临界
年份与天文历算分歧——1954-11-25 经万年历权威源查证为冬月初一，
天文历算正确、lunardate 错误，故以 sxtwl 为准。
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sxtwl  # noqa: E402

from app.utils.chinese_calendar import LUNAR_EPOCH, solar_to_lunar  # noqa: E402


def main() -> int:
    bad = 0
    d = LUNAR_EPOCH
    end = date(2100, 12, 31)
    while d <= end:
        mine = solar_to_lunar(d)
        ref = sxtwl.fromSolar(d.year, d.month, d.day)
        expect = (ref.getLunarYear(), ref.getLunarMonth(),
                  ref.getLunarDay(), bool(ref.isLunarLeap()))
        if (mine.year, mine.month, mine.day, mine.is_leap) != expect:
            bad += 1
            if bad <= 10:
                print(f"MISMATCH {d}: table={mine} sxtwl={expect}")
        d += timedelta(days=1)
    print(f"checked {LUNAR_EPOCH} ~ {end}, mismatches: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
