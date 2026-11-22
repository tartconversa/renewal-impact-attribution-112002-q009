import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from renewal_analytics.estimators import DiDEstimate, NaiveChange, PreTrendResult  # noqa: E402
from renewal_analytics.evidence import grade_evidence, measures_affecting  # noqa: E402
from renewal_analytics.loading import OtherMeasure  # noqa: E402
from renewal_analytics.timeline import ControlVerdict  # noqa: E402


def naive_ok(change=4):
    return NaiveChange("m", "T", "2026-Q1", "2026-Q2", 10, 10 + change, change, True, "ok")


def did_ok(value=6.0, controls=2):
    return DiDEstimate(
        "m", "2026-Q1", "2026-Q2", ["T"], [f"C{i}" for i in range(controls)],
        10.0, 18.0, 20.0, 22.0, 1, 1, controls, controls,
        value, (value - 2, value + 2), True, "ok", [])


def pretrend_ok(slope_diff=0.0):
    return PreTrendResult("m", {"2025-Q4": -10.0, "2026-Q1": -10.0}, slope_diff, True, "ok")


def pretrend_unavailable():
    return PreTrendResult("m", {"2026-Q1": -10.0}, None, False,
                          "处理前不足两个期别，无法检验平行趋势，结论需降级")


def good_controls(n=2):
    return [ControlVerdict(f"C{i}", True, "2026-Q2", "干净对照") for i in range(n)]


class EvidenceGradeTests(unittest.TestCase):
    def test_grade_a_with_parallel_pretrend_and_controls(self):
        verdict = grade_evidence(
            naive=naive_ok(), did=did_ok(), pretrend=pretrend_ok(),
            control_verdicts=good_controls(2), asymmetric_measures=[],
            common_measures=[], intervention_timing_known=True)
        self.assertEqual(verdict.tier, "A")
        self.assertTrue(verdict.causal_claim_allowed)

    def test_grade_b_when_pretrend_cannot_be_tested(self):
        verdict = grade_evidence(
            naive=naive_ok(), did=did_ok(), pretrend=pretrend_unavailable(),
            control_verdicts=good_controls(2), asymmetric_measures=[],
            common_measures=[], intervention_timing_known=True)
        self.assertEqual(verdict.tier, "B")
        self.assertEqual(verdict.downgraded_from, "A")
        self.assertTrue(verdict.causal_claim_allowed)

    def test_grade_b_when_pretrend_diverges(self):
        verdict = grade_evidence(
            naive=naive_ok(), did=did_ok(value=4.0),
            pretrend=pretrend_ok(slope_diff=3.0),  # |3| > |4|/2
            control_verdicts=good_controls(2), asymmetric_measures=[],
            common_measures=[], intervention_timing_known=True)
        self.assertEqual(verdict.tier, "B")
        self.assertTrue(any("背离" in r for r in verdict.reasons))

    def test_grade_c_when_all_controls_censored(self):
        censored = [ControlVerdict("C1", False, None, "于 2026-Q2 启动同类型措施，审查退出",
                                   "2026-Q2")]
        verdict = grade_evidence(
            naive=naive_ok(), did=did_ok(), pretrend=pretrend_ok(),
            control_verdicts=censored, asymmetric_measures=[],
            common_measures=[], intervention_timing_known=True)
        self.assertEqual(verdict.tier, "C")
        self.assertFalse(verdict.causal_claim_allowed)

    def test_grade_c_when_asymmetric_measure_confounds(self):
        asymmetric = [OtherMeasure("M-9", "T", "transit_change", "2026-Q1", "2026-Q2")]
        verdict = grade_evidence(
            naive=naive_ok(), did=did_ok(), pretrend=pretrend_ok(),
            control_verdicts=good_controls(), asymmetric_measures=asymmetric,
            common_measures=[], intervention_timing_known=True)
        self.assertEqual(verdict.tier, "C")
        self.assertFalse(verdict.causal_claim_allowed)
        self.assertTrue(any("同期措施" in r for r in verdict.reasons))

    def test_grade_d_when_timing_unknown(self):
        verdict = grade_evidence(
            naive=naive_ok(), did=did_ok(), pretrend=pretrend_ok(),
            control_verdicts=good_controls(), asymmetric_measures=[],
            common_measures=[], intervention_timing_known=False)
        self.assertEqual(verdict.tier, "D")
        self.assertFalse(verdict.causal_claim_allowed)

    def test_grade_d_when_baseline_missing(self):
        bad_naive = NaiveChange("m", "T", "2026-Q1", "2026-Q2", None, 14, None, False,
                                "前期或后期指标缺失，无法计算前后差（不补零）")
        verdict = grade_evidence(
            naive=bad_naive, did=did_ok(), pretrend=pretrend_ok(),
            control_verdicts=good_controls(), asymmetric_measures=[],
            common_measures=[], intervention_timing_known=True)
        self.assertEqual(verdict.tier, "D")

    def test_grade_d_when_did_not_estimable(self):
        bad_did = DiDEstimate(
            "m", "2026-Q1", "2026-Q2", ["T"], ["C"],
            None, 18.0, 20.0, None, 0, 1, 1, 0, None, None, False,
            "对照组在 2026-Q2 无有效样本，DiD 不可估计（缺失不补零）", [])
        verdict = grade_evidence(
            naive=naive_ok(), did=bad_did, pretrend=pretrend_ok(),
            control_verdicts=good_controls(), asymmetric_measures=[],
            common_measures=[], intervention_timing_known=True)
        self.assertEqual(verdict.tier, "D")
        self.assertFalse(verdict.causal_claim_allowed)

    def test_common_measures_are_logged_not_downgrading(self):
        common = [OtherMeasure("M-1", "C1", "transit_change", "2026-Q2", "2026-Q3")]
        verdict = grade_evidence(
            naive=naive_ok(), did=did_ok(), pretrend=pretrend_ok(),
            control_verdicts=good_controls(), asymmetric_measures=[],
            common_measures=common, intervention_timing_known=True)
        self.assertEqual(verdict.tier, "A")
        self.assertTrue(any("共同部分" in r for r in verdict.reasons))


class MeasureClassificationTests(unittest.TestCase):
    def test_asymmetric_vs_common_split(self):
        measures = [
            OtherMeasure("M-1", "T", "transit_change", "2026-Q1", "2026-Q2"),
            OtherMeasure("M-2", "C1", "transit_change", "2026-Q1", "2026-Q2"),
        ]
        asymmetric, common = measures_affecting(
            measures, {"T"}, {"C1", "C2"}, "2026-Q1", "2026-Q2")
        self.assertEqual({m.measure_id for m in asymmetric}, {"M-1", "M-2"})
        self.assertEqual(common, [])

    def test_out_of_window_measure_ignored(self):
        measures = [OtherMeasure("M-1", "T", "x", "2025-Q1", "2025-Q2")]
        asymmetric, common = measures_affecting(
            measures, {"T"}, {"C1"}, "2026-Q1", "2026-Q2")
        self.assertEqual(asymmetric, [])
        self.assertEqual(common, [])

    def test_open_ended_measure_still_counts(self):
        measures = [OtherMeasure("M-1", "T", "x", "2026-Q2", None)]
        asymmetric, _ = measures_affecting(
            measures, {"T"}, {"C1"}, "2026-Q1", "2026-Q2")
        self.assertEqual(len(asymmetric), 1)


if __name__ == "__main__":
    unittest.main()
