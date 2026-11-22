import json
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from renewal_analytics.pipeline import run_analysis  # noqa: E402
from renewal_analytics.report import render_comparison, render_markdown  # noqa: E402
from renewal_analytics.versioning import (  # noqa: E402
    compare_runs,
    load_manifest,
    load_run,
    reproduce,
    save_run,
)

DATA = ROOT / "data"


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.runs_dir = self.tmp / "runs"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _block(self, run, iv_type):
        return next(b for b in run.interventions if b.intervention_type == iv_type)

    def _finding(self, block, metric):
        return next(f for f in block.findings if f.metric == metric)

    def test_full_run_structure_and_fixture_outcomes(self):
        run = run_analysis(data_dir=DATA, run_id="t1")
        self.assertEqual(run.observation_periods, ["2026-Q1", "2026-Q2"])
        self.assertEqual(run.areas, ["A-1", "A-2", "A-3"])

        park = self._block(run, "pocket_park")
        # 修订记录生效：开放时间以 I-1-R 的 2026-05-25 为准
        self.assertTrue(any("2026-Q2" in note and "实际" in note for note in [park.timing_note]))
        self.assertEqual(park.cohorts, {"2026-Q2": ["A-1"]})

        controls = {v["area_id"]: v for v in park.control_verdicts}
        self.assertTrue(controls["A-2"]["eligible"])
        self.assertFalse(controls["A-3"]["eligible"])
        self.assertEqual(controls["A-3"]["first_event_period"], "2026-Q2")

        # 出行便利：DiD = (52-48) - (58-57) = 3；
        # 但 A-1 独有的 transit_change（M-1）构成非对称同期措施 -> C 级，数字仅作相关参考
        walk = self._finding(park, "walkability_score")
        self.assertEqual(walk.evidence["tier"], "C")
        self.assertFalse(walk.evidence["causal_claim_allowed"])
        self.assertEqual(walk.did["did"], 3.0)
        self.assertTrue(any("同期措施" in r for r in walk.evidence["reasons"]))
        # 每组每片区仅 1 个样本，无法估计区间
        self.assertIsNone(walk.did["ci95"])
        self.assertTrue(any("不确定性" in w for w in walk.did["warnings"]))

        # 服务使用：对照 A-2 的 Q2 缺失 -> DiD 不可估计 -> D，且不产生效应数字
        service = self._finding(park, "service_use")
        self.assertEqual(service.evidence["tier"], "D")
        self.assertFalse(service.evidence["causal_claim_allowed"])
        self.assertIsNone(service.did["did"])
        self.assertTrue(service.naive["available"])  # 处理片区自身变化仍可描述

        # 数量/强度分解仍可在处理片区上完成
        self.assertTrue(service.count_decomposition["available"])

    def test_unopened_interventions_have_no_effect_findings(self):
        run = run_analysis(data_dir=DATA, run_id="t2")
        for iv_type in ("service_point", "road_access"):
            block = self._block(run, iv_type)
            self.assertEqual(block.findings, [])
            self.assertIn("尚无实际开放日期", block.timing_note)
            self.assertIn("available", block.staggered_summary)
            self.assertFalse(block.staggered_summary["available"])

    def test_global_warnings_cover_short_window_and_missing(self):
        run = run_analysis(data_dir=DATA, run_id="t3")
        joined = "\n".join(run.global_warnings)
        self.assertIn("B 级", joined)
        self.assertIn("缺失", joined)

    def test_population_reconciliation_flags_scope_differences(self):
        run = run_analysis(data_dir=DATA, run_id="t4")
        check = next(c for c in run.population_checks
                     if c["area_id"] == "A-1" and c["period"] == "2026-Q1")
        self.assertEqual(check["reported_residents"], 1240)
        self.assertEqual(check["block_sum"], 720)
        self.assertEqual(check["gap"], 520)

    def test_population_decomposition_attached_to_open_cohort(self):
        run = run_analysis(data_dir=DATA, run_id="t5")
        park = self._block(run, "pocket_park")
        self.assertEqual(len(park.population_decomposition), 1)
        decomp = park.population_decomposition[0]
        self.assertEqual(decomp["area_id"], "A-1")
        self.assertTrue(decomp["scope_break"])
        self.assertEqual(decomp["persistent_change"], -100)
        # 未开放的干预类型没有处理片区，不产出分解
        road = self._block(run, "road_access")
        self.assertEqual(road.population_decomposition, [])

    def test_as_of_snapshot_changes_naive_baseline(self):
        latest = run_analysis(data_dir=DATA, run_id="latest")
        old = run_analysis(data_dir=DATA, as_of=date(2026, 8, 31), run_id="old")
        park_latest = self._block(latest, "pocket_park")
        park_old = self._block(old, "pocket_park")
        svc_latest = self._finding(park_latest, "service_use")
        svc_old = self._finding(park_old, "service_use")
        # 9 月修订把 A-1 Q1 服务使用 310 改为 318
        self.assertEqual(svc_latest.naive["pre_value"], 318)
        self.assertEqual(svc_old.naive["pre_value"], 310)
        self.assertEqual(old.as_of, "2026-08-31")

    def test_save_reproduce_and_compare(self):
        run = run_analysis(data_dir=DATA, run_id="v1")
        run_dir = save_run(run, self.runs_dir)
        self.assertTrue((run_dir / "result.json").is_file())
        self.assertTrue((run_dir / "manifest.json").is_file())

        # 复现：同一 as_of、输入未变 -> 指纹一致
        outcome = reproduce(run_dir / "manifest.json", DATA, self.runs_dir)
        self.assertFalse(outcome["fingerprint_changed"])

        # 并列对比两个版本
        old_loaded = load_run(run_dir)
        new_run = run_analysis(data_dir=DATA, as_of=date(2026, 8, 31), run_id="v2")
        new_dir = save_run(new_run, self.runs_dir)
        comparison = compare_runs(old_loaded, load_run(new_dir))
        rows = {(r["intervention_type"], r["metric"]): r for r in comparison["rows"]}
        self.assertIn(("pocket_park", "walkability_score"), rows)
        md = render_comparison(comparison)
        self.assertIn("新旧分析版本并列对比", md)

    def test_reproduce_flags_changed_inputs(self):
        run = run_analysis(data_dir=DATA, run_id="v1")
        run_dir = save_run(run, self.runs_dir)
        manifest = load_manifest(run_dir)

        # 篡改数据目录中的一个文件后复现
        altered = self.tmp / "data"
        shutil.copytree(DATA, altered)
        target = altered / "other-measures.csv"
        target.write_text(target.read_text(encoding="utf-8") + "M-9,A-3,x,2026-Q1,\n",
                          encoding="utf-8")
        outcome = reproduce(run_dir / "manifest.json", altered, self.runs_dir)
        self.assertTrue(outcome["fingerprint_changed"])
        self.assertIn("已变更", outcome["note"])

    def test_report_uses_causal_wording_only_when_allowed(self):
        run = run_analysis(data_dir=DATA, run_id="v3")
        md = render_markdown(run)
        self.assertIn("微更新影响评估报告", md)
        self.assertIn("A 可信对照 / B 对照倾向 / C 仅相关 / D 资料不足", md)
        # D 级指标明确禁止因果表述
        self.assertIn("不得表述为项目因果效应", md)
        # 审查原因出现在报告中
        self.assertIn("对照失效", md)
        # 样本缺失表
        self.assertIn("缺失片区", md)

    def test_fingerprints_recorded_for_every_input(self):
        run = run_analysis(data_dir=DATA, run_id="v4")
        self.assertEqual(
            set(run.input_fingerprints),
            {"area-periods.csv", "interventions.csv", "other-measures.csv", "area-history.csv"},
        )
        saved = json.loads((save_run(run, self.runs_dir) / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["run_id"], "v4")


if __name__ == "__main__":
    unittest.main()
