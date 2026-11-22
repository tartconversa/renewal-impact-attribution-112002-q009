"""估计量与样本可用性。

层级（由弱到强）：

1. 朴素前后差：只描述处理片区自身的期别变化，含季节、人口、同期措施等共同因素，
   不单独代表项目成效。
2. 双重差分（DiD）：处理片区变化减去对照片区同期变化，扣除共同趋势；
   前提是对照仍然有效、平行趋势可接受、各比较期样本齐全。
3. 多批次队列估计：观察窗内分批开放时，每个开放队列只与"当时尚未处理"的片区比较，
   再等权汇总，避免已处理片区被错当对照造成的污染。

缺失规则：指标缺失（None）的 (片区, 期别) 直接跳过，不用零补齐；
任一组在任一比较期没有有效样本，对应估计量返回 None 并说明原因。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .loading import PeriodObs
from .periods import compare, period_index, shift_period, sort_periods


def obs_index(rows: list[PeriodObs]) -> dict[tuple[str, str], PeriodObs]:
    return {(row.area_id, row.period): row for row in rows}


def value(rows_index: dict[tuple[str, str], PeriodObs], area_id: str, period: str, metric: str):
    row = rows_index.get((area_id, period))
    return None if row is None else row.values.get(metric)


def group_period_mean(index, areas, period: str, metric: str) -> tuple[float | None, int]:
    """组内某期指标均值与有效样本数；缺失值跳过，绝不补零。"""
    values = [value(index, area, period, metric) for area in areas]
    present = [v for v in values if v is not None]
    if not present:
        return None, 0
    return sum(present) / len(present), len(present)


@dataclass
class NaiveChange:
    metric: str
    area_id: str
    pre_period: str
    post_period: str
    pre_value: int | None
    post_value: int | None
    change: int | None
    available: bool
    reason: str


def naive_change(index, area_id: str, pre_period: str, post_period: str, metric: str) -> NaiveChange:
    pre = value(index, area_id, pre_period, metric)
    post = value(index, area_id, post_period, metric)
    if pre is None or post is None:
        return NaiveChange(metric, area_id, pre_period, post_period, pre, post, None, False,
                           "前期或后期指标缺失，无法计算前后差（不补零）")
    return NaiveChange(metric, area_id, pre_period, post_period, pre, post, post - pre, True,
                       "仅反映片区自身前后变化，含共同趋势与同期措施，不能单独归因")


@dataclass
class DiDEstimate:
    metric: str
    pre_period: str
    post_period: str
    treated_areas: list[str]
    control_areas: list[str]
    treated_pre: float | None
    treated_post: float | None
    control_pre: float | None
    control_post: float | None
    treated_n_pre: int
    treated_n_post: int
    control_n_pre: int
    control_n_post: int
    did: float | None
    ci95: tuple[float, float] | None
    available: bool
    reason: str
    warnings: list[str] = field(default_factory=list)


def _normal_critical_95() -> float:
    return 1.96


def did_estimate(
    index,
    treated_areas,
    control_areas,
    pre_period: str,
    post_period: str,
    metric: str,
) -> DiDEstimate:
    treated_areas, control_areas = sorted(treated_areas), sorted(control_areas)
    t_pre, t_n_pre = group_period_mean(index, treated_areas, pre_period, metric)
    t_post, t_n_post = group_period_mean(index, treated_areas, post_period, metric)
    c_pre, c_n_pre = group_period_mean(index, control_areas, pre_period, metric)
    c_post, c_n_post = group_period_mean(index, control_areas, post_period, metric)

    warnings: list[str] = []
    missing = []
    if t_n_pre == 0:
        missing.append(f"处理组在 {pre_period} 无有效样本")
    if t_n_post == 0:
        missing.append(f"处理组在 {post_period} 无有效样本")
    if c_n_pre == 0:
        missing.append(f"对照组在 {pre_period} 无有效样本")
    if c_n_post == 0:
        missing.append(f"对照组在 {post_period} 无有效样本")
    if missing:
        return DiDEstimate(metric, pre_period, post_period, treated_areas, control_areas,
                           t_pre, t_post, c_pre, c_post, t_n_pre, t_n_post, c_n_pre, c_n_post,
                           None, None, False, "；".join(missing) + "，DiD 不可估计（缺失不补零）",
                           warnings)

    did = (t_post - t_pre) - (c_post - c_pre)
    # 独立四格均值差的近似标准误（组内样本独立、方差未知的大样本近似）
    se = _four_cell_se(index, treated_areas, control_areas, pre_period, post_period, metric)
    ci95 = None
    if se is not None:
        z = _normal_critical_95()
        ci95 = (did - z * se, did + z * se)
    else:
        warnings.append("某组某期有效样本不足 2 个，无法估计抽样不确定性，仅给点估计")
    if len(control_areas) < 2:
        warnings.append(f"合格对照仅 {len(control_areas)} 个片区，共同趋势代表性有限")

    return DiDEstimate(metric, pre_period, post_period, treated_areas, control_areas,
                       t_pre, t_post, c_pre, c_post, t_n_pre, t_n_post, c_n_pre, c_n_post,
                       did, ci95, True, "DiD 可估计；区间为近似 95% 置信区间", warnings)


def _four_cell_se(index, treated, controls, pre_period, post_period, metric) -> float | None:
    parts = []
    for areas in (treated, controls):
        for period in (pre_period, post_period):
            values = [value(index, area, period, metric) for area in areas]
            values = [v for v in values if v is not None]
            if len(values) < 2:
                return None
            mean = sum(values) / len(values)
            variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
            parts.append(variance / len(values))
    return math.sqrt(sum(parts))


@dataclass
class PreTrendResult:
    metric: str
    differences: dict[str, float | None]   # 各前期相对最近前期的处理-对照差值
    slope_difference: float | None         # 两组前期线性趋势之差（每期）
    available: bool
    reason: str


def pretrend_check(
    index, treated_areas, controls, pre_periods, metric: str
) -> PreTrendResult:
    """平行趋势检验：比较处理组与对照组在处理前各期的水平差与变化斜率。"""
    pre_periods = sort_periods(pre_periods)
    diffs: dict[str, float | None] = {}
    for period in pre_periods:
        t_mean, t_n = group_period_mean(index, treated_areas, period, metric)
        c_mean, c_n = group_period_mean(index, controls, period, metric)
        diffs[period] = (t_mean - c_mean) if (t_n and c_n) else None

    if len(pre_periods) < 2:
        return PreTrendResult(metric, diffs, None, False,
                              "处理前不足两个期别，无法检验平行趋势，结论需降级")
    if any(diffs[p] is None for p in pre_periods):
        return PreTrendResult(metric, diffs, None, False,
                              "处理前期存在缺失，平行趋势检验不完整，结论需降级")

    def slope(areas) -> float | None:
        means = []
        for period in pre_periods:
            mean, n = group_period_mean(index, areas, period, metric)
            if n == 0:
                return None
            means.append(mean)
        xs = [period_index(p) - period_index(pre_periods[0]) for p in pre_periods]
        x_mean = sum(xs) / len(xs)
        y_mean = sum(means) / len(means)
        denom = sum((x - x_mean) ** 2 for x in xs)
        return sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, means)) / denom if denom else None

    t_slope, c_slope = slope(treated_areas), slope(controls)
    if t_slope is None or c_slope is None:
        return PreTrendResult(metric, diffs, None, False, "前期样本不足，斜率无法计算", )
    return PreTrendResult(metric, diffs, t_slope - c_slope, True,
                          "前期双组各期均有样本；斜率差越接近 0，平行趋势越可信")


@dataclass
class CohortEstimate:
    open_period: str
    treated_areas: list[str]
    control_areas: list[str]               # 该队列比较时仍未处理的片区（not-yet-treated）
    pre_period: str
    post_period: str
    estimates: dict[str, DiDEstimate]      # metric -> DiD
    dropped_reason: str | None = None


def staggered_cohort_estimates(
    index,
    cohorts: dict[str, list[str]],
    timeline_status_controls,   # 回调：(intervention_type, window_last, treated_set) -> ControlVerdict 列表
    intervention_type: str,
    pre_periods: list[str],
    metrics: list[str],
) -> list[CohortEstimate]:
    """分批开放的队列估计。

    每个开放队列：前期取开放前一期，后期取开放当期；对照只取截至后期
    仍未发生同类型事件的片区（not-yet-treated），从源头杜绝对照污染。
    """
    results: list[CohortEstimate] = []
    all_open_periods = sorted(cohorts, key=period_index)
    for open_period in all_open_periods:
        treated = sorted(cohorts[open_period])
        treated_set = set(treated)
        verdicts = timeline_status_controls(intervention_type, open_period, treated_set)
        controls = sorted(v.area_id for v in verdicts if v.eligible)
        pre_period = shift_period(open_period, -1)
        if pre_period not in pre_periods and compare(pre_period, min(pre_periods)) < 0:
            results.append(CohortEstimate(
                open_period, treated, controls, pre_period, open_period, {},
                dropped_reason=f"开放期 {open_period} 的前一期 {pre_period} 不在观察窗内，无前期基线"))
            continue
        if not controls:
            results.append(CohortEstimate(
                open_period, treated, controls, pre_period, open_period, {},
                dropped_reason="截至开放期已无未处理片区，缺少有效对照，该队列只能做描述性比较"))
            continue
        estimates = {
            metric: did_estimate(index, treated, controls, pre_period, open_period, metric)
            for metric in metrics
        }
        results.append(CohortEstimate(open_period, treated, controls, pre_period, open_period, estimates))
    return results


def aggregate_cohorts(cohorts: list[CohortEstimate], metric: str) -> dict[str, object]:
    """等权汇总各队列 DiD 点估计与可用区间；不可用队列不计入并单列原因。"""
    point_estimates, ci_estimates = [], []
    dropped = []
    for cohort in cohorts:
        if cohort.dropped_reason:
            dropped.append({"open_period": cohort.open_period, "reason": cohort.dropped_reason})
            continue
        estimate = cohort.estimates.get(metric)
        if estimate is None or not estimate.available or estimate.did is None:
            dropped.append({
                "open_period": cohort.open_period,
                "reason": estimate.reason if estimate else "该指标无估计",
            })
            continue
        point_estimates.append((cohort.open_period, estimate.did))
        if estimate.ci95 is not None:
            ci_estimates.append((cohort.open_period, estimate.did, estimate.ci95))
    if not point_estimates:
        return {"available": False, "reason": "没有任何队列可估计该指标", "dropped": dropped}
    pooled = sum(did for _, did in point_estimates) / len(point_estimates)
    return {
        "available": True,
        "pooled_did": pooled,
        "cohort_points": dict(point_estimates),
        "cohort_count": len(point_estimates),
        "ci_note": "队列数过少，不做跨队列合并区间；请查看各队列自身区间" if len(ci_estimates) < 2
        else "各队列等权平均；区间见各队列结果",
        "dropped": dropped,
    }


@dataclass
class CountDecomposition:
    """计数指标变化的数量/强度分解：Δ总量 ≈ 强度效应 + 规模效应 + 交互项。"""
    metric: str
    pre_count: int | None
    post_count: int | None
    pre_residents: int | None
    post_residents: int | None
    rate_pre: float | None
    rate_post: float | None
    rate_effect: float | None
    size_effect: float | None
    interaction: float | None
    available: bool
    reason: str


def decompose_count_change(index, area_id: str, pre_period: str, post_period: str,
                           metric: str, population_metric: str = "residents") -> CountDecomposition:
    pre_count = value(index, area_id, pre_period, metric)
    post_count = value(index, area_id, post_period, metric)
    pre_pop = value(index, area_id, pre_period, population_metric)
    post_pop = value(index, area_id, post_period, population_metric)
    if None in (pre_count, post_count, pre_pop, post_pop) or pre_pop == 0 or post_pop == 0:
        return CountDecomposition(metric, pre_count, post_count, pre_pop, post_pop,
                                  None, None, None, None, None, False,
                                  "计数或人口缺失/为零，无法做数量-强度分解（不补零）")
    rate_pre, rate_post = pre_count / pre_pop, post_count / post_pop
    rate_effect = (rate_post - rate_pre) * pre_pop
    size_effect = rate_pre * (post_pop - pre_pop)
    interaction = (rate_post - rate_pre) * (post_pop - pre_pop)
    return CountDecomposition(metric, pre_count, post_count, pre_pop, post_pop,
                              rate_pre, rate_post, rate_effect, size_effect, interaction, True,
                              "强度效应=同等居民使用率变化；规模效应=人口变化；交互项为两者共同作用")


def sample_availability(index, areas, periods, metrics) -> dict[str, dict[str, dict[str, object]]]:
    """按期别统计每个指标的有效样本数、缺失片区与缺失率，供分析人员判断资料完整度。"""
    table: dict[str, dict[str, dict[str, object]]] = {}
    for period in sort_periods(periods):
        table[period] = {}
        for metric in metrics:
            present, missing = [], []
            for area in areas:
                (present if value(index, area, period, metric) is not None else missing).append(area)
            total = len(areas)
            table[period][metric] = {
                "n_available": len(present),
                "n_total": total,
                "missing_areas": sorted(missing),
                "missing_rate": (len(missing) / total) if total else None,
            }
    return table
