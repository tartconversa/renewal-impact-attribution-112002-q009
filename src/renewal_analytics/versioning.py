"""分析运行的版本留痕、复现与并列对比。

每次运行写入 ``runs/<run_id>/``：

- ``result.json``：完整结论包（含输入文件校验和、as_of 快照日期）；
- ``manifest.json``：运行元数据（运行时间、as_of、输入指纹、代码版本）。

复现：按旧 manifest 的 as_of 与输入指纹重跑；若当前输入指纹与旧指纹不一致，
明确提示数据已变更，并把新旧两份结论并列输出，而不是覆盖旧结论。
"""
from __future__ import annotations

import json
import platform
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from .pipeline import AnalysisRun, run_analysis


def _to_jsonable(obj):
    if isinstance(obj, dict):
        return {key: _to_jsonable(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(value) for value in obj]
    if isinstance(obj, date):
        return obj.isoformat()
    return obj


def save_run(run: AnalysisRun, runs_dir: str | Path) -> Path:
    run_dir = Path(runs_dir) / run.run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    manifest = {
        "run_id": run.run_id,
        "generated_on": run.generated_on,
        "as_of": run.as_of,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "input_fingerprints": run.input_fingerprints,
    }
    (run_dir / "result.json").write_text(
        json.dumps(_to_jsonable(asdict(run)), ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return run_dir


def load_run(run_dir: str | Path) -> dict:
    run_dir = Path(run_dir)
    return json.loads((run_dir / "result.json").read_text(encoding="utf-8"))


def load_manifest(manifest_path: str | Path) -> dict:
    path = Path(manifest_path)
    if path.is_dir():
        path = path / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def reproduce(manifest_path: str | Path, data_dir: str | Path, runs_dir: str | Path) -> dict:
    """按旧 manifest 复现：同样的 as_of 快照重跑，并核对输入指纹。"""
    manifest = load_manifest(manifest_path)
    as_of = None if manifest["as_of"] == "latest" else date.fromisoformat(manifest["as_of"])

    fresh = run_analysis(data_dir=data_dir, as_of=as_of,
                         run_id=f"{manifest['run_id']}-repro-{date.today().isoformat()}")
    fingerprint_changed = fresh.input_fingerprints != manifest["input_fingerprints"]
    save_run(fresh, runs_dir)
    return {
        "reproduced_run_id": fresh.run_id,
        "original_run_id": manifest["run_id"],
        "as_of": fresh.as_of,
        "fingerprint_changed": fingerprint_changed,
        "original_fingerprints": manifest["input_fingerprints"],
        "current_fingerprints": fresh.input_fingerprints,
        "note": ("输入数据自旧运行后已变更：复现结果基于当前数据与旧 as_of 口径，"
                 "请与旧版本并列查看，勿覆盖旧结论" if fingerprint_changed
                 else "输入指纹一致，复现结果应与旧版本相同"),
    }


def compare_runs(old: dict, new: dict) -> dict:
    """并列对比两个结论包：样本、分级、DiD 点估计与区间的逐项差异。"""
    def index_findings(result):
        out = {}
        for block in result["interventions"]:
            for finding in block["findings"]:
                out[(block["intervention_type"], finding["metric"])] = finding
        return out

    old_findings, new_findings = index_findings(old), index_findings(new)
    keys = sorted(set(old_findings) | set(new_findings))
    rows = []
    for key in keys:
        before, after = old_findings.get(key), new_findings.get(key)
        row = {"intervention_type": key[0], "metric": key[1]}
        for tag, finding in (("old", before), ("new", after)):
            if finding is None:
                row[f"{tag}_tier"] = None
                row[f"{tag}_did"] = None
                continue
            row[f"{tag}_tier"] = finding["evidence"]["tier"]
            did = finding["did"]
            row[f"{tag}_did"] = None if did is None else did.get("did")
            row[f"{tag}_ci95"] = None if did is None else did.get("ci95")
        row["tier_changed"] = row["old_tier"] != row["new_tier"]
        rows.append(row)
    return {
        "old_run": old["run_id"],
        "new_run": new["run_id"],
        "old_as_of": old["as_of"],
        "new_as_of": new["as_of"],
        "rows": rows,
        "periods_changed": old["observation_periods"] != new["observation_periods"],
    }
