"""分析管线编排：从原始输入到单次运行的完整结论包。

运行单位 = 一次数据快照（as_of）× 全部干预类型。每个干预类型产出：

- 阶段时间线（计划/施工/开放）与计划-实际开放偏差；
- 开放队列、对照组资格与审查原因；
- 各指标的样本可用性、朴素前后差、DiD、多批次队列汇总；
- 人口边界核对与计数指标的数量/强度分解；
- 证据分级（A/B/C/D）与降级原因。

任何一步资料不足都只降低结论强度并说明原因，不抛掉流程、不补零、不编造数字。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from . import evidence as evidence_mod
from . import estimators, population
from .loading import (
    AreaHistoryRow,
    Intervention,
    OtherMeasure,
    as_of_snapshot,
    load_area_history,
    load_interventions,
    load_other_measures,
    load_period_rows,
)
from .periods import compare, period_for_date, period_index, shift_period, sort_periods
from .timeline import Timeline, assess_controls, build_timeline, open_cohorts

# 参与效应分析的指标（residents 作为人口上下文，不在此列）
EFFECT_METRICS = ("service_use", "walkability_score")
ALL_METRICS = ("residents", "service_use", "walkability_score")
# 属于"计数"口径、可做人均强度分解的指标
COUNT_METRICS = ("service_use",)


@dataclass
class MetricFinding:
    metric: str
    sample: dict
    naive: dict
    did: dict | None
    pretrend: dict | None
    count_decomposition: dict | None
    evidence: dict


@dataclass
class InterventionAnalysis:
    intervention_type: str
    intervention_ids: list[str]
    timing_note: str
    cohorts: dict
    control_verdicts: list[dict]
    findings: list[MetricFinding]
    staggered_summary: dict
    construction_note: str | None
    population_decomposition: list[dict] = field(default_factory=list)


@dataclass
class AnalysisRun:
    run_id: str
    as_of: str | None
    generated_on: str
    observation_periods: list[str]
    areas: list[str]
    sample_availability: dict
    population_checks: list[dict]
    interventions: list[InterventionAnalysis]
    global_warnings: list[str]
    input_fingerprints: dict[str, str]


def _control_provider(timeline: Timeline, window_periods: list[str],
                      excluded_areas: set[str] | None = None):
    def provider(intervention_type: str, window_last: str, treated_set: set[str]):
        return assess_controls(timeline, intervention_type, window_last, treated_set,
                               excluded_areas=excluded_areas)
    return provider


def _naive_to_dict(change) -> dict:
    return asdict(change)


def _did_to_dict(estimate) -> dict | None:
    if estimate is None:
        return None
    data = asdict(estimate)
    if estimate.ci95 is not None:
        data["ci95"] = [round(estimate.ci95[0], 2), round(estimate.ci95[1], 2)]
    if estimate.did is not None:
        data["did"] = round(estimate.did, 2)
    return data


def _pretrend_to_dict(result) -> dict | None:
    if result is None:
        return None
    data = asdict(result)
    data["differences"] = {
        period: (round(diff, 2) if diff is not None else None)
        for period, diff in result.differences.items()
    }
    if result.slope_difference is not None:
        data["slope_difference"] = round(result.slope_difference, 4)
    return data


def _finding_for_metric(
    *, metric, index, areas, treated_areas, control_areas, pre_period, post_period,
    pre_periods, control_verdicts, measures, timing_known, timeline, intervention_type,
    history,
) -> MetricFinding:
    treated_set, control_set = set(treated_areas), set(control_areas)
    # 多片区处理组时，朴素变化取组均值口径；单片区直接取该片区
    if len(treated_set) > 1:
        naive = _group_naive(index, sorted(treated_set), pre_period, post_period, metric)
    else:
        naive = estimators.naive_change(
            index, next(iter(treated_set)), pre_period, post_period, metric)

    did = estimators.did_estimate(index, sorted(treated_set), sorted(control_set),
                                  pre_period, post_period, metric)
    pretrend = estimators.pretrend_check(index, sorted(treated_set), sorted(control_set),
                                         pre_periods, metric)
    asymmetric, common = evidence_mod.measures_affecting(
        measures, treated_set, control_set, pre_period, post_period)
    verdict = evidence_mod.grade_evidence(
        naive=naive, did=did, pretrend=pretrend, control_verdicts=control_verdicts,
        asymmetric_measures=asymmetric, common_measures=common,
        intervention_timing_known=timing_known,
    )

    decomposition = None
    if metric in COUNT_METRICS and len(treated_set) == 1:
        decomp = estimators.decompose_count_change(
            index, next(iter(treated_set)), pre_period, post_period, metric)
        decomposition = asdict(decomp)
        for key in ("rate_pre", "rate_post", "rate_effect", "size_effect", "interaction"):
            if decomposition[key] is not None:
                decomposition[key] = round(decomposition[key], 3)

    sample = estimators.sample_availability(
        index, sorted(treated_set | control_set), [pre_period, post_period], [metric])

    return MetricFinding(
        metric=metric,
        sample=sample,
        naive=_naive_to_dict(naive),
        did=_did_to_dict(did),
        pretrend=_pretrend_to_dict(pretrend),
        count_decomposition=decomposition,
        evidence=asdict(verdict),
    )


def _group_naive(index, treated_areas, pre_period, post_period, metric):
    pre_mean, pre_n = estimators.group_period_mean(index, treated_areas, pre_period, metric)
    post_mean, post_n = estimators.group_period_mean(index, treated_areas, post_period, metric)
    if pre_n == 0 or post_n == 0:
        return estimators.NaiveChange(metric, "、".join(treated_areas), pre_period, post_period,
                                      None, None, None, False,
                                      "处理组前期或后期无有效样本（缺失不补零）")
    change = post_mean - pre_mean
    return estimators.NaiveChange(metric, "、".join(treated_areas), pre_period, post_period,
                                  round(pre_mean, 2), round(post_mean, 2), round(change, 2), True,
                                  "处理组组均值口径的前后变化，仍含共同趋势，不能单独归因")


def analyze_intervention_type(
    *, intervention_type: str, interventions: list[Intervention], timeline: Timeline,
    index, periods: list[str], measures: list[OtherMeasure],
    history: list[AreaHistoryRow], areas: list[str],
) -> InterventionAnalysis:
    type_records = [iv for iv in interventions if not iv.superseded and iv.type == intervention_type]
    type_areas = sorted({iv.area_id for iv in type_records})
    cohorts = open_cohorts(timeline, intervention_type)

    timing_known = all(iv.actual_open is not None or iv.planned_start is not None for iv in type_records)
    timing_bits = []
    for iv in type_records:
        if iv.actual_open is None:
            timing_bits.append(f"{iv.intervention_id}（{iv.area_id}）尚无实际开放日期，"
                               "不纳入开放效应分析，仅跟踪施工/计划状态")
        else:
            planned_open_period = period_for_date(iv.planned_start) if iv.planned_start else None
            actual_period = period_for_date(iv.actual_open)
            if planned_open_period and actual_period != planned_open_period:
                timing_bits.append(f"{iv.intervention_id}（{iv.area_id}）计划 {planned_open_period}、"
                                   f"实际 {actual_period} 开放，分期已按实际日期重算")
            else:
                timing_bits.append(f"{iv.intervention_id}（{iv.area_id}）实际开放于 {actual_period}")
    timing_note = "；".join(timing_bits)

    construction_periods = sorted(
        {p for area in type_areas for p, state in timeline.status.get((area, intervention_type), {}).items()
         if state == "construction"}, key=period_index)
    construction_note = (
        "施工期：" + "、".join(construction_periods) +
        "。施工本身可能影响出行与反馈，施工期变化单独描述，不计入开放效应。"
        if construction_periods else None)

    # 以"已有实际开放"的片区作为处理组；未开放的记录不产生效应分析
    treated_set = {area for opens in cohorts.values() for area in opens}
    window_last = periods[-1]
    # assess_controls 会把"已施工/开放"的有记录片区标记为审查失效；
    # 仅把停留在计划阶段、自身有该类型项目的片区作为候选外排除（不能当自己的对照）
    planned_only = {
        area for area in type_areas
        if area not in treated_set
        and not any(state in ("construction", "open", "unknown_timing")
                    for state in timeline.status.get((area, intervention_type), {}).values())
    }
    control_verdicts = assess_controls(
        timeline, intervention_type, window_last, treated_set, excluded_areas=planned_only)
    eligible_controls = sorted(v.area_id for v in control_verdicts if v.eligible)

    findings: list[MetricFinding] = []
    staggered_summary: dict = {"available": False, "reason": "尚无实际开放队列"}

    if cohorts:
        first_open = min(cohorts, key=period_index)
        pre_period = shift_period(first_open, -1)
        if compare(pre_period, periods[0]) < 0:
            pre_period = periods[0]

        def _first_open_period(area_id: str) -> str:
            cells = timeline.status.get((area_id, intervention_type), {})
            return min((p for p, state in cells.items() if state == "open"), key=period_index)

        post_period = min(
            max((_first_open_period(area) for area in treated_set), key=period_index),
            window_last,
        )
        pre_periods = [p for p in periods if period_index(p) < period_index(first_open)]

        for metric in EFFECT_METRICS:
            findings.append(_finding_for_metric(
                metric=metric, index=index, areas=areas,
                treated_areas=treated_set, control_areas=eligible_controls,
                pre_period=pre_period, post_period=post_period, pre_periods=pre_periods,
                control_verdicts=control_verdicts, measures=measures,
                timing_known=timing_known, timeline=timeline,
                intervention_type=intervention_type, history=history,
            ))

        cohort_results = estimators.staggered_cohort_estimates(
            index, cohorts, _control_provider(timeline, periods, planned_only),
            intervention_type, pre_periods, list(EFFECT_METRICS))
        staggered_summary = {
            metric: estimators.aggregate_cohorts(cohort_results, metric)
            for metric in EFFECT_METRICS
        }
        staggered_summary["_cohorts"] = [
            {
                "open_period": c.open_period,
                "treated_areas": c.treated_areas,
                "control_areas": c.control_areas,
                "pre_period": c.pre_period,
                "post_period": c.post_period,
                "dropped_reason": c.dropped_reason,
            }
            for c in cohort_results
        ]

    return InterventionAnalysis(
        intervention_type=intervention_type,
        intervention_ids=[iv.intervention_id for iv in type_records],
        timing_note=timing_note,
        cohorts={period: areas_ for period, areas_ in cohorts.items()},
        control_verdicts=[asdict(v) for v in control_verdicts],
        findings=findings,
        staggered_summary=staggered_summary,
        construction_note=construction_note,
        population_decomposition=_population_section(
            history, sorted(treated_set), periods),
    )


def _population_section(history: list[AreaHistoryRow], treated_areas: list[str],
                        periods: list[str]) -> list[dict]:
    """对已开放片区做边界版本与人口分解；观察窗不足两期则跳过。"""
    if len(periods) < 2 or not treated_areas:
        return []
    pre_period, post_period = periods[0], periods[-1]
    section = []
    for area_id in treated_areas:
        decomp = population.decompose_population(history, area_id, pre_period, post_period)
        section.append(asdict(decomp))
    return section


def run_analysis(
    *, data_dir: str | Path, as_of: date | None = None, run_id: str | None = None,
) -> AnalysisRun:
    data_dir = Path(data_dir)
    period_path = data_dir / "area-periods.csv"
    intervention_path = data_dir / "interventions.csv"
    measures_path = data_dir / "other-measures.csv"
    history_path = data_dir / "area-history.csv"

    raw_rows = load_period_rows(period_path)
    snapshot = as_of_snapshot(raw_rows, as_of)
    interventions = load_interventions(intervention_path)
    measures = load_other_measures(measures_path)
    history = load_area_history(history_path)

    periods = sort_periods(row.period for row in snapshot)
    areas = sorted({row.area_id for row in snapshot})
    timeline = build_timeline(interventions, periods, all_areas=areas)
    index = estimators.obs_index(snapshot)

    population_checks = [
        population.reconcile_totals(
            history,
            {row.area_id: row.values.get("residents") for row in snapshot if row.period == period},
            area, period)
        for period in periods for area in areas
    ]

    analyses = [
        analyze_intervention_type(
            intervention_type=iv_type, interventions=interventions, timeline=timeline,
            index=index, periods=periods, measures=measures, history=history, areas=areas,
        )
        for iv_type in timeline.types
    ]

    global_warnings = []
    if len(periods) < 3:
        global_warnings.append(
            f"观察窗仅 {len(periods)} 个期别，平行趋势检验无法达到 A 级所需的两期前期要求，"
            "所有对照结论最高为 B 级；延长历史数据后可重跑升级")
    if any(row.values.get(metric) is None for row in snapshot for metric in EFFECT_METRICS):
        global_warnings.append("存在指标缺失，所有计算按缺失跳过处理，未做零值填充")

    fingerprints = {
        path.name: _sha256(path)
        for path in (period_path, intervention_path, measures_path, history_path)
    }
    return AnalysisRun(
        run_id=run_id or f"run-{date.today().isoformat()}-{(as_of or date.today()).isoformat()}",
        as_of=as_of.isoformat() if as_of else "latest",
        generated_on=date.today().isoformat(),
        observation_periods=periods,
        areas=areas,
        sample_availability=estimators.sample_availability(index, areas, periods, ALL_METRICS),
        population_checks=population_checks,
        interventions=analyses,
        global_warnings=global_warnings,
        input_fingerprints=fingerprints,
    )


def _sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
