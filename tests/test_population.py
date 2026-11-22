import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from renewal_analytics.loading import load_area_history  # noqa: E402
from renewal_analytics.population import (  # noqa: E402
    blocks_at,
    decompose_population,
    version_at,
)

DATA = ROOT / "data"


def history(rows_spec):
    from renewal_analytics.loading import AreaHistoryRow
    return [
        AreaHistoryRow(version, area, block, residents, valid_from)
        for version, area, block, residents, valid_from in rows_spec
    ]


class PopulationTests(unittest.TestCase):
    def setUp(self):
        self.history = load_area_history(DATA / "area-history.csv")

    def test_version_resolves_by_period_start(self):
        self.assertEqual(version_at(self.history, "A-1", "2026-Q1"), "B1")
        self.assertEqual(version_at(self.history, "A-1", "2026-Q2"), "B2")

    def test_blocks_follow_version(self):
        self.assertEqual(blocks_at(self.history, "A-3", "2026-Q1"), {"BL-3": 760})
        self.assertEqual(blocks_at(self.history, "A-3", "2026-Q2"), {"BL-3": 770})

    def test_scope_break_with_added_and_lost_blocks(self):
        rows = history([
            ("B1", "A-1", "BL-1", 500, date(2025, 1, 1)),
            ("B1", "A-1", "BL-2", 300, date(2025, 1, 1)),
            ("B2", "A-1", "BL-1", 400, date(2026, 4, 1)),
            ("B2", "A-1", "BL-3", 200, date(2026, 4, 1)),
        ])
        result = decompose_population(rows, "A-1", "2026-Q1", "2026-Q2")
        self.assertTrue(result.scope_break)
        # 持续区块 BL-1：500 -> 400，才是同类居民变化
        self.assertEqual(result.persistent_change, -100)
        # 总量变化 600 - 800 = -200，混了新增/流失区块，不能直接当趋势
        self.assertEqual(result.total_change, -200)
        self.assertEqual(result.lost_blocks, {"BL-2": 300})
        self.assertEqual(result.added_blocks, {"BL-3": 200})
        self.assertIn("范围发生变化", result.note)

    def test_stable_boundary_marks_no_break(self):
        rows = history([
            ("B1", "A-1", "BL-1", 500, date(2025, 1, 1)),
            ("B1", "A-1", "BL-1", 520, date(2026, 4, 1)),
        ])
        result = decompose_population(rows, "A-1", "2026-Q1", "2026-Q2")
        # 同区块同版本号，仅人口数变化
        self.assertFalse(result.scope_break)
        self.assertEqual(result.persistent_change, 20)
        self.assertEqual(result.total_change, 20)

    def test_missing_history_returns_empty(self):
        self.assertEqual(blocks_at(self.history, "A-X", "2026-Q1"), {})
        self.assertIsNone(version_at(self.history, "A-X", "2026-Q1"))


if __name__ == "__main__":
    unittest.main()
