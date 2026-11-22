import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from renewal_analytics.estimators import (  # noqa: E402
    decompose_count_change,
    did_estimate,
    naive_change,
    obs_index,
    pretrend_check,
    sample_availability,
    staggered_cohort_estimates,
)
from renewal_analytics.loading import PeriodObs  # noqa: E402

METRICS = ("residents", "service_use", "walkability_score")


def make_rows(spec, received=date(2026, 9, 1)):
    """spec: {area: {period: {metric: value or None}}}"""
    rows = []
    for area, periods in spec.items():
        for period, values in periods.items():
            full = {metric: values.get(metric) for metric in METRICS}
            rows.append(PeriodObs(area, period, full, received))
    return rows


# 四期数据：处理组 T 在前两期与对照 C 平行，Q3 开放后处理组额外抬升
PARALLEL_SPEC = {
    "T": {
        "2025-Q4": {"walkability_score": 40, "service_use": 100, "residents": 500},
        "2026-Q1": {"walkability_score": 42, "service_use": 110, "residents": 500},
        "2026-Q2": {"walkability_score": 50, "service_use": 140, "residents": 500},
        "2026-Q3": {"walkability_score": 52, "service_use": 150, "residents": 500},
    },
    "C1": {
        "2025-Q4": {"walkability_score": 60, "service_use": 200, "residents": 800},
        "2026-Q1": {"walkability_score": 62, "service_use": 210, "residents": 800},
        "2026-Q2": {"walkability_score": 64, "service_use": 220, "residents": 800},
        "2026-Q3": {"walkability_score": 66, "service_use": 230, "residents": 800},
    },
    "C2": {
        "2025-Q4": {"walkability_score": 50, "service_use": 160, "residents": 600},
        "2026-Q1": {"walkability_score": 52, "service_use": 170, "residents": 600},
        "2026-Q2": {"walkability_score": 54, "service_use": 180, "residents": 600},
        "2026-Q3": {"walkability_score": 56, "service_use": 190, "residents": 600},
    },
}


