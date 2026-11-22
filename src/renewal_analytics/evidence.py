"""证据强度分级。

四级口径（报告中必须原样使用，禁止把低级别结论表述为因果数字）：

- ``A 可信对照``：有效对照片区、DiD 可估计、至少两期前期且平行趋势无明显背离、
  无落在处理组/对照组上的非对称同期措施、各比较期样本齐全。
- ``B 对照倾向``：具备有效对照且 DiD 可估计，但平行趋势无法检验（前期不足/缺失）、
  对照片区偏少或存在需要提示的样本问题；可作有条件的因果推断。
- ``C 仅相关``：无合格对照（全部被审查/排除）或存在非对称同期措施无法剥离；
  只报告描述性前后变化与相关性，DiD 即使算出数字也不得作为因果结论。
- ``D 资料不足``：处理片区自身比较期指标缺失、干预时间无法确定等；
  不输出效应数字，只列缺口与补数建议。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .estimators import DiDEstimate, NaiveChange, PreTrendResult
from .loading import OtherMeasure
from .periods import period_index
from .timeline import ControlVerdict

TIERS = ("A", "B", "C", "D")
TIER_LABELS = {
    "A": "可信对照",
    "B": "对照倾向",
    "C": "仅相关",
    "D": "资料不足",
}


@dataclass
class EvidenceVerdict:
    tier: str
    label: str
    reasons: list[str] = field(default_factory=list)
    causal_claim_allowed: bool = False
    downgraded_from: str | None = None

    def as_text(self) -> str:
        head = f"{self.tier}（{self.label}）"
        if self.downgraded_from:
            head += f"，由 {self.downgraded_from} 降级"
        return head + ("：" + "；".join(self.reasons) if self.reasons else "")


def measures_affecting(
    measures: list[OtherMeasure],
    treated_areas: set[str],
    control_areas: set[str],
    pre_period: str,
    post_period: str,
) -> tuple[list[OtherMeasure], list[OtherMeasure]]:
    """把同期措施按落点分为非对称（只落在一组）与共同（两组都有）两类。

    措施在 [start_period, end_period] 内与比较窗口 [pre, post] 有重叠即视为相关。
    """
    asymmetric, common = [], []
    groups = (("treated", treated_areas), ("control", control_areas))
    for measure in measures:
        end = measure.end_period or post_period
        if period_index(end) < period_index(pre_period) or period_index(measure.start_period) > period_index(post_period):
            continue
        in_treated = measure.area_id in treated_areas
        in_control = measure.area_id in control_areas
        if in_treated and in_control:
            common.append(measure)
        elif in_treated or in_control:
            asymmetric.append(measure)
    return asymmetric, common


def grade_evidence(
    *,
    naive: NaiveChange,
    did: DiDEstimate | None,
    pretrend: PreTrendResult | None,
    control_verdicts: list[ControlVerdict],
    asymmetric_measures: list[OtherMeasure],
    common_measures: list[OtherMeasure],
    intervention_timing_known: bool,
) -> EvidenceVerdict:
    reasons: list[str] = []

    # 1. 时间无法定位或自身基线缺失 -> D
    if not intervention_timing_known:
        return EvidenceVerdict("D", TIER_LABELS["D"],
                               ["干预实际开工/开放日期缺失，无法界定处理前后阶段，不输出效应数字；"
                                "建议先补全开放日期再重跑"],
                               causal_claim_allowed=False)
    if not naive.available:
        return EvidenceVerdict("D", TIER_LABELS["D"],
                               [f"处理片区自身比较无法成立：{naive.reason}"],
                               causal_claim_allowed=False)

    eligible_controls = [v for v in control_verdicts if v.eligible]
    censored = [v for v in control_verdicts if not v.eligible]

    # 2. 无合格对照 -> C（只描述、只相关）
    if not eligible_controls:
        detail = "；".join(v.reason for v in censored) or "观察窗内没有可作为对照的片区"
        return EvidenceVerdict("C", TIER_LABELS["C"],
                               [f"无合格对照（{detail}），无法扣除共同趋势，"
                                "只报告片区自身前后变化，结论仅为相关"],
                               causal_claim_allowed=False)

    # 3. DiD 本身不可估计（样本缺失）-> D：资料缺口优先于混杂降级，
    #    此时任何效应数字（无论是否因果）都无法计算
    if did is None or not did.available:
        return EvidenceVerdict("D", TIER_LABELS["D"],
                               [f"DiD 无法估计：{did.reason if did else '缺少比较组信息'}；"
                                "建议补报缺失月份后重跑，期间不输出效应数字"],
                               causal_claim_allowed=False)

    # 4. 存在非对称同期措施 -> C（DiD 可算但不能作因果结论，仅保留相关描述）
    if asymmetric_measures:
        names = "、".join(f"{m.measure_id}({m.type}@{m.area_id})" for m in asymmetric_measures)
        return EvidenceVerdict("C", TIER_LABELS["C"],
                               [f"存在只落在单一比较组的同期措施：{names}，"
                                "其影响无法通过 DiD 剥离，效应数字不能作因果结论，仅保留相关描述"],
                               causal_claim_allowed=False)

    reasons.append(f"合格对照片区 {len(eligible_controls)} 个："
                   + "、".join(v.area_id for v in eligible_controls))
    if censored:
        reasons.append("另有片区对照失效：" + "；".join(f"{v.area_id}（{v.reason}）" for v in censored))
    if common_measures:
        names = "、".join(f"{m.measure_id}({m.type})" for m in common_measures)
        reasons.append(f"两组均受同期措施 {names} 影响，DiD 可扣除其共同部分，但特异性影响仍需留意")
    reasons.extend(did.warnings)

    # 4. 平行趋势检验
    if pretrend is None or not pretrend.available:
        reasons.append(f"平行趋势无法检验（{pretrend.reason if pretrend else '无前期数据'}），"
                       "无法排除处理前固有趋势差异")
        return EvidenceVerdict("B", TIER_LABELS["B"], reasons, causal_claim_allowed=True,
                               downgraded_from="A")

    # 简单经验阈值：前期斜率差超过处理后 DiD 点估计的一半，视为明显背离
    if did.did is not None and abs(did.did) > 0 and pretrend.slope_difference is not None \
            and abs(pretrend.slope_difference) > abs(did.did) / 2:
        reasons.append(
            f"前期斜率差（每期 {pretrend.slope_difference:.2f}）相对处理后变化（{did.did:.2f}）"
            "偏大，平行趋势假设存在明显背离风险")
        return EvidenceVerdict("B", TIER_LABELS["B"], reasons, causal_claim_allowed=True,
                               downgraded_from="A")

    reasons.append("处理前至少两期双组样本齐全，平行趋势未见明显背离")
    return EvidenceVerdict("A", TIER_LABELS["A"], reasons, causal_claim_allowed=True)
