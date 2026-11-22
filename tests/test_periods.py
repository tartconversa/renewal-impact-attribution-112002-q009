import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from renewal_analytics.periods import (  # noqa: E402
    compare,
    parse_period,
    period_bounds,
    period_for_date,
    period_index,
    shift_period,
    sort_periods,
)


class PeriodTests(unittest.TestCase):
    def test_parse_and_sort(self):
        self.assertEqual(parse_period("2026-Q4"), (2026, 4))
        self.assertEqual(sort_periods(["2026-Q2", "2026-Q1", "2025-Q4"]),
                         ["2025-Q4", "2026-Q1", "2026-Q2"])

    def test_bounds(self):
        self.assertEqual(period_bounds("2026-Q1"), (date(2026, 1, 1), date(2026, 3, 31)))
        self.assertEqual(period_bounds("2024-Q4"), (date(2024, 10, 1), date(2024, 12, 31)))

    def test_date_to_period(self):
        self.assertEqual(period_for_date(date(2026, 5, 20)), "2026-Q2")
        self.assertEqual(period_for_date(date(2026, 1, 1)), "2026-Q1")

    def test_shift_and_compare(self):
        self.assertEqual(shift_period("2026-Q1", 1), "2026-Q2")
        self.assertEqual(shift_period("2026-Q1", -1), "2025-Q4")
        self.assertEqual(shift_period("2026-Q4", 1), "2027-Q1")
        self.assertEqual(compare("2026-Q1", "2026-Q2"), -1)
        self.assertEqual(period_index("2026-Q2") - period_index("2026-Q1"), 1)

    def test_bad_period_raises(self):
        with self.assertRaises(ValueError):
            parse_period("2026/2")


if __name__ == "__main__":
    unittest.main()
