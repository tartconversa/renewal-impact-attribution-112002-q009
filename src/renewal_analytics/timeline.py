"""干预时间线与对照组资格。

状态（片区 × 期别 × 干预类型）：

- ``none``：观察窗内没有该类型干预；
- ``planned``：有计划开工日期但尚未开工；
- ``construction``：已开工、未开放（施工期本身也可能影响指标，需单独标注）；
- ``open``：实际开放所在期别及以后（以 ``actual_open`` 为准，不是计划日期）；
- ``unknown_timing``：有干预记录但开工/开放日期均缺失，无法定位时间，
  既不能进处理组也不能当对照。

对照片区一旦在观察窗内出现同类型施工/开放，即被"审查"（censor），
自该期别起退出对照；无法确认时间的片区直接排除并说明原因。
"""
from __future__ import annotations

from dataclasses import dataclass

from .loading import Intervention
from .periods import compare, period_bounds, period_index, sort_periods

# 状态严格程度排序：多份记录并存时取最强状态
_STATUS_RANK = {
    "none": 0,
    "planned": 1,
    "unknown_timing": 2,
    "construction": 3,
    "open": 4,
}


def _status_for(intervention: Intervention, period: str) -> str:
    period_start, period_end = period_bounds(period)
    open_day = intervention.actual_open
    start_day = intervention.planned_start

    # 开放日在该季度内或更早 -> 开放
    if open_day is not None and open_day <= period_end:
        return "open"
    if start_day is not None:
        if open_day is not None:
            # 已开工（开工日不晚于季度末）且尚未开放（开放日晚于季度初）-> 施工
            if start_day <= period_end and open_day > period_start:
                return "construction"
        elif start_day <= period_end:
            # 已按计划开工但实际开放日期未知（可能延期）：视作施工中
            return "construction"
        return "planned"
    # 既无开工日期也无开放日期，无法在时间轴上定位
    return "unknown_timing"


@dataclass
class Timeline:
    """status[(area_id, type)][period] = 状态；members 保留对应干预编号。"""

    status: dict[tuple[str, str], dict[str, str]]
    members: dict[tuple[str, str], list[str]]
    areas: list[str]
    types: list[str]
    periods: list[str]

    def cell(self, area_id: str, intervention_type: str, period: str) -> str:
        return self.status.get((area_id, intervention_type), {}).get(period, "none")


def build_timeline(interventions: list[Intervention], periods: list[str],
                   all_areas: list[str] | None = None) -> Timeline:
    periods = sort_periods(periods)
    active = [iv for iv in interventions if not iv.superseded]
    intervention_areas = {iv.area_id for iv in active}
    # 时间线必须覆盖观察窗内全部片区：没有任何干预记录的纯对照片区状态恒为 none，
    # 否则它们会从对照评估中"消失"。all_areas 缺省时退化为仅干预片区。
    areas = sorted(set(all_areas) | intervention_areas) if all_areas else sorted(intervention_areas)
    types = sorted({iv.type for iv in active})
    status: dict[tuple[str, str], dict[str, str]] = {}
    members: dict[tuple[str, str], list[str]] = {}
    for iv in active:
        key = (iv.area_id, iv.type)
        members.setdefault(key, []).append(iv.intervention_id)
        bucket = status.setdefault(key, {})
        for period in periods:
            current = bucket.get(period, "none")
            candidate = _status_for(iv, period)
            if _STATUS_RANK[candidate] > _STATUS_RANK[current]:
                bucket[period] = candidate
    return Timeline(status=status, members=members, areas=areas, types=types, periods=periods)


def open_cohorts(timeline: Timeline, intervention_type: str) -> dict[str, list[str]]:
    """按实际开放期别分组的处理队列：{开放期别: [片区]}。"""
    cohorts: dict[str, list[str]] = {}
    for area_id in timeline.areas:
        cells = timeline.status.get((area_id, intervention_type), {})
        opens = [p for p, state in cells.items() if state == "open"]
        if opens:
            first_open = min(opens, key=period_index)
            cohorts.setdefault(first_open, []).append(area_id)
    return {period: sorted(areas) for period, areas in sorted(cohorts.items(), key=lambda kv: period_index(kv[0]))}


@dataclass
class ControlVerdict:
    area_id: str
    eligible: bool
    usable_through: str | None          # 可作为对照的最后一期别
    reason: str                          # 中文说明（合格时说明依据，不合格时说明审查/排除原因）
    first_event_period: str | None = None


def assess_controls(
    timeline: Timeline,
    intervention_type: str,
    window_last_period: str,
    treated_areas: set[str],
    excluded_areas: set[str] | None = None,
) -> list[ControlVerdict]:
    """逐个非处理片区判断其在截至 ``window_last_period`` 的窗口内能否作对照。

    保守规则：

    - 自身有该类型干预记录的片区（含尚未开放者）从候选中排除，不能当自己的对照；
    - 窗口内出现同类型 construction/open：自事件期别起审查，之前期别仍可用；
    - 出现 unknown_timing：无法证明干净，整个窗口排除；
    - 全程 none/planned：合格（planned 仅表示计划存在、尚未实施，不构成处理）。
    """
    excluded = treated_areas | (excluded_areas or set())
    verdicts: list[ControlVerdict] = []
    for area_id in timeline.areas:
        if area_id in excluded:
            continue
        cells = timeline.status.get((area_id, intervention_type), {})
        event_periods = [
            p for p, state in cells.items()
            if state in ("construction", "open") and compare(p, window_last_period) <= 0
        ]
        unknown = any(state == "unknown_timing" for state in cells.values())
        if unknown:
            verdicts.append(
                ControlVerdict(
                    area_id=area_id, eligible=False, usable_through=None,
                    reason="存在同类型干预但开工/开放日期缺失，无法确认观察窗内是否已实施，排除对照",
                )
            )
        elif event_periods:
            first = min(event_periods, key=period_index)
            verdicts.append(
                ControlVerdict(
                    area_id=area_id, eligible=False,
                    usable_through=None if period_index(first) <= period_index(timeline.periods[0]) else
                    _previous_period(timeline.periods, first),
                    first_event_period=first,
                    reason=f"于 {first} 启动同类型措施，自该期别起对照失效（审查退出）",
                )
            )
        else:
            verdicts.append(
                ControlVerdict(
                    area_id=area_id, eligible=True, usable_through=window_last_period,
                    reason="观察窗内无同类型实施记录，可作为未处理对照",
                )
            )
    return verdicts


def _previous_period(periods: list[str], period: str) -> str:
    ordered = sort_periods(periods)
    index = ordered.index(period)
    return ordered[index - 1]