class EstimatorTests(unittest.TestCase):
    def setUp(self):
        self.index = obs_index(make_rows(PARALLEL_SPEC))

    def test_naive_change_includes_common_trend(self):
        change = naive_change(self.index, "T", "2026-Q1", "2026-Q2", "walkability_score")
        self.assertTrue(change.available)
        self.assertEqual(change.change, 8)  # 含共同趋势 +2 与处理效应 +6

    def test_did_removes_common_trend(self):
        result = did_estimate(self.index, ["T"], ["C1", "C2"], "2026-Q1", "2026-Q2",
                              "walkability_score")
        # 处理组仅 1 个片区，四格中处理组单元格 n=1 无法估计抽样方差 -> 不给区间但保留点估计
        self.assertTrue(result.available)
        self.assertAlmostEqual(result.did, 6.0, places=6)
        self.assertIsNone(result.ci95)
        self.assertTrue(any("不确定性" in w for w in result.warnings))
        self.assertEqual(result.control_n_pre, 2)

    def test_missing_value_blocks_did_without_zero_fill(self):
        spec = {
            "T": {"2026-Q1": {"service_use": 10}, "2026-Q2": {"service_use": 12}},
            "C": {"2026-Q1": {"service_use": 20}, "2026-Q2": {"service_use": None}},
        }
        index = obs_index(make_rows(spec))
        result = did_estimate(index, ["T"], ["C"], "2026-Q1", "2026-Q2", "service_use")
        self.assertFalse(result.available)
        self.assertIsNone(result.did)
        self.assertIn("不补零", result.reason)

    def test_pretrend_parallel_slopes_pass(self):
        result = pretrend_check(self.index, ["T"], ["C1", "C2"],
                                ["2025-Q4", "2026-Q1"], "walkability_score")
        self.assertTrue(result.available)
        self.assertAlmostEqual(result.slope_difference, 0.0, places=6)

    def test_pretrend_detects_diverging_slopes(self):
        spec = {
            "T": {"2025-Q4": {"walkability_score": 40}, "2026-Q1": {"walkability_score": 48}},
            "C": {"2025-Q4": {"walkability_score": 60}, "2026-Q1": {"walkability_score": 62}},
        }
        index = obs_index(make_rows(spec))
        result = pretrend_check(index, ["T"], ["C"], ["2025-Q4", "2026-Q1"], "walkability_score")
        self.assertTrue(result.available)
        self.assertAlmostEqual(result.slope_difference, 6.0, places=6)

    def test_pretrend_needs_two_pre_periods(self):
        result = pretrend_check(self.index, ["T"], ["C1"], ["2026-Q1"], "walkability_score")
        self.assertFalse(result.available)
        self.assertIn("不足两个期别", result.reason)

    def test_count_decomposition_separates_intensity_and_size(self):
        spec = {
            "T": {
                "2026-Q1": {"service_use": 100, "residents": 500},
                "2026-Q2": {"service_use": 132, "residents": 600},
            }
        }
        index = obs_index(make_rows(spec))
        decomp = decompose_count_change(index, "T", "2026-Q1", "2026-Q2", "service_use")
        self.assertTrue(decomp.available)
        # 人均 0.20 -> 0.22；强度效应 0.02*500=10，规模效应 0.20*100=20，交互 2
        self.assertAlmostEqual(decomp.rate_effect, 10.0, places=6)
        self.assertAlmostEqual(decomp.size_effect, 20.0, places=6)
        self.assertAlmostEqual(decomp.interaction, 2.0, places=6)
        self.assertAlmostEqual(
            decomp.rate_effect + decomp.size_effect + decomp.interaction, 32.0, places=6)

    def test_sample_availability_reports_missing(self):
        spec = {"T": {"2026-Q1": {"service_use": None},
                       "2026-Q2": {"service_use": 5}}}
        index = obs_index(make_rows(spec))
        table = sample_availability(index, ["T"], ["2026-Q1", "2026-Q2"], ["service_use"])
        self.assertEqual(table["2026-Q1"]["service_use"]["n_available"], 0)
        self.assertEqual(table["2026-Q1"]["service_use"]["missing_areas"], ["T"])
        self.assertEqual(table["2026-Q2"]["service_use"]["n_available"], 1)

    def test_staggered_cohorts_use_not_yet_treated_controls(self):
        # T1 于 2026-Q2 开放，T2 于 2026-Q3 开放；T1 的 Q2 队列对照可含 T2，
        # T2 的 Q3 队列对照不得含已开放的 T1。
        spec = {
            "T1": {p: {"walkability_score": v} for p, v in zip(
                ["2026-Q1", "2026-Q2", "2026-Q3"], [40, 46, 48])},
            "T2": {p: {"walkability_score": v} for p, v in zip(
                ["2026-Q1", "2026-Q2", "2026-Q3"], [40, 42, 50])},
            "C": {p: {"walkability_score": v} for p, v in zip(
                ["2026-Q1", "2026-Q2", "2026-Q3"], [40, 42, 44])},
        }
        index = obs_index(make_rows(spec))

        # 用极简时间线替身模拟审查回调
        def fake_control_provider(iv_type, window_last, treated_set):
            from renewal_analytics.timeline import ControlVerdict
            verdicts = []
            for area in ("T1", "T2", "C"):
                if area in treated_set:
                    continue
                if area == "T2" and window_last == "2026-Q2":
                    # T2 尚未处理，是 T1 队列的合格对照
                    verdicts.append(ControlVerdict(area, True, window_last, "干净"))
                elif area == "T2":
                    # T2 自身队列时已在处理组，不会出现在候选中
                    verdicts.append(ControlVerdict(area, False, "2026-Q2",
                                                    "于 2026-Q3 启动", "2026-Q3"))
                elif area == "T1" and window_last == "2026-Q3":
                    # T1 早已开放，禁止充当 T2 队列的对照
                    verdicts.append(ControlVerdict(area, False, "2026-Q1",
                                                    "于 2026-Q2 启动同类型措施，审查退出",
                                                    "2026-Q2"))
                else:
                    verdicts.append(ControlVerdict(area, True, window_last, "干净"))
            return verdicts

        cohorts = {"2026-Q2": ["T1"], "2026-Q3": ["T2"]}
        results = staggered_cohort_estimates(
            index, cohorts, fake_control_provider, "pocket_park",
            ["2026-Q1"], ["walkability_score"])
        q2, q3 = results
        self.assertIn("T2", q2.control_areas)       # 尚未处理，可作对照
        self.assertNotIn("T1", q3.control_areas)    # 已处理，禁止当对照
        self.assertIn("C", q3.control_areas)
        self.assertTrue(q2.estimates["walkability_score"].available)


if __name__ == "__main__":
    unittest.main()
