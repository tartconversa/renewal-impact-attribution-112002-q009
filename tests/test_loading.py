import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from renewal_analytics.loading import (  # noqa: E402
    as_of_snapshot,
    load_interventions,
    load_other_measures,
    load_period_rows,
)

DATA = ROOT / "data"


class LoadingTests(unittest.TestCase):
    def test_empty_cells_become_none_not_zero(self):
        rows = load_period_rows(DATA / "area-periods.csv")
        a2_q2 = next(r for r in rows if r.area_id == "A-2" and r.period == "2026-Q2")
        self.assertIsNone(a2_q2.values["service_use"])
        self.assertEqual(a2_q2.values["walkability_score"], 58)

    def test_late_revision_kept_and_latest_wins(self):
        rows = load_period_rows(DATA / "area-periods.csv")
        # 同一 (A-1, 2026-Q1) 有两份记录：310（4 月到达）与 318（9 月修订）
        snapshot = as_of_snapshot(rows, None)
        revised = next(r for r in snapshot if r.area_id == "A-1" and r.period == "2026-Q1")
        self.assertEqual(revised.values["service_use"], 318)

    def test_as_of_snapshot_excludes_late_arrivals(self):
        rows = load_period_rows(DATA / "area-periods.csv")
        old_snapshot = as_of_snapshot(rows, date(2026, 8, 31))
        old_row = next(r for r in old_snapshot if r.area_id == "A-1" and r.period == "2026-Q1")
        self.assertEqual(old_row.values["service_use"], 310)
        # 截至 8 月底共 6 条记录到达；9 月的修订记录被排除
        self.assertEqual(len(old_snapshot), 6)
        later = as_of_snapshot(rows, None)
        self.assertEqual(len(later), 6)  # 修订只覆盖旧值，不新增 (片区, 期别) 键

    def test_intervention_revision_chain_marks_superseded(self):
        records = load_interventions(DATA / "interventions.csv")
        by_id = {r.intervention_id: r for r in records}
        self.assertTrue(by_id["I-1"].superseded)
        self.assertFalse(by_id["I-1-R"].superseded)
        self.assertEqual(by_id["I-1-R"].revision_of, "I-1")
        # 实际开放日期以修订记录为准
        self.assertEqual(by_id["I-1-R"].actual_open, date(2026, 5, 25))

    def test_other_measures_loaded(self):
        measures = load_other_measures(DATA / "other-measures.csv")
        self.assertEqual(len(measures), 4)
        m1 = next(m for m in measures if m.measure_id == "M-1")
        self.assertEqual((m1.start_period, m1.end_period), ("2026-Q2", "2026-Q3"))
        self.assertEqual(m1.area_id, "A-1")


if __name__ == "__main__":
    unittest.main()
