import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from renewal_analytics.loading import Intervention, load_interventions  # noqa: E402
from renewal_analytics.periods import period_for_date  # noqa: E402
from renewal_analytics.timeline import (  # noqa: E402
    assess_controls,
    build_timeline,
    open_cohorts,
)

DATA = ROOT / "data"
PERIODS = ["2026-Q1", "2026-Q2"]


def iv(intervention_id, area_id, iv_type, planned=None, opened=None, revision_of=None):
    return Intervention(intervention_id, area_id, iv_type, planned, opened, revision_of)


class TimelineFixtureTests(unittest.TestCase):
    def setUp(self):
        self.interventions = load_interventions(DATA / "interventions.csv")
        self.timeline = build_timeline(self.interventions, PERIODS)

    def test_open_uses_actual_date_after_revision(self):
        # I-1-R 修订后实际开放 2026-05-25 -> Q2；Q1 应为施工状态
        self.assertEqual(self.timeline.cell("A-1", "pocket_park", "2026-Q1"), "construction")
        self.assertEqual(self.timeline.cell("A-1", "pocket_park", "2026-Q2"), "open")

    def test_planned_without_open_is_construction_after_start(self):
        # I-4：计划 2026-05-01 开工、无实际开放 -> Q2 为 construction
        self.assertEqual(self.timeline.cell("A-3", "pocket_park", "2026-Q1"), "planned")
        self.assertEqual(self.timeline.cell("A-3", "pocket_park", "2026-Q2"), "construction")

    def test_cohorts_only_include_actually_open_areas(self):
        cohorts = open_cohorts(self.timeline, "pocket_park")
        self.assertEqual(cohorts, {"2026-Q2": ["A-1"]})

    def test_control_censoring_when_control_starts_same_measure(self):
        verdicts = assess_controls(self.timeline, "pocket_park", "2026-Q2", {"A-1"})
        by_area = {v.area_id: v for v in verdicts}
        # A-2 全程无口袋公园：合格对照
        self.assertTrue(by_area["A-2"].eligible)
        # A-3 在 Q2 启动同类施工：对照失效并给出审查期别
        self.assertFalse(by_area["A-3"].eligible)
        self.assertEqual(by_area["A-3"].first_event_period, "2026-Q2")
        self.assertEqual(by_area["A-3"].usable_through, "2026-Q1")

    def test_service_point_not_open_produces_no_cohort(self):
        cohorts = open_cohorts(self.timeline, "service_point")
        self.assertEqual(cohorts, {})

    def test_planned_only_area_can_be_excluded_from_controls(self):
        # 道路改善：A-2 仅在 Q2 处于 planned（开工日 8 月在观察窗之后），
        # 作为自身有项目的片区被排除；A-1/A-3 才是候选对照
        verdicts = assess_controls(
            self.timeline, "road_access", "2026-Q2", set(), excluded_areas={"A-2"})
        verdict_areas = {v.area_id for v in verdicts}
        self.assertNotIn("A-2", verdict_areas)
        self.assertEqual(verdict_areas, {"A-1", "A-3"})
        self.assertTrue(all(v.eligible for v in verdicts))

    def test_construction_area_listed_as_censored_not_silent(self):
        # 服务点：A-2 在 Q2 施工，应明确出现在对照名单并标记失效，而非静默消失
        verdicts = assess_controls(self.timeline, "service_point", "2026-Q2", set())
        a2 = next(v for v in verdicts if v.area_id == "A-2")
        self.assertFalse(a2.eligible)
        self.assertEqual(a2.first_event_period, "2026-Q2")
        self.assertIn("审查", a2.reason)


class TimingUnknownTests(unittest.TestCase):
    def test_unknown_timing_area_excluded_from_control(self):
        interventions = [
            iv("T-1", "A-1", "pocket_park", date(2026, 3, 1), date(2026, 5, 1)),
            iv("X-1", "A-9", "pocket_park"),  # 无任何日期
        ]
        timeline = build_timeline(interventions, PERIODS)
        self.assertEqual(timeline.cell("A-9", "pocket_park", "2026-Q2"), "unknown_timing")
        verdicts = assess_controls(timeline, "pocket_park", "2026-Q2", {"A-1"})
        verdict = next(v for v in verdicts if v.area_id == "A-9")
        self.assertFalse(verdict.eligible)
        self.assertIsNone(verdict.usable_through)

    def test_actual_open_differs_from_plan(self):
        # 计划 Q1 开工、实际 Q2 才开放
        interventions = [iv("T-1", "A-1", "road_access", date(2026, 1, 15), date(2026, 5, 2))]
        timeline = build_timeline(interventions, PERIODS)
        self.assertEqual(timeline.cell("A-1", "road_access", "2026-Q1"), "construction")
        self.assertEqual(timeline.cell("A-1", "road_access", "2026-Q2"), "open")
        self.assertEqual(period_for_date(interventions[0].actual_open), "2026-Q2")


if __name__ == "__main__":
    unittest.main()
