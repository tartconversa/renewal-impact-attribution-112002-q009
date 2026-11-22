"""季度期别（YYYY-Qn）解析、排序与日期归属。

期别是本项目的标准时间粒度：工程开放日期按日期归属到季度，
所有"前后阶段"的划分都以期别为单位。
"""
from __future__ import annotations

import calendar
import re
from datetime import date

_PERIOD_RE = re.compile(r"^(\d{4})-Q([1-4])$")


def parse_period(period: str) -> tuple[int, int]:
    """把 ``2026-Q2`` 解析为 ``(2026, 2)``。"""
    match = _PERIOD_RE.match(period.strip())
    if not match:
        raise ValueError(f"无法解析期别 {period!r}，应为 YYYY-Qn 形式（如 2026-Q1）")
    return int(match.group(1)), int(match.group(2))


def sort_periods(periods) -> list[str]:
    """按期别先后排序并去重。"""
    return sorted(set(periods), key=parse_period)


def period_bounds(period: str) -> tuple[date, date]:
    """返回该季度的起止日期（含首尾）。"""
    year, quarter = parse_period(period)
    start_month = 3 * (quarter - 1) + 1
    end_month = 3 * quarter
    start = date(year, start_month, 1)
    end = date(year, end_month, calendar.monthrange(year, end_month)[1])
    return start, end


def period_for_date(day: date) -> str:
    """日期归属到季度期别。"""
    return f"{day.year}-Q{(day.month - 1) // 4 + 1}"


def shift_period(period: str, offset: int) -> str:
    """期别平移 ``offset`` 个季度（可为负）。"""
    year, quarter = parse_period(period)
    index = year * 4 + (quarter - 1) + offset
    return f"{index // 4}-Q{index % 4 + 1}"


def period_index(period: str) -> int:
    """可做差的单调整数序号。"""
    year, quarter = parse_period(period)
    return year * 4 + (quarter - 1)


def compare(left: str, right: str) -> int:
    """期别比较：左早于右返回 -1，相等 0，晚于返回 1。"""
    a, b = period_index(left), period_index(right)
    return (a > b) - (a < b)
