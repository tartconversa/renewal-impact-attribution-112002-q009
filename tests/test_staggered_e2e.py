import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from renewal_analytics.pipeline import run_analysis  # noqa: E402

# 四期 × 四片区合成数据：
# T1 于 2026-Q2 开放、T2 于 2026-Q3 开放（分批）；
# P 仅有 2027 年的计划（观察窗内保持 planned，禁止当对照）；C 全程干净。
# 前期 T1/T2/C 完全平行（每期 +2），开放当季处理组额外 +6。
PERIOD_ROWS = [
    # area, period, residents, service_use, walkability
    ("T1", "2025-Q4", 500, 100, 40),
    ("T1", "2026-Q1", 500, 110, 42),
    ("T1", "2026-Q2", 500, 140, 50),
    ("T1", "2026-Q3", 500, 150, 52),
    ("T2", "2025-Q4", 500, 100, 40),
    ("T2", "2026-Q1", 500, 110, 42),
    ("T2", "2026-Q2", 500, 120, 44),
    ("T2", "2026-Q3", 500, 160, 52),
    ("C", "2025-Q4", 500, 100, 40),
    ("C", "2026-Q1", 500, 110, 42),
    ("C", "2026-Q2", 500, 120, 44),
    ("C", "2026-Q3", 500, 130, 46),
    ("P", "2025-Q4", 500, 100, 40),
    ("P", "2026-Q1", 500, 110, 42),
    ("P", "2026-Q2", 500, 120, 44),
    ("P", "2026-Q3", 500, 130, 46),
]

INTERVENTION_ROWS = [
    # id, area, type, planned_start, actual_open, revision_of
    ("I-T1", "T1", "pocket_park", "2026-04-01", "2026-05-10", ""),
    ("I-T2", "T2", "pocket_park", "2026-07-01", "2026-08-05", ""),
    ("I-P", "P", "pocket_park", "2027-03-01", "", ""),
]

HISTORY_ROWS = [
    # version, area, block, residents, valid_from
    ("B1", "T1", "BT1", 500, "2025-01-01"),
    ("B1", "T2", "BT2", 500, "2025-01-01"),
    ("B1", "C", "BC", 500, "2025-01-01"),
    ("B1", "P", "BP", 500, "2025-01-01"),
]


class StaggeredEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.data = self.tmp / "data"
        self.data.mkdir()
        self._write_csvs()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_csvs(self):
        period_csv = "area_id,period,residents,service_use,walkability_score,received_at\n"
        period_csv += "".join(
            f"{area},{period},{res},{svc},{walk},2026-10-15\n"
            for area, period, res, svc, walk in PERIOD_ROWS)
        (self.data / "area-periods.csv").write_text(period_csv, encoding="utf-8")

        iv_csv = "intervention_id,area_id,type,planned_start,actual_open,revision_of\n"
        iv_csv += "".join(",".join(row) + "\n" for row in INTERVENTION_ROWS)
        (self.data / "interventions.csv").write_text(iv_csv, encoding="utf-8")

        (self.data / "other-measures.csv").write_text(
            "measure_id,area_id,type,start_period,end_period\n", encoding="utf-8")
        hist_csv = "boundary_version,area_id,block,residents,valid_from\n"
        hist_csv += "".join(",".join(map(str, row)) + "\n" for row in HISTORY_ROWS)
        (self.data / "area-history.csv").write_text(hist_csv, encoding="utf-8")

    def _park_block(self, run):
        return next(b for b in run.interventions if b.intervention_type == "pocket_park")

    def test_grade_a_with_two_pre_periods_and_parallel_trend(self):
        run = run_analysis(data_dir=self.data, run_id="staggered")
        park = self._park_block(run)
        walk = next(f for f in park.findings if f.metric == "walkability_score")
        # 两期前期、平行趋势、无同期措施、对照干净 -> A 级
        self.assertEqual(walk.evidence["tier"], "A")
        self.assertTrue(walk.evidence["causal_claim_allowed"])
        # 主 DiD 窗口 2026-Q1 -> 2026-Q3：
        # 处理组 (T1,T2) 均值 42 -> 52 (+10)；对照 C 42 -> 46 (+4) -> DiD 6
        self.assertEqual(walk.did["did"], 6.0)
        # 对照仅 1 个片区，其单元格 n=1 无法估计抽样方差 -> 诚实不给区间并给出提示
        self.assertIsNone(walk.did["ci95"])
        self.assertTrue(any("不确定性" in w for w in walk.did["warnings"]))
        self.assertTrue(any("对照仅 1 个片区" in w for w in walk.did["warnings"]))

    def test_not_yet_treated_controls_per_cohort(self):
        run = run_analysis(data_dir=self.data, run_id="staggered-2")
        park = self._park_block(run)
        cohorts = {c["open_period"]: c for c in park.staggered_summary["_cohorts"]}
        # Q2 队列（T1）：T2 尚未处理，可与 C 一起当对照；P 被排除
        q2 = cohorts["2026-Q2"]
        self.assertEqual(q2["treated_areas"], ["T1"])
        self.assertEqual(set(q2["control_areas"]), {"T2", "C"})
        self.assertNotIn("P", q2["control_areas"])
        # Q3 队列（T2）：T1 已开放，禁止回流为对照；只剩 C
        q3 = cohorts["2026-Q3"]
        self.assertEqual(q3["treated_areas"], ["T2"])
        self.assertEqual(q3["control_areas"], ["C"])

        pooled = park.staggered_summary["walkability_score"]
        self.assertTrue(pooled["available"])
        # 两个队列各 +6，等权汇总仍为 6
        self.assertAlmostEqual(pooled["pooled_did"], 6.0, places=6)

    def test_planned_only_area_never_appears_as_control(self):
        run = run_analysis(data_dir=self.data, run_id="staggered-3")
        park = self._park_block(run)
        verdict_areas = {v["area_id"] for v in park.control_verdicts}
        self.assertNotIn("P", verdict_areas)
        # P 也不在处理组中
        self.assertNotIn("P", park.cohorts.get("2026-Q2", []))
        self.assertNotIn("P", park.cohorts.get("2026-Q3", []))

    def test_cohort_open_periods_follow_actual_dates(self):
        run = run_analysis(data_dir=self.data, run_id="staggered-4")
        park = self._park_block(run)
        self.assertEqual(park.cohorts, {
            "2026-Q2": ["T1"],
            "2026-Q3": ["T2"],
        })

    def test_ci95_available_with_two_control_areas(self):
        # 追加第二个干净对照片区 C2：变化斜率平行（每期 +2）但水平不同，
        # 使对照组两片区之间存在可估计的组间方差
        extra = [
            ("C2", "2025-Q4", 500, 100, 30),
            ("C2", "2026-Q1", 500, 110, 32),
            ("C2", "2026-Q2", 500, 120, 34),
            ("C2", "2026-Q3", 500, 130, 36),
        ]
        period_path = self.data / "area-periods.csv"
        with period_path.open("a", encoding="utf-8") as stream:
            for area, period, res, svc, walk in extra:
                stream.write(f"{area},{period},{res},{svc},{walk},2026-10-15\n")
        hist_path = self.data / "area-history.csv"
        with hist_path.open("a", encoding="utf-8") as stream:
            stream.write("B1,C2,BC2,500,2025-01-01\n")

        run = run_analysis(data_dir=self.data, run_id="staggered-5")
        park = self._park_block(run)
        walk = next(f for f in park.findings if f.metric == "walkability_score")
        self.assertEqual(walk.evidence["tier"], "A")
        self.assertIsNotNone(walk.did["ci95"])
        low, high = walk.did["ci95"]
        self.assertLess(low, walk.did["did"])
        self.assertLess(walk.did["did"], high)


if __name__ == "__main__":
    unittest.main()
