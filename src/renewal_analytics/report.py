"""中文 Markdown 报告生成。

报告严格区分表述口径：

- A/B 级且 ``causal_claim_allowed`` 才使用"效应/归因"措辞，并同时给出区间；
- C 级只称"相关/前后变化"；D 级不出现效应数字，只列资料缺口；
- 每个结论后附样本量、对照依据、降级原因与共同变化因素。
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass

from .pipeline import AnalysisRun

METRIC_NAMES = {
    "service_use": "服务使用量",
    "walkability_score": "出行便利评分",
    "residents": "居民数",
}
TYPE_NAMES = {
    "pocket_park": "口袋空间改造",
    "road_access": "道路改善",
    "service_point": "社区服务点开放",
}


def _name(mapping: dict, key: str) -> str:
    return mapping.get(key, key)


def render_markdown(run: AnalysisRun | dict) -> str:
    if is_dataclass(run):
        run = asdict(run)
    lines: list[str] = []
    lines.append(f"# 微更新影响评估报告（运行版本 {run['run_id']}）")
    lines.append("")
    lines.append(f"- 生成日期：{run['generated_on']}；数据快照口径（as_of）：{run['as_of']}")
    lines.append(f"- 观察期别：{'、'.join(run['observation_periods'])}；覆盖片区：{'、'.join(run['areas'])}")
    lines.append("- 输入文件指纹：" + "；".join(f"{name}={fp}" for name, fp in run["input_fingerprints"].items()))
    lines.append("- 空单元格一律按缺失处理，未做零值填充；分级口径：A 可信对照 / B 对照倾向 / C 仅相关 / D 资料不足")
    lines.append("")

    if run["global_warnings"]:
        lines.append("## 一、全局提示（影响所有结论强度）")
        for warning in run["global_warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    lines.append("## 二、各阶段可用样本")
    lines.append("")
    lines.append("| 期别 | 指标 | 有效样本/片区数 | 缺失片区 | 缺失率 |")
    lines.append("|---|---|---|---|---|")
    for period, metrics in run["sample_availability"].items():
        for metric, info in metrics.items():
            missing = "、".join(info["missing_areas"]) or "—"
            rate = "—" if info["missing_rate"] is None else f"{info['missing_rate']:.0%}"
            lines.append(f"| {period} | {_name(METRIC_NAMES, metric)} | "
                         f"{info['n_available']}/{info['n_total']} | {missing} | {rate} |")
    lines.append("")

    lines.append("## 三、人口与边界核对")
    lines.append("")
    lines.append("| 片区 | 期别 | 上报居民数 | 区块汇总 | 口径差 |")
    lines.append("|---|---|---|---|---|")
    for check in run["population_checks"]:
        gap = "—" if check["gap"] is None else check["gap"]
        lines.append(f"| {check['area_id']} | {check['period']} | "
                     f"{check['reported_residents'] if check['reported_residents'] is not None else '缺失'} | "
                     f"{check['block_sum'] if check['block_sum'] is not None else '无边界资料'} | {gap} |")
    lines.append("")
    lines.append("> 口径差异提示两个来源的统计范围可能不同；若边界版本在观察窗内变更，"
                 "总量趋势不可直接比较，应按区块分解（见各干预的人口说明）。")
    lines.append("")

    lines.append("## 四、分干预评估")
    for block in run["interventions"]:
        lines.extend(_render_intervention(block))

    lines.append("## 五、结论口径汇总")
    lines.append("")
    lines.append("| 干预类型 | 指标 | 证据级别 | 是否可作因果表述 | 核心依据/降级原因 |")
    lines.append("|---|---|---|---|---|")
    for block in run["interventions"]:
        for finding in block["findings"]:
            ev = finding["evidence"]
            allowed = "是（附区间）" if ev["causal_claim_allowed"] else "否（仅相关或资料不足）"
            reason = "；".join(ev["reasons"][:2]) if ev["reasons"] else "—"
            lines.append(f"| {_name(TYPE_NAMES, block['intervention_type'])} | "
                         f"{_name(METRIC_NAMES, finding['metric'])} | {ev['tier']}（{ev['label']}） | "
                         f"{allowed} | {reason} |")
    lines.append("")
    lines.append("> 共同变化因素（季节波动、人口变化、同期其他措施）无法被前后均值相减扣除；"
                 "只有通过有效对照与 DiD 扣除共同趋势、且通过分级检查的结论才允许因果表述。")
    lines.append("")
    return "\n".join(lines)


def _render_intervention(block) -> list[str]:
    lines = [f"### {_name(TYPE_NAMES, block['intervention_type'])}"]
    lines.append("")
    lines.append(f"- 涉及记录：{'、'.join(block['intervention_ids'])}")
    lines.append(f"- 时间线说明：{block['timing_note']}")
    if block["construction_note"]:
        lines.append(f"- {block['construction_note']}")
    if block["cohorts"]:
        cohort_text = "；".join(f"{period} 开放：{'、'.join(areas)}"
                                for period, areas in block["cohorts"].items())
        lines.append(f"- 开放队列：{cohort_text}")
    lines.append("")

    lines.append("**对照组适用性**")
    lines.append("")
    lines.append("| 片区 | 是否合格 | 可用至期别 | 说明 |")
    lines.append("|---|---|---|---|")
    for verdict in block["control_verdicts"]:
        lines.append(f"| {verdict['area_id']} | {'合格' if verdict['eligible'] else '失效/排除'} | "
                     f"{verdict['usable_through'] or '—'} | {verdict['reason']} |")
    lines.append("")

    for finding in block["findings"]:
        lines.extend(_render_finding(finding))

    if block.get("population_decomposition"):
        lines.append("**人口构成与边界变化（区分总体变化与同类居民变化）**")
        lines.append("")
        lines.append("| 片区 | 边界版本（前→后） | 总量变化 | 持续区块同类变化 | 新增区块 | 流失区块 | 范围断裂 |")
        lines.append("|---|---|---|---|---|---|---|")
        for decomp in block["population_decomposition"]:
            added = "、".join(f"{b}(+{n})" for b, n in decomp["added_blocks"].items()) or "—"
            lost = "、".join(f"{b}(-{n})" for b, n in decomp["lost_blocks"].items()) or "—"
            lines.append(
                f"| {decomp['area_id']} | "
                f"{decomp['pre_version'] or '未知'}→{decomp['post_version'] or '未知'} | "
                f"{decomp['total_change']} | {decomp['persistent_change']} | {added} | {lost} | "
                f"{'是' if decomp['scope_break'] else '否'} |")
        for decomp in block["population_decomposition"]:
            if decomp["scope_break"]:
                lines.append(f"- {decomp['note']}")
        lines.append("")

    summary = block["staggered_summary"]
    if summary.get("available") or "_cohorts" in summary:
        lines.append("**分批队列估计（not-yet-treated 对照）**")
        lines.append("")
        for cohort in summary.get("_cohorts", []):
            if cohort["dropped_reason"]:
                lines.append(f"- 队列 {cohort['open_period']}（处理：{'、'.join(cohort['treated_areas'])}）："
                             f"不参与汇总——{cohort['dropped_reason']}")
            else:
                lines.append(f"- 队列 {cohort['open_period']}（处理：{'、'.join(cohort['treated_areas'])}；"
                             f"对照：{'、'.join(cohort['control_areas']) or '无'}），"
                             f"比较 {cohort['pre_period']} → {cohort['post_period']}")
        for metric, payload in summary.items():
            if metric.startswith("_"):
                continue
            if payload.get("available"):
                lines.append(f"- {_name(METRIC_NAMES, metric)}：{payload['cohort_count']} 个队列等权汇总 "
                             f"DiD = {payload['pooled_did']:.2f}（{payload['ci_note']}）")
            else:
                lines.append(f"- {_name(METRIC_NAMES, metric)}：无法汇总——{payload.get('reason', '资料不足')}")
        lines.append("")
    return lines


def _render_finding(finding) -> list[str]:
    metric = finding["metric"]
    lines = [f"**指标：{_name(METRIC_NAMES, metric)}**", ""]
    ev = finding["evidence"]
    lines.append(f"- 证据级别：{ev['tier']}（{ev['label']}）"
                 + (f"，由 {ev['downgraded_from']} 降级" if ev.get("downgraded_from") else ""))
    for reason in ev["reasons"]:
        lines.append(f"  - {reason}")

    naive = finding["naive"]
    if naive["available"]:
        lines.append(f"- 朴素前后差（仅描述）：{naive['pre_value']} → {naive['post_value']}，"
                     f"变化 {naive['change']}；{naive['reason']}")
    else:
        lines.append(f"- 朴素前后差不可计算：{naive['reason']}")

    did = finding["did"]
    if did and did["available"]:
        ci = did["ci95"]
        ci_text = f"，近似 95% 区间 [{ci[0]}, {ci[1]}]" if ci else "（样本不足，无区间）"
        lines.append(f"- 双重差分：DiD = {did['did']}{ci_text}")
        lines.append(f"  - 处理组 {did['pre_period']}→{did['post_period']}："
                     f"{did['treated_pre']:.2f}→{did['treated_post']:.2f}（样本 {did['treated_n_pre']}/{did['treated_n_post']}）")
        lines.append(f"  - 对照组 {did['pre_period']}→{did['post_period']}："
                     f"{did['control_pre']:.2f}→{did['control_post']:.2f}（样本 {did['control_n_pre']}/{did['control_n_post']}）")
        if did["warnings"]:
            for warning in did["warnings"]:
                lines.append(f"  - 提示：{warning}")
    elif did:
        lines.append(f"- 双重差分不可估计：{did['reason']}")

    pretrend = finding["pretrend"]
    if pretrend:
        if pretrend["available"]:
            lines.append(f"- 平行趋势检验：通过，前期斜率差 {pretrend['slope_difference']}（每期）")
        else:
            lines.append(f"- 平行趋势检验：{pretrend['reason']}")

    decomp = finding["count_decomposition"]
    if decomp and decomp["available"]:
        lines.append(f"- 数量/强度分解：强度效应 {decomp['rate_effect']}、规模效应 {decomp['size_effect']}、"
                     f"交互项 {decomp['interaction']}（人均使用率 {decomp['rate_pre']} → {decomp['rate_post']}）")

    if not ev["causal_claim_allowed"]:
        lines.append("- 口径限制：本指标结论**不得表述为项目因果效应**，仅可作为相关/描述性证据使用。")
    lines.append("")
    return lines


def render_comparison(comparison: dict) -> str:
    lines = ["# 新旧分析版本并列对比", ""]
    lines.append(f"- 旧版本：{comparison['old_run']}（as_of {comparison['old_as_of']}）")
    lines.append(f"- 新版本：{comparison['new_run']}（as_of {comparison['new_as_of']}）")
    if comparison["periods_changed"]:
        lines.append("- 观察期别范围发生变化，跨版本比较时请注意口径差异")
    lines.append("")
    lines.append("| 干预类型 | 指标 | 旧分级 | 新分级 | 旧 DiD | 新 DiD | 分级是否变化 |")
    lines.append("|---|---|---|---|---|---|---|")
    for row in comparison["rows"]:
        lines.append(
            f"| {_name(TYPE_NAMES, row['intervention_type'])} | {_name(METRIC_NAMES, row['metric'])} | "
            f"{row['old_tier'] or '—'} | {row['new_tier'] or '—'} | "
            f"{row['old_did'] if row['old_did'] is not None else '—'} | "
            f"{row['new_did'] if row['new_did'] is not None else '—'} | "
            f"{'是' if row['tier_changed'] else '否'} |")
    lines.append("")
    return "\n".join(lines)
